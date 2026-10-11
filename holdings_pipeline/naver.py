"""Read the publicly displayed Npay ETF portfolio without assuming history.

ETFComponent is the full-portfolio modal's feed. ``startIdx`` is a zero-based
PAGE NUMBER, not a row offset. The provider date belongs to the rows; neither
the observation date nor the ETF quote date is a portfolio date.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import urllib.error
import urllib.request
from typing import Callable


SOURCE = "NAVER"
BASE_URL = "https://stock.naver.com/api/domestic/detail"
MAX_PAGES = 30
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class SourceBlockedError(RuntimeError):
    """The caller must stop further requests to this source for this run."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.source = SOURCE


def _get_json(url: str):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "RoyETFMonitor/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 429):
            raise SourceBlockedError(f"NAVER HTTP {exc.code}", exc.code) from exc
        raise
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("NAVER response exceeds size limit")
    text = body.decode("utf-8-sig")
    if any(marker in text[:3000].lower() for marker in (
        "captcha", "access denied", "too many requests", "비정상적인 접근",
    )):
        raise SourceBlockedError("NAVER access challenge")
    return json.loads(text)


def _number(value):
    if value is None or value == "":
        return None
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value):
    number = _number(value)
    return int(number) if number is not None and number >= 0 and number.is_integer() else None


def _iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _asset_type(row: dict, etf_codes: set[str]):
    code = str(row.get("componentItemCode") or "").strip().upper()
    isin = str(row.get("componentIsinCode") or "").strip().upper()
    name = str(row.get("componentName") or "")
    if code and code in etf_codes:
        return "fund"
    if str(row.get("componentMarketType")) == "4":
        return "derivative"
    if not code and ("현금" in name or name.upper() in ("CASH", "KRW CASH", "USD CASH")):
        return "cash"
    # KR7 is the domestic securities ISIN prefix. The embedded issue code must
    # also agree. Registered ETF codes take precedence above, even with KR7.
    if (
        re.fullmatch(r"[0-9A-Z]{6}", code)
        and re.fullmatch(r"KR7[0-9A-Z]{9}", isin)
        and isin[3:9] == code
        and not row.get("componentReutersCode")
        and str(row.get("componentMarketType")) in ("0", "1")
    ):
        return "equity"
    return "other"


def normalize_rows(asset: dict, rows: list, etf_codes: set[str], *,
                   source_url: str, observed_at: str, errors: list[str] | None = None):
    """Normalize full raw rows and conservatively flag incomplete portfolios."""
    issues = list(errors or [])
    symbol = str(asset.get("symbol") or asset["id"].split(":")[-1]).upper()
    codes = {str(code).split(":")[-1].upper() for code in etf_codes}
    clean = [row for row in rows if isinstance(row, dict)]
    if len(clean) != len(rows):
        issues.append("malformed_row")
    if not clean:
        issues.append("empty_portfolio")

    dates = {_iso_date(row.get("referenceDate")) for row in clean}
    actual_date = next(iter(dates)) if len(dates) == 1 and None not in dates else None
    if not actual_date:
        issues.append("missing_or_inconsistent_reference_date")
    counts = {_integer(row.get("componentCount")) for row in clean}
    reported_count = next(iter(counts)) if len(counts) == 1 and None not in counts else None
    if reported_count is None or reported_count != len(clean):
        issues.append("missing_or_inconsistent_component_count")

    holdings, keys = [], set()
    for row in clean:
        if str(row.get("itemCode") or "").upper() != symbol:
            issues.append("etf_identity_mismatch")
        code = str(row.get("componentItemCode") or "").strip().upper() or None
        isin = str(row.get("componentIsinCode") or "").strip().upper() or None
        reuters_code = str(row.get("componentReutersCode") or "").strip() or None
        key = isin or code or reuters_code
        if not key:
            issues.append("missing_constituent_identity")
        elif key in keys:
            issues.append("duplicate_constituent_identity")
        keys.add(key)
        weight = _number(row.get("weight"))
        if weight is None:
            issues.append("missing_or_invalid_weight")
        holdings.append({
            "code": code,
            "isin": isin,
            "name": str(row.get("componentName") or ""),
            "asset_type": _asset_type(row, codes),
            "weight_pct": weight,
            "quantity": _number(row.get("cuUnitQuantity")),
        })

    return {
        "etf_id": asset.get("id") or f"KR:{symbol}",
        "date": actual_date,
        "date_kind": "provider_date",
        "basis": "provider_holdings",
        "weight_basis": "provider_reported_percentage",
        "source": SOURCE,
        "source_url": source_url,
        "observed_at": observed_at,
        "collection_mode": "live",
        "complete": not issues,
        "reported_count": reported_count,
        "creation_unit_shares": None,
        "quantity_basis": "per_cu",
        "holdings": holdings,
        "validation_errors": sorted(set(issues)),
        "weight_sum_pct": round(sum(h["weight_pct"] for h in holdings if h["weight_pct"] is not None), 8),
        "quantity_note": "공급자 CU당 수량. CU 좌수 미확인으로 수량 변화·매수 판단에 사용하지 않음.",
    }


def fetch_snapshot(asset: dict, etf_codes: set[str], *,
                   fetch_json: Callable | None = None, observed_at: str | None = None,
                   page_size: int = 100):
    """Collect one ETF, leaving cross-ETF pacing/circuit handling to the caller.

    No historical date parameter exists in the verified public-page API.
    A same-date repeat is a repeat observation, not another trading session.
    """
    if not isinstance(page_size, int) or not 1 <= page_size <= 100:
        raise ValueError("page_size must be between 1 and 100")
    get_json = fetch_json or _get_json
    observed_at = observed_at or dt.datetime.now(dt.timezone.utc).isoformat()
    symbol = str(asset.get("symbol") or asset["id"].split(":")[-1]).upper()
    if not re.fullmatch(r"[0-9A-Z]{6}", symbol):
        raise ValueError("invalid domestic ETF code")
    root_url = f"{BASE_URL}/{symbol}/ETFComponent"
    first_url = f"{root_url}?startIdx=0&pageSize={page_size}"
    rows, errors, urls = [], [], []
    declared_count = None
    for page in range(MAX_PAGES):
        url = f"{root_url}?startIdx={page}&pageSize={page_size}"
        batch = get_json(url)
        urls.append(url)
        if not isinstance(batch, list):
            errors.append("response_is_not_array")
            break
        if not batch:
            if not rows or declared_count is None or len(rows) < declared_count:
                errors.append("page_ended_before_declared_count")
            break
        if declared_count is None and isinstance(batch[0], dict):
            declared_count = _integer(batch[0].get("componentCount"))
        rows.extend(batch)
        if declared_count is None:
            errors.append("missing_component_count")
            break
        if len(rows) >= declared_count:
            break
        if len(batch) < page_size:
            errors.append("short_page_before_declared_count")
            break
    else:
        errors.append("pagination_limit_reached")
    snapshot = normalize_rows(asset, rows, etf_codes, source_url=first_url,
                              observed_at=observed_at, errors=errors)
    snapshot["page_count"] = len(urls)
    snapshot["source_urls"] = urls
    return snapshot
