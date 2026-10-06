import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.helpers import assistant_lines, load_module, touch, user_line, write_jsonl

cb = load_module()

USAGE = {"input_tokens": 100, "output_tokens": 50,
         "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 2000}


def make_tree(root, now):
    slug = "-home-dev-git-demo"
    main = [user_line(now - timedelta(minutes=10), "Investigate the parser", session="parent")]
    main += assistant_lines(now - timedelta(minutes=9), "claude-opus-4-7", USAGE, "m-p", "r-p", session="parent")
    p = write_jsonl(root / slug / "parent.jsonl", main)
    touch(p, now - timedelta(minutes=9))
    agent = assistant_lines(now - timedelta(minutes=5), "claude-sonnet-4-6", USAGE, "m-a", "r-a", blocks=3,
                            session="parent", sidechain=True)
    agent += assistant_lines(now - timedelta(minutes=4), "claude-sonnet-4-6", USAGE, "m-b", "r-b",
                             session="parent", sidechain=True)
    a = write_jsonl(root / slug / "parent" / "subagents" / "agent-abc123.jsonl", agent)
    touch(a, now - timedelta(minutes=4))
    # orphan: subagent dir whose parent transcript is gone
    o = write_jsonl(root / slug / "gone" / "subagents" / "agent-zzz.jsonl",
                    assistant_lines(now - timedelta(minutes=3), "claude-sonnet-4-6", USAGE, "m-o", "r-o",
                                    session="gone", sidechain=True))
    touch(o, now - timedelta(minutes=3))


class SubagentRollup(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(tz=timezone.utc)

    def test_rolled_up_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_tree(Path(tmp), self.now)
            sessions = cb.parse_sessions(Path(tmp))
        by_id = {s["id"]: s for s in sessions}
        self.assertEqual(set(by_id), {"parent", "gone/agent-zzz"})
        parent = by_id["parent"]
        self.assertEqual(parent["messages"], 3)          # 1 own + 2 from the agent (3 blocks = 1 msg)
        self.assertEqual(parent["subagents"], 1)
        p_opus = cb.PRICING["claude-opus-4-7"]; p_son = cb.PRICING["claude-sonnet-4-6"]
        one = lambda p: (100 * p["input"] + 50 * p["output"] + 1000 * p["cache_write"] + 2000 * p["cache_read"]) / 1e6
        self.assertAlmostEqual(parent["cost"], one(p_opus) + 2 * one(p_son), places=9)
        self.assertAlmostEqual(parent["subagent_cost"], 2 * one(p_son), places=9)
        self.assertEqual(set(parent["models"]), {"claude-opus-4-7", "claude-sonnet-4-6"})
        self.assertEqual(parent["model"], "claude-opus-4-7")   # dominant model is the parent's own
        # the agent wrote last, so the parent's activity marker moves to the agent's mtime
        self.assertAlmostEqual(parent["mtime"].timestamp(), (self.now - timedelta(minutes=4)).timestamp(), delta=1)
        self.assertTrue(by_id["gone/agent-zzz"]["sidechain"])
        self.assertEqual(by_id["gone/agent-zzz"]["parent_id"], "gone")

    def test_split_lists_agents_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_tree(Path(tmp), self.now)
            sessions = cb.parse_sessions(Path(tmp), split_subagents=True)
        by_id = {s["id"]: s for s in sessions}
        self.assertEqual(set(by_id), {"parent", "parent/agent-abc123", "gone/agent-zzz"})
        self.assertEqual(by_id["parent"]["messages"], 1)
        self.assertEqual(by_id["parent"]["subagents"], 0)
        child = by_id["parent/agent-abc123"]
        self.assertEqual(child["messages"], 2)
        self.assertEqual(child["project"], "git/demo")
        self.assertEqual(child["parent_id"], "parent")
        self.assertEqual(cb.display_project(child), "↳ git/demo")

    def test_cache_is_not_mutated_by_rollup(self):
        cache = {}
        with tempfile.TemporaryDirectory() as tmp:
            make_tree(Path(tmp), self.now)
            first = cb.parse_sessions(Path(tmp), cache=cache)
            second = cb.parse_sessions(Path(tmp), cache=cache)
        p1 = [s for s in first if s["id"] == "parent"][0]
        p2 = [s for s in second if s["id"] == "parent"][0]
        self.assertEqual(p1["messages"], 3)
        self.assertEqual(p2["messages"], 3)   # would be 5 if the cached parent were mutated


if __name__ == "__main__":
    unittest.main()
