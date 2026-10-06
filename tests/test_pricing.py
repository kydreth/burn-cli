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
