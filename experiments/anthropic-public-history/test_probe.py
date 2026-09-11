"""Synthetic validation tests, not market-history or strategy backtests."""
import unittest
from probe_anthropic import (ProbeError, decode_json, resolve_market, normalize_candles,
                             merge_rows, timestamp_s, price, windows)
BASE = 1789084800  # 2026-09-11 00:00 UTC

def candle(t=BASE, close=110):
    return {'t': t, 'open': 100., 'high': 120., 'low': 90., 'close': float(close)}

class ProbeTests(unittest.TestCase):
    def test_seconds_and_milliseconds(self):
        self.assertEqual(timestamp_s(BASE), timestamp_s(BASE * 1000))
    def test_nonfinite_timestamps_rejected(self):
        for v in (float('nan'), float('inf'), True):
            with self.assertRaises(ProbeError): timestamp_s(v)
    def test_microseconds_rejected(self):
        with self.assertRaises(ProbeError): timestamp_s(BASE * 1_000_000)
    def test_html_is_not_success(self):
        with self.assertRaises(ProbeError): decode_json(b'<html>Login</html>', 'text/html')
    def test_api_error_in_http_200_rejected(self):
        with self.assertRaises(ProbeError): decode_json(b'{"code":400,"message":"bad request"}', 'application/json')
    def test_non_json_rejected(self):
        with self.assertRaises(ProbeError): decode_json(b'Loading...', 'text/plain')
    def test_exact_symbol_resolution(self):
        self.assertEqual(resolve_market({'order_books':[{'symbol':'ANTHROPIC','market_id':123}]}),123)
    def test_ambiguous_market_rejected(self):
        with self.assertRaises(ProbeError):
            resolve_market({'order_books':[{'symbol':'ANTHROPIC','market_id':123},{'symbol':'ANTHROPIC','market_id':456}]})
    def test_missing_market_is_not_id_zero(self):
        with self.assertRaises(ProbeError): resolve_market({'order_books':[{'symbol':'BTC','market_id':0}]})
    def test_negative_or_nonfinite_prices_rejected(self):
        for v in (-1, 0, float('nan'), float('inf'), True):
            with self.assertRaises(ProbeError): price(v)
    def test_normalize_lighter(self):
        x = normalize_candles({'c':[{'t':BASE*1000,'o':'100','h':'120','l':'90','c':'110'}]}, 'lighter')
        self.assertEqual(x, [candle()])
    def test_normalize_vari_candidate(self):
        x = normalize_candles([{'unix_time_ms':BASE*1000,'open':'100','high':'120','low':'90','close':'110'}], 'variational')
        self.assertEqual(x, [candle()])
    def test_unsupported_vari_array_rejected(self):
        with self.assertRaises(ProbeError): normalize_candles([[BASE*1000,100,120,90,110]], 'variational')
    def test_bad_ohlc_rejected(self):
        with self.assertRaises(ProbeError):
            normalize_candles({'c':[{'t':BASE,'o':100,'h':105,'l':90,'c':110}]}, 'lighter')
    def test_identical_duplicate_removed(self):
        rows, audit = merge_rows([candle(), candle()], BASE, BASE+3600)
        self.assertEqual(len(rows),1)
        self.assertEqual(audit['duplicates_removed'],1)
    def test_conflicting_duplicate_rejected(self):
        with self.assertRaises(ProbeError): merge_rows([candle(),candle(close=111)], BASE,BASE+3600)
    def test_missing_hours_not_filled(self):
        rows, audit = merge_rows([candle()],BASE,BASE+7200)
        self.assertEqual(audit['missing_rows'],1)
        self.assertFalse(audit['complete_requested_window'])
        self.assertEqual(len(rows),1)
    def test_incomplete_last_candle_excluded(self):
        rows, audit = merge_rows([candle(BASE),candle(BASE+3600)],BASE,BASE+3600)
        self.assertEqual(len(rows),1)
        self.assertEqual(audit['outside_or_incomplete_rows_excluded'],1)
    def test_time_alignment_not_silently_rounded(self):
        with self.assertRaises(ProbeError): merge_rows([candle(BASE+1)],BASE,BASE+7200)
    def test_page_windows_no_gaps(self):
        w = list(windows(BASE,BASE+720*3600,240))
        self.assertEqual(len(w),3)
        self.assertEqual(w[0][1],w[1][0])
        self.assertEqual(w[1][1],w[2][0])
        self.assertEqual(w[-1][1],BASE+720*3600)

if __name__ == '__main__': unittest.main(verbosity=2)
