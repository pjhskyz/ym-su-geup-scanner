// 브라우저용 로그인 도우미 (window.YMAuth). Netlify Identity 의 표준 주소만 사용한다:
//   /.netlify/identity/authorize?provider=google  구글 로그인 시작 → 끝나면 "#access_token=…&refresh_token=…" 을 붙여 돌려보냄
//   /.netlify/identity/token                       갱신 토큰으로 새 접근 토큰 받기
//   /.netlify/identity/logout                      로그아웃(갱신 토큰 폐기)
// 로그인 상태는 쿠키 두 개에만 둔다. nf_jwt(접근 토큰, 1시간)는 Netlify 가 _redirects 의 Role 조건을
// 판단할 때 읽는 쿠키이고, nf_refresh 는 새 접근 토큰을 받는 데 쓴다. 쿠키는 모든 탭이 함께 쓰므로
// 탭마다 상태가 어긋나지 않고, 갱신은 한 번에 한 탭만 하도록 잠금(Web Locks)을 건다.
(function () {
  'use strict';
  const L = window.AuthLogic;
  const IDENTITY = '/.netlify/identity';
  const ACCESS = 'nf_jwt';
  const REFRESH = 'nf_refresh';
  const STARTED = 'ym_auth_started'; // 이 브라우저에서 방금 로그인을 시작했다는 표시
  const SIGNAL = 'ym.auth.signal'; // 다른 탭에 로그인·로그아웃을 알리는 신호
  const KEEP_SECONDS = 30 * 24 * 3600; // 브라우저를 닫아도 30일 동안 로그인 유지
  const STARTED_SECONDS = 10 * 60;
  const MARGIN_SECONDS = 5 * 60; // 만료 5분 전부터 새 토큰을 받는다
  const TICK_MS = 30 * 1000;
  const listeners = new Set();
  let lastSign = null;
  let started = null;

  const nowSeconds = () => Date.now() / 1000;
  const read = name => L.cookieValue(document.cookie, name);
  const write = (name, value, seconds) => {
    document.cookie = `${name}=${encodeURIComponent(value)}; path=/; secure; samesite=lax; max-age=${seconds}`;
  };

  function save(tokens) {
    write(ACCESS, tokens.access_token, KEEP_SECONDS);
    if (tokens.refresh_token) write(REFRESH, tokens.refresh_token, KEEP_SECONDS);
  }

  function clear() {
    write(ACCESS, '', 0);
    write(REFRESH, '', 0);
  }

  function signal() {
    try {
      localStorage.setItem(SIGNAL, String(Date.now()));
    } catch (error) {
      /* 신호를 못 보내도 다른 탭은 다음 확인 주기에 알아챈다 */
    }
  }

  const token = () => read(ACCESS);

  // 지금 쿠키에 든 토큰 기준 상태: anonymous | pending | member | admin. 서버가 보는 것과 같은 값이다.
  function snapshot() {
    const claims = L.decodeJwt(token());
    const state = L.access(claims, nowSeconds());
    if (state === 'anonymous') return { state, email: null, name: null, roles: [] };
    const meta = claims.user_metadata || {};
    return { state, email: claims.email || null, name: meta.full_name || meta.name || null, roles: L.rolesOf(claims) };
  }

  // 여러 탭이 같은 갱신 토큰을 동시에 쓰면 뒤늦은 쪽이 거절되므로 한 번에 한 탭만 갱신한다.
  function lock(task) {
    try {
      if (navigator.locks && navigator.locks.request) return navigator.locks.request('ym-auth-refresh', task);
    } catch (error) {
      /* 잠금을 쓸 수 없는 환경에서는 그냥 진행한다 */
    }
    return task();
  }

  // 새 접근 토큰을 받는다. 결과: 'ok'(유효한 토큰 있음) | 'kept'(일시 장애 — 로그인은 그대로 둠) | 'gone'(로그인이 더는 유효하지 않음)
  function renew(force) {
    return lock(async () => {
      // 잠금을 기다리는 사이 다른 탭이 이미 갱신했을 수 있다.
      if (!force && !L.needsRefresh(L.decodeJwt(token()), nowSeconds(), MARGIN_SECONDS)) return 'ok';
      const used = read(REFRESH);
      if (!used) return 'gone';
      let response;
      try {
        response = await fetch(IDENTITY + '/token', {
          method: 'POST',
          cache: 'no-store',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: 'grant_type=refresh_token&refresh_token=' + encodeURIComponent(used),
        });
      } catch (error) {
        return 'kept';
      }
      if (response.ok) {
        const tokens = await response.json().catch(() => null);
        if (!tokens || !tokens.access_token) return 'kept';
        save(tokens);
        return 'ok';
      }
      if (response.status === 400 || response.status === 401) {
        // 갱신 토큰이 거절됐다. 그 사이 다른 창에서 새로 로그인했다면 그 토큰을 그대로 쓴다.
        if (read(REFRESH) !== used) return 'ok';
        clear();
        return 'gone';
      }
      return 'kept';
    });
  }

  // 필요하면 토큰을 갱신하고 현재 상태를 돌려준다. certain 이 false 면 일시 장애로 판단을 미룬 것이다.
  async function sync(force) {
    let outcome = 'ok';
    if (read(REFRESH) && (force || L.needsRefresh(L.decodeJwt(token()), nowSeconds(), MARGIN_SECONDS))) {
      try {
        outcome = navigator.onLine === false ? 'kept' : await renew(Boolean(force));
      } catch (error) {
        outcome = 'kept';
      }
    }
    return { view: snapshot(), certain: outcome !== 'kept' };
  }

  const sign = view => JSON.stringify([view.state, view.email, view.roles]);

  async function tick() {
    const result = await sync(false);
    if (!result.certain) return;
    const now = sign(result.view);
    if (now === lastSign) return;
    lastSign = now;
    for (const listener of listeners) {
      try {
        listener(result.view);
      } catch (error) {
        /* 한 화면의 오류가 다른 화면의 갱신을 막지 않게 한다 */
      }
    }
  }

  // 구글 로그인에서 돌아온 주소의 토큰을 받아 저장한다. 토큰이 주소창·방문 기록에 남지 않게 바로 지운다.
  function takeCallback() {
    const callback = L.parseCallback(location.hash);
    if (!callback) return null;
    history.replaceState(null, '', location.pathname + location.search);
    const expected = read(STARTED);
    write(STARTED, '', 0);
    if (callback.error) return callback.error;
    // 이 브라우저에서 로그인 버튼을 누른 적이 없는데 토큰이 붙어 왔다면 남이 만든 링크일 수 있으므로 받지 않는다.
    if (!expected) return '로그인 요청을 확인할 수 없습니다. 다시 시도해 주세요.';
    save(callback.tokens);
    signal();
    return snapshot().state === 'anonymous' ? '로그인 정보를 확인하지 못했습니다. 다시 시도해 주세요.' : null;
  }

  async function begin() {
    const error = takeCallback();
    const result = await sync(false);
    lastSign = sign(result.view);
    setInterval(tick, TICK_MS);
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') tick();
    });
    window.addEventListener('online', tick);
    window.addEventListener('storage', event => {
      if (event.key === SIGNAL) tick();
    });
    return { ...result.view, error };
  }

  // 화면을 열 때 한 번 부른다. 결과: {state, email, name, roles, error}
  const start = () => started || (started = begin());

  async function refresh() {
    return (await sync(false)).view;
  }

  // 승인 여부처럼 권한이 바뀌었는지 바로 확인할 때: 접근 토큰을 강제로 새로 받는다.
  async function recheck() {
    return (await sync(true)).view;
  }

  function login() {
    write(STARTED, '1', STARTED_SECONDS);
    location.href = IDENTITY + '/authorize?provider=google';
  }

  async function logout() {
    const current = token();
    clear();
    lastSign = sign(snapshot());
    signal();
    if (!current) return;
    try {
      await fetch(IDENTITY + '/logout', { method: 'POST', cache: 'no-store', keepalive: true, headers: { Authorization: 'Bearer ' + current } });
    } catch (error) {
      /* 이 브라우저의 로그인은 이미 지워졌다 */
    }
  }

  // 로그인 서비스 상태: {ok: Identity 응답 여부, google: 구글 로그인 사용 여부}
  async function settings() {
    try {
      const response = await fetch(IDENTITY + '/settings', { cache: 'no-store' });
      if (!response.ok) return { ok: false, google: false };
      const body = await response.json();
      return { ok: true, google: Boolean(body && body.external && body.external.google) };
    } catch (error) {
      return { ok: false, google: false };
    }
  }

  // 로그인 상태(상태·계정·권한)가 바뀌면 알려 준다. 다른 탭에서 일어난 변화도 포함된다.
  function watch(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  window.YMAuth = { logic: L, start, refresh, recheck, login, logout, settings, snapshot, token, watch };
})();
