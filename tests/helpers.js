// Shared helpers: open ../index.html in headless Chromium and talk to window.__globus.
const path = require('path');
const { chromium } = require('playwright');
const PAGE = 'file://' + path.resolve(__dirname, '..', 'index.html');
const W = 41;
async function open({ seedRandom } = {}) {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  if (seedRandom != null) await page.addInitScript(k => { let a = k; Math.random = () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }, seedRandom);
  await page.goto(PAGE);
  await page.waitForTimeout(300);
  return { browser, page, errors };
}
// Click the centre of board cell c (index y*41+x).
async function clickCell(page, c, wait = 700) {
  const box = await page.locator('#board').boundingBox();
  const w = await page.evaluate(() => document.getElementById('board').clientWidth);
  let CS, OX;
  for (let cs = 30; cs >= 12; cs--) { const ox = Math.round(Math.max(14, Math.min(20, cs * 0.9))); if (ox + 41 * cs + 2 === w) { CS = cs; OX = ox; break; } }
  await page.mouse.click(box.x + OX + (c % W + 0.5) * CS, box.y + OX + (Math.floor(c / W) + 0.5) * CS);
  await page.waitForTimeout(wait);
}
let failed = 0;
function check(name, ok, extra) { if (!ok) failed++; console.log((ok ? 'PASS ' : 'FAIL ') + name + (extra !== undefined && !ok ? ' ' + JSON.stringify(extra) : '')); }
const failures = () => failed;
module.exports = { open, clickCell, check, failures, W };
