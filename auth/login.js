// 로그인 화면 동작. 상태: 로그인 전 / 승인 대기 / 승인됨(→ 원래 가려던 화면으로) / 서버 거부.
(function () {
  'use strict';
  const A = window.YMAuth;
  const L = A.logic;
  const ENTER_KEY = 'ym.auth.entered';
  const ENTER_GUARD_MS = 15000;
  const RECHECK_MS = 60 * 1000;
  const $ = id => document.getElementById(id);
  let entering = false;
  let busy = false;

  const shown = () => [...document.querySelectorAll('[data-view]')].find(section => !section.hidden);

  function show(name) {
    for (const section of document.querySelectorAll('[data-view]')) section.hidden = section.dataset.view !== name;
  }

  // 이 화면은 원래 요청한 주소 그대로 대신 나오므로, 로그인 뒤에는 같은 주소를 다시 열면 된다.
  const target = () => L.safeNext(location.pathname + location.search);

  // 방금 화면으로 들어가려 한 기록: {at: 시각, tries: 횟수}. 본 화면이 열리면 auth/session.js 가 지운다.
  function attempts() {
    try {
      const [at, tries] = String(sessionStorage.getItem(ENTER_KEY) || '').split(':').map(Number);
      return at && Date.now() - at < ENTER_GUARD_MS ? { at, tries: tries || 1 } : { at: 0, tries: 0 };
    } catch (error) {
      return null;
    }
  }

  function remember(tries) {
    try {
      if (tries) sessionStorage.setItem(ENTER_KEY, `${Date.now()}:${tries}`);
      else sessionStorage.removeItem(ENTER_KEY);
      return true;
    } catch (error) {
      return false;
    }
  }

  function go(tries) {
    if (!remember(tries)) {
      // 기록을 남길 수 없는 브라우저에서는 자동 이동을 반복하지 않도록 버튼으로 안내한다.
      $('ymEnter').href = target();
      return show('ready');
    }
    entering = true;
    location.replace(target());
  }

  // 승인된 계정이면 화면으로 들어간다. 방금 들어가려다 이 화면으로 되돌아왔다면 서버가 거부한 것이다.
  // 토큰을 새로 받아 한 번만 더 시도하고, 그래도 안 되면 새로 고침을 반복하지 않고 안내를 보여 준다.
  async function enter(view) {
    if (entering || busy) return;
    const tried = attempts();
    if (!tried || !tried.tries) return go(1);
    busy = true;
    let fresh;
    try {
      fresh = await A.recheck();
    } finally {
      busy = false;
    }
    if (fresh.state === 'anonymous' || fresh.state === 'pending') {
      remember(0);
      return paint(fresh);
    }
    if (tried.tries < 2) return go(2);
    $('ymDeniedWho').textContent = fresh.email || view.email || '';
    show('denied');
  }

  function paint(view) {
    if (view.state === 'member' || view.state === 'admin') return enter(view);
    if (view.state === 'pending') {
      $('ymPendingWho').textContent = view.email || '';
      return show('pending');
    }
    const box = $('ymLoginError');
    box.hidden = !view.error;
    box.textContent = view.error ? '로그인하지 못했습니다: ' + view.error : '';
    show('login');
    checkService();
  }

  // 로그인 서비스(Netlify Identity)나 구글 로그인이 꺼져 있으면 버튼을 눌러도 오류 화면만 나오므로 미리 알린다.
  async function checkService() {
    const service = await A.settings();
    const message = !service.ok
      ? '로그인 서비스가 아직 준비되지 않았습니다. 관리자에게 알려 주세요.'
      : !service.google
        ? '구글 로그인이 아직 켜져 있지 않습니다. 관리자에게 알려 주세요.'
        : '';
    $('ymLogin').disabled = Boolean(message);
    if (!message) return;
    const box = $('ymLoginError');
    box.textContent = box.hidden || !box.textContent ? message : box.textContent + ' · ' + message;
    box.hidden = false;
  }

  function login() {
    remember(0);
    A.login();
  }

  async function logout() {
    remember(0);
    await A.logout();
    paint(A.snapshot());
  }

  // 승인되었는지 확인: 토큰을 새로 받아 권한이 붙었는지 본다(구글 로그인을 다시 거치지 않는다).
  async function recheck(manual) {
    if (busy || entering) return;
    busy = true;
    const note = $('ymPendingNote');
    if (manual) {
      note.hidden = true;
      $('ymRecheck').disabled = true;
    }
    let view;
    try {
      view = await A.recheck();
    } finally {
      busy = false;
      $('ymRecheck').disabled = false;
    }
    if (view.state !== 'pending') return paint(view);
    if (manual) {
      note.textContent = '아직 승인 전입니다. 승인되면 이 화면이 자동으로 넘어갑니다.';
      note.hidden = false;
    }
  }

  $('ymLogin').addEventListener('click', login);
  $('ymRetry').addEventListener('click', login);
  $('ymRecheck').addEventListener('click', () => recheck(true));
  $('ymSwitch').addEventListener('click', logout);
  $('ymLeave').addEventListener('click', logout);

  A.start().then(view => {
    paint(view);
    // 다른 탭에서 로그인·로그아웃했거나 승인이 반영되면 이 화면도 따라간다.
    A.watch(now => {
      if (!entering) paint(now);
    });
    setInterval(() => {
      const section = shown();
      if (section && section.dataset.view === 'pending' && document.visibilityState === 'visible') recheck(false);
    }, RECHECK_MS);
  });
})();
