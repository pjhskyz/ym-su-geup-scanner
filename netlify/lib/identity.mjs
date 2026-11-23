// Netlify Function 공용: 요청을 보낸 사람이 누구인지 Netlify Identity 에 직접 물어 확인한다.
// 토큰 서명을 여기서 검증할 수 없으므로(비밀키는 Netlify 가 보관) 쿠키 내용만 믿지 않고
// Identity 의 /user 응답을 기준으로 삼는다. 응답은 토큰별로 잠깐만 기억해 호출 수를 줄인다.
import L from "../../auth/auth_logic.js";

const REMEMBER_MS = 5 * 60 * 1000;
const REMEMBER_MAX = 200;
const TIMEOUT_MS = 8000;
const remembered = new Map();

export const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });

// Identity 주소와 관리용 토큰. 관리용 토큰은 Identity 가 켜진 사이트의 Function 실행 환경에서만 주어진다.
export function identityOf(req) {
  const given = globalThis.netlifyIdentityContext || {};
  const url = typeof given.url === "string" && given.url ? given.url : new URL("/.netlify/identity", req.url).href;
  return { url: url.replace(/\/+$/, ""), token: typeof given.token === "string" && given.token ? given.token : null };
}

export function tokenOf(req) {
  return L.bearer(req.headers.get("authorization")) || L.cookieValue(req.headers.get("cookie"), "nf_jwt");
}

export async function call(url, options = {}, fetcher = fetch) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    return await fetcher(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

// 로그인한 사용자를 돌려준다. 로그인하지 않았거나 확인에 실패하면 null.
// fresh: true 면 기억해 둔 응답을 쓰지 않고 매번 Identity 에 다시 묻는다(권한을 바꾸는 요청용).
export async function currentUser(req, { fresh = false, fetcher = fetch, now = Date.now } = {}) {
  const token = tokenOf(req);
  const claims = L.decodeJwt(token);
  if (!claims || typeof claims.exp !== "number" || claims.exp * 1000 <= now()) return null;

  const kept = remembered.get(token);
  if (!fresh && kept && kept.until > now()) return kept.user;

  let user = null;
  try {
    const response = await call(`${identityOf(req).url}/user`, { headers: { Authorization: `Bearer ${token}` } }, fetcher);
    if (response.ok) user = L.publicUser(await response.json());
  } catch (error) {
    user = null;
  }
  if (!user) {
    remembered.delete(token);
    return null;
  }
  if (remembered.size >= REMEMBER_MAX) remembered.clear();
  remembered.set(token, { user, until: Math.min(now() + REMEMBER_MS, claims.exp * 1000) });
  return user;
}

export function forget() {
  remembered.clear();
}

// 다른 사이트에서 보낸 요청이면 이유를, 같은 사이트면 null 을 돌려준다.
export function crossOrigin(req) {
  const origin = req.headers.get("origin");
  if (!origin) return "요청 출처를 확인할 수 없습니다.";
  return origin === new URL(req.url).origin ? null : "다른 사이트에서 보낸 요청은 받지 않습니다.";
}
