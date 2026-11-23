// netlify/functions/members.mjs
// 회원 승인 관리 — 관리자만 호출할 수 있다.
//   GET  /.netlify/functions/members                     → 가입 요청·회원 목록
//   POST /.netlify/functions/members  {id, action}       → approve(승인) | revoke(승인 해제) |
//                                                          promote(관리자 지정) | demote(관리자 해제) | remove(삭제)
// 권한은 Netlify Identity 사용자 정보의 app_metadata.roles 에 저장되고,
// _redirects 의 Role 조건이 이 값을 보고 화면과 자료를 열어 준다.
import L from "../../auth/auth_logic.js";
import { call, crossOrigin, currentUser, identityOf, json } from "../lib/identity.mjs";

const PER_PAGE = 100;
const MAX_PAGES = 20;

async function admin(identity, path, options = {}) {
  const response = await call(`${identity.url}${path}`, {
    ...options,
    headers: { Authorization: `Bearer ${identity.token}`, "Content-Type": "application/json" },
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw Object.assign(new Error(detail.msg || `Identity 요청 실패 (HTTP ${response.status})`), { status: response.status });
  }
  return response;
}

async function listAll(identity) {
  const users = [];
  for (let page = 1; page <= MAX_PAGES; page += 1) {
    const body = await (await admin(identity, `/admin/users?page=${page}&per_page=${PER_PAGE}`)).json();
    const rows = Array.isArray(body.users) ? body.users : [];
    users.push(...rows);
    if (rows.length < PER_PAGE) return { users, truncated: false };
  }
  return { users, truncated: true };
}

async function change(identity, id, action) {
  const path = `/admin/users/${encodeURIComponent(id)}`;
  if (action === "remove") {
    await admin(identity, path, { method: "DELETE" });
    return null;
  }
  const current = await (await admin(identity, path)).json();
  const roles = L.nextRoles(L.rolesOf(current), action);
  const updated = await admin(identity, path, {
    method: "PUT",
    body: JSON.stringify({ app_metadata: { ...(current.app_metadata || {}), roles } }),
  });
  return L.publicUser(await updated.json());
}

export default async (req) => {
  const me = await currentUser(req, { fresh: true });
  if (!me) return json({ error: "로그인이 필요합니다." }, 401);
  if (!L.isAdmin(me.roles)) return json({ error: "관리자만 사용할 수 있습니다." }, 403);

  const identity = identityOf(req);
  if (!identity.token) {
    return json({ error: "Netlify Identity 관리 권한을 받지 못했습니다. 사이트에서 Identity 가 켜져 있는지 확인해 주세요." }, 503);
  }

  try {
    if (req.method === "GET") {
      const { users, truncated } = await listAll(identity);
      const members = L.sortMembers(users.map(L.publicUser).filter(Boolean));
      return json({ me: { id: me.id, email: me.email }, counts: L.counts(members), members, truncated });
    }

    if (req.method === "POST") {
      const blocked = crossOrigin(req);
      if (blocked) return json({ error: blocked }, 403);
      const body = await req.json().catch(() => null);
      const request = { actorId: me.id, targetId: body && body.id, action: body && body.action };
      const reason = L.refusal(request);
      if (reason) return json({ error: reason }, 400);
      const member = await change(identity, request.targetId, request.action);
      return json({ ok: true, action: request.action, id: request.targetId, member });
    }

    return json({ error: "지원하지 않는 요청입니다." }, 405);
  } catch (error) {
    const status = error.status === 404 ? 404 : 502;
    return json({ error: status === 404 ? "해당 회원을 찾을 수 없습니다." : String(error.message || error) }, status);
  }
};
