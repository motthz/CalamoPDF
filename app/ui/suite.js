/* Calamo PDF Studio — the tools of the big suites and a clearer interface:
   all the tools in one searchable window, a hint for every tool, forms (fill, create,
   recognise, flatten), links, cropping, page size and sheets, bookmarks, properties,
   check and PDF/A, digital signatures, password removal, printing, many files at once */
"use strict";

Object.assign(S, { fieldKind: "text", fieldSel: null, linkKind: "uri", linkSel: null, side: S.side || null,
                   sideView: "pages", props: null, certs: [] });
const TSA_URL = "http://timestamp.digicert.com";
Object.assign(TOOL_NAMES, { fill: "Compila modulo", field: "Crea campi modulo", link: "Collegamenti",
                            crop: "Ritaglia pagine", sigbox: "Firma digitale: trascina il riquadro" });
const MORE_TOOLS = ["select", "erase", "redact", "link", "crop", "field", "mark"];

// ---------------------------------------------------------------- a hint for every tool
const HINTS = {
  view: "Scorri il documento. Clicca un collegamento per seguirlo.",
  edit: "Clicca una frase per riscriverla · trascinala per spostarla · Ctrl+Invio applica",
  text: "Clicca un punto della pagina e scrivi il nuovo testo.",
  hand: "Clicca un punto vuoto e scrivi: Calamo usa la calligrafia della pagina.",
  annotate: "Trascina sul testo per evidenziarlo; scegli il tipo di segno e il colore.",
  select: "Trascina per spostare · angolo per ridimensionare · Canc elimina",
  erase: "Trascina sull'area da cancellare.",
  redact: "Trascina sull'area da oscurare: il testo sotto viene eliminato davvero.",
  link: "Trascina un riquadro sul testo da collegare · clicca un collegamento per sceglierlo.",
  crop: "Trascina il riquadro da tenere: il resto della pagina viene tagliato.",
  field: "Scegli il tipo e trascina un riquadro sulla pagina · clicca un campo per sceglierlo.",
  mark: "Trascina un riquadro intorno a una scritta che Calamo non ha letto.",
  sigbox: "Trascina sulla pagina il riquadro dove comparirà la firma digitale.",
};
function hintFor(t) {
  if (t === "insert") return { signature: "Clicca sulla pagina dove mettere la firma.",
    image: "Clicca sulla pagina dove mettere l'immagine, poi scegli il file.",
    digital: "Trascina il riquadro dove mettere la firma digitale (certificato)." }[S.ins] || "";
  if (t === "fill") {
    const n = S.pages.reduce((k, P) => k + ((P.fields || []).length), 0);
    return n ? "Clicca nei campi e scrivi: si salvano da soli."
      : "Nessun campo da compilare? Prova Riconosci i campi.";
  }
  return HINTS[t] || "";
}
function updateHint() {
  $("#toolHintText").textContent = hintFor(S.tool);
}

showToolOpts = function (t) {
  $("#toolOpts").hidden = !S.doc;
  $$("#toolOpts .opts").forEach(o => o.hidden = o.dataset.opts !== t);
  if (t === "insert") loadSignatures();
  if (t !== "select") selectObj(null);
  if (t !== "field") selectField(null);
  if (t !== "link") selectLink(null);
  updateHint();
  S.pages.forEach(drawOverlay);
};

// the tools that are not in the bar are in the "Altro" menu: the button shows the one in use
const _setTool = setTool;
setTool = function (t) {
  _setTool(t);
  if (!S.doc) return;
  const more = MORE_TOOLS.includes(t);
  const b = $("#toolMoreBtn"), item = $(`#toolMenu [data-tool="${t}"]`);
  b.classList.toggle("active", more);
  $("#toolMoreLabel").textContent = more && item ? $("b", item).textContent : "Altro";
  $("#toolMoreIcon").innerHTML = `<use href="#${more && item ? $("use", item).getAttribute("href").slice(1) : "i-dots"}"/>`;
  $("#toolMenu").hidden = true;
};
$("#toolMoreBtn").onclick = e => { e.stopPropagation(); $("#toolMenu").hidden = !$("#toolMenu").hidden; };
$$("#toolMenu [data-tool]").forEach(b => b.onclick = () => setTool(b.dataset.tool));
document.addEventListener("click", e => { if (!e.target.closest("#toolMore")) $("#toolMenu").hidden = true; });

const _setDocUI = setDocUI;
setDocUI = function (has) {
  _setDocUI(has);
  $("#toolOpts").hidden = !has;
  if (!has) { $("#marks").hidden = true; }
};

// ---------------------------------------------------------------- panels of the right side
function showPanel(name) {
  if (name === "settings") {
    $$("#tabs button").forEach(x => x.classList.remove("active"));
  } else {
    $$("#tabs button").forEach(x => x.classList.toggle("active", x.dataset.tab === name));
  }
  $$(".tab-body").forEach(p => p.hidden = p.dataset.panel !== name);
  if (name === "comments") refreshComments();
  if (name === "forms") refreshFieldList();
  if (name === "doc") loadProps();
  if (name === "security") loadCerts();
}
$$("#tabs button").forEach(b => b.onclick = () => showPanel(b.dataset.tab));
// an element of a panel: the panel opens, the card is shown and lit up
function focusCard(id) {
  const el = document.getElementById(id);
  if (!el) return;
  const panel = el.closest(".tab-body");
  if (panel) showPanel(panel.dataset.panel);
  const card = el.closest(".card") || el;
  setTimeout(() => {
    (card.previousElementSibling && card.previousElementSibling.classList.contains("section-title") ? card.previousElementSibling : card)
      .scrollIntoView({ block: "start", behavior: "smooth" });
    card.classList.remove("lit"); void card.offsetWidth; card.classList.add("lit");
    if (el.matches("input, select, textarea")) el.focus({ preventScroll: true });
  }, 30);
}

// ---------------------------------------------------------------- all the tools, in one window
const HUB = [
  { cat: "Organizza le pagine", items: [
    ["Unisci PDF", "Più PDF in un unico file, nell'ordine che vuoi", "i-merge", 0, () => openFilesModal("merge"), "combina"],
    ["Dividi PDF", "Ogni N pagine, per intervalli, capitoli o dimensione", "i-split", 0, () => openFilesModal("split", !!S.doc), "separa"],
    ["Riordina, ruota, elimina", "Trascina le miniature; ruota o togli pagine", "i-layers", 1, () => showPanel("page"), "sposta ordine pagine"],
    ["Estrai pagine", "Alcune pagine in un nuovo PDF", "i-export", 1, () => focusCard("pageSpec"), "copia"],
    ["Ritaglia pagine", "Taglia i bordi o i margini bianchi", "i-crop", 1, () => setTool("crop"), "taglia margini"],
    ["Cambia formato pagine", "Da Lettera ad A4, da A3 ad A4…", "i-resize", 1, () => focusCard("resizeSize"), "ridimensiona a4 a3 dimensione"],
    ["Più pagine per foglio", "2, 4, 6 o 9 pagine su ogni foglio", "i-sheets", 1, () => focusCard("nupKind"), "n-up stampa"],
    ["Opuscolo", "Da stampare fronte-retro e piegare a metà", "i-doc", 1, () => focusCard("nupKind"), "libretto booklet"],
    ["Segnalibri", "L'indice del documento, anche dai titoli", "i-bookmark", 1, () => showSide("marks"), "indice capitoli"],
  ] },
  { cat: "Modifica", items: [
    ["Modifica testo", "Clicca una frase e riscrivila", "i-edit", 1, () => setTool("edit"), "correggi scrivi"],
    ["Aggiungi testo", "Scrivi in un punto qualsiasi", "i-text", 1, () => setTool("text"), ""],
    ["Scrivi a mano", "Con la calligrafia appresa dal documento", "i-pen", 1, () => setTool("hand"), "calligrafia"],
    ["Inserisci immagine", "Foto, logo o timbro", "i-image", 1, () => { setTool("insert"); pickIns("image"); }, "foto logo"],
    ["Collegamenti", "Link a siti web o ad altre pagine", "i-link", 1, () => setTool("link"), "link url"],
    ["Modifica tabelle", "Celle e righe con lo stesso aspetto", "i-table", 1, () => cmd("tableEdit"), ""],
    ["Numeri di pagina e filigrana", "Intestazione, piè di pagina, filigrana", "i-numbers", 1, () => focusCard("stFooter"), "intestazione piè numerazione bozza"],
    ["Numerazione Bates", "Numeri progressivi come negli atti legali", "i-numbers", 1, () => focusCard("batesPrefix"), "legale"],
    ["Gomma", "Cancella un'area della pagina", "i-eraser", 1, () => setTool("erase"), "cancella"],
  ] },
  { cat: "Converti", items: [
    ["Converti in PDF", "Word, Excel, PowerPoint, immagini, testo", "i-export", 0, () => openFilesModal("convert"), "word excel jpg"],
    ["Pagina web in PDF", "Da un indirizzo internet", "i-web", 0, () => { openFilesModal("convert"); setTimeout(() => $("#convUrl").focus(), 50); }, "html sito"],
    ["PDF in Word", "Documento .docx modificabile", "i-word", 1, () => cmd("exportDocx"), "docx"],
    ["PDF in Excel", "Le tabelle in fogli .xlsx", "i-excel", 1, () => cmd("exportXlsx"), "xlsx tabelle"],
    ["PDF in PowerPoint", "Una diapositiva per pagina", "i-slides", 1, () => cmd("exportPptx"), "pptx presentazione"],
    ["PDF in immagini", "Pagine in PNG o JPG", "i-image", 1, () => cmd("exportImages"), "jpg png"],
    ["PDF in pagina web", "Un file .html", "i-code", 1, () => cmd("exportHtml"), "html"],
    ["PDF in testo", "Solo il testo, in un file .txt", "i-txt", 1, () => cmd("exportTxt"), "txt"],
    ["PDF/A per l'archivio", "Per conservare e per gli uffici pubblici", "i-archive", 1, () => cmd("savePdfa"), "pdfa conservazione"],
  ] },
  { cat: "Moduli e firme", items: [
    ["Compila un modulo", "Scrivi nei campi del PDF", "i-form", 1, () => setTool("fill"), "riempi"],
    ["Rendi compilabile un modulo", "Trova righe, puntini e caselle da riempire", "i-scan", 1, () => cmd("detectFields"), "riconosci campi"],
    ["Crea campi", "Testo, caselle, menu, firma", "i-plus", 1, () => setTool("field"), "campo modulo"],
    ["Appiattisci", "Campi e commenti diventano parte della pagina", "i-layers", 1, () => focusCard("flatForms"), "flatten blocca"],
    ["Firma", "Disegnata o presa da una foto", "i-sign", 1, () => { setTool("insert"); pickIns("signature"); }, "autografa"],
    ["Firma digitale", "Con certificato .p12 / .pfx (PAdES)", "i-cert", 1, () => focusCard("certSel"), "certificato pades"],
    ["Verifica firme", "Chi ha firmato e se il file è integro", "i-check-c", 1, () => { showPanel("security"); cmd("verifySigs"); }, "controlla"],
  ] },
  { cat: "Proteggi", items: [
    ["Proteggi con password", "Per aprire, stampare o copiare", "i-lock", 1, () => focusCard("pwUser"), "cifra"],
    ["Togli la password", "Copia senza password né limiti", "i-key", 1, () => focusCard("btnRemovePw"), "sblocca unlock"],
    ["Oscura", "Elimina per sempre testo riservato", "i-redact", 1, () => setTool("redact"), "censura nascondi"],
    ["Trova dati sensibili", "Codici fiscali, IBAN, email, telefoni", "i-search", 1, () => focusCard("sensTerm"), "privacy gdpr"],
  ] },
  { cat: "Ottimizza", items: [
    ["Comprimi PDF", "Un file più leggero da mandare per email", "i-feather", 1, () => focusCard("optQuality"), "riduci alleggerisci dimensione"],
    ["Ripara PDF", "Recupera un file danneggiato", "i-wrench", 0, () => cmd("repair"), "rotto corrotto"],
    ["Rendi cercabile (OCR)", "Le scansioni diventano testo", "i-scan", 1, () => cmd("ocrAll"), "ocr scansione"],
    ["Bianco e nero", "Tutti i colori in scala di grigi", "i-contrast", 1, () => cmd("grayscale"), "grigio"],
    ["Verifica documento", "PDF/A e accessibilità", "i-check-c", 1, () => { focusCard("checkList"); cmd("preflight"); }, "accessibile controllo"],
    ["Proprietà", "Titolo, autore, lingua", "i-info", 1, () => focusCard("prTitle"), "metadati"],
  ] },
  { cat: "Rivedi e condividi", items: [
    ["Annota", "Evidenzia, sottolinea, note e forme", "i-marker", 1, () => setTool("annotate"), "evidenzia"],
    ["Commenti", "Tutti i commenti del documento", "i-note", 1, () => showPanel("comments"), "note"],
    ["Confronta due versioni", "Le differenze tra due PDF", "i-compare", 1, () => cmd("compare"), "differenze"],
    ["Traduci", "Una copia tradotta con la stessa impaginazione", "i-translate", 1, () => focusCard("trLang"), "lingua inglese"],
    ["Leggi la scrittura a mano", "Riconosce e corregge il testo a mano", "i-read", 1, () => cmd("analyze"), "calligrafia ocr"],
    ["Leggi ad alta voce", "Con le voci di Windows", "i-speak", 1, () => cmd("speak"), "audio"],
    ["Presenta", "A schermo intero", "i-present", 1, () => cmd("present"), "schermo"],
    ["Stampa", "Sulla stampante che scegli", "i-print", 1, () => cmd("print"), "carta"],
  ] },
  { cat: "Più file", items: [
    ["Elabora più file insieme", "Comprimi, numera, proteggi, PDF/A… tanti file in un colpo", "i-stack", 0, () => openBatch(), "batch lotto cartella"],
  ] },
];
const WELCOME_TASKS = ["Modifica testo", "Unisci PDF", "Dividi PDF", "Comprimi PDF", "Converti in PDF", "PDF in Word",
                       "Compila un modulo", "Firma"];
const hubItems = () => HUB.flatMap(c => c.items.map(it => ({ cat: c.cat, title: it[0], desc: it[1], icon: it[2],
                                                                 needDoc: !!it[3], run: it[4], words: it[5] || "" })));
// a tool that works on the open document: if none is open, it is asked for first
async function runTool(it) {
  closeHub();
  if (it.needDoc && !S.doc) {
    toast(`Scegli il PDF per “${it.title}”.`);
    await openDialog();
    if (!S.doc) return;
  }
  it.run();
}
function toolCard(it) {
  return `<button class="hub-item" data-title="${esc(it.title)}"><svg><use href="#${it.icon}"/></svg>
    <span><b>${esc(it.title)}</b><small>${esc(it.desc)}</small></span></button>`;
}
function renderHub(q) {
  q = (q || "").trim().toLowerCase();
  const all = hubItems();
  const match = it => !q || (it.title + " " + it.desc + " " + it.words + " " + it.cat).toLowerCase().includes(q);
  const cats = HUB.map(c => ({ cat: c.cat, items: all.filter(it => it.cat === c.cat && match(it)) })).filter(c => c.items.length);
  $("#hubBody").innerHTML = cats.length ? cats.map(c => `<section class="hub-cat"><h4>${esc(c.cat)}</h4>
      <div class="hub-grid">${c.items.map(toolCard).join("")}</div></section>`).join("")
    : `<div class="empty">Nessuno strumento per “${esc(q)}”.</div>`;
  $$("#hubBody .hub-item").forEach(b => b.onclick = () => runTool(all.find(it => it.title === b.dataset.title)));
}
function openHub() {
  $("#hubModal").hidden = false;
  $("#hubSearch").value = "";
  renderHub("");
  setTimeout(() => $("#hubSearch").focus(), 30);
}
$("#hubSearch").addEventListener("input", e => renderHub(e.target.value));
function closeHub() { $("#hubModal").hidden = true; if (document.activeElement) document.activeElement.blur(); }
$("#hubSearch").addEventListener("keydown", e => {
  if (!e.ctrlKey) e.stopPropagation();
  if (e.key === "Escape") closeHub();
  if (e.key === "Enter") { const b = $("#hubBody .hub-item"); if (b) b.click(); }
});
$("#hubClose").onclick = closeHub;
$("#hubModal").addEventListener("mousedown", e => { if (e.target.id === "hubModal") closeHub(); });
function renderWelcomeTasks() {
  const all = hubItems();
  const list = WELCOME_TASKS.map(t => all.find(it => it.title === t)).filter(Boolean);
  $("#welcomeTasks").innerHTML = list.map(toolCard).join("");
  $$("#welcomeTasks .hub-item").forEach(b => b.onclick = () => runTool(all.find(it => it.title === b.dataset.title)));
}
renderWelcomeTasks();
function pickIns(k) { const b = $(`#insKind [data-k="${k}"]`); if (b) b.click(); }

// ---------------------------------------------------------------- the left side: pages or bookmarks
function showSide(view) {
  S.sideView = view;
  $$("#sideSwitch [data-side]").forEach(b => b.classList.toggle("active", b.dataset.side === view));
  $("#thumbs").hidden = view !== "pages";
  $("#marks").hidden = view !== "marks" || !S.doc;
  if (view === "marks") loadMarks();
}
$$("#sideSwitch [data-side]").forEach(b => b.onclick = () => showSide(b.dataset.side));
let MARKS = [], marksGen = 0;
async function loadMarks() {
  if (!S.doc) return;
  const gen = ++marksGen;
  let list;
  try { list = await call("bookmarks"); } catch (e) { list = []; }
  if (gen !== marksGen) return;          // a save (or a newer reading) came after: this list is old
  MARKS = list;
  renderMarks();
}
function renderMarks() {
  const L = $("#marksList");
  if (!MARKS.length) {
    L.innerHTML = `<div class="empty small">Nessun segnalibro.<br><b>Aggiungi</b> crea un segnalibro per la pagina che guardi; <b>Dai titoli</b> li crea dai titoli del documento.</div>`;
    return;
  }
  L.innerHTML = MARKS.map((m, k) => `<div class="mark" data-k="${k}" style="--lvl:${m.level - 1}" title="Pagina ${m.page + 1} · doppio clic per rinominare">
      <span class="mark-t">${esc(m.title)}</span><small>${m.page + 1}</small>
      <span class="mark-acts">
        <button data-a="out" title="Sposta a sinistra (livello superiore)">‹</button><button data-a="in" title="Sposta a destra (dentro il precedente)">›</button>
        <button data-a="up" title="Su">↑</button><button data-a="down" title="Giù">↓</button><button data-a="del" title="Elimina">×</button>
      </span></div>`).join("");
  $$(".mark", L).forEach(el => {
    const k = +el.dataset.k;
    el.onclick = e => {
      const a = e.target.closest("[data-a]");
      if (!a) return goToPage(MARKS[k].page);
      const m = MARKS.slice();
      if (a.dataset.a === "del") m.splice(k, 1);
      else if (a.dataset.a === "in") m[k] = { ...m[k], level: m[k].level + 1 };
      else if (a.dataset.a === "out") m[k] = { ...m[k], level: Math.max(1, m[k].level - 1) };
      else { const j = a.dataset.a === "up" ? k - 1 : k + 1; if (j < 0 || j >= m.length) return; [m[k], m[j]] = [m[j], m[k]]; }
      saveMarks(m);
    };
    el.ondblclick = async () => {
      const t = await modal("Rinomina il segnalibro", "Il testo che compare nell'indice.", [["Annulla", false, "ghost"], ["Rinomina", true, "primary"]], MARKS[k].title);
      if (!t) return;
      const m = MARKS.slice(); m[k] = { ...m[k], title: t }; saveMarks(m);
    };
  });
}
async function saveMarks(list) {
  marksGen++;
  await guarded(async () => { const r = await call("set_bookmarks", list); marksGen++; MARKS = r.bookmarks; updateInfo(r); renderMarks(); });
}

// ---------------------------------------------------------------- forms
function loadFields(P) {
  if (P.fieldsV === P.v || P.fieldsLoading === P.v) return;
  const v = P.fieldsLoading = P.v;
  call("fields", P.i).then(f => {
    if (P.v !== v) return;
    P.fields = f; P.fieldsV = v;
    drawOverlay(P);
    if (S.tool === "fill") updateHint();
  }).catch(() => {}).finally(() => { if (P.fieldsLoading === v) P.fieldsLoading = null; });
}
function setFields(list) {          // the fields of the whole document, after a change
  S.pages.forEach(P => { P.fields = list.filter(f => f.page === P.i); P.fieldsV = -1; });
}
async function fieldsChanged(r, quiet) {
  await afterChange(r);
  setFields(r.fields);
  S.pages.forEach(P => { P.fieldsV = P.v; });
  S.pages.forEach(drawOverlay);
  if (!$('[data-panel="forms"]').hidden) renderFieldList(r.fields);
  if (S.tool === "fill") updateHint();
}
async function fillField(f, value) {
  if (JSON.stringify(f.value) === JSON.stringify(value)) return;
  S.fillFocusAfter = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.fid : null;
  await guarded(async () => fieldsChanged(await call("fill_fields", [{ page: f.page, id: f.id, value }])));
}
function drawFill(P, frag, s) {
  (P.fields || []).forEach(f => {
    const r = f.rect, h = (r[3] - r[1]) * s;
    let el;
    if (f.kind === "checkbox" || f.kind === "radio") {
      el = document.createElement("input");
      el.type = f.kind;
      el.checked = !!f.value;
      if (f.kind === "radio") el.name = "rad-" + f.name;
      el.onchange = () => fillField(f, el.checked);
    } else if (f.kind === "combo" || f.kind === "list") {
      el = document.createElement("select");
      el.innerHTML = f.options.map(o => `<option${o === f.value ? " selected" : ""}>${esc(o)}</option>`).join("");
      if (f.kind === "list") el.size = Math.max(2, Math.min(f.options.length, Math.floor(h / 16)));
      el.onchange = () => fillField(f, el.value);
    } else if (f.kind === "signature") {
      el = document.createElement("button");
      el.textContent = f.signed ? "✓ Firmato" : "Firma qui";
      el.title = f.signed ? "Campo firmato digitalmente" : "Firma digitale in questo campo";
      el.onclick = () => { if (!f.signed) signInto(f.name); };
    } else {
      el = document.createElement(f.multiline ? "textarea" : "input");
      el.value = f.value == null ? "" : f.value;
      el.spellcheck = false;
      el.style.fontSize = Math.max(9, Math.min(h * 0.62, 15 * s)) + "px";
      const commit = () => fillField(f, el.value);
      el.onchange = commit;
      el.onkeydown = e => { e.stopPropagation(); if (e.key === "Enter" && !f.multiline) { e.preventDefault(); el.blur(); } };
    }
    el.className = "fill-in fk-" + f.kind;
    el.dataset.fid = f.id;
    el.title = f.label || f.name;
    if (f.readonly) el.disabled = true;
    Object.assign(el.style, box(r, s, 0));
    el.addEventListener("mousedown", e => e.stopPropagation());
    el.addEventListener("click", e => e.stopPropagation());
    frag.appendChild(el);
  });
}
function drawFieldBoxes(P, frag, s) {
  (P.fields || []).forEach(f => {
    const d = document.createElement("div");
    const on = S.fieldSel && S.fieldSel.page === P.i && S.fieldSel.id === f.id;
    d.className = "fld" + (on ? " on" : "");
    d.dataset.fid = f.id;
    d.innerHTML = `<span>${esc(f.name)}</span>`;
    d.title = `${f.kindLabel}: ${f.name}`;
    Object.assign(d.style, box(f.rect, s, 1));
    frag.appendChild(d);
  });
}
function selectField(sel) {
  S.fieldSel = sel;
  $("#fieldDelete").disabled = $("#fieldEdit").disabled = !sel;
  if (sel) S.pages.forEach(drawOverlay);
}
segSwitch($("#fieldKind"), "k", k => { S.fieldKind = k; });
$$("#fieldKinds [data-k]").forEach(b => b.onclick = () => {
  S.fieldKind = b.dataset.k;
  $$("#fieldKind button").forEach(x => x.classList.toggle("active", x.dataset.k === b.dataset.k));
  setTool("field");
  toast("Trascina un riquadro sulla pagina dove mettere il campo.");
});
const fieldOf = sel => sel && S.pages[sel.page] && (S.pages[sel.page].fields || []).find(f => f.id === sel.id);
async function createField(P, rect) {
  const kind = S.fieldKind;
  let name = "", options = null;
  if (kind === "combo") {
    const v = await modal("Opzioni del menu", "Scrivi le scelte separate da virgole.", [["Annulla", false, "ghost"], ["Crea", true, "primary"]], "Sì, No");
    if (!v) return;
    options = v.split(",").map(x => x.trim()).filter(Boolean);
  } else if (kind === "radio") {
    const groups = [...new Set(S.pages.flatMap(Q => (Q.fields || []).filter(f => f.kind === "radio").map(f => f.name)))];
    name = await modal("Gruppo di scelta", "Le scelte con lo stesso nome si escludono a vicenda (es. Sesso: M / F).",
      [["Annulla", false, "ghost"], ["Crea", true, "primary"]], groups[groups.length - 1] || "Scelta");
    if (!name) return;
  }
  await guarded(async () => {
    const r = await call("add_field", P.i, kind, rect, name, options, kind === "text" && rect[3] - rect[1] > 36);
    await fieldsChanged(r);
    selectField({ page: P.i, id: r.id });
    toast("Campo aggiunto: con Compila puoi provarlo.", "ok");
  });
}
$("#fieldDelete").onclick = async () => {
  const f = fieldOf(S.fieldSel);
  if (!f) return;
  await guarded(async () => { const r = await call("delete_field", f.page, f.id); selectField(null); await fieldsChanged(r); });
};
$("#fieldEdit").onclick = () => { const f = fieldOf(S.fieldSel); if (f) renameField(f); };
async function renameField(f) {
  const name = await modal("Nome del campo", "Il nome con cui il campo viene riconosciuto (es. Nome, Data di nascita).",
    [["Annulla", false, "ghost"], ["Rinomina", true, "primary"]], f.name);
  if (!name) return;
  let options = null;
  if (f.kind === "combo" || f.kind === "list") {
    const v = await modal("Opzioni", "Le scelte, separate da virgole.", [["Lascia così", false, "ghost"], ["Cambia", true, "primary"]], f.options.join(", "));
    if (v) options = v.split(",").map(x => x.trim()).filter(Boolean);
  }
  await guarded(async () => fieldsChanged(await call("edit_field", f.page, f.id, name, options, null, null)));
}
async function refreshFieldList() {
  if (!S.doc) return;
  try { renderFieldList(await call("fields")); } catch (e) { /* later */ }
}
function renderFieldList(list) {
  $("#fieldCount").textContent = list.length ? `(${list.length})` : "";
  const L = $("#fieldList");
  if (!list.length) { L.innerHTML = `<div class="empty small">Nessun campo. Usa <b>Riconosci i campi</b> o aggiungine uno.</div>`; return; }
  L.innerHTML = list.map((f, k) => `<div class="fitem" data-k="${k}"><span class="ftype">${esc(f.kindLabel)}</span>
      <div class="grow"><b>${esc(f.name)}</b><small>pagina ${f.page + 1}${f.kind === "signature" ? (f.signed ? " · firmato" : " · da firmare")
        : f.kind === "checkbox" || f.kind === "radio" ? (f.value ? " · ✓" : "") : f.value ? " · " + esc(String(f.value)).slice(0, 40) : ""}</small></div>
      <button class="icon-btn" data-a="edit" title="Rinomina"><svg><use href="#i-edit"/></svg></button>
      <button class="icon-btn danger" data-a="del" title="Elimina"><svg><use href="#i-trash"/></svg></button></div>`).join("");
  $$(".fitem", L).forEach(el => {
    const f = list[+el.dataset.k];
    el.onclick = async e => {
      const a = e.target.closest("[data-a]");
      if (!a) { goToPage(f.page, f.rect[1]); flashBox(f.page, f.rect); return; }
      if (a.dataset.a === "del") await guarded(async () => fieldsChanged(await call("delete_field", f.page, f.id)));
      else renameField(f);
    };
  });
}

// ---------------------------------------------------------------- links
function drawLinks(P, frag, s) {
  (P.data.links || []).forEach(l => {
    const d = document.createElement("div");
    const on = S.linkSel && S.linkSel.page === P.i && S.linkSel.id === l.id;
    d.className = "lnk" + (on ? " on" : "");
    d.dataset.lid = l.id;
    d.title = l.kind === "uri" ? l.uri : l.kind === "page" ? `Vai a pagina ${l.page + 1}` : "Collegamento";
    Object.assign(d.style, box(l.rect, s, 1));
    frag.appendChild(d);
  });
}
function selectLink(sel) {
  S.linkSel = sel;
  $("#linkDelete").disabled = !sel;
  if (sel) S.pages.forEach(drawOverlay);
}
segSwitch($("#linkKind"), "k", k => { S.linkKind = k; });
async function createLink(P, rect) {
  const web = S.linkKind === "uri";
  const v = await modal(web ? "Collegamento a un sito" : "Collegamento a una pagina",
    web ? "Scrivi l'indirizzo (es. www.esempio.it) o un'email." : `Il numero della pagina (da 1 a ${S.pages.length}).`,
    [["Annulla", false, "ghost"], ["Crea", true, "primary"]], web ? "" : String(Math.min(S.current + 2, S.pages.length)));
  if (!v) return;
  if (!web && !(+v >= 1 && +v <= S.pages.length)) return toast("Pagina non valida.", "warn");
  await guarded(async () => {
    const r = await call("add_link", P.i, rect, web ? v : null, web ? null : +v - 1);
    await afterChange(r);
    toast("Collegamento creato: con lo strumento Naviga puoi provarlo.", "ok");
  });
}
$("#linkDelete").onclick = async () => {
  const sel = S.linkSel;
  if (!sel) return;
  await guarded(async () => { const r = await call("delete_link", sel.page, sel.id); selectLink(null); await afterChange(r); toast("Collegamento eliminato", "ok"); });
};
function followLink(P, l) {
  if (l.kind === "page" && l.page >= 0) goToPage(l.page);
  else if (l.kind === "uri") guarded(async () => call("open_url", l.uri));
}

// ---------------------------------------------------------------- the page: drawing and mouse
const _drawExtras = drawExtras;
drawExtras = function (P, frag, s) {
  $$(".fill-in, .fld, .lnk", P.overlay).forEach(n => n.remove());
  _drawExtras(P, frag, s);
  if (S.tool === "fill" || S.tool === "field") {
    loadFields(P);
    if (S.tool === "fill") drawFill(P, frag, s); else drawFieldBoxes(P, frag, s);
  }
  if (S.tool === "link" || S.tool === "view") drawLinks(P, frag, s);
  if (S.fillFocusAfter && S.tool === "fill") {
    const id = S.fillFocusAfter;
    setTimeout(() => { const el = $(`.fill-in[data-fid="${id}"]`); if (el && document.activeElement !== el) el.focus(); }, 0);
    S.fillFocusAfter = null;
  }
};
const _toolMouseDown = toolMouseDown;
toolMouseDown = function (P, e) {
  const t = S.tool;
  if (t === "field") {
    const el = e.target.closest(".fld");
    if (el) { e.preventDefault(); selectField({ page: P.i, id: +el.dataset.fid }); return true; }
    startDrag(P, e, rect => createField(P, rect), "sel-rect field");
    return true;
  }
  if (t === "link") {
    const el = e.target.closest(".lnk");
    if (el) { e.preventDefault(); selectLink({ page: P.i, id: +el.dataset.lid }); return true; }
    startDrag(P, e, rect => createLink(P, rect), "sel-rect link");
    return true;
  }
  if (t === "crop") {
    startDrag(P, e, rect => cropRect(P, rect), "sel-rect crop");
    return true;
  }
  if (t === "sigbox" || (t === "insert" && S.ins === "digital")) {
    startDrag(P, e, rect => signBox(P, rect), "sel-rect sigbox");
    return true;
  }
  if (t === "view") {
    const el = e.target.closest(".lnk");
    if (el) { const l = (P.data.links || []).find(x => x.id === +el.dataset.lid); if (l) followLink(P, l); return true; }
  }
  return _toolMouseDown(P, e);
};
const _toolClick = toolClick;
toolClick = function (P, e, pt) {
  if (["field", "link", "crop", "sigbox", "fill", "view"].includes(S.tool)) return true;
  if (S.tool === "insert" && S.ins === "digital") return true;
  return _toolClick(P, e, pt);
};

// ---------------------------------------------------------------- crop, page size, sheets
const pagesOr = (id, def) => ($("#" + id).value || "").trim() || def;
async function cropRect(P, rect) {
  const r = await modal("Ritagliare?", `Il riquadro tracciato diventa la nuova pagina (${Math.round((rect[2] - rect[0]) / 72 * 25.4)} × ${Math.round((rect[3] - rect[1]) / 72 * 25.4)} mm). Puoi annullare con Ctrl+Z.`,
    [["Annulla", null, "ghost"], ["Tutte le pagine", "all", "ghost"], ["Solo questa pagina", "one", "primary"]]);
  if (!r) return;
  await guarded(async () => {
    const res = await call("crop_pages", r === "one" ? String(P.i + 1) : "", rect, null, false);
    S.inkCache = {};
    await afterChange(res);
    toast(`Ritagliate ${plural(res.cropped, "pagina", "pagine")}`, "ok");
  });
}
Object.assign(COMMANDS, {
  cropTool: () => setTool("crop"),
  cropMargins: () => guarded(async () => {
    const m = ["cropT", "cropR", "cropB", "cropL"].map(id => +$("#" + id).value || 0);
    const r = await call("crop_pages", $("#cropPages").value, null, m, false);
    S.inkCache = {};
    await afterChange(r);
    toast(r.cropped ? `Ritagliate ${plural(r.cropped, "pagina", "pagine")} (Ctrl+Z per annullare)` : "Niente da ritagliare.", r.cropped ? "ok" : "");
  }),
  cropAuto: () => guarded(async () => {
    const r = await call("crop_pages", $("#cropPages").value, null, null, true);
    S.inkCache = {};
    await afterChange(r);
    toast(r.cropped ? `Tolti i margini bianchi da ${plural(r.cropped, "pagina", "pagine")}` : "Nessun margine bianco da togliere.", r.cropped ? "ok" : "");
  }, "Cerco i margini…"),
  cropReset: () => guarded(async () => {
    const r = await call("reset_crop", $("#cropPages").value);
    S.inkCache = {};
    await afterChange(r);
    toast(r.cropped ? `Ripristinate ${plural(r.cropped, "pagina", "pagine")}` : "Nessuna pagina era ritagliata.", r.cropped ? "ok" : "");
  }),
  resizePages: () => guarded(async () => {
    const r = await call("resize_pages", $("#resizePages").value, $("#resizeSize").value);
    S.inkCache = {};
    await afterChange(r);
    toast(`${plural(r.resized, "pagina portata", "pagine portate")} in ${$("#resizeSize").selectedOptions[0].textContent}`, "ok");
  }, "Cambio formato…"),
  makeNup: () => guarded(async () => {
    const [c, r] = $("#nupKind").value.split("x").map(Number);
    const res = await call("make_nup", c, r, $("#nupSize").value, "", $("#nupBorder").checked);
    loadDoc(res.info);
    toast("Copia creata in una nuova scheda: salvala o stampala.", "ok");
  }, "Preparo i fogli…"),
  makeBooklet: () => guarded(async () => {
    const res = await call("make_booklet", $("#nupSize").value);
    loadDoc(res.info);
    toast("Opuscolo pronto in una nuova scheda: stampa fronte-retro (lato corto), piega e pinza.", "ok");
  }, "Preparo l'opuscolo…"),
});

// ---------------------------------------------------------------- forms: commands
Object.assign(COMMANDS, {
  fillTool: () => setTool("fill"),
  fieldsPanel: () => showPanel("forms"),
  detectFields: () => guarded(async () => {
    const r = await call("detect_fields", "");
    await fieldsChanged(r);
    if (r.added) {
      setTool("fill");
      toast(`Trovati ${plural(r.added, "campo", "campi")} da compilare. Controllali nel pannello Moduli.`, "ok", { label: "Moduli", run: () => showPanel("forms") });
    } else toast("Non ho trovato spazi da compilare. Puoi aggiungere i campi a mano (pannello Moduli).", "warn");
  }, "Cerco i campi da compilare…"),
  flatten: async () => {
    const forms = $("#flatForms").checked, annots = $("#flatAnnots").checked;
    if (!forms && !annots) return toast("Scegli cosa appiattire.", "warn");
    const ok = await modal("Appiattire?", "Gli elementi scelti diventano parte della pagina e non si potranno più modificare. Le firme digitali smettono di essere verificabili. Puoi annullare con Ctrl+Z finché non salvi.",
      [["Annulla", false, "ghost"], ["Appiattisci", true, "primary"]]);
    if (!ok) return;
    await guarded(async () => {
      const r = await call("flatten", forms, annots);
      await afterChange(r);
      setFields([]); refreshFieldList(); S.pages.forEach(drawOverlay);
      toast("Fatto: il documento è appiattito.", "ok");
    });
  },
});

// ---------------------------------------------------------------- digital signatures
async function loadCerts(select) {
  try { S.certs = await call("certificates"); } catch (e) { S.certs = []; }
  const sel = $("#certSel"), cur = select || sel.value || (S.settings && S.settings.cert);
  sel.innerHTML = S.certs.length ? S.certs.map(c => `<option value="${esc(c.path)}">${esc(c.name)}</option>`).join("")
    : `<option value="">Nessun certificato: creane uno o scegli un file</option>`;
  if (cur && S.certs.some(c => c.path === cur)) sel.value = cur;
}
$("#certSel").onchange = e => saveSetting("cert", e.target.value);
async function signReady() {
  const cert = $("#certSel").value, pw = $("#certPw").value;
  if (!cert) { focusCard("certSel"); toast("Prima crea un certificato personale o scegli il file del tuo certificato.", "warn"); return null; }
  if (!pw) { focusCard("certPw"); toast("Scrivi la password del certificato.", "warn"); return null; }
  try { await call("certificate_info", cert, pw); } catch (e) { toast(e.message, "err"); focusCard("certPw"); return null; }
  return { cert, pw };
}
async function doSign(index, rect, field) {
  const c = await signReady();
  if (!c) return;
  await guarded(async () => {
    const r = await call("sign_digital", c.cert, c.pw, index, rect, field || null, $("#sigReason").value, $("#sigPlace").value,
      $("#sigTsa").checked ? TSA_URL : "");
    if (!r) return;
    if (S.tool === "sigbox") setTool("edit");
    if (r.info) loadDoc(r.info);
    toast(`Firmato digitalmente da ${r.signer}: “${r.path.split(/[\\/]/).pop()}”. Non modificarlo più, o la firma non sarà più valida.`, "ok");
  }, "Firma in corso…");
}
async function signBox(P, rect) { await doSign(P.i, rect, null); }
async function signInto(field) { await doSign(0, null, field); }
$("#sigboxCancel").onclick = () => setTool("edit");
Object.assign(COMMANDS, {
  certNew: () => {
    $("#certModal").hidden = false;
    ["certName", "certEmail", "certOrg", "certPw1", "certPw2"].forEach(id => { $("#" + id).value = ""; });
    setTimeout(() => $("#certName").focus(), 30);
  },
  certImport: () => guarded(async () => {
    const r = await call("import_certificate");
    if (!r) return;
    await loadCerts(r.created.path);
    saveSetting("cert", r.created.path);
    $("#certPw").value = ""; $("#certPw").focus();
    toast(`Certificato “${r.created.name}” pronto: scrivi la sua password.`, "ok");
  }),
  signDigital: async () => {
    const c = await signReady();
    if (!c) return;
    let empty = [];
    try { empty = (await call("fields")).filter(f => f.kind === "signature" && !f.signed); } catch (e) { /* none */ }
    if (empty.length) {
      const btns = [["Annulla", null, "ghost"], ["Nuovo riquadro", "new", "ghost"], ...empty.slice(0, 3).map(f => [`Nel campo “${f.name}”`, f.name, "primary"])];
      const r = await modal("Dove mettere la firma?", "Il documento ha già degli spazi per la firma.", btns);
      if (!r) return;
      if (r !== "new") return signInto(r);
    }
    setTool("sigbox");
    toast("Trascina sulla pagina il riquadro dove comparirà la firma.");
  },
  verifySigs: () => guarded(async () => {
    const r = await call("verify_signatures");
    const L = $("#sigResults");
    if (!r.signatures.length) { L.innerHTML = `<p class="muted small">Questo PDF non ha firme digitali.</p>`; return; }
    L.innerHTML = (r.dirty ? `<p class="muted small">Verificata la versione salvata del file (le modifiche non salvate non sono firmate).</p>` : "")
      + r.signatures.map(x => {
        const cls = !x.intact ? "bad" : x.trusted || x.later || x.valid ? (x.changedAfter ? "warn" : "ok") : "warn";
        return `<div class="sig-res ${cls}"><svg><use href="#${cls === "ok" ? "i-check-c" : "i-warn"}"/></svg>
          <div><b>${esc(x.signer || "Firmatario sconosciuto")}</b><small>${x.time ? "il " + esc(x.time) : ""}${x.timestamped ? " · marca temporale" : ""} · campo “${esc(x.field)}”</small>
          <p>${esc(x.summary)}</p></div></div>`;
      }).join("");
  }, "Verifica delle firme…"),
});
$("#certCancel").onclick = () => { $("#certModal").hidden = true; };
$("#certCreate").onclick = () => guarded(async () => {
  if ($("#certPw1").value !== $("#certPw2").value) return toast("Le due password non coincidono.", "warn");
  const r = await call("create_certificate", $("#certName").value, $("#certEmail").value, $("#certOrg").value, $("#certPw1").value);
  $("#certModal").hidden = true;
  await loadCerts(r.created.path);
  saveSetting("cert", r.created.path);
  $("#certPw").value = $("#certPw1").value;
  toast(`Certificato di ${r.created.name} creato. Ora puoi firmare.`, "ok");
}, "Creo il certificato…");
$$("#certModal input").forEach(i => i.addEventListener("keydown", e => e.stopPropagation()));

// ---------------------------------------------------------------- password, colours, properties, check
Object.assign(COMMANDS, {
  securityPanel: () => focusCard("sensTerm"),
  unlockCopy: () => guarded(async () => {
    const r = await call("save_unlocked");
    if (r) toast("Copia senza password salvata", "ok", reveal(r));
  }),
  removePassword: async () => {
    if (!S.doc || !S.doc.protected) return toast("Questo documento non è protetto da password. Per un PDF protetto aprilo con la sua password.", "warn");
    const ok = await modal("Togliere la password?", "Al prossimo salvataggio il documento non chiederà più la password.", [["Annulla", false, "ghost"], ["Togli", true, "primary"]]);
    if (ok) guarded(async () => { updateInfo(await call("remove_password")); toast("Password tolta: salva per confermare (Ctrl+S).", "ok"); });
  },
  grayscale: async () => {
    const ok = await modal("Convertire in bianco e nero?", "Testi, disegni e immagini diventano in scala di grigi. Puoi annullare con Ctrl+Z.", [["Annulla", false, "ghost"], ["Converti", true, "primary"]]);
    if (ok) guarded(async () => { await afterChange(await call("to_grayscale")); toast("Documento in bianco e nero", "ok"); }, "Conversione dei colori…");
  },
  bates: () => guarded(async () => {
    const [where, align] = $("#batesPos").value.split("-");
    const r = await call("stamp_pages", { [where]: "{bates}", align, size: 9, bates_prefix: $("#batesPrefix").value,
      bates_start: +$("#batesStart").value || 1, bates_digits: +$("#batesDigits").value || 6 });
    await afterChange(r);
    toast(`Numerate ${plural(r.stamped, "pagina", "pagine")} (Ctrl+Z per annullare)`, "ok");
  }),
  saveProps: () => guarded(async () => {
    const r = await call("set_properties", { title: $("#prTitle").value, author: $("#prAuthor").value, subject: $("#prSubject").value,
      keywords: $("#prKeywords").value, lang: $("#prLang").value });
    updateInfo(r); fillProps(r.properties);
    toast("Proprietà applicate: salva per tenerle.", "ok");
  }),
  preflight: () => guarded(async () => renderCheck(await call("preflight")), "Verifica del documento…"),
  savePdfa: () => guarded(async () => {
    $("#exportMenu").hidden = true;
    const r = await call("save_pdfa");
    if (!r) return;
    if (r.ok) toast("Copia PDF/A salvata: pronta per l'archiviazione.", "ok", reveal(r));
    else toast(`Copia salvata, ma resta da sistemare: ${r.left.map(x => x.label).join(", ")}.`, "warn", reveal(r));
  }, "Creazione del PDF/A…"),
  repair: () => guarded(async () => {
    const r = await call("repair_file");
    if (!r) return;
    loadDoc(r.info);
    toast(!r.repaired ? "Il file era già integro: ne ho salvato una copia pulita." : r.bad.length
      ? `File riparato. Pagine non recuperabili: ${r.bad.join(", ")}.` : "File riparato: tutte le pagine recuperate.", r.bad.length ? "warn" : "ok");
  }, "Riparazione…"),
});
async function loadProps() {
  if (!S.doc) return;
  try { fillProps(await call("properties")); } catch (e) { /* later */ }
}
function fillProps(p) {
  S.props = p;
  $("#prTitle").value = p.title; $("#prAuthor").value = p.author; $("#prSubject").value = p.subject; $("#prKeywords").value = p.keywords;
  if (p.lang && !$(`#prLang option[value="${p.lang}"]`)) $("#prLang").insertAdjacentHTML("beforeend", `<option value="${esc(p.lang)}">${esc(p.lang)}</option>`);
  $("#prLang").value = p.lang || "";
  const bits = [plural(p.pages, "pagina", "pagine"), p.pageSize, p.size ? kb(p.size) : "", p.format,
    p.created ? "creato il " + p.created : "", p.creator ? "con " + p.creator : "",
    p.encrypted ? "protetto da password" : "", p.forms ? plural(p.forms, "campo", "campi") : "",
    p.signatures ? plural(p.signatures, "firma digitale", "firme digitali") : ""].filter(Boolean);
  $("#prInfo").textContent = bits.join(" · ");
}
const FIXES = { pdfa: ["Salva PDF/A…", () => cmd("savePdfa")], props: ["Proprietà", () => focusCard("prTitle")],
                ocr: ["Rendi cercabile", () => cmd("ocrAll")], bookmarks: ["Segnalibri", () => { showSide("marks"); cmd("markAuto"); }] };
function renderCheck(r) {
  const group = (g, title, ok) => `<div class="chk-head ${ok ? "ok" : "warn"}"><svg><use href="#${ok ? "i-check-c" : "i-warn"}"/></svg>${title}</div>`
    + r.items.filter(i => i.group === g).map((i, k) => `<div class="chk ${i.ok ? "ok" : "no"}"><i>${i.ok ? "✓" : "!"}</i>
      <div><b>${esc(i.label)}</b>${i.detail ? `<small>${esc(i.detail)}</small>` : ""}</div>
      ${!i.ok && FIXES[i.fix] ? `<button class="btn ghost sm" data-fix="${i.fix}">${FIXES[i.fix][0]}</button>` : ""}</div>`).join("");
  $("#checkList").innerHTML = group("pdfa", r.pdfa ? "È un PDF/A pronto per l'archivio" : "Non è ancora un PDF/A", r.pdfa)
    + group("access", r.access ? "Accessibile" : "Accessibilità da migliorare", r.access);
  $$("#checkList [data-fix]").forEach(b => b.onclick = () => FIXES[b.dataset.fix][1]());
}

// ---------------------------------------------------------------- export in other formats
async function exportOther(kind, label) {
  $("#exportMenu").hidden = true;
  closeEditor(true);
  await guarded(async () => {
    const r = await call("export_other", kind);
    busy(false);
    if (r) toast(`Esportato “${r.name}”${r.slides ? ` (${plural(r.slides, "diapositiva", "diapositive")})` : ""}`, "ok", { label: "Apri", run: () => call("reveal", r.path) });
  }, `Esportazione in ${label}…`);
}
Object.assign(COMMANDS, {
  exportPptx: () => exportOther("pptx", "PowerPoint"),
  exportHtml: () => exportOther("html", "pagina web"),
  exportTxt: () => exportOther("txt", "testo"),
  exportImages: () => { $("#exportMenu").hidden = true; openFilesModal("convert"); setTimeout(() => $("#imgPages").focus(), 50); },
  toolsHub: openHub,
  settings: () => showPanel("settings"),
  markAdd: async () => {
    if (S.sideView !== "marks") showSide("marks");
    await loadMarks();
    const def = (S.pages[S.current] && (S.pages[S.current].data || {}).blocks || [])[0];
    const t = await modal("Nuovo segnalibro", `Porta alla pagina ${S.current + 1}. Come lo chiami?`, [["Annulla", false, "ghost"], ["Aggiungi", true, "primary"]],
      def ? def.text.split("\n")[0].slice(0, 60) : `Pagina ${S.current + 1}`);
    if (!t) return;
    const m = MARKS.slice();
    let at = m.findIndex(x => x.page > S.current);
    if (at < 0) at = m.length;
    m.splice(at, 0, { level: at > 0 ? m[at - 1].level : 1, title: t, page: S.current });
    saveMarks(m);
  },
  markAuto: () => guarded(async () => {
    await loadMarks();
    const list = await call("bookmarks_from_headings");
    if (!list.length) return toast("Non ho trovato titoli nel testo (le scansioni vanno prima rese cercabili).", "warn");
    if (MARKS.length) {
      const ok = await modal("Sostituire i segnalibri?", `Ho trovato ${plural(list.length, "titolo", "titoli")}. Prendono il posto dei segnalibri attuali (Ctrl+Z per annullare).`,
        [["Annulla", false, "ghost"], ["Sostituisci", true, "primary"]]);
      if (!ok) return;
    }
    if (S.sideView !== "marks") showSide("marks");
    await saveMarks(list);
    toast(`Creati ${plural(list.length, "segnalibro", "segnalibri")} dai titoli`, "ok");
  }, "Cerco i titoli…"),
  print: () => printDoc(),
});

// ---------------------------------------------------------------- printing (the system dialog)
async function printDoc() {
  if (!S.doc) return;
  let pages = S.pages.map(P => P.i);
  if (pages.length > 40) {
    const v = await modal("Quali pagine stampare?", `Il documento ha ${pages.length} pagine. Lascia vuoto per stamparle tutte, oppure scrivi per esempio 1-5, 8.`,
      [["Annulla", false, "ghost"], ["Prepara la stampa", true, "primary"]], "");
    if (v == null) return;
    if (v) pages = parseSpec(v, pages.length);
    if (!pages.length) return toast("Nessuna pagina in questo intervallo.", "warn");
  }
  const area = $("#printArea");
  area.innerHTML = "";
  try {
    for (let k = 0; k < pages.length; k++) {
      busy(`Preparo la stampa… ${k + 1}/${pages.length}`, (k + 1) / pages.length);
      const P = S.pages[pages[k]];
      const img = new Image();
      img.src = await call("render", P.i, 2.4, "jpeg");
      img.className = P.w > P.h ? "land" : "";
      await img.decode().catch(() => {});
      area.appendChild(img);
    }
  } catch (e) { busy(false); area.innerHTML = ""; return toast(e.message, "err"); }
  busy(false);
  document.body.classList.add("printing");
  let done = false;
  const clean = () => { if (done) return; done = true; document.body.classList.remove("printing"); area.innerHTML = ""; };
  window.addEventListener("afterprint", clean, { once: true });
  setTimeout(() => { window.print(); setTimeout(clean, 120000); }, 60);
}
function parseSpec(spec, n) {
  const out = [];
  spec.split(/[,;]/).forEach(part => {
    const m = part.trim().match(/^(\d*)\s*-\s*(\d*)$/);
    const [a, b] = m ? [+m[1] || 1, +m[2] || n] : [+part, +part];
    for (let p = Math.max(1, a); p <= Math.min(n, b); p++) if (!out.includes(p - 1)) out.push(p - 1);
  });
  return out;
}

// ---------------------------------------------------------------- many files at once
const BT = { items: [] };
function openBatch() {
  $("#batchModal").hidden = false;
  $("#batchResults").innerHTML = "";
  renderBatch(); batchOpts();
}
function renderBatch() {
  $("#batchList").innerHTML = BT.items.length ? BT.items.map((it, k) => `<div class="merge-item" data-k="${k}"><svg><use href="#i-pdf"/></svg>
      <div class="grow"><b>${esc(it.name)}</b><small>${it.locked ? "protetto da password" : plural(it.pages, "pagina", "pagine")} · ${kb(it.size)}</small></div>
      <button class="icon-btn danger" data-a="del" title="Togli"><svg><use href="#i-x"/></svg></button></div>`).join("")
    : `<div class="empty small">Nessun file: premi <b>Aggiungi PDF…</b> e scegline quanti vuoi.</div>`;
  $$("#batchList [data-a=del]").forEach(b => b.onclick = () => { BT.items.splice(+b.closest(".merge-item").dataset.k, 1); renderBatch(); });
  $("#batchCount").textContent = BT.items.length ? plural(BT.items.length, "file", "file") : "";
  $("#batchGo").disabled = !BT.items.length;
}
function batchOpts() {
  const a = $("#batchAction").value;
  $$(".batch-form [data-for]").forEach(l => { l.hidden = !l.dataset.for.split(" ").includes(a); });
  if (a === "numbers" && !$("#batchText").value) $("#batchText").value = "Pagina {n} di {N}";
  if (a === "watermark" && (!$("#batchText").value || $("#batchText").value.includes("{n}"))) $("#batchText").value = "BOZZA";
}
$("#batchAction").onchange = batchOpts;
$$(".batch-form input").forEach(i => i.addEventListener("keydown", e => e.stopPropagation()));
$("#batchClose").onclick = () => { $("#batchModal").hidden = true; };
$("#batchAdd").onclick = () => guarded(async () => {
  const list = await call("pick_pdfs", true);
  list.forEach(f => { if (!BT.items.some(x => x.path === f.path)) BT.items.push(f); });
  renderBatch();
});
$("#batchGo").onclick = () => guarded(async () => {
  const a = $("#batchAction").value;
  if (a === "protect" && !$("#batchNewPw").value) return toast("Scrivi la nuova password.", "warn");
  const opts = { quality: $("#batchQuality").value, text: $("#batchText").value, angle: +$("#batchAngle").value,
                 newPassword: $("#batchNewPw").value, password: $("#batchPw").value };
  const r = await call("batch_run", BT.items.map(it => it.path), a, opts);
  busy(false);
  if (!r) return;
  $("#batchResults").innerHTML = `<p class="small"><b>${r.ok} di ${r.total}</b> file elaborati.</p>` + r.results.map(x =>
    `<div class="bres ${x.ok ? "ok" : "bad"}"><i>${x.ok ? "✓" : "!"}</i><span>${esc(x.name)}</span><small>${esc(x.ok ? x.note || "fatto" : x.error)}</small></div>`).join("")
    + `<button class="btn ghost sm" id="batchFolder"><svg><use href="#i-open"/></svg>Apri la cartella</button>`;
  $("#batchFolder").onclick = () => call("reveal", r.folder);
  toast(`${r.ok} di ${r.total} file elaborati`, r.ok === r.total ? "ok" : "warn");
}, "Elaborazione dei file…");

// saving rewrites the file: the digital signatures in it would no longer be valid
const _save = COMMANDS.save;
COMMANDS.save = async () => {
  if (S.doc && S.doc.dirty && S.props && S.props.signatures) {
    const r = await modal("Il documento è firmato digitalmente", "Salvando le modifiche sullo stesso file le firme non saranno più valide. Ti conviene salvare una copia con un altro nome.",
      [["Annulla", null, "ghost"], ["Salva comunque", "save", "ghost"], ["Salva una copia…", "copy", "primary"]]);
    if (r === "copy") return cmd("saveAs");
    if (r !== "save") return;
  }
  return _save();
};

// ---------------------------------------------------------------- opening a document
const _loadDoc2 = loadDoc;
loadDoc = function (info) {
  S.fieldSel = null; S.linkSel = null; MARKS = [];
  $("#sigResults").innerHTML = ""; $("#checkList").innerHTML = "";
  const r = _loadDoc2(info);
  if (S.sideView === "marks") loadMarks();
  showToolOpts(S.tool);
  if (!$('[data-panel="doc"]').hidden) loadProps();
  if (!$('[data-panel="forms"]').hidden) refreshFieldList();
  updateHint();
  // what this document is: a form to fill, a signed document
  const gen = S.docGen;
  setTimeout(async () => {
    let p;
    try { p = await call("properties"); } catch (e) { return; }
    if (gen !== S.docGen) return;
    S.props = p;
    if (p.signatures) toast(`Questo PDF è firmato digitalmente (${plural(p.signatures, "firma", "firme")}).`, "", { label: "Verifica", run: () => { showPanel("security"); cmd("verifySigs"); } });
    else if (p.forms && S.tool !== "fill") toast("Questo PDF è un modulo da compilare.", "", { label: "Compila", run: () => setTool("fill") });
  }, 700);
  return r;
};

// ---------------------------------------------------------------- keyboard
// first of all: a field that was hidden (a closed panel, the find bar) keeps no keys, and the
// Ctrl shortcuts of the whole app work from any field (the fields stop the other keys)
document.addEventListener("keydown", e => {
  const a = document.activeElement;
  if (a && a !== document.body && a.closest && !a.closest(".popover, .editor") && a.getClientRects().length === 0) a.blur();
  if (!e.ctrlKey || e.altKey) return;
  const k = e.key.toLowerCase();
  const global = { o: "open", s: e.shiftKey ? "saveAs" : "save", p: "print", k: "toolsHub", f: "find" }[k];
  if (!global) return;
  if (!$("#modal").hidden || !$("#tour").hidden) return;
  e.preventDefault(); e.stopImmediatePropagation();
  if (global !== "open" && global !== "toolsHub" && !S.doc) return;
  if (global === "print") return printDoc();
  if (global === "toolsHub") return openHub();
  cmd(global);
}, true);
document.addEventListener("keydown", e => {
  const k = e.key.toLowerCase();
  if (e.key === "Escape") {
    for (const id of ["hubModal", "batchModal", "certModal"]) if (!$("#" + id).hidden) { $("#" + id).hidden = true; if (document.activeElement) document.activeElement.blur(); e.stopImmediatePropagation(); return; }
    if (S.tool === "sigbox") { setTool("edit"); return; }
  }
  if (e.ctrlKey || e.altKey || !S.doc || e.target.closest("input, textarea, select, [contenteditable]")) return;
  if (!$("#tour").hidden || !$("#modal").hidden) return;
  const more = { c: "fill", l: "link", k: "crop", q: "field" }[k];
  if (more) { setTool(more); e.stopImmediatePropagation(); }
  if (e.key === "Delete" && S.tool === "field" && S.fieldSel) { e.preventDefault(); $("#fieldDelete").click(); }
  if (e.key === "Delete" && S.tool === "link" && S.linkSel) { e.preventDefault(); $("#linkDelete").click(); }
}, true);

// ---------------------------------------------------------------- the guide, for the new layout
TOUR.splice(0, TOUR.length,
  { icon: "logo", title: "Benvenuto in Calamo PDF Studio",
    text: "Modifica, firma, compila e converti i PDF, anche le parti scritte a mano. In pochi passi ti mostro dove trovare tutto. Puoi rivedere questa guida con il pulsante ? in alto a destra." },
  { icon: "i-apps", title: "Tutti gli strumenti in un posto", keys: ["Ctrl", "K"],
    text: "Il pulsante “Strumenti” apre l'elenco di tutto quello che Calamo sa fare: unire, dividere, comprimere, convertire, proteggere, firmare… Scrivi cosa vuoi fare nella casella di ricerca e premi Invio." },
  { icon: "i-edit", title: "Modifica il testo", keys: ["M"],
    text: "Con lo strumento Modifica clicca una frase e riscrivila sul posto: font, dimensione e colore restano quelli originali. Sotto la barra trovi sempre un suggerimento su come usare lo strumento scelto." },
  { icon: "i-form", title: "Compila e firma", keys: ["C", "F"],
    text: "Compila (C) per scrivere nei moduli; se il modulo è pensato per la carta, “Riconosci i campi” li crea da solo. Firma (F) per mettere la tua firma o una firma digitale con certificato." },
  { icon: "i-dots", title: "Gli altri strumenti", keys: ["O", "E", "X", "L", "K"],
    text: "Nel menu “Altro” trovi Oggetti (sposta firme e immagini), Gomma, Oscura, Collegamenti, Ritaglia e Crea campi." },
  { icon: "i-layers", title: "I pannelli a destra", text: "Scrittura per la scrittura a mano, Pagine per ruotare, ritagliare e ridimensionare, Moduli, Sicurezza (firma digitale, password, oscuramento), Documento (proprietà, PDF/A, OCR, traduzione, numeri di pagina) e Commenti." },
  { icon: "i-read", title: "La scrittura a mano", text: "Premi “Leggi scrittura”: Calamo legge le righe scritte a mano, impara la calligrafia e ti lascia correggerla. Con lo strumento A mano (H) scrivi nuovo testo con la stessa calligrafia." },
  { icon: "i-bookmark", title: "Pagine e segnalibri", text: "A sinistra trovi le miniature (trascinale per riordinare) e i segnalibri, l'indice del documento. Ogni PDF si apre in una scheda; con il pulsante delle due colonne ne affianchi un altro." },
  { icon: "i-export", title: "Salva, esporta, stampa", keys: ["Ctrl", "S"],
    text: "Ctrl+S salva, Ctrl+P stampa. “Esporta” salva in Word, Excel, PowerPoint, immagini, pagina web o PDF/A per l'archivio. Calamo si aggiorna da solo quando esce una nuova versione." },
);

// ---------------------------------------------------------------- dragging a page far away: the list scrolls by itself
(() => {
  let speed = 0, timer = null;
  const tick = () => { if (!speed) { timer = null; return; } $("#thumbs").scrollTop += speed; $("#thumbs2").scrollTop += speed; timer = requestAnimationFrame(tick); };
  document.addEventListener("dragover", e => {
    if (S.dragPage == null && S.dragSide == null) { speed = 0; return; }
    const box = e.target.closest && e.target.closest("#thumbs, #thumbs2");
    if (!box) { speed = 0; return; }
    const r = box.getBoundingClientRect(), edge = 60;
    speed = e.clientY < r.top + edge ? -Math.ceil((r.top + edge - e.clientY) / 4)
      : e.clientY > r.bottom - edge ? Math.ceil((e.clientY - (r.bottom - edge)) / 4) : 0;
    if (speed && !timer) timer = requestAnimationFrame(tick);
  });
  ["drop", "dragend"].forEach(t => document.addEventListener(t, () => { speed = 0; }));
})();
