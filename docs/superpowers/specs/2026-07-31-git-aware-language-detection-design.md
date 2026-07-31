# Git-aware language detection + explicit `--language` override — Design

## Problem

`codeintel index` picked `typescript` for `polaris-code-intelligence`, a Python/FastAPI repo, then
failed because `scip-typescript` found no indexable TS project (no root `package.json` or
`tsconfig.json`).

Root cause, traced against the real repo:

1. `detect_language()` (`index_cli.py:74-91`) walks the **filesystem** via `repo_path.rglob("*")`,
   skipping only the hardcoded `config.IGNORED_DIRS` set
   (`{".git", "node_modules", ".venv", "__pycache__", "dist", "build", "DerivedData", ".build"}`).
2. That repo has a gitignored `.local-checkouts/` directory holding 10 cloned sibling repos
   (`agent-kernel`, `polaris-mcp`, `luz_next`, `vinnstack`, …). It is not in `IGNORED_DIRS`.
3. Measured counts under today's logic: `.ts` 2740, `.tsx` 2042, `.swift` 261, `.py` 201 —
   **100% of the `.ts` and `.tsx` files come from `.local-checkouts/`**. The repo's own code is
   81 tracked `.py` files (`git ls-files "*.ts"` returns 0; `git ls-files "*.py"` returns 81).
4. TypeScript therefore won 4782-to-81 on borrowed evidence, and the indexer failed with nothing
   to index.

The hard failure was luck, not correctness. The language was already wrong before the indexer ran;
the indexer merely had nothing to chew on. **Had a root `tsconfig.json` existed, codeintel would
have published a successful-looking index of ten unrelated repos**, and every `goToDefinition` /
`findReferences` against that slug would have silently answered about the wrong codebase. That is
the same bug in a far more dangerous shape, and it is what this design closes.

Note `.venv` (1419 `.py` files) *was* correctly excluded — the mechanism works. It just depends on
someone having enumerated every possible scratch-directory name in advance. `.worktrees/` is the
same class of hazard (currently empty in that repo; if populated it would double-count the repo's
own files).

### Verified: the fix is correct and surgical

Counting tracked files via git produces the right answer, and changes detection for exactly one
registered repo:

| method | `polaris-code-intelligence` result |
| --- | --- |
| `rglob` (today) | `.ts` 2740, `.tsx` 2042, `.swift` 261, `.py` 201 → **typescript** ❌ |
| `git ls-files` | `.py` 81, `.swift` 9 → **python** ✅ |
| `git ls-files --cached --others --exclude-standard` | `.py` 81, `.swift` 15 → **python** ✅ |

Across all 21 repos in the registry, only `polaris-code-intelligence` changes
(`typescript` → `python`). The other 20 — 9 swift, 4 typescript, 4 python, 3 java — detect
identically under both methods. No migration or forced reindex is required.

Tracked-only (plain `git ls-files`) is chosen over `--cached --others --exclude-standard`: on the
real repo the broader set added 6 leftover `.swift` files in `docs/` and `tests/` that nobody had
gitignored yet — noise, not signal. A handful of uncommitted files cannot flip a repo's plurality,
and the fresh-repo-with-no-commits case is not rescued by it either, since `_git_head()` fails
independently on a repo with no HEAD.

## Scope

In scope: `index_cli.py` (`detect_language`, `_git_head`, `index_repo`, the `index`/`watch`
subparsers), `registry.py` (one new persisted column), and their unit tests.

Out of scope: `config.IGNORED_DIRS` membership (unchanged), the Zoekt/semantic/graph stages, and
multi-language indexing — one language per repo remains the rule.

## Design

### 1. Detection reads git, not the filesystem

`detect_language(repo_path) -> tuple[str, list[str]]` keeps its signature. Only its input set
changes: tracked files from `git -C <repo> ls-files -z` instead of `repo_path.rglob("*")`.

A new helper sits next to the existing `_git_head()`, since both are thin `git -C <path>` wrappers:

```python
def _git_tracked_files(repo_path: Path) -> list[str]:
    """Repo-relative paths of git-tracked files. Git is the source of truth
    for "what belongs to this repo" -- a filesystem walk also counts
    gitignored scratch directories (vendored checkouts, sibling clones,
    worktrees), which can outnumber the repo's own code and flip language
    detection to a language the repo doesn't actually use."""
```

`-z` (NUL-delimited) is required: paths may contain spaces, and git quotes non-ASCII names when
the separator is newline.

The `_IGNORED_DIRS` filter is **retained**, applied as a second pass over git's output. Git alone
does not cover repos that *commit* their build output (a checked-in `dist/` or vendored
`node_modules`), and it is a cheap set-membership test on paths already being iterated.
Tie-breaking by `_EXT_PRIORITY` is unchanged. A git repo with no supported tracked files still
raises `UnsupportedLanguageError`.

`.worktrees/` needs no special handling — worktree directories are not tracked, so they disappear
for free. This is the point of the change: it fixes the whole category, not one directory name.

Keeping the helper in `index_cli.py` rather than a new module follows the existing layout
(`_git_head` already lives there) and the repo's 1:1 test-mirrors-module convention, so its tests
join the existing detection tests in `test_index_cli.py`.

### 2. Non-git directories fail loudly

A new exception:

```python
class NotAGitRepositoryError(Exception):
    """Raised when a repo path is not a git working tree."""
```

Raised by `_git_tracked_files()` when `git ls-files` exits non-zero, naming the path.

This loses nothing in production: `_git_head()` at `index_cli.py:310` already requires a git repo,
one line after detection, so a non-git directory cannot be indexed today either. The change moves
that failure one line earlier with a clearer message, and — more importantly — removes the
filesystem-walk code path entirely rather than leaving it as a fallback where a future refactor
could silently re-enable the bug.

Additionally, `_git_head()` currently runs with `check=True`, so a repo with **no commits** raises
a bare `subprocess.CalledProcessError` — the unhelpful failure hit by `git-workspace-dashboard`.
It is wrapped to raise `IndexingError(f"{repo_path} has no commits yet")` instead.

`server.py` already converts every exception to `{"error": ...}` at the MCP boundary, so no
boundary work is needed.

### 3. `--language` override

An explicit escape hatch for polyglot repos where file plurality is not the language you want
navigation for (e.g. a Python backend with a large committed TypeScript frontend). It follows the
existing `scheme_override` pattern exactly.

`registry.py`:
- Nullable `language_override TEXT` column on `repos`, added to `_SCHEMA` and backfilled by
  `_ensure_language_override_column()` — a verbatim copy of the `_ensure_scheme_override_column()`
  contract (`registry.py:33-44`): swallow `sqlite3.OperationalError` only when the message contains
  `"duplicate column name"`, re-raise otherwise so a lock timeout is never mistaken for
  "already migrated". Called from `Registry.__init__` alongside the existing three.
- `RegisteredRepo` gains `language_override: str | None = None`.
- `Registry.upsert()` gains `language_override: str | None = None`, persisted on every upsert.
- Both `SELECT` column lists (`registry.py:185` and `:194`) and the `_row_to_repo` unpacking
  (`:109`) extend to include it.

`index_cli.py`:
- A reverse map derived from the existing table, so the two cannot drift:
  ```python
  _INDEXER_BY_LANGUAGE = {lang: cmd for lang, cmd in _LANGUAGE_INDEXERS.values()}
  ```
  This yields `{"typescript", "python", "java", "swift"}` — the valid `--language` values.
- `_resolve_language(registry, slug, language)`, mirroring `_resolve_scheme` (`:213-221`):
  an explicit CLI value wins; `None` means "leave the persisted override alone" (so `reindex` and
  `watch` inherit it without repeating the flag) and falls back to the registry row's
  `language_override`.
- `index_repo()` gains `language: str | None = None`.
- `--language` is added to the `index` and `watch` subparsers with
  `choices=sorted(_INDEXER_BY_LANGUAGE)`, so typos fail at argparse rather than at the indexer.
  `reindex` needs no flag — it resolves from the registry.

**Call-order change in `index_repo()`.** `_resolve_language()` needs the `Registry`, which today is
constructed at `:313` — *after* `detect_language()` at `:309`. Detection therefore moves below the
`Registry` construction:

```python
slug = config.repo_slug(slug or repo_path.name)
registry = Registry(config.data_dir(root) / "registry.db")

language_override = _resolve_language(registry, slug, language)
if language_override is not None:
    language, indexer_cmd = language_override, _INDEXER_BY_LANGUAGE[language_override]
else:
    language, indexer_cmd = detect_language(repo_path)

sha = _git_head(repo_path)
check_scip_version()
scheme = _resolve_scheme(registry, slug, scheme)
semantic_include = _resolve_semantic_include(registry, slug, semantic_include)
```

When an override is set, `detect_language()` is not called at all — an override means "do not
guess", not "guess and then correct".

Semantics:
- The registry's existing `language` column keeps holding the **effective** language, so
  `codeintel list` and `codeintel status` show what was actually indexed. `language_override`
  records only that it was forced.
- The Swift path is untouched: `_swift_indexer_cmd()` applies whenever the effective language is
  `swift`, regardless of whether that came from detection or an override.
- No validation that the override matches repo contents. Forcing a language the repo lacks fails
  at the indexer with that indexer's own message, which is the honest failure.

### 4. Testing

All unit-level — no external binaries, so everything stays in the `not integration` lane.

A `git_repo` fixture in `tests/test_index_cli.py`: `git init`, local `user.name`/`user.email`
config (CI has no global git identity), write files, `git add`.

The 7 existing `detect_language` tests are rewritten against real git repos. They currently use
bare `tmp_path` directories with no `git init`, which is precisely why this bug survived: they
exercised a code path production never takes.

New coverage:
- A gitignored directory is excluded — the direct `.local-checkouts` regression.
- An untracked (but not ignored) file is not counted.
- A non-git directory raises `NotAGitRepositoryError`.
- A **committed** `dist/` is still excluded, proving the retained `_IGNORED_DIRS` pass.
- A tracked path containing a space is counted, covering the `-z` requirement.
- `_git_head()` on a repo with no commits raises `IndexingError`, not `CalledProcessError`.

Override coverage:
- An explicit `--language` beats what detection would have chosen, and `detect_language` is not
  called.
- A persisted override is reused by `reindex` with no flag repeated.
- An invalid `--language` value is rejected by argparse.
- `--language swift` on a repo with a checked-in `.xcodeproj` still yields
  `--build-tool xcodebuild`.

`tests/test_registry.py`: `upsert()`/`get()` round-trip `language_override`; a pre-existing DB
created without the column still opens after the guarded `ALTER TABLE`.

### 5. Docs

- `CLAUDE.md`: the **Index pipeline** paragraph's detection sentence — language is decided from
  git-tracked files, not a filesystem walk, so gitignored vendored checkouts cannot skew it; plus
  `--language` in the `Commands` block for `index` and `watch`.
- `README.md`: CLI reference for `index`/`watch` gains `--language`.

## Migration

None. Only `polaris-code-intelligence` changes detection, and it has no live index to invalidate
(its last attempt failed). The other 20 registered repos are unaffected.

## Follow-up (not part of this fix)

After merge, reindex the repo that motivated it:

```bash
codeintel reindex polaris-code-intelligence
```

It should detect `python` with no `--language` flag needed. Requires `scip-python` on `PATH`.

## Open questions

None.
