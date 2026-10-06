import contextlib
import io
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.helpers import assistant_lines, load_module, user_line, write_jsonl

cb = load_module()

TS = datetime(2026, 1, 10, 9, 0, tzinfo=timezone.utc)
USAGE = {"input_tokens": 100, "output_tokens": 50,
         "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 2000}


def parse(records):
    with tempfile.TemporaryDirectory() as tmp:
        path = write_jsonl(Path(tmp) / "s.jsonl", records)
        return cb._parse_jsonl(path, "-x", None)


class LookupPricing(unittest.TestCase):
    def test_exact_and_date_stripped(self):
        self.assertIsNotNone(cb._lookup_pricing("claude-sonnet-4-6"))
        self.assertIsNotNone(cb._lookup_pricing("claude-sonnet-4-6-20991231"))

    def test_unknown_returns_none_not_a_fallback_rate(self):
        self.assertIsNone(cb._lookup_pricing("claude-made-up-9"))
        self.assertIsNone(cb._lookup_pricing("sonnet"))
        self.assertFalse(hasattr(cb, "_FALLBACK_PRICING"))


class UnpricedModels(unittest.TestCase):
    def setUp(self):
        cb._warned_models.clear()

    def test_unknown_model_is_counted_but_not_priced(self):
        records = [user_line(TS, "hello")]
        records += assistant_lines(TS, "claude-made-up-9", USAGE, "m1", "r1")
        records += assistant_lines(TS + timedelta(minutes=1), "claude-sonnet-4-6", USAGE, "m2", "r2")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            s = parse(records)
        self.assertEqual(s["messages"], 2)
        self.assertEqual(s["unpriced_tokens"], 3150)
        self.assertEqual(s["unpriced_models"], ["claude-made-up-9"])
        p = cb.PRICING["claude-sonnet-4-6"]
        only_known = (100 * p["input"] + 50 * p["output"] + 1000 * p["cache_write"]
                      + 2000 * p["cache_read"]) / 1e6
        self.assertAlmostEqual(s["cost"], only_known, places=9)
        self.assertIn("claude-made-up-9", err.getvalue())

    def test_warning_is_emitted_once_per_model(self):
        records = []
        for i in range(5):
            records += assistant_lines(TS + timedelta(minutes=i), "claude-made-up-9", USAGE, f"m{i}", f"r{i}")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            parse(records)
            parse(records)
        self.assertEqual(err.getvalue().count("claude-made-up-9"), 1)

    def test_synthetic_is_zero_cost_silent_and_not_dominant(self):
        records = assistant_lines(TS, "<synthetic>", {"input_tokens": 0, "output_tokens": 0}, "m0", "r0")
        records += assistant_lines(TS + timedelta(minutes=1), "<synthetic>", {"input_tokens": 0, "output_tokens": 0}, "m1", "r1")
        records += assistant_lines(TS + timedelta(minutes=2), "claude-sonnet-4-6", USAGE, "m2", "r2")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            s = parse(records)
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(s["unpriced_tokens"], 0)
        self.assertEqual(s["model"], "claude-sonnet-4-6")

    def test_cost_marker_in_display(self):
        self.assertTrue(cb.fmt_cost(1.5, unpriced=10).endswith(cb.UNPRICED_MARK))
        self.assertFalse(cb.fmt_cost(1.5).endswith(cb.UNPRICED_MARK))


if __name__ == "__main__":
    unittest.main()


class OneHourCacheWrites(unittest.TestCase):
    def setUp(self):
        cb._warned_models.clear()
        cb.PRICING["claude-test-model"] = {"input": 1.0, "output": 10.0,
                                           "cache_write": 1.25, "cache_read": 0.1}

    def tearDown(self):
        cb.PRICING.pop("claude-test-model", None)

    def test_split_prefers_breakdown(self):
        u = {"cache_creation_input_tokens": 1000,
             "cache_creation": {"ephemeral_5m_input_tokens": 400, "ephemeral_1h_input_tokens": 600}}
        self.assertEqual(cb.cache_write_split(u), (400, 600))

    def test_split_without_breakdown_is_all_5m(self):
        self.assertEqual(cb.cache_write_split({"cache_creation_input_tokens": 1000}), (1000, 0))

    def test_1h_writes_cost_double_by_default(self):
        u5 = {"cache_creation_input_tokens": 1_000_000,
              "cache_creation": {"ephemeral_5m_input_tokens": 1_000_000, "ephemeral_1h_input_tokens": 0}}
        u1 = {"cache_creation_input_tokens": 1_000_000,
              "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 1_000_000}}
        self.assertAlmostEqual(cb.calc_cost("claude-test-model", u5), 1.25)
        self.assertAlmostEqual(cb.calc_cost("claude-test-model", u1), 2.50)

    def test_explicit_cache_write_1h_rate_wins(self):
        cb.PRICING["claude-test-model"]["cache_write_1h"] = 3.0
        u1 = {"cache_creation_input_tokens": 1_000_000,
              "cache_creation": {"ephemeral_1h_input_tokens": 1_000_000}}
        self.assertAlmostEqual(cb.calc_cost("claude-test-model", u1), 3.0)

    def test_session_tracks_1h_tokens(self):
        usage = dict(USAGE)
        usage["cache_creation"] = {"ephemeral_5m_input_tokens": 250, "ephemeral_1h_input_tokens": 750}
        s = parse(assistant_lines(TS, "claude-test-model", usage, "m1", "r1", blocks=2))
        self.assertEqual(s["cache_creation_tokens"], 1000)
        self.assertEqual(s["cache_creation_1h_tokens"], 750)
