# Security Analysis

**Subject:** burn — Claude Code usage and cost dashboard (`burn-cli`)  
**Scope:** `burn_cli/` (Python package), `update-pricing` (Python script), `burn_cli/pricing.json`  
**Data sources:** `~/.claude/projects/**/*.jsonl` (local, read-only); one pinned GitHub raw URL, fetched only when `update-pricing` is run

---

## Trust model

burn reads files written by Claude Code on the local machine and renders
them to a terminal. It makes no network connections at runtime. `update-pricing`
makes one outbound HTTPS fetch on demand.

The primary attack surface is untrusted content in the JSONL session files:
string fields written by Claude Code that could contain attacker-controlled
values via prompt injection (branch names, working directories, the user's
first prompt).

A secondary surface is the `update-pricing` fetch: a compromised upstream
repository or CDN incident could deliver poisoned pricing data.

A third concern is not an attack but a leak: the dashboard can display the
first prompt of each session, and screenshots of it travel.

---

## Comparison with other local usage dashboards

Several open-source tools solve the same problem for multiple agent vendors
(Node/TypeScript TUIs with SQLite reads, currency conversion and export). The
vulnerability classes reported against that category, and their status here:

| Class | Applies? | Status here |
|---|---|---|
| Shell command extraction feeding an execution path | No | No subprocess execution or command parsing anywhere |
| Native database driver vulnerabilities | No | No database; plain JSON lines |
| Unvalidated currency-conversion API | No | No currency conversion |
| Formula injection in CSV export | No | No CSV export; `--json` emits JSON only |
| ReDoS via user-supplied project filter | No | `--project` is a plain substring match; config regexes are the user's own |
| Config-directory environment-variable override | No | `~/.claude/projects/` is hard-coded |
| Terminal injection via dashboard output | Yes | **Fixed** — `_sanitize()` on every JSONL-sourced string |
| Unvalidated upstream pricing fetch | Yes | **Fixed** — pinned commit, mandatory diff review |
| World-readable cache file | Informational | `pricing.json` contains public rates only |

---

## Findings

### 1. Terminal escape injection via JSONL field values — Fixed

`gitBranch`, `cwd` and the first user prompt are rendered to the terminal.
An attacker who can influence the session log (e.g. via prompt injection
against Claude Code) could embed ANSI sequences.

**Fix:** `_sanitize()` strips CSI sequences, bare ESC sequences and all
control characters at parse time, before any display use.

### 2. `update-pricing` merge dropped manually added entries — Fixed

Merge now starts from the existing file and overlays fresh data, so entries
added by hand survive a refresh.

### 3. `update-pricing` wrote without showing a diff — Fixed

The script prints added / removed / changed entries with old and new values.
On a TTY it asks for confirmation; off a TTY it refuses unless `--yes` is
passed. Nothing is written when there are no changes.

### 4. Silent fallback pricing — Fixed

Earlier versions priced any unrecognised model at Sonnet rates with no
warning, and a longest-prefix match let a short key like `"claude"` in
`pricing.json` override every unknown model. Both are gone: lookup is exact
match → date-suffix strip → *unpriced*. Unpriced tokens are counted, warned
about once per model on stderr, and marked `+?` on every figure that
includes them. The fallback rate constant no longer exists.

### 5. `import re` in a hot function — Fixed

Hoisted to module level.

### 6. Upstream pricing fetch without integrity check — Fixed

`update-pricing` now fetches from a pinned commit SHA
(`LITELLM_COMMIT`), not a moving branch. Bumping the pin is a deliberate
edit, and the diff review in finding 3 is the human check on the new
content. TLS is verified by `urllib` as before.

### 7. `pricing.json` is world-readable — Informational

Default permissions (0644). Contents are public rates only.

### 8. Full conversation content parsed into memory — Low / design guard

Each JSONL line is deserialised with `json.loads()` even though only a few
keys are used. Message content passes through memory and is discarded. The
parser retains only metadata and the first prompt (see finding 10). Lines of
types other than `user` and `assistant` are skipped before parsing.

### 9. Race on active session files — Informational

`stat()` precedes `open()`; a write between them can make the "active"
marker stale by one refresh. Cosmetic.

### 10. First prompt shown in TOP RUNS — Mitigated

The first user prompt of each session is useful context but is private text
that ends up in screenshots. It is **hidden by default** and shown only with
`--tasks`. `--redact` replaces prompt text and branch names with short
hashes for anything you intend to share.

### 11. No executable hooks in the repository — Policy

The repository ships no agent configuration, hooks, or issue-tracker
metadata. Agent configuration is kept outside version control, so a clone
never runs commands on session start.

---

## Summary

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | Terminal escape injection via JSONL values | Medium | Fixed |
| 2 | Merge dropped manually added model entries | Low | Fixed |
| 3 | No diff / confirmation before writing pricing.json | Low | Fixed |
| 4 | Silent fallback pricing and prefix-match foothold | Low | Fixed |
| 5 | `import re` in hot function | Informational | Fixed |
| 6 | Upstream fetch without integrity check | Low | Fixed — pinned commit |
| 7 | pricing.json world-readable | Informational | Acceptable |
| 8 | Message content parsed into memory | Low | Acceptable — guarded |
| 9 | Race on active session files | Informational | Acceptable |
| 10 | First prompt visible in dashboard | Low (privacy) | Mitigated — off by default, `--redact` |
| 11 | Executable hooks in repo | — | Policy — none shipped |
