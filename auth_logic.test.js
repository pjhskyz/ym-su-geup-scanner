const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const L = require('./auth/auth_logic.js');

const jwt = claims => ['x', Buffer.from(JSON.stringify(claims)).toString('base64url'), 'sig'].join('.');
const ID = '3f2b1c9e-7a44-4c1d-9b0e-5d6f7a8b9c0d';
const OTHER = '11111111-2222-4333-8444-555555555555';

test('토큰 해석: 한글 이름과 잘못된 입력', () => {
  const claims = L.decodeJwt(jwt({ exp: 10, email: 'a@b.c', user_metadata: { full_name: '로이' } }));
  assert.equal(claims.user_metadata.full_name, '로이');
  for (const bad of [null, undefined, '', 'a.b', 'a.b.c', 'a.' + Buffer.from('[1]').toString('base64url') + '.c', 42]) {
    assert.equal(L.decodeJwt(bad), null);
  }
});

test('권한 읽기: 토큰(app_metadata)·사용자 객체 모두, 중복과 잘못된 값 제거', () => {
  assert.deepEqual(L.rolesOf({ app_metadata: { provider: 'google', roles: ['member', 'member', '', 3] } }), ['member']);
  assert.deepEqual(L.rolesOf({ appMetadata: { roles: ['admin'] } }), ['admin']);
  assert.deepEqual(L.rolesOf({ roles: ['member'] }), ['member']);
  assert.deepEqual(L.rolesOf({ app_metadata: { roles: 'admin' } }), []);
  assert.deepEqual(L.rolesOf(null), []);
});

test('접근 상태: 만료·승인 대기·회원·관리자', () => {
  const now = 1000;
  assert.equal(L.access(null, now), 'anonymous');
  assert.equal(L.access({ exp: 1000, app_metadata: { roles: ['admin'] } }, now), 'anonymous');
  assert.equal(L.access({ app_metadata: { roles: ['admin'] } }, now), 'anonymous');
  assert.equal(L.access({ exp: 1001, app_metadata: { provider: 'google' } }, now), 'pending');
  assert.equal(L.access({ exp: 1001, app_metadata: { roles: ['guest'] } }, now), 'pending');
  assert.equal(L.access({ exp: 1001, app_metadata: { roles: ['member'] } }, now), 'member');
  assert.equal(L.access({ exp: 1001, app_metadata: { roles: ['member', 'admin'] } }, now), 'admin');
  assert.equal(L.access({ exp: 1001, app_metadata: { roles: ['admin'] } }, now), 'admin');
});

test('토큰 갱신 시점: 없거나 만료됐거나 여유 시간 이하로 남았을 때', () => {
  assert.equal(L.needsRefresh(null, 1000, 300), true);
  assert.equal(L.needsRefresh({}, 1000, 300), true);
  assert.equal(L.needsRefresh({ exp: 900 }, 1000, 300), true);
  assert.equal(L.needsRefresh({ exp: 1300 }, 1000, 300), true);
  assert.equal(L.needsRefresh({ exp: 1301 }, 1000, 300), false);
  assert.equal(L.needsRefresh({ exp: 4600 }, 1000, 300), false);
});

test('구글 로그인에서 돌아온 주소 읽기: 토큰·거절·무관한 주소', () => {
  assert.deepEqual(L.parseCallback('#access_token=a.b.c&expires_in=3600&refresh_token=r1&token_type=bearer'),
    { tokens: { access_token: 'a.b.c', refresh_token: 'r1' } });
  assert.deepEqual(L.parseCallback('access_token=a.b.c'), { tokens: { access_token: 'a.b.c', refresh_token: '' } });
  assert.deepEqual(L.parseCallback('#error=access_denied&error_description=User+denied+access'), { error: 'User denied access' });
  assert.deepEqual(L.parseCallback('#error=server_error'), { error: 'server_error' });
  for (const other of ['', '#', '#section', '#invite_token=abc', '#recovery_token=abc', null, undefined]) {
    assert.equal(L.parseCallback(other), null);
  }
});

test('권한 변경: 승인·해제·관리자 지정·해제, 다른 권한은 보존', () => {
  assert.deepEqual(L.nextRoles([], 'approve'), ['member']);
  assert.deepEqual(L.nextRoles(['member'], 'approve'), ['member']);
  assert.deepEqual(L.nextRoles(['admin'], 'approve'), ['member', 'admin']);
  assert.deepEqual(L.nextRoles(['beta', 'member', 'admin'], 'revoke'), ['beta']);
  assert.deepEqual(L.nextRoles(['member'], 'promote'), ['member', 'admin']);
  assert.deepEqual(L.nextRoles([], 'promote'), ['member', 'admin']);
  assert.deepEqual(L.nextRoles(['admin'], 'demote'), ['member']);
  assert.deepEqual(L.nextRoles(['beta', 'member', 'admin'], 'demote'), ['beta', 'member']);
  assert.throws(() => L.nextRoles([], 'remove'));
  assert.throws(() => L.nextRoles([], 'nope'));
});

test('요청 거절 사유: 동작·ID 형식·본인 권한 낮추기', () => {
  assert.equal(L.refusal({ actorId: ID, targetId: OTHER, action: 'approve' }), null);
  assert.equal(L.refusal({ actorId: ID, targetId: OTHER, action: 'remove' }), null);
  assert.match(L.refusal({ actorId: ID, targetId: OTHER, action: 'grant' }), /알 수 없는/);
  assert.match(L.refusal({ actorId: ID, targetId: '../admin', action: 'approve' }), /형식/);
  assert.match(L.refusal({ actorId: ID, targetId: undefined, action: 'approve' }), /형식/);
  assert.match(L.refusal(undefined), /알 수 없는/);
  for (const action of ['revoke', 'demote', 'remove']) assert.match(L.refusal({ actorId: ID, targetId: ID, action }), /본인/);
  assert.equal(L.refusal({ actorId: ID, targetId: ID, action: 'approve' }), null);
});

test('회원 목록: 필요한 항목만 추리고 대기 → 관리자 → 회원 순', () => {
  const raw = [
    { id: 'c', email: 'b@x.com', created_at: '2026-11-01T00:00:00Z', app_metadata: { roles: ['member'] } },
    { id: 'a', email: 'new2@x.com', created_at: '2026-11-22T00:00:00Z', app_metadata: { provider: 'google' }, user_metadata: { full_name: '둘', avatar_url: 'https://x/p.png' } },
    { id: 'd', email: 'a@x.com', created_at: '2026-11-02T00:00:00Z', app_metadata: { roles: ['member'] } },
    { id: 'b', email: 'new1@x.com', created_at: '2026-11-21T00:00:00Z' },
    { id: 'e', email: 'roy@x.com', app_metadata: { roles: ['admin'] }, encrypted_password: 'x', recovery_token: 'secret' },
    { email: 'no-id@x.com' },
    null,
  ];
  const list = L.sortMembers(raw.map(L.publicUser).filter(Boolean));
  assert.deepEqual(list.map(item => item.email), ['new1@x.com', 'new2@x.com', 'roy@x.com', 'a@x.com', 'b@x.com']);
  assert.deepEqual(list.map(item => item.status), ['pending', 'pending', 'admin', 'member', 'member']);
  assert.deepEqual(L.counts(list), { pending: 2, member: 2, admin: 1, total: 5 });
  assert.deepEqual(Object.keys(list[2]).sort(), ['createdAt', 'email', 'id', 'name', 'picture', 'provider', 'roles', 'status']);
  assert.equal(list[1].name, '둘');
  assert.equal(list[1].provider, 'google');
});

test('요청 헤더 읽기: 쿠키와 Bearer', () => {
  assert.equal(L.cookieValue('a=1; nf_jwt=abc.def.ghi; b=2', 'nf_jwt'), 'abc.def.ghi');
  assert.equal(L.cookieValue('xnf_jwt=no; nf_jwt=a%2Eb', 'nf_jwt'), 'a.b');
  assert.equal(L.cookieValue('nf_jwt=%E0%A4%A', 'nf_jwt'), '%E0%A4%A');
  assert.equal(L.cookieValue('', 'nf_jwt'), null);
  assert.equal(L.cookieValue(null, 'nf_jwt'), null);
  assert.equal(L.bearer('Bearer abc.def'), 'abc.def');
  assert.equal(L.bearer('bearer   abc'), 'abc');
  assert.equal(L.bearer('Basic abc'), null);
  assert.equal(L.bearer('Bearer a b'), null);
  assert.equal(L.bearer(null), null);
});

test('로그인 뒤 돌아갈 주소: 사이트 안 경로만', () => {
  assert.equal(L.safeNext('/?tab=r-trade'), '/?tab=r-trade');
  assert.equal(L.safeNext('/admin.html'), '/admin.html');
  for (const bad of ['//evil.example', 'https://evil.example', '/\\evil', 'javascript:alert(1)', '/auth/login.html', '/auth', '/.netlify/identity/x', '', null, '/a\nb']) {
    assert.equal(L.safeNext(bad), '/');
  }
});

test('표시 시각은 한국 시간', () => {
  assert.equal(L.dateLabel('2026-11-23T07:59:00Z'), '2026-11-23 16:59');
  assert.equal(L.dateLabel('2026-12-31T15:30:00Z'), '2027-01-01 00:30');
  assert.equal(L.dateLabel(null), '—');
  assert.equal(L.dateLabel('nope'), '—');
});

test('접근 규칙 파일: 공개 범위는 로그인 화면뿐이고 마지막은 로그인 화면(401)', () => {
  const rules = fs.readFileSync(__dirname + '/_redirects', 'utf8').split('\n')
    .map(line => line.trim()).filter(line => line && !line.startsWith('#')).map(line => line.split(/\s+/));
  const open = rules.filter(rule => rule[2].startsWith('200') && !rule.some(part => part.startsWith('Role=')));
  assert.deepEqual(open.map(rule => rule[0]), ['/auth/*']);
  assert.ok(rules.every(rule => rule[2].endsWith('!')), '모든 규칙은 강제(!)여야 파일이 있어도 적용된다');
  assert.deepEqual(rules.at(-1), ['/*', '/auth/login.html', '401!']);
  assert.deepEqual(rules.at(-2), ['/*', '/:splat', '200!', 'Role=member,admin']);
  const proxy = rules.find(rule => rule[0] === '/yf/*');
  assert.ok(proxy.includes('Role=member,admin'));
  for (const file of ['login.html', 'login.js', 'auth.js', 'auth_logic.js', 'auth.css', 'session.js', 'session.css']) assert.ok(fs.existsSync(`${__dirname}/auth/${file}`), file);
});

test('로그인 화면은 공개 경로(/auth/)의 파일만 불러온다', () => {
  const html = fs.readFileSync(__dirname + '/auth/login.html', 'utf8');
  const local = [...html.matchAll(/(?:src|href)="([^"]+)"/g)].map(match => match[1]).filter(url => !/^(https?:|data:)/.test(url));
  assert.ok(local.length >= 3);
  for (const url of local) assert.ok(url === '/' || url.startsWith('/auth/'), url);
});
