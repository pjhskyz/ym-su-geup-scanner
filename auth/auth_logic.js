// 로그인·회원 승인에 쓰는 순수 로직. 로그인 화면·본 화면(auth/auth.js), 관리 화면, Netlify Function 이 함께 쓴다.
// 로그인 화면이 불러와야 하므로 공개 경로(/auth/)에 둔다. 비밀 값은 넣지 않는다.
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AuthLogic = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const MEMBER = 'member';
  const ADMIN = 'admin';
  const ACTIONS = ['approve', 'revoke', 'promote', 'demote', 'remove'];
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

  const isUuid = value => typeof value === 'string' && UUID.test(value);

  function decodeBase64Url(part) {
    const text = String(part).replace(/-/g, '+').replace(/_/g, '/');
    const padded = text + '='.repeat((4 - (text.length % 4)) % 4);
    if (typeof atob === 'function') {
      const bytes = Uint8Array.from(atob(padded), ch => ch.charCodeAt(0));
      return new TextDecoder().decode(bytes);
    }
    return Buffer.from(padded, 'base64').toString('utf8');
  }

  // 서명은 확인하지 않는다. 화면 분기용이며, 접근 허용 여부는 항상 서버(Netlify·Identity)가 다시 판단한다.
  function decodeJwt(token) {
    if (typeof token !== 'string') return null;
    const parts = token.split('.');
    if (parts.length !== 3) return null;
    try {
      const claims = JSON.parse(decodeBase64Url(parts[1]));
      return claims && typeof claims === 'object' && !Array.isArray(claims) ? claims : null;
    } catch (error) {
      return null;
    }
  }

  function rolesOf(source) {
    if (!source || typeof source !== 'object') return [];
    const meta = source.app_metadata || source.appMetadata || {};
    const roles = Array.isArray(meta.roles) ? meta.roles : Array.isArray(source.roles) ? source.roles : [];
    return [...new Set(roles.filter(role => typeof role === 'string' && role))];
  }

  const isAdmin = roles => Array.isArray(roles) && roles.includes(ADMIN);
  const canView = roles => Array.isArray(roles) && (roles.includes(MEMBER) || roles.includes(ADMIN));
  const statusOf = roles => (isAdmin(roles) ? ADMIN : canView(roles) ? MEMBER : 'pending');

  // 쿠키에 든 토큰 기준으로 지금 화면이 어떤 상태인지: anonymous | pending | member | admin
  function access(claims, nowSeconds) {
    if (!claims || typeof claims.exp !== 'number' || !(claims.exp > nowSeconds)) return 'anonymous';
    return statusOf(rolesOf(claims));
  }

  // 접근 토큰을 새로 받아야 하는지: 토큰이 없거나, 만료됐거나, 만료까지 margin 초 이하로 남았을 때.
  function needsRefresh(claims, nowSeconds, marginSeconds) {
    return !claims || typeof claims.exp !== 'number' || claims.exp - nowSeconds <= marginSeconds;
  }

  // 구글 로그인을 마치고 돌아온 주소의 # 뒤 내용을 읽는다.
  // 토큰이 있으면 {tokens}, 로그인이 거절됐으면 {error}, 로그인과 무관하면 null.
  function parseCallback(hash) {
    const params = new URLSearchParams(String(hash || '').replace(/^#/, ''));
    if (params.get('error')) return { error: params.get('error_description') || params.get('error') };
    const access = params.get('access_token');
    if (!access) return null;
    return { tokens: { access_token: access, refresh_token: params.get('refresh_token') || '' } };
  }

  function nextRoles(roles, action) {
    const current = rolesOf({ roles });
    const others = current.filter(role => role !== MEMBER && role !== ADMIN);
    const wasAdmin = current.includes(ADMIN);
    if (action === 'approve') return [...others, MEMBER, ...(wasAdmin ? [ADMIN] : [])];
    if (action === 'revoke') return others;
    if (action === 'promote') return [...others, MEMBER, ADMIN];
    if (action === 'demote') return [...others, MEMBER];
    throw new Error('알 수 없는 동작입니다: ' + action);
  }

  // 요청을 거절해야 하면 이유를, 진행해도 되면 null 을 돌려준다.
  function refusal(request) {
    const { actorId, targetId, action } = request || {};
    if (!ACTIONS.includes(action)) return '알 수 없는 동작입니다.';
    if (!isUuid(targetId)) return '회원 ID 형식이 올바르지 않습니다.';
    // 관리자가 자기 권한을 스스로 내리지 못하게 해 두면 관리자가 한 명도 없는 상태가 생기지 않는다.
    if (targetId === actorId && action !== 'approve' && action !== 'promote') {
      return '본인 계정의 권한은 직접 내리거나 삭제할 수 없습니다. 다른 관리자에게 요청해 주세요.';
    }
    return null;
  }

  function publicUser(raw) {
    if (!raw || typeof raw !== 'object' || !raw.id) return null;
    const meta = raw.user_metadata || {};
    const app = raw.app_metadata || {};
    const roles = rolesOf(raw);
    const text = value => (typeof value === 'string' && value ? value : null);
    return {
      id: String(raw.id),
      email: text(raw.email),
      name: text(meta.full_name) || text(meta.name),
      picture: text(meta.avatar_url),
      provider: text(app.provider),
      createdAt: text(raw.created_at),
      roles,
      status: statusOf(roles),
    };
  }

  const ORDER = { pending: 0, admin: 1, member: 2 };

  // 승인 대기를 맨 위(먼저 요청한 순), 그다음 관리자, 회원(이메일순).
  function sortMembers(list) {
    return [...list].sort((a, b) => {
      const gap = (ORDER[a.status] ?? 9) - (ORDER[b.status] ?? 9);
      if (gap) return gap;
      if (a.status === 'pending') return String(a.createdAt || '').localeCompare(String(b.createdAt || ''));
      return String(a.email || '').localeCompare(String(b.email || ''));
    });
  }

  function counts(list) {
    const out = { pending: 0, member: 0, admin: 0, total: list.length };
    for (const item of list) if (item.status in out) out[item.status] += 1;
    return out;
  }

  function cookieValue(header, name) {
    if (typeof header !== 'string' || !header) return null;
    for (const part of header.split(';')) {
      const index = part.indexOf('=');
      if (index < 0 || part.slice(0, index).trim() !== name) continue;
      const value = part.slice(index + 1).trim();
      try {
        return decodeURIComponent(value);
      } catch (error) {
        return value;
      }
    }
    return null;
  }

  function bearer(header) {
    const match = /^Bearer\s+(\S+)$/i.exec(typeof header === 'string' ? header.trim() : '');
    return match ? match[1] : null;
  }

  // 로그인 뒤 돌아갈 주소. 같은 사이트 안의 경로만 허용하고 로그인 화면 자체로는 돌려보내지 않는다.
  function safeNext(path) {
    if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//') || path.includes('\\')) return '/';
    if (/[\u0000-\u001f]/.test(path)) return '/';
    if (path === '/auth' || path.startsWith('/auth/') || path.startsWith('/.netlify/')) return '/';
    return path;
  }

  function dateLabel(value) {
    const time = Date.parse(value || '');
    if (!Number.isFinite(time)) return '—';
    const kst = new Date(time + 9 * 3600 * 1000);
    const pad = number => String(number).padStart(2, '0');
    return `${kst.getUTCFullYear()}-${pad(kst.getUTCMonth() + 1)}-${pad(kst.getUTCDate())} ${pad(kst.getUTCHours())}:${pad(kst.getUTCMinutes())}`;
  }

  return {
    MEMBER, ADMIN, ACTIONS,
    isUuid, decodeJwt, rolesOf, isAdmin, canView, statusOf, access, needsRefresh, parseCallback,
    nextRoles, refusal, publicUser, sortMembers, counts,
    cookieValue, bearer, safeNext, dateLabel,
  };
});
