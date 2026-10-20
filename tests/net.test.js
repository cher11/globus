// Online play through a Telegram bot, against an in-memory fake of the Bot API. Two separate browser contexts
// play as two people on two devices. Run: node net.test.js
const path = require('path');
const { chromium } = require('playwright');
const { clickCell, check, failures, W } = require('./helpers');
const PAGE = 'file://' + path.resolve(__dirname, '..', 'index.html');

function fakeTelegram(chat = { id: -1001234, type: 'supergroup', title: 'Друзья' }, more = []) {
  const msgs = new Map();
  let nextId = 100, pinned = null;
  const api = {
    getMe: () => ({ id: 1, is_bot: true, username: 'globus_test_bot' }),
    getUpdates: () => [chat.type === 'private' ? { update_id: 1, message: { chat, text: '/start' } } : { update_id: 1, my_chat_member: { chat } }, ...more],
    sendMessage: q => { const m = { message_id: ++nextId, chat, text: q.get('text'), quiet: q.get('disable_notification') === 'true' }; msgs.set(m.message_id, m); return m; },
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

// A fake of the few GitHub REST calls the game makes: create, read and update an issue, comment on it
function fakeGitHub(token, repo) {
  const issues = new Map(), comments = [];
  let next = 1;
  return {
    issues, comments,
    async route(route) {
      const req = route.request(), url = new URL(req.url()), m = /^\/repos\/([^/]+\/[^/]+)\/issues(?:\/(\d+))?(\/comments)?$/.exec(url.pathname);
      const send = (status, body) => route.fulfill({ status, contentType: 'application/json', headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'Authorization, Content-Type, Accept', 'Access-Control-Allow-Methods': 'GET, POST, PATCH' }, body: JSON.stringify(body) });
      if (req.method() === 'OPTIONS') return send(204, {});
      if (req.headers().authorization !== 'Bearer ' + token) return send(401, { message: 'Bad credentials' });
      if (!m || m[1] !== repo) return send(404, { message: 'Not Found' });
      const body = req.postData() ? JSON.parse(req.postData()) : {};
      if (!m[2] && req.method() === 'POST') { const is = { number: next++, title: body.title, body: body.body, state: 'open' }; issues.set(is.number, is); return send(201, is); }
      const is = issues.get(+m[2]);
      if (!is) return send(404, { message: 'Not Found' });
      if (m[3] && req.method() === 'POST') { comments.push({ issue: is.number, body: body.body }); return send(201, { id: comments.length }); }
      if (req.method() === 'PATCH') Object.assign(is, body);
      return send(200, is);
    },
  };
}

(async () => {
  const browser = await chromium.launch(), tg = fakeTelegram(), errors = [];
  const device = async (fake = tg, ghFake = null) => {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await ctx.route('https://api.telegram.org/**', r => fake.route(r));
    if (ghFake) { await ctx.grantPermissions(['clipboard-read', 'clipboard-write']); await ctx.route('https://api.github.com/**', r => ghFake.route(r)); }
    const page = await ctx.newPage();
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push(m.text()); });   // a wrong token's 401 is expected
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

  // ---------- one on one without a group: the host's private chat with the bot
  const old = { id: -77, type: 'group', title: 'Глобус 2' }, gone = { id: -88, type: 'group', title: 'Старая' };
  const pm = fakeTelegram({ id: 555, type: 'private', first_name: 'Илья' }, [
    { update_id: 2, my_chat_member: { chat: old, new_chat_member: { status: 'member' } } },
    { update_id: 3, message: { chat: old, migrate_to_chat_id: -100777 } },
    { update_id: 4, my_chat_member: { chat: { id: -100777, type: 'supergroup', title: 'Глобус 2' }, new_chat_member: { status: 'administrator' } } },
    { update_id: 5, my_chat_member: { chat: gone, new_chat_member: { status: 'member' } } },
    { update_id: 6, my_chat_member: { chat: gone, new_chat_member: { status: 'left' } } },
  ]);
  const A2 = await device(pm);
  await A2.goto(PAGE); await A2.waitForTimeout(300); await fast(A2);
  await A2.check('#netOn');
  await A2.fill('#netToken', 'TEST-TOKEN');
  await A2.click('[data-act="net-find"]'); await A2.waitForTimeout(300);
  const found = await A2.$$eval('[data-act="net-chat"]', bs => bs.map(b => b.dataset.v));
  check('chats found: private and the upgraded group, not the old id or a chat the bot left', found.join() === '555,-100777', found);
  await A2.click('[data-act="net-chat"][data-v="555"]');
  check('a private chat with the bot is picked', await A2.inputValue('#netChat') === '555');
  await A2.click('[data-act="start"]');
  await A2.waitForFunction(() => __globus.UI.mode === 'play', null, { timeout: 10000 });
  const invite2 = pm.lines().find(x => x.includes('#tg='));
  check('the invite asks to forward the link', !!invite2 && invite2.includes('Перешлите'), pm.lines());
  const B2 = await device(pm);
  await B2.goto(PAGE + invite2.slice(invite2.indexOf('#tg='))); await fast(B2);
  await B2.waitForSelector('[data-act="net-join"]', { timeout: 10000 });
  await B2.click('[data-act="net-join"][data-i="1"]');
  await B2.waitForFunction(() => __globus.UI.mode === 'play', null, { timeout: 10000 });
  await clickCell(A2, 0);
  await waitTurn(B2, 1);
  check('the tab title tells B it is their turn', (await B2.title()).startsWith('▶'));
  await clickCell(B2, 40);
  await waitTurn(A2, 0);
  check('boards match in a private-chat game', await hash(A2) === await hash(B2));
  const turns = [...pm.msgs.values()].filter(m => /^Ход \d+ · /.test(m.text));
  check('the host is not pinged for their own move, but is for the opponent\'s', turns.length === 2 && turns[0].quiet && !turns[1].quiet, turns);
  // ---------- through GitHub: the game is an issue of the repository; the invite link is copied from the game
  const ghf = fakeGitHub('github_pat_TEST', 'cher11/globus');
  const A3 = await device(tg, ghf);
  await A3.goto(PAGE); await A3.waitForTimeout(300); await fast(A3);
  await A3.check('#netOn');
  await A3.click('[data-act="net-via"][data-v="gh"]');
  await A3.fill('#ghToken', 'github_pat_WRONG');
  await A3.fill('#ghRepo', 'cher11/globus');
  await A3.click('[data-act="start"]'); await A3.waitForTimeout(400);
  check('a wrong GitHub token is reported', /токен/.test(await A3.evaluate(() => document.querySelector('#panel').textContent)), await A3.evaluate(() => __globus.UI.mode));
  await A3.fill('#ghToken', 'github_pat_TEST');
  await A3.click('[data-act="start"]');
  await A3.waitForFunction(() => __globus.UI.mode === 'play', null, { timeout: 10000 });
  const issue = ghf.issues.get(1);
  check('the game opens an issue with the game data and no token in it', !!issue && issue.body.includes('GLOBUS1:') && !issue.body.includes('github_pat') && /партия/.test(issue.title), issue && issue.title);
  await A3.click('[data-act="net-copy"]'); await A3.waitForTimeout(200);
  const copied = await A3.evaluate(() => navigator.clipboard.readText());
  check('the invite link is copied from the game', copied.includes('#gh='), copied.slice(0, 80));
  const B3 = await device(tg, ghf);
  await B3.goto(PAGE + copied.slice(copied.indexOf('#gh='))); await fast(B3);
  await B3.waitForSelector('[data-act="net-join"]', { timeout: 10000 });
  await B3.click('[data-act="net-join"][data-i="1"]');
  await B3.waitForFunction(() => __globus.UI.mode === 'play', null, { timeout: 10000 });
  await clickCell(A3, 0);
  await waitTurn(B3, 1);
  await clickCell(B3, 40);
  await waitTurn(A3, 0);
  check('boards match in a GitHub game', await hash(A3) === await hash(B3));
  check('a readable comment per turn', ghf.comments.length === 2 && /^Ход 2 · /.test(ghf.comments[1].body), ghf.comments);
  await B3.reload(); await B3.waitForTimeout(500); await fast(B3);
  await A3.click('[data-act="harvest"]');
  await waitTurn(B3, 1);
  check('after a reload B keeps playing the GitHub game', await hash(A3) === await hash(B3) && await B3.evaluate(() => __globus.net.NET.kind === 'gh'));

  // ---------- taking a move back: B builds a field, takes it back before A answers; later, once A has answered, it cannot
  for (const pg of [A3, B3]) pg.on('dialog', d => d.accept());
  const before = await hash(B3);
  await B3.click('[data-act="build"][data-type="field"]'); await clickCell(B3, 39, 600);
  if (await B3.$('[data-act="end"]')) await B3.click('[data-act="end"]');
  await waitTurn(A3, 0);
  check('A sees B\'s field', await A3.evaluate(() => { const u = __globus.G.units[__globus.G.grid[39]]; return !!u && u.type === 'field'; }));
  await B3.waitForSelector('[data-act="net-undo"]', { timeout: 5000 });
  await B3.click('[data-act="net-undo"]');
  await waitTurn(B3, 1);
  check('B\'s board is back to before the move', await hash(B3) === before);
  await A3.waitForFunction(() => __globus.G.cur === 1, null, { timeout: 10000 });
  check('A gets the board back too, and it is B\'s turn again', await hash(A3) === before && !(await A3.evaluate(() => !!__globus.G.grid[39])));
  check('the take-back is posted as a readable line', /взят назад/.test(ghf.comments[ghf.comments.length - 1].body), ghf.comments.slice(-1));
  await B3.click('[data-act="harvest"]'); await B3.waitForTimeout(300);
  if (await B3.$('[data-act="end"]')) await B3.click('[data-act="end"]');
  await waitTurn(A3, 0);
  check('play goes on after the take-back', await hash(A3) === await hash(B3));
  await A3.click('[data-act="harvest"]');
  await waitTurn(B3, 1);
  check('once A has answered, B has no take-back', !(await B3.$('[data-act="net-undo"]')) && await hash(A3) === await hash(B3));

  // ---------- two moves back: B moves, A answers; A takes its answer back, then B takes back its own move
  const before2 = await hash(B3);
  await B3.click('[data-act="build"][data-type="field"]'); await clickCell(B3, 39, 600);
  if (await B3.$('[data-act="end"]')) await B3.click('[data-act="end"]');
  await waitTurn(A3, 0);
  await A3.click('[data-act="build"][data-type="field"]'); await clickCell(A3, 1, 600);
  if (await A3.$('[data-act="end"]')) await A3.click('[data-act="end"]');
  await waitTurn(B3, 1);
  await A3.waitForSelector('[data-act="net-undo"]', { timeout: 5000 });
  await A3.click('[data-act="net-undo"]');
  await B3.waitForFunction(() => __globus.G.cur === 0, null, { timeout: 10000 });
  check('B follows A\'s take-back and keeps its record', await hash(A3) === await hash(B3) && await B3.evaluate(() => __globus.G.hist.length > 0 && __globus.G.hist[__globus.G.hist.length - 1][0] === 1));
  await B3.waitForSelector('[data-act="net-undo"]', { timeout: 5000 });
  await B3.click('[data-act="net-undo"]');
  await A3.waitForFunction(() => __globus.G.cur === 1, null, { timeout: 10000 });
  check('then B takes back its own move: both boards as before both moves', await hash(B3) === before2 && await hash(A3) === before2);

  // ---------- one player on two devices: B plays its turn on a second device; the first one catches up by itself
  const B4 = await device(tg, ghf);
  await B4.goto(PAGE + copied.slice(copied.indexOf('#gh='))); await fast(B4);
  await B4.waitForSelector('[data-act="net-join"]', { timeout: 10000 });
  await B4.click('[data-act="net-join"][data-i="1"]');
  await waitTurn(B4, 1);
  check('the second device loads the game as it is now', await hash(B4) === await hash(B3));
  await B4.click('[data-act="harvest"]'); await B4.waitForTimeout(300);
  if (await B4.$('[data-act="end"]')) await B4.click('[data-act="end"]');
  await waitTurn(A3, 0);
  await B3.waitForFunction(() => __globus.G.cur === 0, null, { timeout: 10000 });
  check('the first device, which still had B to move, catches up', await hash(B3) === await hash(A3));

  // ---------- a link to another game while a game is on: ask first; staying keeps the game, going opens the link
  const tgLink = invite.slice(invite.indexOf('#tg='));
  await B3.goto(PAGE + tgLink); await B3.waitForSelector('#askModal:not([hidden])', { timeout: 5000 });
  check('a link to another game asks first (online game: it can be picked up later)', /продолжить позже/.test(await B3.textContent('#askText')));
  await B3.click('#askNo'); await B3.waitForTimeout(300);
  check('staying keeps the current game', await B3.evaluate(() => __globus.UI.view === 'game' && __globus.net.NET && __globus.net.NET.kind === 'gh'));
  await B3.goto(PAGE + tgLink); await B3.waitForSelector('#askModal:not([hidden])', { timeout: 5000 });
  await B3.click('#askOk');
  await B3.waitForSelector('[data-act="net-join"]', { timeout: 10000 });
  check('going opens the game from the link', true);
  const L1 = await device();
  await L1.goto(PAGE); await L1.waitForTimeout(300);
  await L1.evaluate(() => __globus.start([{ color: 0, name: 'A', ai: false }, { color: 1, name: 'B', ai: false }], 77));
  await clickCell(L1, 0);
  await L1.goto(PAGE + tgLink); await L1.waitForSelector('#askModal:not([hidden])', { timeout: 5000 });
  check('a local game in progress: the question says it will be lost', /пропадёт/.test(await L1.textContent('#askText')));
  await L1.click('#askOk');
  await L1.waitForSelector('[data-act="net-join"]', { timeout: 10000 });

  check('no page errors', errors.length === 0, errors.slice(0, 3));
  console.log(failures() ? `${failures()} FAILED` : 'ALL PASSED');
  await browser.close();
  process.exit(failures() ? 1 : 0);
})();
