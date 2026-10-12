// Scripted lander for ai-duel.js: the plan a person beat the computer with. A normal economy (two humans, five fields,
// factories up to a yield of 20), then a battleship carries a human across the sea and lands it on the shore nearest the
// opponent's humans; the landed human builds tanks right there (and a second human for room), the tanks crush fields,
// factories and humans and burn the free cells around the opponent's humans. Its machinery moves the way a person plays:
// to a cell from which it strikes next turn (a tank's crush path or shot, a ship's shot), out of guns already aimed
// there, and fires after moving when that is the better shot. The empty ship shells the coast and fetches another human
// when the landed ones are gone. Without a sea route it sends tanks from home by land.
// Runs in the page: window.__mkLander(lib) -> brain for __globus.brain.
window.__mkLander = (L) => {
  const { N, NB, U, BIT, TYPES, LAND, SEA, cx, cy, cheb, bfs, unitList, unitAt, isFree, buildTargets, targetsFrom, gunsOf, forLanes, Solver, humansOf, yieldOf, statics, nextDirs, arrival, STRUCT, TANK_RAM } = L;
  const ST = { forward: new Set(), seaRoute: null, landAt: -1 };
  const freeLandN = (g, c) => { let k = 0; for (let d = 0; d < 8; d++) { const n = NB[c * 8 + d]; if (n >= 0 && isFree(g, n) && g.terrain[n] === LAND) k++; } return k; };
  const val = (g, v) => hasH(v) ? (humansOf(g, v.owner) <= 1 ? 1e4 : 50) : v.type === 'factory' ? 30 : v.type === 'aircraft' ? 45 : v.type === 'field' ? 6 : v.type === 'tank' ? 25 : U[v.type].val;
  const hasH = v => v.type === 'human' || v.cargo === 'human';
  const shotFrom = (g, p, u, from) => {
    let b = null;
    gunsOf(u).forEach((gun, gi) => { for (const c of targetsFrom(g, p, from, gun)) { const v = val(g, unitAt(g, c)); if (!b || v > b.v) b = { id: u.id, gi, c, v }; } });
    return b;
  };
  const bestShot = (g, p, units) => { let b = null; for (const u of units) { const s = shotFrom(g, p, u, u.cell); if (s && (!b || s.v > b.v)) b = s; } return b; };
  // end cells of every exact-length move from c over the terrain (paths cross anything; turning only where blocked)
  const ENDS = new WeakMap();
  const endsOf = (g, type, c) => {
    let m = ENDS.get(g.terrain); if (!m) ENDS.set(g.terrain, m = new Map());
    const key = type + c; if (m.has(key)) return m.get(key);
    const pass = statics(g).pass[U[type].cls], K = U[type].move;
    let cur = [[c, -1]];
    for (let k = 0; k < K; k++) {
      const nx = [], seen = new Set();
      for (const [x, d] of cur) for (const nd of nextDirs(pass, x, d)) { const n = NB[x * 8 + nd], key2 = n * 8 + nd; if (!seen.has(key2)) { seen.add(key2); nx.push([n, nd]); } }
      cur = nx;
    }
    const out = [...new Set(cur.map(s => s[0]))].filter(e => e !== c);
    m.set(key, out);
    return out;
  };
  // first move of the shortest chain of moves from `from` to any goal cell (null: none within `depth` moves)
  const route = (g, type, from, goal, depth = 14) => {
    const prev = new Map([[from, -1]]); let cur = [from];
    for (let k = 0; k < depth && cur.length; k++) {
      const nx = [];
      for (const c of cur) for (const e of endsOf(g, type, c)) {
        if (prev.has(e) || g.burnt[e] || (g.grid[e] && e !== from)) continue;
        prev.set(e, c);
        if (goal.has(e)) { let x = e; while (prev.get(x) !== from) x = prev.get(x); return { first: x, moves: k + 1 }; }
        nx.push(e);
      }
      cur = nx;
    }
    return null;
  };
  const pathTo = (g, u, to) => { const pl = new Solver(g, u).plan(u.cell, -1, U[u.type].move, () => 0).get(to); return pl ? Solver.pathOf(pl.state) : null; };

  function aiThink(g, p, dbg, shotFree = true) {
    const P = g.players[p], all = unitList(g), mine = all.filter(u => u.owner === p), foes = all.filter(u => u.owner !== p), n = {};
    TYPES.forEach(t => { n[t] = 0; }); mine.forEach(u => n[u.type]++);
    const shot = shotFree ? bestShot(g, p, mine) : null, v0 = shot ? shot.v : 0;
    const eh = foes.filter(u => u.type === 'human');
    const build = (type, score = () => 0, modes = ['build', 'place']) => {
      let b = -1, bs = -1e9;
      for (const [c, info] of buildTargets(g, p, type)) { if (!modes.includes(info.mode)) continue; const s = score(c, info) + Math.random() * 0.01; if (s > bs) { bs = s; b = c; } }
      return b >= 0 && bs > -1e8 ? { kind: 'build', type, cell: b, shot } : null;
    };
    if (!P.placed) return build('human', c => { let s = Math.random() * 4; for (const e of eh) s += Math.min(cheb(c, e.cell), 30); return s; });
    const st = statics(g), sea = st.pass.sea;
    // guns already aimed at each cell (by target mask): a careful player keeps out of them
    const aimed = new Int32Array(N);
    for (const e of foes) for (const gun of gunsOf(e)) forLanes(e.cell, gun.range, c => { aimed[c] |= gun.mask; });
    // where an enemy could hit a battleship after one move of its own (a ship at sea keeps out of that when it can)
    const reachHit = new Uint8Array(N);
    for (const e of foes) if (U[e.type].move) {
      const guns = gunsOf(e).filter(gun => gun.mask & BIT.battleship);
      if (!guns.length && e.type !== 'battleship') continue;
      for (const c of new Solver(g, e).reach().dests) { reachHit[c] = 1; for (const gun of guns) forLanes(c, gun.range, x => { reachHit[x] = 1; }); }
    }
    // the forward group: humans landed by the ship (and those built next to them)
    const mh = mine.filter(u => u.type === 'human');
    if (ST.landAt >= 0) { const h = unitAt(g, ST.landAt); if (h && h.owner === p && h.type === 'human') ST.forward.add(h.id); ST.landAt = -1; }
    for (const id of [...ST.forward]) if (!g.units[id]) ST.forward.delete(id);
    // (a position set up with a human already by the enemy: that one is the forward group)
    if (!ST.seen) { ST.seen = 1; for (const h of mh) if (eh.some(e => cheb(e.cell, h.cell) <= 8)) ST.forward.add(h.id); }
    for (const h of mh) if (!ST.forward.has(h.id) && mh.some(f => ST.forward.has(f.id) && cheb(f.cell, h.cell) <= 1)) ST.forward.add(h.id);
    const fwd = mh.filter(h => ST.forward.has(h.id)), home = mh.filter(h => !ST.forward.has(h.id));
    // burning a free cell next to an enemy human is worth the more, the fewer free cells it has left
    const burn = new Float32Array(N);
    for (const h of eh) {
      let k = 0; for (let d = 0; d < 8; d++) { const x = NB[h.cell * 8 + d]; if (x >= 0 && isFree(g, x)) k++; }
      for (let d = 0; d < 8; d++) {
        const x = NB[h.cell * 8 + d];
        if (x < 0 || !isFree(g, x) || g.terrain[x] !== LAND) continue;
        burn[x] += 2 + 8 / k;
        for (let e = 0; e < 8; e++) { const y = NB[x * 8 + e]; if (y >= 0 && isFree(g, y) && g.terrain[y] === LAND && !burn[y]) burn[y] = 0.4; }
      }
    }
    // my own humans' free cells are not to be burnt
    for (const h of mh) for (let d = 0; d < 8; d++) { const x = NB[h.cell * 8 + d]; if (x >= 0 && isFree(g, x)) burn[x] -= 1.5; }
    const goalLand = []; for (const h of eh) for (let d = 0; d < 8; d++) { const x = NB[h.cell * 8 + d]; if (x >= 0 && st.pass.land[x]) goalLand.push(x); }
    const dist = bfs(goalLand, st.pass.land);
    const D = c => isFinite(dist[c]) ? Math.min(dist[c], 40) : 40;
    // tank: what it crushes on its way and takes or rams at the end
    const gainT = c => { if (g.burnt[c]) return 0; const v = unitAt(g, c); if (v) return v.owner === p || v.type === 'aircraft' ? 0 : (BIT[v.type] & STRUCT) ? val(g, v) : 0; return burn[c]; };
    const finT = c => { const v = unitAt(g, c); if (v && v.owner !== p) return (BIT[v.type] & STRUCT) ? val(g, v) : (BIT[v.type] & TANK_RAM) ? val(g, v) - 25 : 0; return burn[c] * 0.5; };
    // what a tank standing on c could do next turn: its best crush path, or a shot next door
    const tankNext = c => {
      const sv = new Solver(g, { type: 'tank', owner: p, cell: c });
      let b = 0;
      if (sv.good(c, -1, U.tank.move)) for (const [, pl] of sv.plan(c, -1, U.tank.move, gainT, finT)) if (pl.gain > b) b = pl.gain;
      forLanes(c, 1, x => { const v = unitAt(g, x); if (v && v.owner !== p && v.type !== 'aircraft') b = Math.max(b, val(g, v)); });
      return b;
    };
    const shipNext = c => { let b = 0; forLanes(c, U.battleship.guns[0].range, x => { const v = unitAt(g, x); if (v && v.owner !== p && (U.battleship.guns[0].mask & BIT[v.type])) b = Math.max(b, val(g, v)); }); return b; };
    const moves = [];
    for (const u of mine) if (u.type === 'tank') {
      const sv = new Solver(g, u);
      if (!sv.good(u.cell, -1, U.tank.move)) continue;
      const now = aimed[u.cell] & BIT.tank ? 20 : 0;
      for (const [c, pl] of sv.plan(u.cell, -1, U.tank.move, gainT, finT)) {
        const v = unitAt(g, c), ram = v && v.owner !== p && arrival('tank', v) === 'ram';
        let s = pl.gain + now, after = null;
        if (!ram) {
          after = shotFrom(g, p, u, c);
          if (after && after.v > v0) s += after.v - v0; else after = null;
          s += 0.6 * tankNext(c) + (D(u.cell) - D(c)) * 0.5;
          if (aimed[c] & BIT.tank) s -= 25;
        }
        moves.push({ kind: 'act', id: u.id, path: Solver.pathOf(pl.state), crush: true, after, shot, score: s });
      }
    }
    const bestMove = () => moves.reduce((a, b) => (!a || b.score > a.score ? b : a), null);
    let att = bestMove();
    if (att && att.score > 10) return att;
    // the ship and where it lands: free coast with room, nearest the enemy humans, best out of their guns
    const ship = mine.find(u => u.type === 'battleship');
    const landScore = c => {
      if (!isFree(g, c) || g.terrain[c] !== LAND) return -1e9;
      let d = 99; for (const e of eh) d = Math.min(d, cheb(c, e.cell));
      return -Math.max(0, d - 2) - (aimed[c] & BIT.human ? 6 : 0) + Math.min(freeLandN(g, c), 4) * 0.5 - (freeLandN(g, c) < 2 ? 5 : 0);
    };
    const shore = []; let bestShore = -1e9;
    for (let c = 0; c < N; c++) if (g.terrain[c] === LAND && isFree(g, c)) {
      let wet = false; for (let d = 0; d < 8; d++) { const x = NB[c * 8 + d]; if (x >= 0 && sea[x]) wet = true; }
      if (!wet) continue;
      const s = landScore(c); shore.push([c, s]); if (s > bestShore) bestShore = s;
    }
    const goalSea = new Set();
    for (const [c, s] of shore) if (s >= bestShore - 2.5) for (let d = 0; d < 8; d++) { const x = NB[c * 8 + d]; if (x >= 0 && sea[x] && !(aimed[x] & BIT.battleship)) goalSea.add(x); }
    // a sea route from my coast to that shore (checked once, while I have no ship)
    if (ST.seaRoute === null && !ship && home.length) {
      const starts = new Set(); for (const h of home) for (let d = 0; d < 8; d++) { const x = NB[h.cell * 8 + d]; if (x >= 0 && sea[x] && isFree(g, x)) starts.add(x); }
      if (starts.size) ST.seaRoute = [...starts].some(s => goalSea.has(s) || route(g, 'battleship', s, goalSea, 16));
      else {
        // no coast by my humans: is there coast within reach of my free land?
        const fl = new Uint8Array(N); for (let c = 0; c < N; c++) fl[c] = isFree(g, c) && g.terrain[c] === LAND ? 1 : 0;
        const src = []; for (const h of home) for (let d = 0; d < 8; d++) { const x = NB[h.cell * 8 + d]; if (x >= 0 && fl[x]) src.push(x); }
        const dd = bfs(src, fl); let reach = false;
        for (let c = 0; c < N && !reach; c++) if (dd[c] <= 6) for (let d = 0; d < 8; d++) { const x = NB[c * 8 + d]; if (x >= 0 && sea[x]) { reach = true; break; } }
        if (!reach) ST.seaRoute = false;
      }
    }
    const seaPlan = ST.seaRoute !== false;
    const seaD = bfs([...Array(N).keys()].filter(c => sea[c]), null);
    // the ship's moves: with a human aboard toward the landing shore; empty, to shell the coast or to fetch another human
    let shipAct = null;
    if (ship) {
      const sv = new Solver(g, ship);
      if (sv.good(ship.cell, -1, U.battleship.move)) {
        const plans = sv.plan(ship.cell, -1, U.battleship.move, () => 0), now = aimed[ship.cell] & BIT.battleship ? 40 : 0;
        if (ship.cargo === 'human' || (!fwd.length && seaPlan)) {
          let goal = goalSea;
          if (ship.cargo !== 'human') { goal = new Set(); for (const h of home) for (let d = 0; d < 8; d++) { const x = NB[h.cell * 8 + d]; if (x >= 0 && sea[x]) goal.add(x); } }
          // the move that shortens the voyage most, out of guns already aimed and, if it can, out of reach of a move-and-shoot
          const r0 = goal.has(ship.cell) ? 0 : (route(g, 'battleship', ship.cell, goal) || { moves: 20 }).moves;
          if (r0) for (const [c, pl] of plans) {
            const r = goal.has(c) ? 0 : (route(g, 'battleship', c, goal) || { moves: 20 }).moves;
            if (r >= 20) continue;
            const after = shotFrom(g, p, ship, c), sc = 12 + (r0 - r) * 3 - (aimed[c] & BIT.battleship ? 30 : 0) - (reachHit[c] ? 10 : 0) + now - (reachHit[ship.cell] ? 0 : 0);
            if (!shipAct || sc > shipAct.score) shipAct = { kind: 'act', id: ship.id, path: Solver.pathOf(pl.state), crush: false, after: after && after.v > v0 ? after : null, shot, score: sc };
          }
          // nothing safe that gets closer: wait where it stands if that is safe
          if (shipAct && shipAct.score < 12 && !reachHit[ship.cell] && !(aimed[ship.cell] & BIT.battleship)) shipAct = null;
        }
        if (!shipAct || shipAct.score < 0) for (const [c, pl] of plans) {
          let s = now, after = shotFrom(g, p, ship, c);
          if (after && after.v > v0) s += after.v - v0; else after = null;
          s += 0.5 * shipNext(c) - (aimed[c] & BIT.battleship ? 45 : 0) - Math.min(seaD[c], 5) * 0;
          if (!shipAct || s > shipAct.score) shipAct = { kind: 'act', id: ship.id, path: Solver.pathOf(pl.state), crush: false, after, shot, score: s };
        }
      }
    }
    // landing: the human aboard goes ashore on the best free cell next to the ship
    // (with the price of a tank in hand, so that the landed human builds one at once; until then the ship waits)
    const R = yieldOf(g, p), room = buildTargets(g, p, 'field').size;
    if (ship && ship.cargo === 'human') {
      const b = build('human', c => landScore(c) >= bestShore - 3 ? landScore(c) : -1e9, ['land']);
      if (b && P.credits >= 20) { ST.landAt = b.cell; return b; }
      if (b && !(aimed[ship.cell] & BIT.battleship)) return { kind: 'harvest', shot };
      if (shipAct) return shipAct;
    }
    let b = null;
    // home defence: AA guns once enemy aircraft are about, artillery against an enemy tank near home
    // a gun guards the cells a raider would strike my humans and factories from (their cells and free neighbours) that
    // its lanes cover and no gun of the kind covers yet
    const guard = (c, kind) => {
      const cov = new Uint8Array(N); for (const u of mine) if (u.type === kind) forLanes(u.cell, 5, x => { cov[x] = 1; });
      let s = 0;
      for (const u of mine) if ((u.type === 'factory' || u.type === 'human') && !ST.forward.has(u.id)) {
        const look = x => { if (x < 0 || g.burnt[x] || cov[x]) return; const o = unitAt(g, x); if (o && o.owner === p && !(BIT[o.type] & STRUCT)) return; if (x !== c && (cheb(x, c) > 5 || !(cx(x) === cx(c) || cy(x) === cy(c) || Math.abs(cx(x) - cx(c)) === Math.abs(cy(x) - cy(c))))) return; s += u.type === 'factory' ? 0.5 : 0.3; };
        look(u.cell); for (let d = 0; d < 8; d++) look(NB[u.cell * 8 + d]);
      }
      return s - freeLandN(g, c) * 0.2 - (aimed[c] & BIT[kind] ? 10 : 0);
    };
    if (foes.some(e => e.type === 'aircraft') && n.aa < 3 && P.credits >= 10) b = build('aa', c => { const s = guard(c, 'aa'); return s > 0.5 ? s : -1e9; });
    if (!b && foes.some(e => e.type === 'tank' && home.some(h => cheb(h.cell, e.cell) <= 7)) && n.artillery < 2 && P.credits >= 10) b = build('artillery', c => { const s = guard(c, 'artillery'); return s > 0.5 ? s : -1e9; });
    if (b) return b;
    // economy at home
    if (home.length < 2 || room < 3) b = build('human', c => freeLandN(g, c) + (seaPlan && !ship ? -Math.min(seaD[c], 8) * 0.8 : 0) - (fwd.some(f => cheb(f.cell, c) <= 1) ? 50 : 0));
    if (!b && n.field < 5) b = build('field', c => -freeLandN(g, c) - (seaD[c] <= 1 ? 3 : 0));
    if (!b && R < 15 && P.credits >= 10) b = build('factory', c => -freeLandN(g, c) - (seaD[c] <= 1 ? 3 : 0));
    if (b) return b;
    // the ship: from a coast of mine, the nearest to the landing shore
    if (seaPlan && !ship && R >= 15) {
      if (P.credits >= 50) {
        b = build('battleship', c => { const r = goalSea.has(c) ? { moves: 0 } : route(g, 'battleship', c, goalSea, 16); return r ? -r.moves : -50; });
        if (b) return b;
      } else if (R < 15 && P.credits >= 10) b = build('factory', c => -freeLandN(g, c) - (seaD[c] <= 1 ? 3 : 0));
      // no coast yet: a human toward the sea
      if (!b && !home.some(h => seaD[h.cell] <= 1)) b = build('human', c => -seaD[c] * 2 + freeLandN(g, c) * 0.2 - (fwd.some(f => cheb(f.cell, c) <= 1) ? 50 : 0));
      if (b) return b;
    }
    // an empty ship by my coast takes a human aboard (when none is ashore over there)
    if (ship && !ship.cargo && !fwd.length && seaPlan) { b = build('human', () => 0, ['load']); if (b) return b; }
    if (shipAct && ship && (ship.cargo === 'human' || (!fwd.length && seaPlan)) && shipAct.score > 0) return shipAct;
    // the landed humans build tanks next to the enemy (from home, by land, without a sea route)
    const front = fwd.length ? fwd : seaPlan ? [] : home;
    if (front.length && n.tank < 4 && P.credits >= 20) {
      b = build('tank', c => front.some(f => cheb(f.cell, c) <= 1) ? -D(c) - (aimed[c] & BIT.tank ? 15 : 0) : -1e9);
      if (b) return b;
    }
    if (fwd.length && fwd.length < 2 && n.tank >= 1) {
      b = build('human', c => fwd.some(f => cheb(f.cell, c) <= 1) ? freeLandN(g, c) - (aimed[c] & BIT.human ? 10 : 0) : -1e9);
      if (b) return b;
    }
    if (!b && R < 20 && P.credits >= 10) b = build('factory', c => -freeLandN(g, c) - (seaD[c] <= 1 ? 3 : 0) - (fwd.some(f => cheb(f.cell, c) <= 1) ? 50 : 0));
    if (b) return b;
    att = bestMove();
    const sa = shipAct && shipAct.score > 3 ? shipAct : null;
    if (att && att.score > -3 && (!sa || att.score >= sa.score)) return att;
    if (sa) return sa;
    return { kind: 'harvest', shot };
  }
  return { aiThink, aiContext: (g, p) => ({ p }), aiBestShot: (g, ctx) => bestShot(g, ctx.p, unitList(g).filter(u => u.owner === ctx.p)) };
};
