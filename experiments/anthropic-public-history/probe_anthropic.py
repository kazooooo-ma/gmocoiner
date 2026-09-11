#!/usr/bin/env python3
"""Read-only Anthropic history probe. No credentials, orders, or account APIs.

Python 3.10+. Run: python3 probe_anthropic.py --venue lighter --days 1 --out output
A partial/failed collection exits 2, not 0. Successful collection does not prove
that the two markets use economically equivalent price or contract units.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import math
import re
from pathlib import Path
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

UTC = dt.timezone.utc
LIGHTER = 'https://mainnet.zklighter.elliot.ai'
VARI_PUBLIC = 'https://omni-client-api.prod.ap-northeast-1.variational.io'
VARI_APP = 'https://omni.variational.io'
MAX_BYTES = 12_000_000
SOURCE_URLS = {
    'lighter': 'https://github.com/elliottech/lighter-python/blob/main/docs/CandlestickApi.md',
    'vari_public': 'https://docs.variational.io/technical-documentation/api',
    'funding_schema': 'https://github.com/elliottech/lighter-python/blob/main/docs/Funding.md',
    'vari_candles_candidate': 'https://github.com/Caio-Fl/Airdrop_Points/blob/87a4de00d4a8b3e7a41685305b20a6df6793b138/exchanges_klines.py',
}

class ProbeError(ValueError):
    pass

def utc_iso(s: int | float) -> str:
    return dt.datetime.fromtimestamp(s, UTC).isoformat().replace('+00:00', 'Z')

def timestamp_s(value: object) -> int:
    if isinstance(value, bool):
        raise ProbeError('INVALID_TIMESTAMP')
    try:
        n = float(value)
    except (TypeError, ValueError) as exc:
        raise ProbeError('INVALID_TIMESTAMP') from exc
    if not math.isfinite(n):
        raise ProbeError('INVALID_TIMESTAMP')
    if n >= 100_000_000_000:
        n /= 1000
    if not 946684800 <= n < 4102444800 or abs(n - round(n)) > 1e-6:
        raise ProbeError('INVALID_TIMESTAMP_RANGE_OR_UNIT')
    return round(n)

def price(value: object) -> float:
    if isinstance(value, bool):
        raise ProbeError('INVALID_PRICE')
    try:
        n = float(value)
    except (TypeError, ValueError) as exc:
        raise ProbeError('INVALID_PRICE') from exc
    if not math.isfinite(n) or n <= 0:
        raise ProbeError('INVALID_PRICE')
    return n

def decode_json(body: bytes, content_type: str) -> object:
    if len(body) > MAX_BYTES:
        raise ProbeError('RESPONSE_TOO_LARGE')
    if 'html' in content_type.lower() or body.lstrip().startswith(b'<'):
        raise ProbeError('HTML_NOT_MARKET_DATA')
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeError('NON_JSON_RESPONSE') from exc
    if not isinstance(data, (dict, list)):
        raise ProbeError('INVALID_JSON_ROOT')
    if isinstance(data, dict):
        if data.get('code') not in (None, 200):
            raise ProbeError('API_ERROR_CODE')
        if data.get('success') is False or data.get('error'):
            raise ProbeError('API_ERROR')
    return data

def resolve_market(data: object) -> int:
    if not isinstance(data, dict):
        raise ProbeError('MARKET_METADATA_SCHEMA_UNKNOWN')
    rows = []
    for key in ('order_books', 'order_book_details', 'perp_order_book_details'):
        val = data.get(key)
        if isinstance(val, list):
            rows.extend(val)
    matches = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get('symbol', '')).upper()
        if symbol not in {'ANTHROPIC', 'ANTHROPIC-PERP', 'ANTHROPIC-USD', 'ANTHROPIC-USDC'}:
            continue
        if str(row.get('market_type', '')).lower() == 'spot':
            continue
        mid = row.get('market_id')
        if isinstance(mid, bool) or not isinstance(mid, int) or mid < 0:
            raise ProbeError('INVALID_MARKET_ID')
        matches.add(mid)
    if len(matches) != 1:
        raise ProbeError(f'ANTHROPIC_MARKET_UNRESOLVED_count_{len(matches)}')
    return next(iter(matches))

def normalize_candles(data: object, venue: str) -> list[dict]:
    if venue == 'lighter':
        rows = data.get('c') if isinstance(data, dict) else None
    else:
        rows = data if isinstance(data, list) else None
    if not isinstance(rows, list) or not rows:
        raise ProbeError('EMPTY_OR_UNKNOWN_CANDLE_SCHEMA')
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            raise ProbeError('CANDLE_ROW_SCHEMA_UNKNOWN')
        keys = ('t', 'o', 'h', 'l', 'c') if venue == 'lighter' else ('unix_time_ms', 'open', 'high', 'low', 'close')
        if any(k not in row for k in keys):
            raise ProbeError('CANDLE_FIELDS_MISSING')
        t = timestamp_s(row[keys[0]])
        o, h, l, c = [price(row[k]) for k in keys[1:]]
        if not l <= min(o, c) <= max(o, c) <= h:
            raise ProbeError('INVALID_OHLC')
        normalized.append({'t': t, 'open': o, 'high': h, 'low': l, 'close': c})
    return normalized

def merge_rows(rows: list[dict], start: int, end: int, step: int = 3600) -> tuple[list[dict], dict]:
    unique = {}
    excluded = 0
    duplicates = 0
    for row in rows:
        t = row['t']
        if t < start or t + step > end:
            excluded += 1
            continue
        if t % step:
            raise ProbeError('MISALIGNED_CANDLE_TIMESTAMP')
        if t in unique:
            if unique[t] != row:
                raise ProbeError('CONFLICTING_DUPLICATE_CANDLE')
            duplicates += 1
        unique[t] = row
    result = [unique[t] for t in sorted(unique)]
    expected = set(range(start, end, step))
    actual = set(unique)
    audit = {
        'expected_rows': len(expected), 'rows': len(result),
        'missing_rows': len(expected - actual),
        'duplicates_removed': duplicates, 'outside_or_incomplete_rows_excluded': excluded,
        'first_utc': utc_iso(result[0]['t']) if result else None,
        'last_utc': utc_iso(result[-1]['t']) if result else None,
        'complete_requested_window': bool(result) and actual == expected,
        'imputation': 'NONE',
    }
    return result, audit

def windows(start: int, end: int, maximum: int):
    a = start
    while a < end:
        b = min(end, a + maximum * 3600)
        yield a, b
        a = b

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProbeError('REDIRECT_BLOCKED')


def funding_number(value: object) -> str:
    """Preserve numeric values without asserting a funding unit or direction."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ProbeError('INVALID_FUNDING_NUMBER')
    text = str(value)
    if len(text) > 80 or not re.fullmatch(r'-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?', text):
        raise ProbeError('INVALID_FUNDING_NUMBER')
    if not math.isfinite(float(text)):
        raise ProbeError('INVALID_FUNDING_NUMBER')
    return text


def normalize_funding(data: object) -> list[dict]:
    rows = data.get('fundings') if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ProbeError('EMPTY_OR_UNKNOWN_FUNDING_SCHEMA')
    result = []
    for row in rows:
        if not isinstance(row, dict) or any(k not in row for k in ('timestamp', 'value', 'rate', 'direction')):
            raise ProbeError('FUNDING_FIELDS_MISSING')
        direction = row['direction']
        # Keep only a bounded, non-secret direction label, never arbitrary response fields.
        if direction not in ('long', 'short', 'buy', 'sell', 'LONG', 'SHORT', 'BUY', 'SELL', 'long_to_short', 'short_to_long', 'positive', 'negative'):
            raise ProbeError('FUNDING_DIRECTION_UNVERIFIED')
        result.append({'t': timestamp_s(row['timestamp']), 'value': funding_number(row['value']),
                       'rate': funding_number(row['rate']), 'direction': direction})
    return result


ENDPOINTS = {
    'lighter-metadata': (LIGHTER, '/api/v1/orderBooks', set()),
    'lighter-trade': (LIGHTER, '/api/v1/candles', {'market_id', 'resolution', 'start_timestamp', 'end_timestamp', 'count_back', 'set_timestamp_to_end'}),
    'lighter-mark': (LIGHTER, '/api/v1/markPriceCandles', {'market_id', 'resolution', 'start_timestamp', 'end_timestamp', 'count_back'}),
    'lighter-funding': (LIGHTER, '/api/v1/fundings', {'market_id', 'resolution', 'start_timestamp', 'end_timestamp', 'count_back'}),
    'vari-stats': (VARI_PUBLIC, '/metadata/stats', set()),
    'vari-candles': (VARI_APP, '/api/candles', {'period', 'cex_asset', 'start', 'end'}),
}


class Collector:
    def __init__(self, out: Path, timeout: float, timestamp_unit: str = 's'):
        self.out, self.timeout = out, timeout
        self.multiplier = 1000 if timestamp_unit == 'ms' else 1
        self.logs: list[dict] = []
        # No cookies, netrc, environment proxy credentials, or redirects.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def get(self, label: str, params: dict | None = None):
        base, path, allowed = ENDPOINTS[label]
        params = params or {}
        if not set(params) <= allowed:
            raise ProbeError('UNEXPECTED_REQUEST_PARAMETER')
        url = base + path + ('?' + urllib.parse.urlencode(params) if params else '')
        log = {'endpoint': label, 'url': url, 'method': 'GET', 'credentials': 'NONE',
               'started_utc': utc_iso(time.time()), 'http_status': None}
        before = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'public-anthropic-history-probe/7'})
            with self.opener.open(req, timeout=self.timeout) as response:
                log['http_status'] = response.status
                body = response.read(MAX_BYTES + 1)
                content_type = response.headers.get('Content-Type', '')
            data = decode_json(body, content_type)
            log.update(status='JSON_RECEIVED_NOT_VALIDATED', response_bytes=len(body),
                       response_sha256=hashlib.sha256(body).hexdigest())
            # Intentionally do not save raw JSON, response headers, or cookies.
            return data
        except urllib.error.HTTPError as exc:
            log['http_status'] = exc.code
            log['status'] = ('AUTH_OR_ACCESS_REQUIRED' if exc.code in (401, 403) else
                             'RATE_LIMITED' if exc.code == 429 else 'HTTP_ERROR')
            return None
        except (urllib.error.URLError, TimeoutError, OSError, ProbeError) as exc:
            reason = getattr(exc, 'reason', exc)
            log['status'] = 'DNS_FAILED' if isinstance(reason, socket.gaierror) else 'FETCH_OR_VALIDATION_FAILED'
            log['error_type'] = type(reason).__name__
            if isinstance(exc, ProbeError):
                log['validation_error'] = str(exc)
            return None
        finally:
            log['elapsed_seconds'] = round(time.monotonic() - before, 3)
            self.logs.append(log)
            print(label + ': ' + log.get('status', 'UNKNOWN'), flush=True)

    def series(self, stream: str, start: int, end: int, market_id: int | None = None) -> dict:
        combined: list[dict] = []
        failure = None
        try:
            for a, b in windows(start, end, 240):
                if stream == 'vari-candles':
                    params = {'period': '1h', 'cex_asset': 'ANTHROPIC', 'start': utc_iso(a), 'end': utc_iso(b)}
                else:
                    if market_id is None:
                        raise ProbeError('MARKET_ID_UNRESOLVED')
                    params = {'market_id': market_id, 'resolution': '1h',
                              'start_timestamp': a * self.multiplier, 'end_timestamp': b * self.multiplier,
                              'count_back': (b - a) // 3600}
                    if stream == 'lighter-funding':
                        # The live API omitted the exact start boundary. Request
                        # one prior hour, then retain actual rows in [start,end).
                        # This is overlap retrieval, never timestamp shifting or imputation.
                        params['start_timestamp'] = (a - 3600) * self.multiplier
                        params['count_back'] += 1
                    if stream == 'lighter-trade':
                        params['set_timestamp_to_end'] = 'false'
                data = self.get(stream, params)
                if data is None:
                    raise ProbeError('FETCH_FAILED_NO_IMPUTATION')
                rows = normalize_funding(data) if stream == 'lighter-funding' else normalize_candles(
                    data, 'variational' if stream == 'vari-candles' else 'lighter')
                combined.extend(rows)
                time.sleep(1.1)
        except ProbeError as exc:
            failure = str(exc)
        try:
            rows, audit = merge_rows(combined, start, end)
        except ProbeError as exc:
            # A conflicting duplicate or timestamp invalidates the normalized stream.
            rows, audit = [], {'complete_requested_window': False, 'validation_error': str(exc)}
            failure = str(exc)
        audit.update(status='PARTIAL' if failure or not audit.get('complete_requested_window') else 'COMPLETE_DATA_ONLY',
                     stream=stream, rows=len(rows), request_error=failure, price_or_funding_semantics='UNVERIFIED')
        if failure:
            audit['complete_requested_window'] = False
        (self.out / (stream + '.json')).write_text(json.dumps(rows, indent=2), encoding='utf-8')
        return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--venue', required=True, choices=['lighter', 'variational'])
    parser.add_argument('--days', type=int, choices=[1, 30], default=1)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--end', help='Completed UTC hour in ISO 8601; default current last completed hour')
    parser.add_argument('--timeout', type=float, default=12)
    parser.add_argument('--lighter-time-unit', choices=['s', 'ms'], default='s')
    parser.add_argument('--eligible-from', type=Path, help='Backfill only streams complete in a prior smoke summary')
    args = parser.parse_args()
    if not 0 < args.timeout <= 30:
        parser.error('--timeout must be within (0,30]')
    now = time.time()
    end = int(now // 3600 * 3600)
    if args.end:
        value = dt.datetime.fromisoformat(args.end.replace('Z', '+00:00'))
        if value.tzinfo is None or value.timestamp() % 3600 or value.timestamp() > end:
            parser.error('--end must be a completed hour with timezone')
        end = int(value.timestamp())
    start = end - args.days * 86400
    args.out.mkdir(parents=True, exist_ok=False)
    collector = Collector(args.out, args.timeout, args.lighter_time_unit)
    expected = ['lighter-trade', 'lighter-mark', 'lighter-funding'] if args.venue == 'lighter' else ['vari-candles']
    streams = expected[:]
    summary = {'schema': 'anthropic-public-probe/v7', 'status': 'PARTIAL', 'venue': args.venue,
               'requested_start_utc': utc_iso(start), 'requested_end_utc': utc_iso(end), 'days': args.days,
               'created_utc': utc_iso(now), 'sources': SOURCE_URLS, 'streams': {}, 'imputation': 'NONE',
               'lighter_request_timestamp_unit': args.lighter_time_unit, 'timestamp_unit_live_verification': 'UNVERIFIED',
               'convergence_status': 'UNPROVEN', 'funding_pnl_status': 'UNPROVEN',
               'contract_unit_equivalence': 'UNVERIFIED', 'price_kind_equivalence': 'UNVERIFIED',
               'vari_history_api_status': 'UNOFFICIAL_CANDIDATE', 'eligible_backfill_streams': []}
    try:
        if args.eligible_from:
            previous = json.loads(args.eligible_from.read_text())
            if previous.get('schema') != 'anthropic-public-probe/v7' or previous.get('venue') != args.venue:
                raise ProbeError('INVALID_SMOKE_SUMMARY')
            streams = [k for k in expected if previous.get('streams', {}).get(k, {}).get('complete_requested_window') is True]
            summary['skipped_unproven_streams'] = [k for k in expected if k not in streams]
            if not streams:
                summary['status'] = 'SKIPPED_NO_VALIDATED_STREAMS'
                return 2
        mid = None
        if args.venue == 'lighter' and streams:
            metadata = collector.get('lighter-metadata')
            if metadata is None:
                raise ProbeError('MARKET_METADATA_FETCH_FAILED')
            mid = resolve_market(metadata)
            summary['market_id'] = mid
        elif args.venue == 'variational':
            data = collector.get('vari-stats')
            summary['public_stats_json_received'] = data is not None
            if isinstance(data, dict):
                listings = data.get('listings')
                summary['anthropic_listing_found'] = isinstance(listings, list) and any(isinstance(r, dict) and r.get('ticker') == 'ANTHROPIC' for r in listings)
        for stream in streams:
            summary['streams'][stream] = collector.series(stream, start, end, mid)
        complete = [k for k, v in summary['streams'].items() if v.get('complete_requested_window') is True]
        summary['eligible_backfill_streams'] = complete
        if set(complete) == set(expected):
            summary['status'] = 'COMPLETE_DATA_ONLY'
        elif not complete and not any(v.get('rows', 0) for v in summary['streams'].values()):
            summary['status'] = 'FAILED'
    except (ProbeError, ValueError, OSError, TypeError, KeyError) as exc:
        summary['error_type'] = type(exc).__name__
        if isinstance(exc, ProbeError):
            summary['error_code'] = str(exc)
        summary['status'] = 'FAILED'
    finally:
        (args.out / 'requests.json').write_text(json.dumps(collector.logs, indent=2), encoding='utf-8')
        (args.out / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        print(json.dumps(summary, indent=2), flush=True)
    return 0 if summary['status'] == 'COMPLETE_DATA_ONLY' else 2


if __name__ == '__main__':
    raise SystemExit(main())
