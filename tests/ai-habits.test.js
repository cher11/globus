// Habits of the computer player that a person noticed, checked on hand-built boards:
//   a tank does not burn empty cells around its own colony (it crushes only what is worth it, near enemy humans);
//   an aircraft goes for a human of a small colony rather than one of a big colony under an AA gun.
// Run: node ai-habits.test.js
const path = require('path');
const { open, check, failures } = require('./helpers');

(async () => {
  const { browser, page, errors } = await open({ seedRandom: 11 });
  const run = (units, opts = {}) => page.evaluate(([units, opts]) => {
    const G = window.__globus, L = G.lib, W = L.W, at = (x, y) => y * W + x;
    const g = { v: 1, seed: 1, terrain: new Array(L.N).fill(L.LAND), burnt: new Array(L.N).fill(0), grid: new Array(L.N).fill(0), units: {}, nextId: 1,
      players: [0, 1].map(i => ({ name: 'P' + i, color: i, ai: true, credits: (opts.credits || [0, 0])[i], placed: true, alive: true })),
      cur: 0, turn: 80, log: [], winner: -1, lastKill: 1, draw: '' };
    for (const [x0, y0, x1, y1] of opts.sea || []) for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) g.terrain[at(x, y)] = L.SEA;
    for (const [type, owner, x, y] of units) { const id = g.nextId++, c = at(x, y); g.units[id] = { id, type, owner, cell: c, cargo: null }; g.grid[c] = id; }
    const out = [];
    for (let t = 0; t < 6; t++) {
      const dbg = [], a = G.aiThink(g, 0, dbg, true);
      // every tank move the computer considered: does it crush with nothing of the enemy on or next to its path?
      const idle = dbg.filter(x => x.kind === 'act' && g.units[x.id].type === 'tank' && x.crush).filter(x => {
        const u = g.units[x.id], p = L.Solver.pathOf(x.st);
        return L.pathVictims(g, u, p, true).every(c => !g.grid[c] && [0, 1, 2, 3, 4, 5, 6, 7].every(d => { const n = L.NB[c * 8 + d], w = n >= 0 && g.grid[n] && g.units[g.grid[n]]; return !w || w.owner === 0 || w.type !== 'human'; }));
      }).length;
      const tgt = a.kind === 'act' ? a.path[a.path.length - 1] : -1, shot = a.shot ? a.shot.c : -1, hit = a.after ? a.after.c : -1;
      out.push({ kind: a.kind, type: a.kind === 'act' ? g.units[a.id].type : a.type, to: tgt >= 0 ? L.coord(tgt) : a.kind === 'build' ? L.coord(a.cell) : '', shot: shot >= 0 ? L.coord(shot) : hit >= 0 ? L.coord(hit) : '', idle });
    }
    return out;
  }, [units, opts]);

  // ---------- a tank at home, the enemy far away: no crushing on idle moves
  const home = [['human', 0, 10, 10], ['human', 0, 11, 11], ['human', 0, 12, 10], ['field', 0, 9, 11], ['field', 0, 10, 12], ['factory', 0, 13, 11], ['tank', 0, 14, 14],
    ['human', 1, 35, 30], ['human', 1, 36, 31], ['field', 1, 34, 31]];
  const r1 = await run(home);
  check('a tank at home crushes nothing on idle moves', r1.every(x => x.idle === 0), r1);

  // ---------- an aircraft: a lone colonist in the open vs a human of a big colony covered by AA
  // the aircraft at (16,13) reaches (26,13) going east and (26,23) going south-east, both 10 cells
  const big = [[25, 12], [24, 11], [26, 11], [25, 10], [27, 12], [23, 12], [24, 13], [26, 13]].map(([x, y]) => ['human', 1, x, y]);
  const air = [['human', 0, 5, 30], ['human', 0, 6, 31], ['field', 0, 4, 31], ['aircraft', 0, 15, 12],
    ...big, ['aa', 1, 25, 14], ['field', 1, 28, 11],
    ['human', 1, 25, 22], ['field', 1, 26, 22]];          // a fresh colony: one human
  const r2 = await run(air);
  const bigCells = new Set(big.map(([, , x, y]) => `${x + 1}:${y + 1}`));
  check('the aircraft does not go for a human of the big colony under AA', r2.every(x => !(x.type === 'aircraft' && bigCells.has(x.to))), r2);
  check('the aircraft goes for the lone colonist', r2.filter(x => x.type === 'aircraft' && (x.to === '26:23' || x.shot === '26:23')).length >= 4, r2);

  // ---------- the same without the colonist: trading the aircraft for one human of a big colony is not worth it
  const r3 = await run(air.slice(0, -2));
  check('with no small colony in reach, the aircraft still does not trade itself for a big-colony human', r3.every(x => !(x.type === 'aircraft' && bigCells.has(x.to))), r3);

  // ---------- an enemy battleship off the coast: a new gun inside its lanes is shot before it can fire (it fires next turn)
  const coast = [['human', 0, 10, 10], ['human', 0, 11, 11], ['human', 0, 10, 12], ['field', 0, 9, 11], ['factory', 0, 9, 9], ['factory', 0, 8, 12],
    ['battleship', 1, 16, 10], ['human', 1, 35, 30], ['human', 1, 36, 31], ['field', 1, 34, 31]];
  const lanes = new Set();
  for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]]) for (let k = 1; k <= 5; k++) lanes.add(`${16 + dx * k + 1}:${10 + dy * k + 1}`);
  const r4 = await run(coast, { sea: [[14, 0, 25, 25]], credits: [60, 0] });
  check('no gun is built where the enemy battleship can shoot it first', r4.every(x => !(x.kind === 'build' && ['artillery', 'aa', 'tank'].includes(x.type) && lanes.has(x.to))), r4);

  check('no page errors', errors.length === 0, errors.slice(0, 3));
  console.log(failures() ? `${failures()} FAILED` : 'ALL PASSED');
  await browser.close();
  process.exit(failures() ? 1 : 0);
})();
