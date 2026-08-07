---
name: jarvis-issues
description: Report bugs and request features for the jarvis MCP server via GitHub issues. Use when jarvis errors, an index fails, a limitation bites, or to request an improvement.
version: "0.1.0"
---

# jarvis issues

Part of the jarvis toolkit. Siblings: `jarvis-setup` (onboard), `jarvis-use` (everyday queries).

File well-formed bug reports and feature requests against **jarvis-intelligence/jarvis-index** on GitHub — jarvis's public issue tracker. (Development happens in a private repo; `jarvis-index` is where the installer, plugin, and binaries are published, and where issues are filed.) This skill targets only the jarvis project itself, not other repos.

## 1. Gather context first

Before drafting, collect:
- The exact command run (e.g. `jarvis index /path --slug foo`).
- Repo + slug, and `jarvis status <slug>` output.
- The tool name + arguments if it was an MCP call (e.g. `findReferences(repo="foo", symbol="bar")`).
- The full error payload — every jarvis tool returns `{"error": "..."}`, copy it verbatim.
- jarvis version: `uv run --python 3.12 --with jarvis-mcp python3 -c "import importlib.metadata; print(importlib.metadata.version('jarvis-mcp'))"` (jarvis has no `--version` flag; this reads it from the distribution metadata. `uv run --with` builds a throwaway env containing the package just for this one command, so it resolves correctly regardless of how jarvis is installed — `uv tool install`, the plugin's `uvx` registration, or anything else — without depending on bare `python3` being able to see an isolated tool venv. Note the distribution is `jarvis-mcp`, not `jarvis`.)
- OS/arch (`uname -s`, `uname -m`).

## 2. Classify — and check known limitations

Decide: **bug**, **feature**, or **known limitation**. Before filing a bug, confirm it isn't one of these already-documented gaps (do NOT file duplicates of these):

- `typeHierarchy` returns an error only on indexes built with an unpatched `scip` — the fix is `jarvis reindex <slug>` after re-running setup.sh, not a bug report. DO file a bug if it still errors on a freshly reindexed repo.
- Single-tenant hardcoding: `config.py` pins `PROJECT = "_"` / `BRANCH = "_"`. Not multi-tenancy.
- One language per repo — no multi-language merge.
- `blastRadius` reports `freshness: unknown` — the package graph has no per-node timestamp.
- Windows unsupported.

If it's a known limitation, say so to the user instead of filing.

## 3. Draft the issue

**Bug template:**

```
**Bug:** <one-line summary>

**Steps to reproduce:**
1. ...

**Expected:** ...
**Actual:** <paste the {"error": ...} payload or stderr>

**Environment:**
- jarvis version: ...
- OS/arch: ...
- repo + slug: ...
- relevant command: ...
```

**Feature template:**

```
**Feature:** <one-line summary>
**Why:** <the concrete use case this unlocks>
**Proposal:** <optional sketch>
```

## 4. File via gh — confirm before submitting

Filing is outward-facing and public. **Always show the drafted title + body to the user and get explicit confirmation before running:**

```bash
gh issue create --repo jarvis-intelligence/jarvis-index --title "<title>" --body "<body>"
```

If `gh` is missing or not authed, stop and tell the user to run `gh auth login` — do not attempt to file another way.

## 5. After filing

Paste the returned issue URL back to the user. If the bug is blocking work, suggest the documented workaround (e.g. fall back to grep for the affected query) rather than waiting on the fix.
