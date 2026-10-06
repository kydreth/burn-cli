# Security Analysis

**Subject:** codeburn — Claude Code agent usage monitor  
**Scope:** `codeburn` (Python script), `update-pricing` (Python script), `pricing.json`  
**Data sources:** `~/.claude/projects/**/*.jsonl` (local, read-only), LiteLLM GitHub (fetch only during `update-pricing`)

---

## Trust model

codeburn reads files written by Claude Code on the local machine and renders them to a terminal. It makes no network connections at runtime. `update-pricing` makes one outbound HTTPS fetch to LiteLLM's GitHub on demand.

The primary attack surface is untrusted content in the JSONL session files — specifically, string fields written by Claude Code that could contain attacker-controlled values via prompt injection.

A secondary surface is the `update-pricing` fetch: a compromised LiteLLM repository or CDN incident could deliver poisoned pricing data.

---

## Findings

### 1. Terminal escape injection via JSONL field values — **Fixed**

`gitBranch` and `cwd` are read from JSONL and were previously rendered to the terminal without sanitisation. An attacker who could write to the session log (e.g. via prompt injection against Claude Code) could embed ANSI escape sequences to manipulate terminal output.

**Fix:** `_sanitize()` strips CSI sequences (`\x1b[...m`), bare ESC sequences, and all control characters. Applied to `git_branch` and `cwd` at parse time in `_parse_jsonl()`, before any display use.

---

### 2. `update-pricing` merge silently dropped manually added model entries — **Fixed**

The previous merge logic used `if not isinstance(v, dict) or "_" in k` to decide what to keep from the existing file. Model keys use hyphens (`claude-sonnet-4-6`), not underscores, so the `"_" in k` condition was always False for them. Any model entry added by hand was silently deleted on the next `./update-pricing` run, despite the comment claiming otherwise.

**Fix:** Merge now starts with `dict(existing)` and calls `.update(fresh)` — fresh data wins on key conflicts, everything else (including manually added models) survives.

---

### 3. `update-pricing` fetched pricing with no diff shown — **Fixed**

`./update-pricing` previously printed "wrote 20 models" with no indication of what changed. A poisoned LiteLLM update could silently alter rates.

**Fix:** Before writing, the script prints a diff of added (`+`), removed (`-`), and changed (`~`) entries with old and new values. Review the output before accepting the result.

---

### 4. `_lookup_pricing` prefix match created a catch-all poisoning foothold — **Fixed**

The previous implementation included a longest-prefix fallback:

```python
candidates = [k for k in PRICING if model.startswith(k)]
if candidates:
    return PRICING[max(candidates, key=len)]
```

A single short key like `"claude"` in `pricing.json` with wrong rates would silently override costs for every unrecognised Claude model.

**Fix:** Prefix match removed. Lookup is now: exact match → strip 8-digit date suffix → `_FALLBACK_PRICING`. Unknown models get Sonnet rates and a warning to run `./update-pricing`.

---

### 5. `import re` inside hot function — **Fixed**

`import re` was called inside `_lookup_pricing()`, which runs for every assistant turn in every parsed session.

**Fix:** `import re` moved to the top-level imports block.

---

### 6. `update-pricing` fetches from LiteLLM without content integrity check — Open

`./update-pricing` fetches `model_prices_and_context_window.json` from LiteLLM's GitHub over HTTPS. Python's `urllib` verifies TLS certificates by default. However, there is no hash or signature check on the content itself. A compromised LiteLLM repository or a malicious PR could deliver wrong pricing data.

The diff output added in finding #3 provides a human review step. For higher assurance, pin to a specific commit SHA:

```
LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/<sha>"
    "/model_prices_and_context_window.json"
)
```

---

### 7. `pricing.json` is world-readable with default file permissions — Informational

`PRICING_FILE.write_text(...)` uses default permissions (0644). On a multi-user system, other users can read `pricing.json`. The file contains no secrets — only model names and public pricing.

---

### 8. Full conversation content parsed into memory — Low / Guard for future development

Every JSONL line is fully deserialised with `json.loads()` even though only a few keys are read. The full object — including user prompts, assistant responses, and file contents — passes through memory before being discarded. Not a current issue; guard against this when extending `_parse_jsonl()`.

---

### 9. Race condition on active session files — Informational

`path.stat().st_mtime` is read before the file is opened. A write landing between `stat()` and `open()` means the mtime used for "active session" detection may be stale by one refresh cycle. Consequence is cosmetic only.

---

## Installation note

`~/.local/bin/codeburn` must be a symlink to the repository file, not a copy:

```bash
ln -sf ~/git/codeburn/codeburn ~/.local/bin/codeburn
```

A copy silently diverges from the repo on every update. Verify:

```bash
ls -la ~/.local/bin/codeburn
# → ... ~/.local/bin/codeburn -> /path/to/checkout/codeburn
```

---

## Summary

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | Terminal escape injection via JSONL values | Medium | Fixed |
| 2 | Merge logic dropped manually added model entries | Low | Fixed |
| 3 | No diff shown before overwriting pricing.json | Low | Fixed |
| 4 | Prefix match catch-all poisoning foothold | Low | Fixed |
| 5 | `import re` inside hot function | Informational | Fixed |
| 6 | LiteLLM fetch without content integrity check | Low | Open — pin to commit SHA for higher assurance |
| 7 | pricing.json world-readable (default permissions) | Informational | Acceptable — no secrets |
| 8 | Full message content parsed into memory | Low | Acceptable — guard in future changes |
| 9 | Race condition on active session files | Informational | Acceptable — cosmetic only |
