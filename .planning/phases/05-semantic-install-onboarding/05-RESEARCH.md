# Phase 5: Semantic Install Onboarding - Research

**Researched:** 2026-08-23
**Domain:** CLI interactive onboarding (TTY gating, subprocess package install, registry persistence, import-machinery same-invocation re-import)
**Confidence:** HIGH (code claims verified by direct reads this session; no web access — external-behavior claims verified by local experiment or marked `[ASSUMED]`)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Install mechanics (Area 1 — accepted as proposed)**
- Install command on consent: `uv pip install --python <sys.executable> "jarvis-mcp[semantic]"` — targets the running interpreter's environment regardless of how the venv was created.
- Missing-extra detection: `importlib.util.find_spec` on the same module(s) the semantic stage imports (e.g. `sentence_transformers`/`lancedb`) — the honest "would semantic run" test.
- Install failure (offline, no uv, resolver error): warn + continue — index completes without semantic, one stderr line naming the failed command; the decline is NOT remembered (next TTY index offers again).
- After a successful install: the semantic stage runs in the SAME invocation (fresh import after install) — SC1's "index completes with semantic search enabled".

**Prompt UX & TTY gating (Area 2 — accepted as proposed)**
- TTY gate: `sys.stdin.isatty() and sys.stdout.isatty()` — pip's convention; any redirected stream means automation, never prompt.
- Placement: at the semantic stage, AFTER SCIP/Zoekt publish succeeded — the offer never delays or risks the index itself; prompt names what installs.
- Prompt: `Install semantic search support for this repo? [y/N] ` — Enter = No (safe default); y/Y/yes accepts; everything else declines.
- EOFError/KeyboardInterrupt at the prompt: treated as decline (remembered), no traceback, index completes.

**Memory semantics (Area 3 — accepted as proposed)**
- Storage: additive `semantic_declined` (0/1) column via `_ensure_column` on the existing repos row — migration-safe, mirrors `fallback_enabled`.
- Clearing: `jarvis forget` only (row death); a successful later install makes the bit moot.
- Explicit `--semantic-include` on a declined repo: runs semantic as given — the flag is direct user intent, never blocked, does not clear the decline.
- Extra already installed: no prompt ever; decline bit never written.

### Claude's Discretion
None flagged — all areas resolved with explicit answers.

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SEMA-01 | TTY `jarvis index` with semantic extra missing offers install (y/N), auto-installs on yes, remembers decline per-repo | Offer placement analysis (§Architecture Patterns), find_spec detection set (§Detection), registry additive-column pattern (§Registry Design), same-invocation re-import feasibility (§Re-import), CLI-layer wiring via `offer_semantic` flag (§Prompt Ownership) |
| SEMA-02 | Non-TTY paths (watch, MCP reindex) never prompt — silent skip + stderr hint preserved | Structural call-graph proof (§SEMA-02 Proof), isatty-insufficiency finding (watch IS a TTY), stderr hint pinned by existing test (`tests/test_index_cli.py:1036-1039`) |
</phase_requirements>

## Summary

Phase 5 adds an interactive install offer to exactly one code path — the `jarvis index` CLI command — while leaving the library-layer pipeline (`index_repo`, `_run_semantic_stage`) completely stdin-free. The single most important structural finding: **`index_repo` is called from four places** (`_cmd_index`, `_cmd_reindex` via `_cmd_index` delegation, `_cmd_watch`'s `_reindex` closure, and tests), and **`jarvis watch` is itself a foreground TTY process** — so the locked `isatty` gate alone would NOT protect the watch path from prompting. The offer must be keyed to the `jarvis index` command path structurally (an `offer_semantic` argparse default set only on the `index` subparser, read via `getattr(args, "offer_semantic", False)`), with isatty as defense-in-depth. This yields a four-gate offer predicate: extra-missing → TTY → not-declined → explicit `index` command.

Second key finding: the semantic stage in the full-build path runs at `index_cli.py:1131`, **after zoekt shards are live but BEFORE `_publish_atomically` flips the SCIP pointer (line 1140)**. A consented install (torch-scale download, potentially minutes) at that site would delay the pointer swap and widen the stranded-at-`indexing` window — violating the locked "the offer never delays or risks the index itself". The clean placement is in `_cmd_index` **after `index_repo` returns successfully**: the index is published, the row is terminal, and the offer can re-run `_run_semantic_stage` (fresh import after `importlib.invalidate_caches()`) plus `mark_semantic_indexed` through its own short-lived Registry connection — the exact `_cmd_list`/`_cmd_status` precedent.

Third: same-invocation enablement is verified feasible by local experiment — a failed import leaves no `sys.modules` entry, and after install files appear, `find_spec` + fresh `import` succeed (with `importlib.invalidate_caches()` as the documented belt-and-suspenders against stale FileFinder directory caches). Registry storage mirrors Phase 3's `fallback_enabled` exactly: additive nullable INTEGER column, dedicated `UPDATE` setter, and **zero** `upsert` column-list changes (adding it to the ON CONFLICT list would NULL-reset the decline on every run — the exact trap `tracked_files`' docstring documents at `registry.py:317-323`).

**Primary recommendation:** Implement the offer entirely in the CLI layer — `_cmd_index` gains `getattr(args, "offer_semantic", False)` (True only via `index_parser.set_defaults(offer_semantic=True)`), plus four small test-seam helpers in `index_cli.py` (`_semantic_extra_missing`, `_at_interactive_tty`, `_install_semantic_extra`, and the prompt loop). Registry gains one column, one field, one setter, two SELECT-list appends. Zero changes to `index_repo`, `_run_semantic_stage`, `_publish_search_only`, `watch.py`, or `server.py`.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| TTY gating + prompt (stdin ownership) | CLI command layer (`_cmd_index`) | — | Only `jarvis index` is user-interactive-by-contract; `index_repo` is a library function shared with watch/MCP/reindex and must stay stdin-free |
| Missing-extra detection (`find_spec`) | CLI helper in `index_cli.py` | — | Must mirror the semantic stage's own lazy imports; kept beside `_run_semantic_stage` so the two cannot drift |
| Package install (`uv pip install` subprocess) | CLI helper in `index_cli.py` | — | Fixed argv (no shell), non-fatal by contract; invoked only from the offer path |
| Same-invocation semantic enablement | CLI layer, reusing `_run_semantic_stage` | Registry (`mark_semantic_indexed`) | Stage function is registry-free (verified); the post-return bookkeeping opens its own Registry like `_cmd_list` |
| Decline memory persistence | Registry (`registry.py`) | — | SQLite is the established per-repo memory; `_ensure_column` is the migration mechanism |
| Decline memory death | `jarvis forget` (row DELETE) | — | Already deletes the row — free, zero new wiring (`index_cli.py:1429-1456`) |
| Watch silence | Structural (offer unreachable) | isatty gate | Watch never routes through `_cmd_index`; no code change |
| MCP silence | Structural (no index path exists) | — | `server.py` imports no `index_cli`/`index_repo` at all (verified below) |

## Standard Stack

### Core

No new libraries. Phase 5 is built entirely from the standard library plus code already in the repo:

| Facility | Version | Purpose | Why Standard |
|----------|---------|---------|--------------|
| `sys.stdin.isatty()` / `sys.stdout.isatty()` | stdlib (py3.12+) | TTY gate | Locked decision (pip's convention) [VERIFIED: 05-CONTEXT.md:23] |
| `builtins.input()` | stdlib | y/N prompt; raises `EOFError` at EOF | Locked decision; KeyboardInterrupt propagates from any input() |
| `importlib.util.find_spec` | stdlib | missing-extra detection | Locked decision |
| `importlib.invalidate_caches` | stdlib | post-install fresh import | Documented purpose: modules installed while the program runs [ASSUMED — docs knowledge; behavior verified empirically, see §Re-import] |
| `shutil.which` | stdlib | uv-on-PATH detection | Established pattern: tests and `_REQUIRED_BINARIES` already use `shutil.which` [VERIFIED: tests/test_index_cli.py:26-27] |
| `subprocess.run` | stdlib | uv invocation | House pattern (`_run`, `_unpin_zoekt_repo_name`) — but see Pitfall 3: do NOT reuse `_run` |
| `uv` (external binary, invoked not depended) | 0.11.19 local | package install | Distribution's own bootstrap story; present at `~/.local/bin/uv` on this machine [VERIFIED: local `which uv`] |

### Supporting

| Facility | Purpose | When to Use |
|----------|---------|-------------|
| `Registry._ensure_column` | additive column migration | New `semantic_declined` column [VERIFIED: src/jarvis/registry.py:59-75] |
| `RegisteredRepo` frozen dataclass | row shape | New `semantic_declined` field [VERIFIED: src/jarvis/registry.py:89-112] |
| `tests/conftest.BlockImportFinder` | simulate missing module in tests | Detection-set tests; precedent `tests/test_semantic.py:95-106` [VERIFIED: tests/conftest.py:4-23] |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `input()` prompt | argparse `--semantic` flag / env opt-in | Non-interactive by definition — defeats the discoverability goal (SEMA-01 exists because users don't know the extra exists) |
| per-repo `semantic_declined` column | global config file / env var | Repo-scoped memory is the locked decision; a global "asked once" would suppress the offer for other repos (SC2 requires per-repo) |
| `uv pip install --python sys.executable` | `sys.executable -m pip install` | Locked decision; pip is not guaranteed in a uv-tool venv, and the project's distribution story is uv-first [VERIFIED: stderr hint names uv paths, src/jarvis/embeddings.py:26-30] |

**Installation:** none — no `pyproject.toml` dependency changes. The runtime "install" this phase performs is the project's own published distribution (`jarvis-mcp[semantic]`), user-consented, never in CI/tests.

## Package Legitimacy Audit

> This phase adds **no** third-party dependencies. The only package ever installed at runtime is `jarvis-mcp[semantic]` — the project itself (distribution name locked in `pyproject.toml:15`, `"jarvis-mcp"`, published via PyPI trusted publishing per the comment at `pyproject.toml:2-6`). No registry lookups needed; no `[SLOP]`/`[SUS]` surface exists. Disposition: **Approved (self-distribution, fixed argv, no shell)**.

## Architecture Patterns

### System Architecture Diagram

Offer decision flow (the primary use case: `jarvis index` at a TTY, extra missing):

```
jarvis index <path>                     jarvis watch / MCP server
      │                                        │
      ▼                                        ▼
_cmd_index(args)                        _cmd_watch._reindex ──► index_repo(...)     server.py (9 tools,
      │                                        ▲         ▲         no offer flag      no index path at all)
      ├─► index_repo(...)  ── publish ──┘        │         │
      │        │                                 │         └─ never reaches offer code (structural)
      │        └─ _run_semantic_stage (line 1131: extra missing → skip + stderr hint, unchanged)
      │
      └─ [index_repo returned a slug: index IS published, row is terminal]
             │
             ▼
      offer gate 1: getattr(args, "offer_semantic", False)   ← True only from `index` subparser
             │ False → done (watch/reindex/MCP all land here)
             ▼
      offer gate 2: _semantic_extra_missing()?   find_spec("lancedb") is None
             │                                    or find_spec("sentence_transformers") is None
             │ False → done (extra installed: "no prompt ever")
             ▼
      offer gate 3: _at_interactive_tty()?        sys.stdin.isatty() and sys.stdout.isatty()
             │ False → done (automation: silent, hint already printed by stage)
             ▼
      offer gate 4: registry row semantic_declined?
             │ True → done (remembered decline)
             ▼
      prompt: input("Install semantic search support for this repo? [y/N] ")
             │  y/Y/yes                │ Enter/other          │ EOFError/KeyboardInterrupt
             ▼                         ▼                      ▼
      shutil.which("uv")         set_semantic_declined   set_semantic_declined(slug, True)
             │ absent → warn+continue (decline NOT remembered)
             ▼ present
      subprocess.run([uv, "pip", "install", "--python", sys.executable, "jarvis-mcp[semantic]"])
             │ returncode != 0 → warn+continue (decline NOT remembered)
             ▼ success
      importlib.invalidate_caches()
             ▼
      _run_semantic_stage(repo, slug, ...)   ← fresh import picks up lancedb/sentence_transformers
             ▼ True
      Registry(...).mark_semantic_indexed(slug)   ← SC1 "semantic search enabled", same invocation
```

### Recommended Project Structure

No new files. All changes land in two existing modules plus their 1:1 test files:

```
src/jarvis/
├── index_cli.py    # offer helpers + _cmd_index wiring + build_parser set_defaults
└── registry.py     # semantic_declined column, field, setter, SELECT appends
tests/
├── test_index_cli.py   # offer/install/gate tests (SEMA-01 + SEMA-02 wiring pins)
└── test_registry.py    # migration + tri-state preservation tests
```

### Pattern 1: Offer lives in the CLI layer, keyed by an argparse default

**What:** `index_parser.set_defaults(offer_semantic=True)` in `build_parser` (`index_cli.py:1581` currently reads `index_parser.set_defaults(func=_cmd_index)` — one more line beside it); `_cmd_index` reads `getattr(args, "offer_semantic", False)`.

**When to use:** Always for this phase.

**Why (the four callers of `index_repo`, all verified this session):**
1. `_cmd_index` — direct (`index_cli.py:1272`).
2. `_cmd_reindex` — **delegates by constructing a synthetic `argparse.Namespace` and calling `_cmd_index`** [VERIFIED: src/jarvis/index_cli.py:1365-1369 — `return _cmd_index(argparse.Namespace(path=repo.path, slug=repo.slug, scheme=repo.scheme_override, semantic_include=list(repo.semantic_include), language=repo.language_override))`]. The synthetic namespace lacks the attr → `getattr(..., False)` → reindex never offers. This delegation is a trap if left implicit: any prompt keyed on "am I `_cmd_index`?" without the getattr would fire on reindex too.
3. `_cmd_watch`'s `_reindex` closure — calls `index_repo` directly (`index_cli.py:1497-1500`), never `_cmd_index` → structurally cannot reach the offer.
4. Tests — call `index_repo`/`_cmd_index` with `root=`/tmp dirs.

**Existing precedent for this exact getattr style:** `_cmd_index` already reads every optional via `getattr(args, "search_only", None)` / `getattr(args, "fallback_search_only", None)` (`index_cli.py:1270-1277`) precisely because the reindex synthetic namespace omits them; tests construct partial Namespaces the same way [VERIFIED: tests/test_index_cli.py:1476-1478, 3621-3624].

```python
# Skeleton (values verbatim from locked decisions; seam names are recommendations)
def _semantic_extra_missing() -> bool:
    # The same top-level modules the semantic stage imports lazily:
    # semantic.py:157 `import lancedb`; embeddings.py:102 `from sentence_transformers import SentenceTransformer`
    # tree_sitter_language_pack is deliberately absent — chunker.py:387-390 falls back
    # to fixed-window chunking on ANY failure, so it is not a hard requirement.
    return (importlib.util.find_spec("lancedb") is None
            or importlib.util.find_spec("sentence_transformers") is None)

def _at_interactive_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()

def _install_semantic_extra() -> bool:
    uv = shutil.which("uv")
    if uv is None:
        return False
    result = subprocess.run(
        [uv, "pip", "install", "--python", sys.executable, "jarvis-mcp[semantic]"],
        capture_output=True, text=True,
    )
    return result.returncode == 0
```

### Pattern 2: The offer runs post-return in `_cmd_index`

**What:** After `index_repo` returns the slug and before/after `print(f"indexed {slug}")` (UX choice — after is calmer), `_cmd_index` runs the four-gate predicate and, on consent+success, re-runs the stage and stamps the row:

```python
# In _cmd_index, after index_repo(...) succeeded:
if getattr(args, "offer_semantic", False) and _semantic_extra_missing() and _at_interactive_tty():
    registry = Registry(config.data_dir() / "registry.db")
    try:
        entry = registry.get(slug)
        declined = entry is not None and entry.semantic_declined
    finally:
        registry.close()
    if not declined:
        try:
            answer = input("Install semantic search support for this repo? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""   # locked: treated as decline (remembered), no traceback
        if answer in ("y", "yes"):
            if _install_semantic_extra():
                importlib.invalidate_caches()
                if _run_semantic_stage(Path(args.path), slug, None, include_prefixes):
                    registry = Registry(config.data_dir() / "registry.db")
                    try:
                        registry.mark_semantic_indexed(slug)
                    finally:
                        registry.close()
            else:
                print("warning: semantic extra install failed — index completed without semantic "
                      "(uv pip install --python <python> \"jarvis-mcp[semantic]\")", file=sys.stderr)
                # decline NOT remembered (locked): next TTY index offers again
        else:
            registry = Registry(config.data_dir() / "registry.db")
            try:
                registry.set_semantic_declined(slug, True)
            finally:
                registry.close()
```

**When to use:** This exact shape. **Why not inside `index_repo`:** three terminal return sites would need the offer (full-build success `:1266`, search-only `:1056`, degraded `:1259`), `index_repo` would grow stdin-adjacent code on a function watch calls, and the registry handle is already closed in its `finally` blocks. The CLI layer does all of it with its own short-lived Registry connections — the established `_cmd_list`/`_cmd_status` pattern (`index_cli.py:1287, 1320`).

**Covered runs (consequence to bless):** `_cmd_index` returns 0 for search-only and degraded runs too, so the offer also fires after those — correct and desirable: search-only/degraded repos are precisely the ones where semanticSearch is the available tool (FALL-01 publishes "Zoekt + semantic queryable"). The find_spec gate keeps it once-per-repo.

**Note on the locked "Placement: at the semantic stage, AFTER SCIP/Zoekt publish succeeded":** in code, the full-build semantic stage (`index_cli.py:1131`) runs after zoekt but **before** the pointer swap (`_publish_atomically`, `:1140`). Prompting/installing there would delay the publish — contradicting the second half of the same locked sentence ("the offer never delays or risks the index itself"). The post-return slot is the placement that satisfies the lock's intent: publish has completed, the offer sits at the point where semantic would have mattered, and the existing stage-skip hint (fired at `:1131`) still prints first, so "prompt names what installs" holds — the hint names `jarvis-mcp[semantic]` immediately before the prompt asks.

**Prompt destination:** `input()` writes the prompt to stdout. The "stdout stays just `indexed <slug>` for scripting" invariant (`_print_semantic_report` docstring, `index_cli.py:715-717`) is preserved because scripting = non-TTY = the prompt never fires.

### Pattern 3: Registry — mirror `fallback_enabled`, touch nothing else

**What:** Four additive changes in `registry.py` and zero changes to `upsert`/`record_failure`:

1. `_SCHEMA` fresh-create list gains `semantic_declined INTEGER` (nullable — matches `fallback_enabled`'s `INTEGER` decl at `registry.py:193`).
2. `__init__` gains `_ensure_column(self._conn, "semantic_declined", "INTEGER")` beside the existing `_ensure_column(self._conn, "fallback_enabled", "INTEGER")` [VERIFIED: src/jarvis/registry.py:193].
3. `RegisteredRepo` gains `semantic_declined: bool = False` (NULL reads falsy) after `fallback_enabled: bool | None = None` (`registry.py:112`); `_row_to_repo` unpacks it **last** (`registry.py:119`).
4. `get()` and `list()` SELECT lists append `semantic_declined` after `fallback_enabled` (`registry.py:302, 312`).

Plus one setter, modeled verbatim on `set_fallback_enabled` (`registry.py:329-346`):

```python
def set_semantic_declined(self, slug: str, value: bool) -> None:
    self._conn.execute(
        "UPDATE repos SET semantic_declined = ? WHERE slug = ?",
        (int(value), slug),
    )
    self._conn.commit()
```

**Column shape (answers the key question):** storage is 0/1-as-INTEGER, but only `1` is ever written ("decline bit never written" unless an explicit decline happened — locked). NULL = never answered; both NULL and 0 read as "offer". Model it as plain `bool` on the dataclass (`bool(None)` guard at `_row_to_repo`, or `bool(...)` if not None) rather than a tri-state — unlike `fallback_enabled` there is no precedence chain, so `None` carries no meaning and a tri-state field would be speculative API.

**Upsert column-list answer: NO changes — and adding it would be a bug.** `upsert`'s INSERT column list and ON CONFLICT SET list (`registry.py:212-231`) determine what every run overwrites. `semantic_declined` must stay out of both, exactly like `tracked_files` and `fallback_enabled`, because the transitional `indexing` upserts fire before/without any decline decision and would NULL-reset the memory every run. `record_failure`'s conflict branch (`registry.py:278-283`) also never touches it — a failed run must not forget the decline.

**Does a successful semantic index clear it? NO — and structurally it shouldn't.** Locked: "Clearing: `jarvis forget` only (row death); a successful later install makes the bit moot." The mootness is structural: the offer predicate checks `_semantic_extra_missing()` **before** the decline bit, so once the extra exists the bit is never consulted. A clear-on-success path would be dead code; don't add an unset parameter at all. `jarvis forget` already `DELETE`s the row (`registry.py:348-351`) — memory dies with it, zero new wiring.

**`--semantic-include` on a declined repo (locked: runs semantic, never blocked, does not clear):** falls out for free — nothing in the pipeline reads `semantic_declined` except the offer gate; the stage runs whenever the extra is installed, regardless of the bit. No code needed; pin with a test.

### Anti-Patterns to Avoid

- **Prompting inside `index_repo` or `_run_semantic_stage`:** puts stdin-reading code on the watch/MCP/reindex shared path; isatty alone cannot save you (watch is a TTY).
- **Adding `semantic_declined` to `upsert`'s column lists:** NULL-resets the decline on every transitional write (the `tracked_files` docstring trap, `registry.py:317-323`).
- **Reusing `_run()` for the uv subprocess:** `_run` raises `MissingBinaryError`/`IndexingError` on failure (`index_cli.py:444-452`) — the install must be warn+continue, and `shutil.which` detection must precede any exec.
- **Installing on decline-instead-of-remember:** install failure ≠ decline (locked). Three distinct outcomes: yes+success (install, no bit), yes+failure (warn, no bit), no/EOF/Ctrl-C (bit).
- **Catching KeyboardInterrupt broadly around the whole offer:** the lock covers EOF/Ctrl-C *at the prompt* only. A Ctrl-C during the (long) install should propagate honestly; catching it widely would also swallow real aborts and mask bugs.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| "Would semantic run" test | Import-and-catch probe, version sniffing, dist-metadata lookup | `importlib.util.find_spec` on `lancedb` + `sentence_transformers` | Locked; find_spec has no import side effects (loading sentence-transformers eagerly would cost seconds/memory); verified: find_spec returns `None` cleanly for absent top-level modules, and a prior failed import leaves no `sys.modules` poison [VERIFIED: local experiment, Python 3.14.3] |
| Post-install module visibility | Manipulating `sys.path`, re-exec, hand-rolled finder reset | `importlib.invalidate_caches()` + normal `import` | Documented stdlib mechanism; verified locally (see §Re-import) |
| Per-repo memory | Dotfile/state file in the repo, global cache | Existing `repos` row + `_ensure_column` | Registry is the per-repo memory; forget already sweeps it |
| Decline write on every run | Conflict-list plumbing in `upsert` | Dedicated `UPDATE` setter (`set_fallback_enabled` precedent) | The upsert path would reset the bit (see Pattern 3) |

**Key insight:** every mechanism this phase needs already exists in the codebase in reviewed, tested form — the work is wiring, not construction.

## Common Pitfalls

### Pitfall 1: isatty is NOT the watch gate
**What goes wrong:** gating the prompt on `sys.stdin.isatty() and sys.stdout.isatty()` alone prompts from `jarvis watch` — a foreground interactive command whose first reindex fires immediately at startup, blocking the watch loop on stdin.
**Why it happens:** watch is a TTY; the locked gate answers "is this interactive?", not "is this `jarvis index`?".
**How to avoid:** the structural gate (`offer_semantic` argparse default, only on the `index` subparser) is the real SEMA-02 protection; isatty is defense-in-depth. Order the predicate cheapest/most-selective first.
**Warning signs:** any test that drives `index_repo` directly and sees a prompt.

### Pitfall 2: `_cmd_reindex` silently inherits `_cmd_index` behavior
**What goes wrong:** a prompt implemented as "always offer in `_cmd_index`" fires from `jarvis reindex <slug>` too (it calls `_cmd_index` with a synthetic Namespace, `index_cli.py:1365-1369`).
**Why it happens:** the delegation is invisible at the `_cmd_index` call site.
**How to avoid:** `getattr(args, "offer_semantic", False)` + `set_defaults` on the index subparser only. SEMA-01's literal scope is `jarvis index`; SC2 says "later **indexes**". (If the user later wants reindex to offer too, it's a one-line synthetic-Namespace change — see Open Questions.)
**Warning signs:** a reindex-driven test prompting; `build_parser().parse_args(["reindex", ...])` carrying `offer_semantic`.

### Pitfall 3: the install can take minutes and must never use `_run`
**What goes wrong:** (a) `_run` converts non-zero exits and missing binaries into `IndexingError` — the exact opposite of warn+continue; (b) no timeout means a stalled network hangs the offer forever (the index is already published, so nothing is corrupted, but the command never returns).
**Why it happens:** muscle-memory reuse of the pipeline's subprocess helper.
**How to avoid:** dedicated `_install_semantic_extra()` with `shutil.which("uv")` + `subprocess.run(..., capture_output=True, text=True)` and returncode check. A generous `timeout=` (planner's discretion; subprocess.TimeoutExpired lands in the same failure path if caught) is a reasonable hardening, not a lock requirement.
**Warning signs:** `MissingBinaryError` in offer tests; installs attempted during unit tests.

### Pitfall 4: system Python + uv refuses (expected failure path, don't "fix" it)
**What goes wrong:** when `sys.executable` is a system/EXTERNALLY-MANAGED interpreter, `uv pip install --python <sys.executable>` without `--system` refuses — uv targets virtualenvs by default [VERIFIED: local `uv pip install --help` — `--system  Install packages into the system Python environment`].
**Why it happens:** the locked command targets "the running interpreter's environment"; for a system interpreter that's exactly the case uv guards against.
**How to avoid:** nothing — the non-zero exit lands in the locked warn+continue path with the decline NOT remembered; the next TTY index re-offers. Do not add `--system`/`--break-system-packages` (unlocked scope creep, and modifying a system Python silently is worse).
**Warning signs:** any urge to pass `--break-system-packages`.

### Pitfall 5: CI/dev installs have the extra — detection must be a test seam
**What goes wrong:** offer-path tests never exercise the missing-extra branch because CI installs `--extra semantic` (`test.yml` per AGENTS.md CI section) and dev runs `uv sync --extra semantic` — `find_spec("lancedb")` finds it.
**How to avoid:** detection lives in a module-level helper (`_semantic_extra_missing`) that tests monkeypatch to `True`/`False` — the `_scip_version_output` "Isolated for tests to monkeypatch" precedent (`index_cli.py:486-492`). For testing the helper itself, `BlockImportFinder` (conftest) or a monkeypatched `find_spec`.
**Warning signs:** tests that conditionally skip based on whether lancedb imports.

### Pitfall 6: dev-checkout self-clobber on consent
**What goes wrong:** in a source checkout running `uv run jarvis index`, a consented `uv pip install jarvis-mcp[semantic]` installs the **PyPI release into the dev venv**, replacing the editable jarvis install (and possibly a different version).
**Why it happens:** the locked command resolves latest `jarvis-mcp` from PyPI. The stderr hint's `uv sync --extra semantic` branch exists precisely for checkouts (`embeddings.py:26-30`).
**How to avoid:** accepted by lock (the offer only fires when the extra is missing, which in a synced checkout it never is). Mitigation option if the user wants it: pin the spec to the running version via `importlib.metadata.version("jarvis-mcp")` → `jarvis-mcp[semantic]==<ver>` (falls back to unpinned on `PackageNotFoundError` [VERIFIED: local probe — metadata lookup raises for absent dists]). Do NOT implement without planner/user sign-off (Open Question 2).
**Warning signs:** a dev consenting during everyday work in a checkout.

### Pitfall 7: pytest stdin capture makes real `input()` calls fail
**What goes wrong:** under pytest capture, `sys.stdin` is a `DontReadFromInput` whose reads raise — offer tests that feed real stdin crash confusingly.
**How to avoid:** monkeypatch the prompt seam (or `builtins.input`) with scripted returns/raises — the house style (Phase 3 watch tests scripted `time.sleep`/fake observers rather than running real loops; STATE.md Phase 3 decisions).
**Warning signs:** `OSError: reading from stdin while output is captured`.

## Code Examples

All examples verified against source this session.

### The semantic stage's missing-extra skip (the SEMA-02-preserved behavior)

```python
# Source: src/jarvis/index_cli.py:737-766 (verbatim key lines)
def _run_semantic_stage(repo_path: Path, slug: str, root: Path | None,
                        include_prefixes: tuple[str, ...] = ()) -> bool:
    """Chunk + embed + write the LanceDB table. Optional and non-fatal: ..."""
    try:
        from jarvis import semantic
        from jarvis.embeddings import SemanticExtraMissingError
    except ImportError:
        print(
            "semantic indexing skipped — install jarvis-mcp[semantic] "
            "(uv tool install), or `uv sync --extra semantic` in a source checkout",
            file=sys.stderr,
        )
        return False
    try:
        report = semantic.index_semantic(repo_path, slug, root=root,
                                         include_prefixes=include_prefixes)
        ...
    except SemanticExtraMissingError as exc:
        print(f"semantic indexing skipped — {exc}", file=sys.stderr)   # <- the hint users see
        return False
```

On a healthy base install the hint users actually see comes from the `SemanticExtraMissingError` branch, carrying `_INSTALL_HINT` [VERIFIED: src/jarvis/embeddings.py:26-30]: `"semantic search requires the 'semantic' extra — install jarvis-mcp[semantic] (uv tool install, or uvx --from), or `uv sync --extra semantic` in a source checkout"`. Already pinned by `tests/test_index_cli.py:1036-1039` (`assert "jarvis-mcp[semantic]" in capsys.readouterr().err`).

### The lazy imports that define the detection set

```python
# Source: src/jarvis/semantic.py:156-160 (verbatim)
try:
    import lancedb
except ImportError as exc:
    from jarvis.embeddings import SemanticExtraMissingError, _INSTALL_HINT
    raise SemanticExtraMissingError(_INSTALL_HINT) from exc

# Source: src/jarvis/embeddings.py:101-104 (verbatim)
try:
    from sentence_transformers import SentenceTransformer
except ImportError as exc:
    raise SemanticExtraMissingError(_INSTALL_HINT) from exc

# Source: src/jarvis/chunker.py:386-390 (verbatim) — NOT a hard requirement
try:
    from tree_sitter_language_pack import get_parser
    tree = get_parser(language).parse(source.encode("utf-8"))
except Exception:
    return _apply_headers(_fixed_windows(rel_path, language, file_hash, source), rel_path, language)
```

Detection set = `{lancedb, sentence_transformers}` (extra-missing iff ANY is absent). `tree_sitter_language_pack` is excluded — its failure degrades chunking, never disables semantic. The extra's full contents [VERIFIED: pyproject.toml:68-73]: `lancedb>=0.20`, `sentence-transformers>=3.0`, `tree-sitter>=0.25`, `tree-sitter-language-pack>=0.1`.

### The additive-column precedent to mirror

```python
# Source: src/jarvis/registry.py:190-193 (verbatim)
# Tri-state NULL/0/1 (FALL-02). NULL = never set, defer to the env
# tier; written only via set_fallback_enabled with the explicit
# CLI value, never by upsert (Pitfall 1).
_ensure_column(self._conn, "fallback_enabled", "INTEGER")

# Source: src/jarvis/registry.py:342-346 (verbatim) — the setter shape
self._conn.execute(
    "UPDATE repos SET fallback_enabled = ? WHERE slug = ?",
    (int(value), slug),
)
self._conn.commit()
```

### Full-pipeline test mock (reuse for offer tests)

```python
# Source: tests/test_index_cli.py:3686-3699 (verbatim)
def _mock_healthy_full_run(monkeypatch):
    """Mocks for a fully successful main-pipeline run ..."""
    def _successful_run(cmd, *, cwd, step, env=None):
        if step == "scip expt-convert":
            _make_index_db(Path(cmd[3]), chunks=1, mentions=14)
        return _fake_completed_process(cmd)

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", _successful_run)
    monkeypatch.setattr("jarvis.index_cli.populate_graph_for_repo", lambda *a, **k: None)
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)
```

Offer tests drive `cli._cmd_index(argparse.Namespace(path=..., slug=None, scheme=None, semantic_include=None, language=None, search_only=None, fallback_search_only=None, offer_semantic=True))` under `monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))` — the exact shape of the existing CLI-layer test at `tests/test_index_cli.py:3620-3624`.

## Same-Invocation Re-import (verified feasibility)

Locked: after install, the semantic stage runs in the SAME invocation. Local experiment (Python 3.14.3; repo targets ≥3.12 — same machinery) [VERIFIED: local experiment, 2026-08-23]:

1. `importlib.util.find_spec("never_installed_top_level")` → `None`, no exception.
2. After a failed `import`, `sys.modules` holds **no** entry for the module (CPython removes failed imports) and `find_spec` stays `None` — the detection check is never poisoned by the stage's earlier failed import.
3. Simulated install (module file created on disk mid-process, path already on `sys.path`): `find_spec` located it, `importlib.invalidate_caches()` cleared finder caches, and a fresh `import` succeeded in-process.

Mechanics for the implementation: `jarvis.semantic`/`jarvis.embeddings` themselves import fine on a base install (module-level imports are stdlib + jarvis-internal only — verified `semantic.py:8-22`, `embeddings.py:9-11`), so after `uv pip install` the only missing pieces are `lancedb`/`sentence_transformers`, whose earlier failed imports left no `sys.modules` residue. Sequence: `importlib.invalidate_caches()` → call `_run_semantic_stage(...)` again (its internal `from jarvis import semantic` hits the module cache; `index_semantic`'s `import lancedb` and `_load`'s `from sentence_transformers import ...` execute fresh). One caveat kept honest: `invalidate_caches()` is the belt-and-suspenders — the experiment showed the finder already recovered without it in the common case; it exists because FileFinder directory caches can go stale on same-mtime-granularity writes [ASSUMED — documented purpose from training knowledge; empirically the call is safe and idempotent]. First in-process `sentence_transformers` import also pays one-time torch load cost — expected, same as any cold semantic index.

## SEMA-02 Structural Proof (watch/MCP can never prompt)

1. **Watch:** `_cmd_watch`'s `_reindex` closure calls `index_repo(...)` directly [VERIFIED: src/jarvis/index_cli.py:1497-1500]. The offer code will exist only in `_cmd_index` (and helpers only it calls). No flag is threaded into `index_repo` at all — the signature is untouched. Watch cannot reach `input()` through any path.
2. **MCP:** `server.py` imports neither `index_cli` nor anything that calls `index_repo` — its full import list is `mcp.server.fastmcp`, `jarvis.config`, `jarvis.graph`, `jarvis.index_reader`, `jarvis.query`, `jarvis.registry`, `jarvis.search`, `jarvis.symbols` [VERIFIED: src/jarvis/server.py:8-22]. There is no MCP reindex tool today (nine tools, none index). "MCP-triggered reindex" is therefore a forward-looking phrasing of SEMA-02: the structural property to preserve is that prompt code is reachable only from `_cmd_index`. A future MCP reindex tool would call `index_repo` (not `_cmd_index`), inheriting silence automatically.
3. **The stderr hint stays:** `_run_semantic_stage`'s skip branches are untouched — non-TTY runs keep today's exact one-line hint, and SC3's "silently skipped with the existing stderr hint" is preserved bit-for-bit (existing pin: `tests/test_index_cli.py:1036-1039`).
4. **Non-interactive `jarvis index`** (piped/cron): gates 1–2 (`offer_semantic` passes, extra missing) still reach the isatty gate → False → no prompt, no decline write, hint already printed. This is SEMA-02's "automation" leg.

## State of the Art

Not a fast-moving domain — the relevant "current practice" is pip's own installer UX: prompt on stdout only when both stdin and stdout are TTYs, `Enter` = safe default, non-TTY never blocks. The locked decisions already encode this convention [VERIFIED: 05-CONTEXT.md:23-26]. Python-side, the modern APIs are exactly what's locked: `find_spec` (importlib), `invalidate_caches` (importlib), `shutil.which`. Nothing deprecated involved.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `importlib.invalidate_caches()` semantics (clears finder caches so newly installed modules are visible) | Re-import | Low — verified empirically locally; stdlib-documented behavior |
| A2 | `input()` writes the prompt to stdout and raises `EOFError` at EOF; `KeyboardInterrupt` propagates from any input wait | Patterns 1–2 | Negligible — core language behavior; tests will pin it |
| A3 | uv's default refusal to target non-venv interpreters without `--system` applies to the `--python <path>` form | Pitfall 4 | Low — lands in the locked warn+continue failure path either way; no code depends on the distinction |
| A4 | Case-insensitive accept (`"YES"`/`"Yes"` accepted via `.strip().lower()`) is within the locked "y/Y/yes accepts" | Pattern 2 | Negligible — strictly widens acceptance in the user's favor; pin the parse table in tests |
| A5 | Decline-memory write failure (locked registry.db during the post-offer write) can follow the `_record_failure_best_effort` spirit (warn, never crash the finished index) | Pattern 2 | Low — planner should specify: warn + continue (the index already succeeded; the bit is advisory) |

**If this table is empty:** n/a — A1–A5 above are all LOW-risk; none gate planning.

## Open Questions

1. **Should `jarvis reindex <slug>` also offer?**
   - What we know: SEMA-01/SC2 name `jarvis index` only; `_cmd_reindex` delegates to `_cmd_index`, so this is a one-attribute decision (`offer_semantic=True` in the synthetic Namespace at `index_cli.py:1365-1369` or not).
   - What's unclear: whether "offered once per repo at a TTY" was meant to include reindex-driven TTY runs.
   - Recommendation: **index-only** (literal requirement scope; conservative default). Flag at plan review — one-line change if the user disagrees.
2. **Pin the install spec to the running version (`jarvis-mcp[semantic]==<importlib.metadata.version>`) or install latest (locked command as written)?**
   - What we know: locked command has no version pin; a PyPI release newer than the running install would upgrade jarvis mid-run (harmless to the live process — modules already imported; affects next run).
   - What's unclear: user's tolerance for surprise upgrades vs. spec drift between locked wording and implementation.
   - Recommendation: implement the locked command verbatim; raise the pin as an optional hardening question at plan review. Do not silently deviate from the lock.
3. **Timeout on the uv install subprocess?**
   - What we know: no lock either way; a stalled download hangs the offer indefinitely (index already published, so no corruption).
   - Recommendation: planner's discretion; if added, catch `subprocess.TimeoutExpired` into the same warn+continue failure path and document it as an unlocked hardening choice.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `uv` on PATH | consented install | ✓ (local `~/.local/bin/uv`) | 0.11.19 | `shutil.which` → None → warn+continue (locked failure path) |
| Python ≥3.12 | runtime floor (pyproject `requires-python = ">=3.12,<3.15"`) | ✓ | 3.14.3 (local system), repo `.venv` per `.python-version` | — |
| pytest | validation | ✓ (dev group `pytest>=8.3`) | per uv.lock | — |
| `semantic` extra (lancedb/sentence-transformers) | dev/CI test envs | ✓ (`uv sync --extra semantic`; CI installs it) | per uv.lock | detection seam is monkeypatched in tests — tests must NOT require the extra absent or present (Pitfall 5) |

**Missing dependencies with no fallback:** none.
**Missing dependencies with fallback:** uv absent on an end-user machine → locked warn+continue path (offer still fires once per TTY index until installed or declined — no, correction: install failure does NOT remember the decline, so it re-offers next TTY index; that is the locked behavior, not a gap).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest ≥8.3 (dev dependency-group; config in `pyproject.toml [tool.pytest.ini_options]`, `testpaths = ["tests"]`, marker `integration`) |
| Config file | `pyproject.toml` (no separate pytest.ini/conftest beyond `tests/conftest.py`'s `BlockImportFinder`) |
| Quick run command | `uv run pytest tests/test_index_cli.py tests/test_registry.py -m "not integration" -q` |
| Full suite command | `uv run pytest -m "not integration" -rs` (CI gate); `uv run pytest` for everything incl. integration (skips cleanly without binaries) |

### Phase Requirements → Test Map
All tests are unit-level with monkeypatched seams (no network, no real uv, no real model — house rules per AGENTS.md). Existing files only, mirroring the 1:1 convention.

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SEMA-01 | Prompt fires on TTY + missing extra + `index` cmd; exact prompt text; answer parse table (accept `y`,`Y`,`yes`,`Yes`,`YES`; decline `""`,`n`,`N`,`no`,`anything-else`) | unit | `uv run pytest "tests/test_index_cli.py" -k "offer" -q` | ❌ Wave 0 (new tests in existing file) |
| SEMA-01 | Yes → uv argv exactly `[<uv>, "pip", "install", "--python", sys.executable, "jarvis-mcp[semantic]"]`; success → `invalidate_caches` + `_run_semantic_stage` re-run + `mark_semantic_indexed` (row's `semantic_indexed_at` set) | unit | same `-k "offer"` | ❌ Wave 0 |
| SEMA-01 | uv missing (`shutil.which`→None) or install rc≠0 → one stderr warning naming the command; `semantic_declined` stays unset | unit | same | ❌ Wave 0 |
| SEMA-01 | No/Enter/garbage/EOFError/KeyboardInterrupt → `semantic_declined=1` persisted; exit code 0; no traceback in output | unit | same | ❌ Wave 0 |
| SEMA-01 | Decline remembered: second `_cmd_index` run — `input` never called (monkeypatch to raise AssertionError); a *different* slug still prompts | unit | same | ❌ Wave 0 |
| SEMA-01 | Extra installed (`_semantic_extra_missing`→False): no prompt, no bit; declined row + `--semantic-include` + extra installed: stage runs, bit untouched (locked Area 3) | unit | same | ❌ Wave 0 |
| SEMA-01 | `_cmd_reindex` delegation never offers (synthetic Namespace lacks the attr); `build_parser().parse_args(["index", p])` has `offer_semantic=True` | unit | same | ❌ Wave 0 |
| SEMA-02 | Watch: `_reindex` → recorded `index_repo` call carries no offer path; `input` never invoked even with isatty→True (Pitfall 1 pin; extend the `test_cmd_watch_reindex_forwards_fallback_flag_to_index_repo` pattern at `tests/test_index_cli.py:4106`) | unit | same | ❌ Wave 0 |
| SEMA-02 | Non-TTY `_cmd_index` (isatty→False): no prompt, no bit, stderr hint still printed by the stage (existing pin `tests/test_index_cli.py:1036-1039` stays green untouched) | unit | same | ✅ partially (hint pin exists) |
| SEMA-02 | Structural: server.py cannot reach prompt code — covered by the watch/`_cmd_index`-only wiring tests; no server change exists to test | unit | same | n/a (regression pin via wiring tests) |
| Registry | `semantic_declined` column migrates onto a pre-existing db (NULL default, legacy rows intact) — clone of `test_fallback_enabled_column_migrates_onto_an_existing_database` (`tests/test_registry.py:577`) | unit | `uv run pytest tests/test_registry.py -k "semantic_declined" -q` | ❌ Wave 0 |
| Registry | Bit roundtrip via setter + survives plain `upsert` and `record_failure` (clone of `test_fallback_enabled_tri_state_roundtrip`, `tests/test_registry.py:616`) | unit | same | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_index_cli.py tests/test_registry.py -m "not integration" -q`
- **Per wave merge:** `uv run pytest -m "not integration" -rs`
- **Phase gate:** full unit suite green before `/gsd-verify-work`; live TTY verification (a real `jarvis index` at a terminal with the extra absent) belongs to UAT — cannot be automated honestly in CI (CI installs the extra; forcing absence would require a second wheel-install leg).

### Wave 0 Gaps
- [ ] `tests/test_index_cli.py` — offer/gate/install tests (SEMA-01 rows above); no new file needed
- [ ] `tests/test_registry.py` — migration + preservation tests; no new file needed
- [ ] Framework install: none — pytest already configured (`pyproject.toml [tool.pytest.ini_options]`)

*(Shared fixtures exist: `BlockImportFinder` in `tests/conftest.py`; `_mock_healthy_full_run`/`_fake_completed_process`/`_init_git_repo` in `tests/test_index_cli.py`.)*

## Security Domain

`security_enforcement: true`, ASVS level 1, block-on high [VERIFIED: .planning/config.json:47-49].

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Local single-user CLI; no auth surface |
| V3 Session Management | no | None |
| V4 Access Control | no | Local files, existing permissions unchanged |
| V5 Input Validation | yes | Prompt answer parsed against a fixed accept-set (`{"y","yes"}` lowercased); **never** interpolated into any command — the uv argv is a static list where the only variable is `sys.executable`'s path and the resolved uv path from `shutil.which` |
| V6 Cryptography | no | None |
| V12 File Handling (adjacent) | yes | Registry writes stay parameterized SQL (existing house rule, AGENTS.md: "Always use parameterized queries"); no new file types |

### Known Threat Patterns for this change

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Command injection via prompt answer | Tampering | Answer maps to a boolean branch only; install argv is a fixed list, `shell=False` implicitly (subprocess.run list form) — no string construction |
| Dependency confusion / typosquat on install | Tampering/Spoofing | Spec is the literal `jarvis-mcp[semantic]` — the project's own PyPI trusted-publisher distribution (`pyproject.toml:2-6`); no user-controlled package names |
| Unattended install (prompt spoofing in non-TTY) | Elevation | isatty gate means piped/automation runs can never reach the install; install additionally requires explicit y/Y/yes |
| Registry lock abuse / memory tampering | Tampering | sqlite parameterized writes + `busy_timeout` (existing); decline bit is advisory UX, not a security control |
| Supply-chain: executing arbitrary setup hooks | Tampering | `uv pip install` of a fromager-published wheel — same trust as the user installing jarvis itself; consented, one stderr line records what ran |

## Sources

### Primary (HIGH confidence)
- `src/jarvis/index_cli.py` — read this session: `_run_semantic_stage` (737-766), `_publish_search_only` (811-856), `index_repo` full pipeline (918-1266, semantic call site 1131, publish 1140, terminal returns 1056/1259/1266), `_cmd_index` (1269-1283), `_cmd_reindex` delegation (1351-1369), `_cmd_forget` (1429-1456), `_cmd_watch`/`_reindex` (1459-1540), `build_parser` (1543-1621), `_run` (427-453)
- `src/jarvis/registry.py` — read in full this session: `_SCHEMA` (38-56), `_ensure_column` (59-75), `RegisteredRepo`/`_row_to_repo` (89-137), `upsert` (195-244), `record_failure` (253-289), `set_fallback_enabled` (329-346), `get`/`list` SELECTs (298-315), `forget` (348-351)
- `src/jarvis/semantic.py` (1-188: module imports, `SemanticStore._connect` lancedb lazy import 154-163), `src/jarvis/embeddings.py` (1-157: `_INSTALL_HINT` 26-30, `_load` 99-109), `src/jarvis/chunker.py` (386-390)
- `src/jarvis/server.py` (8-22 import list — no index path)
- `pyproject.toml` (extras 66-73, distribution name 15, pytest config 109-111)
- `tests/conftest.py`, `tests/test_index_cli.py` (mock helpers 3686-3715, CLI-layer tests 3594-3648, hint pin 1024-1039, watch forwarding test 4106+), `tests/test_registry.py` (migration/tri-state templates 577-634)
- `.planning/ROADMAP.md` Phase 5 (123-134), `.planning/REQUIREMENTS.md` (SEMA-01/02, 33-34), `.planning/STATE.md` (phase 1-4 decisions), `05-CONTEXT.md` (all locked decisions)
- Local experiments (2026-08-23, Python 3.14.3): find_spec/failed-import/invalidate_caches/re-import; `uv 0.11.19 --help` flag surface

### Secondary (MEDIUM confidence)
- None — no web access this session; nothing cited from external docs

### Tertiary (LOW confidence)
- Training-knowledge items logged as [ASSUMED] in the Assumptions Log (A1–A4) — each independently low-risk and empirically corroborated locally where testable

## Metadata

**Confidence breakdown:**
- Code integration points (call sites, line numbers, patterns): HIGH — every claim from direct reads this session with verbatim quotes
- Placement recommendation: HIGH — derived from verified call graph + locked decision text; the one interpretive step (post-return vs in-stage) is argued from the lock's own "never delays" clause
- Same-invocation re-import: HIGH — empirically verified locally; stdlib behavior
- External behavior (uv system-python refusal): MEDIUM — local `--help` evidence + ASSUMED semantics; failure path is locked warn+continue regardless
- Test strategy: HIGH — built entirely on verified existing fixtures/mocking patterns

**Research date:** 2026-08-23
**Valid until:** 2026-09-22 (stable internal-codebase domain; re-verify line numbers only if `index_cli.py`/`registry.py` change before planning)
