// 회원 관리 화면. 목록·승인 처리는 /.netlify/functions/members 가 하고(관리자인지 서버가 다시 확인), 여기서는 화면만 그린다.
(function () {
  'use strict';
  const A = window.YMAuth;
  const L = A.logic;
  const ENDPOINT = '/.netlify/functions/members';
  const ARM_MS = 4000;
  const LABEL = { pending: '승인 대기', member: '회원', admin: '관리자' };
  // [동작, 버튼 이름, 모양, 한 번 더 눌러 확인할지]
  const BUTTONS = {
    pending: [['approve', '승인', 'primary', false], ['remove', '거절', 'danger', true]],
    member: [['promote', '관리자 지정', '', true], ['revoke', '승인 해제', 'danger', true]],
    admin: [['demote', '관리자 해제', '', true]],
  };
  const DONE = {
    approve: '승인했습니다. 상대방의 대기 화면은 1분 안에 자동으로 넘어갑니다.',
    revoke: '승인을 해제했습니다. 이미 로그인한 기기에는 최대 1시간 뒤에 적용됩니다.',
    promote: '관리자로 지정했습니다.',
    demote: '관리자 권한을 해제했습니다. 일반 회원으로 남습니다.',
    remove: '요청을 삭제했습니다.',
  };
  const $ = id => document.getElementById(id);
  const state = { members: [], me: null, filter: null, busy: null, armed: null, armTimer: null };

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function notice(text, bad) {
    const box = $('ymNotice');
    box.hidden = !text;
    box.textContent = text || '';
    box.classList.toggle('bad', Boolean(bad));
  }

  async function api(method, body) {
    await A.refresh();
    const headers = { Authorization: 'Bearer ' + A.token() };
    if (body) headers['Content-Type'] = 'application/json';
    const response = await fetch(ENDPOINT, { method, headers, cache: 'no-store', body: body ? JSON.stringify(body) : undefined });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `요청을 처리하지 못했습니다 (HTTP ${response.status})`);
    return data;
  }

  const visible = () =>
    state.members.filter(member =>
      state.filter === 'all' ? true : state.filter === 'pending' ? member.status === 'pending' : member.status !== 'pending');

  function actionButton(member, [action, label, look, confirm]) {
    const key = member.id + ':' + action;
    const armed = state.armed === key;
    const button = element('button', 'ym-btn small' + (look ? ' ' + look : ''), armed ? '한 번 더 누르면 실행' : label);
    button.type = 'button';
    button.disabled = Boolean(state.busy);
    button.addEventListener('click', () => {
      if (confirm && !armed) return arm(key);
      run(member, action);
    });
    return button;
  }

  function arm(key) {
    clearTimeout(state.armTimer);
    state.armed = key;
    state.armTimer = setTimeout(() => {
      state.armed = null;
      paint();
    }, ARM_MS);
    paint();
  }

  function row(member) {
    const line = element('tr');
    const status = element('td');
    status.append(element('span', 'ym-tag ' + member.status, LABEL[member.status] || member.status));
    const who = element('td', 'who');
    who.append(element('b', '', member.email || '(이메일 없음)'));
    const mine = state.me && member.id === state.me.id;
    const sub = [member.name, mine ? '내 계정' : null].filter(Boolean).join(' · ');
    if (sub) who.append(element('span', '', sub));
    const acts = element('div', 'acts');
    if (!mine) for (const spec of BUTTONS[member.status] || []) acts.append(actionButton(member, spec));
    const cell = element('td');
    cell.append(acts);
    line.append(status, who, element('td', 'when', L.dateLabel(member.createdAt)), cell);
    return line;
  }

  function paint() {
    const counts = L.counts(state.members);
    $('ymCountPending').textContent = counts.pending;
    $('ymCountApproved').textContent = counts.member + counts.admin;
    $('ymCountAll').textContent = counts.total;
    for (const button of $('ymFilter').querySelectorAll('button')) button.classList.toggle('on', button.dataset.filter === state.filter);
    const rows = visible();
    $('ymRows').replaceChildren(...rows.map(row));
    const empty = $('ymEmpty');
    empty.hidden = rows.length > 0;
    empty.textContent = state.filter === 'pending' ? '승인을 기다리는 요청이 없습니다.' : '표시할 회원이 없습니다.';
  }

  async function load() {
    try {
      const data = await api('GET');
      state.members = data.members || [];
      state.me = data.me || null;
      if (!state.filter) state.filter = L.counts(state.members).pending ? 'pending' : 'all';
      notice(data.truncated ? '회원이 많아 일부만 표시했습니다.' : '');
      paint();
    } catch (error) {
      $('ymRows').replaceChildren();
      $('ymEmpty').hidden = false;
      $('ymEmpty').textContent = '목록을 불러오지 못했습니다.';
      notice(String(error.message || error), true);
    }
  }

  async function run(member, action) {
    clearTimeout(state.armTimer);
    state.armed = null;
    state.busy = member.id;
    paint();
    try {
      const data = await api('POST', { id: member.id, action });
      state.members = L.sortMembers(
        state.members.flatMap(item => (item.id !== member.id ? [item] : data.member ? [data.member] : [])));
      notice(`${member.email || '계정'} — ${DONE[action]}`);
    } catch (error) {
      notice(`${member.email || '계정'} — ${String(error.message || error)}`, true);
    } finally {
      state.busy = null;
      paint();
    }
  }

  $('ymFilter').addEventListener('click', event => {
    const button = event.target.closest('button[data-filter]');
    if (!button) return;
    state.filter = button.dataset.filter;
    paint();
  });
  $('ymReload').addEventListener('click', load);

  A.start().then(load);
})();
