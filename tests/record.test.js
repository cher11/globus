// The game record: play a computer-vs-computer game, save its record, replay it and check the board comes out the same.
// Run: node record.test.js
const path = require('path');
const { chromium } = require('playwright');
const { check, failures } = require('./helpers');
const PAGE = 'file://' + path.resolve(__dirname, '..', 'index.html');

(async () => {
  const browser = await chromium.launch(), errors = [];
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(PAGE); await page.waitForTimeout(300);
  for (const [seed, cap] of [[31, 60], [77, 400]]) {
    await page.evaluate(seed => { __globus.setInstant(true); __globus.start([{ color: 0, name: '', ai: true }, { color: 2, name: '', ai: true }], seed); }, seed);
    await page.waitForFunction(cap => __globus.G.winner >= 0 || __globus.G.draw || __globus.G.turn >= cap, cap, { timeout: 240000 });
    await page.evaluate(() => __globus.G.players.forEach(P => { P.ai = false; }));   // let the turn in progress finish, then stop
    await page.waitForFunction(() => !__globus.UI.busy, null, { timeout: 20000 }); await page.waitForTimeout(300);
    const dl = page.waitForEvent('download');
    await page.click('[data-act="rec-save"]');
    const file = await (await dl).path(), text = require('fs').readFileSync(file, 'utf8');
    check(`seed ${seed}: the record downloads as text (${Math.round(text.length / 1024)} KB)`, text.startsWith('GLOBUS-REC1:'), text.slice(0, 40));
    const r = await page.evaluate(async text => { const r = await __globus.replay(text); return { turns: r.turns, of: r.of, same: r.same, over: __globus.G.winner >= 0 || !!__globus.G.draw }; }, text);
    check(`seed ${seed}: replaying all ${r.of} turns gives the same board`, r.same && r.turns === r.of && r.of > 20, r);
  }
  check('no page errors', errors.length === 0, errors.slice(0, 3));
  console.log(failures() ? `${failures()} FAILED` : 'ALL PASSED');
  await browser.close();
  process.exit(failures() ? 1 : 0);
})();
