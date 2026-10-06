# burn-cli

Terminal dashboard for Claude Code token usage and cost. It reads the session
logs Claude Code already writes on your machine and shows what each project,
session, model, task and day cost, how fast you are spending right now, and
how much prompt caching is saving you. No network calls, no API keys, nothing
leaves your computer.

## Requirements

- Python 3.8+ (no third-party dependencies)
- Claude Code, with session logs under `~/.claude/projects/`

## Installation

```bash
pipx install burn-cli              # or: pip install burn-cli
```

From a checkout:

```bash
pip install .                      # installs the `burn` command
# or run without installing:
python -m burn_cli
ln -sf "$PWD/bin/burn" ~/.local/bin/burn
```

## Usage

```
burn [options]
```

| Flag | Default | Description |
|---|---|---|
| `--days N` | 7 | Window for the daily chart, MODELS, CACHE and BY TASK sections |
| `--since YYYY-MM-DD` | 30 days ago | Only count turns on/after this UTC date |
| `--all-time` | off | No time window at all |
| `--sessions` | off | Per-session breakdown sorted by cost |
| `--limit N` | 30 | Max sessions shown with `--sessions` |
| `--by-task` | off | Group cost by task id parsed from branch names |
| `--tasks` | off | Show each session's first prompt in TOP RUNS (private text, off by default) |
| `--redact` | off | Replace prompt text and branch names with short hashes |
| `--split-subagents` | off | List subagent transcripts as their own rows instead of rolling them up |
| `--budget USD` | — | Exit 1 and print a warning when today's spend exceeds USD |
| `--json` | off | Machine-readable report instead of the dashboard |
| `--watch` / `-w` | off | Auto-refresh the dashboard |
| `--interval N` | 15 | Refresh interval in seconds (with `--watch`) |
| `--project NAME` | all | Only sessions whose project name contains NAME |
| `--config PATH` | `~/.config/burn/config.json` | Optional config file (see below) |

### Examples

```bash
burn                              # last 7 days
burn --watch --interval 10        # live dashboard
burn --sessions --since 2026-10-01
burn --project flutter --by-task  # one project, grouped by task id
burn --redact --tasks             # safe to screenshot
NO_COLOR=1 burn | less            # plain text; stdout is the only data stream
burn --json | jq '.today'         # scripting
burn --budget 50 >/dev/null || notify "Claude spend over $50 today"
```

Before sharing a screenshot, run with `--redact`: branch names and prompt text
(if you enabled `--tasks`) are replaced with stable hashes.

## Output sections

**ACTIVE** — sessions whose log changed in the last 10 minutes: project,
branch, cumulative cost, burn rate over the last 60 minutes, age, model.

**TODAY** — per-project cost bars for the current UTC day with total tokens,
cache hit rate and output tokens, then a `BURN RATE` line with the combined
$/hour over the last 60 minutes and a projected end-of-day total (today so far
+ current rate × hours left). If any model had no pricing, an explicit
`⚠ … tokens unpriced` line names it and every affected figure carries a `+?`.

**MODELS** — messages, tokens, cost and share of spend per model id, with an
`unpriced` row for anything missing from the price table.

**CACHE** — cache hit rate, the 5-minute vs 1-hour split of cache writes, and
what the same work would have cost with no prompt cache.

**BY TASK** — with `--by-task`: cost grouped by task id (see below).

**TOP RUNS** — most expensive sessions in the last 7 days and in the window.

**LAST N DAYS** — daily cost bars. Green < $1, yellow < $5, orange < $20,
red ≥ $20.

**ALL SESSIONS** — with `--sessions`: date, project, branch, cost, turns
(`+Nag` = rolled-up subagents), cache hit rate, model.

## How costs are calculated

Claude Code logs one JSONL line per content block of an assistant message
and repeats the message's `usage` on each line. burn counts each
message once (deduplicated on `message.id` + `requestId`) and prices five
buckets per message:

| Bucket | Field in log | Rate |
|---|---|---|
| Fresh input | `input_tokens` | `input` |
| Cache write, 5 min | `cache_creation.ephemeral_5m_input_tokens` | `cache_write` |
| Cache write, 1 hour | `cache_creation.ephemeral_1h_input_tokens` | `cache_write_1h` (2× `cache_write` if absent) |
| Cache read | `cache_read_input_tokens` | `cache_read` |
| Output | `output_tokens` | `output` |

Every turn is attributed to the UTC day it happened and to the model that
produced it, so a session that runs past midnight is split correctly.

Rates live in `burn_cli/pricing.json` ($ per million tokens) and are
refreshed with `./update-pricing`, which fetches a pinned commit of LiteLLM's
price table, prints a diff and asks before writing (`--yes` to skip the
prompt). There is **no fallback rate**: a model missing from the table is
counted but reported as *unpriced*, with a one-time warning on stderr, so a
dollar figure is never silently wrong. The pseudo-model `<synthetic>` that
Claude Code logs for local turns is zero-cost.

### Subagents

Subagent transcripts (`<project>/<session-id>/subagents/agent-*.jsonl`) are
rolled into the parent session by default. `--split-subagents` lists them as
their own rows, prefixed `↳`.

### Task ids

A task id is taken from branch names of the form
`feat|fix|docs|chore|refactor/<id>-slug`, where `<id>` is a word, a hyphen
and a segment containing a digit (`ABC-123`, `gitops-szr45`). Override the
regex (first capture group = id) with `task_id_pattern` in the config file.

## Config file

`~/.config/burn/config.json` (or `--config PATH`) is optional:

```json
{
  "project_map": [
    { "pattern": "^org/(.*)-feature-.*$", "replace": "org/\\1" }
  ],
  "task_id_pattern": "^[a-z]+/([A-Z]+-\\d+)"
}
```

`project_map` rules are regexes applied to the derived project name
(`<parent-dir>/<repo>`); the first rule that changes the name wins. Claude
Code's `.claude/worktrees/<name>` directories and sibling worktrees named
`<repo>-<id>-wt` are folded into their parent project automatically.

## JSON output

`--json` prints one object: `today` (cost, unpriced tokens, burn rate,
projection, budget status), `cache`, and arrays `sessions`, `projects`,
`days`, `models`, `tasks`, all restricted to the active window. `first_task`
is included only with `--tasks`; `--redact` applies to JSON too.

## Colour and streams

Colours are disabled when stdout is not a TTY or `NO_COLOR` is set
([no-color.org](https://no-color.org)). The dashboard and the JSON report are
written to stdout only; warnings go to stderr.

## Data source and privacy

Session logs live at `~/.claude/projects/<project-slug>/<session-id>.jsonl`.
burn reads metadata fields only (`timestamp`, `usage`, `model`, `cwd`,
`gitBranch`, message and request ids) plus, when `--tasks` is given, the first
user prompt. Nothing is stored or transmitted.

## Development

```bash
python3 -m unittest -q
```

Tests use hand-built synthetic fixtures in `tests/`. Never commit a real
session log.

## License

MIT — see [LICENSE](LICENSE).
