// netlify/functions/kis-night.mjs  (v3 — 전광판 기반 월물 자동 탐지)
// 한국투자증권(KIS) 야간선물 시세 중계 — appkey/secret 은 Netlify 환경변수에만 존재.
//
// 환경변수(필수): KIS_APPKEY, KIS_APPSECRET
// 호출: /.netlify/functions/kis-night              → 자동 탐지된 최근월물 시세
//       /.netlify/functions/kis-night?board=1      → 선물 전광판 원본 (상장 월물 확인)
//       /.netlify/functions/kis-night?probe=1      → 전광판+후보 코드 응답 현황 (진단용)
//       /.netlify/functions/kis-night?code=XXXX    → 특정 코드 강제 (탐지 우회)
//
// v2 변경: KIS는 존재하지 않는 종목코드에 '성공+빈 응답'을 주므로,
// 연도문자(두 가지 매핑 후보) × 분기월로 후보 코드를 생성해 순서대로 조회하고
// 값이 오는 첫 코드를 12시간 캐시한다 → 분기 만기 롤오버 자동 해결.

const BASE = "https://openapi.koreainvestment.com:9443";
let tok = { v: null, exp: 0 };
let resolved = { code: null, at: 0 };          // 자동 탐지된 월물 코드 캐시
let qCache = { key: "", at: 0, body: null };   // 시세 5초 마이크로캐시

async function getToken() {
  if (tok.v && Date.now() < tok.exp - 300000) return tok.v;
  const r = await fetch(BASE + "/oauth2/tokenP", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      grant_type: "client_credentials",
      appkey: process.env.KIS_APPKEY,
      appsecret: process.env.KIS_APPSECRET,
    }),
  });
  const j = await r.json();
  if (!j.access_token) {
    const msg = j.error_description || j.msg1 || ("HTTP " + r.status);
    const rate = String(j.error_code || "").includes("EGW00133");
    throw Object.assign(new Error("토큰 발급 실패: " + msg), { status: rate ? 503 : 502 });
  }
  tok = { v: j.access_token, exp: Date.now() + (parseInt(j.expires_in || 86400, 10) * 1000) };
  return tok.v;
}

function pick(o, keys) {
  for (const k of keys)
    if (o && o[k] !== undefined && o[k] !== "" && o[k] !== null) return o[k];
  return null;
}

// ---- 후보 월물 코드 생성 ----
function yearLetters(y) {
  // KRX 파생 연도문자: 기점·제외문자(I,O[,U]) 관례가 자료마다 달라 두 매핑을 모두 후보로.
  const base = 2006;
  const maps = ["ABCDEFGHJKLMNPQRSTUVWXYZ", "ABCDEFGHJKLMNPQRSTVWXYZ"]; // I,O 제외 / I,O,U 제외
  const out = new Set();
  for (const m of maps) {
    const i = y - base;
    if (i >= 0 && i < m.length) out.add(m[i]);
  }
  return [...out];
}
const monthChar = (m) => (m < 10 ? String(m) : { 10: "A", 11: "B", 12: "C" }[m]);

function candidates() {
  const kst = new Date(Date.now() + 9 * 36e5);
  const y = kst.getUTCFullYear(), mo = kst.getUTCMonth() + 1;
  const exp = [];
  for (const q of [3, 6, 9, 12]) if (q >= mo) exp.push([y, q]);
  exp.push([y + 1, 3], [y + 1, 6]);
  const list = [];
  for (const [yy, mm] of exp.slice(0, 3)) {          // 가까운 만기 3개
    for (const L of yearLetters(yy)) {
      list.push("101" + L + monthChar(mm) + "000");  // 야간/표준 8자리 (우선)
      list.push("101" + L + String(mm).padStart(2, "0")); // 주간 6자리 (폴백)
    }
  }
  return [...new Set(list)];
}

// ---- 선물 전광판에서 실제 상장 월물 코드 추출 (추측 불필요) ----
async function board(token) {
  const qs = new URLSearchParams({
    FID_COND_MRKT_DIV_CODE: "F",
    FID_COND_SCR_DIV_CODE: "20503",
    FID_COND_MRKT_CLS_CODE: "MKI",
  });
  const r = await fetch(
    BASE + "/uapi/domestic-futureoption/v1/quotations/display-board-futures?" + qs,
    {
      headers: {
        authorization: "Bearer " + token,
        appkey: process.env.KIS_APPKEY,
        appsecret: process.env.KIS_APPSECRET,
        tr_id: "FHPIF05030200",
        custtype: "P",
      },
    }
  );
  const j = await r.json();
  const rows = j.output || j.output1 || [];
  return { rows: Array.isArray(rows) ? rows : [rows], rt: j.rt_cd, msg: j.msg1 };
}
function codesFromRows(rows) {
  // K200 선물 코드: 101 + 연도문자 + (월2자리 | 월1자리+000) — 4번째 자리는 반드시 영문
  const out = [];
  for (const row of rows)
    for (const v of Object.values(row || {})) {
      const s = String(v).trim();
      if (/^101[A-Z](\d{2}|[0-9A-C]000)$/.test(s) && !out.includes(s)) out.push(s);
    }
  return out;
}

// ---- 시세 조회 ----
async function quote(token, code, div = "F") {
  const qs = new URLSearchParams({ FID_COND_MRKT_DIV_CODE: div, FID_INPUT_ISCD: code });
  const r = await fetch(
    BASE + "/uapi/domestic-futureoption/v1/quotations/inquire-price?" + qs,
    {
      headers: {
        authorization: "Bearer " + token,
        appkey: process.env.KIS_APPKEY,
        appsecret: process.env.KIS_APPSECRET,
        tr_id: "FHMIF10000000",
        custtype: "P",
      },
    }
  );
  const j = await r.json();
  if (j.rt_cd !== "0") return { px: null, msg: j.msg1 || j.msg_cd };
  const o = j.output1 || j.output || {};
  const px = parseFloat(pick(o, ["futs_prpr", "prpr", "stck_prpr"]));
  const ctrt = parseFloat(pick(o, ["futs_prdy_ctrt", "prdy_ctrt"]));
  const vrss = parseFloat(pick(o, ["futs_prdy_vrss", "prdy_vrss"]));
  if (isNaN(px) || px === 0) return { px: null, raw: o };
  let pv = null;
  if (!isNaN(ctrt) && ctrt !== 0) pv = px / (1 + ctrt / 100);
  else if (!isNaN(vrss)) pv = px - vrss;
  return { px, pv: pv != null ? +pv.toFixed(2) : null, ctrt: isNaN(ctrt) ? null : ctrt, raw: o };
}

export default async (req) => {
  const url = new URL(req.url);
  const force = (url.searchParams.get("code") || "").trim();
  const probe = url.searchParams.get("probe") === "1";
  const debug = url.searchParams.get("debug") === "1";
  const json = (b, s = 200) =>
    new Response(JSON.stringify(b), {
      status: s,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });

  if (!process.env.KIS_APPKEY || !process.env.KIS_APPSECRET)
    return json({ error: "환경변수 KIS_APPKEY / KIS_APPSECRET 미설정" }, 500);

  try {
    const token = await getToken();

    if (url.searchParams.get("board") === "1") {   // 진단: 전광판 원본 (필드 확인용)
      const b = await board(token);
      return json({ rt: b.rt, msg: b.msg, codes: codesFromRows(b.rows), rows: b.rows.slice(0, 8) });
    }

    if (probe) {                                   // 진단: 전광판 코드 + 생성 후보 응답 현황
      let pool = [];
      try { pool = codesFromRows((await board(token)).rows); } catch (e) {}
      for (const c of candidates()) if (!pool.includes(c)) pool.push(c);
      const out = [];
      for (const c of pool.slice(0, 20)) {
        const q = await quote(token, c);
        out.push({ code: c, px: q.px, msg: q.msg });
      }
      return json({ kst: new Date(Date.now() + 9 * 36e5).toISOString().slice(0, 19), probe: out });
    }

    // 코드 결정: 강제 > 캐시(12h) > 자동 탐지
    let code = force || (Date.now() - resolved.at < 12 * 36e5 ? resolved.code : null);
    let q = null;
    if (code) {
      q = await quote(token, code);
      if (q.px == null) { code = null; q = null; }   // 캐시된 코드가 죽었으면 재탐지
    }
    if (!code) {
      let pool = [];
      try { pool = codesFromRows((await board(token)).rows); } catch (e) {}  // 1순위: 전광판 실코드
      for (const c of candidates()) if (!pool.includes(c)) pool.push(c);     // 2순위: 생성 후보
      for (const c of pool) {
        const t = await quote(token, c);
        if (t.px != null) { code = c; q = t; break; }
      }
      if (!code) return json({ error: "유효한 월물 코드를 찾지 못했습니다 — ?board=1 로 전광판을 확인하세요" }, 502);
      resolved = { code, at: Date.now() };
    }

    // 5초 마이크로캐시
    if (!debug && qCache.key === code && Date.now() - qCache.at < 5000) return json(qCache.body);
    const kstT = new Date(Date.now() + 9 * 36e5).toISOString().slice(11, 19);
    const body = { px: q.px, pv: q.pv, ctrt: q.ctrt, code, night: code.length === 8, t: kstT };
    if (debug) body.raw = q.raw;
    qCache = { key: code, at: Date.now(), body };
    return json(body);
  } catch (e) {
    return json({ error: String(e.message || e) }, e.status || 500);
  }
};
