// Interface test with a real browser and the real mouse (no synthetic events):
//   python tools/ui_harness.py build/uitest &   then   node tools/ui_test.js build/uitest
// * dragging the elements of a page (table with its text, drawing, picture, form field);
// * dragging a page between two documents side by side;
// * reading aloud with the voices of the system (skipped where there are none).
const path = require("path"), fs = require("fs");
const { chromium } = require(process.env.PLAYWRIGHT || "playwright");
const WORK = path.resolve(process.argv[2] || "build/uitest");
const Q = (...v) => fs.writeFileSync(path.join(WORK, "next.json"), JSON.stringify(v));
let failed = 0;
const ok = (name, cond, extra = "") => { console.log((cond ? "PASS " : "FAIL ") + name + (extra !== "" ? "  [" + extra + "]" : "")); if (!cond) failed++; };

(async () => {
  const b = await chromium.launch(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {});
  const p = await b.newPage({ viewport: { width: 1480, height: 920 } });
  const errs = [];
  p.on("pageerror", e => errs.push(e.message));
  await p.addInitScript(() => { window.__t = []; new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => { if (n.classList && n.classList.contains("toast")) window.__t.push(n.textContent); }))).observe(document, { childList: true, subtree: true }); });
  const T = async () => (await p.evaluate(() => (window.__t || []).splice(0))).forEach(t => console.log("   avviso: " + t));
  const W = ms => p.waitForTimeout(ms);
  const geo = async (y = 0) => p.evaluate(y => { const P = S.pages[0], w = document.querySelector("#pages"); w.scrollTop = P.el.offsetTop + y * scale() - w.clientHeight / 2; const r = P.el.getBoundingClientRect(); return { x: r.left, y: r.top, s: scale() }; }, y);
  const drag = async (x0, y0, x1, y1) => {
    await geo((y0 + y1) / 2); await W(400); const g = await geo((y0 + y1) / 2);
    await p.mouse.move(g.x + x0 * g.s, g.y + y0 * g.s); await p.mouse.down();
    await p.mouse.move(g.x + x1 * g.s, g.y + y1 * g.s, { steps: 15 }); await p.mouse.up(); await W(2500);
  };
  const gfx = () => p.evaluate(async () => (await call("graphics", S.current)).map(g => [g.kind, g.bbox.map(Math.round)]));
  for (let i = 0; i < 40; i++) { try { await p.goto("http://127.0.0.1:8765/"); break; } catch (e) { await W(500); } }
  await W(1500);

  // ---- every element of the page can be dragged
  Q("dig.pdf"); await p.keyboard.press("Control+o"); await W(2500);
  await p.keyboard.press("m"); await W(1500);
  ok("elementi trovati", (await p.$$(".page .gfx")).length >= 4, (await p.$$(".page .gfx")).length);
  await drag(150, 100, 200, 400);
  const voce = (await p.evaluate(async () => (await call("blocks", 0)).blocks)).find(x => x.text.startsWith("Voce 1"));
  ok("tabella trascinata con il suo testo", voce && Math.abs(voce.bbox[0] - 115) < 3 && Math.abs(voce.bbox[1] - 404) < 6, voce && voce.bbox.map(Math.round));
  await drag(450, 120, 450, 220);
  ok("disegno trascinato", (await gfx()).some(x => x[0] === "vector" && Math.abs(x[1][1] - 195) < 3), JSON.stringify(await gfx()));
  await drag(100, 325, 100, 625);
  ok("immagine trascinata", (await gfx()).some(x => x[0] === "image" && Math.abs(x[1][1] - 600) < 3));
  await drag(440, 300, 440, 690);
  ok("campo modulo trascinato", (await gfx()).some(x => x[0] === "field" && Math.abs(x[1][1] - 690) < 3));

  // ---- resize from the corner handle (hover the element, drag its handle)
  const handleDrag = async (sel, dx, dy) => {
    const el = await p.$(sel);
    const r = await el.boundingBox();
    await p.mouse.move(r.x + r.width / 2, r.y + r.height / 2); await W(250);
    const h = await (await p.$(sel + " .rz")).boundingBox();
    await p.mouse.move(h.x + h.width / 2, h.y + h.height / 2); await p.mouse.down();
    await p.mouse.move(h.x + h.width / 2 + dx, h.y + h.height / 2 + dy, { steps: 12 }); await p.mouse.up(); await W(2500);
  };
  await geo(600); await W(400);
  const imgId = await p.evaluate(() => (S.pages[0].data.graphics || []).find(g => g.kind === "image").id);
  await handleDrag(`.page .gfx[data-id="${imgId}"]`, 60, 0);
  await T();
  let gi = (await gfx()).find(x => x[0] === "image");
  ok("immagine ingrandita dall'angolo (proporzioni tenute)", gi && gi[1][2] - gi[1][0] > 100 && gi[1][3] - gi[1][1] > 60, JSON.stringify(await gfx()));
  await geo(60); await W(400);
  const titleId = await p.evaluate(() => S.pages[0].data.blocks.find(b => b.text.startsWith("Documento di prova")).id);
  await handleDrag(`.page .blk[data-id="${titleId}"]`, 80, 0);
  const title = (await p.evaluate(async () => (await call("blocks", 0)).blocks)).find(b => b.text.startsWith("Documento di prova"));
  ok("titolo ingrandito dall'angolo", title && title.size > 18, title && title.size);
  // ---- print: the three choices
  await p.click("#btnPrint"); await W(500);
  const pm = await p.$$eval("#printMenu button", bs => bs.map(b => b.querySelector("b").textContent));
  ok("stampa: tre possibilità", pm.length === 3 && pm[1].includes("tutto") && pm[2].includes("pagina"), JSON.stringify(pm));
  await p.keyboard.press("Escape"); await W(200);
  // ---- a scan not read yet: each writing and a mark inside the table can be taken by itself
  Q("scan_tab.pdf"); await p.keyboard.press("Control+o"); await W(2500);
  await p.keyboard.press("m"); await W(4000);
  const sg = await p.evaluate(() => (S.pages[0].data.graphics || []).map(g => [g.label, g.bbox.map(Math.round)]));
  const writings = sg.filter(x => x[0] === "Scritta").length;
  const mark = sg.find(x => x[0] === "Grafica" && x[1][2] - x[1][0] < 60);
  ok("scansione non letta: scritte nelle celle prendibili una per una", writings >= 4, JSON.stringify(sg));
  ok("scansione non letta: il segno dentro la tabella è un elemento a sé", !!mark, JSON.stringify(sg));
  if (mark) {
    const cx = (mark[1][0] + mark[1][2]) / 2, cy = (mark[1][1] + mark[1][3]) / 2;
    await drag(cx, cy, cx - 60, cy + 140);
    await T();
    const sg2 = await p.evaluate(async () => (await call("graphics", 0)).map(g => [g.kind, g.label, g.bbox.map(Math.round)]));
    ok("segno spostato senza leggere la pagina", sg2.some(x => x[0] === "image" && Math.abs(x[2][1] - (mark[1][1] + 140)) < 6), JSON.stringify(sg2));
  }

  // ---- a page dragged between two documents side by side
  Q("a.pdf"); await p.keyboard.press("Control+o"); await W(2000);
  Q("b.pdf"); await p.keyboard.press("Control+o"); await W(2000);
  await p.click("#docTabs .doc-tab:has-text('a.pdf')"); await W(1500);
  await p.click("[data-cmd=sideBySide]"); await W(1500);
  const opts = await p.$$eval("#side2Tab option", os => os.map(o => [o.value, o.textContent]));
  const bopt = opts.find(o => o[1].trim().startsWith("b.pdf"));
  if (bopt) await p.selectOption("#side2Tab", bopt[0]);
  await W(1500);
  await W(500);
  const src = await (await p.$("#thumbs2 .thumb[data-i=\"2\"]")).boundingBox();
  const dst = await (await p.$("#thumbs .thumb[data-i=\"1\"]")).boundingBox();
  await p.mouse.move(src.x + src.width / 2, src.y + src.height / 2); await p.mouse.down();
  await p.mouse.move(src.x + src.width / 2 + 10, src.y + src.height / 2, { steps: 5 });
  await p.mouse.move(dst.x + dst.width / 2, dst.y + 6, { steps: 20 }); await W(200);
  await p.mouse.up(); await W(2500);
  const texts = await p.evaluate(async () => Promise.all(S.pages.map(async P => (await call("blocks", P.i)).blocks.map(b => b.text).join(" ").trim())));
  ok("pagina trascinata col mouse da un documento all'altro", texts.length === 4 && texts.includes("B3"), JSON.stringify(texts));
  await p.click("[data-cmd=sideClose]"); await W(300);

  // ---- reading aloud with the system voices
  Q("voce.pdf"); await p.keyboard.press("Control+o"); await W(2500);
  const voices = await p.evaluate(async () => {
    for (let i = 0; i < 20 && !speechSynthesis.getVoices().length; i++) await new Promise(r => setTimeout(r, 250));
    return speechSynthesis.getVoices().map(v => v.name + " (" + v.lang + ")");
  });
  console.log("   voci: " + (voices.join(", ") || "nessuna"));
  if (!voices.length) console.log("SKIP lettura ad alta voce: nessuna voce in questo browser");
  else {
    // what the app asks the system to say, and how each sentence ends (spoken, or an error)
    await p.evaluate(() => {
      window.__said = [];
      const sp = speechSynthesis.speak.bind(speechSynthesis);
      speechSynthesis.speak = u => {
        const rec = { text: u.text, voice: u.voice ? u.voice.name : "", end: null };
        window.__said.push(rec);
        u.addEventListener("end", () => { rec.end = "end"; });
        u.addEventListener("error", e => { rec.end = "error:" + e.error; });
        sp(u);
      };
    });
    // at this window width the button is in "Strumenti", not in the bar
    if (await p.isVisible("#btnSpeak")) await p.click("#btnSpeak");
    else { await p.click("#btnHub"); await p.fill("#hubSearch", "alta voce"); await p.keyboard.press("Enter"); }
    let said = [];
    for (let i = 0; i < 60 && !(said.length && said[0].end); i++) { await W(500); said = await p.evaluate(() => window.__said); }
    const first = said[0] || {};
    console.log("   frase: " + JSON.stringify(first));
    ok("lettura ad alta voce: la frase va a una voce di sistema", (first.text || "").includes("Buongiorno") && voices.some(v => v.startsWith(first.voice)), first.voice);
    if (first.end && first.end.startsWith("error:") && process.env.NO_AUDIO) console.log("SKIP pronuncia: questa macchina non ha una scheda audio (" + first.end + ")");
    else ok("lettura ad alta voce: frase pronunciata fino alla fine", first.end === "end", first.end);
    await p.keyboard.press("Escape");
  }
  ok("nessun errore JavaScript", errs.length === 0, errs.join(" | "));
  await b.close();
  console.log(failed ? `${failed} FAIL` : "TUTTO OK");
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
