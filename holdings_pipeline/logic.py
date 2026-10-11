"""Pure, conservative comparisons of dated ETF constituent snapshots.

Weights use percentage units, so 16.9 -> 17.0 is +0.1 percentage points.
Neither weight nor normalized quantity changes establish purchases or flows.
"""

from __future__ import annotations

import math
import re
from datetime import date


_CODE = re.compile(r"^[0-9A-Z]{6}$")
_QUANTITY_NOTE = "ETF 1좌당 구성 수량 비교. 분할·합병 미검증이며 실제 매매를 뜻하지 않습니다."


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _date(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _result(previous, current, status, reason):
    return {
        "status": status,
        "from_date": (previous or {}).get("date"),
        "to_date": (current or {}).get("date"),
        "date_kind": (current or {}).get("date_kind"),
        "reason": reason,
        "changes": [],
    }


def _equities(snapshot):
    rows = snapshot.get("holdings")
    if not isinstance(rows, list):
        return None, "구성종목 전체 목록이 없습니다."
    equities = {}
    for holding in rows:
        if not isinstance(holding, dict):
            return None, "구성종목 자료 형식이 올바르지 않습니다."
        if holding.get("asset_type") != "equity":
            continue
        code = str(holding.get("code", "")).strip().upper()
        if not _CODE.fullmatch(code):
            return None, "국내 주식 식별코드를 확인하지 못했습니다."
        if code in equities:
            return None, "같은 주식 코드가 중복되어 비교를 보류합니다."
        weight = _number(holding.get("weight_pct"))
        if weight is None or weight < 0 or weight > 100:
            return None, "일부 주식의 유효 비중이 없습니다."
        equities[code] = {**holding, "code": code, "weight_pct": weight}
    return equities, None


def _quantity_per_share(snapshot, holding):
    basis = snapshot.get("quantity_basis")
    if basis not in {"per_etf_share", "per_cu"}:
        return None
    divisor = 1.0
    if basis == "per_cu":
        divisor = _number(snapshot.get("creation_unit_shares"))
        if divisor is None or divisor <= 0:
            return None
    # Absence is zero only after the caller has checked complete snapshots.
    quantity = 0.0 if holding is None else _number(holding.get("quantity"))
    if quantity is None or quantity < 0:
        return None
    return quantity / divisor


def _quantity_change(previous, current, before, after):
    q0 = _quantity_per_share(previous, before)
    q1 = _quantity_per_share(current, after)
    if q0 is None or q1 is None:
        return None, "unavailable", "동일 기준의 ETF 1좌당 수량을 확인할 수 없습니다."
    if math.isclose(q0, q1, rel_tol=1e-10, abs_tol=1e-12):
        return 0.0, "unchanged", _QUANTITY_NOTE
    status = "increased" if q1 > q0 else "decreased"
    if q0 == 0:
        return None, status, "이전 구성 수량이 0이어서 증감률은 계산하지 않습니다. " + _QUANTITY_NOTE
    return round((q1 / q0 - 1) * 100, 10), status, _QUANTITY_NOTE


def compare_snapshots(previous: dict | None, current: dict | None,
                      threshold_pp: float = 0.05) -> dict:
    """Compare two complete snapshots; caller selects exact session dates.

    Observation-date pairs are accepted but remain explicitly labelled and
    cannot be combined with provider-date pairs. Missing or partial data never
    imply exits. Fund-total quantities are deliberately not compared because
    changes in ETF shares outstanding cannot be separated with these inputs.
    """
    threshold = _number(threshold_pp)
    if threshold is None or threshold <= 0:
        raise ValueError("threshold_pp must be a finite positive number")
    if not previous or not current:
        return _result(previous, current, "waiting", "비교할 두 기준일의 자료가 필요합니다.")
    for key in ("etf_id", "source", "basis", "weight_basis", "date_kind"):
        if not previous.get(key) or previous.get(key) != current.get(key):
            return _result(previous, current, "basis_mismatch", f"비교 기준이 다르거나 미확인입니다: {key}")
    if current.get("date_kind") not in {"provider_date", "observation_date"}:
        return _result(previous, current, "basis_mismatch", "지원하지 않는 기준일 구분입니다.")
    d0, d1 = _date(previous.get("date")), _date(current.get("date"))
    if d0 is None or d1 is None:
        return _result(previous, current, "incomplete", "유효한 비교 기준일이 없습니다.")
    if d1 <= d0:
        return _result(previous, current, "stale", "동일하거나 이전 기준일 재조회여서 신규 변화를 계산하지 않습니다.")
    if previous.get("complete") is not True or current.get("complete") is not True:
        return _result(previous, current, "incomplete", "두 날짜 모두 전체 구성종목 확인이 필요합니다.")
    before, before_error = _equities(previous)
    after, after_error = _equities(current)
    if before_error or after_error:
        return _result(previous, current, "incomplete", before_error or after_error)
    for code in before.keys() & after.keys():
        first, last = before[code].get("isin"), after[code].get("isin")
        if first and last and first != last:
            return _result(previous, current, "incomplete", "같은 주식 코드의 ISIN 변경을 확인해야 합니다.")
    result = _result(previous, current, "ready", "조회일 스냅샷 비교" if current["date_kind"] == "observation_date" else "같은 공급자·비중 기준의 전체 구성종목 비교")
    result["threshold_pp"] = threshold
    result["basis"] = current["basis"]
    result["weight_basis"] = current["weight_basis"]
    result["source"] = current["source"]
    for code in sorted(before.keys() | after.keys()):
        first, last = before.get(code), after.get(code)
        w0 = first["weight_pct"] if first else 0.0
        w1 = last["weight_pct"] if last else 0.0
        delta = round(w1 - w0, 10)
        if first is None:
            event = "new"
        elif last is None:
            event = "removed"
        elif delta >= threshold - 1e-10:
            event = "increased"
        elif delta <= -threshold + 1e-10:
            event = "decreased"
        else:
            event = "unchanged"
        quantity_change, quantity_status, quantity_note = _quantity_change(previous, current, first, last)
        holding = last or first
        result["changes"].append({
            "code": code,
            "isin": holding.get("isin") or (first or {}).get("isin"),
            "name": holding.get("name") or code,
            "previous_weight_pct": w0,
            "current_weight_pct": w1,
            "delta_weight_pp": delta,
            "event": event,
            "quantity_change_pct": quantity_change,
            "quantity_status": quantity_status,
            "quantity_note": quantity_note,
        })
    return result


def aggregate_comparisons(funds: list[dict], period: str = "daily",
                         representatives_only: bool = True,
                         min_increases: int = 2) -> dict:
    """Aggregate within date-kind/date-pair cohorts; no cross-cohort ranking.

    Average delta is over the comparable selected funds exposed at either end,
    including decreases and unchanged rows. Counts are ETF observations, not
    independent investment decisions or measured fund flows.
    """
    if period not in {"daily", "weekly"}:
        raise ValueError("period must be daily or weekly")
    if not isinstance(min_increases, int) or isinstance(min_increases, bool) or min_increases < 0:
        raise ValueError("min_increases must be a nonnegative integer")
    groups, seen = {}, set()
    selected_count = comparable_count = 0
    for fund in funds:
        if representatives_only and fund.get("representative") is not True:
            continue
        fund_id = fund.get("id")
        if not fund_id or fund_id in seen:
            continue
        seen.add(fund_id)
        selected_count += 1
        comparison = fund.get(period) or {}
        if comparison.get("status") != "ready":
            continue
        d0, d1 = comparison.get("from_date"), comparison.get("to_date")
        if not _date(d0) or not _date(d1) or d1 <= d0:
            continue
        date_kind = comparison.get("date_kind")
        if date_kind not in {"provider_date", "observation_date"}:
            continue
        comparable_count += 1
        key = date_kind, d0, d1
        cohort = groups.setdefault(key, {"date_kind": date_kind, "from_date": d0, "to_date": d1, "fund_ids": [], "stocks": {}})
        cohort["fund_ids"].append(fund_id)
        seen_codes = set()
        for change in comparison.get("changes", []):
            code = change.get("code")
            delta = _number(change.get("delta_weight_pp"))
            if not isinstance(code, str) or not _CODE.fullmatch(code) or code in seen_codes or delta is None:
                continue
            seen_codes.add(code)
            event = change.get("event")
            if event not in {"new", "removed", "increased", "decreased", "unchanged"}:
                continue
            stock = cohort["stocks"].setdefault(code, {
                "code": code, "name": change.get("name") or code,
                "isin": change.get("isin"), "increased_count": 0, "decreased_count": 0,
                "new_count": 0, "removed_count": 0, "observed_count": 0,
                "quantity_increased_count": 0, "quantity_unchanged_count": 0,
                "quantity_unavailable_count": 0, "fund_details": [],
                "_deltas": [], "_issuers": set(), "_groups": set(),
            })
            stock["observed_count"] += 1
            stock["increased_count"] += event in {"new", "increased"}
            stock["decreased_count"] += event in {"removed", "decreased"}
            stock["new_count"] += event == "new"
            stock["removed_count"] += event == "removed"
            quantity_status = change.get("quantity_status", "unavailable")
            for status in ("increased", "unchanged", "unavailable"):
                stock[f"quantity_{status}_count"] += quantity_status == status
            stock["_deltas"].append(delta)
            if fund.get("issuer"):
                stock["_issuers"].add(fund["issuer"])
            if fund.get("group"):
                stock["_groups"].add(fund["group"])
            stock["fund_details"].append({"fund_id": fund_id, "fund_name": fund.get("name"), "issuer": fund.get("issuer"), "group": fund.get("group"), **change})
    cohorts = []
    for key in sorted(groups):
        cohort = groups[key]
        stocks = []
        for stock in cohort.pop("stocks").values():
            stock["average_delta_weight_pp"] = round(sum(stock["_deltas"]) / len(stock["_deltas"]), 10)
            stock["net_count"] = stock["increased_count"] - stock["decreased_count"]
            stock["issuer_count"] = len(stock.pop("_issuers"))
            stock["exposure_group_count"] = len(stock.pop("_groups"))
            del stock["_deltas"]
            if stock["increased_count"] >= min_increases:
                stocks.append(stock)
        stocks.sort(key=lambda x: (-x["increased_count"], -x["net_count"], -x["average_delta_weight_pp"], x["code"]))
        cohort["rows"] = stocks
        cohort["comparable_count"] = len(cohort["fund_ids"])
        cohorts.append(cohort)
    return {"period": period, "selected_count": selected_count,
            "comparable_count": comparable_count,
            "status": "ready" if comparable_count else "waiting", "cohorts": cohorts}
