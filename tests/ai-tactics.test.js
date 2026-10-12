// Tactical positions for the computer player: hand-built boards (land, some sea) where the right turn is clear.
//   node ai-tactics.test.js [other.html]   the AI section of another version of the game, run against this page's engine
// Each position is played a few times (the AI adds a little noise to its scores); the turn is played the way aiTurn plays it,
// and then this page's aiReply judges what the opponent can do in its next turn.
const fs = require('fs'), path = require('path');
const { open, check, failures } = require('./helpers');
const file = path.resolve(process.argv[2] || path.join(__dirname, '..', 'index.html'));
const src = (s => s.slice(s.indexOf('// ===================== Computer player'), s.indexOf('// ===================== Text')))(fs.readFileSync(file, 'utf8'));
const TRIES = 4;
// burnt cells all round the given ones but `keep`: humans with no room to build
const around = (cells, keep = []) => { const out = [], k = c => c[0] + ':' + c[1], skip = new Set([...cells, ...keep].map(k));
  for (const [x, y] of cells) for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) { const c = [x + dx, y + dy]; if (!skip.has(k(c))) { skip.add(k(c)); out.push(c); } }
  return out; };

(async () => {
  const { browser, page, errors } = await open({ seedRandom: 7 });
  console.log('AI of', path.relative(process.cwd(), file));
  const run = (name, units, opts) => page.evaluate(([src, name, units, opts, TRIES]) => {
    const G = window.__globus, L = G.lib, W = L.W;
    const AI = new Function(...Object.keys(L), src + '\nreturn { aiThink };')(...Object.values(L));
    const at = (x, y) => y * W + x;
    const mk = () => {
      const g = { v: 1, seed: 1, terrain: new Array(L.N).fill(L.LAND), burnt: new Array(L.N).fill(0), grid: new Array(L.N).fill(0), units: {}, nextId: 1,
        players: [0, 1].map(i => ({ name: 'P' + i, color: i, ai: true, credits: (opts.credits || [0, 0])[i], placed: true, alive: true })),
        cur: 0, turn: opts.turn || 60, log: [], winner: -1, lastKill: 1, draw: '' };
      for (const [x0, y0, x1, y1] of opts.sea || []) for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) g.terrain[at(x, y)] = L.SEA;
      for (const [x, y] of opts.burnt || []) g.burnt[at(x, y)] = 1;
      for (const [type, owner, x, y, cargo] of units) { const id = g.nextId++, c = at(x, y); g.units[id] = { id, type, owner, cell: c, cargo: cargo || null }; g.grid[c] = id; }
      return g;
    };
    // one turn of player 0, the way aiTurn plays it
    const play = g => {
      const act = a => {
        if (a.kind === 'harvest') g.players[0].credits += L.yieldOf(g, 0);
        else if (a.kind === 'build') { const info = L.buildTargets(g, 0, a.type).get(a.cell); if (info) L.applyBuild(g, 0, a.type, a.cell, info); }
        else if (a.path && g.units[a.id]) { const u = g.units[a.id], path = a.path.slice(0, L.moveEnd(g, u, a.path) + 1); L.applyMove(g, a.id, path, a.crush); }
      };
      const shoot = (id, gi, c) => { const u = g.units[id], gun = u && L.gunsOf(u)[gi]; if (gun && L.targetsFrom(g, 0, u.cell, gun).includes(c)) { L.killAt(g, c); return true; } return false; };
      let a = AI.aiThink(g, 0, null, true), d = describe(g, a);
      if (a.kind === 'act' && a.after && (!a.shot || a.after.v > a.shot.v)) { act(a); shoot(a.id, a.after.gi, a.after.c); d += ' +shot ' + L.coord(a.after.c); }
      else if (a.shot) { shoot(a.shot.id, a.shot.gi, a.shot.c); d = 'shot ' + L.coord(a.shot.c) + ', '; a = AI.aiThink(g, 0, null, false); d += describe(g, a); act(a); }
      else act(a);
      for (const u of Object.values(g.units)) { delete u.fresh; delete u.aaFresh; }
      return d;
    };
    const describe = (g, a) => a.kind === 'harvest' ? 'harvest' : a.kind === 'build' ? `build ${a.type} ${L.coord(a.cell)}` : `move ${g.units[a.id].type} ${L.coord(g.units[a.id].cell)}->${L.coord(a.path[a.path.length - 1])}`;
    const res = [];
    for (let t = 0; t < TRIES; t++) {
      const g = mk(), ids = Object.keys(g.units), turn = play(g), hc = g.players.map((_, i) => L.humansOf(g, i));
      const rep = G.aiReply(g, 1, 0, hc), kept = id => !!g.units[id];
      const u = id => g.units[id] && { type: g.units[id].type, cell: L.coord(g.units[id].cell) };
      res.push({ turn, rep: { net: Math.round(rep.net), loss: Math.round(rep.loss), line: rep.line && { ...rep.line, to: rep.line.to !== undefined ? L.coord(rep.line.to) : undefined, dead: (rep.line.dead || []).map(L.coord), shot: rep.line.shot >= 0 ? L.coord(rep.line.shot) : undefined } },
        humans: hc[0], alive: ids.map(id => [id, u(id)]).filter(x => x[1]).reduce((o, [id, v]) => (o[id] = v, o), {}) });
    }
    return res;
  }, [src, name, units, opts, TRIES]);
  const test = async (name, units, opts, ok) => {
    const res = await run(name, units, opts), bad = res.filter(r => !ok(r));
    check(name, !bad.length, bad.length ? { turns: res.map(r => r.turn), first: bad[0].rep } : undefined);
    if (!bad.length) console.log('   ', [...new Set(res.map(r => r.turn))].join(' | '));
  };

  // 1. An enemy aircraft can land on my human and then shoot whatever stands next to it: taking the enemy factory right
  //    there with my aircraft (a small gain) loses the aircraft as well. My humans have no room left, so it is a move or nothing.
  await test('aircraft does not take a factory where the enemy can take a human and shoot it in one turn', [
    ['human', 0, 30, 22], ['human', 0, 27, 22], ['human', 0, 5, 30], ['aircraft', 0, 21, 22],
    ['human', 1, 35, 3], ['human', 1, 36, 3], ['factory', 1, 31, 22], ['aircraft', 1, 40, 22],
  ], { burnt: around([[30, 22], [27, 22], [5, 30]], [[31, 22]]) }, r => r.rep.loss <= 75);

  // 2. The same raid threatens my battleship lying next to the human (it cannot fire at aircraft): it must sail off
  //    rather than build a field
  await test('a threatened battleship steps out of a move-and-shoot zone', [
    ['human', 0, 30, 22], ['human', 0, 27, 22], ['battleship', 0, 31, 22],
    ['human', 1, 35, 3], ['human', 1, 36, 3], ['aircraft', 1, 20, 12],
  ], { sea: [[31, 15, 40, 30]] }, r => r.rep.loss <= 90);

  // 3. My tank stands in the way of an enemy tank that would crush both my humans: it must not drive off for a factory
  await test('a tank that blocks the way to the last humans stays (or rams the attacker)', [
    ['human', 0, 14, 15], ['human', 0, 15, 15], ['tank', 0, 12, 15],
    ['human', 1, 35, 3], ['human', 1, 36, 3], ['factory', 1, 12, 20], ['tank', 1, 10, 15],
  ], {}, r => r.humans === 2 && r.rep.loss < 100);

  // 4. An enemy tank will take my last human: I cannot shoot it, but a gun built on its path stops it (a tank rams machinery)
  await test('the last human is saved: the attacking tank is blocked', [
    ['human', 0, 20, 15], ['field', 0, 21, 14], ['field', 0, 21, 16],
    ['human', 1, 35, 3], ['human', 1, 36, 3], ['tank', 1, 15, 15],
  ], { credits: [10, 0] }, r => r.rep.loss < 40);

  // 5. My artillery can shoot the enemy battleship (worth more, aimed at one of my humans) or the enemy tank that would
  //    crush two of them
  await test('the shot goes to the unit that would do the most harm', [
    ['human', 0, 14, 15], ['human', 0, 15, 15], ['human', 0, 7, 17], ['artillery', 0, 10, 18],
    ['human', 1, 35, 3], ['human', 1, 36, 3], ['tank', 1, 10, 15], ['battleship', 1, 7, 21],
  ], { sea: [[2, 20, 8, 26]] }, r => r.rep.loss < 100);

  // 6. An enemy aircraft can land on my last human; my aircraft can fly next to it and shoot it, or take a factory far away
  await test('the raider of the last human is shot down (move, then shoot)', [
    ['human', 0, 20, 15], ['field', 0, 19, 16], ['field', 0, 21, 16], ['aircraft', 0, 21, 5],
    ['human', 1, 35, 30], ['human', 1, 36, 30], ['factory', 1, 11, 5], ['aircraft', 1, 30, 15],
  ], {}, r => r.rep.loss < 100);

  check('no page errors', errors.length === 0, errors.slice(0, 3));
  console.log(failures() ? `${failures()} FAILED` : 'ALL PASSED');
  await browser.close();
  process.exit(failures() ? 1 : 0);
})();
