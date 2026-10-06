import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tests.helpers import load_module
from tests.test_render import default_args, make_projects

cb = load_module()


class TaskIdFromBranch(unittest.TestCase):
    def setUp(self):
        cb.CONFIG = {}

    def test_default_pattern(self):
        cases = {
            "feat/ABC-123-login-page": "ABC-123",
            "fix/gitops-szr45-retry": "gitops-szr45",
            "chore/x-1": "x-1",
            "docs/proj-9a/nested": "proj-9a",
            "fix/typo-in-readme": None,       # no digit → not a task id
            "main": None,
            "release/v1.2.0": None,
            "": None,
            None: None,
        }
        for branch, expected in cases.items():
            self.assertEqual(cb.task_id_from_branch(branch), expected, branch)

    def test_config_pattern_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "c.json"
            cfg.write_text(json.dumps({"task_id_pattern": r"#(\d+)"}))
            cb.CONFIG = cb.load_config(cfg)
        try:
            self.assertEqual(cb.task_id_from_branch("work-on-#42-thing"), "42")
            self.assertIsNone(cb.task_id_from_branch("feat/ABC-123"))
        finally:
            cb.CONFIG = {}

    def test_bad_config_pattern_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "c.json"
            cfg.write_text(json.dumps({"task_id_pattern": "("}))
            with contextlib.redirect_stderr(io.StringIO()):
                cb.CONFIG = cb.load_config(cfg)
        try:
            self.assertEqual(cb.task_id_from_branch("feat/ABC-123"), "ABC-123")
        finally:
            cb.CONFIG = {}


class ByTaskSection(unittest.TestCase):
    def test_section_and_json(self):
        now = datetime.now(tz=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            sessions = cb.parse_sessions(make_projects(tmp, now))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cb.render(sessions, default_args(by_task=True))
            jout = io.StringIO()
            with contextlib.redirect_stdout(jout):
                cb.render_json(sessions, default_args(json=True))
            plain = io.StringIO()
            with contextlib.redirect_stdout(plain):
                cb.render(sessions, default_args())
        text = out.getvalue()
        self.assertIn("BY TASK — LAST 7 DAYS", text)
        self.assertIn("x-1", text)                       # from branch feat/x-1-login
        self.assertIn(cb.NO_TASK, text)                  # the main-branch sessions
        self.assertNotIn("BY TASK", plain.getvalue())    # opt-in section
        tasks = json.loads(jout.getvalue())["tasks"]
        by_id = {t["task_id"]: t for t in tasks}
        self.assertEqual(by_id["x-1"]["sessions"], 1)
        self.assertEqual(by_id[None]["sessions"], 2)
        [active] = [s for s in sessions if s["id"] == "active"]
        self.assertEqual(active["task_id"], "x-1")

    def test_redact_hashes_task_ids(self):
        now = datetime.now(tz=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            sessions = cb.parse_sessions(make_projects(tmp, now))
        cb.REDACT = True
        try:
            by_task = cb.tasks_in_window(sessions, now, 7)
        finally:
            cb.REDACT = False
        self.assertIn(cb.redact("x-1"), by_task)
        self.assertNotIn("x-1", by_task)


if __name__ == "__main__":
    unittest.main()
