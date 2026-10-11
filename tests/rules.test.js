// Rules and interface checks. Run: npm install && npm test  (needs Chromium for Playwright)
const { open, clickCell, check, failures, W } = require('./helpers');
(async () => {
  const { browser, page, errors } = await open();
  const start = () => page.evaluate(() => __globus.start([{ color: 0, name: 'A', ai: false }, { color: 1, name: 'B', ai: false }], 4242));
  const put = (type, owner, c) => page.evaluate(([type, owner, c]) => {
    const G = __globus.G, id = G.nextId++;
    G.units[id] = { id, type, owner, cell: c, cargo: null }; G.grid[c] = id; G.players[owner].placed = true;
    __globus.UI.sel = null; __globus.render(); return id;
  }, [type, owner, c]);

  // ---------- movement: a unit may turn only when the next cell ahead is blocked
  await start();
  await put('human', 0, 33 * W); await put('human', 1, 33 * W + 40);
  const mv = await page.evaluate(() => {
    const g = __globus, G = g.G, W = 41, t = G.units[G.grid[0 * W + 10]] || null;
    const id = G.nextId++; G.units[id] = { id, type: 'tank', owner: 0, cell: 10, cargo: null }; G.grid[10] = id;
    const sv = new g.Solver(G, G.units[id]);
    let bad = 0;
    for (const [, pl] of sv.plan(10, -1, 5, () => 0)) {
      const path = [10, ...g.Solver.pathOf(pl.state)];
      for (let i = 1; i < path.length - 1; i++) {
        const d1 = path[i] - path[i - 1], d2 = path[i + 1] - path[i];
        if (d1 === d2) continue;
        const ax = path[i] % W + (d1 % W === 1 || d1 % W === -40 ? 1 : d1 % W === -1 || d1 % W === 40 ? -1 : 0), ahead = path[i] + d1;
        if (ahead >= 0 && ahead < W * 34 && ax >= 0 && ax < W && G.terrain[ahead] === 0) bad++;
      }
    }
    delete G.units[id]; G.grid[10] = 0;
    return { along: sv.dirs(12, 0), corner: sv.dirs(40, 0).length, bad };
  });
  check('moving along the edge keeps its direction', JSON.stringify(mv.along) === '[0]', mv);
  check('bounce is allowed when blocked ahead', mv.corner > 1, mv);
  check('no illegal turns in planned paths', mv.bad === 0, mv);

  // ---------- arrivals and crushing (engine level)
  const arr = await page.evaluate(() => {
    const g = __globus, G = g.G, W = 41;
    const put = (type, owner, c) => { const id = G.nextId++; G.units[id] = { id, type, owner, cell: c, cargo: null }; G.grid[c] = id; return G.units[id]; };
    const clear = c => { const u = G.units[G.grid[c]]; if (u) delete G.units[u.id]; G.grid[c] = 0; G.burnt[c] = 0; };
    const out = {};
    const A = (m, v) => g.arrival(m, { type: v });
    out.table = [A('tank', 'human'), A('tank', 'tank'), A('tank', 'artillery'), A('tank', 'aa'), A('tank', 'aircraft'), A('aircraft', 'factory'), A('aircraft', 'aircraft'), A('aircraft', 'tank'), A('aircraft', 'battleship'),
      A('aircraft', 'sub'), A('battleship', 'battleship'), A('battleship', 'sub'), A('sub', 'sub'), A('sub', 'battleship')].join(',');
    // tank drives along row 2 from x=5 to x=10: enemy aircraft at x=7 survives, enemy field at x=8 is crushed, human at x=10 is taken
    const tank = put('tank', 0, 2 * W + 5); const plane = put('aircraft', 1, 2 * W + 7); put('field', 1, 2 * W + 8); put('human', 1, 2 * W + 10);
    const ev = g.applyMove(G, tank.id, [2 * W + 6, 2 * W + 7, 2 * W + 8, 2 * W + 9, 2 * W + 10], true);
    out.crush = { planeAlive: !!G.units[plane.id] && !G.burnt[2 * W + 7], fieldGone: !G.grid[2 * W + 8] && G.burnt[2 * W + 8] === 1,
      tankOnHuman: G.units[tank.id] && G.units[tank.id].cell === 2 * W + 10 && !G.burnt[2 * W + 10], kinds: ev.map(e => e.kind).join(',') };
    // tank rams an enemy tank: both destroyed, cell burnt
    const t2 = put('tank', 0, 4 * W + 5); const et = put('tank', 1, 4 * W + 10);
    const ev2 = g.applyMove(G, t2.id, [4 * W + 6, 4 * W + 7, 4 * W + 8, 4 * W + 9, 4 * W + 10], false);
    out.ram = { both: !G.units[t2.id] && !G.units[et.id], burnt: G.burnt[4 * W + 10] === 1, kind: ev2[ev2.length - 1].kind };
    // solver: tank cannot end on an aircraft; aircraft cannot end on a submarine
    const t3 = put('tank', 0, 5 * W + 0); const pl2 = put('aircraft', 1, 5 * W + 5);
    out.tankOnPlane = new g.Solver(G, t3).dest(5 * W + 5);
    out.tankOnTankOk = new g.Solver(G, t3).dest(2 * W + 10) === false; // own tank there
    // tank runs into enemy artillery mid-path at x=7: a ram there, the move ends; the field at x=9 beyond is untouched
    const t4 = put('tank', 0, 6 * W + 5), gun = put('artillery', 1, 6 * W + 7), fld = put('field', 1, 6 * W + 9);
    const sv4 = new g.Solver(G, t4), pl = sv4.plan(t4.cell, -1, 5, () => 0).get(6 * W + 7);
    out.ramPlan = { end: !!pl, len: pl ? g.Solver.pathOf(pl.state).length : 0, beyond: sv4.reach().inter.has(6 * W + 7) };
    const ev4 = g.applyMove(G, t4.id, [6 * W + 6, 6 * W + 7, 6 * W + 8, 6 * W + 9, 6 * W + 10], false);
    out.midRam = { both: !G.units[t4.id] && !G.units[gun.id], burnt: G.burnt[6 * W + 7] === 1, kind: ev4.map(e => e.kind).join(','),
      after: !G.burnt[6 * W + 8] && !G.burnt[6 * W + 10] && !!G.units[fld.id] && !G.grid[6 * W + 10] };
    // aircraft cannot end on an enemy tank
    const pl3 = put('aircraft', 0, 7 * W + 0); put('tank', 1, 7 * W + 10);
    out.planeOnTank = new g.Solver(G, pl3).dest(7 * W + 10);
    // at sea: a battleship cannot end on an enemy submarine, and passing over it leaves it alive
    let row = -1, x0 = -1;
    for (let y = 0; y < 34 && row < 0; y++) for (let x = 0; x + 6 < W; x++) { let ok = true; for (let i = 0; i <= 6; i++) if (G.terrain[y * W + x + i] !== 1) ok = false; if (ok) { row = y; x0 = x; break; } }
    const bs = put('battleship', 0, row * W + x0), sub = put('sub', 1, row * W + x0 + 2);
    out.shipOnSub = new g.Solver(G, bs).dest(sub.cell);
    const ev5 = g.applyMove(G, bs.id, [1, 2, 3, 4, 5].map(i => row * W + x0 + i), false);
    out.overSub = { alive: !!G.units[sub.id] && G.grid[sub.cell] === sub.id, unburnt: !G.burnt[sub.cell], events: ev5.length, shipAt: G.units[bs.id].cell === row * W + x0 + 5 };
    return out;
  });
  check('arrival table (tank/aircraft/ships)', arr.table === 'take,ram,ram,ram,,take,ram,,,,ram,,ram,', arr.table);
  check('tank passes under aircraft, crushes the field, takes the human', arr.crush.planeAlive && arr.crush.fieldGone && arr.crush.tankOnHuman, arr.crush);
  check('ram destroys both, cell burns', arr.ram.both && arr.ram.burnt && arr.ram.kind === 'ram', arr.ram);
  check('tank cannot end on an aircraft', arr.tankOnPlane === false, arr);
  check('tank rams enemy machinery mid-path: both gone, cell burnt, move ends there', arr.midRam.both && arr.midRam.burnt && arr.midRam.kind === 'ram' && arr.midRam.after, arr.midRam);
  check('solver ends a tank move at the ram', arr.ramPlan.end && arr.ramPlan.len === 2 && !arr.ramPlan.beyond, arr.ramPlan);
  check('aircraft cannot end on a tank', arr.planeOnTank === false, arr);
  check('battleship cannot end on a submarine', arr.shipOnSub === false, arr);
  check('battleship passing over a submarine leaves it alive', arr.overSub.alive && arr.overSub.unburnt && arr.overSub.events === 0 && arr.overSub.shipAt, arr.overSub);

  // ---------- only AA guns and aircraft can shoot aircraft
  const air = await page.evaluate(() => {
    const g = __globus, G = g.G, L = g.lib, W = 41;
    const put = (type, owner, c, cargo = null) => { const id = G.nextId++; G.units[id] = { id, type, owner, cell: c, cargo }; G.grid[c] = id; return G.units[id]; };
    const s0 = G.terrain.findIndex((t, c) => t === 1 && G.terrain[c + 2] === 1 && !G.grid[c] && !G.grid[c + 2] && Math.floor((c + 2) / W) === Math.floor(c / W));
    const ship = put('battleship', 0, s0), plane = put('aircraft', 1, s0 + 2);
    const tank = put('tank', 0, 30 * W + 20), plane2 = put('aircraft', 1, 30 * W + 21);
    const hits = (u, c) => L.gunsOf(u).some(gun => L.targetsFrom(G, u.owner, u.cell, gun).includes(c));
    const out = { ship: hits(ship, plane.cell), tank: hits(tank, plane2.cell) };
    ship.cargo = 'aa'; out.shipAA = hits(ship, plane.cell);
    out.planeVsPlane = hits(plane2, tank.cell) === true && hits(put('aircraft', 0, 31 * W + 21), plane2.cell);
    for (const u of [ship, plane, tank, plane2]) { G.grid[u.cell] = 0; delete G.units[u.id]; }
    const p3 = G.units[G.grid[31 * W + 21]]; G.grid[p3.cell] = 0; delete G.units[p3.id];
    return out;
  });
  check('battleship and tank cannot shoot aircraft', air.ship === false && air.tank === false, air);
  check('battleship with an AA gun and aircraft can', air.shipAA === true && air.planeVsPlane === true, air);

  // ---------- one action + one shot per turn (through the interface)
  await start();
  await clickCell(page, 0); await clickCell(page, 40);            // both place their first human
  await put('artillery', 0, 5 * W); await put('field', 1, 9 * W);  // field 4 cells south of the gun
  await clickCell(page, 5 * W);
  await clickCell(page, 9 * W, 900);                               // fire
  let st = await page.evaluate(() => ({ cur: __globus.G.cur, shot: __globus.UI.turn.shot, act: __globus.UI.turn.act, field: !!__globus.G.grid[9 * 41] }));
  check('shot does not end the turn', st.cur === 0 && st.shot && !st.act && !st.field, st);
  await page.click('[data-act="harvest"]'); await page.waitForTimeout(900);
  st = await page.evaluate(() => __globus.G.cur);
  check('action after the shot ends the turn', st === 1, st);
  await page.click('[data-act="harvest"]'); await page.waitForTimeout(900);   // B has nothing to fire with: turn ends
  check('turn ends when no shot is possible', await page.evaluate(() => __globus.G.cur) === 0);
  await put('field', 1, 10 * W);
  await page.click('[data-act="build"][data-type="field"]'); await page.waitForTimeout(100);
  await clickCell(page, 1 * W + 1, 900);
  st = await page.evaluate(() => ({ cur: __globus.G.cur, act: __globus.UI.turn.act, endBtn: !!document.querySelector('[data-act="end"]') }));
  check('after the action a shot is still offered', st.cur === 0 && st.act && st.endBtn, st);
  await page.click('[data-act="end"]'); await page.waitForTimeout(400);
  check('end turn skips the shot', await page.evaluate(() => __globus.G.cur) === 1);

  // ---------- boxed in with nothing to do but harvest = out
  await start();
  await clickCell(page, 0); await clickCell(page, 40);   // A at (0,0), B at (40,0)
  await page.evaluate(() => { const G = __globus.G, W = 41; for (const c of [39, 39 + W, 40 + W]) G.burnt[c] = 1; __globus.render(); });
  await page.click('[data-act="harvest"]'); await page.waitForTimeout(900);
  const boxed = await page.evaluate(() => ({ winner: __globus.G.winner, out: !__globus.G.players[1].alive, log: __globus.G.log.slice(-3).map(e => e.k) }));
  check('a boxed-in player with nothing to do but harvest is out', boxed.out && boxed.winner === 0 && boxed.log.includes('stuck'), boxed);

  // ---------- AI vs AI smoke: a few turns without errors
  await page.evaluate(() => { __globus.setInstant(true); __globus.start([{ color: 0, name: '', ai: true }, { color: 1, name: '', ai: true }], 99); });
  await page.waitForFunction(() => __globus.G.turn >= 40 || __globus.G.winner >= 0, null, { timeout: 120000 });
  check('AI plays 40 turns without errors', errors.length === 0, errors.slice(0, 3));

  console.log(failures() ? `${failures()} FAILED` : 'ALL PASSED', 'errors:', errors.length);
  await browser.close();
  process.exit(failures() ? 1 : 0);
})();
