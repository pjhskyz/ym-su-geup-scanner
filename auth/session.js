// 본 화면·관리 화면 공용: 로그인한 계정 표시, 로그아웃, 로그인이 풀렸을 때의 처리.
// 화면과 자료를 막는 일은 서버(_redirects)가 하고, 여기서는 표시와 편의 기능만 맡는다.
(function () {
  'use strict';
  const A = window.YMAuth;
  if (!A) return;
  const L = A.logic;
  const box = document.getElementById('ymUser');
  let signedIn = false;
  let leaving = false;
  let pending = null;

  try {
    sessionStorage.removeItem('ym.auth.entered');
  } catch (error) {
    /* 저장소를 못 쓰는 브라우저는 그대로 둔다 */
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  const menu = () => box && box.querySelector('.ym-user-menu');
  const isOpen = () => Boolean(menu() && !menu().hidden);

  function setOpen(open) {
    const panel = menu();
    if (!panel) return;
    const button = box.querySelector('.ym-user-btn');
    if (open) {
      // 상단 막대가 옆으로 밀리는 좁은 화면에서도 잘리지 않도록 화면 기준으로 띄운다.
      const rect = button.getBoundingClientRect();
      panel.style.top = Math.round(rect.bottom + 6) + 'px';
      panel.style.right = Math.max(8, Math.round(window.innerWidth - rect.right)) + 'px';
    }
    panel.hidden = !open;
    button.setAttribute('aria-expanded', String(open));
  }

  function paint(view) {
    if (!box) return;
    const wasOpen = isOpen();
    box.textContent = '';
    box.hidden = !signedIn;
    if (!signedIn) return;
    const manage = view.state === 'admin' && !document.body.hasAttribute('data-ym-admin');
    const label = view.name || String(view.email || '').split('@')[0] || '계정';

    const button = element('button', 'ym-user-btn');
    button.type = 'button';
    button.setAttribute('aria-haspopup', 'menu');
    button.setAttribute('aria-expanded', 'false');
    button.title = manage && pending ? `승인 대기 ${pending}건` : '계정';
    button.append(element('span', 'who', label + ' 님'));
    if (manage && pending) button.append(element('b', '', String(pending)));
    button.addEventListener('click', () => setOpen(!isOpen()));

    const panel = element('div', 'ym-user-menu');
    panel.hidden = true;
    panel.setAttribute('role', 'menu');
    panel.append(element('div', 'mail', view.email || ''));
    if (manage) {
      const link = element('a', '', '회원 관리');
      link.href = '/admin.html';
      link.setAttribute('role', 'menuitem');
      if (pending) link.append(element('b', '', String(pending)));
      panel.append(link);
    }
    const out = element('button', 'ym-user-out', '로그아웃');
    out.type = 'button';
    out.setAttribute('role', 'menuitem');
    out.addEventListener('click', logout);
    panel.append(out);

    box.append(button, panel);
    if (wasOpen) setOpen(true);
  }

  if (box) {
    document.addEventListener('click', event => {
      if (!box.contains(event.target)) setOpen(false);
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') setOpen(false);
    });
    window.addEventListener('resize', () => setOpen(false));
  }

  // 로그인이 풀렸으면(로그아웃·승인 해제) 같은 주소를 다시 열어 서버가 로그인 화면을 보여 주게 한다.
  function leave() {
    if (leaving) return;
    leaving = true;
    location.replace(location.pathname + location.search);
  }

  async function logout() {
    leaving = true;
    await A.logout();
    location.replace('/');
  }

  // 관리자에게는 승인 대기 건수를 알려 준다.
  async function loadPending(view) {
    if (view.state !== 'admin') return;
    try {
      const response = await fetch('/.netlify/functions/members', { headers: { Authorization: 'Bearer ' + A.token() }, cache: 'no-store' });
      if (!response.ok) return;
      const body = await response.json();
      pending = (body.counts && body.counts.pending) || 0;
      paint(A.snapshot());
    } catch (error) {
      /* 건수 표시는 없어도 된다 */
    }
  }

  A.start().then(view => {
    // 로그인 없이 열린 환경(로컬 미리보기 등)에서는 아무 것도 표시하지 않는다.
    signedIn = L.canView(view.roles);
    paint(view);
    if (!signedIn) return;
    loadPending(view);
    A.watch(now => {
      if (leaving) return;
      if (L.canView(now.roles)) paint(now);
      else leave();
    });
  });
})();
