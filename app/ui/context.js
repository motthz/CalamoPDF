// The menu of the right mouse button: on a page, the tools and what can be done with that
// page; on a thumbnail, the page operations. Everything in it is also in the bar or in the
// panels: the menu only puts it where the mouse already is.
const CTX_PAGE = [
  ["Modifica il testo", "i-edit", "M", () => setTool("edit")],
  ["Aggiungi testo", "i-text", "T", () => setTool("text")],
  ["Evidenzia, note e forme", "i-marker", "A", () => setTool("annotate")],
  ["Firma o immagine", "i-sign", "F", () => setTool("insert")],
  ["Sposta e ridimensiona gli elementi", "i-move", "O", () => setTool("select")],
  "-",
  ["Leggi la scrittura a mano", "i-read", "", () => cmd("analyze")],
  ["Stampa questa pagina", "i-print", "", () => cmd("printPageNow")],
  ["Ruota la pagina", "i-rotate", "", () => cmd("rotatePage")],
];
const CTX_THUMB = [
  ["Ruota", "i-rotate", "", () => cmd("rotatePage")],
  ["Duplica", "i-files", "", () => cmd("duplicatePage")],
  ["Aggiungi una pagina vuota dopo", "i-page-plus", "", () => cmd("insertPage")],
  ["Stampa questa pagina", "i-print", "", () => cmd("printPageNow")],
  "-",
  ["Elimina la pagina", "i-trash", "", () => cmd("deletePage"), "danger"],
];

function closeCtx() { const m = $("#ctxMenu"); if (m) m.remove(); }

function openCtx(x, y, title, items) {
  closeCtx(); closePop();
  const m = document.createElement("div");
  m.id = "ctxMenu";
  m.className = "ctx-menu";
  m.innerHTML = `<div class="ctx-title">${esc(title)}</div>`;
  const sel = String(window.getSelection() || "").trim();
  if (sel) items = [["Copia", "i-files", "Ctrl+C", () => navigator.clipboard.writeText(sel).catch(() => document.execCommand("copy"))], "-", ...items];
  items.forEach(it => {
    if (it === "-") { m.insertAdjacentHTML("beforeend", '<div class="menu-sep"></div>'); return; }
    const [label, icon, key, run, kind] = it;
    const b = document.createElement("button");
    if (kind) b.className = kind;
    b.innerHTML = `<svg><use href="#${icon}"/></svg><span>${esc(label)}</span>${key ? `<kbd>${esc(key)}</kbd>` : ""}`;
    b.onclick = () => { closeCtx(); run(); };
    m.appendChild(b);
  });
  document.body.appendChild(m);
  // inside the window, also near its right and bottom edges
  m.style.left = Math.max(4, Math.min(x, window.innerWidth - m.offsetWidth - 6)) + "px";
  m.style.top = Math.max(4, Math.min(y, window.innerHeight - m.offsetHeight - 6)) + "px";
}

document.addEventListener("contextmenu", e => {
  if (e.target.closest("input, textarea, [contenteditable=true], .ctx-menu")) return;   // the usual menu to copy and paste
  e.preventDefault();
  if (!S.doc) return;
  const th = e.target.closest("#thumbs .thumb");
  const page = e.target.closest("#pages .page");
  if (th) {
    const i = +th.dataset.i;
    setCurrent(i);
    openCtx(e.clientX, e.clientY, `Pagina ${i + 1}`, CTX_THUMB);
  } else if (page) {
    const i = +page.dataset.i;
    setCurrent(i);
    openCtx(e.clientX, e.clientY, `Pagina ${i + 1}`, CTX_PAGE);
  }
});
document.addEventListener("mousedown", e => { if (!e.target.closest(".ctx-menu")) closeCtx(); }, true);
document.addEventListener("keydown", e => { if (e.key === "Escape") closeCtx(); });
window.addEventListener("blur", closeCtx);
// the thumbnails scroll by themselves to the page just chosen with the right button
document.addEventListener("scroll", e => { if (e.target.id !== "thumbs") closeCtx(); }, true);
