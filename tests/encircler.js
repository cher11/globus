// Scripted encircler for ai-duel.js: the tank siege that once beat the computer. A small economy (two humans, five
// fields, factories up to a yield of 15), then up to four tanks driven around the opponent's humans, burning the free
// cells next to them (and crushing what stands in the way) until no human has a free cell left and the opponent is
// out for having no options. It keeps its tanks out of guns already aimed at them, and an AA gun by its humans once
// enemy aircraft are about. Runs in the page: window.__mkEncircler(lib) -> brain for __globus.brain.
window.__mkEncircler = (L) => {
  const { N, NB, U, BIT, TYPES, LAND, cheb, bfs, unitList, unitAt, isFree, buildTargets, targetsFrom, gunsOf, forLanes, Solver, humansOf, yieldOf, statics } = L;
  const freeLand = (g, c) => { let k = 0; for (let d = 0; d < 8; d++) { const n = NB[c * 8 + d]; if (n >= 0 && isFree(g, n) && g.terrain[n] === LAND) k++; } return k; };
  const val = (g, v) => v.type === 'human' || v.cargo === 'human' ? (humansOf(g, v.owner) <= 1 ? 1e4 : 50) : v.type === 'factory' ? 30 : v.type === 'aircraft' ? 45 : U[v.type].val;
  const bestShot = (g, p, units) => {
    let b = null;
    for (const u of units) gunsOf(u).forEach((gun, gi) => { for (const c of targetsFrom(g, p, u.cell, gun)) { const v = val(g, unitAt(g, c)); if (!b || v > b.v) b = { id: u.id, gi, c, v }; } });
    return b;
  };
  function aiThink(g, p, dbg, shotFree = true) {
    const P = g.players[p], all = unitList(g), mine = all.filter(u => u.owner === p), n = {};
    TYPES.forEach(t => { n[t] = 0; }); mine.forEach(u => n[u.type]++);
    const shot = shotFree ? bestShot(g, p, mine) : null;
    const eh = all.filter(u => u.owner !== p && u.type === 'human');
    const build = (type, score = () => 0) => {
      let b = -1, bs = -1e9;
      for (const [c, info] of buildTargets(g, p, type)) { if (info.mode !== 'build' && info.mode !== 'place') continue; const s = score(c) + Math.random() * 0.01; if (s > bs) { bs = s; b = c; } }
      return b >= 0 ? { kind: 'build', type, cell: b, shot } : null;
    };
    if (!P.placed) return build('human', c => { let s = Math.random() * 4; for (const e of eh) s += Math.min(cheb(c, e.cell), 30); return s; });
    // what burning a cell is worth: a free cell next to an enemy human, the more so the fewer free cells that human has
    // left; one cell further out a little (the room the enemy would grow into)
    const burn = new Float32Array(N), libs = new Map();
    for (const h of eh) { let k = 0; for (let d = 0; d < 8; d++) { const x = NB[h.cell * 8 + d]; if (x >= 0 && isFree(g, x)) k++; } libs.set(h.id, k); }
    for (const h of eh) for (let d = 0; d < 8; d++) {
      const x = NB[h.cell * 8 + d];
      if (x < 0 || !isFree(g, x) || g.terrain[x] !== LAND) continue;
      burn[x] += 2 + 8 / libs.get(h.id);
      for (let e = 0; e < 8; e++) { const y = NB[x * 8 + e]; if (y >= 0 && isFree(g, y) && g.terrain[y] === LAND && !burn[y]) burn[y] = 0.4; }
    }
    // where enemy guns already aim at tanks
    const aimed = new Uint8Array(N);
    for (const e of all) if (e.owner !== p) for (const gun of gunsOf(e)) if (gun.mask & BIT.tank) forLanes(e.cell, gun.range, c => { aimed[c] = 1; });
    const goal = [];
    for (const h of eh) for (let d = 0; d < 8; d++) { const x = NB[h.cell * 8 + d]; if (x >= 0 && g.terrain[x] === LAND) goal.push(x); }
    const dist = bfs(goal, statics(g).pass.land);
    let att = null;
    for (const u of mine) if (u.type === 'tank') {
      const sv = new Solver(g, u);
      if (!sv.good(u.cell, -1, U.tank.move)) continue;
      const gain = c => { const v = unitAt(g, c); if (g.burnt[c]) return 0; if (v) return v.owner === p || v.type === 'aircraft' ? 0 : v.type === 'human' ? 12 : 3; return burn[c]; };
      const fin = c => { const v = unitAt(g, c); return v && v.owner !== p ? (v.type === 'human' ? 12 : (BIT[v.type] & BIT.tank) ? -10 : 3) : burn[c] * 0.5; };
      const start = burn[u.cell];
      for (const [c, pl] of sv.plan(u.cell, -1, U.tank.move, gain, fin)) {
        let s = pl.gain + start - (isFinite(dist[c]) ? Math.min(dist[c], 40) * 0.5 : 20) + (isFinite(dist[u.cell]) ? Math.min(dist[u.cell], 40) * 0.5 : 20);
        if (aimed[c]) s -= 25;
        if (!att || s > att.score) att = { kind: 'act', id: u.id, path: Solver.pathOf(pl.state), crush: true, after: null, shot, score: s };
      }
    }
    if (att && att.score > 4) return att;
    const R = yieldOf(g, p), room = buildTargets(g, p, 'field').size;
    let b = null;
    if (n.human < 2 || room < 3) b = build('human', c => freeLand(g, c));
    if (!b && n.field < 5) b = build('field', c => -freeLand(g, c));
    if (!b && R < 15 && P.credits >= 10) b = build('factory', c => -freeLand(g, c));
    if (!b && n.aa < 1 && all.some(e => e.owner !== p && e.type === 'aircraft') && P.credits >= 10) b = build('aa', c => -freeLand(g, c));
    // tanks start from the side nearest the enemy humans
    if (!b && n.tank < 4 && P.credits >= 20) b = build('tank', c => -Math.min(dist[c], 60));
    if (b) return b;
    if (att && att.score > -3) return att;
    return { kind: 'harvest', shot };
  }
  return { aiThink, aiContext: (g, p) => ({ p }), aiBestShot: (g, ctx) => bestShot(g, ctx.p, unitList(g).filter(u => u.owner === ctx.p)) };
};
