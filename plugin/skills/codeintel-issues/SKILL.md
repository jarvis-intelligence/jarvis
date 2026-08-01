---
name: codeintel-issues
description: Report bugs and request features for the codeintel MCP server via GitHub issues. Use when codeintel errors, an index fails, a limitation bites, or to request an improvement.
version: "0.1.0"
---

# codeintel issues

Part of the codeintel toolkit. Siblings: `codeintel-setup` (onboard), `codeintel-use` (everyday queries).

File well-formed bug reports and feature requests against **phuongddx/codeintel** on GitHub. This skill targets only the codeintel project itself, not other repos.

## 1. Gather context first

Before drafting, collect:
- The exact command run (e.g. `codeintel index /path --slug foo`).
- Repo + slug, and `codeintel status <slug>` output.
- The tool name + arguments if it was an MCP call (e.g. `findReferences(repo="foo", symbol="bar")`).
- The full error payload — every codeintel tool returns `{"error": "..."}`, copy it verbatim.
- codeintel version: `python3 -c "import importlib.metadata; print(importlib.metadata.version('codeintel-navigation-mcp'))"` (codeintel has no `--version` flag; this reads it from the distribution metadata — note the distribution is `codeintel-navigation-mcp`, not `codeintel`).
- OS/arch (`uname -s`, `uname -m`).

## 2. Classify — and check known limitations

Decide: **bug**, **feature**, or **known limitation**. Before filing a bug, confirm it isn't one of these already-documented gaps (do NOT file duplicates of these):

- `typeHierarchy` returns an error on real indexes — upstream `scip expt-convert` never populates `relationships`.
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
- codeintel version: ...
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
gh issue create --repo phuongddx/codeintel --title "<title>" --body "<body>"
```

If `gh` is missing or not authed, stop and tell the user to run `gh auth login` — do not attempt to file another way.

## 5. After filing

Paste the returned issue URL back to the user. If the bug is blocking work, suggest the documented workaround (e.g. fall back to grep for the affected query) rather than waiting on the fix.
