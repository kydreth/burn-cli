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
                project=None, all_time=False, config=None, tasks=False, redact=False)
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
