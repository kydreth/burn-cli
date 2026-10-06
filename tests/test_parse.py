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


if __name__ == "__main__":
    unittest.main()
