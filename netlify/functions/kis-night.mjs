// netlify/functions/kis-night.mjs
// 한국투자증권(KIS) 야간선물 시세 중계 — appkey/secret 은 Netlify 환경변수에만 존재.
//
// 환경변수(필수): KIS_APPKEY, KIS_APPSECRET
// 호출: /.netlify/functions/kis-night?code=101W9000        (야간선물 종목코드)
//       /.netlify/functions/kis-night?code=...&debug=1      (원시 응답 필드 확인용)
//
// 설계:
//  - 접근토큰(24h)은 웜 인스턴스 메모리에 캐시. KIS는 유효기간 내 재요청 시
//    같은 토큰을 돌려주므로 콜드스타트도 안전. 발급 레이트리밋(EGW00133)은 503 처리.
//  - 시세는 5초 마이크로캐시 — 다중 탭/새로고침이 KIS 호출 한도를 치지 않게.
//  - 시세 엔드포인트: /uapi/domestic-futureoption/v1/quotations/inquire-price
//    TR FHMIF10000000 (한투 공식 예제에서 검증). 야간은 야간 종목코드로 조회.

const BASE = "https://openapi.koreainvestment.com:9443";
let tok = { v: null, exp: 0 };
let qCache = { key: "", at: 0, body: null };

async function getToken() {
  if (tok.v && Date.now() < tok.exp - 300_000) return tok.v;
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
  for (const k of keys) {
    if (o && o[k] !== undefined && o[k] !== "" && o[k] !== null) return o[k];
  }
  return null;
}

export default async (req) => {
  const url = new URL(req.url);
  const code = (url.searchParams.get("code") || "101W9000").trim();
  const div = (url.searchParams.get("div") || "F").trim();
  const debug = url.searchParams.get("debug") === "1";
  const json = (body, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });

  if (!process.env.KIS_APPKEY || !process.env.KIS_APPSECRET)
    return json({ error: "환경변수 KIS_APPKEY / KIS_APPSECRET 미설정" }, 500);

  // 5초 마이크로캐시
  const ck = div + ":" + code;
  if (!debug && qCache.key === ck && Date.now() - qCache.at < 5000)
    return json(qCache.body);

  try {
    const token = await getToken();
    const qs = new URLSearchParams({
      FID_COND_MRKT_DIV_CODE: div,
      FID_INPUT_ISCD: code,
    });
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
    if (j.rt_cd !== "0")
      return json({ error: "KIS 응답: " + (j.msg1 || j.msg_cd || "실패"), code, ...(debug ? { raw: j } : {}) }, 502);

    const o = j.output1 || j.output || {};
    const px = parseFloat(pick(o, ["futs_prpr", "prpr", "stck_prpr", "optn_prpr"]));
    const ctrt = parseFloat(pick(o, ["futs_prdy_ctrt", "prdy_ctrt"]));
    const vrss = parseFloat(pick(o, ["futs_prdy_vrss", "prdy_vrss"]));
    if (isNaN(px))
      return json({ error: "가격 필드 미인식 — debug=1 로 필드 확인 필요", code, ...(debug ? { raw: o } : { fields: Object.keys(o) }) }, 502);

    // 전일 기준가: 등락률 우선, 없으면 대비값으로 역산
    let pv = null;
    if (!isNaN(ctrt) && ctrt !== 0) pv = px / (1 + ctrt / 100);
    else if (!isNaN(vrss)) pv = px - vrss;
    else if (!isNaN(ctrt)) pv = px;

    const kst = new Date(Date.now() + 9 * 36e5).toISOString().slice(11, 19);
    const body = { px, pv: pv != null ? +pv.toFixed(2) : null, ctrt: isNaN(ctrt) ? null : ctrt, code, t: kst, ...(debug ? { raw: o } : {}) };
    qCache = { key: ck, at: Date.now(), body };
    return json(body);
  } catch (e) {
    return json({ error: String(e.message || e) }, e.status || 500);
  }
};
