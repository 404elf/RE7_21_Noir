const $ = (id) => document.getElementById(id);
const categories = { attack: '进攻', guard: '防御', draw: '抽牌', control: '干扰', resource: '资源', target: '目标' };
let catalog = {}, state = null, seat = null, socket = null, selected = null;
let pending = null, rendered = -1, reconnectTimer, heartbeat, toastTimer, cooldownTimer;
let closing = false, retry = 0, online = false, cooldown = 0;

function storageRead(key) { try { return sessionStorage.getItem(key); } catch { return null; } }
function saveSeat(value) {
  seat = value;
  try { if (value) sessionStorage.setItem('noir-seat', JSON.stringify(value)); else sessionStorage.removeItem('noir-seat'); }
  catch { toast('浏览器未允许会话存储，关闭页面后将无法重连。'); }
}
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function toast(message) {
  $('toast').textContent = message;
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 5000);
}
function connection(text) { $('connection').textContent = text; }
function cardInfo(name) { return catalog[name] || { title: name, description: '', category: 'control', english: name }; }
function myPlayer() { return state?.players.find((p) => p.id === state.pid); }
function playerName(pid) { return pid === state?.pid ? '你' : state?.players.find((p) => p.id === pid)?.name || '对手'; }

async function enter(path) {
  $('home-error').textContent = '';
  $('create-room').disabled = $('join-room').disabled = true;
  try {
    const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: $('nickname').value.trim() }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '无法进入房间。');
    saveSeat(data);
    try { sessionStorage.setItem('noir-name', $('nickname').value.trim()); } catch { /* Storage is optional. */ }
    history.replaceState(null, '', `?room=${encodeURIComponent(data.room)}`);
    state = null; rendered = -1; closing = false; retry = 0;
    connect();
  } catch (error) { $('home-error').textContent = error.message; }
  finally { $('create-room').disabled = $('join-room').disabled = false; }
}

function connect() {
  if (!seat || closing) return;
  clearTimeout(reconnectTimer);
  $('home').hidden = true; $('room').hidden = false;
  $('room-label').textContent = seat.room;
  $('notice').textContent = retry ? '连接中断，正在恢复席位…' : '正在连接牌桌…';
  connection('连接中');
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/${encodeURIComponent(seat.room)}`);
  socket = ws;
  ws.onopen = () => {
    if (socket !== ws) return;
    ws.send(JSON.stringify({ type: 'auth', token: seat.token }));
    clearInterval(heartbeat);
    heartbeat = setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' })); }, 10000);
  };
  ws.onmessage = (event) => {
    if (socket !== ws) return;
    const data = JSON.parse(event.data);
    if (data.type === 'state') {
      if (state && data.revision < state.revision) return;
      online = true; retry = 0;
      if (pending !== null && data.revision > pending) pending = null;
      if (JSON.stringify(myPlayer()?.trumps) !== JSON.stringify(data.players.find((p) => p.id === data.pid)?.trumps)) selected = null;
      state = data;
      connection('已连接 · 私人牌桌');
      render();
    } else if (data.type === 'error') {
      pending = null; toast(data.message); updateControls();
    } else if (data.type === 'fatal' || data.type === 'closed') {
      closing = true; online = false; pending = null;
      $('notice').textContent = data.message;
      connection('连接已结束'); updateControls();
      toast(data.message);
      // Keep the last table visible; Exit is always available.
      saveSeat(null);
      ws.close();
    }
  };
  ws.onclose = () => {
    if (socket !== ws) return;
    clearInterval(heartbeat);
    online = false; pending = null; updateControls();
    if (!closing && seat) {
      connection('重连中');
      $('notice').textContent = '连接中断，正在重连。对局会暂停，60 秒内恢复可继续。';
      reconnectTimer = setTimeout(connect, Math.min(1000 * 2 ** retry++, 5000));
    }
  };
  ws.onerror = () => { connection('网络不可用'); };
}

function send(type, action, index) {
  if (!online || !state || pending !== null || socket?.readyState !== WebSocket.OPEN) return;
  pending = state.revision;
  const message = { type, revision: state.revision };
  if (action) message.action = action;
  if (index !== undefined) message.index = index;
  socket.send(JSON.stringify(message));
  if (['HIT', 'STAY', 'TRUMP', 'DISCARD'].includes(action)) {
    cooldown = Date.now() + 550;
    clearTimeout(cooldownTimer);
    cooldownTimer = setTimeout(updateControls, 560);
  }
  if (action === 'TRUMP' || action === 'DISCARD') selected = null;
  updateControls();
}

function render() {
  $('home').hidden = true; $('room').hidden = false;
  $('lobby').hidden = state.phase !== 'LOBBY'; $('game').hidden = state.phase === 'LOBBY';
  if (state.paused) $('notice').textContent = `对手连接中断，牌局与计时已暂停。剩余重连时间 ${state.reconnect_seconds} 秒。`;
  else if (state.phase === 'LOBBY') $('notice').textContent = '把房间码或邀请链接发给朋友，双方准备后开局。';
  else if (state.phase === 'GAMEOVER') $('notice').textContent = '本场结束。双方同意再战后，将开启全新一场。';
  else $('notice').textContent = state.phase === 'RESULT' ? '双方亮牌，正在结算。本局结果会保留在记录中。' : '暗牌只对你可见。每次抽牌或停牌后交接行动。';
  if (rendered !== state.revision) {
    rendered = state.revision;
    if (state.phase === 'LOBBY') renderLobby();
    else renderGame();
  }
  updateClocks(); updateControls();
}

function renderLobby() {
  $('lobby-players').replaceChildren(...state.players.map((p) => {
    const node = element('div', null, 'lobby-seat');
    node.append(element('strong', p.name), element('span', p.ready ? '已准备' : p.connected ? '已入座 · 等待准备' : '等待连接'));
    return node;
  }));
  const config = state.rules.game_settings;
  $('lobby-settings').textContent = `目标 ${config.target_score} · 生命 ${config.max_hp} · 数字牌 ${config.deck_range_start}–${config.deck_range_end} · ${state.timer.enabled ? { turn: '行动计时', round: '每局计时', fischer: '整场加秒制' }[state.timer.mode] : '不限时'}`;
  $('ready').textContent = myPlayer().ready ? '已准备 · 等待对手' : '我准备好了';
}

function renderPlayer(p, mine) {
  const root = $(mine ? 'my-player' : 'opponent');
  const heading = element('div', null, 'player-heading');
  const label = element('div');
  label.append(element('span', p.name, 'player-name'), element('span', mine ? 'YOU' : 'OPPONENT', 'player-tag'));
  const stats = element('div');
  stats.append(element('span', `♥ ${p.hp} / ${state.max_hp}`, 'health'));
  const clock = element('span', '', 'clock'); clock.id = `clock-${p.id}`;
  stats.append(clock); heading.append(label, stats);
  const cards = element('div', null, 'number-cards');
  p.hand.forEach((number, index) => {
    const card = element('div', number === null ? 'N' : String(number), `number-card ${number === null ? 'hidden-card' : index === 0 && mine ? 'private-card' : ''}`);
    card.setAttribute('aria-label', number === null ? '对手暗牌' : `${index === 0 ? '底牌' : '明牌'} ${number}`);
    if (index === 0 && number !== null) card.append(element('small', '底牌'));
    cards.append(card);
  });
  const total = element('div', null, `total ${p.total > state.target ? 'bust' : ''}`);
  total.append(element('strong', p.total === null ? '? + ' + p.hand.slice(1).reduce((a, b) => a + b, 0) : String(p.total)), element('small', p.total === null ? `明牌点数 · 王牌 ${p.trump_count}` : p.total > state.target ? '已爆牌 · 可用王牌自救' : `总点数 · 王牌 ${p.trump_count}`));
  cards.append(total); root.replaceChildren(heading, cards);
}

function renderGame() {
  $('round-label').textContent = `ROUND ${String(state.round).padStart(2, '0')}`;
  $('deck-label').textContent = `牌堆剩余 ${state.deck_count} 张`;
  $('target').textContent = state.target;
  state.players.forEach((p) => renderPlayer(p, p.id === state.pid));
  for (const mine of [true, false]) {
    const effects = state.active_trumps.filter((t) => (t.owner === state.pid) === mine).map((t) => {
      const info = cardInfo(t.name);
      const node = element('span', info.title + (t.counter === undefined ? '' : ` ${t.counter}/${t.val}`), `effect ${info.category}`);
      node.title = info.description; return node;
    });
    $(mine ? 'my-effects' : 'opponent-effects').replaceChildren(...effects);
  }
  const cards = myPlayer().trumps;
  if (selected !== null && selected >= cards.length) selected = null;
  $('trump-count').textContent = `${cards.length} / ${state.rules.game_settings.max_trumps_hand_size}`;
  $('trump-hand').replaceChildren(...cards.map(([name], index) => {
    const info = cardInfo(name);
    const button = element('button', null, `trump-card ${selected === index ? 'selected' : ''}`);
    button.dataset.index = index;
    button.setAttribute('aria-pressed', String(selected === index));
    button.setAttribute('aria-label', `${info.title}：${info.description}`);
    button.append(element('span', categories[info.category], 'category'), element('strong', info.title), element('small', info.english));
    button.onclick = () => { selected = index; renderGame(); updateClocks(); updateControls(); $('trump-hand').querySelector(`[data-index="${index}"]`)?.focus({ preventScroll: true }); };
    return button;
  }));
  if (!cards.length) $('trump-hand').append(element('p', '暂无王牌。每局开始会按配置补充。', 'muted'));
  const info = selected === null ? null : cardInfo(cards[selected][0]);
  $('selected-title').textContent = info?.title || '选择一张王牌';
  $('selected-description').textContent = info?.description || '出牌与弃牌不结束行动；出牌会解除对手的停牌。';
  renderResult();
  $('events').replaceChildren(...state.events.slice(-16).reverse().map((event) => {
    const node = element('li'); node.append(element('small', `R${event.round}`), document.createTextNode(eventText(event))); return node;
  }));
  $('draw-request').hidden = state.draw_offer !== 3 - state.pid || state.phase !== 'ACTION';
  $('draw-request-text').textContent = `${playerName(3 - state.pid)} 提议平局结束本场。`;
}

function eventText(event) {
  const who = playerName(event.pid);
  switch (event.event) {
    case 'round_start': return `新一局开始 · 目标 ${event.target}`;
    case 'hit': return `${who} 抽牌 ${event.drawn?.join('、') || '（牌堆已空）'}`;
    case 'stay': return `${who} ${event.automatic ? '超时自动停牌' : '停牌'}`;
    case 'trump': return `${who} 使用 ${cardInfo(event.card).title}`;
    case 'discard': return `${who} 丢弃一张王牌`;
    case 'result': return `${event.winner ? playerName(event.winner) + ' 赢下本局' : '平局'} · ${event.totals.join(' : ')} · 伤害 ${event.damage}`;
    case 'gameover': return event.winner ? `${playerName(event.winner)} 赢得本场` : '本场平局';
    case 'draw_offer': return `${who} 提出求和`;
    case 'draw_decline': return `${who} 拒绝求和`;
    case 'rematch': return `${who} 请求再战`;
    case 'timeout': return `${who} 行动超时`;
    case 'preparation_timeout': return `${who} 准备超时`;
    default: return event.event;
  }
}

function renderResult() {
  const current = ['RESULT', 'GAMEOVER'].includes(state.phase);
  const result = current ? state : state.last_result;
  $('result-panel').hidden = !result;
  if (!result) return;
  const over = state.phase === 'GAMEOVER';
  $('result-title').textContent = result.winner === 0 ? '平局。' : result.winner === state.pid ? (over ? '你赢得了本场。' : '这一手，你赢了。') : (over ? '本场落败。' : '这一手，对手胜。');
  const reasons = { surrender: '玩家投降', agreement: '双方同意平局', timeout: '整场计时耗尽', preparation_timeout: '首次准备超时', disconnect: '断线超过重连宽限期' };
  $('result-text').textContent = (over && reasons[state.end_reason]) || (result.escape ? '逃脱成功，平局结束整场。' : `${current ? '本局' : `上一局（第 ${result.round} 局）`}伤害 ${result.damage}。${over ? '双方可选择再战。' : '下一局继续争夺。'}`);
  $('rematch').hidden = !over;
  $('rematch').textContent = myPlayer().rematch ? '已请求 · 等待对手' : '再来一场';
}

function updateClocks() {
  if (!state || state.phase === 'LOBBY') return;
  for (const p of state.players) {
    const node = $(`clock-${p.id}`);
    if (!node) continue;
    const prep = state.preparation_active === p.id;
    const left = prep ? p.preparation : p.clock;
    node.textContent = state.timer.enabled ? `${prep ? '准备 ' : ''}${left === null ? '∞' : Math.ceil(left) + 's'}${state.paused ? ' · 暂停' : ''}` : '';
  }
}

function updateControls() {
  const ready = online && state && pending === null;
  $('ready').disabled = !ready || state?.phase !== 'LOBBY' || myPlayer()?.ready;
  const action = ready && !state.paused && state.phase === 'ACTION';
  const turn = action && state.turn === state.pid && Date.now() >= cooldown;
  const lockedDraw = state?.active_trumps?.some((t) => t.owner !== state.pid && ['GAMBLE', 'SILENCE'].includes(t.type));
  const lockedTrump = state?.active_trumps?.some((t) => t.owner !== state.pid && t.type === 'DESTROY_BLOCK');
  $('hit').disabled = !turn || myPlayer()?.total > state.target || state.deck_count === 0 || lockedDraw;
  $('stay').disabled = !turn;
  $('use-trump').disabled = !turn || selected === null || lockedTrump;
  $('discard-trump').disabled = !turn || selected === null;
  $('offer-draw').disabled = !action || Boolean(state.draw_offer);
  $('surrender').disabled = !action;
  $('accept-draw').disabled = $('decline-draw').disabled = !action;
  $('rematch').disabled = !ready || state?.players.some((p) => !p.connected) || myPlayer()?.rematch;
  if (state && state.phase !== 'LOBBY') $('turn-hint').textContent = state.paused ? '暂停 · 等待对手重连' : state.phase !== 'ACTION' ? '等待下一手' : state.turn === state.pid ? (myPlayer().stopped ? '你已停牌，仍可重新行动' : lockedDraw ? '轮到你 · 抽牌被封锁' : '轮到你 · 做出选择') : '对手正在思考…';
}

function renderCatalog() {
  const query = $('catalog-search').value.trim().toLowerCase();
  $('catalog').replaceChildren(...Object.values(catalog).filter((c) => `${c.name} ${c.title} ${c.description}`.toLowerCase().includes(query)).map((c) => {
    const disabled = state?.enabled_cards?.[c.name] === false;
    const item = element('article', null, disabled ? 'disabled-card' : '');
    item.append(element('h3', c.title), element('small', `${c.english} · ${disabled ? '当前牌池未启用' : categories[c.category]}`), element('p', c.description)); return item;
  }));
}

$('create-room').onclick = () => enter('/api/rooms');
$('join-form').onsubmit = (event) => { event.preventDefault(); const code = $('room-code').value.trim().toUpperCase(); if (!/^[A-Z2-9]{8}$/.test(code)) { $('home-error').textContent = '请输入完整的 8 位房间码。'; return; } enter(`/api/rooms/${encodeURIComponent(code)}/join`); };
$('ready').onclick = () => send('ready');
for (const [id, action] of Object.entries({ hit: 'HIT', stay: 'STAY', rematch: 'REMATCH', 'offer-draw': 'DRAW_OFFER', 'accept-draw': 'DRAW_ACCEPT', 'decline-draw': 'DRAW_DECLINE' })) $(id).onclick = () => send('action', action);
$('use-trump').onclick = () => send('action', 'TRUMP', selected);
$('discard-trump').onclick = () => send('action', 'DISCARD', selected);
$('surrender').onclick = () => { if (confirm('确定投降并结束本场对局？')) send('action', 'SURRENDER'); };
$('copy-invite').onclick = async () => {
  const link = `${location.origin}/?room=${state?.room || seat?.room}`;
  try { await navigator.clipboard.writeText(link); toast('邀请链接已复制'); }
  catch { prompt('复制下方邀请链接：', link); }
};
$('leave-room').onclick = () => {
  if (state?.phase === 'ACTION' && !confirm('退出会清除本页席位凭证，无法再用本页重连；未结束的对局会按断线规则判负。确定退出？')) return;
  closing = true; clearTimeout(reconnectTimer); clearInterval(heartbeat); socket?.close();
  saveSeat(null); state = null; rendered = -1; pending = null; online = false; selected = null;
  $('home').hidden = false; $('room').hidden = true; history.replaceState(null, '', '/'); connection('双人在线牌局');
};
$('rules-open').onclick = () => { renderCatalog(); $('rules-dialog').showModal(); };
$('rules-close').onclick = () => $('rules-dialog').close();
$('catalog-search').oninput = renderCatalog;
document.addEventListener('keydown', (event) => {
  if (event.repeat || event.ctrlKey || event.metaKey || event.altKey || $('rules-dialog').open || ['INPUT', 'TEXTAREA', 'BUTTON'].includes(document.activeElement.tagName)) return;
  const button = event.key.toLowerCase() === 'h' ? $('hit') : event.key.toLowerCase() === 's' ? $('stay') : null;
  if (button && !button.disabled) { event.preventDefault(); button.click(); }
});

async function init() {
  const code = new URLSearchParams(location.search).get('room');
  if (code && /^[A-Z2-9]{8}$/i.test(code)) $('room-code').value = code.toUpperCase();
  $('nickname').value = storageRead('noir-name') || '无名旅人';
  try { const response = await fetch('/api/catalog'); if (!response.ok) throw new Error(); catalog = await response.json(); }
  catch { toast('图鉴加载失败，请检查网络后刷新。'); }
  try { const saved = JSON.parse(storageRead('noir-seat')); if (saved && typeof saved.token === 'string' && /^[A-Z2-9]{8}$/.test(saved.room)) { seat = saved; connect(); } }
  catch { saveSeat(null); }
}
init();
