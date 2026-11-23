// Netlify Function 쪽 로그인 확인·회원 승인 처리 검증 (Identity 응답은 가짜로 대신한다).
import test from "node:test";
import assert from "node:assert/strict";
import { crossOrigin, currentUser, forget, identityOf, tokenOf } from "./netlify/lib/identity.mjs";
import members from "./netlify/functions/members.mjs";
import kisNight from "./netlify/functions/kis-night.mjs";

const SITE = "https://scanner.example";
const ROY = "3f2b1c9e-7a44-4c1d-9b0e-5d6f7a8b9c0d";
const STAFF = "11111111-2222-4333-8444-555555555555";
const jwt = (sub, exp = Math.floor(Date.now() / 1000) + 600) =>
  ["x", Buffer.from(JSON.stringify({ sub, exp })).toString("base64url"), "sig"].join(".");
const reply = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const request = (path, { token, cookie, method = "GET", body, origin } = {}) => {
  const headers = {};
  if (token) headers.authorization = `Bearer ${token}`;
  if (cookie) headers.cookie = cookie;
  if (origin) headers.origin = origin;
  if (body) headers["content-type"] = "application/json";
  return new Request(SITE + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
};

// 가짜 Identity: 토큰의 sub 로 사용자를 찾아 주고, 관리 API 는 관리용 토큰일 때만 응답한다.
function fakeIdentity(users) {
  const calls = [];
  const realFetch = globalThis.fetch;
  globalThis.netlifyIdentityContext = { url: `${SITE}/.netlify/identity`, token: "operator" };
  globalThis.fetch = async (url, options = {}) => {
    const path = String(url).replace(`${SITE}/.netlify/identity`, "");
    const auth = (options.headers && options.headers.Authorization) || "";
    calls.push(`${options.method || "GET"} ${path}`);
    if (path === "/user") {
      const sub = JSON.parse(Buffer.from(auth.split(".")[1] || "", "base64url").toString("utf8") || "{}").sub;
      return users[sub] && auth.endsWith(".sig") ? reply(users[sub]) : reply({ msg: "Invalid token" }, 401);
    }
    if (auth !== "Bearer operator") return reply({ msg: "not allowed" }, 401);
    if (path.startsWith("/admin/users?")) return reply({ users: Object.values(users) });
    const id = path.split("/")[3];
    if (!users[id]) return reply({ msg: "User not found" }, 404);
    if (options.method === "PUT") users[id] = { ...users[id], ...JSON.parse(options.body) };
    if (options.method === "DELETE") delete users[id];
    return reply(users[id] || {});
  };
  return { calls, restore: () => { globalThis.fetch = realFetch; delete globalThis.netlifyIdentityContext; forget(); } };
}

const people = () => ({
  [ROY]: { id: ROY, email: "roy@x.com", app_metadata: { provider: "google", roles: ["admin"] } },
  [STAFF]: { id: STAFF, email: "staff@x.com", app_metadata: { provider: "google" } },
});

test("토큰은 Authorization 헤더가 우선, 없으면 nf_jwt 쿠키", () => {
  assert.equal(tokenOf(request("/", { token: "h.h.h", cookie: "nf_jwt=c.c.c" })), "h.h.h");
  assert.equal(tokenOf(request("/", { cookie: "a=1; nf_jwt=c.c.c" })), "c.c.c");
  assert.equal(tokenOf(request("/")), null);
});

test("Identity 주소: 실행 환경이 준 값, 없으면 같은 사이트의 /.netlify/identity", () => {
  delete globalThis.netlifyIdentityContext;
  assert.deepEqual(identityOf(request("/.netlify/functions/members")), { url: `${SITE}/.netlify/identity`, token: null });
  globalThis.netlifyIdentityContext = { url: "https://id.example/identity/", token: "op" };
  assert.deepEqual(identityOf(request("/")), { url: "https://id.example/identity", token: "op" });
  delete globalThis.netlifyIdentityContext;
});

test("사용자 확인: Identity 가 인정한 토큰만, 만료 토큰은 묻지도 않음, 결과는 잠깐 기억", async () => {
  const identity = fakeIdentity(people());
  try {
    assert.equal((await currentUser(request("/", { cookie: `nf_jwt=${jwt(ROY)}` }))).email, "roy@x.com");
    assert.equal(await currentUser(request("/", { token: jwt(ROY).replace(/\.sig$/, ".forged") })), null);
    assert.equal(await currentUser(request("/", { token: jwt("nobody") })), null);
    const before = identity.calls.length;
    assert.equal(await currentUser(request("/", { token: jwt(ROY, Math.floor(Date.now() / 1000) - 1) })), null);
    assert.equal(await currentUser(request("/")), null);
    assert.equal(identity.calls.length, before);

    const token = jwt(STAFF);
    await currentUser(request("/", { token }));
    await currentUser(request("/", { token }));
    assert.equal(identity.calls.length, before + 1, "같은 토큰은 다시 묻지 않는다");
    await currentUser(request("/", { token }), { fresh: true });
    assert.equal(identity.calls.length, before + 2, "fresh 는 매번 다시 묻는다");
    await currentUser(request("/", { token }), { now: () => Date.now() + 6 * 60 * 1000 });
    assert.equal(identity.calls.length, before + 3, "기억해 두는 5분이 지나면 다시 묻는다");
    assert.equal(await currentUser(request("/", { token }), { now: () => Date.now() + 11 * 60 * 1000 }), null);
    assert.equal(identity.calls.length, before + 3, "토큰이 만료된 뒤에는 묻지 않고 거절");
  } finally {
    identity.restore();
  }
});

test("Identity 가 응답하지 않으면 로그인하지 않은 것으로 본다", async () => {
  const failing = async () => { throw new Error("network"); };
  assert.equal(await currentUser(request("/", { token: jwt(ROY) }), { fetcher: failing }), null);
  forget();
});

test("다른 사이트에서 온 요청 구분", () => {
  assert.equal(crossOrigin(request("/", { origin: SITE })), null);
  assert.match(crossOrigin(request("/", { origin: "https://evil.example" })), /다른 사이트/);
  assert.match(crossOrigin(request("/")), /출처/);
});

test("회원 Function: 로그인 전 401, 일반 회원·대기자 403", async () => {
  const users = people();
  users[STAFF].app_metadata.roles = ["member"];
  const identity = fakeIdentity(users);
  try {
    assert.equal((await members(request("/.netlify/functions/members"))).status, 401);
    assert.equal((await members(request("/.netlify/functions/members", { token: jwt(STAFF) }))).status, 403);
    const attempt = await members(request("/.netlify/functions/members", { token: jwt(STAFF), method: "POST", origin: SITE, body: { id: STAFF, action: "promote" } }));
    assert.equal(attempt.status, 403);
    assert.deepEqual(users[STAFF].app_metadata.roles, ["member"]);
    assert.ok(!identity.calls.some(call => call.includes("/admin/")), "권한이 없으면 관리 API 를 부르지 않는다");
  } finally {
    identity.restore();
  }
});

test("회원 Function: 관리자는 목록을 받고 승인·해제·삭제할 수 있다", async () => {
  const users = people();
  const identity = fakeIdentity(users);
  const call = (options = {}) => members(request("/.netlify/functions/members", { token: jwt(ROY), ...options }));
  const post = body => call({ method: "POST", origin: SITE, body });
  try {
    const list = await (await call()).json();
    assert.deepEqual(list.members.map(item => [item.email, item.status]), [["staff@x.com", "pending"], ["roy@x.com", "admin"]]);
    assert.deepEqual(list.counts, { pending: 1, member: 0, admin: 1, total: 2 });
    assert.deepEqual(list.me, { id: ROY, email: "roy@x.com" });

    const approved = await (await post({ id: STAFF, action: "approve" })).json();
    assert.equal(approved.member.status, "member");
    assert.deepEqual(users[STAFF].app_metadata, { provider: "google", roles: ["member"] }, "기존 값(provider)은 보존");

    await post({ id: STAFF, action: "promote" });
    assert.deepEqual(users[STAFF].app_metadata.roles, ["member", "admin"]);
    await post({ id: STAFF, action: "demote" });
    assert.deepEqual(users[STAFF].app_metadata.roles, ["member"]);
    await post({ id: STAFF, action: "revoke" });
    assert.deepEqual(users[STAFF].app_metadata.roles, []);

    assert.equal((await post({ id: ROY, action: "demote" })).status, 400);
    assert.equal((await post({ id: ROY, action: "remove" })).status, 400);
    assert.deepEqual(users[ROY].app_metadata.roles, ["admin"]);
    assert.equal((await post({ id: "not-a-uuid", action: "approve" })).status, 400);
    assert.equal((await post({ id: STAFF, action: "grant" })).status, 400);
    assert.equal((await call({ method: "POST", origin: SITE })).status, 400);
    assert.equal((await post({ id: "99999999-9999-4999-8999-999999999999", action: "approve" })).status, 404);
    assert.equal((await call({ method: "POST", origin: "https://evil.example", body: { id: STAFF, action: "approve" } })).status, 403);
    assert.equal((await call({ method: "POST", body: { id: STAFF, action: "approve" } })).status, 403);
    assert.equal((await call({ method: "DELETE" })).status, 405);
    assert.deepEqual(users[STAFF].app_metadata.roles, [], "거절된 요청은 아무 것도 바꾸지 않는다");

    const removed = await (await post({ id: STAFF, action: "remove" })).json();
    assert.equal(removed.member, null);
    assert.equal(users[STAFF], undefined);
  } finally {
    identity.restore();
  }
});

test("회원 Function: 관리자 권한이 회수되면 예전 토큰으로도 거절", async () => {
  const users = people();
  const identity = fakeIdentity(users);
  try {
    const token = jwt(ROY);
    assert.equal((await members(request("/.netlify/functions/members", { token }))).status, 200);
    users[ROY].app_metadata.roles = ["member"];
    assert.equal((await members(request("/.netlify/functions/members", { token }))).status, 403);
  } finally {
    identity.restore();
  }
});

test("회원 Function: 관리용 토큰이 없으면 503 으로 알린다", async () => {
  const identity = fakeIdentity(people());
  try {
    globalThis.netlifyIdentityContext = { url: `${SITE}/.netlify/identity` };
    const response = await members(request("/.netlify/functions/members", { token: jwt(ROY) }));
    assert.equal(response.status, 503);
    assert.match((await response.json()).error, /Identity/);
  } finally {
    identity.restore();
  }
});

test("야간선물 Function: 로그인 전·승인 대기는 401, 승인 회원만 통과", async () => {
  const users = people();
  const identity = fakeIdentity(users);
  const keys = [process.env.KIS_APPKEY, process.env.KIS_APPSECRET];
  delete process.env.KIS_APPKEY;
  delete process.env.KIS_APPSECRET;
  try {
    assert.equal((await kisNight(request("/.netlify/functions/kis-night"))).status, 401);
    assert.equal((await kisNight(request("/.netlify/functions/kis-night", { cookie: `nf_jwt=${jwt(STAFF)}` }))).status, 401);
    const passed = await kisNight(request("/.netlify/functions/kis-night", { cookie: `nf_jwt=${jwt(ROY)}` }));
    assert.equal(passed.status, 500, "회원 확인을 통과하면 다음 단계(환경변수 확인)까지 간다");
    assert.match((await passed.json()).error, /KIS_APPKEY/);
  } finally {
    if (keys[0]) process.env.KIS_APPKEY = keys[0];
    if (keys[1]) process.env.KIS_APPSECRET = keys[1];
    identity.restore();
  }
});
