"""burn — terminal dashboard for Claude Code token usage and cost.

Reads the session logs Claude Code writes under ~/.claude/projects and
renders cost per project, session, model and day. No network, no API keys.
"""

__version__ = "0.1.0"

import hashlib
import json
import os
import re
import sys
import time
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

# ── Pricing ($/million tokens) ────────────────────────────────────────────────
_PRICING_FILE = Path(__file__).resolve().parent / "pricing.json"

# Pseudo-model Claude Code logs for locally generated turns (no API call).
_ZERO_COST_MODELS = {"<synthetic>"}

# Per-turn (timestamp, cost) pairs are kept this far back for burn-rate maths.
RECENT_TURNS_SECONDS = 25 * 3600

def _load_pricing():
    if not _PRICING_FILE.exists():
        print(
            f"warning: {_PRICING_FILE} not found — every model will be reported as unpriced.\n"
            f"  Run ./update-pricing to fetch current rates.",
            file=sys.stderr,
        )
        return {}
    try:
        raw = json.loads(_PRICING_FILE.read_text())
        return {k: v for k, v in raw.items() if not k.startswith("_")}
    except (json.JSONDecodeError, OSError) as e:
        print(f"warning: could not load {_PRICING_FILE}: {e}", file=sys.stderr)
        return {}

PRICING = _load_pricing()

def _lookup_pricing(model):
    """Exact match → strip 8-digit date suffix → None (unpriced).

    There is deliberately no fallback rate: a wrong dollar figure is worse
    than an explicit "unpriced" bucket.
    """
    if model in PRICING:
        return PRICING[model]
    # Strip trailing date: claude-haiku-4-5-20251001 → claude-haiku-4-5
    base = re.sub(r"-\d{8}$", "", model)
    if base in PRICING:
        return PRICING[base]
    return None


_warned_models = set()

def _warn_unpriced(model):
    """Warn once per process for each model missing from pricing.json."""
    if model in _warned_models:
        return
    _warned_models.add(model)
    print(
        f"warning: no pricing for model {model!r} — its tokens are counted but shown as unpriced.\n"
        f"  Run ./update-pricing or add an entry to {_PRICING_FILE.name}.",
        file=sys.stderr,
    )


_CTRL_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b[^[]|[\x00-\x1f\x7f]")

def _sanitize(s):
    """Strip ANSI escape sequences and control characters from untrusted strings."""
    return _CTRL_RE.sub("", s) if s else s

# ── Redaction ─────────────────────────────────────────────────────────────────
REDACT = False  # set by --redact: replace prompt text and branch names with hashes

def redact(text):
    """Stable 8-hex-digit stand-in for a private string (same input → same hash)."""
    if not text:
        return text
    return "#" + hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:8]

def display_branch(s):
    b = s.get("git_branch")
    return redact(b) if (REDACT and b) else b

def display_task(s):
    t = s.get("first_task")
    return redact(t) if (REDACT and t) else t

# ── ANSI colours ──────────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
RED    = "\033[38;5;196m"
ORANGE = "\033[38;5;208m"
YELLOW = "\033[38;5;220m"
GREEN  = "\033[38;5;82m"
CYAN   = "\033[38;5;51m"
BLUE   = "\033[38;5;75m"
PURPLE = "\033[38;5;141m"
WHITE  = "\033[38;5;255m"
GRAY   = "\033[38;5;245m"

NO_COLOR = not sys.stdout.isatty() or os.getenv("NO_COLOR")

def c(color, text):
    return text if NO_COLOR else f"{color}{text}{RESET}"

def bar(value, max_value, width=20):
    if max_value == 0:
        filled = 0
    else:
        filled = round(value / max_value * width)
    return "█" * filled + "░" * (width - filled)

# ── Data model ────────────────────────────────────────────────────────────────
def cache_write_split(usage):
    """Return (5m_tokens, 1h_tokens) for a message's cache writes.

    Newer logs carry usage.cache_creation = {ephemeral_5m_input_tokens,
    ephemeral_1h_input_tokens}; older ones only have the total, which is
    assumed to be 5-minute writes.
    """
    total = usage.get("cache_creation_input_tokens", 0) or 0
    breakdown = usage.get("cache_creation") or {}
    one_hour = breakdown.get("ephemeral_1h_input_tokens", 0) or 0
    one_hour = min(one_hour, total)
    return total - one_hour, one_hour

def cache_write_1h_rate(p):
    """1-hour cache writes cost 2x the 5-minute rate unless the entry says otherwise."""
    rate = p.get("cache_write_1h")
    if rate is None:
        rate = (p.get("cache_write") or 0) * 2
    return rate

def calc_cost(model, usage):
    """Return the USD cost of one message, or None if the model is unpriced."""
    if model in _ZERO_COST_MODELS:
        return 0.0
    p = _lookup_pricing(model)
    if p is None:
        _warn_unpriced(model)
        return None
    write_5m, write_1h = cache_write_split(usage)
    cost  = usage.get("input_tokens", 0)            / 1_000_000 * p["input"]
    cost += usage.get("output_tokens", 0)           / 1_000_000 * p["output"]
    cost += write_5m                                / 1_000_000 * (p.get("cache_write") or 0)
    cost += write_1h                                / 1_000_000 * cache_write_1h_rate(p)
    cost += usage.get("cache_read_input_tokens", 0) / 1_000_000 * (p.get("cache_read") or 0)
    return cost

def usage_total(usage):
    return (usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            + usage.get("cache_creation_input_tokens", 0)
            + usage.get("cache_read_input_tokens", 0))

def calc_uncached_cost(model, usage):
    """What the message would have cost with no prompt cache: every context
    token billed as fresh input. None if the model is unpriced."""
    if model in _ZERO_COST_MODELS:
        return 0.0
    p = _lookup_pricing(model)
    if p is None:
        return None
    ctx = (usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
           + usage.get("cache_read_input_tokens", 0))
    return ctx / 1_000_000 * p["input"] + usage.get("output_tokens", 0) / 1_000_000 * p["output"]

BUCKET_KEYS = ("input", "output", "cache_create", "cache_create_1h", "cache_read",
               "cost", "uncached_cost", "unpriced", "messages")

def new_bucket():
    return {k: 0 for k in BUCKET_KEYS}

def bucket_add_usage(b, usage, msg_cost, uncached_cost):
    """Accumulate one (deduplicated) message into a bucket."""
    b["input"]        += usage.get("input_tokens", 0)
    b["output"]       += usage.get("output_tokens", 0)
    b["cache_create"] += usage.get("cache_creation_input_tokens", 0)
    b["cache_create_1h"] += cache_write_split(usage)[1]
    b["cache_read"]   += usage.get("cache_read_input_tokens", 0)
    b["messages"]     += 1
    if msg_cost is None:
        b["unpriced"] += usage_total(usage)
    else:
        b["cost"] += msg_cost
        b["uncached_cost"] += uncached_cost or 0.0

def bucket_merge(dst, src):
    for k in BUCKET_KEYS:
        dst[k] += src[k]
    return dst

def bucket_tokens(b):
    return b["input"] + b["output"] + b["cache_create"] + b["cache_read"]

# ── Config ────────────────────────────────────────────────────────────────────
def default_config_path():
    base = os.getenv("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "burn" / "config.json"

def load_config(path=None):
    """Optional user config. Currently:

      {"project_map": [{"pattern": "<regex>", "replace": "<repl>"}, ...]}

    Each rule is applied to the derived project name with re.sub; the first
    rule that changes the name wins. Missing file → empty config.
    """
    path = Path(path) if path else default_config_path()
    if not path.exists():
        return {}
    try:
        cfg = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"warning: could not read config {path}: {e}", file=sys.stderr)
        return {}
    rules = []
    for rule in cfg.get("project_map", []) or []:
        try:
            rules.append((re.compile(rule["pattern"]), rule.get("replace", "")))
        except (re.error, KeyError, TypeError) as e:
            print(f"warning: ignoring bad project_map rule {rule!r}: {e}", file=sys.stderr)
    cfg["_project_rules"] = rules
    return cfg

CONFIG = {}

# Sibling-directory worktree convention: <repo>-<task-id>-wt or <repo>-wt
_WT_SUFFIX_RE = re.compile(r"^(.+?)(?:-[A-Za-z0-9]+)?-wt$")

def _strip_worktree_suffix(name):
    m = _WT_SUFFIX_RE.match(name)
    return m.group(1) if m else name

def _apply_project_map(name):
    for pattern, repl in CONFIG.get("_project_rules", ()):
        new = pattern.sub(repl, name)
        if new != name:
            return new
    return name

def project_name(cwd, slug):
    """Derive a readable project name from cwd path or slug."""
    name = _raw_project_name(cwd, slug)
    head, sep, tail = name.rpartition("/")
    name = head + sep + _strip_worktree_suffix(tail)
    return _apply_project_map(name)

def _raw_project_name(cwd, slug):
    if cwd:
        parts = cwd.rstrip("/").split("/")
        # Worktree: strip 'worktrees/NAME' suffix (and Claude Code's '.claude' parent)
        if "worktrees" in parts:
            wt_idx = parts.index("worktrees")
            proj_parts = parts[:wt_idx]
            # Claude Code stores worktrees under .claude/worktrees — drop the .claude component
            if proj_parts and proj_parts[-1] == ".claude":
                proj_parts = proj_parts[:-1]
            if len(proj_parts) >= 2:
                return "/".join(proj_parts[-2:])
            elif proj_parts:
                return proj_parts[-1]
        # Normal: last 2 path components (e.g. git/my-workspace)
        if len(parts) >= 2:
            return "/".join(parts[-2:])
        return parts[-1] if parts else slug
    # Fallback: slug is like -Users-foo-git-my-project — not reliable for hyphenated names
    # so just take last segment after common anchors
    parts = [p for p in slug.split("-") if p]
    for anchor in ("git", "projects", "src", "code", "dev"):
        try:
            idx = parts.index(anchor)
            return "-".join(parts[idx+1:]) if idx + 1 < len(parts) else slug
        except ValueError:
            continue
    return "-".join(parts[-3:]) if len(parts) >= 3 else slug

def parse_sessions(projects_dir, since=None, cache=None):
    """
    Returns list of session dicts:
      { id, project, project_slug, git_branch, cwd,
        start_ts, last_ts, mtime, model, messages,
        input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens, cost }

    cache: optional dict mapping (path_str, mtime_float) -> session dict.
    Pass the same dict across calls in watch mode to avoid re-parsing unchanged files.
    """
    sessions = []
    for proj_dir in sorted(projects_dir.iterdir()):
        if not proj_dir.is_dir():
            continue
        for jsonl_path in sorted(proj_dir.glob("*.jsonl")):
            if cache is not None:
                mtime_f = jsonl_path.stat().st_mtime
                key = (str(jsonl_path), mtime_f)
                if key not in cache:
                    cache[key] = _parse_jsonl(jsonl_path, proj_dir.name, since)
                session = cache[key]
            else:
                session = _parse_jsonl(jsonl_path, proj_dir.name, since)
            if session:
                sessions.append(session)
    return sessions

def _parse_jsonl(path, proj_slug, since):
    session_id = path.stem
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

    # Quick reject: if the file hasn't been touched since `since`, skip reading it.
    # mtime is always >= the last appended line's timestamp, so this is safe.
    if since and mtime < since:
        return None

    total = new_bucket()
    day_models = defaultdict(new_bucket)  # ("YYYY-MM-DD", model) -> bucket, per turn
    turns = []                        # (epoch, cost) for recent turns, for burn rate
    turns_cutoff = time.time() - RECENT_TURNS_SECONDS
    unpriced_models = set()
    start_ts = last_ts = None
    sidechain = False
    git_branch = cwd = first_task = None
    model_turns = defaultdict(int)  # model -> turn count, to find dominant model
    msg_count = 0
    # Claude Code writes one JSONL line per content block (text, tool_use, ...)
    # of an assistant message and repeats message.id, requestId and usage on
    # every one of them. Count each API message exactly once.
    seen_messages = set()

    try:
        with open(path, "r", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                # Cheap pre-filter: only user/assistant records carry anything we
                # use. Attachments, hook summaries, snapshots etc. are skipped
                # before the (expensive) JSON parse. Once the first prompt is
                # known, user records (often huge tool results) are skipped too;
                # assistant records carry the same timestamp/cwd/branch fields.
                is_assistant = '"assistant"' in raw
                is_user = (not is_assistant) and '"user"' in raw
                if not (is_assistant or is_user):
                    continue
                if is_user and first_task is not None:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                ts_str = obj.get("timestamp")
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if start_ts is None:
                            start_ts = ts
                        last_ts = ts
                    except ValueError:
                        pass
                if obj.get("isSidechain"):
                    sidechain = True

                if obj.get("gitBranch"):
                    git_branch = _sanitize(obj["gitBranch"])
                if obj.get("cwd"):
                    cwd = _sanitize(obj["cwd"])

                if first_task is None and obj.get("type") == "user":
                    content = obj.get("message", {}).get("content", "")
                    if isinstance(content, str):
                        text = content.strip()
                        if text.startswith("<"):
                            text = ""
                    elif isinstance(content, list):
                        # Skip injected context blocks (ide_opened_file, system tags)
                        text = next(
                            (b.get("text", "").strip() for b in content
                             if isinstance(b, dict) and b.get("type") == "text"
                             and b.get("text", "").strip()
                             and not b["text"].lstrip().startswith("<")),
                            "",
                        )
                    else:
                        text = ""
                    # Skip if it looks like pasted shell output (user@host: prompt)
                    if text and re.match(r"\w+@\w+[^:]*:", text):
                        text = ""
                    if text:
                        first_task = _sanitize(" ".join(text.split()))  # collapse whitespace

                if obj.get("type") == "assistant":
                    msg = obj.get("message", {})
                    turn_model = msg.get("model") or ""
                    usage = msg.get("usage", {})
                    if usage:
                        msg_id = msg.get("id")
                        if msg_id is not None:
                            key = (msg_id, obj.get("requestId"))
                        else:
                            # No API message id (older logs): count every line
                            key = ("line", obj.get("uuid") or len(seen_messages))
                        if key in seen_messages:
                            continue
                        seen_messages.add(key)
                        msg_cost = calc_cost(turn_model, usage)
                        uncached = calc_uncached_cost(turn_model, usage) if msg_cost is not None else None
                        if msg_cost is None:
                            unpriced_models.add(turn_model)
                        bucket_add_usage(total, usage, msg_cost, uncached)
                        day_ts = last_ts or mtime
                        bucket_add_usage(day_models[(day_ts.strftime("%Y-%m-%d"), turn_model)],
                                         usage, msg_cost, uncached)
                        epoch = day_ts.timestamp()
                        if epoch >= turns_cutoff:
                            turns.append((epoch, msg_cost or 0.0))
                        if turn_model not in _ZERO_COST_MODELS:
                            model_turns[turn_model] += 1
                        msg_count += 1
    except (OSError, PermissionError):
        return None

    if msg_count == 0:
        return None  # no assistant turns — skip

    effective_ts = last_ts or mtime
    if since and effective_ts < since:
        return None

    # Dominant model = whichever had the most turns (most representative for display)
    dominant_model = max(model_turns, key=model_turns.__getitem__) if model_turns else "unknown"

    days, models = defaultdict(new_bucket), defaultdict(new_bucket)
    for (day, model), b in day_models.items():
        bucket_merge(days[day], b)
        bucket_merge(models[model], b)

    proj = project_name(cwd, proj_slug)
    return {
        "id":                   session_id,
        "project":              proj,
        "project_slug":         proj_slug,
        "git_branch":           git_branch,
        "first_task":           first_task,
        "cwd":                  cwd,
        "start_ts":             start_ts,
        "last_ts":              last_ts or mtime,
        "mtime":                mtime,
        "model":                dominant_model,
        "sidechain":            sidechain,
        "messages":             msg_count,
        "input_tokens":         total["input"],
        "output_tokens":        total["output"],
        "cache_creation_tokens": total["cache_create"],
        "cache_creation_1h_tokens": total["cache_create_1h"],
        "cache_read_tokens":    total["cache_read"],
        "cost":                 total["cost"],
        "uncached_cost":        total["uncached_cost"],
        "unpriced_tokens":      total["unpriced"],
        "unpriced_models":      sorted(unpriced_models),
        "days":                 dict(days),
        "models":               dict(models),
        "day_models":           dict(day_models),
        "turns":                turns,
    }

# ── Display helpers ───────────────────────────────────────────────────────────
UNPRICED_MARK = "+?"  # appended to a cost when some of its tokens had no pricing

def fmt_cost(d, unpriced=False):
    """Format a dollar figure. `unpriced` truthy appends a marker so a partial
    figure is never mistaken for a complete one."""
    if d < 0.01:
        out = c(GRAY, "<$0.01")
    elif d < 1.0:
        out = c(GREEN, f"${d:.3f}")
    elif d < 5.0:
        out = c(YELLOW, f"${d:.2f}")
    else:
        out = c(ORANGE if d < 20 else RED, f"${d:.2f}")
    if unpriced:
        out += c(RED, UNPRICED_MARK)
    return out

def session_cost(s):
    return fmt_cost(s["cost"], s.get("unpriced_tokens", 0))

def fmt_tokens(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}K"
    return str(n)

def fmt_age(dt):
    now = datetime.now(tz=timezone.utc)
    delta = now - dt
    s = int(delta.total_seconds())
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s//60}m ago"
    if s < 86400:
        return f"{s//3600}h ago"
    return f"{s//86400}d ago"

def header(label):
    print(c(BOLD + CYAN, f"\n  {label}"))
    print(c(GRAY, "  " + "─" * 64))

def _model_color(m):
    if "fable" in m or "mythos" in m:
        return RED
    if "opus" in m:
        return PURPLE
    if "sonnet" in m:
        return BLUE
    if "haiku" in m:
        return GREEN
    return GRAY

def model_badge(m, width=None):
    """Short family badge ("opus") or, with `width`, the padded full model id."""
    if width:
        return c(_model_color(m), m[:width].ljust(width))
    for family in ("fable", "mythos", "opus", "sonnet", "haiku"):
        if family in m:
            return c(_model_color(m), family)
    return c(GRAY, m[:10])

def _render_top_run(s, show_tasks):
    day = s["last_ts"].strftime("%m-%d")
    proj = c(WHITE, s["project"][:24].ljust(25))
    if show_tasks:
        detail = display_task(s) or ""
    else:
        b = display_branch(s)
        detail = f"[{b}]" if b else ""
    detail_str = c(GRAY, (detail[:46] + "…" if len(detail) > 47 else detail.ljust(47)))
    turns = c(GRAY, f"{s['messages']}t".rjust(5))
    print(f"  {c(GRAY, day)}  {proj}  {detail_str}  {session_cost(s)}  {turns}  {model_badge(s['model'])}")


def models_in_window(sessions, now, days):
    """Aggregate per-model buckets over turns in the last `days` UTC days."""
    cutoff = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    out = defaultdict(new_bucket)
    for s in sessions:
        for (day, model), b in s["day_models"].items():
            if day >= cutoff:
                bucket_merge(out[model], b)
    return dict(out)

def render_model_table(by_model):
    priced = {m: b for m, b in by_model.items() if not b["unpriced"] or b["cost"]}
    unpriced_models = sorted(m for m, b in by_model.items() if b["unpriced"])
    total_cost = sum(b["cost"] for b in by_model.values()) or 1.0
    print(c(GRAY, f"  {'model':26}  {'msgs':>6}  {'tokens':>7}  {'cost':>8}  share"))
    for m, b in sorted(priced.items(), key=lambda kv: -kv[1]["cost"]):
        share = b["cost"] / total_cost * 100
        print(f"  {model_badge(m, width=26)}  {c(GRAY, str(b['messages']).rjust(6))}"
              f"  {c(GRAY, fmt_tokens(bucket_tokens(b)).rjust(7))}  {fmt_cost(b['cost']).rjust(8)}"
              f"  {c(PURPLE, bar(b['cost'], total_cost, 12))} {share:3.0f}%")
    if unpriced_models:
        toks = sum(by_model[m]["unpriced"] for m in unpriced_models)
        msgs = sum(by_model[m]["messages"] for m in unpriced_models)
        print(f"  {c(RED, 'unpriced'.ljust(26))}  {c(GRAY, str(msgs).rjust(6))}"
              f"  {c(GRAY, fmt_tokens(toks).rjust(7))}  {c(RED, '—'.rjust(8))}"
              f"  {c(GRAY, ', '.join(unpriced_models))}")


# ── Render functions ──────────────────────────────────────────────────────────
def render(sessions, args):
    now = datetime.now(tz=timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    ten_min_ago = now - timedelta(minutes=10)
    seven_days_ago = now - timedelta(days=7)

    MAX_ACTIVE = 8   # rows shown in ACTIVE before truncation
    MAX_TODAY  = 12  # project rows shown in TODAY before truncation
    TOP_RUNS   = 5   # rows in each TOP RUNS section

    # ── Title bar ─────────────────────────────────────────────────────────────
    if not NO_COLOR:
        print("\033[H\033[2J", end="")  # clear screen
    title = f"  🔥 BURN — Claude Code usage monitor   {today_str}"
    print(c(BOLD + WHITE, title))
    print(c(GRAY, "  " + "═" * 64))

    # ── Active sessions ───────────────────────────────────────────────────────
    active = sorted(
        [s for s in sessions if s["mtime"] >= ten_min_ago],
        key=lambda x: x["mtime"], reverse=True,
    )
    if active:
        header("ACTIVE  (last 10 min)")
        for s in active[:MAX_ACTIVE]:
            b = display_branch(s)
            branch = f" [{b}]" if b else ""
            age = fmt_age(s["mtime"])
            proj_str = c(WHITE, s["project"][:35].ljust(36))
            branch_str = c(GRAY, branch[:20].ljust(21))
            print(f"  {proj_str}{branch_str}  {session_cost(s)}  {c(GRAY, age)}  {model_badge(s['model'])}")
        hidden = len(active) - MAX_ACTIVE
        if hidden > 0:
            print(c(GRAY, f"  + {hidden} more active"))

    # ── Today ─────────────────────────────────────────────────────────────────
    today_sessions = [s for s in sessions if today_str in s["days"]]
    if today_sessions:
        by_proj = defaultdict(new_bucket)
        for s in today_sessions:
            bucket_merge(by_proj[s["project"]], s["days"][today_str])

        sorted_projs = sorted(by_proj.items(), key=lambda x: -x[1]["cost"])
        max_cost = max((v["cost"] for v in by_proj.values()), default=1)
        total_cost = sum(v["cost"] for v in by_proj.values())
        total_in   = sum(v["input"] for v in by_proj.values())
        total_out  = sum(v["output"] for v in by_proj.values())
        total_cc   = sum(v["cache_create"] for v in by_proj.values())
        total_cr   = sum(v["cache_read"] for v in by_proj.values())
        total_unpriced = sum(v["unpriced"] for v in by_proj.values())

        header(f"TODAY  {today_str}")
        for proj, v in sorted_projs[:MAX_TODAY]:
            b = c(ORANGE, bar(v["cost"], max_cost, 18))
            total_toks = v["input"] + v["cache_create"] + v["cache_read"] + v["output"]
            print(f"  {c(WHITE, proj[:35].ljust(36))}  {fmt_cost(v['cost'], v['unpriced'])}  {b}  {c(GRAY, fmt_tokens(total_toks))}")
        hidden_p = len(sorted_projs) - MAX_TODAY
        if hidden_p > 0:
            rest_cost = sum(v["cost"] for _, v in sorted_projs[MAX_TODAY:])
            print(f"  {c(GRAY, f'+ {hidden_p} more  ')}  {fmt_cost(rest_cost)}")

        print()
        total_ctx = total_in + total_cc + total_cr
        pct_cached = (total_cr / (total_ctx + 0.001)) * 100
        print(f"  {c(BOLD, 'TOTAL TODAY:')}  {fmt_cost(total_cost, total_unpriced)}"
              f"   {c(GRAY, fmt_tokens(total_ctx + total_out))} tokens"
              f"   {c(GREEN, fmt_tokens(total_cr))} cached ({pct_cached:.0f}%)"
              f"   {c(GRAY, fmt_tokens(total_out))} output")
        if total_unpriced:
            models = sorted({m for s in today_sessions for m in s["unpriced_models"]})
            print(c(RED, f"  ⚠ {fmt_tokens(total_unpriced)} tokens unpriced ({', '.join(models)}) — "
                         f"total is a lower bound; run ./update-pricing"))

    # ── Per-model table (same window as LAST N DAYS) ──────────────────────────
    by_model = models_in_window(sessions, now, args.days)
    if by_model:
        header(f"MODELS — LAST {args.days} DAYS")
        render_model_table(by_model)

    # ── Top runs — last 7 days ─────────────────────────────────────────────────
    week_sessions = [s for s in sessions if s["last_ts"] >= seven_days_ago]
    if week_sessions:
        top_week = sorted(week_sessions, key=lambda s: -s["cost"])[:TOP_RUNS]
        header("TOP RUNS — LAST 7 DAYS")
        for s in top_week:
            _render_top_run(s, args.tasks)

    # ── Top runs — all scanned ─────────────────────────────────────────────────
    if sessions:
        top_all = sorted(sessions, key=lambda s: -s["cost"])[:TOP_RUNS]
        window_label = "ALL TIME" if args.all_time else "ALL SCANNED (30d)"
        header(f"TOP RUNS — {window_label}")
        for s in top_all:
            _render_top_run(s, args.tasks)

    # ── Daily breakdown (last N days) ─────────────────────────────────────────
    days = args.days
    header(f"LAST {days} DAYS")
    by_day = defaultdict(new_bucket)
    for s in sessions:
        for day, b in s["days"].items():
            bucket_merge(by_day[day], b)

    recent_days = sorted(by_day.keys(), reverse=True)[:days]
    max_day_cost = max((by_day[d]["cost"] for d in recent_days), default=1)
    for day in recent_days:
        v = by_day[day]
        b = c(BLUE, bar(v["cost"], max_day_cost, 20))
        suffix = "  ← today" if day == today_str else ""
        padding = " " * (21 - len(day) - len(suffix))
        print(f"  {c(WHITE, day)}{c(CYAN, suffix)}{padding}  {fmt_cost(v['cost'], v['unpriced'])}  {b}")

    # ── Per-session detail ─────────────────────────────────────────────────────
    if args.sessions:
        limit = args.limit
        ranked = sorted(sessions, key=lambda s: -s["cost"])
        top = ranked[:limit]
        label = "ALL SESSIONS  (most expensive first)"
        if len(ranked) > limit:
            label += f"  — showing {limit} of {len(ranked)}"
        header(label)
        for s in top:
            day = s["last_ts"].strftime("%m-%d")
            b = display_branch(s)
            branch = f"[{b}]" if b else ""
            print(f"  {c(GRAY, day)}  {c(WHITE, s['project'][:28].ljust(29))}"
                  f"  {c(GRAY, branch[:20].ljust(21))}"
                  f"  {session_cost(s)}  {c(GRAY, str(s['messages']) + ' turns')}"
                  f"  {model_badge(s['model'])}")

    # ── Footer ────────────────────────────────────────────────────────────────
    total_all = sum(s["cost"] for s in sessions)
    unpriced_all = sum(s["unpriced_tokens"] for s in sessions)
    print()
    window = "all time" if args.all_time else f"last {max(args.days, 30)}d"
    print(c(GRAY, f"  {len(sessions)} sessions scanned ({window})  ·  total: ") + fmt_cost(total_all, unpriced_all))
    if args.watch:
        print(c(GRAY, f"  Refreshing every {args.interval}s  ·  Ctrl+C to quit"))
    print()


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="burn — Claude Code token usage and cost dashboard",
        prog="burn",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--version",  action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--days",     type=int, default=7,   help="Days of history (default: 7)")
    ap.add_argument("--sessions", action="store_true",   help="Show per-session breakdown")
    ap.add_argument("--limit",    type=int, default=30,  help="Max sessions shown with --sessions (default: 30)")
    ap.add_argument("--watch",  "-w", action="store_true", help="Auto-refresh dashboard")
    ap.add_argument("--interval", type=int, default=15,  help="Refresh interval in seconds (default: 15)")
    ap.add_argument("--project",  type=str, default=None, help="Filter to project substring")
    ap.add_argument("--all-time", action="store_true",   help="Include all sessions (not just last 30 days)")
    ap.add_argument("--tasks",    action="store_true",
                    help="Show each session's first prompt in TOP RUNS (off by default: it is private text)")
    ap.add_argument("--redact",   action="store_true",
                    help="Replace prompt text and branch names with short hashes (for screenshots)")
    ap.add_argument("--config",   type=str, default=None,
                    help=f"Config file (default: {default_config_path()})")
    args = ap.parse_args()

    global CONFIG, REDACT
    CONFIG = load_config(args.config)
    REDACT = args.redact

    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        print(f"No Claude projects directory found at {projects_dir}", file=sys.stderr)
        sys.exit(1)

    since = None if args.all_time else (
        datetime.now(tz=timezone.utc) - timedelta(days=max(args.days, 30))
    )

    if args.interval != 15 and not args.watch:
        print("warning: --interval has no effect without --watch", file=sys.stderr)

    session_cache = {} if args.watch else None

    def load_and_render():
        sessions = parse_sessions(projects_dir, since=since, cache=session_cache)
        if args.project:
            sessions = [s for s in sessions if args.project.lower() in s["project"].lower()]
        render(sessions, args)

    if args.watch:
        try:
            while True:
                load_and_render()
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print()
    else:
        load_and_render()


if __name__ == "__main__":
    main()
