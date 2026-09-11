"""Regression: fetch boundary records instead of fabricating missing funding."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import probe_anthropic as p

T = 1789084800

class FundingBoundaryTests(unittest.TestCase):
    def test_exclusive_start_api_yields_complete_real_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            collector = p.Collector(Path(tmp), 1)
            def exclusive(label, params):
                self.assertEqual(label, "lighter-funding")
                self.assertEqual(params["start_timestamp"], T - 3600)
                self.assertEqual(params["count_back"], 25)
                return {"fundings": [
                    {"timestamp": t, "rate": "0.0004", "value": "0.008", "direction": "long"}
                    for t in range(params["start_timestamp"] + 3600, params["end_timestamp"], 3600)
                ]}
            collector.get = mock.Mock(side_effect=exclusive)
            with mock.patch.object(p.time, "sleep"):
                audit = collector.series("lighter-funding", T, T + 24 * 3600, 193)
            self.assertEqual(audit["rows"], 24)
            self.assertTrue(audit["complete_requested_window"])
            self.assertEqual(audit["imputation"], "NONE")

    def test_true_missing_hour_stays_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            collector = p.Collector(Path(tmp), 1)
            collector.get = mock.Mock(return_value={"fundings": [
                {"timestamp": T + 3600, "rate": "0.0004", "value": "0.008", "direction": "long"}
            ]})
            with mock.patch.object(p.time, "sleep"):
                audit = collector.series("lighter-funding", T, T + 2 * 3600, 193)
            self.assertEqual(audit["missing_rows"], 1)
            self.assertFalse(audit["complete_requested_window"])

    def test_millisecond_requests_keep_original_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            collector = p.Collector(Path(tmp), 1, "ms")
            collector.get = mock.Mock(return_value={"fundings": [
                {"timestamp": T * 1000, "rate": "0.0004", "value": "0.008", "direction": "long"}
            ]})
            with mock.patch.object(p.time, "sleep"):
                audit = collector.series("lighter-funding", T, T + 3600, 193)
            self.assertEqual(collector.get.call_args.args[1]["start_timestamp"], (T - 3600) * 1000)
            self.assertTrue(audit["complete_requested_window"])
            self.assertEqual(audit["first_utc"], p.utc_iso(T))
