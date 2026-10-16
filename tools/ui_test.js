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

  // ---- a page dragged between two documents side by side
  Q("a.pdf"); await p.keyboard.press("Control+o"); await W(2000);
  Q("b.pdf"); await p.keyboard.press("Control+o"); await W(2000);
  await p.click("#docTabs .doc-tab[data-i=\"1\"]"); await W(1500);
  await p.click("[data-cmd=sideBySide]"); await W(1500);
  const opts = await p.$$eval("#side2Tab option", os => os.map(o => [o.value, o.textContent]));
  const bopt = opts.find(o => o[1].includes("b.pdf"));
  if (bopt) await p.selectOption("#side2Tab", bopt[0]);
  await W(1500);
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
    await p.click("#btnSpeak");
    let said = [];
    for (let i = 0; i < 60 && !(said.length && said[0].end); i++) { await W(500); said = await p.evaluate(() => window.__said); }
    const first = said[0] || {};
    console.log("   frase: " + JSON.stringify(first));
    ok("lettura ad alta voce: la frase va a una voce di sistema", (first.text || "").includes("Buongiorno") && voices.some(v => v.startsWith(first.voice)), first.voice);
    if (first.end && first.end.startsWith("error:audio")) console.log("SKIP pronuncia: questa macchina non ha un'uscita audio (" + first.end + ")");
    else ok("lettura ad alta voce: frase pronunciata fino alla fine", first.end === "end", first.end);
    await p.keyboard.press("Escape");
  }
  ok("nessun errore JavaScript", errs.length === 0, errs.join(" | "));
  await b.close();
  console.log(failed ? `${failed} FAIL` : "TUTTO OK");
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
