// Computer player statistics, seeded and reproducible.
//   node ai-duel.js [games=6] [turnCap=300] [A=new] [B=new] [jobs=3]
// A and B: new (the AI of ../index.html), rush / rush1 (the scripted raider of rusher.js, careful / reckless) or the
// path of another version of the game, whose "Computer player" section is compiled against this page's engine.
// When A and B differ, every seed is played twice, once from each seat. Per seat it reports the opening (turn of the
// 5th field, first factory, yield >= 30, fields built beyond five, humans at turn 20), losses (units killed right
// after they moved, mobile units killed standing, static units, factories and humans, own units lost in rams) and think time.
const fs = require('fs'), path = require('path');
const { chromium } = require('playwright');
const PAGE = 'file://' + path.resolve(__dirname, '..', 'index.html');
const arg = process.argv.slice(2), games = Number(arg[0] || 6), cap = Number(arg[1] || 300), jobs = Number(arg[4] || 3);
const specs = [arg[2] || 'new', arg[3] || 'new'];
const label = s => /\.html$/.test(s) ? path.basename(s, '.html') : s;
const section = f => { const s = fs.readFileSync(f, 'utf8'); return s.slice(s.indexOf('// ===================== Computer player'), s.indexOf('// ===================== Text')); };
const srcOf = s => /\.html$/.test(s) ? section(path.resolve(s)) : null;
const rusher = fs.readFileSync(path.join(__dirname, 'rusher.js'), 'utf8');

async function play(browser, k, seats) {
  const page = await browser.newPage({ viewport: { width: 1000, height: 700 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push(m.text()); });
  await page.addInitScript(k => { let a = 777 + k * 31; Math.random = () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }, k);
  await page.goto(PAGE);
  await page.addScriptTag({ content: rusher });
  const r = await page.evaluate(async ([seats, srcs, seed, cap]) => {
    const g = window.__globus, L = g.lib, VAL = Object.fromEntries(L.TYPES.map(t => [t, L.U[t].val]));
    const lastAct = seats.map(() => null), seen = new WeakSet();
    const S = seats.map(() => ({ exposed: 0, times: [], late: [], t5: 0, tF: 0, t30: 0, extra: 0, humans20: 0, moved: 0, movedV: 0, standing: 0, stat: 0, lostF: 0, lostH: 0, rams: 0, lostV: 0, kills: 0 }));
    // read the log as it grows (it keeps only the last 400 entries)
    let last = null;
    const fields = seats.map(() => 0);
    const eat = () => {
      const log = g.G.log, i0 = last ? log.lastIndexOf(last) + 1 : 0;
      for (let i = i0; i < log.length; i++) {
        const e = log[i], s = S[e.p];
        if (['build', 'harvest', 'pass', 'move', 'load', 'land', 'place'].includes(e.k)) lastAct[e.p] = e;
        if (e.k === 'build' && e.t === 'field' && fields[e.p] >= 5) s.extra++;
        if (e.k === 'build' && e.t === 'field' && ++fields[e.p] === 5 && !s.t5) s.t5 = e.tn;
        if (e.k === 'build' && e.t === 'factory' && !s.tF) s.tF = e.tn;
        if (['kill', 'crush', 'ram', 'smash', 'bomb'].includes(e.k) && e.vp !== undefined && e.vp !== e.p) {
          const v = S[e.vp], la = lastAct[e.vp];
          s.kills++; v.lostV += VAL[e.v];
          if (e.v === 'field') fields[e.vp]--;
          if (e.v === 'factory') v.lostF++;
          if (e.v === 'human' || e.vc === 'human') v.lostH++;
          if (L.U[e.v].move) { if (la && la.k === 'move' && la.c === e.c) { v.moved++; v.movedV += VAL[e.v]; } else v.standing++; } else v.stat++;
          if (e.k === 'ram') { s.rams++; s.lostV += VAL[e.t]; }
        }
      }
      if (log.length) last = log[log.length - 1];
    };
    seats.forEach((spec, p) => {
      const B = spec === 'new' ? { aiThink: g.aiThink, aiBestShot: g.aiBestShot, aiContext: g.aiContext }
        : /^rush/.test(spec) ? window.__mkRusher(L, spec === 'rush1' ? 1 : 2)
        : new Function(...Object.keys(L), srcs[p] + '\nreturn { aiThink, aiBestShot, aiContext };')(...Object.values(L));
      g.brain(p, { ...B, aiThink(G, q, dbg, shotFree) {
        if (shotFree !== false) { // start of the turn
          eat();
          const c = {}; for (const u of Object.values(G.units)) if (u.owner === q) c[u.type] = (c[u.type] || 0) + 1;
          // did the opponent's last move end where one of our guns is already aimed?
          for (let o = 0; o < seats.length; o++) { const m = lastAct[o]; if (o === q || !m || m.k !== 'move' || seen.has(m)) continue; seen.add(m);
            const u = G.units[G.grid[m.c]]; if (!u || u.owner !== o) continue;
            if (Object.values(G.units).some(e => e.owner === q && L.gunsOf(e).some(gun => L.targetsFrom(G, q, e.cell, gun).includes(m.c)))) S[o].exposed++; }
          const s = S[q], y = (c.field || 0) + 5 * (c.factory || 0);
          if (!s.t30 && y >= 30) s.t30 = G.turn;
          if (G.turn === 20) s.humans20 = c.human || 0;
        }
        const t0 = performance.now(), a = B.aiThink(G, q, dbg, shotFree), dt = performance.now() - t0;
        S[q].times.push(dt); if (G.turn > 100) S[q].late.push(dt);
        return a;
      } });
    });
    g.setInstant(true);
    g.start(seats.map((_, i) => ({ color: i, name: 'P' + i, ai: true })), seed);
    while (g.G.winner < 0 && !g.G.draw && g.G.turn < cap) { await new Promise(r => setTimeout(r, 4)); eat(); }
    eat(); g.setInstant(false);
    const G = g.G, st = a => { const s = a.slice().sort((x, y) => x - y); return [s.length ? s.reduce((x, y) => x + y, 0) / s.length : 0, s[Math.floor(s.length * 0.95)] || 0, s[s.length - 1] || 0]; };
    const fin = G.players.map((P, i) => { const u = Object.values(G.units).filter(x => x.owner === i); return `h${u.filter(x => x.type === 'human' || x.cargo === 'human').length} y${u.reduce((a, x) => a + (L.U[x.type].yield || 0), 0)} $${P.credits} m${u.filter(x => L.U[x.type].move).length}`; });
    return { turn: G.turn, winner: G.winner, draw: G.draw || '', fin, S: S.map(s => ({ ...s, times: st(s.times), late: st(s.late) })) };
  }, [seats, seats.map(srcOf), 1000 + k * 97, cap]);
  r.errors = errors; r.k = k; r.seats = seats.map(label);
  await page.close();
  return r;
}

(async () => {
  const browser = await chromium.launch(), tasks = [];
  for (let k = 0; k < games; k++) { tasks.push([k, specs]); if (specs[0] !== specs[1]) tasks.push([k, [specs[1], specs[0]]]); }
  const out = []; let next = 0;
  await Promise.all(Array.from({ length: jobs }, async () => { while (next < tasks.length) { const [k, seats] = tasks[next++]; const r = await play(browser, k, seats); out.push(r);
    const res = r.winner >= 0 ? r.seats[r.winner] + ' wins' : (r.draw ? 'draw ' + r.draw : 'cap') + ' (' + r.fin.join(' : ') + ')';
    console.log(`seed ${k} ${r.seats.join('-')}: ${res} at turn ${r.turn}; ` + r.S.map((s, i) => `[${r.seats[i]} 5f@${s.t5} F@${s.tF} y30@${s.t30 || '-'} +f${s.extra} h20=${s.humans20} into fire ${s.exposed} lost: moved ${s.moved} standing ${s.standing} static ${s.stat} (F${s.lostF} H${s.lostH}) rams ${s.rams} value ${s.lostV} kills ${s.kills}]`).join(' ') + (r.errors.length ? ' ERR ' + r.errors.slice(0, 2).join(' | ') : '')); } }));
  await browser.close();
  const agg = {};
  for (const r of out) r.seats.forEach((b, i) => {
    const a = agg[b] = agg[b] || { exposed: 0, n: 0, wins: 0, t5: [], tF: [], t30: [], no30: 0, extra: 0, h20: 0, moved: 0, movedV: 0, standing: 0, stat: 0, lostF: 0, lostH: 0, rams: 0, lostV: 0, kills: 0, avg: 0, p95: 0, max: 0, late: 0, lateP95: 0 }, s = r.S[i];
    a.n++; if (r.winner === i) a.wins++;
    if (s.t5) a.t5.push(s.t5); if (s.tF) a.tF.push(s.tF); if (s.t30) a.t30.push(s.t30); else a.no30++;
    for (const key of ['exposed', 'extra', 'moved', 'movedV', 'standing', 'stat', 'lostF', 'lostH', 'rams', 'lostV', 'kills']) a[key] += s[key];
    a.h20 += s.humans20; a.avg += s.times[0]; a.p95 = Math.max(a.p95, s.times[1]); a.max = Math.max(a.max, s.times[2]); a.late += s.late[0]; a.lateP95 = Math.max(a.lateP95, s.late[1]);
  });
  const med = a => { const s = a.slice().sort((x, y) => x - y); return s.length ? s[s.length >> 1] : '-'; }, f1 = x => Math.round(x * 10) / 10;
  for (const [b, a] of Object.entries(agg)) console.log(`${b}: wins ${a.wins}/${a.n}; median turn of 5 fields ${med(a.t5)}, first factory ${med(a.tF)}, yield 30 ${med(a.t30)} (never ${a.no30}); extra fields ${a.extra}; humans at turn 20 ${f1(a.h20 / a.n)}; ` +
    `per game: moves into aimed guns ${f1(a.exposed / a.n)}; lost: after moving ${f1(a.moved / a.n)} (value ${f1(a.movedV / a.n)}), mobile standing ${f1(a.standing / a.n)}, static ${f1(a.stat / a.n)} (factories ${f1(a.lostF / a.n)}, humans ${f1(a.lostH / a.n)}), rams ${f1(a.rams / a.n)}, value ${f1(a.lostV / a.n)}; kills ${f1(a.kills / a.n)}; ` +
    `think avg ${f1(a.avg / a.n)} ms, p95 ${f1(a.p95)}, max ${f1(a.max)}, after turn 100 avg ${f1(a.late / a.n)} p95 ${f1(a.lateP95)}`);
  const fin = out.filter(r => r.winner >= 0);
  console.log(`games ${out.length}, decided ${fin.length}, draws ${out.filter(r => r.draw).length}, at cap ${out.filter(r => r.winner < 0 && !r.draw).length}, avg length of decided ${Math.round(fin.reduce((a, r) => a + r.turn, 0) / Math.max(1, fin.length))}, errors ${out.reduce((a, r) => a + r.errors.length, 0)}`);
})();
