import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import load_module

cb = load_module()


class ProjectName(unittest.TestCase):
    def setUp(self):
        cb.CONFIG = {}

    def test_plain_repo(self):
        self.assertEqual(cb.project_name("/home/dev/git/demo", "x"), "git/demo")

    def test_claude_worktree_collapses_to_parent(self):
        self.assertEqual(
            cb.project_name("/home/dev/git/demo/.claude/worktrees/brave-gould-6c5d7a", "x"),
            "git/demo")

    def test_sibling_worktree_with_task_id(self):
        self.assertEqual(cb.project_name("/home/dev/git/org/converge-wcs36-wt", "x"), "org/converge")

    def test_sibling_worktree_plain(self):
        self.assertEqual(cb.project_name("/home/dev/git/org/converge-wt", "x"), "org/converge")

    def test_hyphenated_repo_not_mangled(self):
        self.assertEqual(cb.project_name("/home/dev/git/my-workspace", "x"), "git/my-workspace")
        self.assertEqual(cb.project_name("/home/dev/git/wt", "x"), "git/wt")

    def test_slug_fallback(self):
        self.assertEqual(cb.project_name(None, "-home-dev-git-demo"), "demo")


class ProjectMapConfig(unittest.TestCase):
    def tearDown(self):
        cb.CONFIG = {}

    def test_regex_rule_rewrites_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            cfg.write_text(json.dumps({"project_map": [
                {"pattern": r"^org/(.*)-feature-.*$", "replace": r"org/\1"},
                {"pattern": r"^git/scratch.*$", "replace": "scratch"},
            ]}))
            cb.CONFIG = cb.load_config(cfg)
        self.assertEqual(cb.project_name("/home/dev/git/org/converge-feature-login", "x"), "org/converge")
        self.assertEqual(cb.project_name("/home/dev/git/scratch-42", "x"), "scratch")
        self.assertEqual(cb.project_name("/home/dev/git/demo", "x"), "git/demo")

    def test_missing_config_is_empty(self):
        self.assertEqual(cb.load_config(Path("/nonexistent/burn.json")).get("_project_rules", []), [])

    def test_bad_rule_is_ignored(self):
        import contextlib, io
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            cfg.write_text(json.dumps({"project_map": [{"pattern": "("}]}))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                c = cb.load_config(cfg)
        self.assertEqual(c["_project_rules"], [])
        self.assertIn("ignoring", err.getvalue())
