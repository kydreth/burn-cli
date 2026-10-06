# burn

Terminal dashboard for Claude Code token usage and cost. It reads the session
logs Claude Code already writes on your machine and shows what each project,
session, model and day cost. No network calls, no API keys, nothing leaves
your computer.

> **Name note.** An unrelated public package called `codeburn` exists. This
> project is not affiliated with it and is published as **`burn-cli`**.

## Requirements

- Python 3.8+ (no third-party dependencies)
- Claude Code, with session logs under `~/.claude/projects/`

## Installation

```bash
pipx install burn-cli        # or: pip install burn-cli
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
| `--days N` | 7 | Days of history shown in the daily chart |
| `--sessions` | off | Add a per-session breakdown sorted by cost |
| `--limit N` | 30 | Max sessions shown with `--sessions` |
| `--watch` / `-w` | off | Auto-refresh the dashboard |
| `--interval N` | 15 | Refresh interval in seconds (with `--watch`) |
| `--project NAME` | all | Filter to sessions whose project name contains NAME |
| `--all-time` | off | Include all sessions (default window is 30 days) |
| `--config PATH` | `~/.config/burn/config.json` | Optional config file (see below) |

### Examples

```bash
burn                              # last 7 days
burn --watch --interval 10        # live dashboard
burn --sessions --all-time        # every session, most expensive first
burn --project flutter --sessions # one project
NO_COLOR=1 burn | less            # plain text; stdout is the only data stream
```

## Output sections

**ACTIVE** — sessions whose log was modified in the last 10 minutes: project,
branch, cumulative cost, age, model.

**TODAY** — per-project cost bars for the current UTC day with total tokens,
cache hit rate and output tokens. If any model had no pricing, an explicit
`⚠ … tokens unpriced` line names it and the total is marked `+?`.

**TOP RUNS** — most expensive sessions in the last 7 days and in the whole
scanned window.

**LAST N DAYS** — daily cost bars. Green < $1, yellow < $5, orange < $20,
red ≥ $20.

**ALL SESSIONS** — with `--sessions`: date, project, branch, cost, turns, model.

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

Rates live in `burn_cli/pricing.json` ($ per million tokens) and are
refreshed with `./update-pricing`, which fetches a pinned commit of LiteLLM's
price table, prints a diff and asks before writing (`--yes` to skip the
prompt). There is **no fallback rate**: a model missing from the table is
counted but reported as *unpriced*, with a one-time warning on stderr, so a
dollar figure is never silently wrong. The pseudo-model `<synthetic>` that
Claude Code logs for local turns is zero-cost.

## Config file

`~/.config/burn/config.json` (or `--config PATH`) is optional:

```json
{
  "project_map": [
    { "pattern": "^org/(.*)-feature-.*$", "replace": "org/\\1" }
  ]
}
```

`project_map` rules are regexes applied to the derived project name
(`<parent-dir>/<repo>`); the first rule that changes the name wins. Claude
Code's `.claude/worktrees/<name>` directories and sibling worktrees named
`<repo>-<id>-wt` are folded into their parent project automatically.

## Colour and streams

Colours are disabled when stdout is not a TTY or `NO_COLOR` is set
([no-color.org](https://no-color.org)). The dashboard is written to stdout
only; warnings go to stderr.

## Data source and privacy

Session logs live at `~/.claude/projects/<project-slug>/<session-id>.jsonl`.
burn reads metadata fields only (`timestamp`, `usage`, `model`, `cwd`,
`gitBranch`, message and request ids) and never stores or transmits message
content.

## Development

```bash
python3 -m unittest -q
```

Tests use hand-built synthetic fixtures in `tests/`. Never commit a real
session log.

## License

MIT — see [LICENSE](LICENSE).
