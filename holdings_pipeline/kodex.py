"""Normalize public KODEX creation baskets already fetched by the price pipeline.

This adapter has no networking.  Product JSON may publish the next session's
basket before that session; callers must select the desired completed date.
The issuer's ``ratio`` is the published non-cash constituent weight, and the
quantity is per creation unit, never a whole-fund position or a fund flow.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

SOURCE = 'KODEX'
WEIGHT_BASIS = 'issuer_reported_non_cash_constituent_weight'
_PRODUCT_URL = re.compile(r'https://www\.samsungfund\.com/api/v1/kodex/product/([A-Za-z0-9]+)\.do(?:\?.*)?\Z')
_SECURITY_CODE = re.compile(r'[0-9][0-9A-Z]{5}\Z')


def _number(value: Any, *, nullable: bool = True) -> float | None:
    if value is None or str(value).strip() in ('', '-', '--'):
        if nullable:
            return None
        raise ValueError('kodex_missing_number')
    try:
        number = float(str(value).replace(',', '').strip())
    except (TypeError, ValueError) as exc:
        raise ValueError('kodex_invalid_number') from exc
    if not math.isfinite(number):
        raise ValueError('kodex_non_finite_number')
    return number


def _date(value: Any) -> str:
    text = str(value or '').strip()
    try:
        return datetime.strptime(text, '%Y%m%d').date().isoformat()
    except ValueError:
        try:
            return datetime.strptime(text, '%Y-%m-%d').date().isoformat()
        except ValueError as exc:
            raise ValueError('kodex_invalid_basket_date') from exc


def _observed_at(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if re.fullmatch(r'\d{8}T\d{12}', text):
        return datetime.strptime(text, '%Y%m%dT%H%M%S%f').replace(tzinfo=timezone.utc).isoformat()
    try:
        stamp = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('kodex_invalid_observed_at') from exc
    if stamp.tzinfo is None:
        raise ValueError('kodex_observed_timezone_missing')
    return stamp.isoformat()


def _catalog_codes(etf_codes: Iterable[str] | Mapping[str, Any]) -> set[str]:
    # Accept the current catalog object, its rows, or a set of codes.
    values = etf_codes.get('rows', etf_codes.keys()) if isinstance(etf_codes, Mapping) else etf_codes
    return {str(value.get('symbol', '') if isinstance(value, Mapping) else value).removeprefix('KR:').upper() for value in values}


def normalize_product(payload: Mapping[str, Any], asset: Mapping[str, Any],
                      etf_codes: Iterable[str] | Mapping[str, Any],
                      observed_at: str | None = None,
                      collection_mode: str = 'archived_official_response') -> dict[str, Any]:
    """Return one schema-v1 basket; reject invalid identity, dates, or numbers.

    ``complete`` reflects the official total/returned counts, independently of
    quantity availability.  Six-character Korean codes are equity candidates
    only within an issuer-identified domestic-equity product; known ETF codes
    are funds, and explicit cash/derivative instruments are excluded.
    """
    product = payload.get('info', {}).get('product', {})
    symbol = str(product.get('stkTicker', '')).upper()
    expected = str(asset.get('symbol', '')).upper()
    if not expected or symbol != expected or asset.get('id', 'KR:' + expected) != 'KR:' + expected:
        raise ValueError('kodex_identity_mismatch')
    if product.get('typeLnm') != '국내주식' or asset.get('scope', 'KR') != 'KR':
        raise ValueError('kodex_not_domestic_equity_product')
    fund_id = str(product.get('fId', ''))
    if not re.fullmatch(r'[A-Za-z0-9]+', fund_id):
        raise ValueError('kodex_fund_identity_missing')
    if asset.get('issuer_fund_id') and asset['issuer_fund_id'] != fund_id:
        raise ValueError('kodex_fund_identity_mismatch')
    source_url = asset.get('issuer_quote_url') or f'https://www.samsungfund.com/api/v1/kodex/product/{fund_id}.do'
    match = _PRODUCT_URL.fullmatch(source_url)
    if not match or match.group(1) != fund_id:
        raise ValueError('kodex_source_identity_mismatch')

    basket = payload.get('pdf', {})
    rows = basket.get('list')
    if not isinstance(rows, list) or not rows:
        raise ValueError('kodex_empty_holdings')
    date = _date(basket.get('gijunYMD'))
    creation_unit = _number(product.get('qPerCu'))
    if creation_unit is not None and (creation_unit <= 0 or creation_unit != int(creation_unit)):
        raise ValueError('kodex_invalid_creation_unit')
    reported = _number(basket.get('totalCnt'))
    returned = _number(basket.get('nowCnt'))
    if reported is not None and (reported < 0 or reported != int(reported)):
        raise ValueError('kodex_invalid_reported_count')
    if returned is not None and (returned < 0 or returned != int(returned)):
        raise ValueError('kodex_invalid_returned_count')
    known_etfs = _catalog_codes(etf_codes)
    holdings = []
    seen = set()
    for row in rows:
        code = str(row.get('itmNo', '')).strip().upper()
        name = html.unescape(str(row.get('secNm', '')).strip())
        if not code or not name:
            raise ValueError('kodex_security_identity_missing')
        if code in seen:
            raise ValueError('kodex_duplicate_security')
        seen.add(code)
        if code.startswith('KRD') or name in ('원화예금', '현금', '외화예금'):
            asset_type = 'cash'
        elif code in known_etfs:
            asset_type = 'fund'
        elif re.search(r'선물|옵션|스왑|FUTURE|SWAP|OPTION', name, re.I):
            asset_type = 'derivative'
        elif _SECURITY_CODE.fullmatch(code):
            asset_type = 'equity'
        else:
            asset_type = 'other'
        weight = _number(row.get('ratio'))
        quantity = _number(row.get('applyQ'))
        if weight is not None and (weight < 0 or weight > 100):
            raise ValueError('kodex_invalid_weight')
        # A creation basket can contain a negative cash balancing amount.
        if quantity is not None and quantity < 0 and asset_type not in ('derivative', 'cash'):
            raise ValueError('kodex_invalid_quantity')
        isin = next((row.get(key) for key in ('isin', 'isinCd', 'ISIN') if row.get(key)), None)
        if isin is not None and not re.fullmatch(r'[A-Z]{2}[A-Z0-9]{9}[0-9]', str(isin)):
            raise ValueError('kodex_invalid_isin')
        holdings.append({'code': code, 'isin': isin, 'name': name,
                         'asset_type': asset_type, 'weight_pct': weight,
                         'quantity': quantity})
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    return {'etf_id': 'KR:' + symbol, 'date': date, 'date_kind': 'provider_date',
            'basis': 'creation_basket', 'weight_basis': WEIGHT_BASIS,
            'source': SOURCE, 'source_url': source_url,
            'observed_at': _observed_at(observed_at), 'collection_mode': collection_mode,
            'complete': reported == returned == len(holdings),
            'reported_count': int(reported) if reported is not None else None,
            'creation_unit_shares': creation_unit, 'quantity_basis': 'per_cu',
            'source_sha256': hashlib.sha256(canonical).hexdigest(),
            'source_hash_basis': 'canonical_payload', 'holdings': holdings}


def extract_archives(root: str | Path, assets: Iterable[Mapping[str, Any]],
                     etf_codes: Iterable[str] | Mapping[str, Any], *,
                     errors: list[dict[str, Any]] | None = None,
                     collection_mode: str = 'archived_official_response') -> list[dict[str, Any]]:
    """Read previously saved ``*.meta.json``/``*.body`` pairs below root.

    No network requests are made. Latest observed response wins for each ETF
    and basket date. Raw hashes are checked. Invalid records are skipped and,
    when supplied, recorded in ``errors`` with URL/reason but no local path.
    Future baskets are intentionally retained for the caller's date selection.
    """
    asset_map = {str(a.get('symbol', '')).upper(): a for a in assets if a.get('market', 'KR') == 'KR'}
    codes = _catalog_codes(etf_codes)
    selected = {}
    for metadata_file in sorted(Path(root).rglob('*.meta.json')):
        source_url = None
        etf_id = None
        try:
            meta = json.loads(metadata_file.read_text(encoding='utf-8'))
            source_url = meta.get('url', '')
            if not _PRODUCT_URL.fullmatch(source_url):
                continue
            if meta.get('status', 200) != 200:
                raise ValueError('kodex_archive_non_success_response')
            body_file = metadata_file.with_name(metadata_file.name.replace('.meta.json', '.body'))
            raw = body_file.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if meta.get('sha256') and digest != meta['sha256']:
                raise ValueError('kodex_raw_hash_mismatch')
            payload = json.loads(raw)
            symbol = str(payload.get('info', {}).get('product', {}).get('stkTicker', '')).upper()
            asset = asset_map.get(symbol)
            if asset is None:
                continue
            etf_id = asset['id']
            if asset.get('issuer_quote_url') and source_url != asset['issuer_quote_url']:
                raise ValueError('kodex_archive_url_mismatch')
            enriched_asset = dict(asset, issuer_quote_url=source_url)
            snap = normalize_product(payload, enriched_asset, codes,
                                     observed_at=meta.get('retrieved_at'),
                                     collection_mode=collection_mode)
            snap.update(source_sha256=digest, source_hash_basis='raw_response')
            key = (snap['etf_id'], snap['date'])
            if key not in selected or (snap['observed_at'] or '') > (selected[key]['observed_at'] or ''):
                selected[key] = snap
        except (KeyError, TypeError, ValueError, OSError) as exc:
            if errors is not None:
                errors.append({'source': SOURCE, 'source_url': source_url,
                               'etf_id': etf_id, 'error': str(exc) if isinstance(exc, ValueError) else type(exc).__name__})
    return [selected[key] for key in sorted(selected)]
