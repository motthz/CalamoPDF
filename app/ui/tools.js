/* Calamo PDF Studio — page editing tools: marks, notes, shapes, signatures, pictures,
   redaction, pages, header/footer, compare, optimize, protect */
"use strict";

const ANN_COLORS = ["#ffd400", "#7ee081", "#6cc6ff", "#ff8fb1", "#e53935", "#1e40af", "#111111"];
const ANN_DEFAULT = { highlight: "#ffd400", underline: "#e53935", strike: "#e53935", note: "#ffd400",
                      rect: "#e53935", ellipse: "#e53935", arrow: "#e53935" };
const PICTURE_TYPES = ["Stamp", "Image", "signature"];
Object.assign(S, { ann: "highlight", annColor: null, ins: "signature", sig: null, sigs: [], sel: null,
                   sens: [], diffs: [], dragPage: null });

function resetExtras() {
  S.sel = null; S.sens = []; S.diffs = [];
  $("#sensList").innerHTML = ""; $("#cmpList").innerHTML = "";
  $("#redactBtn").disabled = true;
  updateObjButtons();
}
const annColor = () => S.annColor || ANN_DEFAULT[S.ann];
const pageOf = i => S.pages[i];

// ---------------------------------------------------------------- tool options bar
function showToolOpts(t) {
  const has = ["annotate", "insert", "redact", "select"].includes(t);
  $("#toolOpts").hidden = !has;
  $$("#toolOpts .opts").forEach(o => o.hidden = o.dataset.opts !== t);
  if (t === "insert") loadSignatures();
  if (t !== "select") selectObj(null);
  S.pages.forEach(drawOverlay);
}
function segSwitch(root, attr, onPick) {
  $$("button", root).forEach(b => b.onclick = () => {
    $$("button", root).forEach(x => x.classList.toggle("active", x === b));
    onPick(b.dataset[attr]);
  });
}
segSwitch($("#annKind"), "k", k => { S.ann = k; S.annColor = null; drawSwatches(); });
segSwitch($("#insKind"), "k", k => {
  S.ins = k;
  $("#sigPick").hidden = $("#sigNew").hidden = k !== "signature";
  $("#insHint").textContent = k === "signature" ? "Clicca sulla pagina dove mettere la firma." : "Clicca sulla pagina dove mettere l'immagine: poi scegli il file.";
});
function drawSwatches() {
  const cur = annColor();
  $("#annColors").innerHTML = ANN_COLORS.map(c => `<button style="--c:${c}" data-c="${c}" class="${c === cur ? "on" : ""}" title="${c}"></button>`).join("");
  $$("#annColors button").forEach(b => b.onclick = () => { S.annColor = b.dataset.c; drawSwatches(); });
}
drawSwatches();

// ---------------------------------------------------------------- overlay: objects, sensitive data, differences
function drawExtras(P, frag, s) {
  if (S.tool === "select") {
    (P.data.objects || []).forEach(o => {
      const d = document.createElement("div");
      const on = S.sel && S.sel.page === P.i && String(S.sel.id) === String(o.id);
      d.className = "obj" + (on ? " on" : "");
      d.dataset.id = o.id; d.dataset.src = o.src;
      d.title = o.label + (o.content ? `: ${o.content}` : "");
      Object.assign(d.style, box(o.rect, s, 2));
      if (on && o.type !== "Text") d.innerHTML = `<i class="obj-h"></i>`;
      frag.appendChild(d);
    });
    if (S.sel && S.sel.page === P.i) updateObjButtons();
  }
  drawMulti(P, frag, s);
  S.sens.forEach((h, k) => {
    if (h.page !== P.i || !h.on) return;
    const d = document.createElement("div");
    d.className = "diff sens";
    d.title = `${h.label}: ${h.text}`;
    d.dataset.k = k;
    Object.assign(d.style, box(h.bbox, s, 1));
    frag.appendChild(d);
  });
  const dp = S.diffs.find(x => x.page === P.i);
  if (dp) dp.boxes.forEach(b => {
    const d = document.createElement("div");
    d.className = "diff" + (dp.status === "removed" ? " gone" : "");
    Object.assign(d.style, box(b, s, 2));
    frag.appendChild(d);
  });
}
const objOf = (P, id) => P && P.data && (P.data.objects || []).find(o => String(o.id) === String(id));
function selectObj(sel) {
  const old = S.sel;
  S.sel = sel;
  if (old && pageOf(old.page)) drawOverlay(pageOf(old.page));
  if (sel && pageOf(sel.page) && (!old || old.page !== sel.page)) drawOverlay(pageOf(sel.page));
  updateObjButtons();
}
function updateObjButtons() {
  const o = S.sel && objOf(pageOf(S.sel.page), S.sel.id);
  $("#objDelete").disabled = !o;
  $("#objReplace").disabled = !(o && PICTURE_TYPES.includes(o.type) && o.type !== "signature");
  $("#objCrop").disabled = !(o && PICTURE_TYPES.includes(o.type));
}
// after an object changed: re-read the page and keep the object selected
async function objectsChanged(P, r) {
  await afterChange();
  if (r && r.id != null && S.tool === "select") selectObj({ page: P.i, id: r.id });
  else updateObjButtons();
  if (!$('[data-panel="comments"]').hidden) refreshComments();
}

// a drag on the page: rectangle (and the two end points, for arrows)
function startDrag(P, e, done, cls = "sel-rect") {
  e.preventDefault();
  const a = pagePoint(P, e), s = scale();
  const r = document.createElement("div");
  r.className = cls;
  P.overlay.appendChild(r);
  let b = a;
  const rectOf = () => [Math.min(a.x, b.x), Math.min(a.y, b.y), Math.max(a.x, b.x), Math.max(a.y, b.y)];
  const move = ev => { b = pagePoint(P, ev); Object.assign(r.style, box(rectOf(), s)); };
  const up = async () => {
    window.removeEventListener("mousemove", move);
    window.removeEventListener("mouseup", up);
    r.remove();
    const rect = rectOf();
    if (rect[2] - rect[0] < 3 && rect[3] - rect[1] < 3) return;
    await done(rect, a, b);
  };
  window.addEventListener("mousemove", move);
  window.addEventListener("mouseup", up);
}

function toolMouseDown(P, e) {
  if (S.tool === "annotate") {
    if (S.ann === "note") return false;
    const mark = ["highlight", "underline", "strike"].includes(S.ann);
    startDrag(P, e, (rect, a, b) => guarded(async () => {
      const r = mark ? await call("mark_text", P.i, rect, S.ann, annColor())
        : await call("add_shape", P.i, S.ann, [a.x, a.y], [b.x, b.y], annColor(), 2);
      await objectsChanged(P, r);
    }), mark ? "sel-rect mark" : "sel-rect shape");
    return true;
  }
  if (S.tool === "redact") {
    startDrag(P, e, async rect => {
      const ok = await modal("Oscurare quest'area?", "Il testo e le immagini sotto il riquadro nero vengono eliminati davvero dal PDF, non solo coperti. Puoi annullare con Ctrl+Z finché non salvi.",
        [["Annulla", false, "ghost"], ["Oscura", true, "primary"]]);
      if (ok) await guarded(async () => { const r = await call("redact", [{ page: P.i, bbox: rect }], false); S.inkCache = {}; await afterChange(r); toast("Area oscurata", "ok"); });
    }, "sel-rect redact");
    return true;
  }
  if (S.tool === "select") {
    const el = e.target.closest(".obj");
    if (!el) { selectObj(null); return true; }
    e.preventDefault();
    const o = objOf(P, el.dataset.id);
    if (!o) return true;
    if (!S.sel || S.sel.page !== P.i || String(S.sel.id) !== String(o.id)) selectObj({ page: P.i, id: o.id });
    dragObject(P, o, e, !!e.target.closest(".obj-h"));
    return true;
  }
  return false;
}
// move (or resize from the corner) an object; pictures keep their proportions
function dragObject(P, o, e, resize) {
  const s = scale(), a = pagePoint(P, e);
  const el = $(`.obj[data-id="${o.id}"]`, P.overlay);
  const r0 = o.rect.slice(), ratio = (r0[3] - r0[1]) / Math.max(r0[2] - r0[0], 1);
  const keep = PICTURE_TYPES.includes(o.type);
  let rect = r0, moved = false;
  const move = ev => {
    const b = pagePoint(P, ev), dx = b.x - a.x, dy = b.y - a.y;
    if (Math.abs(dx) + Math.abs(dy) > 2) moved = true;
    if (resize) {
      const w = Math.max(8, r0[2] - r0[0] + dx);
      rect = [r0[0], r0[1], r0[0] + w, keep ? r0[1] + w * ratio : Math.max(r0[1] + 8, r0[3] + dy)];
    } else {
      const d = snapDrag(P, r0, { x: dx, y: dy }, { kind: "obj", id: o.id });
      rect = [r0[0] + d.x, r0[1] + d.y, r0[2] + d.x, r0[3] + d.y];
    }
    if (el) Object.assign(el.style, box(rect, s, 2));
  };
  const up = async () => {
    window.removeEventListener("mousemove", move);
    window.removeEventListener("mouseup", up);
    clearGuides(P);
    if (!moved) return;
    await guarded(async () => objectsChanged(P, await call("move_object", P.i, o.src, o.id, rect)));
  };
  window.addEventListener("mousemove", move);
  window.addEventListener("mouseup", up);
}

function toolClick(P, e, pt) {
  if (S.tool === "annotate") {
    if (S.ann === "note") addNote(P, pt);
    return true;
  }
  if (S.tool === "insert") {
    if (S.ins === "image") {
      guarded(async () => { const r = await call("insert_picture", P.i, pt.x, pt.y); if (r) { await objectsChanged(P, r); toast("Immagine inserita: con lo strumento Oggetti puoi spostarla e ridimensionarla.", "ok"); } });
    } else if (!S.sig) {
      openSigModal();
    } else {
      guarded(async () => { await objectsChanged(P, await call("place_signature", P.i, S.sig, pt.x, pt.y)); toast("Firma inserita: con lo strumento Oggetti puoi spostarla e ridimensionarla.", "ok"); });
    }
    return true;
  }
  return S.tool === "redact" || S.tool === "select";
}
async function addNote(P, pt) {
  const text = await modal("Nuova nota", "Scrivi il commento: comparirà come un'icona sulla pagina.", [["Annulla", false, "ghost"], ["Aggiungi", true, "primary"]], "");
  if (!text) return;
  await guarded(async () => objectsChanged(P, await call("add_note", P.i, pt.x, pt.y, text, annColor())));
}
$("#pages").addEventListener("dblclick", async e => {
  const el = e.target.closest(".obj");
  if (!el || S.tool !== "select") return;
  const P = pageOf(+el.closest(".page").dataset.i), o = objOf(P, el.dataset.id);
  if (o && o.src === "annot" && !PICTURE_TYPES.includes(o.type)) editNote(P.i, o);
});
async function editNote(page, o) {
  const text = await modal(`Modifica: ${o.label}`, "Il testo del commento.", [["Annulla", false, "ghost"], ["Salva", true, "primary"]], o.content || "");
  if (text == null) return;
  await guarded(async () => objectsChanged(pageOf(page), await call("edit_object", page, o.id, text, null)));
}
async function deleteSelected() {
  const sel = S.sel, P = sel && pageOf(sel.page), o = P && objOf(P, sel.id);
  if (!o) return;
  await guarded(async () => { const r = await call("delete_object", P.i, o.src, o.id); S.sel = null; await objectsChanged(P, r); toast(`${o.label} eliminata`, "ok"); });
}
$("#objDelete").onclick = deleteSelected;
$("#objReplace").onclick = () => {
  const sel = S.sel, P = sel && pageOf(sel.page), o = P && objOf(P, sel.id);
  if (o) guarded(async () => { const r = await call("replace_picture", P.i, o.src, o.id); if (r) await objectsChanged(P, r); });
};
$("#objCrop").onclick = async () => {
  const sel = S.sel, P = sel && pageOf(sel.page), o = P && objOf(P, sel.id);
  if (!o) return;
  const v = await modal("Ritaglia", "Quanto togliere da ogni lato, in percentuale: sinistra, sopra, destra, sotto (es. 10 0 10 0).",
    [["Annulla", false, "ghost"], ["Ritaglia", true, "primary"]], "0 0 0 0");
  if (!v) return;
  const f = v.split(/[\s,;]+/).map(x => Math.min(45, Math.max(0, parseFloat(x) || 0)) / 100);
  while (f.length < 4) f.push(0);
  await guarded(async () => {
    let id = o.id, src = o.src;
    if (src === "image") {   // a picture of the page becomes an object first
      const m = await call("move_object", P.i, src, id, o.rect);
      id = m.id; src = "annot";
    }
    await objectsChanged(P, await call("crop_picture", P.i, id, f.slice(0, 4)));
  });
};

// ---------------------------------------------------------------- signatures
async function loadSignatures() {
  try { S.sigs = await call("signatures"); } catch (e) { S.sigs = []; }
  if (!S.sigs.some(x => x.name === S.sig)) S.sig = S.sigs.length ? S.sigs[0].name : null;
  renderSignatures();
}
function renderSignatures() {
  $("#sigPick").innerHTML = S.sigs.map(x => `<button class="sig${x.name === S.sig ? " on" : ""}" data-name="${esc(x.name)}" title="${esc(x.name)}">
    <img src="${x.img}" alt=""><i data-del title="Elimina questa firma">×</i></button>`).join("")
    || `<span class="muted small">Nessuna firma salvata.</span>`;
  $$("#sigPick .sig").forEach(b => b.onclick = async e => {
    if (e.target.closest("[data-del]")) {
      const ok = await modal("Eliminare la firma?", `“${b.dataset.name}” verrà tolta dalle firme salvate (non dai documenti in cui l'hai già messa).`, [["Annulla", false, "ghost"], ["Elimina", true, "primary"]]);
      if (ok) await guarded(async () => { S.sigs = await call("delete_signature", b.dataset.name); if (S.sig === b.dataset.name) S.sig = S.sigs.length ? S.sigs[0].name : null; renderSignatures(); });
      return;
    }
    S.sig = b.dataset.name; renderSignatures();
  });
}
$("#sigNew").onclick = () => openSigModal();
let sigMode = "draw", sigInk = false;
function openSigModal() {
  $("#sigModal").hidden = false;
  clearSig();
  $("#sigName").value = S.sigs.length ? `Firma ${S.sigs.length + 1}` : "La mia firma";
}
function clearSig() {
  const c = $("#sigCanvas"), g = c.getContext("2d");
  g.clearRect(0, 0, c.width, c.height);
  sigInk = false;
}
segSwitch($("#sigMode"), "m", m => { sigMode = m; $("#sigDrawBox").hidden = m !== "draw"; $("#sigImportBox").hidden = m !== "import"; $("#sigSave").textContent = m === "draw" ? "Salva firma" : "Scegli la foto…"; });
$("#sigClear").onclick = clearSig;
$("#sigCancel").onclick = () => { $("#sigModal").hidden = true; };
(() => {
  const c = $("#sigCanvas"), g = c.getContext("2d");
  let last = null;
  const at = e => { const r = c.getBoundingClientRect(); return [(e.clientX - r.left) * c.width / r.width, (e.clientY - r.top) * c.height / r.height]; };
  c.addEventListener("pointerdown", e => { c.setPointerCapture(e.pointerId); last = at(e); });
  c.addEventListener("pointermove", e => {
    if (!last) return;
    const p = at(e);
    g.strokeStyle = "#14213d"; g.lineCap = g.lineJoin = "round";
    g.lineWidth = 2.2 + 1.6 * (e.pressure && e.pointerType === "pen" ? e.pressure : 0.5);
    g.beginPath(); g.moveTo(last[0], last[1]); g.lineTo(p[0], p[1]); g.stroke();
    last = p; sigInk = true;
  });
  const end = () => { last = null; };
  c.addEventListener("pointerup", end); c.addEventListener("pointercancel", end);
})();
$("#sigSave").onclick = () => guarded(async () => {
  const name = $("#sigName").value.trim() || "Firma";
  const before = new Set(S.sigs.map(x => x.name));
  let list;
  if (sigMode === "draw") {
    if (!sigInk) return toast("Disegna prima la firma nel riquadro.", "warn");
    list = await call("save_signature_drawn", name, $("#sigCanvas").toDataURL("image/png"));
  } else {
    list = await call("import_signature", name);
    if (!list) return;
  }
  S.sigs = list;
  const added = list.find(x => !before.has(x.name));
  if (added) S.sig = added.name;
  renderSignatures();
  $("#sigModal").hidden = true;
  if (S.tool !== "insert") setTool("insert");
  toast("Firma salvata: clicca sulla pagina dove metterla.", "ok");
});

// ---------------------------------------------------------------- pages: drag the thumbnails
function bindThumbDrag(th, i) {
  th.draggable = true;
  th.addEventListener("dragstart", e => { S.dragPage = i; th.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(i)); });
  th.addEventListener("dragend", () => { S.dragPage = null; $$(".thumb").forEach(t => t.classList.remove("dragging", "drop-before", "drop-after")); });
  th.addEventListener("dragover", e => {
    if (S.dragPage == null) return;
    e.preventDefault();
    const r = th.getBoundingClientRect(), after = e.clientY > r.top + r.height / 2;
    $$(".thumb").forEach(t => t.classList.remove("drop-before", "drop-after"));
    th.classList.add(after ? "drop-after" : "drop-before");
  });
  th.addEventListener("drop", e => {
    if (S.dragPage == null) return;
    e.preventDefault();
    e.stopPropagation();
    const src = S.dragPage, after = th.classList.contains("drop-after");
    S.dragPage = null;
    $$(".thumb").forEach(t => t.classList.remove("dragging", "drop-before", "drop-after"));
    let dst = after ? i + 1 : i;
    if (src < dst) dst -= 1;
    if (dst === src) return;
    guarded(async () => { closeEditor(true); S.inkCache = {}; S.sel = null; await afterChange(await call("move_page", src, dst)); setCurrent(dst); toast(`Pagina ${src + 1} spostata in posizione ${dst + 1}`, "ok"); });
  });
}
const pageSpec = () => $("#pageSpec").value.trim() || String(S.current + 1);
const reveal = r => ({ label: "Apri", run: () => call("reveal", r.path || r.files[0]) });
const kb = n => n > 1048576 ? (n / 1048576).toFixed(1).replace(".", ",") + " MB" : Math.max(1, Math.round(n / 1024)) + " KB";

Object.assign(COMMANDS, {
  duplicatePage: () => guarded(async () => { S.inkCache = {}; await afterChange(await call("duplicate_page", S.current)); goToPage(S.current + 1); }),
  mergePdf: () => guarded(async () => {
    const r = await call("merge_pdf", S.current);
    if (!r) return;
    S.inkCache = {};
    await afterChange(r);
    goToPage(r.at);
    toast(`Aggiunte ${r.added} pagine da ${r.files} file`, "ok");
  }, "Unione dei PDF…"),
  extractPages: () => guarded(async () => {
    const r = await call("extract_pages", pageSpec());
    if (r) toast(`Salvate ${r.pages} pagine in un nuovo PDF`, "ok", reveal(r));
  }),
  deletePages: async () => {
    const spec = pageSpec();
    const ok = await modal("Eliminare le pagine?", `Le pagine ${spec} verranno rimosse. Puoi annullare con Ctrl+Z.`, [["Annulla", false, "ghost"], ["Elimina", true, "primary"]]);
    if (ok) guarded(async () => { S.inkCache = {}; await afterChange(await call("delete_pages", spec)); setCurrent(Math.min(S.current, S.pages.length - 1)); });
  },
  filesTool: () => openFilesModal("merge"),
  splitCurrent: () => openFilesModal("split", true),
  stampPages: () => guarded(async () => {
    const r = await call("stamp_pages", {
      header: $("#stHeader").value, footer: $("#stFooter").value, align: $("#stAlign").value,
      size: parseFloat($("#stSize").value) || 10, pages: $("#stPages").value, start: parseInt($("#stStart").value) || 1,
      watermark: $("#stWatermark").value, wm_opacity: (+$("#stOpacity").value || 18) / 100, wm_color: $("#stWmColor").value,
    });
    await afterChange(r);
    toast(`Applicato a ${r.stamped} pagine (Ctrl+Z per annullare)`, "ok");
  }, "Applicazione alle pagine…"),
  findSensitive: () => guarded(async () => {
    const kinds = $$("#sensKinds input:checked").map(x => x.value);
    const hits = await call("find_sensitive", kinds, $("#sensTerm").value);
    S.sens = hits.map(h => ({ ...h, on: true }));
    renderSensitive();
    S.pages.forEach(drawOverlay);
    if (hits.length) goToPage(hits[0].page, hits[0].bbox[1]);
    else toast("Nessun dato sensibile trovato.", "ok");
  }, "Ricerca dei dati sensibili…"),
  redactSelected: async () => {
    const areas = S.sens.filter(h => h.on).map(h => ({ page: h.page, bbox: h.bbox }));
    if (!areas.length) return;
    const ok = await modal("Oscurare per sempre?", `${areas.length} elementi verranno coperti di nero e il testo sotto eliminato dal PDF. Puoi annullare con Ctrl+Z finché non salvi.`,
      [["Annulla", false, "ghost"], ["Oscura", true, "primary"]]);
    if (!ok) return;
    await guarded(async () => {
      const r = await call("redact", areas, $("#sensMeta").checked);
      S.sens = []; renderSensitive(); S.inkCache = {};
      await afterChange(r);
      toast(`Oscurati ${r.redacted} elementi`, "ok");
    }, "Oscuramento…");
  },
  compare: () => guarded(async () => {
    const r = await call("compare_with");
    if (!r) return;
    S.diffs = r.pages.filter(p => p.status !== "same");
    renderCompare(r);
    S.pages.forEach(drawOverlay);
  }, "Confronto delle versioni…"),
  optimize: () => guarded(async () => {
    const r = await call("save_optimized", $("#optQuality").value);
    if (r) toast(`Copia leggera salvata: ${kb(r.before)} → ${kb(r.after)}`, "ok", reveal(r));
  }, "Alleggerimento…"),
  protect: () => guarded(async () => {
    const r = await call("save_protected", $("#pwUser").value, $("#pwOwner").value, $("#pwPrint").checked, $("#pwCopy").checked, $("#pwEdit").checked);
    if (r) { toast("Copia protetta salvata", "ok", reveal(r)); $("#pwUser").value = $("#pwOwner").value = ""; }
  }),
});

function renderSensitive() {
  const L = $("#sensList");
  L.innerHTML = S.sens.map((h, k) => `<label class="sens-item"><input type="checkbox" data-k="${k}"${h.on ? " checked" : ""}>
    <span><b>${esc(h.text)}</b><small>${esc(h.label)} · pagina ${h.page + 1}</small></span></label>`).join("");
  $$("input", L).forEach(c => c.onchange = () => { S.sens[+c.dataset.k].on = c.checked; S.pages.forEach(drawOverlay); $("#redactBtn").disabled = !S.sens.some(h => h.on); });
  $$(".sens-item span", L).forEach((sp, k) => sp.onclick = e => { e.preventDefault(); goToPage(S.sens[k].page, S.sens[k].bbox[1]); });
  $("#redactBtn").disabled = !S.sens.some(h => h.on);
}
function renderCompare(r) {
  const what = { changed: "modificata", added: "solo nell'altra versione", removed: "non c'è nell'altra versione" };
  $("#cmpList").innerHTML = `<p class="small">${r.changed ? `<b>${r.changed}</b> ${r.changed === 1 ? "pagina" : "pagine"} su ${r.total} ${r.changed === 1 ? "diversa" : "diverse"} da “${esc(r.other)}”.` : `Nessuna differenza con “${esc(r.other)}”.`}</p>`
    + S.diffs.map((p, k) => `<button class="cmp-item" data-k="${k}"><b>Pagina ${p.page + 1}</b> <small>${what[p.status] || ""}${p.pixels ? " (scansione)" : ""}</small>
      ${p.added.length ? `<span class="add">+ ${esc(p.added.join(" ").slice(0, 160))}</span>` : ""}
      ${p.removed.length ? `<span class="del">− ${esc(p.removed.join(" ").slice(0, 160))}</span>` : ""}</button>`).join("")
    + (S.diffs.length ? `<button class="btn ghost sm" id="cmpClear">Togli i segni del confronto</button>` : "");
  $$("#cmpList .cmp-item").forEach(b => b.onclick = () => { const p = S.diffs[+b.dataset.k]; if (p.page < S.pages.length) goToPage(p.page, p.boxes[0] && p.boxes[0][1]); });
  const c = $("#cmpClear");
  if (c) c.onclick = () => { S.diffs = []; $("#cmpList").innerHTML = ""; S.pages.forEach(drawOverlay); };
}

// ---------------------------------------------------------------- comments panel
async function refreshComments() {
  if (!S.doc) return;
  let list;
  try { list = await call("comments"); } catch (e) { return; }
  $("#cmtCount").textContent = list.length ? `(${list.length})` : "";
  const L = $("#cmtList");
  if (!list.length) { L.innerHTML = `<div class="empty small">Nessun commento. Usa lo strumento <b>Annota</b> per evidenziare o lasciare una nota.</div>`; return; }
  L.innerHTML = list.map((c, k) => `<div class="cmt" data-k="${k}"><i style="background:${c.color || "#ffd400"}"></i>
    <div class="grow"><small>${esc(c.type)} · pagina ${c.page + 1}</small><p>${c.text ? esc(c.text) : "<span class='muted'>—</span>"}</p></div>
    <button class="icon-btn" data-a="edit" title="Modifica il testo"><svg><use href="#i-edit"/></svg></button>
    <button class="icon-btn danger" data-a="del" title="Elimina"><svg><use href="#i-trash"/></svg></button></div>`).join("");
  $$(".cmt", L).forEach(el => {
    const c = list[+el.dataset.k];
    el.onclick = async e => {
      const a = e.target.closest("[data-a]");
      if (!a) { goToPage(c.page, c.rect[1]); flashBox(c.page, c.rect); return; }
      if (a.dataset.a === "del") {
        await guarded(async () => { await call("delete_object", c.page, "annot", c.id); await afterChange(); refreshComments(); });
      } else editNote(c.page, { id: c.id, label: c.type, content: c.text });
    };
  });
}
function flashBox(page, rect) {
  const P = pageOf(page);
  if (!P) return;
  const d = document.createElement("div");
  d.className = "diff flash";
  Object.assign(d.style, box(rect, scale(), 4));
  P.overlay.appendChild(d);
  setTimeout(() => d.remove(), 1500);
}

// ---------------------------------------------------------------- whole PDFs: merge and split
const FM = { items: [], split: null };
function openFilesModal(mode, current) {
  $("#filesModal").hidden = false;
  if (current && S.doc) FM.split = { path: "@current", name: S.doc.name };
  else if (!FM.split && S.doc) FM.split = { path: "@current", name: S.doc.name };
  if (!S.doc && FM.split && FM.split.path === "@current") FM.split = null;
  $("#mergeAddCurrent").disabled = !S.doc;
  $(`#filesMode [data-m="${mode}"]`).click();
  renderMerge(); renderSplitSrc();
}
segSwitch($("#filesMode"), "m", m => { $("#mergeBox").hidden = m !== "merge"; $("#splitBox").hidden = m !== "split"; });
$("#filesClose").onclick = () => { $("#filesModal").hidden = true; };
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
function renderMerge() {
  const L = $("#mergeList");
  L.innerHTML = FM.items.length ? FM.items.map((it, k) => `<div class="merge-item" draggable="true" data-k="${k}">
      <span class="grip">⋮⋮</span><svg><use href="#i-pdf"/></svg>
      <div class="grow"><b>${esc(it.name)}</b><small>${it.current ? "documento aperto, con le modifiche" : plural(it.pages, "pagina", "pagine")}</small></div>
      <input class="mpages" placeholder="tutte" value="${esc(it.spec || "")}" title="Pagine da prendere (es. 1-3, 5)" spellcheck="false">
      <button class="icon-btn" data-a="up" title="Su" ${k ? "" : "disabled"}>↑</button>
      <button class="icon-btn" data-a="down" title="Giù" ${k < FM.items.length - 1 ? "" : "disabled"}>↓</button>
      <button class="icon-btn danger" data-a="del" title="Togli"><svg><use href="#i-x"/></svg></button></div>`).join("")
    : `<div class="empty small">Nessun PDF: premi <b>Aggiungi PDF…</b> e scegline due o più.</div>`;
  $$(".merge-item", L).forEach(el => {
    const k = +el.dataset.k;
    $("input", el).oninput = e => { FM.items[k].spec = e.target.value; };
    $("input", el).onkeydown = e => e.stopPropagation();
    $$("[data-a]", el).forEach(b => b.onclick = () => {
      const a = b.dataset.a;
      if (a === "del") FM.items.splice(k, 1);
      else { const j = a === "up" ? k - 1 : k + 1; [FM.items[k], FM.items[j]] = [FM.items[j], FM.items[k]]; }
      renderMerge();
    });
    el.addEventListener("dragstart", e => { FM.drag = k; e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", "m" + k); el.classList.add("dragging"); });
    el.addEventListener("dragend", () => { FM.drag = null; renderMerge(); });
    el.addEventListener("dragover", e => { if (FM.drag == null) return; e.preventDefault(); e.stopPropagation();
      $$(".merge-item", L).forEach(x => x.classList.remove("drop-before", "drop-after"));
      const r = el.getBoundingClientRect(); el.classList.add(e.clientY > r.top + r.height / 2 ? "drop-after" : "drop-before"); });
    el.addEventListener("drop", e => {
      if (FM.drag == null) return;
      e.preventDefault(); e.stopPropagation();
      let dst = el.classList.contains("drop-after") ? k + 1 : k;
      const src = FM.drag; FM.drag = null;
      if (src < dst) dst -= 1;
      FM.items.splice(dst, 0, FM.items.splice(src, 1)[0]);
      renderMerge();
    });
  });
  const pages = FM.items.reduce((n, it) => n + (it.pages || 0), 0);
  $("#mergeTotal").textContent = FM.items.length ? `${plural(FM.items.length, "file", "file")}${pages ? ", " + plural(pages, "pagina", "pagine") : ""}` : "";
  $("#mergeGo").disabled = !FM.items.length || (FM.items.length < 2 && !FM.items.some(it => it.spec));
}
$("#mergeAdd").onclick = () => guarded(async () => {
  const list = await call("pick_pdfs", true);
  list.forEach(f => { if (f.locked) toast(`“${f.name}” è protetto da password: aprilo e salvane una copia senza password.`, "warn"); else FM.items.push({ path: f.path, name: f.name, pages: f.pages }); });
  renderMerge();
});
$("#mergeAddCurrent").onclick = () => {
  if (!S.doc) return;
  FM.items.push({ path: "@current", name: S.doc.name, pages: S.doc.pages.length, current: true });
  renderMerge();
};
$("#mergeGo").onclick = async () => {
  const open = $("#mergeOpen").checked;
  await guarded(async () => {
    const r = await call("merge_files", FM.items.map(it => ({ path: it.path, pages: it.spec || "" })), open);
    if (!r) return;
    $("#filesModal").hidden = true;
    FM.items = [];
    if (r.info) loadDoc(r.info);
    toast(`Uniti ${plural(r.files, "file", "file")} in “${r.name}” (${plural(r.pages, "pagina", "pagine")})`, "ok", r.info ? null : reveal(r));
  }, "Unione dei PDF…");
};
function renderSplitSrc() {
  $("#splitName").textContent = FM.split ? FM.split.name + (FM.split.path === "@current" ? " (aperto)" : "") : "Nessun PDF scelto";
  $("#splitGo").disabled = !FM.split;
}
$("#splitPick").onclick = () => guarded(async () => {
  const [f] = await call("pick_pdfs", false);
  if (!f) return;
  if (f.locked) return toast(`“${f.name}” è protetto da password: aprilo e salvane una copia senza password.`, "warn");
  FM.split = { path: f.path, name: f.name };
  renderSplitSrc();
});
$$("#splitModes input:not([type=radio])").forEach(i => {
  i.onfocus = () => { i.closest("label").querySelector("input[type=radio]").checked = true; };
  i.onkeydown = e => e.stopPropagation();
});
$("#splitGo").onclick = () => guarded(async () => {
  const mode = $("#splitModes input[type=radio]:checked").value;
  const value = { every: +$("#splitN").value || 1, ranges: $("#splitRanges").value, size: +$("#splitMB").value || 10 }[mode] ?? null;
  const r = await call("split_file", FM.split.path, mode, value);
  if (!r) return;
  $("#filesModal").hidden = true;
  toast(`Creati ${plural(r.files.length, "file", "file")} da ${plural(r.pages, "pagina", "pagine")}`, "ok", { label: "Apri cartella", run: () => call("reveal", r.folder) });
}, "Divisione del PDF…");

// ---------------------------------------------------------------- drag the text and the writing of a page
// Press on a block of text or a line of a scan and drag it: on release it is moved there.
// A click without moving keeps doing what the tool does (edit the text, open the line).
function startElementDrag(P, e) {
  if (S.tool !== "edit" && S.tool !== "select") return false;
  if (e.target.closest(".obj, .editor")) return false;
  const el = e.target.closest(".blk, .ink");
  if (!el) return false;
  const isInk = el.classList.contains("ink");
  const item = isInk ? (P.data.ink || []).find(l => l.id === +el.dataset.id) : P.data.blocks.find(b => b.id === +el.dataset.id);
  if (!item || (isInk && item.source === "unread" && !item.text && S.tool === "edit")) return false;
  const s = scale(), a = pagePoint(P, e), bb = item.bbox;
  let ghost = null, d = { x: 0, y: 0 };
  const move = ev => {
    const b = pagePoint(P, ev);
    d = { x: b.x - a.x, y: b.y - a.y };
    if (ghost) d = snapDrag(P, bb, d, { kind: isInk ? "ink" : "blk", id: item.id });
    if (!ghost) {
      if (Math.abs(d.x) + Math.abs(d.y) < 5) return;
      ghost = document.createElement("div");
      ghost.className = "drag-ghost";
      const pad = 3;
      Object.assign(ghost.style, box(bb, s, pad), {
        backgroundImage: `url("${P.img.src}")`, backgroundSize: `${P.w * s}px ${P.h * s}px`,
        backgroundPosition: `${-(bb[0] * s - pad)}px ${-(bb[1] * s - pad)}px`,
      });
      P.overlay.appendChild(ghost);
      el.classList.add("drag-src");
      document.body.classList.add("dragging-el");
    }
    ghost.style.transform = `translate(${d.x * s}px, ${d.y * s}px)`;
  };
  const up = async () => {
    window.removeEventListener("mousemove", move);
    window.removeEventListener("mouseup", up);
    document.body.classList.remove("dragging-el");
    clearGuides(P);
    if (!ghost) return;
    S.suppressClick = Date.now();
    await guarded(async () => {
      const r = isInk ? await call("move_ink", P.i, item.id, d.x, d.y) : await call("move_block", P.i, item.bbox, d.x, d.y);
      await afterChange();
      toast(isInk ? "Scritta spostata (Ctrl+Z per annullare)" : "Testo spostato (Ctrl+Z per annullare)", "ok");
      return r;
    }, isInk ? "Spostamento della scritta…" : null);
    ghost.remove();
    el.classList.remove("drag-src");
  };
  window.addEventListener("mousemove", move);
  window.addEventListener("mouseup", up);
  e.preventDefault();
  return true;
}
