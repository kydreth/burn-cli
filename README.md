# codeburn

Terminal dashboard for monitoring Claude Code agent token usage and costs. Reads session logs written by Claude Code locally — no network calls, no API keys.

## Requirements

- Python 3.6+
- Claude Code installed (session logs at `~/.claude/projects/`)

## Installation

```bash
cp codeburn ~/.local/bin/codeburn
chmod +x ~/.local/bin/codeburn
```

Or symlink so edits to the repo are reflected immediately:

```bash
ln -sf ~/git/codeburn/codeburn ~/.local/bin/codeburn
```

## Usage

```
codeburn [options]
```

| Flag | Default | Description |
|---|---|---|
| `--days N` | 7 | Days of history shown in the daily chart |
| `--sessions` | off | Add a per-session breakdown sorted by cost |
| `--watch` / `-w` | off | Auto-refresh the dashboard |
| `--interval N` | 15 | Refresh interval in seconds (with `--watch`) |
| `--project NAME` | all | Filter to sessions whose project path contains NAME |
| `--all-time` | off | Include all sessions (default window is 30 days) |

### Examples

```bash
# Default view — last 7 days
codeburn

# Live dashboard, refresh every 10s
codeburn --watch --interval 10

# Show every session, sorted by cost
codeburn --sessions --all-time

# Zoom in on a specific project
codeburn --project flutter --sessions

# Plain text output (pipe-friendly)
NO_COLOR=1 codeburn
```

## Output sections

**ACTIVE** — Sessions whose log file was modified in the last 10 minutes, showing project, branch, cumulative session cost, age, and model.

**TODAY** — Per-project cost bar chart for the current UTC day, with total tokens, cache hit rate, and output token count.

**LAST N DAYS** — Daily cost bar chart. Costs are colour-coded: green < $1, yellow < $5, orange < $20, red $20+.

**ALL SESSIONS** — (with `--sessions`) Top 30 sessions by cost, showing date, project, branch, cost, turn count, and model.

## How costs are calculated

Each Claude Code session logs token usage in a `.jsonl` file per API turn. codeburn sums four token buckets per assistant turn and multiplies by the per-model rate:

| Bucket | Field in log |
|---|---|
| Fresh input | `input_tokens` |
| Cache write | `cache_creation_input_tokens` |
| Cache read | `cache_read_input_tokens` |
| Output | `output_tokens` |

Pricing table ($/million tokens, as of April 2026):

| Model | Input | Output | Cache write | Cache read |
|---|---|---|---|---|
| claude-opus-4-6 | $15.00 | $75.00 | $18.75 | $1.50 |
| claude-sonnet-4-6 | $3.00 | $15.00 | $3.75 | $0.30 |
| claude-haiku-4-5 | $0.80 | $4.00 | $1.00 | $0.08 |

Unknown models fall back to the Sonnet rate. Update `PRICING` in the script when Anthropic publishes new models or rates.

## Color support

Colors are automatically disabled when stdout is not a TTY or when `NO_COLOR` is set (the [no-color.org](https://no-color.org) convention). Pipe output or set `NO_COLOR=1` for plain text.

## Data source

Session logs live at `~/.claude/projects/<project-slug>/<session-id>.jsonl`. Each line is a JSON object. codeburn reads only the metadata fields (`timestamp`, `usage`, `model`, `cwd`, `gitBranch`) — it never reads or displays message content.
