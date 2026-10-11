"""ETF net assets, kept separate from prices, market cap and basket history.

Only an AUM record with its own issuer date may be used for a weight-change
amount. Npay ETFBase mixes quote dates and does not publish an AUM date: its
totalNetAssets is retained as an explicitly undated size reference only.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

MAX_BYTES = 12 * 1024 * 1024
NAVER_BASE = "https://stock.naver.com/api/domestic/detail"
SAMSUNG_PRODUCT = re.compile(r"https://www\.samsungfund\.com/api/v1/kodex/product/([A-Za-z0-9]+)\.do$")
KOACT_PRODUCT = re.compile(r"https://www\.samsungactive\.co\.kr/api/v1/product/etf/([A-Za-z0-9]+)\.do$")


class AumSourceBlockedError(RuntimeError):
    def __init__(self, host, status_code=None):
        self.host, self.status_code = host, status_code
        super().__init__(f"AUM source restricted: {host} ({status_code or 'challenge'})")


def _iso(value):
    text = str(value or "")
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError("aum_invalid_date")
    return dt.date.fromisoformat(text).isoformat()


def _stamp(value=None):
    if value is None:
        return dt.datetime.now(dt.timezone.utc).isoformat()
    text = str(value)
    if re.fullmatch(r"\d{8}T\d{12}", text):
        return dt.datetime.strptime(text, "%Y%m%dT%H%M%S%f").replace(tzinfo=dt.timezone.utc).isoformat()
    stamp = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("aum_observation_timezone_missing")
    return stamp.isoformat()


def _positive(value):
    if isinstance(value, bool):
        raise ValueError("aum_invalid_number")
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("aum_invalid_number") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError("aum_invalid_number")
    return number


def _symbol(asset):
    symbol = str(asset.get("symbol") or str(asset.get("id", "")).removeprefix("KR:")).upper()
    if not re.fullmatch(r"[0-9A-Z]{6}", symbol) or asset.get("id", "KR:" + symbol) != "KR:" + symbol:
        raise ValueError("aum_invalid_etf_identity")
    return symbol


def normalize_samsung(asset, payload, *, source_url, observed_at=None,
                      collection_mode="live", expected_date=None):
    """KODEX / KoAct published net assets (억원), with product's own date.

    `nav` here is the site's 순자산 총액 field; `fP`/`F_P` is NAV per share.
    The rounded issuer amount retains a 1억원 reporting resolution.
    """
    symbol = _symbol(asset)
    match = SAMSUNG_PRODUCT.fullmatch(source_url) or KOACT_PRODUCT.fullmatch(source_url)
    if not match:
        raise ValueError("aum_unverified_source_url")
    product = payload.get("info", {}).get("product", {})
    if product.get("stkTicker") != symbol or str(product.get("fId")) != match.group(1):
        raise ValueError("aum_etf_identity_mismatch")
    day = _iso(product.get("gijunYMD"))
    if expected_date is not None and day != _iso(expected_date):
        raise ValueError("aum_date_mismatch")
    value = _positive(product.get("nav"))
    source = "KODEX" if SAMSUNG_PRODUCT.fullmatch(source_url) else "KOACT"
    return {"etf_id": "KR:" + symbol, "asof": day, "value_krw": round(value * 100_000_000),
            "metric": "net_assets", "currency": "KRW", "source": source,
            "source_url": source_url, "observed_at": _stamp(observed_at),
            "collection_mode": collection_mode, "status": "verified",
            "reported_value": product.get("nav"), "reported_unit": "KRW_100_million",
            "resolution_krw": 100_000_000, "date_field": "info.product.gijunYMD",
            "value_field": "info.product.nav", "fund_id": match.group(1),
            "note": "운용사 공시 순자산총액·억원 단위 반올림값"}


def normalize_naver(asset, payload, *, source_url, observed_at=None,
                    collection_mode="live"):
    """Retain explicitly named total net assets, NEVER assign another feed date."""
    symbol = _symbol(asset)
    if source_url != f"{NAVER_BASE}/{symbol}/ETFBase" or payload.get("itemCode") != symbol:
        raise ValueError("aum_etf_identity_mismatch")
    value = _positive(payload.get("totalNetAssets"))
    if not value.is_integer():
        raise ValueError("aum_naver_expected_krw_integer")
    return {"etf_id": "KR:" + symbol, "asof": None, "value_krw": int(value),
            "metric": "net_assets", "currency": "KRW", "source": "NAVER",
            "source_url": source_url, "observed_at": _stamp(observed_at),
            "collection_mode": collection_mode, "status": "undated",
            "reported_value": payload.get("totalNetAssets"), "reported_unit": "KRW",
            "resolution_krw": 1, "date_field": None, "value_field": "totalNetAssets",
            "note": "원천 기준일 미제공 · 조회 시점 참고 · 환산액 계산 제외"}


def _valid_record(record):
    if not isinstance(record, dict):
        raise ValueError("aum_record_must_be_object")
    if record.get("metric") != "net_assets" or record.get("currency") != "KRW":
        raise ValueError("aum_metric_or_currency_mismatch")
    symbol = _symbol({"id": record.get("etf_id")})
    _positive(record.get("value_krw"))
    if not record.get("observed_at"):
        raise ValueError("aum_observation_time_missing")
    _stamp(record.get("observed_at"))
    source, url = record.get("source"), record.get("source_url", "")
    if source == "NAVER":
        if url != f"{NAVER_BASE}/{symbol}/ETFBase" or record.get("status") != "undated":
            raise ValueError("aum_unverified_source_identity")
    elif source in {"KODEX", "KOACT"}:
        match = (SAMSUNG_PRODUCT if source == "KODEX" else KOACT_PRODUCT).fullmatch(url)
        if not match or record.get("fund_id") != match.group(1) or record.get("status") != "verified":
            raise ValueError("aum_unverified_source_identity")
    else:
        raise ValueError("aum_unknown_source")
    if record.get("status") == "verified":
        _iso(record.get("asof"))
    elif record.get("status") != "undated" or record.get("asof") is not None:
        raise ValueError("aum_invalid_status")
    return record


def save_record(history_dir, record):
    _valid_record(record)
    symbol = record["etf_id"].split(":")[-1]
    source = record["source"]
    if source not in {"NAVER", "KODEX", "KOACT"}:
        raise ValueError("aum_unknown_source")
    key = record["asof"] or "observed-" + re.sub(r"[^0-9A-Za-z]", "", record["observed_at"])
    path = Path(history_dir) / source / symbol / (key + ".json")
    if path.exists():
        prior = json.loads(path.read_text())
        if prior.get("observed_at", "") > record["observed_at"]:
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
    return True


def load_aum_history(history_dir):
    result = {}
    for path in sorted(Path(history_dir).rglob("*.json")):
        try:
            record = _valid_record(json.loads(path.read_text()))
        except (OSError, TypeError, ValueError, KeyError, AttributeError):
            continue
        key = record["asof"] or "observed:" + record["observed_at"]
        result.setdefault(record["etf_id"], {}).setdefault(record["source"], {})[key] = record
    return result


def import_archives(assets, archive_roots, history_dir):
    """Normalize existing raw responses; preserved provenance says archived."""
    lookup = {_symbol(asset): asset for asset in assets}
    statuses, seen = [], set()
    for root in archive_roots:
        for path in sorted(Path(root).rglob("*.meta.json")):
            try:
                meta = json.loads(path.read_text())
                url = meta.get("url", "")
                if not (SAMSUNG_PRODUCT.fullmatch(url) or KOACT_PRODUCT.fullmatch(url)):
                    continue
                if meta.get("status", 200) != 200:
                    continue
                body = path.with_name(path.name.replace(".meta.json", ".body")).read_bytes()
                digest = hashlib.sha256(body).hexdigest()
                if meta.get("sha256") and meta["sha256"] != digest:
                    raise ValueError("aum_raw_hash_mismatch")
                if digest in seen:
                    continue
                seen.add(digest)
                payload = json.loads(body)
                symbol = payload.get("info", {}).get("product", {}).get("stkTicker")
                if symbol not in lookup:
                    continue
                record = normalize_samsung(lookup[symbol], payload, source_url=url,
                                           observed_at=meta.get("retrieved_at"),
                                           collection_mode="archived_official_response")
                record["source_sha256"] = digest
                save_record(history_dir, record)
                statuses.append({"etf_id": record["etf_id"], "source": record["source"],
                                 "status": "archived", "asof": record["asof"]})
            except (OSError, TypeError, KeyError, ValueError) as exc:
                statuses.append({"source": "archive", "status": "failed", "error": str(exc)})
    return statuses


def _download_json(url, raw_dir):
    host = urlsplit(url).netloc
    request = urllib.request.Request(url, headers={"User-Agent": "RoyETFMonitor/1.0",
                                                  "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            body = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 429):
            raise AumSourceBlockedError(host, exc.code) from exc
        raise
    if len(body) > MAX_BYTES:
        raise ValueError("aum_response_size_limit")
    text = body.decode("utf-8-sig")
    if any(x in text[:5000].lower() for x in ("captcha", "access denied", "too many requests", "비정상적인 접근")):
        raise AumSourceBlockedError(host)
    payload = json.loads(text)
    observed = _stamp()
    stamp = re.sub(r"[^0-9A-Za-z]", "", observed)
    prefix = Path(raw_dir) / (stamp + "_" + hashlib.sha256(url.encode()).hexdigest()[:16])
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".body").write_bytes(body)
    prefix.with_suffix(".meta.json").write_text(json.dumps({"url": url, "retrieved_at": observed,
        "status": 200, "sha256": hashlib.sha256(body).hexdigest()}, ensure_ascii=False))
    return payload, observed, hashlib.sha256(body).hexdigest()


def collect_aum(assets, dates, history_dir, raw_dir, *, archive_roots=(),
                blocked_hosts=None, fetch_json=None, pause=0.15,
                kodex_archive_only=False):
    """Collect sizes and exact-date evidence. No historical API is fabricated.

    Dated caches remain valid historical evidence on a provider failure, but a
    cached read never becomes a new live observation. Undated Npay sizes are
    reference-only. Restriction responses open a host-wide circuit for this run.
    """
    from .issuers import KOACT
    assets = list(assets)
    dates = sorted({_iso(day) for day in dates})
    if not dates:
        raise ValueError("aum_dates_required")
    latest = dates[-1]
    blocked = set(blocked_hosts or ())
    statuses = import_archives(assets, archive_roots, history_dir) if archive_roots else []
    history = load_aum_history(history_dir)
    for asset in assets:
        symbol = _symbol(asset)
        if asset.get("market", "KR") != "KR":
            continue
        official_url = asset.get("issuer_quote_url", "")
        if not SAMSUNG_PRODUCT.fullmatch(official_url):
            official_url = (f"https://www.samsungactive.co.kr/api/v1/product/etf/{KOACT[symbol]}.do"
                            if symbol in KOACT else None)
        urls = [official_url] if official_url else []
        urls.append(f"{NAVER_BASE}/{symbol}/ETFBase")
        latest_dated = any(latest in records for records in history.get(asset["id"], {}).values())
        for url in urls:
            host = urlsplit(url).netloc
            source = "NAVER" if host == "stock.naver.com" else "KODEX" if host == "www.samsungfund.com" else "KOACT"
            if source == "NAVER" and latest_dated:
                continue
            item = {"etf_id": asset["id"], "source": source, "source_url": url,
                    "requested_dates": dates, "mode": "live"}
            if source != "NAVER" and latest in history.get(asset["id"], {}).get(source, {}):
                item.update(status="stored_history", mode="stored_history", asof=latest)
                statuses.append(item)
                continue
            if source == "KODEX" and kodex_archive_only:
                item.update(status="not_refreshed", mode="archive_only",
                            reason="official_price_archive_only")
                statuses.append(item)
                continue
            if host in blocked:
                item.update(status="not_attempted", reason="provider_circuit_open")
                statuses.append(item)
                continue
            try:
                if fetch_json:
                    payload = fetch_json(url)
                    observed, digest = _stamp(), hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
                else:
                    payload, observed, digest = _download_json(url, raw_dir)
                normalize = normalize_naver if source == "NAVER" else normalize_samsung
                record = normalize(asset, payload, source_url=url, observed_at=observed)
                record["source_sha256"] = digest
                if record["asof"] and record["asof"] > latest:
                    item.update(status="future_date", asof=record["asof"], reason="latest_completed_session_exceeded")
                else:
                    save_record(history_dir, record)
                    if record["asof"] == latest:
                        latest_dated = True
                    item.update(status=record["status"], asof=record["asof"], observed_at=observed)
            except AumSourceBlockedError as exc:
                blocked.add(exc.host)
                item.update(status="failed", reason="provider_access_restricted", error=str(exc))
            except Exception as exc:
                item.update(status="failed", error=str(exc))
            statuses.append(item)
            if pause:
                time.sleep(pause)
    return statuses
