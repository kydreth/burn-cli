import argparse
import contextlib
import io
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.helpers import assistant_lines, load_module, touch, user_line, write_jsonl

cb = load_module()

USAGE = {"input_tokens": 100, "output_tokens": 50,
         "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 2000}


def make_projects(tmp, now):
    """Two projects, three sessions: one active now, one from today, one old."""
    root = Path(tmp)
    sessions = [
        ("-home-dev-git-demo", "active", now - timedelta(minutes=2), "/home/dev/git/demo", "feat/x-1-login"),
        ("-home-dev-git-demo", "earlier", now - timedelta(hours=3), "/home/dev/git/demo", "main"),
        ("-home-dev-git-other", "old", now - timedelta(days=5), "/home/dev/git/other", "main"),
    ]
    for slug, sid, ts, cwd, branch in sessions:
        recs = [user_line(ts - timedelta(seconds=30), f"Please do the {sid} task", cwd=cwd, branch=branch, session=sid)]
        recs += assistant_lines(ts, "claude-sonnet-4-6", USAGE, f"m-{sid}", f"r-{sid}", blocks=2,
                                cwd=cwd, branch=branch, session=sid)
        path = write_jsonl(root / slug / f"{sid}.jsonl", recs)
        touch(path, ts)
    return root


def default_args(**over):
    base = dict(days=7, sessions=False, limit=30, watch=False, interval=15,
                project=None, all_time=False, config=None, tasks=False, redact=False,
                budget=None, since=None, json=False, split_subagents=False)
    base.update(over)
    return argparse.Namespace(**base)


class RenderStreams(unittest.TestCase):
    def test_dashboard_goes_to_stdout_only(self):
        now = datetime.now(tz=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = make_projects(tmp, now)
            sessions = cb.parse_sessions(root)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                cb.render(sessions, default_args(sessions=True))
        self.assertEqual(err.getvalue(), "", "dashboard must not write to stderr")
        text = out.getvalue()
        self.assertIn("ACTIVE", text)
        self.assertIn("TODAY", text)
        self.assertIn("git/demo", text)
        self.assertEqual(len(sessions), 3)

    def test_no_color_output_has_no_escape_codes(self):
        now = datetime.now(tz=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = make_projects(tmp, now)
            sessions = cb.parse_sessions(root)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cb.render(sessions, default_args())
        self.assertNotIn("\x1b", out.getvalue())


class ModelTable(unittest.TestCase):
    def test_models_section_lists_models_and_unpriced_row(self):
        now = datetime.now(tz=timezone.utc)
        cb._warned_models.clear()
        with tempfile.TemporaryDirectory() as tmp:
            root = make_projects(tmp, now)
            recs = assistant_lines(now - timedelta(minutes=5), "claude-unknown-9", USAGE, "mu", "ru",
                                   cwd="/home/dev/git/demo", session="unk")
            path = write_jsonl(root / "-home-dev-git-demo" / "unk.jsonl", recs)
            touch(path, now)
            with contextlib.redirect_stderr(io.StringIO()):
                sessions = cb.parse_sessions(root)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cb.render(sessions, default_args())
        text = out.getvalue()
        self.assertIn("MODELS — LAST 7 DAYS", text)
        self.assertIn("claude-sonnet-4-6", text)
        self.assertRegex(text, r"unpriced\s+1\s+3K\s+—\s+claude-unknown-9")

    def test_models_in_window_respects_days(self):
        now = datetime.now(tz=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            sessions = cb.parse_sessions(make_projects(tmp, now))
        week = cb.models_in_window(sessions, now, 7)
        today = cb.models_in_window(sessions, now, 1)
        self.assertEqual(week["claude-sonnet-4-6"]["messages"], 3)
        self.assertEqual(today["claude-sonnet-4-6"]["messages"], 2)


class BurnRateAndBudget(unittest.TestCase):
    def test_burn_rate_counts_only_last_hour(self):
        now = datetime.now(tz=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            sessions = cb.parse_sessions(make_projects(tmp, now))
        active = [s for s in sessions if s["id"] == "active"]
        earlier = [s for s in sessions if s["id"] == "earlier"]
        self.assertAlmostEqual(cb.burn_rate(active, now), active[0]["cost"])  # 1 msg in last 60 min
        self.assertEqual(cb.burn_rate(earlier, now), 0.0)                      # 3 h ago
        self.assertAlmostEqual(cb.burn_rate(sessions, now, minutes=30), active[0]["cost"] * 2)

    def test_projection_adds_rate_for_hours_left(self):
        now = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
        self.assertAlmostEqual(cb.projected_end_of_day(10.0, 2.0, now), 10.0 + 2.0 * (11 + 59 / 60 + 59 / 3600))
        self.assertAlmostEqual(cb.projected_end_of_day(5.0, 0.0, now), 5.0)

    def test_budget_exit_code_and_message(self):
        now = datetime.now(tz=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            sessions = cb.parse_sessions(make_projects(tmp, now))
        today = now.strftime("%Y-%m-%d")
        spent = cb.today_total_cost(sessions, today)
        self.assertGreater(spent, 0)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            over = cb.render(sessions, default_args(budget=spent / 2))
            under = cb.render(sessions, default_args(budget=spent * 2))
            none = cb.render(sessions, default_args())
        self.assertEqual((over, under, none), (1, 0, 0))
        self.assertIn("BUDGET EXCEEDED", out.getvalue())
        self.assertIn("BURN RATE", out.getvalue())


class JsonReport(unittest.TestCase):
    def test_json_is_the_only_stdout_and_sums_agree(self):
        import json as _json
        now = datetime.now(tz=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            sessions = cb.parse_sessions(make_projects(tmp, now))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cb.render_json(sessions, default_args(json=True, budget=1000))
        self.assertEqual(code, 0)
        self.assertEqual(err.getvalue(), "")
        report = _json.loads(out.getvalue())
        self.assertEqual(len(report["sessions"]), 3)
        self.assertEqual({p["project"] for p in report["projects"]}, {"git/demo", "git/other"})
        total = sum(s["cost"] for s in report["sessions"])
        self.assertAlmostEqual(sum(d["cost"] for d in report["days"]), total, places=5)
        self.assertAlmostEqual(sum(m["cost"] for m in report["models"]), total, places=5)
        self.assertAlmostEqual(report["today"]["cost"], total * 2 / 3, places=5)
        self.assertFalse(report["today"]["budget_exceeded"])
        self.assertNotIn("first_task", report["sessions"][0])   # private by default
        self.assertEqual(report["sessions"][0]["model"], "claude-sonnet-4-6")

    def test_json_budget_exit_and_tasks_redacted(self):
        import json as _json
        now = datetime.now(tz=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            sessions = cb.parse_sessions(make_projects(tmp, now))
            out = io.StringIO()
            cb.REDACT = True
            try:
                with contextlib.redirect_stdout(out):
                    code = cb.render_json(sessions, default_args(json=True, budget=0.0000001, tasks=True))
            finally:
                cb.REDACT = False
        report = _json.loads(out.getvalue())
        self.assertEqual(code, 1)
        self.assertTrue(report["today"]["budget_exceeded"])
        self.assertTrue(all(s["first_task"].startswith("#") for s in report["sessions"]))
        self.assertTrue(all(s["branch"].startswith("#") for s in report["sessions"]))

    def test_restrict_sessions_drops_turns_before_since(self):
        now = datetime.now(tz=timezone.utc)
        old = now - timedelta(days=10)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recs = assistant_lines(old, "claude-sonnet-4-6", USAGE, "m-old", "r-old", session="long")
            recs += assistant_lines(now - timedelta(hours=1), "claude-sonnet-4-6", USAGE, "m-new", "r-new", session="long")
            path = write_jsonl(root / "-home-dev-git-demo" / "long.jsonl", recs)
            touch(path, now)
            [s] = cb.parse_sessions(root)
            self.assertEqual(s["messages"], 2)
            [r] = cb.restrict_sessions([s], now - timedelta(days=2))
        self.assertEqual(r["messages"], 1)
        self.assertAlmostEqual(r["cost"], s["cost"] / 2)
        self.assertEqual(len(r["days"]), 1)
        self.assertEqual(cb.restrict_sessions([s], None), [s])
        self.assertEqual(cb.restrict_sessions([s], now + timedelta(days=1)), [])

    def test_since_filters_old_sessions(self):
        now = datetime.now(tz=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = make_projects(tmp, now)
            all_sessions = cb.parse_sessions(root)
            recent = cb.parse_sessions(root, since=now - timedelta(days=2))
        self.assertEqual(len(all_sessions), 3)
        self.assertEqual({s["id"] for s in recent}, {"active", "earlier"})


class PromptPrivacy(unittest.TestCase):
    def render(self, **over):
        now = datetime.now(tz=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = make_projects(tmp, now)
            sessions = cb.parse_sessions(root)
            out = io.StringIO()
            cb.REDACT = over.pop("redact", False)
            try:
                with contextlib.redirect_stdout(out):
                    cb.render(sessions, default_args(sessions=True, redact=cb.REDACT, **over))
            finally:
                cb.REDACT = False
        return out.getvalue()

    def test_prompt_text_hidden_by_default(self):
        text = self.render()
        self.assertNotIn("Please do the", text)
        self.assertIn("[feat/x-1-login]", text)

    def test_tasks_flag_shows_prompt_text(self):
        text = self.render(tasks=True)
        self.assertIn("Please do the active task", text)

    def test_redact_hashes_prompts_and_branches(self):
        text = self.render(tasks=True, redact=True)
        self.assertNotIn("Please do the", text)
        self.assertNotIn("feat/x-1-login", text)
        self.assertIn(cb.redact("feat/x-1-login"), text)
        self.assertIn(cb.redact("Please do the active task"), text)

    def test_redact_is_stable_and_short(self):
        self.assertEqual(cb.redact("abc"), cb.redact("abc"))
        self.assertRegex(cb.redact("abc"), r"^#[0-9a-f]{8}$")
        self.assertIsNone(cb.redact(None))


if __name__ == "__main__":
    unittest.main()
