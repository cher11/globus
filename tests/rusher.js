// Scripted raider for ai-duel.js: the early aircraft raid on factories. Two humans, five fields, factories up to a
// yield of 20 (and humans for room), then an aircraft whenever it can pay, sent at the opponent's factories and humans (it takes them by
// landing on them or shoots them from next door). Level 1 flies into anything; level 2 keeps out of guns already
// aimed at aircraft, as a careful person would. Runs in the page: window.__mkRusher(lib, level) -> brain for __globus.brain.
window.__mkRusher = (L, level = 2) => {
  const { N, NB, U, BIT, TYPES, LAND, cheb, unitList, unitAt, isFree, buildTargets, targetsFrom, gunsOf, forLanes, Solver, humansOf, yieldOf, arrival } = L;
  const free = (g, c) => { let k = 0; for (let d = 0; d < 8; d++) { const n = NB[c * 8 + d]; if (n >= 0 && isFree(g, n) && g.terrain[n] === LAND) k++; } return k; };
  const val = (g, v) => v.type === 'human' || v.cargo === 'human' ? (humansOf(g, v.owner) <= 1 ? 1e4 : 50) : v.type === 'factory' ? 40 : v.type === 'aircraft' ? 45 : v.type === 'aa' ? 30 : U[v.type].val;
  const bestShot = (g, p, units) => {
    let b = null;
    for (const u of units) gunsOf(u).forEach((gun, gi) => { for (const c of targetsFrom(g, p, u.cell, gun)) { const v = val(g, unitAt(g, c)); if (!b || v > b.v) b = { id: u.id, gi, c, v }; } });
    return b;
  };
  const aaCover = (g, p) => { const z = new Uint8Array(N); for (const e of unitList(g)) if (e.owner !== p) for (const gun of gunsOf(e)) if (gun.mask & BIT.aircraft) forLanes(e.cell, gun.range, c => { z[c] = 1; }); return z; };
  function aiThink(g, p, dbg, shotFree = true) {
    const P = g.players[p], all = unitList(g), mine = all.filter(u => u.owner === p), n = {};
    TYPES.forEach(t => { n[t] = 0; }); mine.forEach(u => n[u.type]++);
    const shot = shotFree ? bestShot(g, p, mine) : null;
    const build = (type, score = () => 0) => {
      let b = -1, bs = -1e9;
      for (const [c, info] of buildTargets(g, p, type)) { if (info.mode !== 'build' && info.mode !== 'place') continue; const s = score(c) + Math.random() * 0.01; if (s > bs) { bs = s; b = c; } }
      return b >= 0 ? { kind: 'build', type, cell: b, shot } : null;
    };
    if (!P.placed) { const eh = all.filter(u => u.owner !== p && u.type === 'human'); return build('human', c => { let s = Math.random() * 4; for (const e of eh) s += Math.min(cheb(c, e.cell), 30); return s; }); }
    const cover = aaCover(g, p), prey = all.filter(e => e.owner !== p && (e.type === 'factory' || e.type === 'human'));
    let att = null;
    for (const u of mine) if (u.type === 'aircraft') {
      const sv = new Solver(g, u);
      if (!sv.good(u.cell, -1, U.aircraft.move)) continue;
      for (const [c, pl] of sv.plan(u.cell, -1, U.aircraft.move, () => 0)) {
        const v = unitAt(g, c);
        let s = v && v.owner !== p ? (arrival('aircraft', v) === 'take' ? val(g, v) : val(g, v) - 45) : 0, after = null;
        for (const t of targetsFrom(g, p, c, U.aircraft.guns[0])) { const w = val(g, unitAt(g, t)); if (!after || w > after.v) after = { gi: 0, c: t, v: w }; }
        if (after && (!shot || after.v > shot.v)) s += after.v - (shot ? shot.v : 0); else after = null;
        if (level >= 2 && cover[c]) s -= 45;
        let d = 99; for (const e of prey) d = Math.min(d, cheb(c, e.cell));
        s -= d * 0.3;
        if (!att || s > att.score) att = { kind: 'act', id: u.id, path: Solver.pathOf(pl.state), crush: false, after, shot, score: s };
      }
    }
    if (att && att.score > 5) return att;
    const R = yieldOf(g, p);
    let b = null;
    // humans where there is room to grow, buildings where there is not; a new human once the room runs short
    const room = buildTargets(g, p, 'field').size, grow = c => free(g, c), fill = c => -free(g, c);
    if (n.human < 2 || room < 3) b = build('human', grow);
    if (!b && n.field < 5) b = build('field', fill);
    if (!b && R >= 15 && P.credits >= 40) b = build('aircraft', fill);
    if (!b && R < 20 && P.credits >= 10) b = build('factory', fill);
    if (!b && n.human < 4 && P.credits < 30) b = build('human', grow);
    if (b) return b;
    if (att && att.score > -12) return att;
    return { kind: 'harvest', shot };
  }
  return { aiThink, aiContext: (g, p) => ({ p }), aiBestShot: (g, ctx) => bestShot(g, ctx.p, unitList(g).filter(u => u.owner === ctx.p)) };
};
