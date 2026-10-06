import unittest
from pathlib import Path

from tests.helpers import FIXTURES, load_module

cb = load_module()


class MultiBlockDedupe(unittest.TestCase):
    """Claude Code writes one JSONL line per content block of an assistant
    message and repeats message.id / requestId / usage on each line. Usage
    must be counted once per message, not once per line."""

    def setUp(self):
        self.session = cb._parse_jsonl(FIXTURES / "multiblock.jsonl", "-home-dev-git-demo", None)
        self.assertIsNotNone(self.session)

    def test_three_block_message_counted_once(self):
        s = self.session
        # msg_fixture_1 (3 lines) + msg_fixture_2 (1 line) = 2 messages
        self.assertEqual(s["messages"], 2)
        self.assertEqual(s["input_tokens"], 100 + 10)
        self.assertEqual(s["output_tokens"], 50 + 20)
        self.assertEqual(s["cache_creation_tokens"], 1000)
        self.assertEqual(s["cache_read_tokens"], 2000 + 3000)

    def test_cost_is_for_unique_messages_only(self):
        p = cb.PRICING["claude-sonnet-4-6"]
        expected = (
            110 / 1e6 * p["input"]
            + 70 / 1e6 * p["output"]
            + 1000 / 1e6 * p["cache_write"]
            + 5000 / 1e6 * p["cache_read"]
        )
        self.assertAlmostEqual(self.session["cost"], expected, places=9)

    def test_metadata(self):
        s = self.session
        self.assertEqual(s["project"], "git/demo")
        self.assertEqual(s["git_branch"], "main")
        self.assertEqual(s["model"], "claude-sonnet-4-6")
        self.assertEqual(s["first_task"], "Add a unit test for the parser")


class DedupeFallbacks(unittest.TestCase):
    def _parse(self, tmp, records):
        from tests.helpers import write_jsonl
        path = write_jsonl(Path(tmp) / "s.jsonl", records)
        return cb._parse_jsonl(path, "-x", None)

    def test_same_message_id_without_request_id_is_one_message(self):
        import tempfile
        from datetime import datetime, timezone
        from tests.helpers import assistant_lines
        ts = datetime(2026, 1, 10, tzinfo=timezone.utc)
        usage = {"input_tokens": 5, "output_tokens": 5}
        lines = assistant_lines(ts, "claude-sonnet-4-6", usage, "msg_a", None, blocks=2)
        for l in lines:
            del l["requestId"]
        with tempfile.TemporaryDirectory() as tmp:
            s = self._parse(tmp, lines)
        self.assertEqual(s["messages"], 1)
        self.assertEqual(s["output_tokens"], 5)

    def test_distinct_messages_are_all_counted(self):
        import tempfile
        from datetime import datetime, timedelta, timezone
        from tests.helpers import assistant_lines
        ts = datetime(2026, 1, 10, tzinfo=timezone.utc)
        usage = {"input_tokens": 1, "output_tokens": 2}
        lines = []
        for i in range(4):
            lines += assistant_lines(ts + timedelta(minutes=i), "claude-sonnet-4-6", usage,
                                     f"msg_{i}", f"req_{i}", blocks=2)
        with tempfile.TemporaryDirectory() as tmp:
            s = self._parse(tmp, lines)
        self.assertEqual(s["messages"], 4)
        self.assertEqual(s["output_tokens"], 8)

    def test_lines_without_any_id_are_each_counted(self):
        import tempfile
        from datetime import datetime, timezone
        from tests.helpers import assistant_lines
        ts = datetime(2026, 1, 10, tzinfo=timezone.utc)
        usage = {"input_tokens": 1, "output_tokens": 2}
        lines = assistant_lines(ts, "claude-sonnet-4-6", usage, None, None, blocks=3)
        for l in lines:
            del l["requestId"]
            del l["message"]["id"]
        with tempfile.TemporaryDirectory() as tmp:
            s = self._parse(tmp, lines)
        # Older logs without ids: nothing to dedupe on, keep the old behaviour
        self.assertEqual(s["messages"], 3)



class PerDayPerModel(unittest.TestCase):
    def test_turns_are_attributed_to_their_own_day_and_model(self):
        import tempfile
        from datetime import datetime, timedelta, timezone
        from tests.helpers import assistant_lines, write_jsonl
        d1 = datetime(2026, 1, 10, 23, 50, tzinfo=timezone.utc)
        d2 = d1 + timedelta(minutes=20)  # crosses midnight into 2026-01-11
        u = {"input_tokens": 10, "output_tokens": 10}
        lines = assistant_lines(d1, "claude-sonnet-4-6", u, "m1", "r1", blocks=2)
        lines += assistant_lines(d2, "claude-opus-4-7", u, "m2", "r2")
        lines += assistant_lines(d2 + timedelta(minutes=1), "claude-opus-4-7", u, "m3", "r3")
        with tempfile.TemporaryDirectory() as tmp:
            s = cb._parse_jsonl(write_jsonl(Path(tmp) / "s.jsonl", lines), "-x", None)
        self.assertEqual(sorted(s["days"]), ["2026-01-10", "2026-01-11"])
        self.assertEqual(s["days"]["2026-01-10"]["messages"], 1)
        self.assertEqual(s["days"]["2026-01-11"]["messages"], 2)
        self.assertEqual(s["models"]["claude-opus-4-7"]["messages"], 2)
        self.assertEqual(s["models"]["claude-sonnet-4-6"]["output"], 10)
        self.assertAlmostEqual(sum(b["cost"] for b in s["days"].values()), s["cost"])
        self.assertAlmostEqual(sum(b["cost"] for b in s["models"].values()), s["cost"])
        self.assertEqual(s["model"], "claude-opus-4-7")

    def test_uncached_cost_treats_all_context_as_fresh_input(self):
        import tempfile
        from datetime import datetime, timezone
        from tests.helpers import assistant_lines, write_jsonl
        ts = datetime(2026, 1, 10, tzinfo=timezone.utc)
        u = {"input_tokens": 0, "output_tokens": 0,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 1_000_000}
        with tempfile.TemporaryDirectory() as tmp:
            s = cb._parse_jsonl(write_jsonl(Path(tmp) / "s.jsonl",
                                            assistant_lines(ts, "claude-sonnet-4-6", u, "m", "r")), "-x", None)
        p = cb.PRICING["claude-sonnet-4-6"]
        self.assertAlmostEqual(s["cost"], p["cache_read"])
        self.assertAlmostEqual(s["uncached_cost"], p["input"])


if __name__ == "__main__":
    unittest.main()
