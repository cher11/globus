// Positions from real games against the computer (records in games/, saved with the game's "Copy game record" button).
// Each case replays a record up to the computer's turn where it went wrong, asks the computer again (a few tries,
// it adds a little noise) and checks the mistake is not repeated.
//   node user-games.test.js [other.html]   the AI section of another version, run against this page's engine
const fs = require('fs'), path = require('path');
const { open, check, failures } = require('./helpers');
const file = path.resolve(process.argv[2] || path.join(__dirname, '..', 'index.html'));
const src = (s => s.slice(s.indexOf('// ===================== Computer player'), s.indexOf('// ===================== Text')))(fs.readFileSync(file, 'utf8'));
const rec = name => fs.readFileSync(path.join(__dirname, 'games', name), 'utf8').trim();

// what went wrong, judged on the position before the turn (g) and the chosen turn (a):
const rules = {
  // an aircraft ends its move where an enemy AA gun can shoot it (to take one human of a big colony)
  noAirIntoAA: `(g, a, L) => !(a.kind === 'act' && g.units[a.id].type === 'aircraft' && Object.values(g.units).some(e => e.owner !== g.units[a.id].owner && L.gunsOf(e).some(gun => (gun.mask & L.BIT.aircraft) && (() => { let hit = false; L.forLanes(e.cell, gun.range, c => { if (c === a.path[a.path.length - 1]) hit = true; }); return hit; })())))`,
  // a tank burns empty cells with no enemy human next to them
  noIdleCrush: `(g, a, L) => !(a.kind === 'act' && g.units[a.id].type === 'tank' && a.crush && L.pathVictims(g, g.units[a.id], a.path, true).every(c => !g.grid[c] && [0, 1, 2, 3, 4, 5, 6, 7].every(d => { const n = L.NB[c * 8 + d], w = n >= 0 && g.grid[n] && g.units[g.grid[n]]; return !w || w.type !== 'human' || w.owner === g.units[a.id].owner; })))`,
  // a new gun goes where an enemy gun already aims at it (it is shot before it can fire)
  noGunUnderFire: `(g, a, L) => !(a.kind === 'build' && ['artillery', 'aa', 'tank'].includes(a.type) && Object.values(g.units).some(e => e.owner !== g.cur && L.gunsOf(e).some(gun => { let hit = false; if (gun.mask & L.BIT[a.type]) L.forLanes(e.cell, gun.range, c => { if (c === a.cell) hit = true; }); return hit; })))`,
};
const cases = [
  { game: '2026-10-13-red-wins.txt', turn: 36, rule: 'noAirIntoAA', what: 'an aircraft takes two humans of the main colony and is shot down by AA' },
  { game: '2026-10-13-red-wins.txt', turn: 51, rule: 'noAirIntoAA', what: 'the same again' },
  { game: '2026-10-13-red-wins.txt', turn: 79, rule: 'noIdleCrush', what: 'a tank burns cells along its own coast' },
  { game: '2026-10-13-red-wins.txt', turn: 81, rule: 'noIdleCrush', what: 'the same again' },
  { game: '2026-10-13-red-wins.txt', turn: 105, rule: 'noGunUnderFire', what: 'artillery built in the lane of a battleship, shot next turn' },
  { game: '2026-10-13-red-wins.txt', turn: 114, rule: 'noGunUnderFire', what: 'the same again' },
  { game: '2026-10-13-red-wins.txt', turn: 138, rule: 'noGunUnderFire', what: 'the same again' },
  { game: '2026-10-13-red-wins.txt', turn: 139, rule: 'noGunUnderFire', what: 'the same again' },
];

(async () => {
  const { browser, page, errors } = await open({ seedRandom: 5 });
  console.log('AI of', path.relative(process.cwd(), file));
  for (const k of cases) {
    const r = await page.evaluate(async ([text, turn, src, rule]) => {
      const X = window.__globus, L = X.lib;
      X.setInstant(true);
      await X.replay(text, (turn - 1) * 2 + 1);      // two players: the computer's turn n is record turn 2n
      const G = X.G, p = G.cur, ok = eval(rule);
      const AI = new Function(...Object.keys(L), src + '\nreturn { aiThink };')(...Object.values(L));
      const res = [];
      for (let i = 0; i < 4; i++) {
        const g = JSON.parse(JSON.stringify(G)), a = AI.aiThink(g, p, null, true);
        if (a.kind === 'act') a.path = a.path.slice(0, L.moveEnd(g, g.units[a.id], a.path) + 1);
        const d = a.kind === 'harvest' ? 'harvest' : a.kind === 'build' ? `build ${a.type} ${L.coord(a.cell)}` : `move ${g.units[a.id].type} ${L.coord(g.units[a.id].cell)}->${L.coord(a.path[a.path.length - 1])}${a.crush ? ' crush' : ''}`;
        res.push({ ok: ok(g, a, L), d });
      }
      return res;
    }, [rec(k.game), k.turn, src, rules[k.rule]]);
    check(`${k.game} turn ${k.turn}: not "${k.what}"`, r.every(x => x.ok), r.map(x => x.d));
  }
  check('no page errors', errors.length === 0, errors.slice(0, 3));
  console.log(failures() ? `${failures()} FAILED` : 'ALL PASSED');
  await browser.close();
  process.exit(failures() ? 1 : 0);
})();
