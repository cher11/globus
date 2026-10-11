// AI-vs-AI batch: node ai-batch.js <players> <games> <turnCap>
// Each game seeds Math.random, so results are reproducible.
const { open } = require('./helpers');
(async () => {
  const [n = 2, count = 6, cap = 400] = process.argv.slice(2).map(Number);
  const out = [];
  for (let k = 0; k < count; k++) {
    const { browser, page, errors } = await open({ seedRandom: 777 + k * 31 });
    const r = await page.evaluate(async ([n, seed, cap]) => {
      const g = window.__globus; g.setInstant(true);
      g.start(Array.from({ length: n }, (_, i) => ({ color: i, name: '', ai: true })), seed);
      while (g.G.winner < 0 && !g.G.draw && g.G.turn < cap) await new Promise(r => setTimeout(r, 10));
      g.setInstant(false);
      const G = g.G, yieldOf = i => Object.values(G.units).filter(u => u.owner === i).reduce((a, u) => a + (u.type === 'field' ? 1 : u.type === 'factory' ? 5 : 0), 0);
      return { turn: G.turn, winner: G.winner, draw: G.draw || '', humans: G.players.map((p, i) => Object.values(G.units).filter(u => u.owner === i && u.type === 'human').length).join('/'), yield: G.players.map((p, i) => yieldOf(i)).join('/') };
    }, [n, 1000 + k * 97, cap]);
    r.errors = errors.length; out.push(r); console.log(JSON.stringify(r));
    await browser.close();
  }
  const wins = out.filter(r => r.winner >= 0);
  console.log(`players=${n}: wins ${wins.length}/${count}, draws ${out.filter(r => r.draw).length}, avg turns of finished ${Math.round(wins.reduce((a, r) => a + r.turn, 0) / Math.max(1, wins.length))}, errors ${out.reduce((a, r) => a + r.errors, 0)}`);
})();
