import io
import json
import socket
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock
import probe_anthropic as p

T = 1789084800  # 2026-09-11 00:00:00 UTC, exact hour

class Reply:
    status = 200
    headers = {'Content-Type': 'application/json', 'Set-Cookie': 'test-only-do-not-save'}
    def __init__(self, body): self.body = body
    def read(self, maximum): return self.body[:maximum]
    def __enter__(self): return self
    def __exit__(self, *args): pass

class PublicTests(unittest.TestCase):
    def test_reply_body_and_cookie_never_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = p.Collector(Path(tmp), 1)
            c.opener.open = mock.Mock(return_value=Reply(b'{"code":200,"test_private_field":"fixture-not-a-secret"}'))
            self.assertIsNotNone(c.get('lighter-metadata'))
            self.assertEqual(list(Path(tmp).iterdir()), [])
            self.assertNotIn('test_private_field', json.dumps(c.logs))
            self.assertNotIn('Set-Cookie', json.dumps(c.logs))
            req = c.opener.open.call_args.args[0]
            self.assertEqual(set(k.lower() for k in req.headers), {'accept', 'user-agent'})

    def test_401_is_failure_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=p.Collector(Path(tmp),1)
            c.opener.open=mock.Mock(side_effect=urllib.error.HTTPError('https://example.invalid',401,'',{},None))
            self.assertIsNone(c.get('lighter-metadata'))
            self.assertEqual(c.logs[-1]['status'],'AUTH_OR_ACCESS_REQUIRED')
            self.assertEqual(c.opener.open.call_count,1)

    def test_403_is_failure_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=p.Collector(Path(tmp),1)
            c.opener.open=mock.Mock(side_effect=urllib.error.HTTPError('https://example.invalid',403,'',{},None))
            self.assertIsNone(c.get('lighter-metadata'))
            self.assertEqual(c.logs[-1]['status'],'AUTH_OR_ACCESS_REQUIRED')
            self.assertEqual(c.opener.open.call_count,1)

    def test_redirect_is_blocked(self):
        with self.assertRaises(p.ProbeError):
            p.NoRedirect().redirect_request(None,None,302,'',{},'https://example.invalid')

    def test_dns_failure_is_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=p.Collector(Path(tmp),1)
            c.opener.open=mock.Mock(side_effect=urllib.error.URLError(socket.gaierror(-2,'fixture')))
            self.assertIsNone(c.get('lighter-metadata'))
            self.assertEqual(c.logs[-1]['status'],'DNS_FAILED')

    def test_unknown_parameter_rejected_before_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=p.Collector(Path(tmp),1)
            c.opener.open=mock.Mock()
            with self.assertRaises(p.ProbeError): c.get('lighter-metadata', {'unexpected':'fixture'})
            c.opener.open.assert_not_called()

    def test_funding_drops_unrecognized_fields(self):
        row={'timestamp':T,'value':'0.2','rate':'0.0001','direction':'short','test_private_field':'fixture'}
        rows=p.normalize_funding({'fundings':[row]})
        self.assertEqual(set(rows[0]),{'t','value','rate','direction'})

    def test_funding_label_fails_closed(self):
        row={'timestamp':T,'value':'0.2','rate':'0.0001','direction':'unexpected-free-text'}
        with self.assertRaises(p.ProbeError): p.normalize_funding({'fundings':[row]})

    def test_funding_nan_rejected(self):
        with self.assertRaises(p.ProbeError): p.funding_number('NaN')

    def test_funding_boolean_rejected(self):
        with self.assertRaises(p.ProbeError): p.funding_number(True)

    def test_missing_series_is_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=p.Collector(Path(tmp),1)
            c.get=mock.Mock(return_value=None)
            result=c.series('lighter-trade',T,T+3600,1)
            self.assertFalse(result['complete_requested_window'])
            self.assertEqual(result['rows'],0)

    def test_outside_timestamp_is_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=p.Collector(Path(tmp),1)
            c.get=mock.Mock(return_value={'c':[{'t':T-3600,'o':1,'h':1,'l':1,'c':1}]})
            with mock.patch.object(p.time,'sleep'):
                result=c.series('lighter-trade',T,T+3600,1)
            self.assertFalse(result['complete_requested_window'])
            self.assertEqual(result['rows'],0)

    def test_valid_hour_succeeds_without_claiming_pnl(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=p.Collector(Path(tmp),1)
            c.get=mock.Mock(return_value={'c':[{'t':T,'o':1,'h':2,'l':1,'c':2,'unknown':'fixture'}]})
            with mock.patch.object(p.time,'sleep'):
                result=c.series('lighter-trade',T,T+3600,1)
            self.assertTrue(result['complete_requested_window'])
            self.assertEqual(result['price_or_funding_semantics'],'UNVERIFIED')
            self.assertNotIn('unknown', (Path(tmp)/'lighter-trade.json').read_text())

    def test_all_endpoints_are_public_read_candidates(self):
        self.assertEqual(len(p.ENDPOINTS),6)
        for base,path,params in p.ENDPOINTS.values():
            self.assertTrue(base.startswith('https://'))
            self.assertFalse(any(x in path.lower() for x in ('account','order/create','transfer','login')))
            self.assertIsInstance(params,set)

    def test_workflow_has_no_credential_persistence_or_schedule(self):
        text=(Path(__file__).parents[2]/'.github/workflows/anthropic-public-history.yml').read_text()
        self.assertIn('persist-credentials: false',text)
        self.assertIn('fail-fast: false',text)
        self.assertIn('contents: read',text)
        self.assertNotIn('secrets.',text)
        self.assertNotIn('schedule:',text)
        self.assertNotIn('pull_request_target:',text)
        self.assertNotIn('contents: write',text)
        for line in text.splitlines():
            if 'uses:' in line:
                import re
                self.assertRegex(line,r'@[0-9a-f]{40}$')

if __name__ == '__main__': unittest.main()
