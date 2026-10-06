"""Shared helpers for the test-suite.

Fixtures are synthetic. Never copy real session logs from ~/.claude/projects
into this directory.
"""

import importlib.machinery
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_module():
    """Import the CLI script (which has no .py suffix) as a module."""
    name = "codeburn_under_test"
    if name in sys.modules:
        return sys.modules[name]
    path = REPO_ROOT / "codeburn"
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def user_line(ts, text, cwd="/home/dev/git/demo", branch="main", session="s1"):
    return {
        "type": "user",
        "uuid": f"u-{ts.timestamp():.0f}",
        "timestamp": iso(ts),
        "cwd": cwd,
        "gitBranch": branch,
        "sessionId": session,
        "isSidechain": False,
        "message": {"role": "user", "content": text},
    }


def assistant_lines(ts, model, usage, msg_id, request_id, blocks=1,
                    cwd="/home/dev/git/demo", branch="main", session="s1",
                    sidechain=False):
    """Emit `blocks` JSONL records for ONE assistant message.

    Mirrors Claude Code, which writes one line per content block and repeats
    the same message.id, requestId and usage on each of them.
    """
    out = []
    for i in range(blocks):
        out.append({
            "type": "assistant",
            "uuid": f"a-{msg_id}-{i}",
            "timestamp": iso(ts + timedelta(seconds=i)),
            "cwd": cwd,
            "gitBranch": branch,
            "sessionId": session,
            "isSidechain": sidechain,
            "requestId": request_id,
            "apiBlockIndex": i,
            "message": {
                "id": msg_id,
                "model": model,
                "role": "assistant",
                "type": "message",
                "content": [{"type": "text", "text": f"block {i}"}],
                "usage": dict(usage),
            },
        })
    return out


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


def touch(path, when):
    """Set mtime so 'active' / 'since' logic sees the session as recent."""
    t = when.timestamp()
    os.utime(path, (t, t))
