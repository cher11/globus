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
    out.table = [A('tank', 'human'), A('tank', 'tank'), A('tank', 'aircraft'), A('aircraft', 'factory'), A('aircraft', 'battleship'), A('aircraft', 'sub'), A('battleship', 'sub'), A('sub', 'battleship')].join(',');
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
    return out;
  });
  check('arrival table (tank/aircraft/ships)', arr.table === 'take,ram,,take,ram,,take,ram', arr.table);
  check('tank passes under aircraft, crushes the rest, takes the human', arr.crush.planeAlive && arr.crush.fieldGone && arr.crush.tankOnHuman, arr.crush);
  check('ram destroys both, cell burns', arr.ram.both && arr.ram.burnt && arr.ram.kind === 'ram', arr.ram);
  check('tank cannot end on an aircraft', arr.tankOnPlane === false, arr);

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

  // ---------- AI vs AI smoke: a few turns without errors
  await page.evaluate(() => { __globus.setInstant(true); __globus.start([{ color: 0, name: '', ai: true }, { color: 1, name: '', ai: true }], 99); });
  await page.waitForFunction(() => __globus.G.turn >= 40 || __globus.G.winner >= 0, null, { timeout: 120000 });
  check('AI plays 40 turns without errors', errors.length === 0, errors.slice(0, 3));

  console.log(failures() ? `${failures()} FAILED` : 'ALL PASSED', 'errors:', errors.length);
  await browser.close();
  process.exit(failures() ? 1 : 0);
})();
