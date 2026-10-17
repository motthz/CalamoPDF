// Interface test of the tools added in 1.5, with a real browser and the real mouse:
//   python tools/ui_harness.py build/uitest &   then   node tools/ui_test_suite.js build/uitest
// all the tools window, forms (recognise, fill, create), links, crop, bookmarks, properties,
// check and PDF/A, digital signature and its check, printing, export, sheets, many files.
const path = require("path"), fs = require("fs");
const { chromium } = require(process.env.PLAYWRIGHT || "playwright");
const WORK = path.resolve(process.argv[2] || "build/uitest");
const OUT = path.join(WORK, "out");
const Q = (...v) => fs.writeFileSync(path.join(WORK, "next.json"), JSON.stringify(v));
let failed = 0;
const ok = (name, cond, extra = "") => { console.log((cond ? "PASS " : "FAIL ") + name + (extra !== "" ? "  [" + extra + "]" : "")); if (!cond) failed++; };

(async () => {
  const b = await chromium.launch(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {});
  const p = await b.newPage({ viewport: { width: 1480, height: 920 } });
  const errs = [];
  p.on("pageerror", e => errs.push(e.message));
  p.on("dialog", d => d.dismiss());
  const W = ms => p.waitForTimeout(ms);
  const ev = (f, a) => p.evaluate(f, a);
  const modalBtn = async label => { await p.waitForSelector("#modal:not([hidden])", { timeout: 5000 }); await p.click(`#modalActions button:text-is("${label}")`); await W(1200); };
  const modalInput = async (text, label) => { await p.waitForSelector("#modal:not([hidden])", { timeout: 5000 }); await p.fill("#modalInput", text); await p.click(`#modalActions button:text-is("${label}")`); await W(1500); };
  const pageXY = async (i, x, y) => ev(([i, x, y]) => { const P = S.pages[i], w = document.querySelector("#pages");
    w.scrollTop = P.el.offsetTop + y * scale() - w.clientHeight / 2; const r = P.el.getBoundingClientRect(); return [r.left + x * scale(), r.top + y * scale()]; }, [i, x, y]);
  const at = async (i, x, y) => ev(([i, x, y]) => { const r = S.pages[i].el.getBoundingClientRect(); return [r.left + x * scale(), r.top + y * scale()]; }, [i, x, y]);
  const drag = async (i, x0, y0, x1, y1) => {
    await pageXY(i, x0, (y0 + y1) / 2); await W(400);
    const a = await at(i, x0, y0), c = await at(i, x1, y1);
    await p.mouse.move(a[0], a[1]); await p.mouse.down(); await p.mouse.move(c[0], c[1], { steps: 12 }); await p.mouse.up(); await W(800);
  };
  const toasts = () => ev(() => [...document.querySelectorAll(".toast span")].map(t => t.textContent).join(" | "));
  for (let i = 0; i < 40; i++) { try { await p.goto("http://127.0.0.1:8765/"); break; } catch (e) { await W(500); } }
  await W(1500);

  // ---- welcome and all the tools
  ok("attività nella schermata iniziale", (await p.$$("#welcomeTasks .hub-item")).length === 8);
  await p.click("#btnHub"); await W(400);
  const all = (await p.$$("#hubBody .hub-item")).length;
  await p.fill("#hubSearch", "comprimi"); await W(200);
  const found = await ev(() => [...document.querySelectorAll("#hubBody .hub-item b")].map(x => x.textContent));
  ok("ricerca negli strumenti", all > 50 && found.includes("Comprimi PDF") && found.length < 5, `${all} ${found}`);
  await p.keyboard.press("Escape"); await W(200);
  ok("Esc chiude gli strumenti", await ev(() => document.querySelector("#hubModal").hidden));
  // a tool that needs a document asks for it first
  Q("modulo.pdf");
  await p.click('#welcomeTasks .hub-item[data-title="Compila un modulo"]'); await W(2500);
  ok("lo strumento chiede il PDF e poi parte", await ev(() => S.doc && S.doc.name === "modulo.pdf" && S.tool === "fill"), await ev(() => [S.doc && S.doc.name, S.tool]));
  ok("suggerimento dello strumento", (await p.textContent("#toolHintText")).length > 10, await p.textContent("#toolHintText"));

  // ---- forms: recognise, fill, create
  await p.click('[data-tool="fill"]'); await W(300);
  await p.click('#toolOpts [data-cmd="detectFields"]'); await W(2500);
  const fields = await ev(async () => (await call("fields")).map(f => [f.name, f.kind]));
  ok("riconosci i campi", fields.length === 3 && fields.some(f => f[0] === "Nome"), JSON.stringify(fields));
  await W(800);
  const inp = await p.$('.fill-in.fk-text');
  ok("campi da compilare sulla pagina", !!inp && (await p.$$(".fill-in")).length === 3);
  await inp.click(); await p.keyboard.type("Lucia"); await p.keyboard.press("Tab"); await W(2000);
  const nome = await ev(async () => (await call("fields")).find(f => f.name === "Nome").value);
  ok("compila un campo di testo", nome === "Lucia", nome);
  await p.click(".fill-in.fk-checkbox"); await W(2000);
  ok("spunta una casella", await ev(async () => (await call("fields")).find(f => f.kind === "checkbox").value === true));
  await p.click("#tabs [data-tab=forms]"); await W(500);
  ok("elenco dei campi nel pannello", (await p.$$("#fieldList .fitem")).length === 3);
  await p.click('#fieldKinds [data-k="combo"]'); await W(300);
  await drag(0, 300, 300, 450, 320);
  await modalInput("Roma, Milano, Torino", "Crea");
  const combo = await ev(async () => (await call("fields")).find(f => f.kind === "combo"));
  ok("crea un menu a tendina trascinando", combo && combo.options.length === 3, combo ? combo.options : await toasts());
  await p.keyboard.press("c"); await W(1200);
  await p.selectOption(".fill-in.fk-combo", "Milano"); await W(2000);
  ok("sceglie dal menu", await ev(async () => (await call("fields")).find(f => f.kind === "combo").value === "Milano"));

  // ---- links
  await p.click("#toolMoreBtn"); await p.click('#toolMenu [data-tool="link"]'); await W(300);
  ok("il menu Altro mostra lo strumento scelto", (await p.textContent("#toolMoreLabel")) === "Collegamenti");
  await p.click('#linkKind [data-k="page"]');
  await drag(0, 60, 45, 240, 65);
  await modalInput("3", "Crea");
  ok("collegamento a una pagina", await ev(async () => (await call("links", 0)).some(l => l.kind === "page" && l.page === 2)));
  await p.keyboard.press("n"); await W(800);
  const lnk = await p.$(".page[data-i='0'] .lnk");
  if (lnk) { await lnk.click(); await W(1200); }
  ok("Naviga segue il collegamento", await ev(() => S.current === 2), await ev(() => S.current));

  // ---- bookmarks
  await p.click('#sideSwitch [data-side="marks"]'); await W(500);
  await p.click('[data-cmd="markAuto"]'); await W(1500);
  const marks = await ev(async () => (await call("bookmarks")).map(m => m.title));
  ok("segnalibri dai titoli", marks.includes("Capitolo 1") && marks.includes("Capitolo 2"), marks);
  ok("segnalibri nella barra a sinistra", (await p.$$("#marksList .mark")).length === marks.length);
  await p.click("#marksList .mark >> nth=0"); await W(800);
  await p.click('#sideSwitch [data-side="pages"]');

  // ---- crop by dragging, undo
  await p.click("#toolMoreBtn"); await p.click('#toolMenu [data-tool="crop"]'); await W(300);
  await drag(1, 40, 40, 400, 300);
  await modalBtn("Solo questa pagina");
  const w1 = await ev(() => S.doc.pages[1].w);
  ok("ritaglia trascinando", Math.abs(w1 - 360) < 2, w1);
  await p.keyboard.press("Control+z"); await W(1500);
  ok("annulla il ritaglio", Math.abs((await ev(() => S.doc.pages[1].w)) - 595) < 2);

  // ---- properties, check, PDF/A
  await p.click("#tabs [data-tab=doc]"); await W(800);
  await p.fill("#prTitle", "Iscrizione 2026"); await p.selectOption("#prLang", "it-IT");
  await p.click('[data-cmd="saveProps"]'); await W(1200);
  ok("proprietà applicate", await ev(async () => (await call("properties")).title === "Iscrizione 2026"));
  await p.click('[data-cmd="preflight"]'); await W(2000);
  ok("verifica del documento", (await p.$$("#checkList .chk")).length >= 10);
  Q("modulo-pdfa.pdf");
  await p.click('#checkList ~ [data-cmd="savePdfa"]'); await W(4000);
  ok("salva PDF/A", fs.existsSync(path.join(OUT, "modulo-pdfa.pdf")), await toasts());

  // ---- digital signature: certificate, sign into a new box, check
  await p.click("#tabs [data-tab=security]"); await W(600);
  await p.click('[data-cmd="certNew"]'); await W(300);
  await p.fill("#certName", "Lucia Bianchi"); await p.fill("#certPw1", "segreta1"); await p.fill("#certPw2", "segreta1");
  await p.click("#certCreate"); await W(4000);
  ok("certificato personale creato", (await ev(() => document.querySelector("#certSel").selectedOptions[0].textContent)) === "Lucia Bianchi");
  await p.click('[data-cmd="signDigital"]'); await W(1500);
  ok("chiede di trascinare il riquadro", await ev(() => S.tool === "sigbox"));
  Q("modulo-firmato.pdf");
  await drag(0, 300, 650, 520, 720); await W(5000);
  ok("documento firmato e aperto", await ev(() => S.doc.name === "modulo-firmato.pdf"), await toasts());
  await p.click('[data-cmd="verifySigs"]'); await W(4000);
  const res = await p.textContent("#sigResults");
  ok("verifica della firma", res.includes("Lucia Bianchi") && res.includes("Integra"), res.slice(0, 160));

  // ---- print
  await ev(() => { window.__printed = 0; window.print = () => { window.__printed = document.querySelectorAll("#printArea img").length; dispatchEvent(new Event("afterprint")); }; });
  await p.keyboard.press("Control+p"); await W(4000);
  ok("stampa: tutte le pagine passate alla stampa", (await ev(() => window.__printed)) === 3, await ev(() => window.__printed));

  // ---- export, sheets
  Q("slide.pptx"); await p.click('[data-cmd="exportMenu"]'); await p.click('[data-cmd="exportPptx"]'); await W(4000);
  ok("esporta PowerPoint", fs.existsSync(path.join(OUT, "slide.pptx")));
  Q("pagina.html"); await p.click('[data-cmd="exportMenu"]'); await p.click('[data-cmd="exportHtml"]'); await W(2000);
  ok("esporta pagina web", fs.existsSync(path.join(OUT, "pagina.html")));
  await p.click("#tabs [data-tab=page]"); await W(500);
  await p.selectOption("#nupKind", "2x1"); await p.click('[data-cmd="makeNup"]'); await W(3000);
  ok("2 pagine per foglio in una nuova scheda", await ev(() => S.doc.pages.length === 2 && S.doc.pages[0].w > S.doc.pages[0].h), await ev(() => S.doc.pages.map(x => [Math.round(x.w), Math.round(x.h)])));

  // ---- many files at once
  await p.click("#btnHub"); await p.fill("#hubSearch", "più file"); await W(200); await p.keyboard.press("Enter"); await W(500);
  Q("a.pdf|b.pdf"); await p.click("#batchAdd"); await W(1000);
  ok("file aggiunti", (await p.$$("#batchList .merge-item")).length === 2);
  await p.selectOption("#batchAction", "numbers"); await W(200);
  Q("cartella"); await p.click("#batchGo"); await W(4000);
  ok("elabora più file", (await p.textContent("#batchResults")).includes("2 di 2"), await p.textContent("#batchResults"));
  await p.click("#batchClose");

  // ---- saving over a signed PDF warns first
  await ev(() => { const t = [...document.querySelectorAll("#docTabs .doc-tab span")].findIndex(x => x.textContent === "modulo-firmato.pdf"); return t; })
    .then(async t => { if (t >= 0) { await p.click(`#docTabs .doc-tab[data-i="${t}"]`); await W(2000); } });
  await p.keyboard.press("t"); await W(300);
  const pt = await pageXY(0, 300, 500); await p.mouse.click(pt[0], pt[1]); await W(400);
  await p.keyboard.type("Aggiunto"); await p.keyboard.press("Control+Enter"); await W(1500);
  await p.keyboard.press("Control+s"); await W(800);
  const warn = await ev(() => !document.querySelector("#modal").hidden && document.querySelector("#modalTitle").textContent);
  ok("salvare un PDF firmato avvisa", warn === "Il documento è firmato digitalmente", warn);
  if (warn) await modalBtn("Annulla");

  // ---- the other page and document tools
  Q("modulo.pdf"); await p.keyboard.press("Control+o"); await W(2500);
  await p.click("#tabs [data-tab=page]"); await W(400);
  await p.fill("#cropPages", "1"); await p.click('[data-cmd="cropMargins"]'); await W(1500);
  ok("ritaglia millimetri", Math.abs((await ev(() => S.doc.pages[0].w)) - (595 - 20 * 72 / 25.4)) < 2);
  await p.click('.card [data-cmd="cropReset"]'); await W(1500);
  await p.click('.card [data-cmd="cropAuto"]'); await W(2000);
  ok("ritaglia margini bianchi", (await ev(() => S.doc.pages[0].w)) < 500, await ev(() => S.doc.pages[0].w));
  await p.click('.card [data-cmd="cropReset"]'); await W(1500);
  await p.selectOption("#resizeSize", "A3"); await p.fill("#resizePages", "2"); await p.click('[data-cmd="resizePages"]'); await W(2500);
  ok("cambia formato", Math.round(await ev(() => S.doc.pages[1].w)) === 842);
  await p.click('[data-cmd="makeBooklet"]'); await W(3000);
  ok("opuscolo", await ev(() => S.doc.name.includes("opuscolo") && S.doc.pages.length === 2));
  await p.click("#docTabs .doc-tab >> nth=0"); await W(1500);
  await p.click("#tabs [data-tab=doc]"); await W(500);
  await p.fill("#batesPrefix", "ATTO-"); await p.click('[data-cmd="bates"]'); await W(1500);
  ok("numerazione Bates", (await ev(async () => (await call("search", "ATTO-000001")).length)) === 1);
  await p.click('[data-cmd="grayscale"]'); await modalBtn("Converti");
  ok("bianco e nero", (await toasts()).includes("bianco e nero"));
  Q("modulo.txt"); await p.click('[data-cmd="exportMenu"]'); await p.click('[data-cmd="exportTxt"]'); await W(1500);
  ok("esporta testo", fs.readFileSync(path.join(OUT, "modulo.txt"), "utf8").includes("Capitolo 1"));

  // ---- fields and bookmarks: rename, delete
  await p.click("#tabs [data-tab=forms]"); await W(800);
  const before = (await p.$$("#fieldList .fitem")).length;
  await p.click('#fieldList .fitem >> nth=0 >> [data-a="edit"]'); await modalInput("Nome di battesimo", "Rinomina");
  await W(500);
  const names = await ev(async () => (await call("fields")).map(f => f.name));
  ok("rinomina un campo", names.includes("Nome di battesimo"), names);
  await p.click('#fieldList .fitem >> nth=0 >> [data-a="del"]'); await W(1500);
  ok("elimina un campo", (await p.$$("#fieldList .fitem")).length === before - 1);
  await p.click('[data-cmd="flatten"]'); await modalBtn("Appiattisci");
  ok("appiattisci", (await ev(async () => (await call("fields")).length)) === 0);
  await p.click('#sideSwitch [data-side="marks"]'); await W(500);
  await p.click('[data-cmd="markAdd"]'); await modalInput("Inizio", "Aggiungi");
  ok("aggiungi un segnalibro", (await ev(async () => (await call("bookmarks")).length)) >= 1);
  const nm = (await p.$$("#marksList .mark")).length;
  await p.hover("#marksList .mark >> nth=0"); await p.click('#marksList .mark >> nth=0 >> [data-a="del"]'); await W(1000);
  ok("elimina un segnalibro", (await p.$$("#marksList .mark")).length === nm - 1 || (nm === 1 && (await p.$$("#marksList .mark")).length === 0));
  await p.click('#sideSwitch [data-side="pages"]');

  // ---- password, repair
  await p.click("#tabs [data-tab=security]"); await W(400);
  await p.fill("#pwUser", "abc"); Q("protetto.pdf"); await p.click('[data-cmd="protect"]'); await W(1500);
  fs.copyFileSync(path.join(OUT, "protetto.pdf"), path.join(WORK, "protetto.pdf"));
  Q("protetto.pdf"); await p.keyboard.press("Control+o"); await W(1500);
  await p.waitForSelector("#modal:not([hidden])"); await p.fill("#modalInput", "abc"); await p.click('#modalActions button:text-is("Apri")'); await W(2500);
  ok("apre il PDF protetto", await ev(() => S.doc.name === "protetto.pdf" && S.doc.protected));
  Q("libero.pdf"); await p.click('[data-cmd="unlockCopy"]'); await W(1500);
  ok("copia senza password", fs.existsSync(path.join(OUT, "libero.pdf")));
  await p.click('[data-cmd="removePassword"]'); await modalBtn("Togli");
  ok("togli la password dal documento", await ev(() => !S.doc.protected));
  fs.writeFileSync(path.join(WORK, "rotto.pdf"), fs.readFileSync(path.join(WORK, "modulo.pdf")).subarray(0, 3000));
  Q("rotto.pdf", "riparato.pdf"); await p.click("#btnHub"); await p.fill("#hubSearch", "ripara"); await p.keyboard.press("Enter"); await W(3000);
  ok("ripara un PDF", await ev(() => S.doc.name === "riparato.pdf"), await toasts());

  // ---- the guide
  await p.click('.titlebar [data-cmd="tutorial"]'); await W(400);
  ok("guida aggiornata", (await p.textContent("#tourTitle")).includes("Benvenuto") && (await p.textContent("#tourStep")).includes("di 9"));
  await p.keyboard.press("Escape");
  await p.click('.titlebar [data-cmd="settings"]'); await W(300);
  ok("impostazioni dalla barra del titolo", await ev(() => !document.querySelector('[data-panel="settings"]').hidden));

  ok("nessun errore JavaScript", errs.length === 0, errs.join(" | "));
  console.log(failed ? `${failed} PROBLEMI` : "TUTTO OK");
  await b.close();
  process.exit(failed ? 1 : 0);
})();
