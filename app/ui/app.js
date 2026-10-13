/* Calamo PDF Studio — UI controller */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const PT = 4 / 3;            // css px per PDF point at 100%
const TOOL_NAMES = { view: "Naviga", edit: "Modifica", text: "Nuovo testo", hand: "Scrittura a mano", erase: "Gomma" };

const isDoubt = l => l.source === "ocr" && l.doubt > 0.4 && l.kind !== "print";

const S = {
  doc: null, zoom: 1, tool: "edit", current: 0, pages: [], fonts: [], settings: {},
  hits: [], hitIdx: -1, editing: null, pop: null, handHeight: 14, handInk: "#1f2a5a", glyphCount: 0,
  newStyle: { family: "Arial", size: 12, color: "#111111", bold: false, italic: false },
  inkCache: {},                 // page -> ink lines
};

// ---------------------------------------------------------------- bridge
async function call(name, ...args) {
  const r = await window.pywebview.api[name](...args);
  if (r && r.ok === false) throw new Error(r.error);
  return r ? r.data : r;
}
async function guarded(fn, busyText) {
  try {
    if (busyText) busy(busyText);
    return await fn();
  } catch (e) {
    toast(e.message || String(e), "err");
  } finally {
    if (busyText) busy(false);
  }
}
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const scale = () => S.zoom * PT;
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ---------------------------------------------------------------- feedback
function toast(msg, kind = "", action) {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.innerHTML = `<i></i><span>${esc(msg)}</span>`;
  if (action) {
    const b = document.createElement("button");
    b.textContent = action.label;
    b.onclick = () => { action.run(); close(); };
    el.appendChild(b);
  }
  $("#toasts").appendChild(el);
  const close = () => { el.classList.add("out"); setTimeout(() => el.remove(), 260); };
  setTimeout(close, action ? 7000 : kind === "err" ? 6000 : 3400);
}
function busy(text, frac) {
  const b = $("#busy");
  if (text === false) { b.hidden = true; return; }
  b.hidden = false;
  $("#busyText").textContent = text;
  $("#busyBar").style.width = frac == null ? "0" : Math.round(frac * 100) + "%";
  $("#busyBar").parentElement.style.display = frac == null ? "none" : "";
}
function modal(title, text, buttons) {
  return new Promise(resolve => {
    $("#modalTitle").textContent = title;
    $("#modalText").textContent = text;
    const box = $("#modalActions");
    box.innerHTML = "";
    buttons.forEach(([label, value, cls]) => {
      const b = document.createElement("button");
      b.className = "btn sm " + (cls || "");
      b.textContent = label;
      b.onclick = () => { $("#modal").hidden = true; resolve(value); };
      box.appendChild(b);
    });
    $("#modal").hidden = false;
  });
}
function status(msg) { $("#stMsg").textContent = msg || ""; }

// ---------------------------------------------------------------- boot
window.addEventListener("pywebviewready", async () => {
  const b = await call("boot");
  S.settings = b.settings;
  S.fonts = b.fonts;
  applyTheme(S.settings.theme || "dark");
  fillSelect($("#ebFont"), S.fonts);
  fillSelect($("#newFont"), S.fonts.filter(f => f !== "Originale"));
  $("#newFont").value = "Arial";
  fillSelect($("#ocrLang"), [["", "Lingua del sistema"], ...b.languages.map(l => [l, l])]);
  $("#ocrLang").value = S.settings.ocrLang || "";
  $("#autoUpdate").checked = S.settings.autoUpdate !== false;
  $("#about").textContent = `Calamo PDF Studio ${b.version}`;
  S.version = b.version;
  renderRecent(b.recent);
  updateLearning(b.learning);
  setDocUI(false);
  if (!S.settings.tutorialDone) startTour();
  if (b.startup) openPath(b.startup);
  setTimeout(() => checkUpdate(false), 2500);
});

function fillSelect(sel, items) {
  sel.innerHTML = items.map(it => Array.isArray(it)
    ? `<option value="${esc(it[0])}">${esc(it[1])}</option>`
    : `<option value="${esc(it)}" style="font-family:'${esc(it)}'">${esc(it)}</option>`).join("");
}
function renderRecent(list) {
  $("#recentWrap").hidden = !list.length;
  $("#recentList").innerHTML = list.map(r => `<button class="recent-item" data-path="${esc(r.path)}">
    <svg><use href="#i-pdf"/></svg><div><b>${esc(r.name)}</b><small>${esc(r.folder)}</small></div></button>`).join("");
  $$("#recentList .recent-item").forEach(el => el.onclick = () => openPath(el.dataset.path));
}
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  $$("#themeSwitch button").forEach(b => b.classList.toggle("active", b.dataset.theme === t));
}
function setDocUI(has) {
  $$("[data-need-doc]").forEach(el => { el.disabled = !has; });
  $("#tools").classList.toggle("disabled", !has);
  $("#welcome").hidden = has;
}

// ---------------------------------------------------------------- document
async function openPath(path) {
  if (!(await confirmDiscard())) return;
  await guarded(async () => loadDoc(await call("open_path", path)), "Apertura del documento…");
}
async function openDialog() {
  if (!(await confirmDiscard())) return;
  await guarded(async () => { const info = await call("open_dialog"); if (info) loadDoc(info); });
}
async function newDoc() {
  if (!(await confirmDiscard())) return;
  await guarded(async () => loadDoc(await call("new_doc")));
}
async function confirmDiscard() {
  if (!S.doc || !S.doc.dirty) return true;
  const r = await modal("Modifiche non salvate", `Vuoi salvare le modifiche a “${S.doc.name}”?`,
    [["Annulla", null, "ghost"], ["Non salvare", "discard", "ghost"], ["Salva", "save", "primary"]]);
  if (r === "save") return !!(await save());
  return r === "discard";
}

function loadDoc(info) {
  closeEditor(false); closePop();
  S.doc = info;
  S.inkCache = {};
  S.hits = []; S.hitIdx = -1;
  setDocUI(true);
  buildPages();
  updateInfo(info);
  zoomFit();
  S.current = -1;
  setCurrent(0);
  refreshHand();
  renderInkList();
  toast(`Aperto “${info.name}”`, "ok");
}

function updateInfo(info) {
  S.doc = info;
  $("#docName").textContent = info.name;
  $("#dirtyDot").hidden = !info.dirty;
  $("#btnUndo").disabled = !info.canUndo;
  $("#btnRedo").disabled = !info.canRedo;
  $("#pageCount").textContent = info.pages.length;
  document.title = `${info.dirty ? "• " : ""}${info.name} — Calamo PDF Studio`;
}

// after any change: re-render the pages whose version moved
async function afterChange(info) {
  info = info || await call("info");
  const rebuild = info.pages.length !== S.pages.length;
  updateInfo(info);
  if (rebuild) { buildPages(); layoutPages(); return; }
  info.pages.forEach((p, i) => {
    const P = S.pages[i];
    if (P.v !== p.v || P.w !== p.w || P.h !== p.h) {
      P.v = p.v; P.w = p.w; P.h = p.h;
      P.dataV = -1;
      sizePage(P);
      if (P.visible) loadPage(P, true);
      P.thumbKey = null;
      if (P.thumbVisible) loadThumb(P);
    }
  });
}

// ---------------------------------------------------------------- pages
let pageObserver, thumbObserver;
function buildPages() {
  const wrap = $("#pages"), thumbs = $("#thumbs");
  wrap.innerHTML = ""; thumbs.innerHTML = "";
  pageObserver && pageObserver.disconnect();
  thumbObserver && thumbObserver.disconnect();
  pageObserver = new IntersectionObserver(es => es.forEach(e => {
    const P = S.pages[+e.target.dataset.i];
    if (!P) return;
    P.visible = e.isIntersecting;
    if (P.visible) loadPage(P);
  }), { root: wrap, rootMargin: "900px 0px" });
  thumbObserver = new IntersectionObserver(es => es.forEach(e => {
    const P = S.pages[+e.target.dataset.i];
    if (!P) return;
    P.thumbVisible = e.isIntersecting;
    if (P.thumbVisible) loadThumb(P);
  }), { root: thumbs, rootMargin: "400px 0px" });

  S.pages = S.doc.pages.map((p, i) => {
    const el = document.createElement("div");
    el.className = "page"; el.dataset.i = i;
    el.innerHTML = `<div class="shimmer"></div><img class="render" alt=""><div class="overlay"></div><div class="page-num">${i + 1}</div>`;
    wrap.appendChild(el);
    const th = document.createElement("div");
    th.className = "thumb"; th.dataset.i = i;
    th.innerHTML = `<div class="tframe"><img alt=""></div><span class="tnum">${i + 1}</span>`;
    th.onclick = () => goToPage(i);
    thumbs.appendChild(th);
    const P = { i, w: p.w, h: p.h, v: p.v, el, th, img: $("img.render", el), overlay: $(".overlay", el),
      renderKey: null, dataV: -1, data: null, visible: false, thumbVisible: false, thumbKey: null, canvas: null };
    bindOverlay(P);
    pageObserver.observe(el);
    thumbObserver.observe(th);
    return P;
  });
  layoutPages();
}
function sizePage(P) {
  P.el.style.width = P.w * scale() + "px";
  P.el.style.height = P.h * scale() + "px";
  const tw = 164;
  $(".tframe", P.th).style.aspectRatio = `${P.w} / ${P.h}`;
  return tw;
}
function layoutPages() {
  S.pages.forEach(P => { sizePage(P); P.renderKey = null; });
  S.pages.filter(P => P.visible).forEach(P => loadPage(P));
  $("#zoomVal").textContent = Math.round(S.zoom * 100) + "%";
}
async function loadPage(P, force) {
  const k = `${P.v}@${S.zoom}`;
  if (P.renderKey !== k || force) {
    P.renderKey = k;
    const ratio = Math.min(Math.max(window.devicePixelRatio || 1, 1) * scale(), 6);
    try {
      const url = await call("render", P.i, ratio, "png");
      if (P.renderKey === k) {
        P.img.src = url;
        P.canvas = null;
        P.img.onload = () => { const sh = $(".shimmer", P.el); sh && sh.remove(); };
      }
    } catch (e) { toast(e.message, "err"); }
  }
  if (P.dataV !== P.v) {
    P.dataV = P.v;
    try {
      const d = await call("blocks", P.i);
      P.data = d;
      S.inkCache[P.i] = d.ink;
      drawOverlay(P);
      if (P.i === S.current) renderInkList();
    } catch (e) { toast(e.message, "err"); }
  } else drawOverlay(P);
}
async function loadThumb(P) {
  const k = String(P.v);
  if (P.thumbKey === k) return;
  P.thumbKey = k;
  try {
    const url = await call("render", P.i, 164 / P.w * (window.devicePixelRatio || 1), "jpeg");
    $("img", P.th).src = url;
  } catch (e) { /* thumbnails are best effort */ }
}
function drawOverlay(P) {
  if (!P.data) return;
  const s = scale(), ov = P.overlay;
  $$(".blk, .ink, .hit", ov).forEach(n => n.remove());
  const frag = document.createDocumentFragment();
  P.data.blocks.forEach(b => {
    const d = document.createElement("div");
    d.className = "blk";
    d.dataset.id = b.id;
    Object.assign(d.style, box(b.bbox, s, 2));
    frag.appendChild(d);
  });
  (P.data.ink || []).forEach(l => {
    if (l.kind === "print" && l.synthetic) return;   // rewritten print is now normal text: edit it as such
    const d = document.createElement("div");
    d.className = "ink" + (isDoubt(l) ? " doubt" : "") + (l.kind === "print" ? " print" : "");
    d.dataset.id = l.id;
    d.title = (l.kind === "print" ? "Stampato: " : "") + l.text;
    Object.assign(d.style, box(l.bbox, s, 3));
    if (isDoubt(l)) d.innerHTML = `<span class="ink-tag">?</span>`;
    frag.appendChild(d);
  });
  S.hits.forEach((h, k) => {
    if (h.page !== P.i) return;
    const d = document.createElement("div");
    d.className = "hit" + (k === S.hitIdx ? " current" : "");
    Object.assign(d.style, box(h.bbox, s, 1));
    frag.appendChild(d);
  });
  ov.appendChild(frag);
  P.el.classList.toggle("show-ink", (P.data.ink || []).length > 0);
}
function box(b, s, pad = 0) {
  return { left: b[0] * s - pad + "px", top: b[1] * s - pad + "px",
    width: (b[2] - b[0]) * s + 2 * pad + "px", height: (b[3] - b[1]) * s + 2 * pad + "px" };
}

// current page tracking
$("#pages").addEventListener("scroll", debounce(() => {
  const wrap = $("#pages"), mid = wrap.scrollTop + wrap.clientHeight / 2;
  let best = 0, bd = Infinity;
  S.pages.forEach(P => {
    const c = P.el.offsetTop + P.el.offsetHeight / 2;
    if (Math.abs(c - mid) < bd) { bd = Math.abs(c - mid); best = P.i; }
  });
  setCurrent(best);
}, 60));
function setCurrent(i) {
  if (!S.pages.length) return;
  const changed = S.current !== i;
  S.current = i;
  S.pages.forEach(P => P.th.classList.toggle("active", P.i === i));
  $("#stPage").textContent = `Pagina ${i + 1} di ${S.pages.length}`;
  $("#pageLabel").textContent = `${i + 1} di ${S.pages.length}`;
  if (changed) {
    const th = S.pages[i].th;
    th.scrollIntoView({ block: "nearest", behavior: "smooth" });
    renderInkList();
  }
}
function goToPage(i, y) {
  const P = S.pages[i];
  if (!P) return;
  const wrap = $("#pages");
  const top = P.el.offsetTop - 24 + (y ? y * scale() - wrap.clientHeight / 3 : 0);
  wrap.scrollTo({ top, behavior: "smooth" });
  setCurrent(i);
}

// ---------------------------------------------------------------- zoom
function setZoom(z, anchor) {
  const wrap = $("#pages");
  const old = S.zoom;
  z = Math.min(4, Math.max(0.25, z));
  if (Math.abs(z - old) < 0.001) return;
  const ax = anchor ? anchor.x : wrap.clientWidth / 2, ay = anchor ? anchor.y : wrap.clientHeight / 2;
  const fx = (wrap.scrollLeft + ax) / old, fy = (wrap.scrollTop + ay) / old;
  S.zoom = z;
  closeEditor(true); closePop();
  layoutPages();
  wrap.scrollLeft = fx * z - ax;
  wrap.scrollTop = fy * z - ay;
}
function zoomFit() {
  if (!S.pages.length) return;
  const w = $("#pages").clientWidth - 90;
  const maxW = Math.max(...S.pages.map(P => P.w));
  S.zoom = Math.min(2, Math.max(0.3, w / (maxW * PT)));
  layoutPages();
}
$("#pages").addEventListener("wheel", e => {
  if (!e.ctrlKey || !S.doc) return;
  e.preventDefault();
  const r = $("#pages").getBoundingClientRect();
  setZoom(S.zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1), { x: e.clientX - r.left, y: e.clientY - r.top });
}, { passive: false });

// ---------------------------------------------------------------- tools
function setTool(t) {
  if (!S.doc) return;
  closeEditor(true); closePop();
  S.tool = t;
  $$("#tools button").forEach(b => b.classList.toggle("active", b.dataset.tool === t));
  const st = $("#stage");
  st.className = "stage tool-" + t;
  $("#stTool").textContent = TOOL_NAMES[t];
  if (t === "hand" && !S.glyphCount)
    toast("Analizza una pagina scritta a mano per far imparare la calligrafia. Nel frattempo uso un carattere calligrafico.", "warn");
}
$("#stage").className = "stage tool-edit";
setDocUI(false);

function pagePoint(P, e) {
  const r = P.el.getBoundingClientRect();
  return { x: (e.clientX - r.left) / scale(), y: (e.clientY - r.top) / scale() };
}
function bindOverlay(P) {
  const ov = P.overlay;
  ov.addEventListener("mousedown", e => {
    if (e.button !== 0 || e.target.closest(".editor")) return;
    if (S.tool === "erase") return startErase(P, e);
  });
  ov.addEventListener("click", e => {
    if (e.target.closest(".editor")) return;
    if (S.editing) { applyEditor(); return; }
    if (S.pop) { closePop(); return; }
    setCurrent(P.i);
    const pt = pagePoint(P, e);
    if (S.tool === "edit") {
      const blk = e.target.closest(".blk"), ink = e.target.closest(".ink");
      if (ink) return openInk(P, (P.data.ink || []).find(l => l.id === +ink.dataset.id), ink);
      if (blk) return startEdit(P, P.data.blocks.find(b => b.id === +blk.dataset.id), e);
    } else if (S.tool === "text") {
      startNewText(P, pt);
    } else if (S.tool === "hand") {
      openHandNew(P, pt, e);
    }
  });
}

// ---------------------------------------------------------------- erase tool
function startErase(P, e) {
  e.preventDefault();
  const a = pagePoint(P, e), s = scale();
  const r = document.createElement("div");
  r.className = "sel-rect";
  P.overlay.appendChild(r);
  let b = a;
  const move = ev => {
    b = pagePoint(P, ev);
    Object.assign(r.style, box([Math.min(a.x, b.x), Math.min(a.y, b.y), Math.max(a.x, b.x), Math.max(a.y, b.y)], s));
  };
  const up = async () => {
    window.removeEventListener("mousemove", move);
    window.removeEventListener("mouseup", up);
    r.remove();
    const rect = [Math.min(a.x, b.x), Math.min(a.y, b.y), Math.max(a.x, b.x), Math.max(a.y, b.y)];
    if (rect[2] - rect[0] < 3 || rect[3] - rect[1] < 3) return;
    await guarded(async () => { await call("erase_area", P.i, rect); await afterChange(); toast("Area cancellata", "ok"); });
  };
  window.addEventListener("mousemove", move);
  window.addEventListener("mouseup", up);
}

// ---------------------------------------------------------------- in-place editor
function sampleBg(P, x, y) {
  try {
    if (!P.img.complete || !P.img.naturalWidth) return "#ffffff";
    if (!P.canvas) {
      P.canvas = document.createElement("canvas");
      P.canvas.width = P.img.naturalWidth; P.canvas.height = P.img.naturalHeight;
      P.canvas.getContext("2d", { willReadFrequently: true }).drawImage(P.img, 0, 0);
    }
    const k = P.img.naturalWidth / P.w;
    const px = P.canvas.getContext("2d").getImageData(Math.max(0, Math.round(x * k)), Math.max(0, Math.round(y * k)), 1, 1).data;
    return `rgb(${px[0]},${px[1]},${px[2]})`;
  } catch (e) { return "#ffffff"; }
}

function startEdit(P, b, ev) {
  if (!b) return;
  const s = scale();
  const ed = document.createElement("div");
  ed.className = "editor";
  ed.contentEditable = "plaintext-only";
  ed.spellcheck = false;
  ed.textContent = b.text;
  const multi = b.nlines > 1;
  const lh = (multi ? b.lineHeight : b.size * 1.18) * s;
  Object.assign(ed.style, {
    left: b.bbox[0] * s - 1 + "px",
    top: (b.firstBaseline - b.size * 0.93) * s + "px",
    minWidth: (b.bbox[2] - b.bbox[0]) * s + 4 + "px",
    width: multi ? (b.bbox[2] - b.bbox[0]) * s + 4 + "px" : "auto",
    maxWidth: multi ? "" : (P.w - b.bbox[0] - 18) * s + "px",
    fontFamily: `'${b.css}'`, fontSize: b.size * s + "px", lineHeight: lh + "px",
    color: b.color, fontWeight: b.bold ? 700 : 400, fontStyle: b.italic ? "italic" : "normal",
    background: sampleBg(P, b.bbox[0] - 2, b.bbox[1] - 2), textAlign: b.align || "left",
  });
  P.overlay.appendChild(ed);
  $$(".blk", P.overlay).forEach(n => { if (+n.dataset.id === b.id) n.style.visibility = "hidden"; });
  S.editing = { P, b, ed, kind: "edit", style: {} };
  ed.focus();
  placeCaret(ed, ev);
  openEditBar(b);
  ed.addEventListener("keydown", editorKeys);
}
function startNewText(P, pt) {
  const s = scale(), st = S.newStyle;
  const ed = document.createElement("div");
  ed.className = "editor";
  ed.contentEditable = "plaintext-only";
  ed.spellcheck = false;
  Object.assign(ed.style, {
    left: pt.x * s + "px", top: pt.y * s + "px", minWidth: "40px", maxWidth: (P.w - pt.x - 10) * s + "px",
    fontFamily: `'${st.family}'`, fontSize: st.size * s + "px", lineHeight: st.size * 1.25 * s + "px",
    color: st.color, fontWeight: st.bold ? 700 : 400, fontStyle: st.italic ? "italic" : "normal",
    background: "transparent",
  });
  P.overlay.appendChild(ed);
  S.editing = { P, pt, ed, kind: "new", style: {} };
  ed.focus();
  openEditBar({ css: st.family, size: st.size, color: st.color, bold: st.bold, italic: st.italic, align: "left" }, true);
  ed.addEventListener("keydown", editorKeys);
}
function placeCaret(ed, ev) {
  try {
    const r = ev && document.caretRangeFromPoint(ev.clientX, ev.clientY);
    const sel = getSelection();
    sel.removeAllRanges();
    if (r && ed.contains(r.startContainer)) sel.addRange(r);
    else { const rr = document.createRange(); rr.selectNodeContents(ed); rr.collapse(false); sel.addRange(rr); }
  } catch (e) { /* caret placement is cosmetic */ }
}
function editorKeys(e) {
  if (e.key === "Escape") { e.preventDefault(); closeEditor(false); }
  else if (e.key === "Enter" && e.ctrlKey) { e.preventDefault(); applyEditor(); }
  e.stopPropagation();
}
function openEditBar(b, isNew) {
  const bar = $("#editBar");
  bar.hidden = false;
  $("#ebFont").value = isNew ? b.css : "Originale";
  $("#ebSize").value = b.size;
  $("#ebColor").value = toHex(b.color);
  $("#ebBold").classList.toggle("on", !!b.bold);
  $("#ebItalic").classList.toggle("on", !!b.italic);
  $$("#editBar [data-align]").forEach(x => x.classList.toggle("on", x.dataset.align === (b.align || "left")));
  $("#ebDelete").hidden = !!isNew;
  positionEditBar();
}
function positionEditBar() {
  if (!S.editing) return;
  const r = S.editing.ed.getBoundingClientRect(), bar = $("#editBar");
  const bw = bar.offsetWidth, bh = bar.offsetHeight;
  let top = r.top - bh - 12;
  if (top < 100) top = r.bottom + 12;
  let left = Math.min(Math.max(r.left, 12), window.innerWidth - bw - 12);
  bar.style.top = top + "px"; bar.style.left = left + "px";
}
$("#pages").addEventListener("scroll", () => { positionEditBar(); positionPop(); });
function toHex(c) {
  if (!c) return "#000000";
  if (c[0] === "#") return c.length === 7 ? c : "#000000";
  const m = c.match(/\d+/g);
  return m ? "#" + m.slice(0, 3).map(v => (+v).toString(16).padStart(2, "0")).join("") : "#000000";
}
function editStyle(key, value, css) {
  const E = S.editing;
  if (!E) return;
  E.style[key] = value;
  Object.assign(E.ed.style, css);
  if (E.kind === "new") {
    const map = { family: "family", size: "size", color: "color", bold: "bold", italic: "italic" };
    if (map[key]) S.newStyle[map[key]] = value;
  }
  positionEditBar();
  E.ed.focus();
}
$("#ebFont").onchange = e => {
  const v = e.target.value;
  const fam = v === "Originale" ? (S.editing && S.editing.b ? S.editing.b.css : "Arial") : v;
  editStyle("family", v, { fontFamily: `'${fam}'` });
};
$("#ebSize").oninput = e => {
  const v = parseFloat(e.target.value);
  if (!v || !S.editing) return;
  const s = scale(), E = S.editing;
  const ratio = E.b ? (E.b.nlines > 1 ? E.b.lineHeight / E.b.size : 1.18) : 1.25;
  editStyle("size", v, { fontSize: v * s + "px", lineHeight: v * ratio * s + "px" });
};
$("#ebColor").oninput = e => editStyle("color", e.target.value, { color: e.target.value });
$("#ebBold").onclick = () => { const on = $("#ebBold").classList.toggle("on"); editStyle("bold", on, { fontWeight: on ? 700 : 400 }); };
$("#ebItalic").onclick = () => { const on = $("#ebItalic").classList.toggle("on"); editStyle("italic", on, { fontStyle: on ? "italic" : "normal" }); };
$$("#editBar [data-align]").forEach(btn => btn.onclick = () => {
  $$("#editBar [data-align]").forEach(x => x.classList.toggle("on", x === btn));
  editStyle("align", btn.dataset.align, { textAlign: btn.dataset.align });
});
$("#ebApply").onclick = () => applyEditor();
$("#ebCancel").onclick = () => closeEditor(false);
$("#ebDelete").onclick = async () => {
  const E = S.editing;
  if (!E || !E.b) return;
  closeEditor(false);
  await guarded(async () => { await call("delete_block", E.P.i, E.b.bbox); await afterChange(); toast("Testo eliminato", "ok"); });
};
$("#editBar").addEventListener("mousedown", e => { if (!e.target.closest("select, input")) e.preventDefault(); });

function closeEditor(apply) {
  if (!S.editing) return;
  if (apply) return applyEditor();
  const E = S.editing;
  S.editing = null;
  E.ed.remove();
  $("#editBar").hidden = true;
  $$(".blk", E.P.overlay).forEach(n => n.style.visibility = "");
}
async function applyEditor() {
  const E = S.editing;
  if (!E) return;
  const text = E.ed.innerText.replace(/ /g, " ").replace(/\n+$/, "");
  S.editing = null;
  $("#editBar").hidden = true;
  const restore = () => { E.ed.remove(); $$(".blk", E.P.overlay).forEach(n => n.style.visibility = ""); };
  if (E.kind === "edit") {
    const styled = Object.keys(E.style).length > 0;
    if (text === E.b.text && !styled) return restore();
    E.ed.contentEditable = "false";
    await guarded(async () => {
      const res = await call("edit_block", E.P.i, E.b.bbox, text, E.style);
      await afterChange();
      if (res && res.warning) toast(res.warning, "warn");
      else toast("Testo aggiornato", "ok");
    });
    setTimeout(restore, 120);
  } else {
    if (!text.trim()) return restore();
    const st = { ...S.newStyle };
    await guarded(async () => { await call("add_text", E.P.i, E.pt.x, E.pt.y, text, st); await afterChange(); toast("Testo aggiunto", "ok"); });
    setTimeout(restore, 120);
  }
}
document.addEventListener("mousedown", e => {
  if (S.editing && !e.target.closest(".editor, #editBar, .modal")) {
    if (e.target.closest(".page")) return; // handled by overlay click
    applyEditor();
  }
});

// ---------------------------------------------------------------- handwriting popover
function positionPop() {
  if (!S.pop || !S.pop.anchor) return;
  const pop = $("#inkPop"), r = S.pop.anchor.getBoundingClientRect();
  const h = pop.offsetHeight, w = pop.offsetWidth;
  let top = r.bottom + 10;
  if (top + h > window.innerHeight - 30) top = Math.max(100, r.top - h - 10);
  pop.style.top = top + "px";
  pop.style.left = Math.min(Math.max(r.left, 12), window.innerWidth - w - 12) + "px";
}
function closePop() {
  if (!S.pop) return;
  if (S.pop.temp) S.pop.temp.remove();
  S.pop = null;
  $("#inkPop").hidden = true;
}
async function openInk(P, line, anchor) {
  if (!line) return;
  closePop();
  S.pop = { P, line, anchor, kind: "ink" };
  $("#inkPopTitle").textContent = line.synthetic ? "Testo scritto da Calamo" : line.kind === "print" ? "Testo stampato (scansione)" : "Testo scritto a mano";
  $("#readSection").hidden = !!line.synthetic;
  setKindSwitch(line);
  $("#inkCrop").hidden = false;
  $("#inkCrop").removeAttribute("src");
  $("#inkRead").value = line.text;
  setSrcBadge($("#inkSrc"), line);
  $("#inkNew").value = line.text;
  $("#inkColor").value = S.handInk;
  $("#inkApply").querySelector("span").textContent = line.kind === "print" ? "Applica come testo stampato" : "Applica con la tua calligrafia";
  $("#inkPop").hidden = false;
  positionPop();
  highlightInkItem(line.id);
  previewHand();
  try { $("#inkCrop").src = await call("ink_crop", P.i, line.id); positionPop(); } catch (e) { /* optional */ }
  $("#inkNew").focus();
}
function openHandNew(P, pt, e) {
  closePop();
  const anchor = document.createElement("div");
  anchor.style.cssText = `position:absolute;left:${pt.x * scale()}px;top:${pt.y * scale()}px;width:2px;height:${S.handHeight * scale()}px;background:var(--gold);box-shadow:0 0 8px var(--gold)`;
  P.overlay.appendChild(anchor);
  S.pop = { P, pt, anchor, kind: "new", temp: anchor };
  $("#inkPopTitle").textContent = "Scrivi a mano";
  $("#readSection").hidden = true;
  $("#inkCrop").hidden = true;
  $("#inkNew").value = "";
  $("#inkColor").value = S.handInk;
  $("#inkApply").querySelector("span").textContent = "Inserisci";
  $("#inkPop").hidden = false;
  positionPop();
  previewHand();
  $("#inkNew").focus();
}
function setKindSwitch(line) {
  $$("#inkKind button").forEach(b => b.classList.toggle("active", b.dataset.kind === (line.kind || "hand")));
  $("#inkLetters").disabled = line.kind === "print";
}
function setSrcBadge(el, l) {
  const map = { ocr: ["OCR", "src-ocr"], learned: ["Appreso", "src-learned"], user: ["Tu", "src-user"] };
  let [t, c] = map[l.source] || map.ocr;
  if (isDoubt(l)) { t = "Da verificare"; c = "src-doubt"; }
  if (l.kind === "print" && !l.synthetic) { t = "Stampato"; c = "src-print"; }
  el.textContent = t;
  el.className = "src-badge " + c;
}
const previewHand = debounce(async () => {
  if (!S.pop) return;
  const text = $("#inkNew").value;
  const img = $("#inkPreview");
  if (!text.trim()) { img.hidden = true; $("#inkPreviewEmpty").hidden = false; $("#inkCoverage").textContent = ""; return; }
  const h = S.pop.line ? S.pop.line.bbox[3] - S.pop.line.bbox[1] : S.handHeight;
  if (S.pop.line && S.pop.line.kind === "print") {
    img.hidden = true; $("#inkPreviewEmpty").hidden = false; $("#inkCoverage").textContent = "";
    $("#inkPreviewEmpty").textContent = "Verrà scritto come testo stampato";
    return;
  }
  $("#inkPreviewEmpty").textContent = "Anteprima";
  try {
    const r = await call("preview_hand", text, h, $("#inkColor").value);
    if (!r) return;
    img.src = r.img; img.hidden = false; $("#inkPreviewEmpty").hidden = true;
    const pct = Math.round(r.coverage * 100);
    $("#inkCoverage").textContent = S.glyphCount ? `${pct}% lettere dalla calligrafia appresa` : "Carattere calligrafico (nessuna lettera appresa)";
    positionPop();
  } catch (e) { /* preview is best effort */ }
}, 220);
$("#inkNew").addEventListener("input", previewHand);
$("#inkColor").addEventListener("input", previewHand);
$("#inkNew").addEventListener("keydown", e => {
  if (e.key === "Enter" && (e.ctrlKey || !e.shiftKey)) { e.preventDefault(); $("#inkApply").click(); }
  if (e.key === "Escape") closePop();
  e.stopPropagation();
});
$("#inkRead").addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); $("#inkTeach").click(); }
  if (e.key === "Escape") closePop();
  e.stopPropagation();
});
$("#inkTeach").onclick = async () => {
  const p = S.pop;
  if (!p || !p.line) return;
  const v = $("#inkRead").value.trim();
  if (!v) return;
  const syncNew = $("#inkNew").value === p.line.text;
  await guarded(async () => {
    await teach(p.P.i, p.line.id, v);
    p.line = (S.inkCache[p.P.i] || []).find(l => l.id === p.line.id) || p.line;
    setSrcBadge($("#inkSrc"), p.line);
    if (syncNew) { $("#inkNew").value = v; previewHand(); }
  });
};
async function teach(page, id, text) {
  const r = await call("correct_ink", page, id, text);
  S.inkCache[page] = r.ink;
  const P = S.pages[page];
  if (P.data) { P.data.ink = r.ink; drawOverlay(P); }
  applyHand(r.hand);
  updateLearning(r.stats);
  renderInkList();
  toast(r.learned ? "Grazie! Ho imparato la correzione e la userò d'ora in poi." : "Lettura aggiornata.", "ok");
}
$("#inkApply").onclick = async () => {
  const p = S.pop;
  if (!p) return;
  const text = $("#inkNew").value.replace(/\n/g, " ").trim();
  const color = $("#inkColor").value;
  if (p.kind === "new" && !text) return closePop();
  closePop();
  await guarded(async () => {
    if (p.kind === "ink") await call("edit_ink", p.P.i, p.line.id, text, color);
    else await call("add_hand", p.P.i, p.pt.x, p.pt.y, text, S.handHeight, color);
    await afterChange();
    toast(p.kind === "ink" ? "Scrittura a mano aggiornata" : "Testo scritto a mano inserito", "ok");
  }, "Composizione della calligrafia…");
};
document.addEventListener("mousedown", e => {
  if (S.pop && !e.target.closest("#inkPop, .ink, .page, .modal")) closePop();
});
$$("#inkKind button").forEach(b => b.onclick = async () => {
  const p = S.pop;
  if (!p || !p.line) return;
  await guarded(async () => {
    await setInkKind(p.P.i, p.line.id, b.dataset.kind);
    p.line = (S.inkCache[p.P.i] || []).find(l => l.id === p.line.id) || p.line;
    openInk(p.P, p.line, p.anchor);
  });
});
$("#inkLetters").onclick = () => {
  const p = S.pop;
  if (!p || !p.line) return;
  closePop();
  openLetters(p.P.i, p.line.id);
};
async function setInkKind(page, id, kind) {
  const r = await call("set_ink_kind", page, id, kind);
  S.inkCache[page] = r.ink;
  const P = S.pages[page];
  if (P.data) { P.data.ink = r.ink; drawOverlay(P); }
  applyHand(r.hand);
  renderInkList();
  toast(kind === "print" ? "Riga segnata come stampata: non verrà usata per la calligrafia." :
    "Riga segnata come scritta a mano: le sue lettere entrano nella calligrafia.", "ok");
}

// ---------------------------------------------------------------- recognition panel
function renderInkList() {
  const list = $("#inkList");
  $("#inkPageLabel").textContent = S.doc ? `· pagina ${S.current + 1}` : "";
  const ink = S.inkCache[S.current] || [];
  if (!S.doc || !ink.length) {
    list.innerHTML = `<div class="empty">${S.doc && (S.doc.analyzed || []).includes(S.current) ? "Nessuna scrittura a mano trovata in questa pagina." :
      "Nessuna analisi per questa pagina.<br>Premi <b>Analizza pagina</b> per leggere la scrittura a mano."}</div>`;
    return;
  }
  list.innerHTML = "";
  ink.forEach(l => {
    const it = document.createElement("div");
    const doubt = isDoubt(l);
    it.className = "ink-item" + (doubt ? " doubt" : "");
    it.dataset.id = l.id;
    const canMark = !l.synthetic && l.kind !== "print";
    const asText = l.synthetic && l.kind === "print";    // now ordinary text on the page
    it.innerHTML = `<div class="ii-top"><span class="src-badge"></span><span class="grow">${asText ? "riscritto · si modifica come testo" : l.synthetic ? "riscritto" : ""}</span>
      <button class="icon-btn" data-a="show" title="Mostra nella pagina"><svg><use href="#i-search"/></svg></button>
      ${canMark ? `<button class="icon-btn" data-a="letters" title="Segna lettere e numeri"><svg><use href="#i-letters"/></svg></button>` : ""}
      ${asText ? "" : `<button class="icon-btn" data-a="rewrite" title="Riscrivi"><svg><use href="#i-pen"/></svg></button>`}</div>
      <input value="${esc(l.text)}" spellcheck="false"${asText ? " readonly" : ""}>
      <div class="ii-actions" hidden><button class="btn ghost sm" data-a="teach"><svg><use href="#i-learn"/></svg>Insegna lettura</button></div>`;
    setSrcBadge($(".src-badge", it), l);
    const input = $("input", it), acts = $(".ii-actions", it);
    input.addEventListener("input", () => acts.hidden = input.value.trim() === l.text);
    input.addEventListener("keydown", e => { if (e.key === "Enter") $("[data-a=teach]", it).click(); e.stopPropagation(); });
    $("[data-a=teach]", it).onclick = () => guarded(() => teach(S.current, l.id, input.value.trim()));
    $("[data-a=show]", it).onclick = () => flashInk(S.current, l.id);
    if (canMark) $("[data-a=letters]", it).onclick = () => openLetters(S.current, l.id);
    if (!asText) $("[data-a=rewrite]", it).onclick = () => {
      const P = S.pages[S.current];
      flashInk(S.current, l.id);
      setTimeout(() => {
        const el = $(`.ink[data-id="${l.id}"]`, P.overlay);
        if (el) openInk(P, l, el);
      }, 450);
    };
    list.appendChild(it);
  });
}
function highlightInkItem(id) {
  $$(".ink-item").forEach(n => n.classList.toggle("focus", +n.dataset.id === id));
}
function flashInk(page, id) {
  const l = (S.inkCache[page] || []).find(x => x.id === id);
  if (!l) return;
  goToPage(page, l.bbox[1]);
  const el = $(`.ink[data-id="${id}"]`, S.pages[page].overlay);
  if (el) { el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash"); }
}
async function analyze(all) {
  if (!S.doc) return;
  const pages = all ? S.pages.map(P => P.i) : [S.current];
  let found = 0;
  try {
    for (let k = 0; k < pages.length; k++) {
      const i = pages[k];
      busy(all ? `Lettura della pagina ${i + 1} di ${S.pages.length}…` : "Lettura della pagina…", all ? k / pages.length : null);
      const r = await call("analyze", i);
      S.inkCache[i] = r.ink;
      found += r.ink.length;
      const P = S.pages[i];
      if (P.data) { P.data.ink = r.ink; drawOverlay(P); }
      applyHand(r.hand);
    }
    S.doc = await call("info");
    renderInkList();
    if (!found) toast("Non ho trovato scrittura a mano o testo scansionato.", "");
    else {
      const all = Object.values(S.inkCache).flat();
      const doubt = all.filter(isDoubt).length, printed = all.filter(l => l.kind === "print").length;
      toast(`Ho letto ${found} righe${printed ? `, ${printed} stampate (in grigio)` : ""}${doubt ? `, ${doubt} da verificare (in rosso)` : ""}. Cliccale per correggere o riscrivere.`, "ok");
      if (S.tool !== "edit") setTool("edit");
      $$("#tabs button")[0].click();
    }
  } catch (e) { toast(e.message, "err"); } finally { busy(false); }
}
function applyHand(h) {
  if (!h) return;
  S.glyphCount = h.glyphs.length;
  S.handInk = h.ink;
  $("#inkSwatch").style.background = h.ink;
  $("#handMeta").textContent = h.glyphs.length
    ? `${h.glyphs.length} caratteri appresi${h.words ? ` da ${h.words} parole` : ""}${h.manual ? ` · ${h.manual} segnati da te` : ""}`
    : "Nessuna lettera appresa";
  $("#lettersStats").textContent = h.manual ? `Hai segnato ${h.manual} lettere e numeri: restano anche negli altri documenti.` : "Nessuna lettera segnata a mano.";
  $("#glyphs").innerHTML = h.glyphs.map(g => `<div class="glyph${g.manual ? " manual" : ""}" data-char="${esc(g.char)}" title="“${esc(g.char)}” · ${g.count} esempi${g.manual ? `, ${g.manual} segnati da te` : ""}"><img src="${g.img}"><span>${esc(g.char)}</span></div>`).join("");
  $$("#glyphs .glyph").forEach(el => el.onclick = () => openGlyph(el.dataset.char));
}
async function refreshHand() {
  try {
    const r = await call("hand_summary");
    applyHand(r.hand);
    updateLearning(r.learning);
    S.handHeight = r.height;
  } catch (e) { /* no document yet */ }
}
function updateLearning(st) {
  if (!st) return;
  const n = (v, one, many) => `${v} ${v === 1 ? one : many}`;
  const txt = st.words || st.lines ? `Calamo ricorda ${n(st.words, "parola", "parole")} e ${n(st.lines, "frase", "frasi")} che gli hai corretto.` : "Nessuna correzione memorizzata finora.";
  $("#learnStats").textContent = txt;
  $("#learnMeta").textContent = txt;
}

// ---------------------------------------------------------------- glyph samples
async function openGlyph(ch) {
  await guarded(async () => {
    S.glyphChar = ch;
    renderGlyphVariants(await call("glyph_variants", ch));
    $("#glyphModal").hidden = false;
  });
}
function renderGlyphVariants(list) {
  const ch = S.glyphChar;
  $("#glyphTitle").textContent = `Esempi di “${ch}”`;
  const box = $("#glyphVariants");
  if (!list.length) { box.innerHTML = `<div class="empty">Nessun esempio rimasto.</div>`; return; }
  box.innerHTML = list.map(v => `<div class="glyph${v.kind === "manual" ? " manual" : ""}"><img src="${v.img}">
    <button data-kind="${v.kind}" data-i="${v.i}" title="Elimina questo esempio"><svg><use href="#i-trash"/></svg></button></div>`).join("");
  $$("button", box).forEach(b => b.onclick = () => guarded(async () => {
    const r = await call("delete_glyph", ch, b.dataset.kind, +b.dataset.i);
    applyHand(r.hand);
    renderGlyphVariants(r.variants);
  }));
}

// ---------------------------------------------------------------- marking letters one by one
const L = { data: null, segs: [], sel: -1, k: 1, page: 0, id: 0 };
async function openLetters(page, id) {
  await guarded(async () => {
    const d = await call("ink_letters", page, id);
    Object.assign(L, { data: d, page, id, sel: -1, segs: d.segments.map(x => ({ ...x })) });
    const img = $("#lettersImg");
    $("#lettersBoxes").innerHTML = "";
    $("#lettersModal").hidden = false;
    img.removeAttribute("src");
    img.onload = () => {
      L.k = img.clientWidth / (d.rect[2] - d.rect[0]);
      L.sel = L.segs.findIndex(x => !x.char);
      drawLetters();
      $("#lettersStage").scrollLeft = 0;
    };
    img.src = d.img;
    $("#lettersUpdateText").checked = true;
  }, "Preparazione delle lettere…");
}
function drawLetters() {
  const wrap = $("#lettersBoxes"), x0 = L.data.rect[0];
  wrap.innerHTML = "";
  L.segs.forEach((sg, i) => {
    const b = document.createElement("div");
    b.className = "lbox" + (sg.char ? "" : " empty") + (i === L.sel ? " sel" : "");
    b.style.left = (sg.x0 - x0) * L.k + "px";
    b.style.width = Math.max((sg.x1 - sg.x0) * L.k, 4) + "px";
    b.dataset.i = i;
    b.innerHTML = `<i class="lh l"></i><i class="lh r"></i><span class="lc">${sg.char ? esc(sg.char) : "?"}</span>`;
    wrap.appendChild(b);
  });
  const done = L.segs.filter(x => x.char).length;
  $("#lettersCount").textContent = `${done} di ${L.segs.length} riquadri con un carattere`;
  if (document.activeElement !== $("#lettersSeq")) $("#lettersSeq").value = L.segs.map(x => x.char || "?").join("");
  const sel = $(".lbox.sel", wrap);
  if (sel) sel.scrollIntoView({ block: "nearest", inline: "nearest" });
}
function lettersPoint(e) {
  const r = $("#lettersCanvas").getBoundingClientRect();
  return L.data.rect[0] + (e.clientX - r.left) / L.k;
}
$("#lettersCanvas").addEventListener("mousedown", e => {
  if (e.button !== 0 || !L.data) return;
  e.preventDefault();
  const boxEl = e.target.closest(".lbox"), handle = e.target.closest(".lh");
  const minW = 1.5 / L.k * 4;
  if (handle) {
    const i = +boxEl.dataset.i, left = handle.classList.contains("l"), sg = L.segs[i];
    L.sel = i;
    const move = ev => {
      const x = lettersPoint(ev);
      if (left) sg.x0 = Math.min(Math.max(x, i > 0 ? L.segs[i - 1].x0 + minW : L.data.rect[0]), sg.x1 - minW);
      else sg.x1 = Math.max(Math.min(x, i < L.segs.length - 1 ? L.segs[i + 1].x1 - minW : L.data.rect[2]), sg.x0 + minW);
      // neighbours give way
      if (left && i > 0 && L.segs[i - 1].x1 > sg.x0) L.segs[i - 1].x1 = sg.x0;
      if (!left && i < L.segs.length - 1 && L.segs[i + 1].x0 < sg.x1) L.segs[i + 1].x0 = sg.x1;
      drawLetters();
    };
    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    drawLetters();
    return;
  }
  if (boxEl) { L.sel = +boxEl.dataset.i; drawLetters(); return; }
  // drag on empty space: a new box
  const a = lettersPoint(e);
  const sg = { x0: a, x1: a + minW, char: "" };
  L.segs.push(sg);
  L.segs.sort((p, q) => p.x0 - q.x0);
  L.sel = L.segs.indexOf(sg);
  const move = ev => {
    const x = lettersPoint(ev);
    sg.x0 = Math.min(a, x); sg.x1 = Math.max(a, x, sg.x0 + minW);
    L.segs.sort((p, q) => p.x0 - q.x0);
    L.sel = L.segs.indexOf(sg);
    drawLetters();
  };
  const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
  window.addEventListener("mousemove", move);
  window.addEventListener("mouseup", up);
  drawLetters();
});
document.addEventListener("keydown", e => {
  if ($("#lettersModal").hidden) return;
  if (e.target === $("#lettersSeq")) return;
  e.stopPropagation();
  if (e.key === "Escape") { e.preventDefault(); return cmd("lettersClose"); }
  if (e.key === "Enter") { e.preventDefault(); return cmd("lettersSave"); }
  if (!L.segs.length) return;
  if (e.key === "ArrowRight" || e.key === "Tab") { e.preventDefault(); L.sel = Math.min(L.sel + 1, L.segs.length - 1); return drawLetters(); }
  if (e.key === "ArrowLeft") { e.preventDefault(); L.sel = Math.max(L.sel - 1, 0); return drawLetters(); }
  if (L.sel < 0) return;
  if (e.key === "Delete") { e.preventDefault(); return cmd("letterDelete"); }
  if (e.key === "Backspace") { e.preventDefault(); L.segs[L.sel].char = ""; L.sel = Math.max(L.sel - 1, 0); return drawLetters(); }
  if (e.key.length === 1 && e.key !== " " && !e.ctrlKey && !e.altKey) {
    e.preventDefault();
    L.segs[L.sel].char = e.key;
    if (L.sel < L.segs.length - 1) L.sel++;
    drawLetters();
  }
}, true);
$("#lettersSeq").addEventListener("input", e => {
  const chars = [...e.target.value.replace(/\s/g, "")];
  L.segs.forEach((sg, i) => { sg.char = chars[i] && chars[i] !== "?" ? chars[i] : ""; });
  drawLetters();
});
$("#lettersSeq").addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); cmd("lettersSave"); }
  if (e.key === "Escape") { e.preventDefault(); cmd("lettersClose"); }
  e.stopPropagation();
});
const LETTER_COMMANDS = {
  letterSplit() {
    const sg = L.segs[L.sel];
    if (!sg) return toast("Seleziona prima un riquadro.", "warn");
    const mid = (sg.x0 + sg.x1) / 2;
    L.segs.splice(L.sel + 1, 0, { x0: mid, x1: sg.x1, char: "" });
    sg.x1 = mid;
    L.sel++;
    drawLetters();
  },
  letterMerge() {
    const sg = L.segs[L.sel], nx = L.segs[L.sel + 1];
    if (!sg || !nx) return toast("Seleziona un riquadro che abbia un riquadro dopo di sé.", "warn");
    sg.x1 = nx.x1;
    sg.char = sg.char || nx.char;
    L.segs.splice(L.sel + 1, 1);
    drawLetters();
  },
  letterDelete() {
    if (L.sel < 0 || !L.segs[L.sel]) return;
    L.segs.splice(L.sel, 1);
    L.sel = Math.min(L.sel, L.segs.length - 1);
    drawLetters();
  },
  letterReset() {
    L.segs = L.data.segments.map(x => ({ ...x }));
    L.sel = L.segs.findIndex(x => !x.char);
    drawLetters();
  },
  lettersClose() { $("#lettersModal").hidden = true; L.data = null; },
  async lettersSave() {
    const marked = L.segs.filter(x => x.char);
    if (!marked.length) return toast("Scrivi almeno un carattere in un riquadro.", "warn");
    const missing = L.segs.length - marked.length;
    if (missing) {
      const ok = await modal("Riquadri senza carattere", `${missing} riquadri non hanno un carattere e verranno ignorati. Continuare?`,
        [["Torna indietro", false, "ghost"], ["Salva comunque", true, "primary"]]);
      if (!ok) return;
    }
    const { page, id } = L;
    await guarded(async () => {
      const r = await call("teach_letters", page, id, L.segs, $("#lettersUpdateText").checked);
      $("#lettersModal").hidden = true;
      L.data = null;
      S.inkCache[page] = r.ink;
      const P = S.pages[page];
      if (P.data) { P.data.ink = r.ink; drawOverlay(P); }
      applyHand(r.hand);
      updateLearning(r.stats);
      renderInkList();
      toast(`Salvate ${r.stored} lettere e numeri nella tua calligrafia.`, "ok");
    });
  },
};

// ---------------------------------------------------------------- updates
async function checkUpdate(manual) {
  const st = $("#updateStatus"), act = $("#updateAction");
  if (manual) st.textContent = "Controllo…";
  try {
    const u = await call("check_update");
    S.update = u;
    if (!u) {
      st.textContent = manual ? `Hai già l'ultima versione (${S.version}).` : "";
      act.hidden = true;
      return;
    }
    if (u.ready) {
      window.app.updateReady(u, true);
    } else if (u.downloading) {
      st.textContent = `Versione ${u.version}: download in corso…`;
      status(`Scaricamento aggiornamento ${u.version}…`);
      act.hidden = true;
    } else if (u.canInstall) {
      st.textContent = `È disponibile la versione ${u.version}.`;
      act.textContent = "Scarica e installa"; act.hidden = false;
      act.onclick = () => guarded(async () => { act.hidden = true; await call("download_update"); st.textContent = "Download in corso…"; });
      if (!manual) toast(`È disponibile Calamo ${u.version}.`, "", { label: "Aggiorna", run: () => act.click() });
    } else {
      st.textContent = `È disponibile la versione ${u.version}.`;
      act.textContent = "Apri la pagina di download"; act.hidden = false;
      act.onclick = () => call("open_release_page");
      if (!manual) toast(`È disponibile Calamo ${u.version}.`, "", { label: "Scarica", run: () => call("open_release_page") });
    }
  } catch (e) {
    if (manual) st.textContent = "Impossibile contattare GitHub: " + e.message.slice(0, 80);
  }
}
async function restartToUpdate() {
  closeEditor(true);
  if (await confirmDiscard()) await guarded(() => call("restart_to_update"));
}

// ---------------------------------------------------------------- search
const runSearch = debounce(async () => {
  const term = $("#findInput").value;
  try {
    S.hits = term ? await call("search", term) : [];
  } catch (e) { S.hits = []; }
  S.hitIdx = S.hits.length ? 0 : -1;
  $("#findCount").textContent = term ? (S.hits.length ? `1 di ${S.hits.length}` : "0") : "";
  S.pages.forEach(drawOverlay);
  if (S.hits.length) goToHit();
}, 250);
function goToHit() {
  const h = S.hits[S.hitIdx];
  if (!h) return;
  $("#findCount").textContent = `${S.hitIdx + 1} di ${S.hits.length}`;
  S.pages.forEach(drawOverlay);
  goToPage(h.page, h.bbox[1]);
}
$("#findInput").addEventListener("input", runSearch);
$("#findInput").addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); cmd(e.shiftKey ? "findPrev" : "findNext"); }
  if (e.key === "Escape") cmd("findClose");
  e.stopPropagation();
});
$("#replaceInput").addEventListener("keydown", e => { if (e.key === "Escape") cmd("findClose"); e.stopPropagation(); });

// ---------------------------------------------------------------- commands
async function save() {
  return guarded(async () => {
    const info = await call("save");
    if (!info) return null;
    updateInfo(info);
    toast("Documento salvato", "ok");
    return info;
  }, "Salvataggio…");
}
async function doExport(kind) {
  $("#exportMenu").hidden = true;
  closeEditor(true);
  try {
    const r = await call(kind === "docx" ? "export_docx" : "export_xlsx");
    busy(false);
    if (!r) return;
    const extra = kind === "xlsx" ? ` (${r.sheets} fogli)` : "";
    toast(`Esportato “${r.name}”${extra}`, "ok", { label: "Apri", run: () => call("reveal", r.path) });
  } catch (e) { busy(false); toast(e.message, "err"); }
}

const COMMANDS = {
  open: openDialog, newDoc,
  save: () => save(),
  saveAs: async () => { $("#exportMenu").hidden = true; await guarded(async () => { const i = await call("save_as"); if (i) { updateInfo(i); toast("Copia salvata", "ok"); } }); },
  undo: () => guarded(async () => { closeEditor(false); afterChange(await call("undo")); }),
  redo: () => guarded(async () => { closeEditor(false); afterChange(await call("redo")); }),
  zoomIn: () => setZoom(S.zoom * 1.2), zoomOut: () => setZoom(S.zoom / 1.2), zoomFit,
  find: () => { $("#findbar").hidden = false; $("#findInput").focus(); $("#findInput").select(); },
  findNext: () => { if (S.hits.length) { S.hitIdx = (S.hitIdx + 1) % S.hits.length; goToHit(); } },
  findPrev: () => { if (S.hits.length) { S.hitIdx = (S.hitIdx - 1 + S.hits.length) % S.hits.length; goToHit(); } },
  findClose: () => { $("#findbar").hidden = true; S.hits = []; S.pages.forEach(drawOverlay); },
  replaceAll: () => guarded(async () => {
    const term = $("#findInput").value, repl = $("#replaceInput").value;
    if (!term) return;
    const r = await call("replace_all", term, repl);
    await afterChange(r.info);
    toast(r.count ? `Sostituite ${r.count} occorrenze` : "Nessuna occorrenza nel testo digitale", r.count ? "ok" : "");
    runSearch();
  }, "Sostituzione in corso…"),
  analyze: () => analyze(false), analyzeAll: () => analyze(true),
  exportMenu: () => { $("#exportMenu").hidden = !$("#exportMenu").hidden; },
  exportDocx: () => doExport("docx"), exportXlsx: () => doExport("xlsx"),
  rotatePage: () => guarded(async () => afterChange(await call("rotate_page", S.current))),
  insertPage: () => guarded(async () => { await afterChange(await call("insert_blank", S.current)); goToPage(S.current + 1); }),
  deletePage: async () => {
    const ok = await modal("Eliminare la pagina?", `La pagina ${S.current + 1} verrà rimossa. Puoi annullare con Ctrl+Z.`, [["Annulla", false, "ghost"], ["Elimina", true, "primary"]]);
    if (ok) guarded(async () => afterChange(await call("delete_page", S.current)));
  },
  inkClose: closePop,
  ...LETTER_COMMANDS,
  glyphClose: () => { $("#glyphModal").hidden = true; },
  checkUpdate: () => checkUpdate(true),
  tutorial: () => startTour(),
  forgetLetters: async () => {
    const ok = await modal("Dimenticare le lettere segnate?", "Calamo cancellerà tutte le lettere e i numeri che hai segnato a mano.", [["Annulla", false, "ghost"], ["Dimentica", true, "primary"]]);
    if (ok) guarded(async () => applyHand(await call("forget_letters")));
  },
  resetLearning: async () => {
    const ok = await modal("Dimenticare le correzioni?", "Calamo cancellerà tutte le letture che gli hai insegnato.", [["Annulla", false, "ghost"], ["Dimentica", true, "primary"]]);
    if (ok) guarded(async () => updateLearning(await call("reset_learning")));
  },
};
function cmd(name) { const f = COMMANDS[name]; if (f) f(); }
document.addEventListener("click", e => {
  const b = e.target.closest("[data-cmd]");
  if (b && !b.disabled) { cmd(b.dataset.cmd); return; }
  if (!e.target.closest(".menu-wrap")) $("#exportMenu").hidden = true;
});
$$("#tools button").forEach(b => b.onclick = () => setTool(b.dataset.tool));
$$("#tabs button").forEach(b => b.onclick = () => {
  $$("#tabs button").forEach(x => x.classList.toggle("active", x === b));
  $$(".tab-body").forEach(p => p.hidden = p.dataset.panel !== b.dataset.tab);
});

// new-text style panel
$("#newFont").onchange = e => S.newStyle.family = e.target.value;
$("#newSize").oninput = e => S.newStyle.size = parseFloat(e.target.value) || 12;
$("#newColor").oninput = e => S.newStyle.color = e.target.value;
$("#newBold").onclick = e => { S.newStyle.bold = e.currentTarget.classList.toggle("on"); };
$("#newItalic").onclick = e => { S.newStyle.italic = e.currentTarget.classList.toggle("on"); };

// settings
function saveSetting(k, v) { S.settings[k] = v; call("set_settings", { [k]: v }).catch(() => {}); }
$$("#themeSwitch button").forEach(b => b.onclick = () => { applyTheme(b.dataset.theme); saveSetting("theme", b.dataset.theme); });
$("#ocrLang").onchange = e => saveSetting("ocrLang", e.target.value);
$("#autoUpdate").onchange = e => saveSetting("autoUpdate", e.target.checked);

// window controls
$$(".win-btn").forEach(b => b.onclick = async () => {
  const a = b.dataset.act;
  if (a === "minimize") call("minimize");
  else if (a === "maximize") call("toggle_maximize");
  else if (a === "close") requestClose();
});
$(".titlebar").addEventListener("dblclick", e => { if (!e.target.closest(".win-btn")) call("toggle_maximize"); });
async function requestClose() {
  closeEditor(true);
  if (await confirmDiscard()) call("close", true);
}

// keyboard
document.addEventListener("keydown", e => {
  const typing = e.target.closest("input, textarea, select, [contenteditable]");
  const k = e.key.toLowerCase();
  if (e.ctrlKey) {
    const map = { o: "open", s: e.shiftKey ? "saveAs" : "save", z: e.shiftKey ? "redo" : "undo", y: "redo", f: "find",
      "+": "zoomIn", "=": "zoomIn", "-": "zoomOut", "0": "zoomFit" };
    if (map[k] && !(typing && (k === "z" || k === "y"))) {
      e.preventDefault();
      if (map[k] !== "open" && !S.doc) return;
      cmd(map[k]);
    }
    return;
  }
  if (!$("#tour").hidden) {
    if (e.key === "ArrowRight" || e.key === "Enter") { e.preventDefault(); $("#tourNext").click(); }
    if (e.key === "ArrowLeft") { e.preventDefault(); $("#tourPrev").click(); }
    if (e.key === "Escape") { e.preventDefault(); endTour(); }
    return;
  }
  if (e.key === "Escape" && !$("#glyphModal").hidden) { $("#glyphModal").hidden = true; return; }
  if (typing || !S.doc) return;
  const tools = { n: "view", m: "edit", t: "text", h: "hand", e: "erase" };
  if (tools[k]) setTool(tools[k]);
  if (e.key === "Escape") { closePop(); $("#exportMenu").hidden = true; }
  if (e.key === "PageDown") goToPage(Math.min(S.current + 1, S.pages.length - 1));
  if (e.key === "PageUp") goToPage(Math.max(S.current - 1, 0));
});

// tooltips
let tipTimer;
document.addEventListener("mouseover", e => {
  const el = e.target.closest("[data-tip]");
  clearTimeout(tipTimer);
  $("#tip").hidden = true;
  if (!el) return;
  tipTimer = setTimeout(() => {
    const r = el.getBoundingClientRect(), tip = $("#tip");
    tip.textContent = el.dataset.tip.replace(/  /g, "   ");
    tip.hidden = false;
    tip.style.top = r.bottom + 8 + "px";
    tip.style.left = Math.min(r.left + r.width / 2 - tip.offsetWidth / 2, window.innerWidth - tip.offsetWidth - 8) + "px";
  }, 450);
});

// drag & drop (the file path is resolved by the Python side)
document.addEventListener("dragover", e => { e.preventDefault(); document.body.classList.add("drag-over"); });
document.addEventListener("dragleave", e => { if (!e.relatedTarget) document.body.classList.remove("drag-over"); });
document.addEventListener("drop", e => { e.preventDefault(); document.body.classList.remove("drag-over"); });
window.addEventListener("resize", debounce(() => { positionEditBar(); positionPop(); }, 50));

// ---------------------------------------------------------------- first-run tutorial
const TOUR = [
  { icon: "logo", title: "Benvenuto in Calamo PDF Studio",
    text: "Modifica i PDF come un documento Word, anche le parti scritte a mano. In pochi passi ti mostro come si usa. Puoi rivedere questa guida quando vuoi da Impostazioni → Guida." },
  { icon: "i-open", title: "Apri un documento", keys: ["Ctrl", "O"],
    text: "Premi “Apri un PDF” oppure trascina un file nella finestra. Le pagine compaiono a sinistra; zoom con Ctrl + rotellina." },
  { icon: "i-edit", title: "Modifica il testo", keys: ["M"],
    text: "Con lo strumento Modifica clicca una frase e riscrivila sul posto: font, dimensione e colore restano quelli originali. Ctrl+Invio applica, Esc annulla." },
  { icon: "i-sparkle", title: "Leggi la scrittura a mano",
    text: "Premi “Leggi scrittura”. Le righe scritte a mano sono evidenziate in blu, quelle stampate in grigio (non vengono usate per la calligrafia), quelle dubbie in rosso con un “?”." },
  { icon: "i-letters", title: "Correggi e segna le lettere",
    text: "Clicca una riga per correggerne la lettura. Con “Segna lettere” indichi dove si trova ogni lettera e numero: Calamo li ricorda anche negli altri documenti e la calligrafia diventa sempre più completa." },
  { icon: "i-pen", title: "Scrivi con la tua calligrafia", keys: ["H"],
    text: "Con lo strumento A mano clicca un punto della pagina e scrivi: il testo viene composto con le lettere apprese, nello stesso inchiostro. Poi salva o esporta in Word ed Excel." },
  { icon: "i-download", title: "Sempre aggiornato",
    text: "Quando su GitHub esce una nuova versione, Calamo la scarica da solo e la installa alla chiusura. Puoi disattivarlo nelle Impostazioni." },
];
let tourIdx = 0;
function startTour() { tourIdx = 0; $("#tour").hidden = false; drawTour(); }
function drawTour() {
  const t = TOUR[tourIdx];
  $("#tourArt").innerHTML = `<svg><use href="#${t.icon}"/></svg>` +
    (t.keys ? `<div class="tour-keys">${t.keys.map(k => `<kbd>${k}</kbd>`).join("<span class='muted'>+</span>")}</div>` : "");
  $("#tourStep").textContent = `Passo ${tourIdx + 1} di ${TOUR.length}`;
  $("#tourTitle").textContent = t.title;
  $("#tourText").textContent = t.text;
  $("#tourDots").innerHTML = TOUR.map((_, i) => `<i class="${i === tourIdx ? "on" : ""}"></i>`).join("");
  $("#tourPrev").hidden = tourIdx === 0;
  $("#tourSkip").hidden = tourIdx === TOUR.length - 1;
  $("#tourNext").textContent = tourIdx === TOUR.length - 1 ? "Inizia" : "Avanti";
}
function endTour() {
  $("#tour").hidden = true;
  if (!S.settings.tutorialDone) saveSetting("tutorialDone", true);
}
$("#tourNext").onclick = () => { if (tourIdx < TOUR.length - 1) { tourIdx++; drawTour(); } else endTour(); };
$("#tourPrev").onclick = () => { if (tourIdx > 0) { tourIdx--; drawTour(); } };
$("#tourSkip").onclick = endTour;

// API used by Python
window.app = {
  progress(label, i, n) { busy(`${label}… ${i + 1}/${n}`, (i + 1) / n); },
  requestClose,
  openPath,
  updateProgress(f) {
    const v = S.update ? S.update.version : "";
    status(`Scaricamento aggiornamento ${v}… ${Math.round(f * 100)}%`);
    $("#updateStatus").textContent = `Versione ${v}: download ${Math.round(f * 100)}%`;
  },
  updateReady(u, quiet) {
    status(`Aggiornamento ${u.version} pronto: verrà installato alla chiusura`);
    $("#updateStatus").textContent = `Versione ${u.version} pronta: verrà installata alla chiusura.`;
    const act = $("#updateAction");
    act.textContent = "Riavvia e aggiorna ora"; act.hidden = false; act.onclick = restartToUpdate;
    if (!quiet) toast(`Calamo ${u.version} è pronto. Verrà installato alla chiusura.`, "ok", { label: "Riavvia ora", run: restartToUpdate });
  },
  updateFailed(msg) {
    status("");
    $("#updateStatus").textContent = "Aggiornamento non riuscito: " + msg;
  },
};
