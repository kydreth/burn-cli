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
                project=None, all_time=False, config=None)
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


if __name__ == "__main__":
    unittest.main()
