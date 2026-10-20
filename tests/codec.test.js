// The game's own raw-deflate codec (for browsers without CompressionStream, Safari before 16.4) against the browser's:
// each side must read what the other writes. Run: node codec.test.js
const fs = require('fs'), path = require('path');
const { open, check, failures } = require('./helpers');

(async () => {
  const { browser, page, errors } = await open();
  const rec = fs.readFileSync(path.join(__dirname, 'games', '2026-10-13-red-wins.txt'), 'utf8').trim();
  const r = await page.evaluate(async rec => {
    const C = __globus.codec, enc = s => new TextEncoder().encode(s);
    const native = async (bytes, Stream) => new Uint8Array(await new Response(new Blob([bytes]).stream().pipeThrough(new Stream('deflate-raw'))).arrayBuffer());
    // inputs: game states along a real game, text, random bytes, long runs, nothing
    __globus.setInstant(true);
    const ins = [];
    for (const n of [2, 60, 200, 343]) { await __globus.replay(rec, n); ins.push(['game state at turn ' + n, enc(JSON.stringify({ X: (await C.unpack(await C.pack(__globus.G.log.slice(-5)))), G: __globus.G.grid, u: __globus.G.units }))]); }
    let seed = 1; const rnd = () => (seed = seed * 1103515245 + 12345 >>> 0) / 4294967296;
    ins.push(['random bytes', Uint8Array.from({ length: 5000 }, () => rnd() * 256 | 0)]);
    ins.push(['long runs', enc('a'.repeat(70000) + 'b'.repeat(300) + 'ab'.repeat(20000))]);
    ins.push(['short text', enc('Глобус')]);
    ins.push(['empty', new Uint8Array(0)]);
    const same = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
    const out = [];
    for (const [name, x] of ins) {
      const nat = await native(x, CompressionStream), mine = C.deflateRaw(x);
      out.push({ name, size: x.length, nat: nat.length, mine: mine.length,
        readsNative: same(C.inflateRaw(nat), x), nativeReadsMine: same(await native(mine, DecompressionStream), x), roundTrip: same(C.inflateRaw(mine), x) });
    }
    return out;
  }, rec);
  for (const x of r) check(`${x.name} (${x.size} B → browser ${x.nat} B, own ${x.mine} B): each reads the other`, x.readsNative && x.nativeReadsMine && x.roundTrip, x);
  check('no page errors', errors.length === 0, errors.slice(0, 3));
  console.log(failures() ? `${failures()} FAILED` : 'ALL PASSED');
  await browser.close();
  process.exit(failures() ? 1 : 0);
})();
