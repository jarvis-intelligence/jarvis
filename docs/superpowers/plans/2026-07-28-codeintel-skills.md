# codeintel Skill Toolkit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship three `codeintel-*` agent skills (setup / use / issues) plus a Python helper that symlinks them into `~/.zcode/skills/`, compliant with `ck:skill-creator` v4.0.0 (eval harness deferred).

**Architecture:** Three self-contained skills under `.claude/skills/` (the standard's required location). Each is a single `SKILL.md` (<300 lines) with ≤200-char pushy descriptions. `codeintel-use` spills 8-tool detail to `references/tool-roster.md` (progressive disclosure). A Python helper `scripts/link_skills.py` symlinks each skill dir into `~/.zcode/skills/` so a ZCode agent loads them; repo files remain the single source of truth.

**Tech Stack:** Markdown + YAML frontmatter (skills); Python 3 stdlib only (`link_skills.py`); pytest (test).

## Global Constraints

- Skills live in `.claude/skills/codeintel-<name>/SKILL.md` (NOT `~/.claude/skills/`, NOT repo-root `skills/`).
- Each `description` frontmatter field ≤200 chars (verified values: setup 184, use 195, issues 173).
- Each `SKILL.md` body <300 lines.
- Frontmatter requires `name` + `description`; `version: "0.1.0"` added on all three.
- Imperative voice ("To index a repo, run…"), third-person metadata.
- No duplicated content across siblings — cross-links are one-line pointers only.
- Scripts are Python (not Bash); the helper has a pytest test under `tests/` (repo convention: `testpaths = ["tests"]`).
- `link_skills.py` is stdlib-only (no deps beyond Python 3.9+, the project's floor).
- Never instruct the agent to mutate a published `index-<sha>.db` or auto-submit `gh issue create` without user confirmation.

---

## File Structure

```
.claude/skills/
├── codeintel-setup/
│   └── SKILL.md                 # onboarding runbook (Task 2)
├── codeintel-use/
│   ├── SKILL.md                 # everyday decision matrix + prefer-codeintel rule (Task 3)
│   └── references/
│       └── tool-roster.md       # 8-tool detail, progressive disclosure (Task 3)
└── codeintel-issues/
    └── SKILL.md                 # file codeintel bugs via gh (Task 4)
scripts/
└── link_skills.py               # symlink helper (Task 1)
tests/
└── test_link_skills.py          # test for helper (Task 1)
```

**Responsibilities:**
- `link_skills.py` — sole executable; idempotently links `.claude/skills/codeintel-*` → `~/.zcode/skills/`.
- `codeintel-setup/SKILL.md` — linear install → register → index → verify runbook. Owns onboarding.
- `codeintel-use/SKILL.md` — decision matrix + freshness flow; owns everyday structural queries.
- `codeintel-use/references/tool-roster.md` — per-tool signatures/returns; loaded on demand.
- `codeintel-issues/SKILL.md` — gather/classify/draft/file flow; owns the feedback loop.

---

### Task 1: `link_skills.py` helper + test (TDD)

**Files:**
- Create: `scripts/link_skills.py`
- Create: `tests/test_link_skills.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces: `link_skills(repo_root: Path, target_dir: Path) -> list[str]` — creates one symlink per `.claude/skills/codeintel-*` dir into `target_dir`, returns the list of created symlink paths. Idempotent: skips names already linked. CLI entry `python -m`-style `main()` prints a summary and exits 0.

- [ ] **Step 1: Write the failing test**

Create `tests/test_link_skills.py`:

```python
"""Tests for scripts/link_skills.py — symlink helper for codeintel skills."""
from pathlib import Path

import importlib.util
import sys


def _load_link_skills():
    """Load scripts/link_skills.py as a module (it's not a package)."""
    spec = importlib.util.spec_from_file_location(
        "link_skills", Path(__file__).resolve().parent.parent / "scripts" / "link_skills.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["link_skills"] = module
    spec.loader.exec_module(module)
    return module


def _make_skill(repo_root: Path, name: str) -> None:
    skill_dir = repo_root / ".claude" / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\ndescription: x\n---\n# {name}\n")


def test_creates_symlink_for_each_codeintel_skill(tmp_path):
    link_skills = _load_link_skills()
    repo_root = tmp_path / "repo"
    target = tmp_path / "target"
    for name in ("codeintel-setup", "codeintel-use", "codeintel-issues"):
        _make_skill(repo_root, name)
    # a non-codeintel skill must be ignored
    _make_skill(repo_root, "other-skill")

    created = link_skills.link_skills(repo_root, target)

    assert sorted(Path(p).name for p in created) == [
        "codeintel-issues",
        "codeintel-setup",
        "codeintel-use",
    ]
    for name in ("codeintel-setup", "codeintel-use", "codeintel-issues"):
        link = target / name
        assert link.is_symlink()
        assert (link / "SKILL.md").exists()


def test_idempotent_skips_existing_links(tmp_path):
    link_skills = _load_link_skills()
    repo_root = tmp_path / "repo"
    target = tmp_path / "target"
    _make_skill(repo_root, "codeintel-setup")

    first = link_skills.link_skills(repo_root, target)
    second = link_skills.link_skills(repo_root, target)

    assert len(first) == 1
    assert second == []  # nothing new created on second run
    assert (target / "codeintel-setup" / "SKILL.md").exists()  # still linked


def test_replaces_broken_symlink(tmp_path):
    link_skills = _load_link_skills()
    repo_root = tmp_path / "repo"
    target = tmp_path / "target"
    _make_skill(repo_root, "codeintel-setup")
    # pre-create a broken symlink at the target
    target.mkdir()
    (target / "codeintel-setup").symlink_to(tmp_path / "does-not-exist")

    created = link_skills.link_skills(repo_root, target)

    assert len(created) == 1
    link = target / "codeintel-setup"
    assert link.is_symlink()
    assert (link / "SKILL.md").exists()  # now resolves
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_link_skills.py -v`
Expected: FAIL — `ModuleNotFoundError` / file-not-found loading `scripts/link_skills.py`.

- [ ] **Step 3: Write minimal implementation**

Create `scripts/link_skills.py`:

```python
"""Symlink codeintel skills (.claude/skills/codeintel-*) into a target dir.

The skills live in .claude/skills/ (ck:skill-creator's required location, for
Claude Code auto-discovery). A ZCode agent loads from ~/.zcode/skills/ instead,
so this creates one symlink per codeintel skill there. The repo file stays the
single source of truth; the symlink is only a loader hook.

Idempotent: existing valid links are skipped; broken symlinks are replaced.

Usage:
    python scripts/link_skills.py [--target ~/.zcode/skills]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SKILL_PREFIX = "codeintel-"
DEFAULT_TARGET = Path.home() / ".zcode" / "skills"


def link_skills(repo_root: Path, target_dir: Path) -> list[str]:
    """Create one symlink per `.claude/skills/codeintel-*` dir in target_dir.

    Returns the paths of symlinks created on this run (empty if all already
    linked). Existing valid symlinks are skipped; broken symlinks are replaced.
    """
    skills_root = repo_root / ".claude" / "skills"
    if not skills_root.is_dir():
        raise FileNotFoundError(f"no .claude/skills/ dir at {repo_root}")

    target_dir.mkdir(parents=True, exist_ok=True)
    created: list[str] = []

    for skill_dir in sorted(skills_root.iterdir()):
        if not skill_dir.is_dir() or not skill_dir.name.startswith(SKILL_PREFIX):
            continue
        link = target_dir / skill_dir.name
        # Skip an existing, valid symlink.
        if link.is_symlink() and link.exists():
            continue
        # Replace a broken symlink (or stale file) so re-runs self-heal.
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(skill_dir.resolve())
        created.append(str(link))

    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target", type=Path, default=DEFAULT_TARGET,
        help=f"dir to symlink skills into (default: {DEFAULT_TARGET})",
    )
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parent.parent,
        help="repo root containing .claude/skills/ (default: this repo)",
    )
    args = parser.parse_args(argv)

    try:
        created = link_skills(args.repo_root, args.target)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if created:
        print(f"Linked {len(created)} skill(s) into {args.target}:")
        for path in created:
            print(f"  {path}")
    else:
        print(f"All codeintel skills already linked in {args.target}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_link_skills.py -v`
Expected: PASS — 3 tests (`test_creates_symlink_for_each_codeintel_skill`, `test_idempotent_skips_existing_links`, `test_replaces_broken_symlink`).

- [ ] **Step 5: Confirm no broader test regression**

Run: `uv run pytest -q`
Expected: PASS — all pre-existing tests still green; the 3 new tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/link_skills.py tests/test_link_skills.py
git commit -m "feat(skills): add link_skills.py symlink helper

Idempotently links .claude/skills/codeintel-* into ~/.zcode/skills/ so a
ZCode agent loads them; repo files stay the single source of truth."
```

---

### Task 2: `codeintel-setup` skill

**Files:**
- Create: `.claude/skills/codeintel-setup/SKILL.md`

**Interfaces:**
- Consumes: codeintel's documented install/CLI surface (`setup.sh`, `uv sync`, `codeintel index`, `codeintel status`; CLI flags `--slug`, `--scheme`).
- Produces: a skill that triggers on onboarding intents. Points users to `codeintel-use` (Task 3) and `codeintel-issues` (Task 4) by name.

- [ ] **Step 1: Write the SKILL.md**

Create `.claude/skills/codeintel-setup/SKILL.md`:

```markdown
---
name: codeintel-setup
description: Install and configure codeintel, the local-first code-intelligence MCP server. Use when onboarding, running setup.sh, registering the MCP server, or indexing a repo for the first time.
version: "0.1.0"
---

# codeintel setup

Part of the codeintel toolkit. Siblings: `codeintel-use` (everyday queries), `codeintel-issues` (report bugs).

To take a machine from zero to "codeintel answering queries", run these in order.

## 1. Check prerequisites

- **OS:** macOS or Linux. codeintel does not support Windows.
- **`uv`:** run `uv --version`. If missing, install from https://docs.astral.sh/uv/.
- **PATH:** after install (step 2), `~/.codeintel/bin` must be on `PATH`. Verify with `command -v scip`.

## 2. Install codeintel + external binaries

```bash
curl -fsSL https://raw.githubusercontent.com/phuongddx/codeintel/main/setup.sh | sh
uv sync
```

`setup.sh` installs every binary codeintel needs into `~/.codeintel/bin` and appends it to the shell rc. It is idempotent — re-running skips what's present. Options: `--only <name>` (one dependency), `--force` (reinstall), `--help`.

Binaries installed: `scip` (≥ v0.9.0, SQLite conversion), `zoekt-index` / `zoekt-webserver` (search), and one indexer per language: `scip-typescript`, `scip-python`, `scip-swift` (macOS arm64 only), `scip-java` (detect-only).

## 3. Register the MCP server

Claude Code:

```bash
claude mcp add codeintel --scope user -- uv --directory /path/to/codeintel run codeintel-server
```

Cursor / other MCP clients: point them at the stdio command `uv --directory /path/to/codeintel run codeintel-server`. No HTTP server, no auth, no network.

## 4. Index a repo

```bash
uv run codeintel index /path/to/your/repo            # slug = directory name
uv run codeintel index /path/to/your/repo --slug foo # explicit slug
uv run codeintel index /path/to/your/repo --scheme MyScheme  # Swift, ambiguous Xcode scheme
```

Language is detected by counting source files per extension — **one language per index** (no multi-language merge). For a Swift repo with a checked-in `.xcodeproj`/`.xcworkspace`, codeintel auto-uses `xcodebuild`; pass `--scheme` on the first index if there is more than one scheme (it's persisted, so `reindex`/`watch` reuse it).

## 5. Verify

```bash
uv run codeintel status <slug>     # expect status: indexed
```

Then call a tool through the MCP client, e.g. `goToDefinition(repo: "<slug>", symbol: "main")`. A non-error response with a `definitions` array means the pipeline works end to end.

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `command not found: scip` / `zoekt-index` | `~/.codeintel/bin` not on `PATH`. Open a new shell, or `source ~/.zshrc` (or `~/.bashrc`). |
| `scip` version < v0.9.0 | Re-run `setup.sh --only scip --force`. Older converters silently drop occurrence ranges; `codeintel index` refuses them. |
| Swift: "multiple schemes" / wrong build | Pass `--scheme <name>` on the first `codeintel index`. It's stored in the registry and reused by `reindex`/`watch`. |
| `status: partial` | The index published symbols but no navigable positions (indexer/converter bug). Re-read the stderr from `codeintel index`; reindex after fixing. |
| `status: failed` | Re-run `codeintel index <slug>` and read stderr; the atomic-publish guarantee means the previous good index (if any) is still live. |

## 7. Next

Onboarding done. For everyday structural queries (find references, go-to-definition, call hierarchy), see `codeintel-use`. To report a bug, see `codeintel-issues`.
```

- [ ] **Step 2: Validate against ck:skill-creator requirements**

Run these checks; all must pass:

```bash
f=.claude/skills/codeintel-setup/SKILL.md
# description ≤ 200 chars
desc=$(awk '/^description:/{sub(/^description: /,""); print; exit}' "$f")
echo "${#desc} chars (must be ≤200)"
# body < 300 lines (lines after the closing --- of frontmatter)
tail -n +4 "$f" | awk 'c>=2{print} /^---$/{c++}' | wc -l | awk '{print $1" body lines (must be <300)"}'
```

Expected: description ≤200 chars; body <300 lines.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/codeintel-setup/SKILL.md
git commit -m "feat(skills): add codeintel-setup onboarding skill"
```

---

### Task 3: `codeintel-use` skill + tool roster

**Files:**
- Create: `.claude/skills/codeintel-use/SKILL.md`
- Create: `.claude/skills/codeintel-use/references/tool-roster.md`

**Interfaces:**
- Consumes: the 8 MCP tools registered in `src/codeintel/server.py` (`documentSymbols`, `goToDefinition`, `findReferences`, `callHierarchy`, `typeHierarchy`, `getIndexStatus`, `searchCode`, `blastRadius`) and the CLI (`codeintel reindex <slug>`).
- Produces: a skill that triggers on structural-query intents. References `references/tool-roster.md` via a grep-pointer.

- [ ] **Step 1: Write the SKILL.md (decision matrix + rule)**

Create `.claude/skills/codeintel-use/SKILL.md`:

```markdown
---
name: codeintel-use
description: Use codeintel MCP tools for code structure queries: finding references, go-to-definition, call/type hierarchy, who calls a function, where a symbol is defined, document symbols. Prefer over grep.
version: "0.1.0"
---

# codeintel everyday use

Part of the codeintel toolkit. Siblings: `codeintel-setup` (onboard), `codeintel-issues` (report bugs).

## Decision matrix

For any **structural** code question, prefer the codeintel tool over grep. `repo` is the slug from `codeintel index`.

| Question | codeintel tool | Fallback |
|---|---|---|
| Where is `X` defined? | `goToDefinition(repo, X)` | grep |
| Who calls / uses `X`? | `findReferences(repo, X)` | grep |
| What calls `X` / what `X` calls? | `callHierarchy(repo, X)` | grep |
| Super/subtypes of `X`? | `typeHierarchy(repo, X)` | (often errors — see gotchas) |
| Symbols in a file? | `documentSymbols(repo, path)` | grep |
| Is this repo indexed? | `getIndexStatus(repo, repo_path)` | — |
| Cross-repo dependents of a package? | `blastRadius(repo, pkg)` | — |
| Lexical text search? | grep **or** `searchCode(query, repo?)` | — |

Full signatures and return shapes: `grep -nA20 "## Tool detail" references/tool-roster.md` (loaded on demand).

## The prefer-codeintel rule

Before any structural tool call, check freshness:

1. Call `getIndexStatus(repo, repo_path)` — pass `repo_path` = the repo's local git working dir to compare against `git rev-parse HEAD`.
2. Branch on the result:
   - **indexed + fresh** → call the structural tool now.
   - **indexed + stale** → run `uv run codeintel reindex <slug>`, then call the tool.
   - **not indexed** → fall back to grep for this query; offer to index (`codeintel index <path>`).
3. For **text** search (not structure), use grep or `searchCode` — no preference between them.

## Gotchas

- **`typeHierarchy` errors on real indexes.** Upstream `scip expt-convert` never populates `relationships`, so the tool returns an explicit error (not a bug, not "no supertypes"). Do not file this as a bug; it's a known upstream gap.
- **`blastRadius` only sees already-indexed repos.** Index the dependency first, or re-run `codeintel index`/`reindex` after indexing it, for an edge to appear.
- **One language per repo.** No multi-language merge — a polyglot repo indexes only its plurality language.
- **Every tool returns `{"error": "..."}` on failure, never raises.** Check for an `error` key before reading results.
- **Queries never write.** Published indexes are opened read-only; never try to mutate an `index-<sha>.db`.

## Trigger examples (lightweight validation)

Should trigger: "find all callers of `index_repo`", "where is `QueryService` defined", "call hierarchy of `blast_radius`", "list symbols in server.py".
Should NOT trigger: "search for the string TODO" (text → grep/searchCode), "how do I install codeintel" (→ codeintel-setup).
```

- [ ] **Step 2: Write `references/tool-roster.md`**

Create `.claude/skills/codeintel-use/references/tool-roster.md`:

```markdown
# codeintel tool roster

The 8 MCP tools registered by `codeintel-server`. All take `repo` (the slug from `codeintel index`). On failure every tool returns `{"error": "..."}` rather than raising.

## Tool detail

### documentSymbols(repo, path) → dict
Every top-level symbol defined in `path` within `repo`, each with its range.
Returns: `{"path": ..., "symbols": [{...}], "freshness": {...}}`.

### goToDefinition(repo, symbol) → dict
Resolve `symbol`'s definition location(s) within `repo`.
Returns: `{"symbol": ..., "definitions": [{...}], "freshness": {...}}`.

### findReferences(repo, symbol) → dict
Every occurrence of `symbol` within `repo`, definition sites included.
Returns: `{"symbol": ..., "references": [{...}], "freshness": {...}}`.

### callHierarchy(repo, symbol) → dict
Single-level incoming + outgoing call hierarchy for `symbol`.
Returns: `{"symbol": ..., "incomingCalls": [...], "outgoingCalls": [...], "freshness": {...}}`.

### typeHierarchy(repo, symbol) → dict
Single-level super/subtypes for `symbol`. **Returns an `error` on real indexes** — upstream `scip expt-convert` never populates `relationships`. Treat the error as "unavailable", not as "no supertypes".

### getIndexStatus(repo, repo_path=None) → dict
Whether `repo` has a published index, plus freshness. Pass `repo_path` (the repo's local git dir) to compare the published commit against `git rev-parse HEAD`.
Returns: `{"repo": ..., "indexed": bool, "freshness": {...}}`. Without `repo_path`, freshness is reported without a staleness check (never `stale: true` without evidence).

### searchCode(query, repo=None) → dict
Lexical search via an embedded Zoekt index (lazy-started on first call). `repo`, if given, is applied as a Zoekt `r:` filter scoping results to that one indexed repo.
Returns: `{"query": ..., "hits": [{"repo","path","lineNumber","lineText"}], "total": int}`.

### blastRadius(repo, symbol_or_package) → dict
2-hop bounded BFS over the package dependency graph: every other indexed repo whose package directly (1 hop) or transitively through one intermediary (2 hops) depends on `symbol_or_package` as registered for `repo` (e.g. `"npm:@scope/name"`). The graph has no per-node timestamp, so `freshness` is always `unknown` here.
Returns: `{"repo": ..., "symbolOrPackage": ..., "dependents": [{..., "hops": int}], "freshness": {...}}`.

## Freshness field

Every nav tool returns a `freshness` object describing the published index (`indexed`, `stale`, `published_commit`, etc.). Use it to decide whether to trust results or `codeintel reindex <slug>` first.
```

- [ ] **Step 3: Validate both files**

```bash
main=.claude/skills/codeintel-use/SKILL.md
ref=.claude/skills/codeintel-use/references/tool-roster.md
for f in "$main" "$ref"; do
  # main has frontmatter: count lines after the closing ---. ref has no frontmatter: count all lines.
  if [ "$f" = "$main" ]; then
    lines=$(tail -n +4 "$f" | awk 'c>=2{print} /^---$/{c++}' | wc -l | awk '{print $1}')
  else
    lines=$(wc -l < "$ref" | awk '{print $1}')
  fi
  echo "$f: $lines lines (must be <300)"
done
desc=$(awk '/^description:/{sub(/^description: /,""); print; exit}' "$main")
echo "SKILL.md description: ${#desc} chars (must be ≤200)"
```

Expected: both files <300 lines; SKILL.md description ≤200 chars.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/codeintel-use/SKILL.md .claude/skills/codeintel-use/references/tool-roster.md
git commit -m "feat(skills): add codeintel-use skill + tool roster

Decision matrix prefers codeintel over grep for structural queries;
getIndexStatus-first freshness flow; 8-tool detail in references/."
```

---

### Task 4: `codeintel-issues` skill

**Files:**
- Create: `.claude/skills/codeintel-issues/SKILL.md`

**Interfaces:**
- Consumes: `gh` CLI (assumes installed + authed), codeintel's CLI (`codeintel status`), the package version via `importlib.metadata`, the known-limitations list (embedded).
- Produces: a skill that triggers on codeintel-bug/feature intents. Always confirms with the user before `gh issue create`.

- [ ] **Step 1: Write the SKILL.md**

Create `.claude/skills/codeintel-issues/SKILL.md`:

```markdown
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
- Repo + slug, and `uv run codeintel status <slug>` output.
- The tool name + arguments if it was an MCP call (e.g. `findReferences(repo="foo", symbol="bar")`).
- The full error payload — every codeintel tool returns `{"error": "..."}`, copy it verbatim.
- codeintel version: `uv run python -c "import importlib.metadata; print(importlib.metadata.version('codeintel'))"` (codeintel has no `--version` flag; this reads it from package metadata).
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
```

- [ ] **Step 2: Validate**

```bash
f=.claude/skills/codeintel-issues/SKILL.md
desc=$(awk '/^description:/{sub(/^description: /,""); print; exit}' "$f")
echo "description: ${#desc} chars (must be ≤200)"
tail -n +4 "$f" | awk 'c>=2{print} /^---$/{c++}' | wc -l | awk '{print $1" body lines (must be <300)"}'
```

Expected: description ≤200 chars; body <300 lines.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/codeintel-issues/SKILL.md
git commit -m "feat(skills): add codeintel-issues skill

Gather → classify (with known-limitations dedup) → draft → file via gh,
confirming with the user before each submission."
```

---

### Task 5: Link skills, verify the toolkit, finalize

**Files:**
- No new files; runs the helper from Task 1 across the three skills from Tasks 2–4.

**Interfaces:**
- Consumes: `scripts/link_skills.py` (Task 1), all three skill dirs (Tasks 2–4).
- Produces: live symlinks in `~/.zcode/skills/`; a verified, committed toolkit.

- [ ] **Step 1: Run the helper for real**

```bash
uv run python scripts/link_skills.py
```

Expected output: `Linked 3 skill(s) into /Users/<you>/.zcode/skills/:` followed by the three `codeintel-*` paths. (If run before, `All codeintel skills already linked in ...`.)

- [ ] **Step 2: Verify all three resolve through the symlinks**

```bash
for s in codeintel-setup codeintel-use codeintel-issues; do
  test -f "$HOME/.zcode/skills/$s/SKILL.md" && echo "$s OK" || echo "$s MISSING"
done
```

Expected: three `OK` lines.

- [ ] **Step 3: Whole-repo test sweep**

Run: `uv run pytest -q`
Expected: PASS — no regressions; the Task 1 tests still green.

- [ ] **Step 4: Add a short README pointer to the skills**

Append a `## Skills` section to `README.md` (after the MCP tools section) so adopters know the skills exist and how to link them:

```markdown
## Skills

Three agent skills in `.claude/skills/` help onboard and use codeintel:

- `codeintel-setup` — install, register, index, verify.
- `codeintel-use` — prefer codeintel for structural queries (find references, go-to-definition, hierarchy).
- `codeintel-issues` — file codeintel bugs/features via `gh`.

To load them in a ZCode agent, link them once:

```bash
uv run python scripts/link_skills.py
```
```

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs(skills): document the codeintel skill toolkit in README"
```

---

## Self-Review

**1. Spec coverage:**

| Spec section | Covered by |
|---|---|
| Location `.claude/skills/` | Tasks 2–4 create there |
| `link_skills.py` Python + test, no Bash | Task 1 |
| `codeintel-setup` (7 sections) | Task 2 |
| `codeintel-use` decision matrix + freshness flow + gotchas | Task 3 |
| `codeintel-use/references/tool-roster.md` (8 tools) | Task 3 |
| `codeintel-issues` (gather/classify/draft/file, confirm-before-submit, known-limitations) | Task 4 |
| ≤200-char descriptions | Verified inline in Tasks 2–4 (setup 184, use 195, issues 173) |
| <300-line bodies | Validated in Steps 2/3 of Tasks 2–4 |
| No duplication (cross-link pointers only) | Each skill opens with one sibling-pointer line |
| Symlink + verify | Task 5 |
| Eval harness | **Explicitly deferred** (Global Constraints + lightweight trigger-examples in `codeintel-use`) |

**2. Placeholder scan:** No "TBD/TODO/FIXME". Every code step has full content; every command is exact. The one `<you>`/`<title>`/`<body>`/`<slug>` tokens are user-supplied runtime values, not plan placeholders (each is explained at point of use).

**3. Type consistency:** `link_skills(repo_root: Path, target_dir: Path) -> list[str]` is defined in Task 1 Step 3 and called consistently. Skill names `codeintel-setup` / `codeintel-use` / `codeintel-issues` are identical across all tasks and match the `SKILL_PREFIX = "codeintel-"` filter in Task 1. CLI flags `--slug` / `--scheme` match `src/codeintel/index_cli.py:486-488`. Tool names match `src/codeintel/server.py` decorators.

No gaps, no placeholders, consistent names. Plan is complete.
