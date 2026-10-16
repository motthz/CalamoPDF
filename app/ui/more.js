/* Calamo PDF Studio — tabs, side by side, presentation, read aloud, tables, OCR,
   translation, conversions, grid / guides / align */
"use strict";

Object.assign(S, { tabs: null, tabState: {}, side: null, dragSide: null, multi: [], grid: false, snap: true,
                   pres: null, speak: null, quietLoad: false });
const CM = 72 / 2.54;                    // PDF points in a centimetre
const GRID = CM / 2;                     // the grid snaps every half centimetre

// ---------------------------------------------------------------- tabs
const tabKey = t => t ? (t.path || "") + "|" + t.i : "";
async function refreshTabs() {
  let t;
  try { t = await call("tabs"); } catch (e) { return; }
  S.tabs = t;
  const bar = $("#docTabs");
  const has = t.tabs.some(x => x.pages);
  bar.hidden = !has;
  $("#stage").classList.toggle("has-tabs", has);
  bar.innerHTML = t.tabs.map(x => `<div class="doc-tab${x.i === t.active ? " active" : ""}" data-i="${x.i}" title="${esc(x.path || x.name)}">
      <svg><use href="#i-pdf"/></svg><span>${esc(x.name)}</span>${x.dirty ? '<i class="dot"></i>' : ""}
      <button class="tab-x" data-x="${x.i}" title="Chiudi (Ctrl+W)">×</button></div>`).join("")
    + `<button class="doc-tab-new" title="Apri un altro PDF in una nuova scheda (Ctrl+O)">+</button>`;
  $$(".doc-tab", bar).forEach(el => el.onclick = e => {
    if (e.target.closest(".tab-x")) closeTab(+e.target.closest(".tab-x").dataset.x);
    else if (+el.dataset.i !== t.active) switchTab(+el.dataset.i);
  });
  $$(".doc-tab", bar).forEach(el => el.onauxclick = e => { if (e.button === 1) closeTab(+el.dataset.i); });
  $(".doc-tab-new", bar).onclick = () => cmd("open");
  if (S.side) fillSideSelect();
}
function saveTabState() {
  if (!S.tabs || !S.doc) return;
  const cur = S.tabs.tabs[S.tabs.active];
  S.tabState[tabKey(cur)] = { zoom: S.zoom, scroll: $("#pages").scrollTop, page: S.current };
}
async function switchTab(i) {
  closeEditor(true); closePop(); stopSpeak();
  saveTabState();
  await guarded(async () => {
    const info = await call("switch_tab", i);
    await refreshTabs();
    if (!info) return;
    S.quietLoad = true;
    try { loadDoc(info); } finally { S.quietLoad = false; }
    const st = S.tabState[tabKey(S.tabs.tabs[S.tabs.active])];
    if (st) { S.zoom = st.zoom; layoutPages(); $("#pages").scrollTop = st.scroll; setCurrent(st.page); }
  });
}
async function closeTab(i) {
  if (!S.tabs) return;
  const t = S.tabs.tabs[i];
  if (t && t.dirty) {
    if (i !== S.tabs.active) await switchTab(i);
    if (!(await confirmDiscard())) return;
  }
  stopSpeak();
  await guarded(async () => {
    const r = await call("close_tab", i);
    delete S.tabState[tabKey(t)];
    if (S.side && S.side.tab === i) closeSide();
    await refreshTabs();
    if (r.info) { S.quietLoad = true; try { loadDoc(r.info); } finally { S.quietLoad = false; } }
    else showWelcome();
  });
}
function showWelcome() {
  S.doc = null; S.pages = [];
  $("#pages").innerHTML = ""; $("#thumbs").innerHTML = "";
  closeSide();
  setDocUI(false);
  document.title = "Calamo PDF Studio";
  $("#docName").textContent = ""; $("#dirtyDot").hidden = true;
  $("#docTabs").hidden = true; $("#stage").classList.remove("has-tabs");
}
// every way of opening a document ends in loadDoc: the tab bar follows
const _loadDoc = loadDoc;
loadDoc = function (info) {
  if (S.multi) S.multi = [];
  const r = _loadDoc(info);
  refreshTabs();
  if (S.side) loadSide();
  return r;
};
const _updateInfo = updateInfo;
updateInfo = function (info) {
  const was = S.doc && S.doc.dirty;
  _updateInfo(info);
  if (!!info.dirty !== !!was) refreshTabs();
};
// closing the window: every document with changes is asked about
requestClose = async function () {
  closeEditor(true);
  let dirty = [];
  try { dirty = await call("dirty_tabs"); } catch (e) { /* none */ }
  for (const i of dirty) {
    await switchTab(i);
    if (!(await confirmDiscard())) return;
  }
  call("close", true);
};

// ---------------------------------------------------------------- side by side
async function fillSideSelect() {
  const sel = $("#side2Tab");
  const others = S.tabs.tabs.filter(x => x.i !== S.tabs.active && x.pages);
  if (!others.length) { closeSide(); return; }
  if (!others.some(x => x.i === S.side.tab)) S.side.tab = others[0].i;
  sel.innerHTML = others.map(x => `<option value="${x.i}">${esc(x.name)}</option>`).join("");
  sel.value = String(S.side.tab);
}
$("#side2Tab").onchange = e => { S.side.tab = +e.target.value; loadSide(); };
async function loadSide() {
  if (!S.side) return;
  await fillSideSelect();
  if (!S.side) return;
  let info;
  try { info = await call("tab_info", S.side.tab); } catch (e) { return; }
  if (!info) return closeSide();
  S.side.info = info;
  const box = $("#thumbs2");
  box.innerHTML = info.pages.map((p, i) => `<div class="thumb" draggable="true" data-i="${i}"><div class="tframe" style="aspect-ratio:${p.w}/${p.h}"><img alt=""></div><span class="tnum">${i + 1}</span></div>`).join("");
  $$(".thumb", box).forEach(th => {
    const i = +th.dataset.i;
    call("render_tab", S.side.tab, i, 164 / info.pages[i].w * (window.devicePixelRatio || 1)).then(u => { $("img", th).src = u; }).catch(() => {});
    th.addEventListener("dragstart", e => { S.dragSide = i; e.dataTransfer.effectAllowed = "copy"; e.dataTransfer.setData("text/plain", "side" + i); th.classList.add("dragging"); });
    th.addEventListener("dragend", () => { S.dragSide = null; th.classList.remove("dragging"); clearDropMarks(); });
  });
}
function closeSide() {
  S.side = null;
  $("#sidebar2").hidden = true;
  $(".workspace").classList.remove("side2");
}
function clearDropMarks() { $$(".thumb").forEach(t => t.classList.remove("drop-before", "drop-after")); }
// where a dragged page lands in a list of thumbnails
function dropIndex(box, y) {
  const ths = $$(".thumb", box);
  for (let k = 0; k < ths.length; k++) {
    const r = ths[k].getBoundingClientRect();
    if (y < r.top + r.height / 2) return { at: k, el: ths[k], before: true };
  }
  return { at: ths.length, el: ths[ths.length - 1], before: false };
}
function markDrop(box, e) {
  clearDropMarks();
  const d = dropIndex(box, e.clientY);
  if (d.el) d.el.classList.add(d.before ? "drop-before" : "drop-after");
  return d;
}
// a page of the other document dropped among this document's pages: copied here
$("#thumbs").addEventListener("dragover", e => { if (S.dragSide == null) return; e.preventDefault(); markDrop($("#thumbs"), e); });
$("#thumbs").addEventListener("drop", e => {
  if (S.dragSide == null || !S.side) return;
  e.preventDefault();
  const at = dropIndex($("#thumbs"), e.clientY).at, page = S.dragSide;
  S.dragSide = null; clearDropMarks();
  guarded(async () => {
    const r = await call("copy_pages", S.side.tab, [page], S.tabs.active, at);
    S.inkCache = {};
    await afterChange(r);
    goToPage(at);
    refreshTabs();
    toast(`Pagina ${page + 1} copiata in posizione ${at + 1}`, "ok");
  });
});
// a page of this document dropped on the other one: copied there
$("#thumbs2").addEventListener("dragover", e => { if (S.dragPage == null) return; e.preventDefault(); markDrop($("#thumbs2"), e); });
$("#thumbs2").addEventListener("drop", e => {
  if (S.dragPage == null || !S.side) return;
  e.preventDefault();
  const at = dropIndex($("#thumbs2"), e.clientY).at, page = S.dragPage;
  S.dragPage = null; clearDropMarks();
  guarded(async () => {
    await call("copy_pages", S.tabs.active, [page], S.side.tab, at);
    await loadSide();
    refreshTabs();
    toast(`Pagina ${page + 1} copiata nell'altro documento`, "ok");
  });
});

// ---------------------------------------------------------------- presentation
function presentRender() {
  const P = S.pages[S.pres.i];
  if (!P) return;
  const ratio = Math.min(innerWidth / P.w, innerHeight / P.h) * (window.devicePixelRatio || 1);
  const k = S.pres.i;
  call("render", k, Math.min(ratio, 6), "png").then(u => { if (S.pres && S.pres.i === k) $("#presentImg").src = u; }).catch(() => {});
  $("#presentNum").textContent = `${k + 1} / ${S.pages.length}`;
  if (k + 1 < S.pages.length) call("render", k + 1, Math.min(ratio, 6), "png").catch(() => {});   // ready for the next one
}
function presentGo(d) {
  if (!S.pres) return;
  const n = Math.min(Math.max(S.pres.i + d, 0), S.pages.length - 1);
  if (n !== S.pres.i) { S.pres.i = n; presentRender(); }
}
function presentStart() {
  if (!S.doc) return;
  closeEditor(true); closePop();
  S.pres = { i: Math.max(S.current, 0) };
  $("#present").hidden = false;
  try { document.documentElement.requestFullscreen && document.documentElement.requestFullscreen().catch(() => {}); } catch (e) { /* windowed */ }
  presentRender();
}
function presentEnd() {
  if (!S.pres) return;
  const i = S.pres.i;
  S.pres = null;
  $("#present").hidden = true;
  $("#presentImg").removeAttribute("src");
  try { if (document.fullscreenElement) document.exitFullscreen(); } catch (e) { /* not fullscreen */ }
  goToPage(i);
}
$("#present").addEventListener("click", e => {
  if (e.target.closest(".present-bar")) return;
  presentGo(e.clientX > innerWidth / 3 ? 1 : -1);
});
$("#presentPrev").onclick = () => presentGo(-1);
$("#presentNext").onclick = () => presentGo(1);
$("#presentExit").onclick = presentEnd;
document.addEventListener("fullscreenchange", () => { if (!document.fullscreenElement && S.pres && S.pres.fs) presentEnd(); if (document.fullscreenElement && S.pres) S.pres.fs = true; });
window.addEventListener("resize", debounce(() => { if (S.pres) presentRender(); }, 150));
document.addEventListener("keydown", e => {
  if (S.pres) {
    const k = e.key;
    if (["ArrowRight", "ArrowDown", "PageDown", " ", "Enter"].includes(k)) presentGo(1);
    else if (["ArrowLeft", "ArrowUp", "PageUp", "Backspace"].includes(k)) presentGo(-1);
    else if (k === "Home") presentGo(-1e6);
    else if (k === "End") presentGo(1e6);
    else if (k === "Escape") presentEnd();
    e.preventDefault(); e.stopImmediatePropagation();
    return;
  }
  if (!S.doc || e.target.closest("input, textarea, select, [contenteditable]")) return;
  if (e.key === "F5") { e.preventDefault(); e.stopImmediatePropagation(); presentStart(); }
  if (e.ctrlKey && e.key.toLowerCase() === "w") { e.preventDefault(); e.stopImmediatePropagation(); if (S.tabs) closeTab(S.tabs.active); }
  if (e.ctrlKey && e.key === "Tab" && S.tabs && S.tabs.tabs.length > 1) {
    e.preventDefault(); e.stopImmediatePropagation();
    const n = S.tabs.tabs.length;
    switchTab((S.tabs.active + (e.shiftKey ? n - 1 : 1)) % n);
  }
  if (e.key === "Escape" && S.speak) stopSpeak();
}, true);

// ---------------------------------------------------------------- read aloud
const synth = window.speechSynthesis;
function voices() {
  const v = synth ? synth.getVoices() : [];
  return v.slice().sort((a, b) => (b.lang.startsWith("it") - a.lang.startsWith("it")) || a.name.localeCompare(b.name));
}
function fillVoices() {
  const sel = $("#speakVoice"), v = voices();
  const keep = sel.value;
  sel.innerHTML = v.map(x => `<option value="${esc(x.name)}">${esc(x.name.replace(/^Microsoft /, "").replace(/ - .*/, ""))} (${esc(x.lang)})</option>`).join("");
  if (keep && v.some(x => x.name === keep)) sel.value = keep;
  else if (S.settings && S.settings.voice && v.some(x => x.name === S.settings.voice)) sel.value = S.settings.voice;
}
if (synth) { synth.onvoiceschanged = fillVoices; setTimeout(fillVoices, 300); }
$("#speakVoice").onchange = e => saveSetting("voice", e.target.value);
async function startSpeak(page) {
  if (!synth) return toast("La lettura ad alta voce non è disponibile su questo computer.", "warn");
  stopSpeak(true);
  const items = await call("reading_order", page).catch(() => []);
  S.speak = { page, items, k: 0, paused: false };
  $("#speakBar").hidden = false;
  $("#speakPlay").textContent = "❚❚";
  $("#btnSpeak").classList.add("on");
  if (!items.length) { $("#speakInfo").textContent = `Pagina ${page + 1}: niente da leggere`; return nextSpeakPage(); }
  speakNext();
}
function speakNext() {
  const sp = S.speak;
  if (!sp) return;
  if (sp.k >= sp.items.length) return nextSpeakPage();
  const it = sp.items[sp.k];
  const u = new SpeechSynthesisUtterance(it.text);
  const v = voices().find(x => x.name === $("#speakVoice").value);
  try { if (v) { u.voice = v; u.lang = v.lang; } else u.lang = "it-IT"; } catch (e) { u.lang = (v && v.lang) || "it-IT"; }
  u.rate = +$("#speakRate").value || 1;
  const gen = sp;
  u.onend = () => { if (S.speak === gen && !gen.paused) { gen.k++; speakNext(); } };
  u.onerror = u.onend;
  sp.utter = u;
  highlightSpeak(sp.page, it.bbox);
  $("#speakInfo").textContent = `Pagina ${sp.page + 1} · ${sp.k + 1}/${sp.items.length}`;
  synth.speak(u);
}
function nextSpeakPage() {
  const sp = S.speak;
  if (!sp) return;
  if (sp.page + 1 >= S.pages.length) { stopSpeak(); toast("Lettura finita.", "ok"); return; }
  goToPage(sp.page + 1);
  startSpeak(sp.page + 1);
}
function highlightSpeak(page, bbox) {
  $$(".speak-hl").forEach(n => n.remove());
  const P = S.pages[page];
  if (!P || !bbox) return;
  const d = document.createElement("div");
  d.className = "speak-hl";
  Object.assign(d.style, box(bbox, scale(), 3));
  P.overlay.appendChild(d);
  const wrap = $("#pages"), top = P.el.offsetTop + bbox[1] * scale();
  if (top < wrap.scrollTop + 60 || top > wrap.scrollTop + wrap.clientHeight - 120) wrap.scrollTo({ top: top - wrap.clientHeight / 3, behavior: "smooth" });
}
function stopSpeak(keepBar) {
  if (!S.speak && keepBar) return;
  if (S.speak) S.speak = null;
  try { synth && synth.cancel(); } catch (e) { /* nothing to stop */ }
  $$(".speak-hl").forEach(n => n.remove());
  if (!keepBar) { $("#speakBar").hidden = true; $("#btnSpeak").classList.remove("on"); }
}
$("#speakPlay").onclick = () => {
  const sp = S.speak;
  if (!sp) return;
  if (sp.paused) { sp.paused = false; synth.resume(); $("#speakPlay").textContent = "❚❚"; if (!synth.speaking) speakNext(); }
  else { sp.paused = true; synth.pause(); $("#speakPlay").textContent = "▶"; }
};
$("#speakStop").onclick = () => stopSpeak();
$("#speakRate").onchange = () => { const sp = S.speak; if (sp && !sp.paused) { const k = sp.k; synth.cancel(); sp.k = k; setTimeout(speakNext, 50); } };

// ---------------------------------------------------------------- tables
const TB = { page: 0, id: null, cells: [] };
async function tableEdit() {
  const page = S.current;
  const list = await call("tables", page);
  if (!list.length) return toast("Non trovo tabelle con le righe disegnate in questa pagina.", "warn");
  if (list.length === 1) return openTable(page, 0);
  // several: the user clicks the one to edit
  const P = S.pages[page];
  toast("Ci sono più tabelle: clicca quella da modificare.", "");
  list.forEach(t => {
    const d = document.createElement("div");
    d.className = "table-pick";
    Object.assign(d.style, box(t.bbox, scale(), 4));
    d.onclick = e => { e.stopPropagation(); $$(".table-pick").forEach(n => n.remove()); openTable(page, t.id); };
    P.overlay.appendChild(d);
  });
}
async function openTable(page, id) {
  await guarded(async () => {
    const r = await call("table_cells", page, id);
    TB.page = page; TB.id = r.id; TB.cells = r.cells;
    renderTable();
    $("#tableModal").hidden = false;
  });
}
function renderTable() {
  const cols = Math.max(...TB.cells.map(r => r.length));
  $("#tableGrid").style.gridTemplateColumns = `repeat(${cols}, minmax(70px, 1fr)) auto`;
  $("#tableGrid").innerHTML = TB.cells.map((row, r) => row.map((c, k) => c === null
      ? `<div class="tcell merged"></div>`
      : `<textarea rows="1" data-r="${r}" data-c="${k}" spellcheck="false">${esc(c)}</textarea>`).join("")
    + `<div class="trow-btns"><button class="icon-btn" data-add="${r}" title="Aggiungi una riga sotto">+</button><button class="icon-btn danger" data-del="${r}" title="Togli questa riga"><svg><use href="#i-trash"/></svg></button></div>`).join("");
  $$("#tableGrid textarea").forEach(t => { t.onkeydown = e => e.stopPropagation(); fit(t); t.oninput = () => fit(t); });
  $$("#tableGrid [data-add]").forEach(b => b.onclick = () => tableRow(+b.dataset.add, "add"));
  $$("#tableGrid [data-del]").forEach(b => b.onclick = () => tableRow(+b.dataset.del, "delete"));
  function fit(t) { t.style.height = "auto"; t.style.height = t.scrollHeight + "px"; }
}
function tableChanges() {
  return $$("#tableGrid textarea").map(t => ({ r: +t.dataset.r, c: +t.dataset.c, text: t.value }))
    .filter(ch => (TB.cells[ch.r][ch.c] || "") !== ch.text);
}
async function tableApply(quiet) {
  const ch = tableChanges();
  if (!ch.length) return true;
  const r = await call("edit_table", TB.page, TB.id, ch);
  TB.cells = r.cells; TB.id = r.id;
  await afterChange();
  if (!quiet) toast(`Tabella aggiornata (${ch.length} ${ch.length === 1 ? "cella" : "celle"})`, "ok");
  return true;
}
async function tableRow(r, op) {
  await guarded(async () => {
    await tableApply(true);
    const res = await call("table_row", TB.page, TB.id, r, op);
    TB.cells = res.cells; TB.id = res.id;
    await afterChange();
    renderTable();
    toast(op === "add" ? `Riga aggiunta sotto la ${r + 1}` : `Riga ${r + 1} tolta`, "ok");
  }, op === "add" ? "Aggiungo la riga…" : "Tolgo la riga…");
}
$("#tableApply").onclick = () => guarded(async () => { await tableApply(); renderTable(); });
$("#tableCancel").onclick = $("#tableClose").onclick = () => { $("#tableModal").hidden = true; };

// ---------------------------------------------------------------- OCR, translation, conversions
async function ocrAll() {
  const n = S.pages.length;
  let words = 0, read = 0;
  try {
    for (let i = 0; i < n; i++) {
      busy(`Rendo cercabile il documento… pagina ${i + 1} di ${n}`, i / n);
      const info = await call("info");
      if (!(info.analyzed || []).includes(i)) { await call("analyze", i); read++; }
      const r = await call("make_searchable", i);
      words += r.written;
    }
  } catch (e) { toast(e.message, "err"); }
  finally { busy(false); }
  S.inkCache = {};
  await afterChange();
  renderInkList();
  toast(words ? `Documento cercabile: ${words} parole aggiunte (Ctrl+F per cercare).` : "Il documento era già cercabile.", "ok");
}
async function translateDoc() {
  const lang = $("#trLang").value;
  const name = $("#trLang").selectedOptions[0] ? $("#trLang").selectedOptions[0].textContent : lang;
  const ok = await modal("Tradurre il documento?", `Calamo crea una copia in ${name.toLowerCase()} in una nuova scheda; l'originale resta com'è. Il testo viene inviato al servizio di traduzione di Google.`,
    [["Annulla", false, "ghost"], ["Traduci", true, "primary"]]);
  if (!ok) return;
  await guarded(async () => {
    const r = await call("translate_doc", lang);
    loadDoc(r.info);
    toast(`Copia tradotta: ${r.translated} testi riscritti. Controllala e salvala.`, "ok");
  }, "Traduzione…");
}
async function fillTranslateLangs() {
  try {
    const l = await call("languages_translate");
    $("#trLang").innerHTML = l.map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
    $("#trLang").value = (S.settings && S.settings.trLang) || "en";
  } catch (e) { /* later */ }
}
window.addEventListener("pywebviewready", () => setTimeout(fillTranslateLangs, 400));
$("#trLang").onchange = e => saveSetting("trLang", e.target.value);
// the files window gets a third mode
segSwitch($("#filesMode"), "m", m => {
  $("#mergeBox").hidden = m !== "merge"; $("#splitBox").hidden = m !== "split"; $("#convertBox").hidden = m !== "convert";
  $("#imgGo").disabled = !S.doc;
});
const HOW = { office: "con Microsoft Office", libreoffice: "con LibreOffice", builtin: "con la conversione di Calamo (solo testo e tabelle)" };
$("#convFiles").onclick = () => guarded(async () => {
  const r = await call("convert_files");
  if (!r) return;
  $("#filesModal").hidden = true;
  loadDoc(r.info);
  const how = [...new Set(r.how.filter(h => HOW[h]))].map(h => HOW[h]);
  toast(`Convertito in PDF${how.length ? " " + how.join(", ") : ""}. Salvalo per tenerlo.`, "ok");
}, "Conversione in PDF…");
$("#convUrlGo").onclick = () => guarded(async () => {
  const url = $("#convUrl").value.trim();
  if (!url) return $("#convUrl").focus();
  const r = await call("convert_url", url);
  $("#filesModal").hidden = true;
  loadDoc(r.info);
  toast("Pagina web convertita in PDF.", "ok");
}, "Scarico la pagina…");
$("#convUrl").onkeydown = e => { e.stopPropagation(); if (e.key === "Enter") $("#convUrlGo").click(); };
$("#imgPages").onkeydown = e => e.stopPropagation();
$("#imgGo").onclick = () => guarded(async () => {
  if (!S.doc) return;
  const r = await call("export_images", $("#imgPages").value, $("#imgFmt").value, +$("#imgDpi").value);
  if (r) toast(`Salvate ${r.files.length} immagini`, "ok", { label: "Apri cartella", run: () => call("reveal", r.folder) });
}, "Esportazione delle immagini…");

Object.assign(COMMANDS, {
  sideBySide: async () => {
    if (S.side) return closeSide();
    await refreshTabs();
    const others = S.tabs.tabs.filter(x => x.i !== S.tabs.active && x.pages);
    if (!others.length) return toast("Apri un altro PDF (si apre in una nuova scheda) per affiancarlo e trascinare le pagine tra i due.", "warn");
    S.side = { tab: others[0].i };
    $("#sidebar2").hidden = false;
    $(".workspace").classList.add("side2");
    loadSide();
  },
  sideClose: closeSide,
  present: presentStart,
  speak: () => { if (S.speak) stopSpeak(); else startSpeak(Math.max(S.current, 0)); },
  tableEdit: () => guarded(tableEdit),
  ocrAll, translate: translateDoc,
  convertTool: () => openFilesModal("convert"),
});

// opening never discards a document: it goes to a new tab
openPath = async function (path) {
  await guarded(async () => openResult(await call("open_path", path)), "Apertura del documento…");
};
openDialog = async function () {
  await guarded(async () => { await openResult(await call("open_dialog")); });
};
newDoc = async function () {
  await guarded(async () => loadDoc(await call("new_doc")));
};
COMMANDS.open = () => openDialog();
COMMANDS.newDoc = () => newDoc();

// ---------------------------------------------------------------- grid, guides, several elements
function itemsOf(P) {
  const d = P.data || {};
  return [
    ...(d.objects || []).map(o => ({ kind: "obj", id: o.id, src: o.src, rect: o.rect })),
    ...(d.blocks || []).map(b => ({ kind: "blk", id: b.id, rect: b.bbox })),
    ...(d.ink || []).filter(l => l.source !== "unread" || l.text).map(l => ({ kind: "ink", id: l.id, rect: l.bbox })),
    ...(d.graphics || []).filter(g => !(S.tool === "select" && g.kind === "image"))
      .map(g => ({ kind: "gfx", id: g.id, rect: g.bbox, gkind: g.kind, xref: g.xref })),
  ];
}
const sameItem = (a, b) => a.kind === b.kind && String(a.id) === String(b.id);
// the drag of one element: its edges and centre stick to the others', to the page and to the grid
function snapDrag(P, rect, d, self) {
  clearGuides(P);
  if (!S.snap && !S.grid) return d;
  const th = 6 / scale();
  let dx = d.x, dy = d.y;
  const r = [rect[0] + dx, rect[1] + dy, rect[2] + dx, rect[3] + dy];
  const gx = [], gy = [];
  if (S.grid) {
    const sx = Math.round(r[0] / GRID) * GRID - r[0], sy = Math.round(r[1] / GRID) * GRID - r[1];
    if (Math.abs(sx) < th) dx += sx;
    if (Math.abs(sy) < th) dy += sy;
    return { x: dx, y: dy };
  }
  const xs = [P.w / 2], ys = [P.h / 2];
  itemsOf(P).forEach(it => {
    if (self && sameItem(it, self)) return;
    const q = it.rect;
    xs.push(q[0], (q[0] + q[2]) / 2, q[2]);
    ys.push(q[1], (q[1] + q[3]) / 2, q[3]);
  });
  const best = (mine, targets) => {
    let b = null;
    mine.forEach(m => targets.forEach(t => { const dd = t - m; if (Math.abs(dd) < th && (!b || Math.abs(dd) < Math.abs(b.d))) b = { d: dd, at: t }; }));
    return b;
  };
  const bx = best([r[0], (r[0] + r[2]) / 2, r[2]], xs), by = best([r[1], (r[1] + r[3]) / 2, r[3]], ys);
  if (bx) { dx += bx.d; gx.push(bx.at); }
  if (by) { dy += by.d; gy.push(by.at); }
  const s = scale();
  gx.forEach(x => { const g = document.createElement("div"); g.className = "guide v"; g.style.left = x * s + "px"; P.overlay.appendChild(g); });
  gy.forEach(y => { const g = document.createElement("div"); g.className = "guide h"; g.style.top = y * s + "px"; P.overlay.appendChild(g); });
  return { x: dx, y: dy };
}
function clearGuides(P) { $$(".guide", P ? P.overlay : document).forEach(n => n.remove()); }

// Maiusc+clic: choose several elements of a page
function multiSelectDown(P, e) {
  if (S.tool !== "select" || !e.shiftKey) return false;
  const el = e.target.closest(".obj, .blk, .ink, .gfx");
  if (!el) return false;
  e.preventDefault();
  const kind = el.classList.contains("obj") ? "obj" : el.classList.contains("blk") ? "blk" : el.classList.contains("gfx") ? "gfx" : "ink";
  const it = itemsOf(P).find(x => x.kind === kind && String(x.id) === String(el.dataset.id));
  if (!it) return true;
  if (S.multi.length && S.multi[0].page !== P.i) S.multi = [];
  // the element selected with a simple click joins the group
  if (!S.multi.length && S.sel && S.sel.page === P.i) {
    const o = objOf(P, S.sel.id);
    if (o) S.multi.push({ page: P.i, kind: "obj", id: o.id, src: o.src, rect: o.rect });
  }
  const k = S.multi.findIndex(x => sameItem(x, it));
  if (k >= 0) S.multi.splice(k, 1); else S.multi.push({ page: P.i, ...it });
  drawOverlay(P);
  updateMulti();
  return true;
}
function updateMulti() {
  const n = S.multi.length;
  $("#multiCount").textContent = n > 1 ? `${n} elementi` : n === 1 ? "1 elemento" : "";
  $("#alignBtns").title = n ? "" : "Maiusc+clic sugli elementi per sceglierne più d'uno";
  $$("#alignBtns button").forEach(b => { b.disabled = !n && !S.sel || (b.dataset.al.startsWith("dist") && n < 3); });
}
function drawMulti(P, frag, s) {
  S.multi.filter(m => m.page === P.i).forEach(m => {
    const it = itemsOf(P).find(x => sameItem(x, m));
    if (!it) return;
    const d = document.createElement("div");
    d.className = "multi-box";
    Object.assign(d.style, box(it.rect, s, 3));
    frag.appendChild(d);
  });
  if (S.grid) {
    // the ruler, in centimetres, along the top and the left of the page
    const top = document.createElement("div"), left = document.createElement("div");
    top.className = "ruler top"; left.className = "ruler left";
    let ht = "", hl = "";
    for (let c = 0; c * CM <= P.w; c++) ht += `<i style="left:${c * CM * s}px">${c || ""}</i>`;
    for (let c = 0; c * CM <= P.h; c++) hl += `<i style="top:${c * CM * s}px">${c || ""}</i>`;
    top.innerHTML = ht; left.innerHTML = hl;
    frag.appendChild(top); frag.appendChild(left);
  }
}
async function alignItems(how) {
  let sel = S.multi.slice();
  if (!sel.length && S.sel) {
    const P = S.pages[S.sel.page], o = objOf(P, S.sel.id);
    if (o) sel = [{ page: P.i, kind: "obj", id: o.id, src: o.src, rect: o.rect }];
  }
  if (!sel.length) return toast("Scegli gli elementi: clic, poi Maiusc+clic sugli altri.", "warn");
  const P = S.pages[sel[0].page];
  // fresh positions
  sel = sel.map(m => ({ ...m, ...(itemsOf(P).find(x => sameItem(x, m)) || {}), page: m.page }));
  const one = sel.length === 1, M = 36;
  const L = one ? M : Math.min(...sel.map(m => m.rect[0])), R = one ? P.w - M : Math.max(...sel.map(m => m.rect[2]));
  const T = one ? M : Math.min(...sel.map(m => m.rect[1])), B = one ? P.h - M : Math.max(...sel.map(m => m.rect[3]));
  const moves = [];
  const add = (m, dx, dy) => moves.push({ kind: m.kind, id: m.id, src: m.src, bbox: m.rect, rect: m.rect, dx, dy,
                                         gkind: m.gkind, xref: m.xref });
  if (how.startsWith("dist")) {
    const h = how === "dist-h", a = h ? 0 : 1, b = h ? 2 : 3;
    const s = sel.slice().sort((p, q) => p.rect[a] - q.rect[a]);
    const total = s.reduce((t, m) => t + m.rect[b] - m.rect[a], 0);
    const gap = (s[s.length - 1].rect[b] - s[0].rect[a] - total) / (s.length - 1);
    let pos = s[0].rect[a];
    s.forEach(m => { const d = pos - m.rect[a]; add(m, h ? d : 0, h ? 0 : d); pos += m.rect[b] - m.rect[a] + gap; });
  } else {
    sel.forEach(m => {
      const r = m.rect, w = r[2] - r[0], hh = r[3] - r[1];
      const dx = { left: L - r[0], right: R - r[2], hcenter: (L + R) / 2 - (r[0] + w / 2) }[how] || 0;
      const dy = { top: T - r[1], bottom: B - r[3], vcenter: (T + B) / 2 - (r[1] + hh / 2) }[how] || 0;
      add(m, dx, dy);
    });
  }
  await guarded(async () => {
    await call("arrange", P.i, moves.filter(m => Math.abs(m.dx) + Math.abs(m.dy) > 0.3));
    S.multi = []; S.sel = null;
    await afterChange();
    updateMulti();
    toast("Elementi allineati (Ctrl+Z per annullare)", "ok");
  });
}
$$("#alignBtns button").forEach(b => b.onclick = () => alignItems(b.dataset.al));
$("#gridToggle").onclick = () => {
  S.grid = !S.grid;
  $("#gridToggle").classList.toggle("on", S.grid);
  $("#stage").classList.toggle("show-grid", S.grid);
  setGridSize();
  S.pages.forEach(drawOverlay);
};
$("#snapToggle").onclick = () => { S.snap = !S.snap; $("#snapToggle").classList.toggle("on", S.snap); };
function setGridSize() { $("#stage").style.setProperty("--grid", GRID * scale() + "px"); }
const _layoutPages = layoutPages;
layoutPages = function () { _layoutPages(); setGridSize(); };
updateMulti();
