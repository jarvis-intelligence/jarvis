# codeintel Skill Toolkit — Design

## Purpose

codeintel is a local-first code-intelligence MCP server (SCIP navigation + Zoekt search, 8 MCP
tools). The README documents it well, but an agent working *with* codeintel needs three things the
README doesn't give it: (1) a linear onboarding runbook, (2) an everyday decision matrix that makes
the agent **prefer codeintel's structural tools over grep**, and (3) a feedback loop for filing
codeintel bugs/features. This spec defines three skills that cover that lifecycle:

```
onboard  →  use  →  report
```

Three skills (not one) because the three intents trigger at different times and loading only the
relevant one keeps each invocation lean. Each is self-contained; cross-links are one-line pointers,
not duplicated content.

## Scope

**Build:** three `codeintel-*` skills under `.claude/skills/` in this repo, each a single
`SKILL.md` (<300 lines), plus one Python helper script with a test to symlink them into
`~/.zcode/skills/` so a ZCode agent loads them.

**Standards compliance:** `ck:skill-creator` v4.0.0, **full compliance except the eval harness**.
Eval harness (evals.json + grader agents + benchmark optimization + HTML viewer) is an explicit,
documented scope-cut; lightweight trigger-example lists are the stand-in.

**Out of scope:**
- Eval harness (above).
- Auto-installing `gh` / `gh auth` checks inside `codeintel-issues` — assumes `gh` installed and
  authed.
- Papering over codeintel's own limitations (one-language-per-repo, single-tenant hardcoding) —
  the skills document them honestly, they don't hide them.
- Windows — codeintel itself doesn't support it.

## Architecture

```
codeintel/                                   # repo root = CWD
├── .claude/skills/
│   ├── codeintel-setup/
│   │   └── SKILL.md                         # onboarding runbook
│   ├── codeintel-use/
│   │   ├── SKILL.md                         # everyday decision matrix
│   │   └── references/
│   │       └── tool-roster.md               # 8-tool detail (progressive disclosure)
│   └── codeintel-issues/
│       └── SKILL.md                         # file codeintel bugs via gh
├── scripts/
│   ├── link_skills.py                       # (re)creates ~/.zcode/skills symlinks
│   └── test_link_skills.py                  # test for the above
```

**Why `.claude/skills/`:** `ck:skill-creator` mandates skills live in `.claude/skills/` in the CWD
("NOT `~/.claude/skills/` unless requested"). That satisfies the standard and Claude Code's
auto-discovery. A ZCode agent, however, loads from `~/.zcode/skills/` — so `scripts/link_skills.py`
symlinks each skill dir into `~/.zcode/skills/`. The repo file remains the single source of truth;
the symlink is only a loader hook.

**Progressive disclosure** (the standard's three-level model):
1. **Metadata** (≤200 chars) — always in context; carries trigger phrases.
2. **SKILL.md body** (<300 lines) — loaded when the skill triggers.
3. **`references/`** — loaded as-needed. `codeintel-use` spills the 8-tool detail to
   `references/tool-roster.md` so its main file stays under 300 lines.

### Compliance map

| Requirement (`ck:skill-creator` v4.0.0) | How met |
|---|---|
| Location `.claude/skills/` in CWD | Skills under `.claude/skills/codeintel-*/` |
| `name` + `description` required | Each skill has both; `version` optional added |
| Description ≤200 chars, pushy | Each ≤200 chars with aggressive trigger contexts |
| SKILL.md <300 lines | Enforced; `codeintel-use` splits detail to `references/` |
| No duplication | Single source of truth; cross-links are pointers |
| Scripts: Python not Bash, with tests | `link_skills.py` + `test_link_skills.py` |
| Imperative voice | "To index a repo, run..." throughout |
| **Deferred:** eval harness | Trigger-example lists stand in (documented scope-cut) |

## Component designs

### Skill 1 — `codeintel-setup`

**Intent:** get a new machine/repo from zero to "codeintel indexed and answering queries" in one
linear pass. Used once during adoption; rarely after.

**Frontmatter** (184 chars):
```yaml
---
name: codeintel-setup
description: Install and configure codeintel, the local-first code-intelligence MCP server. Use when onboarding, running setup.sh, registering the MCP server, or indexing a repo for the first time.
version: "0.1.0"
---
```

**Body sections:**
1. **Prerequisites check** — macOS/Linux, `uv` present, `~/.codeintel/bin` on `PATH`.
2. **Install** — `curl -fsSL https://raw.githubusercontent.com/phuongddx/codeintel/main/setup.sh | sh`
   then `uv sync`. One command each, idempotent.
3. **Register MCP** — the exact `claude mcp add codeintel --scope user -- uv --directory
   <path> run codeintel-server` line; one-line note for Cursor/other clients.
4. **Index a repo** — `codeintel index /path [--slug name] [--scheme name]`, with the
   one-language-per-repo detection rule.
5. **Verify** — `codeintel status <slug>` then a smoke `goToDefinition` call; expected output
   shape.
6. **Troubleshooting** — the 4 common failure modes (binary missing, `scip` too old, Swift scheme
   ambiguity, `partial` status) with the fix for each.
7. **Next** — one-line pointer to `codeintel-use`.

**Does NOT cover:** everyday queries (→ `codeintel-use`), issue filing (→ `codeintel-issues`).

### Skill 2 — `codeintel-use`

**Intent:** make the agent **prefer codeintel's MCP tools for structural queries**, check freshness
first, fall back to grep when a repo isn't indexed.

**Frontmatter** (195 chars):
```yaml
---
name: codeintel-use
description: Use codeintel MCP tools for code structure queries: finding references, go-to-definition, call/type hierarchy, who calls a function, where a symbol is defined, document symbols. Prefer over grep.
version: "0.1.0"
---
```

**Core: the decision matrix** (patterned on the `codebase-memory` skill):

| Question | codeintel tool | Fallback |
|---|---|---|
| Where is `X` defined? | `goToDefinition(repo, X)` | grep |
| Who calls / uses `X`? | `findReferences(repo, X)` | grep |
| What does `X` call / callers of `X`? | `callHierarchy(repo, X)` | grep |
| Super/subtypes of `X`? | `typeHierarchy(repo, X)` | (often error — upstream gap) |
| Symbols in a file? | `documentSymbols(repo, path)` | grep |
| Lexical text search? | grep / `searchCode` | — |
| Is this repo indexed? | `getIndexStatus(repo, repo_path)` | — |
| Cross-repo dependents? | `blastRadius(repo, pkg)` | — |

**The prefer-codeintel rule:**
- For any *structural* query (definition, references, callers, hierarchy): call `getIndexStatus`
  first.
  - `indexed: true`, fresh → use the codeintel tool.
  - `indexed: true`, stale → run `codeintel reindex <slug>`, then use the tool.
  - `indexed: false` → fall back to grep; optionally offer to index.
- For *text* search → grep or `searchCode` interchangeably (no preference).

**Gotchas (short):**
- `typeHierarchy` returns an explicit error on real indexes — upstream `scip expt-convert` never
  populates `relationships`, not a codeintel bug.
- `blastRadius` only resolves edges to repos *already indexed* — index the dependency first.
- One language per repo — no multi-language merge.
- Every tool returns `{"error": "..."}` on failure, never raises.

**Progressive disclosure:** the 8-tool detail (full signatures, return shapes, per-tool when-to-use)
moves to `references/tool-roster.md`. SKILL.md carries the matrix + the prefer-codeintel rule + a
grep-pointer (`grep -n "tool-roster"`) to that file.

### Skill 3 — `codeintel-issues`

**Intent:** file well-formed bug reports and feature requests against the codeintel project on
GitHub via `gh`.

**Frontmatter** (173 chars):
```yaml
---
name: codeintel-issues
description: Report bugs and request features for the codeintel MCP server via GitHub issues. Use when codeintel errors, an index fails, a limitation bites, or to request an improvement.
version: "0.1.0"
---
```

**Body sections:**
1. **Gather context first** — exact command run, repo + slug, `codeintel status <slug>` output, tool
   name + arguments, full error payload, codeintel version (`uv run codeintel --version`), OS/arch.
2. **Classify** — bug vs. feature vs. known-limitation. Cross-check against the known-limitations
   list (below) to avoid filing duplicates of documented gaps.
3. **Draft the issue** — title; summary; steps to reproduce; expected vs. actual; environment;
   relevant logs. Bug vs. feature template.
4. **File via `gh`** — `gh issue create --repo phuongddx/codeintel --title "..." --body "..."`.
   Confirm with the user before submitting (outward-facing action).
5. **Known-limitations pointer** — the documented upstream gaps (typeHierarchy empty, single-tenant
   `_`/`_` hardcoding, one-language-per-repo, `blastRadius` unknown-freshness) so the agent doesn't
   file duplicates.

**Does NOT cover:** general GitHub issues for other repos — only the codeintel project itself.

### Shared conventions

- **Cross-link:** each SKILL.md opens with one line — *"Part of the codeintel toolkit. Siblings:
  `codeintel-setup` (onboard), `codeintel-use` (everyday), `codeintel-issues` (report)."* Pointer
  only, no duplicated content (satisfies the no-duplication rule).
- **Imperative voice** throughout.
- **Concrete commands** everywhere — every instruction has the exact command, no placeholders
  except `<repo-path>` / `<slug>`.
- **Lightweight trigger validation** (in lieu of the deferred eval harness): each skill's footer
  lists 3-4 example prompts that *should* trigger it and 1-2 that *should not*.

### `scripts/link_skills.py`

Python (the standard says "avoid Bash"). Idempotent: for each skill in
`.claude/skills/codeintel-*`, create `~/.zcode/skills/<name>` → repo path if absent, skip if
present, print a summary. Has a unit test (`test_link_skills.py`) using `tmp_path` to verify
link creation, idempotency, and skip-existing. Run once after install; documented in
`codeintel-setup`.

## Error handling

- Skills are documentation, not executable logic — the only executable is `link_skills.py`, whose
  failures (missing `.claude/skills/`, unwritable `~/.zcode/skills/`) print a clear message and
  exit non-zero.
- `codeintel-issues` instructs the agent to confirm with the user *before* `gh issue create` —
  filing is an outward-facing action; the skill never auto-submits.
- The skills never instruct the agent to mutate a published `index-<sha>.db` or otherwise violate
  codeintel's read-only-runtime / atomic-publish invariants.

## Testing

- `test_link_skills.py` — unit test for the helper: creates symlinks, idempotent, skips existing.
  Run via `uv run pytest scripts/test_link_skills.py`.
- **Manual verification** of the three SKILL.md files against `ck:skill-creator`'s published
  requirements: ≤200-char descriptions, <300-line bodies, correct frontmatter, no duplicated
  content across siblings, imperative voice. No eval-harness automation (out of scope).
- **Lightweight trigger check:** for each skill, hand-verify the trigger-example prompts against
  the description — should-activate prompts fire, should-not prompts don't.
