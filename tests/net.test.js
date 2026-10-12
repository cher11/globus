// Online play through a Telegram bot, against an in-memory fake of the Bot API. Two separate browser contexts
// play as two people on two devices. Run: node net.test.js
const path = require('path');
const { chromium } = require('playwright');
const { clickCell, check, failures, W } = require('./helpers');
const PAGE = 'file://' + path.resolve(__dirname, '..', 'index.html');

function fakeTelegram() {
  const chat = { id: -1001234, type: 'supergroup', title: 'Друзья' }, msgs = new Map();
  let nextId = 100, pinned = null;
  const api = {
    getMe: () => ({ id: 1, is_bot: true, username: 'globus_test_bot' }),
    getUpdates: () => [{ update_id: 1, my_chat_member: { chat } }],
    sendMessage: q => { const m = { message_id: ++nextId, chat, text: q.get('text') }; msgs.set(m.message_id, m); return m; },
    pinChatMessage: q => { pinned = +q.get('message_id'); return true; },
    editMessageText: q => {
      const m = msgs.get(+q.get('message_id'));
      if (!m) throw new Error('Bad Request: message to edit not found');
      if (m.text === q.get('text')) throw new Error('Bad Request: message is not modified');
      m.text = q.get('text'); return m;
    },
    getChat: () => ({ ...chat, pinned_message: pinned ? msgs.get(pinned) : undefined }),
  };
  return {
    msgs, lines: () => [...msgs.values()].map(m => m.text).filter(x => !x.includes('GLOBUS1:')),
    async route(route) {
      const req = route.request(), method = req.url().split('/').pop(), q = new URLSearchParams(req.postData() || '');
      let body;
      try { body = { ok: true, result: api[method](q) }; } catch (e) { body = { ok: false, description: e.message }; }
      await route.fulfill({ status: 200, contentType: 'application/json', headers: { 'Access-Control-Allow-Origin': '*' }, body: JSON.stringify(body) });
    },
  };
}

(async () => {
  const browser = await chromium.launch(), tg = fakeTelegram(), errors = [];
  const device = async () => {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await ctx.route('https://api.telegram.org/**', r => tg.route(r));
    const page = await ctx.newPage();
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    return page;
  };
  const fast = page => page.evaluate(() => __globus.net.poll(150));
  const waitTurn = (page, p) => page.waitForFunction(p => __globus.G.cur === p && !__globus.UI.busy && __globus.UI.mode === 'play', p, { timeout: 20000 });
  const hash = page => page.evaluate(() => __globus.net.hash());

  // ---------- A creates the game
  const A = await device();
  await A.goto(PAGE); await A.waitForTimeout(300); await fast(A);
  await A.check('#netOn');
  await A.fill('#netToken', 'TEST-TOKEN');
  await A.click('[data-act="net-find"]'); await A.waitForTimeout(300);
  check('chat found from getUpdates', await A.inputValue('#netChat') === '-1001234');
  await A.click('[data-act="start"]');
  await A.waitForFunction(() => __globus.UI.mode === 'play', null, { timeout: 10000 });
  const invite = tg.lines().find(x => x.includes('#tg='));
  check('the bot posted an invite link', !!invite, tg.lines());
  check('both seats are human in an online game', await A.evaluate(() => __globus.G.players.every(p => !p.ai)));

  // ---------- B joins from the link as Blue
  const B = await device();
  await B.goto(PAGE + invite.slice(invite.indexOf('#tg='))); await fast(B);
  await B.waitForSelector('[data-act="net-join"]', { timeout: 10000 });
  await B.click('[data-act="net-join"][data-i="1"]');
  await B.waitForFunction(() => __globus.UI.mode === 'play', null, { timeout: 10000 });
  check('the token is removed from the address bar', !(await B.evaluate(() => location.hash)).includes('tg='));

  // ---------- first turns: placing humans
  await clickCell(A, 0);
  await waitTurn(B, 1);
  check('B sees A\'s first human', await B.evaluate(() => { const u = __globus.G.units[__globus.G.grid[0]]; return !!u && u.owner === 0; }));
  await clickCell(B, 40);
  await waitTurn(A, 0);
  check('after two turns both boards match', await hash(A) === await hash(B));

  // ---------- a move and a shot travel too: give both sides the same units on both devices
  const inject = code => Promise.all([A, B].map(pg => pg.evaluate(code)));
  await inject(() => { const G = __globus.G, W = 41, put = (type, owner, c) => { const id = G.nextId++; G.units[id] = { id, type, owner, cell: c, cargo: null }; G.grid[c] = id; };
    put('tank', 0, 31 * W + 10); put('field', 1, 31 * W + 16); put('artillery', 1, 26 * W + 20); __globus.UI.sel = null; __globus.render(); });
  check('injected state matches', await hash(A) === await hash(B));
  await clickCell(A, 31 * W + 10, 300);
  await clickCell(A, 31 * W + 15, 1500);            // drive 5 east; the field at 16 is then adjacent and can be shot
  const shot = await A.evaluate(() => !!(__globus.UI.sel && __globus.UI.sel.targets && __globus.UI.sel.targets.has(31 * 41 + 16)));
  check('after moving, the tank can fire at the field', shot);
  await clickCell(A, 31 * W + 16, 1500);
  await waitTurn(B, 1);
  check('B replayed the move and the shot', await B.evaluate(() => { const G = __globus.G; return !!G.grid[31 * 41 + 15] && !G.grid[31 * 41 + 16] && G.burnt[31 * 41 + 16] === 1; }));
  check('boards match after a move and a shot', await hash(A) === await hash(B));

  // ---------- reload in the middle of the game, then keep playing
  await B.reload(); await B.waitForTimeout(500); await fast(B);
  await waitTurn(B, 1);
  check('B resumes the online game after a reload', await B.evaluate(() => !!__globus.net.NET && __globus.net.NET.seat === 1));
  await B.click('[data-act="harvest"]'); await B.waitForTimeout(600);
  if (await B.$('[data-act="end"]')) await B.click('[data-act="end"]');   // B's gun could still fire: skip the shot
  await waitTurn(A, 0);
  check('boards match after the reload', await hash(A) === await hash(B));

  // ---------- a broken local state heals from the snapshot
  await B.evaluate(() => { __globus.G.players[1].credits += 77; });
  await A.click('[data-act="harvest"]');
  await waitTurn(B, 1);
  check('a mismatch is repaired from the snapshot', await hash(A) === await hash(B));

  const lines = tg.lines().filter(x => /^Ход \d+ · /.test(x));
  check('a readable line per turn is posted', lines.length === 5, lines);
  check('no page errors', errors.length === 0, errors.slice(0, 3));
  console.log(failures() ? `${failures()} FAILED` : 'ALL PASSED');
  await browser.close();
  process.exit(failures() ? 1 : 0);
})();
