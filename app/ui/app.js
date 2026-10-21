/* Calamo PDF Studio — UI controller */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const PT = 4 / 3;            // css px per PDF point at 100%
const TOOL_NAMES = { view: "Naviga", edit: "Modifica", text: "Nuovo testo", hand: "Scrittura a mano", erase: "Gomma",
                     mark: "Segna una scritta: trascina un riquadro intorno", annotate: "Annota", insert: "Firma e immagini",
                     redact: "Oscura: trascina sull'area", select: "Oggetti" };

const isDoubt = l => l.source === "ocr" && l.doubt > 0.4 && l.kind !== "print";
// writings (people / typefaces) of each page, as the engine groups them
const pageStyles = i => (S.pages[i] && S.pages[i].data && S.pages[i].data.styles) || [];
const styleOf = (i, l) => pageStyles(i).find(st => st.id === l.style);
function setStyles(page, styles) {
  const P = S.pages[page];
  if (P && P.data) P.data.styles = styles;
}
function fillStyleSelect(sel, page, kindFilter, selected, withNew) {
  const sts = pageStyles(page).filter(st => !kindFilter || st.kind === kindFilter);
  sel.innerHTML = sts.map(st => `<option value="${st.id}">${esc(st.name)}${st.kind === "print" ? " (stampato)" : ""} · ${st.lines} righe</option>`).join("")
    + (withNew ? `<option value="new">+ Nuova scrittura (solo questa riga)</option>` : "");
  if (selected != null) sel.value = String(selected);
}

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
  if ([...$$("#toasts .toast:not(.out) span")].some(s => s.textContent === msg)) return;
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
// errors of the interface: kept in the log, and said once, never a silent stop
let lastUiError = "";
function uiError(msg) {
  msg = String(msg || "errore sconosciuto");
  try { window.pywebview && window.pywebview.api.log_error(msg); } catch (e) { /* no bridge yet */ }
  if (msg === lastUiError) return;
  lastUiError = msg;
  setTimeout(() => { if (lastUiError === msg) lastUiError = ""; }, 4000);
  if (typeof toast === "function") toast("Si è verificato un errore: " + msg.split("\n")[0], "err");
}
window.addEventListener("error", e => uiError((e.error && e.error.stack) || e.message));
window.addEventListener("unhandledrejection", e => uiError((e.reason && (e.reason.stack || e.reason.message)) || e.reason));
// the busy layer stays while any of the operations that asked for it is still running
let busyCount = 0;
function busy(text, frac) {
  const b = $("#busy");
  if (text === false) { busyCount = Math.max(0, busyCount - 1); if (!busyCount) b.hidden = true; return; }
  if (frac == null) busyCount++;            // a progress update is not a new operation
  b.hidden = false;
  $("#busyText").textContent = text;
  $("#busyBar").style.width = frac == null ? "0" : Math.round(frac * 100) + "%";
  $("#busyBar").parentElement.style.display = frac == null ? "none" : "";
}
function modal(title, text, buttons, input) {
  return new Promise(resolve => {
    $("#modalTitle").textContent = title;
    $("#modalText").textContent = text;
    const inp = $("#modalInput");
    inp.hidden = input == null;
    inp.value = input || "";
    const box = $("#modalActions");
    box.innerHTML = "";
    const done = value => { $("#modal").hidden = true; inp.onkeydown = null; resolve(input == null ? value : (value ? inp.value.trim() : null)); };
    buttons.forEach(([label, value, cls]) => {
      const b = document.createElement("button");
      b.className = "btn sm " + (cls || "");
      b.textContent = label;
      b.onclick = () => done(value);
      box.appendChild(b);
    });
    $("#modal").hidden = false;
    if (input != null) {
      inp.onkeydown = e => { e.stopPropagation(); if (e.key === "Enter") done(true); if (e.key === "Escape") done(false); };
      setTimeout(() => { inp.focus(); inp.select(); }, 30);
    }
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
  $("#handModel").checked = S.settings.handModel !== false;
  refreshModel();
  $("#about").textContent = `Calamo PDF Studio ${b.version}`;
  S.version = b.version;
  renderRecent(b.recent);
  updateLearning(b.learning);
  setDocUI(false);
  call("styles").then(renderStyles).catch(() => {});
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
  await guarded(async () => openResult(await call("open_path", path)), "Apertura del documento…");
}
// a protected PDF asks for its password, until it is right or the user gives up
async function openResult(info) {
  while (info && info.needPassword) {
    const pw = await modal("Documento protetto", info.wrong ? "Password errata, riprova." : `“${info.path.split(/[\\/]/).pop()}” è protetto: scrivi la password per aprirlo.`,
      [["Annulla", false, "ghost"], ["Apri", true, "primary"]], "");
    if (!pw) return;
    busy("Apertura del documento…");
    try { info = await call("open_with_password", info.path, pw); } finally { busy(false); }
  }
  if (info) loadDoc(info);
}
async function openDialog() {
  if (!(await confirmDiscard())) return;
  await guarded(async () => { await openResult(await call("open_dialog")); });
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
  S.docGen = (S.docGen || 0) + 1;
  if (reading) { reading.cancelled = true; call("cancel_analysis").catch(() => {}); }
  S.doc = info;
  S.inkCache = {};
  S.hits = []; S.hitIdx = -1;
  resetExtras();
  setDocUI(true);
  buildPages();
  updateInfo(info);
  zoomFit();
  S.current = -1;
  setCurrent(0);
  refreshHand();
  renderInkList();
  if (!S.quietLoad) toast(`Aperto “${info.name}”`, "ok");
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
let pageObserver, thumbObserver, farObserver;
// a page far from the screen gives back the memory of its picture (it is drawn again when it
// comes back): scrolling a long document would otherwise keep hundreds of big images
function unloadPage(P) {
  if (!P || P.visible || !P.img.getAttribute("src")) return;
  P.img.removeAttribute("src");
  P.renderKey = null;
  if (!$(".shimmer", P.el)) P.el.insertAdjacentHTML("afterbegin", '<div class="shimmer"></div>');
}
function buildPages() {
  const wrap = $("#pages"), thumbs = $("#thumbs");
  wrap.innerHTML = ""; thumbs.innerHTML = "";
  pageObserver && pageObserver.disconnect();
  thumbObserver && thumbObserver.disconnect();
  farObserver && farObserver.disconnect();
  farObserver = new IntersectionObserver(es => es.forEach(e => {
    if (!e.isIntersecting) unloadPage(S.pages[+e.target.dataset.i]);
  }), { root: wrap, rootMargin: "3000px 0px" });
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
    bindThumbDrag(th, i);
    thumbs.appendChild(th);
    const P = { i, w: p.w, h: p.h, v: p.v, el, th, img: $("img.render", el), overlay: $(".overlay", el),
      renderKey: null, dataV: -1, data: null, visible: false, thumbVisible: false, thumbKey: null, canvas: null };
    bindOverlay(P);
    pageObserver.observe(el);
    farObserver.observe(el);
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
// a page of a document no longer shown (the tab changed while it was loading) is left alone
const stale = P => S.pages[P.i] !== P;
async function loadPage(P, force) {
  if (stale(P)) return;
  const k = `${P.v}@${S.zoom}`;
  if (P.renderKey !== k || force) {
    P.renderKey = k;
    const ratio = Math.min(Math.max(window.devicePixelRatio || 1, 1) * scale(), 6);
    try {
      const url = await call("render", P.i, ratio, "auto");
      if (P.renderKey === k) {
        P.img.src = url;
        P.canvas = null;
        P.img.onload = () => { const sh = $(".shimmer", P.el); sh && sh.remove(); };
      }
    } catch (e) { if (!stale(P)) toast(e.message, "err"); }
  }
  if (stale(P)) return;
  if (P.dataV !== P.v) {
    P.dataV = P.v;
    try {
      const d = await call("blocks", P.i);
      P.data = d;
      S.inkCache[P.i] = d.ink;
      drawOverlay(P);
      if (P.i === S.current) renderInkList();
    } catch (e) { if (!stale(P)) toast(e.message, "err"); }
  } else drawOverlay(P);
}
async function loadThumb(P) {
  if (stale(P)) return;
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
  $$(".gfx, .blk, .ink, .hit, .obj, .diff, .multi-box, .ruler, .guide", ov).forEach(n => n.remove());
  const frag = document.createDocumentFragment();
  drawGraphics(P, frag, s);
  P.data.blocks.forEach(b => {
    const d = document.createElement("div");
    d.className = "blk";
    d.dataset.id = b.id;
    Object.assign(d.style, box(b.bbox, s, 2));
    d.innerHTML = RZ;
    frag.appendChild(d);
  });
  (P.data.ink || []).forEach(l => {
    const d = document.createElement("div");
    const st = styleOf(P.i, l);
    const unread = l.source === "unread";
    d.className = "ink" + (unread ? " unread" : isDoubt(l) ? " doubt" : l.kind === "print" ? " print" : st ? " styled" : "");
    if (st) d.style.setProperty("--c", st.color);
    d.dataset.id = l.id;
    d.title = unread ? "Non letto: clicca e scrivi cosa c'è" : `${st ? st.name + ": " : ""}${l.text}`;
    Object.assign(d.style, box(l.bbox, s, 3));
    if (unread) d.innerHTML = `<span class="ink-tag unread-tag">?</span>`;
    else if (isDoubt(l)) d.innerHTML = `<span class="ink-tag">?</span>`;
    if (!unread) d.insertAdjacentHTML("beforeend", RZ);
    frag.appendChild(d);
  });
  S.hits.forEach((h, k) => {
    if (h.page !== P.i) return;
    const d = document.createElement("div");
    d.className = "hit" + (k === S.hitIdx ? " current" : "") + (h.fuzzy ? " fuzzy" : "");
    Object.assign(d.style, box(h.bbox, s, 1));
    frag.appendChild(d);
  });
  drawExtras(P, frag, s);
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
    refreshHand();          // every page has its own handwriting
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
  // only the tool class changes: the stage keeps its other states (tabs, grid)
  [...st.classList].filter(c => c.startsWith("tool-")).forEach(c => st.classList.remove(c));
  st.classList.add("tool-" + t);
  $("#stTool").textContent = TOOL_NAMES[t];
  showToolOpts(t);
  if (t === "hand" && !S.glyphCount)
    toast("Questa pagina non ha ancora una calligrafia: analizzala, oppure usa una calligrafia ricordata. Nel frattempo uso un carattere calligrafico.", "warn");
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
    if (S.tool === "mark") return startRect(P, e, rect => markInk(P, rect));
    if (multiSelectDown(P, e)) return;
    if (startElementDrag(P, e)) return;
    if (toolMouseDown(P, e)) return;
  });
  ov.addEventListener("click", e => {
    if (e.target.closest(".editor")) return;
    if (S.suppressClick && Date.now() - S.suppressClick < 500) return;     // the end of a drag
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
    } else if (toolClick(P, e, pt)) {
      return;
    } else if (S.tool === "hand") {
      // clicking existing handwriting edits it in place instead of writing a new line
      const ink = e.target.closest(".ink");
      if (ink) return openInk(P, (P.data.ink || []).find(l => l.id === +ink.dataset.id), ink);
      openHandNew(P, pt, e);
    }
  });
}

// ---------------------------------------------------------------- erase tool
function startErase(P, e) {
  startRect(P, e, async rect => {
    await guarded(async () => { await call("erase_area", P.i, rect); await afterChange(); toast("Area cancellata", "ok"); });
  });
}
// writing the reading missed: the user outlines it, Calamo reads it
async function markInk(P, rect) {
  await guarded(async () => {
    const r = await call("mark_ink", P.i, rect);
    applyInkResult(P.i, r);
    S.doc = await call("info");
    const id = (r.added || [])[0];
    const line = r.ink.find(l => l.id === id);
    toast(line && line.text ? `Letto: “${line.text}”. Se non è giusto, correggilo qui.` : "Scritta aggiunta: scrivi cosa c'è.", "ok");
    setTool("edit");
    const el = line && $(`.ink[data-id="${line.id}"]`, P.overlay);
    if (line) openInk(P, line, el || P.overlay);
  }, "Lettura della scritta…");
}
function startRect(P, e, done) {
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
    await done(rect);
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
  // give the keyboard back to the page, so the shortcuts work again
  if (document.activeElement && document.activeElement.closest("#inkPop")) document.activeElement.blur();
  if (S.pop.temp) S.pop.temp.remove();
  S.pop = null;
  $("#inkPop").hidden = true;
}
async function openInk(P, line, anchor) {
  if (!line) return;
  closePop();
  S.pop = { P, line, anchor, kind: "ink" };
  const st = styleOf(P.i, line);
  $("#inkPopTitle").textContent = line.source === "unread" ? "Scritta non letta: scrivi cosa c'è" :
    `${line.synthetic ? "Riscritto" : line.kind === "print" ? "Stampato" : "A mano"}${st ? " · " + st.name : ""}`;
  $("#readSection").hidden = !!line.synthetic;
  $("#inkRemove").hidden = !!line.synthetic;
  $("#newStyleRow").hidden = true;
  fillStyleSelect($("#inkStyle"), P.i, null, line.style, true);
  setKindSwitch(line);
  $("#inkCrop").hidden = false;
  $("#inkCrop").removeAttribute("src");
  $("#inkRead").value = line.text;
  setSrcBadge($("#inkSrc"), line);
  $("#inkNew").value = line.text;
  $("#inkColor").value = S.handInk;
  $("#inkApply").querySelector("span").textContent = line.kind === "print" ? "Applica con le lettere della scansione" : "Applica con questa calligrafia";
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
  anchor.style.cssText = `position:absolute;left:${pt.x * scale()}px;top:${pt.y * scale()}px;width:2px;height:${S.handHeight * scale()}px;background:#2F4C95`;
  P.overlay.appendChild(anchor);
  S.pop = { P, pt, anchor, kind: "new", temp: anchor };
  $("#inkRemove").hidden = true;
  $("#inkPopTitle").textContent = "Scrivi a mano";
  $("#readSection").hidden = true;
  $("#newStyleRow").hidden = !pageStyles(P.i).length;
  // write as the writing selected in the panel, or the main handwriting of the page
  const sts = pageStyles(P.i), cur = sts.find(st => st.id === S.style);
  const pick = cur && cur.kind === "hand" ? cur : sts.find(st => st.kind === "hand") || cur;
  fillStyleSelect($("#newStyle"), P.i, null, pick ? pick.id : null, false);
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
  $("#inkLetters").disabled = !!line.synthetic;
}
function setSrcBadge(el, l) {
  const map = { ocr: ["OCR", "src-ocr"], learned: ["Appreso", "src-learned"], user: ["Tu", "src-user"],
               model: ["Lettore", "src-model"] };
  let [t, c] = map[l.source] || map.ocr;
  if (isDoubt(l)) { t = "Da verificare"; c = "src-doubt"; }
  if (l.kind === "print" && !l.synthetic) { t = "Stampato"; c = "src-print"; }
  if (l.source === "unread") { t = "Non letto"; c = "src-doubt"; }
  el.textContent = t;
  el.className = "src-badge " + c;
}
const previewHand = debounce(async () => {
  if (!S.pop) return;
  const text = $("#inkNew").value;
  const img = $("#inkPreview");
  if (!text.trim()) { img.hidden = true; $("#inkPreviewEmpty").hidden = false; $("#inkCoverage").textContent = ""; return; }
  $("#inkPreviewEmpty").textContent = "Anteprima";
  try {
    const p = S.pop;
    const r = p.line
      ? await call("preview_hand", p.P.i, text, null, $("#inkColor").value, null, p.line.id)
      : await call("preview_hand", p.P.i, text, S.handHeight, $("#inkColor").value, +$("#newStyle").value || null);
    if (!r || S.pop !== p) return;
    img.src = r.img; img.hidden = false; $("#inkPreviewEmpty").hidden = true;
    const pct = Math.round(r.coverage * 100);
    const who = r.kind === "print" ? "dalla scansione" : "da questa calligrafia";
    $("#inkCoverage").textContent = r.coverage > 0 ? `${pct}% lettere ${who}` : "Nessuna lettera appresa: uso il carattere più simile";
    positionPop();
  } catch (e) { /* preview is best effort */ }
}, 220);
$("#inkNew").addEventListener("input", previewHand);
$("#inkColor").addEventListener("input", previewHand);
$("#newStyle").addEventListener("change", () => { S.style = +$("#newStyle").value; previewHand(); });
$("#inkStyle").addEventListener("change", async () => {
  const p = S.pop;
  if (!p || !p.line) return;
  await guarded(async () => {
    const r = await call("set_line_style", p.P.i, p.line.id, $("#inkStyle").value);
    applyInkResult(p.P.i, r);
    p.line = (S.inkCache[p.P.i] || []).find(l => l.id === p.line.id) || p.line;
    toast(`Riga spostata in “${(styleOf(p.P.i, p.line) || {}).name}”: verrà riscritta con le sue lettere.`, "ok");
    openInk(p.P, p.line, p.anchor);
  });
});
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
// every engine result about lines carries the lines and the writings of the page
function applyInkResult(page, r) {
  S.inkCache[page] = r.ink;
  const P = S.pages[page];
  if (P.data) { P.data.ink = r.ink; P.data.styles = r.styles; drawOverlay(P); }
  if (page === S.current && r.selected != null) S.style = r.selected;
  applyHand(r, page);
  renderInkList();
}
async function teach(page, id, text) {
  const r = await call("correct_ink", page, id, text);
  applyInkResult(page, r);
  updateLearning(r.stats);
  renderInkList();
  const n = (r.propagated || []).length;
  toast((r.learned ? "Grazie! Ho imparato la correzione e la userò d'ora in poi." : "Lettura aggiornata.") +
        (n ? ` L'ho applicata anche ${n === 1 ? "a un'altra riga" : `ad altre ${n} righe`} della stessa scrittura.` : ""), "ok");
}
$("#inkRemove").onclick = async () => {
  const p = S.pop;
  if (!p || !p.line) return;
  closePop();
  await guarded(async () => {
    applyInkResult(p.P.i, await call("remove_ink", p.P.i, p.line.id));
    S.doc = await call("info");
    updateInfo(S.doc);
    toast("Riga tolta: non verrà più proposta. Ctrl+Z per ripristinarla.", "ok");
  });
};
$("#inkApply").onclick = async () => {
  const p = S.pop;
  if (!p) return;
  const text = $("#inkNew").value.replace(/[ \t]+\n/g, "\n").trim();   // Maiusc+Invio = a capo
  const color = $("#inkColor").value;
  if (p.kind === "new" && !text) return closePop();
  closePop();
  await guarded(async () => {
    if (p.kind === "ink") await call("edit_ink", p.P.i, p.line.id, text, color);
    else await call("add_hand", p.P.i, p.pt.x, p.pt.y, text, S.handHeight, color, +$("#newStyle").value || null);
    await afterChange();
    toast(p.kind === "ink" ? "Testo riscritto con le lettere della sua scrittura" : "Testo inserito", "ok");
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
  applyInkResult(page, r);
  const n = r.changed || 1;
  toast(`${n > 1 ? `Le ${n} righe di questa scrittura sono` : "Questa scrittura è"} ora ${kind === "print" ? "stampata: verrà riscritta con le lettere della scansione" : "a mano: verrà riscritta con la sua calligrafia"}.`, "ok");
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
    const canMark = !l.synthetic;
    const st = styleOf(S.current, l);
    const chip = st ? `<span class="style-chip" style="--c:${st.color}"><i></i>${esc(st.name)}</span>` : "";
    it.innerHTML = `<div class="ii-top"><span class="src-badge"></span><span class="grow">${chip}${l.synthetic ? " · riscritto" : ""}</span>
      <button class="icon-btn" data-a="show" title="Mostra nella pagina"><svg><use href="#i-search"/></svg></button>
      ${canMark ? `<button class="icon-btn" data-a="letters" title="Segna lettere e numeri"><svg><use href="#i-letters"/></svg></button>` : ""}
      <button class="icon-btn" data-a="rewrite" title="Riscrivi"><svg><use href="#i-pen"/></svg></button></div>
      <input value="${esc(l.text)}" spellcheck="false"${l.source === "unread" ? ' placeholder="Non letto: scrivi cosa c\'è scritto"' : ""}>
      <div class="ii-actions" hidden><button class="btn ghost sm" data-a="teach"><svg><use href="#i-learn"/></svg>Insegna lettura</button></div>`;
    setSrcBadge($(".src-badge", it), l);
    const input = $("input", it), acts = $(".ii-actions", it);
    input.addEventListener("input", () => acts.hidden = input.value.trim() === l.text);
    input.addEventListener("keydown", e => { if (e.key === "Enter") $("[data-a=teach]", it).click(); e.stopPropagation(); });
    $("[data-a=teach]", it).onclick = () => guarded(() => teach(S.current, l.id, input.value.trim()));
    $("[data-a=show]", it).onclick = () => flashInk(S.current, l.id);
    if (canMark) $("[data-a=letters]", it).onclick = () => openLetters(S.current, l.id);
    $("[data-a=rewrite]", it).onclick = () => {
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
// reading runs in the background, page by page: the user keeps working meanwhile
let reading = null;
function taskBar(text, frac) {
  $("#stTask").hidden = text === false;
  if (text === false) return;
  $("#stTaskText").textContent = text;
  $("#stTaskBar").style.width = Math.round((frac || 0) * 100) + "%";
}
$("#stTaskCancel").onclick = async () => {
  if (!reading) return;
  reading.cancelled = true;
  taskBar("Interruzione…", reading.frac);
  try { await call("cancel_analysis"); } catch (e) { /* already finished */ }
};
async function analyze(all) {
  if (!S.doc) return;
  if (reading) { toast("Sto già leggendo: aspetta che finisca o interrompi la lettura.", ""); return; }
  const doc = S.docGen;
  const pages = all ? S.pages.map(P => P.i) : [S.current];
  const job = reading = { cancelled: false, frac: 0 };
  let found = 0, done = 0;
  const skipped = [];
  try {
    for (let k = 0; k < pages.length && !job.cancelled; k++) {
      const i = pages[k];
      job.frac = k / pages.length;
      taskBar(all ? `Lettura pagina ${i + 1} (${k + 1} di ${pages.length})…` : `Lettura della pagina ${i + 1}…`, job.frac);
      let r;
      try { r = await call("analyze", i); }
      catch (e) {
        if (job.cancelled || !S.doc || S.docGen !== doc) break;
        if (/modificata durante la lettura/.test(e.message)) { skipped.push(i + 1); continue; }
        throw e;
      }
      if (!S.doc || S.docGen !== doc) break;
      done++;
      applyInkResult(i, r);
      found += r.ink.length;
    }
    if (!S.doc || S.docGen !== doc) return;
    updateInfo(await call("info"));
    renderInkList();
    if (job.cancelled) { toast(`Lettura interrotta: ${done} ${done === 1 ? "pagina letta" : "pagine lette"}.`, ""); return; }
    if (skipped.length) toast(`Hai modificato ${skipped.length === 1 ? "la pagina " : "le pagine "}${skipped.join(", ")} durante la lettura: rileggi${skipped.length === 1 ? "la" : "le"} quando vuoi.`, "");
    if (!found) toast("Non ho trovato scrittura a mano o testo scansionato.", "");
    else {
      const all = Object.values(S.inkCache).flat();
      const doubt = all.filter(isDoubt).length, printed = all.filter(l => l.kind === "print").length;
      const unread = all.filter(l => l.source === "unread").length;
      const writers = pageStyles(S.current).filter(st => st.kind === "hand").length;
      toast(`Ho letto ${found - unread} righe${writers > 1 ? ` di ${writers} scritture a mano diverse` : ""}${printed ? `, ${printed} stampate` : ""}${doubt ? `, ${doubt} da verificare` : ""}${unread ? `; ${unread} non riesco a leggerle (in arancione): scrivi tu cosa c'è` : ""}.`, "ok");
      if (S.tool !== "edit") setTool("edit");
      $$("#tabs button")[0].click();
    }
  } catch (e) { if (!job.cancelled) toast(e.message, "err"); }
  finally { if (reading === job) { reading = null; taskBar(false); } }
}
function applyHand(info, page) {
  // the panel shows the writings of the current page, and the letters of the selected one
  if (!info || (page != null && page !== S.current)) return;
  if (info.styles) {
    setStyles(S.current, info.styles);
    renderWritings(info.styles, info.selected);
    renderSuspects(info.suspects);
  }
  if (info.height) S.handHeight = info.height;
  const h = info.hand || info;
  if (!h.glyphs) return;
  S.glyphCount = h.glyphs.length;
  $("#styleName").textContent = h.name && info.styles && info.styles.length ? h.name : "—";
  $("#styleKind").textContent = h.kind === "print" ? "stampato" : info.styles && info.styles.length ? "a mano" : "";
  $("#handPageLabel").textContent = S.doc ? `· pagina ${S.current + 1}` : "";
  S.handInk = h.ink;
  $("#inkSwatch").style.background = h.ink;
  $("#handMeta").textContent = h.glyphs.length
    ? `${h.glyphs.length} caratteri appresi${h.words ? ` da ${h.words} parole` : ""}${h.manual ? ` · ${h.manual} segnati da te` : ""}`
    : "Nessuna lettera appresa";
  const lo = $("#styleLoaded");
  lo.hidden = !h.loadedName;
  if (h.loadedName) {
    lo.innerHTML = `<svg><use href="#i-pen"/></svg><span>Usa anche “<b>${esc(h.loadedName)}</b>” per le lettere mancanti</span><button title="Smetti di usarla su questa pagina">Togli</button>`;
    $("button", lo).onclick = () => applyStyle(null);
  }
  $("#glyphs").innerHTML = h.glyphs.map(g => `<div class="glyph${g.manual ? " manual" : ""}${g.loaded ? " loaded" : ""}" data-char="${esc(g.char)}" title="“${esc(g.char)}” · ${g.count} esempi${g.manual ? `, ${g.manual} segnati da te` : ""}${g.loaded ? " (dalla calligrafia ricordata)" : ""}"><img src="${g.img}"><span>${esc(g.char)}</span></div>`).join("");
  $$("#glyphs .glyph").forEach(el => el.onclick = () => openGlyph(el.dataset.char));
}
async function refreshHand(sid) {
  try {
    const page = S.doc ? Math.max(S.current, 0) : 0;
    const r = await call("hand_summary", page, sid == null ? null : sid);
    if (S.doc && page !== Math.max(S.current, 0)) return;   // the user moved on meanwhile
    S.style = r.selected;
    applyHand(r);
    updateLearning(r.learning);
    if (S.pages[page]) drawOverlay(S.pages[page]);
  } catch (e) { /* no document yet */ }
}
// lines that look of the other kind (print in a handwriting, or the opposite): the user decides
function renderSuspects(sus) {
  const box = $("#kindHint");
  const pr = (sus && sus.print) || [], hd = (sus && sus.hand) || [];
  const ids = pr.length ? pr : hd, kind = pr.length ? "print" : "hand";
  if (!S.doc || !ids.length) { box.hidden = true; return; }
  const n = ids.length === 1 ? "1 riga sembra" : `${ids.length} righe sembrano`;
  box.innerHTML = `<p>${n} ${kind === "print" ? "stampat" : "scritt"}${ids.length === 1 ? "a" : "e"}${kind === "print" ? "" : " a mano"}, ma ${ids.length === 1 ? "è" : "sono"} in una scrittura ${kind === "print" ? "a mano" : "stampata"}.</p>
    <div class="kind-hint-actions"><button class="btn sm" data-a="show">Mostra</button><button class="btn sm primary" data-a="sep">Separa</button><button class="btn sm ghost" data-a="keep">Vanno bene così</button></div>`;
  box.hidden = false;
  const page = S.current;
  $('[data-a="show"]', box).onclick = () => { const P = S.pages[page]; ids.forEach(id => { const el = $(`.ink[data-id="${id}"]`, P.overlay); if (el) { el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash"); } }); const l = (P.data.ink || []).find(x => x.id === ids[0]); if (l) goToPage(page, l.bbox[1]); };
  $('[data-a="sep"]', box).onclick = () => guarded(async () => { const r = await call("separate_lines", page, ids, kind); applyInkResult(page, r); toast(kind === "print" ? "Righe spostate nello stampato: non insegnano più lettere alla scrittura a mano" : "Righe spostate in una scrittura a mano", "ok"); });
  $('[data-a="keep"]', box).onclick = () => guarded(async () => { const r = await call("keep_lines", page, ids); applyHand(r, page); });
}
function renderWritings(styles, selected) {
  const box = $("#writingList");
  $("#handPageLabel").textContent = S.doc ? `· pagina ${S.current + 1}` : "";
  if (!styles.length) {
    box.innerHTML = `<div class="empty small">Analizza la pagina: Calamo separa le scritture di persone diverse e lo stampato.</div>`;
    return;
  }
  box.innerHTML = styles.map(st => `<button class="wchip${st.id === selected ? " active" : ""}" data-id="${st.id}" style="--c:${st.color}"
    title="${st.kind === "print" ? "Testo stampato" : "Scritto a mano"} · ${st.lines} righe · ${st.glyphs} caratteri"><i></i>${esc(st.name)}<small>${st.lines}</small></button>`).join("");
  $$(".wchip", box).forEach(b => {
    b.onclick = () => { S.style = +b.dataset.id; refreshHand(S.style); };
    // drag a writing onto another: they are the same person
    b.draggable = true;
    b.ondragstart = e => { e.dataTransfer.setData("text/plain", b.dataset.id); b.classList.add("dragging"); };
    b.ondragend = () => b.classList.remove("dragging");
    b.ondragover = e => { e.preventDefault(); b.classList.add("drop"); };
    b.ondragleave = () => b.classList.remove("drop");
    b.ondrop = async e => {
      e.preventDefault(); b.classList.remove("drop");
      const src = +e.dataTransfer.getData("text/plain"), dst = +b.dataset.id;
      if (!src || src === dst) return;
      const a = styles.find(x => x.id === src), d = styles.find(x => x.id === dst);
      const ok = await modal("Unire le scritture?", `Le righe di “${a.name}” passano a “${d.name}” e verranno riscritte con le sue lettere.`,
        [["Annulla", false, "ghost"], ["Unisci", true, "primary"]]);
      if (!ok) return;
      await guarded(async () => {
        const page = S.current;
        applyInkResult(page, await call("merge_styles", page, src, dst));
        toast(`“${a.name}” unita a “${d.name}”.`, "ok");
      });
    };
  });
}
async function applyStyle(name) {
  await guarded(async () => {
    const r = await call("load_style", S.current, S.style, name);
    applyHand(r);
    toast(name ? `“${name}” completa le lettere mancanti di questa scrittura.` : "Calligrafia ricordata tolta.", "ok");
  });
}
function renderStyles(list) {
  const box = $("#styleList");
  box.innerHTML = list.length ? list.map(st => `<div class="style-item"><span>${esc(st.name)}</span><small>${st.chars} caratteri</small>
    <button class="icon-btn danger" data-name="${esc(st.name)}" title="Dimentica questa calligrafia"><svg><use href="#i-trash"/></svg></button></div>`).join("")
    : `<p class="muted small">Nessuna calligrafia ricordata.</p>`;
  $$("button", box).forEach(b => b.onclick = async () => {
    const ok = await modal("Dimenticare la calligrafia?", `“${b.dataset.name}” non sarà più disponibile. Le pagine già scritte non cambiano.`, [["Annulla", false, "ghost"], ["Dimentica", true, "primary"]]);
    if (ok) guarded(async () => renderStyles(await call("delete_style", b.dataset.name)));
  });
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
    S.glyphPage = S.current;
    S.glyphStyle = S.style;
    renderGlyphVariants(await call("glyph_variants", S.glyphPage, S.glyphStyle, ch));
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
    const r = await call("delete_glyph", S.glyphPage, S.glyphStyle, ch, b.dataset.kind, +b.dataset.i);
    applyHand(r, S.glyphPage);
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
      applyInkResult(page, r);
      updateLearning(r.stats);
      renderInkList();
      toast(`Salvate ${r.stored} lettere e numeri nella calligrafia della pagina ${page + 1}.`, "ok");
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
  $("#findCount").textContent = `${S.hitIdx + 1} di ${S.hits.length}${h.fuzzy ? " · simile" : ""}`;
  $("#findCount").title = h.fuzzy ? "Parola scritta a mano quasi uguale a quella cercata (la lettura può contenere errori)" : "";
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
// scanned pages read on the way (an export reads them first): show what was read
function pagesRead() {
  S.pages.forEach(P => { P.dataV = -1; if (P.visible) loadPage(P); });
  refreshHand();
}
async function doExport(kind) {
  $("#exportMenu").hidden = true;
  closeEditor(true);
  try {
    const r = await call(kind === "docx" ? "export_docx" : "export_xlsx");
    busy(false);
    if (!r) return;
    pagesRead();
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
  findClose: () => { $("#findbar").hidden = true; document.activeElement && document.activeElement.blur(); S.hits = []; S.pages.forEach(drawOverlay); },
  replaceAll: () => guarded(async () => {
    const term = $("#findInput").value, repl = $("#replaceInput").value;
    if (!term) return;
    const r = await call("replace_all", term, repl);
    await afterChange(r.info);
    toast(r.count ? (r.count === 1 ? "Sostituita 1 occorrenza" : `Sostituite ${r.count} occorrenze`) : "Nessuna occorrenza nel testo digitale", r.count ? "ok" : "");
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
  saveStyle: async () => {
    if (!S.glyphCount) return toast("Questa scrittura non ha ancora lettere apprese: analizza la pagina o segna le lettere prima.", "warn");
    const name = await modal("Ricorda questa scrittura", `Dai un nome a “${$("#styleName").textContent}” (per esempio chi l'ha scritta). Potrai usarla su altre pagine o documenti quando vuoi.`,
      [["Annulla", false, "ghost"], ["Ricorda", true, "primary"]], $("#styleName").textContent.replace(/^(Scrittura|Stampato) \d+$/, ""));
    if (!name) return;
    await guarded(async () => { renderStyles(await call("save_style", S.current, S.style, name)); toast(`Calligrafia “${name}” ricordata.`, "ok"); });
  },
  renameStyle: async () => {
    if (S.style == null) return;
    const name = await modal("Rinomina la scrittura", "Per esempio il nome di chi ha scritto, o “Stampato del verbale”.",
      [["Annulla", false, "ghost"], ["Rinomina", true, "primary"]], $("#styleName").textContent);
    if (!name) return;
    await guarded(async () => applyInkResult(S.current, await call("rename_style", S.current, S.style, name)));
  },
  splitStyle: async () => {
    if (S.style == null) return;
    const ok = await modal("Dividere la scrittura?", `Le righe di “${$("#styleName").textContent}” vengono separate in due scritture, secondo inchiostro, tratto, inclinazione e dimensione.`,
      [["Annulla", false, "ghost"], ["Dividi", true, "primary"]]);
    if (!ok) return;
    await guarded(async () => {
      const r = await call("split_style", S.current, S.style);
      applyInkResult(S.current, r);
      toast("Scrittura divisa in due: controlla le righe e spostale se serve.", "ok");
    });
  },
  styleMenu: async () => {
    const menu = $("#styleMenu");
    if (!menu.hidden) { menu.hidden = true; return; }
    const list = await call("styles").catch(() => []);
    menu.innerHTML = list.length ? list.map(st => `<button data-name="${esc(st.name)}"><svg><use href="#i-pen"/></svg><div><b>${esc(st.name)}</b><small>${st.chars} caratteri</small></div></button>`).join("")
      : `<div class="empty">Nessuna calligrafia ricordata.<br>Usa <b>Ricorda…</b> su una pagina già letta.</div>`;
    $$("button", menu).forEach(b => b.onclick = () => { menu.hidden = true; applyStyle(b.dataset.name); });
    menu.hidden = false;
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
  if (!e.target.closest(".menu-wrap")) { $("#exportMenu").hidden = true; $("#styleMenu").hidden = true; $("#printMenu").hidden = true; }
});
$$("#tools button").forEach(b => b.onclick = () => setTool(b.dataset.tool));
$$("#tabs button").forEach(b => b.onclick = () => {
  $$("#tabs button").forEach(x => x.classList.toggle("active", x === b));
  $$(".tab-body").forEach(p => p.hidden = p.dataset.panel !== b.dataset.tab);
  if (b.dataset.tab === "comments") refreshComments();
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
$("#handModel").onchange = e => { saveSetting("handModel", e.target.checked); refreshModel(); };

// the dedicated handwriting reader: downloaded once, on request
let modelPoll = null;
async function refreshModel() {
  let m;
  try { m = await call("model_status"); } catch (e) { return; }
  const act = $("#modelAction"), st = $("#modelStatus"), bar = $("#modelBar");
  bar.hidden = !m.downloading;
  bar.firstElementChild.style.width = Math.round((m.progress || 0) * 100) + "%";
  act.hidden = m.downloading || (m.installed && !m.error) || !m.runtime;
  act.lastChild.textContent = m.error ? " Scarica di nuovo" : " Scarica il lettore";
  st.textContent = !m.runtime ? "Non disponibile in questa installazione."
    : m.downloading ? `Download… ${Math.round((m.progress || 0) * 100)}%`
    : m.error ? m.error
    : m.installed ? (m.enabled ? "Pronto: lo uso quando leggo le pagine." : "Scaricato, ma disattivato.")
    : "Non ancora scaricato.";
  clearTimeout(modelPoll);
  if (m.downloading) modelPoll = setTimeout(refreshModel, 600);
  else if (S.modelWasDownloading && m.installed && !m.error) toast("Lettore della scrittura a mano pronto.", "ok");
  S.modelWasDownloading = m.downloading;
}
$("#modelAction").onclick = async () => {
  try { await call("model_download"); } catch (e) { toast(e.message, "err"); }
  S.modelWasDownloading = true;
  refreshModel();
};

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
  if (e.key === "Escape" && !$("#filesModal").hidden) { $("#filesModal").hidden = true; return; }
  if (typing || !S.doc) return;
  const tools = { n: "view", m: "edit", t: "text", h: "hand", e: "erase", r: "mark", a: "annotate", f: "insert", x: "redact", o: "select" };
  if (tools[k]) setTool(tools[k]);
  if ((e.key === "Delete" || e.key === "Backspace") && S.sel) { e.preventDefault(); deleteSelected(); }
  if (e.key === "Escape") { closePop(); $("#exportMenu").hidden = true; $("#printMenu").hidden = true; }
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
document.addEventListener("dragover", e => { e.preventDefault(); if (S.dragPage == null && S.dragSide == null) document.body.classList.add("drag-over"); });
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
  { icon: "i-read", title: "Leggi la scrittura a mano",
    text: "Premi “Leggi scrittura”. Le righe scritte a mano sono colorate (un colore per persona), quelle stampate in grigio, quelle dubbie in rosso con un “?”, e l'inchiostro che non riesce a leggere in arancione: cliccalo e scrivi cosa c'è." },
  { icon: "i-letters", title: "Correggi e segna le lettere",
    text: "Clicca una riga per correggerne la lettura. Con “Segna lettere” indichi dove si trova ogni lettera e numero, così la calligrafia della pagina diventa più completa." },
  { icon: "i-layers", title: "Ogni scrittura con le sue lettere",
    text: "Sulla stessa pagina possono scrivere più persone: Calamo le separa (un colore per persona) e tiene a parte lo stampato. Ogni riga viene riscritta solo con le lettere della sua scrittura; se ha sbagliato, scegli la scrittura giusta nel riquadro della riga. “Ricorda…” la salva per altre pagine o documenti." },
  { icon: "i-pen", title: "Scrivi con la tua calligrafia", keys: ["H"],
    text: "Con lo strumento A mano clicca un punto vuoto della pagina e scrivi, oppure clicca una scritta esistente per riscriverla: il testo viene composto con le lettere della pagina, nello stesso inchiostro. Poi salva o esporta in Word ed Excel." },
  { icon: "i-marker", title: "Annota, firma, oscura", keys: ["A", "F", "X", "O"],
    text: "Annota (A) per evidenziare, sottolineare, lasciare note e forme; Firma (F) per mettere la tua firma o un'immagine; Oscura (X) per eliminare per sempre dati riservati. Con Modifica o Oggetti (O) prendi e trascina testi, scritte, firme e immagini. Il pulsante con i due fogli unisce o divide PDF interi; trascina le miniature per riordinare le pagine." },
  { icon: "i-columns", title: "Più documenti, leggere e presentare", keys: ["F5"],
    text: "Ogni PDF si apre in una scheda; con il pulsante delle due colonne ne affianchi un altro e trascini le pagine tra i due. Nella scheda Documento rendi cercabile una scansione, traduci il documento o lo converti; in alto puoi farlo leggere ad alta voce o presentarlo a schermo intero (F5)." },
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
