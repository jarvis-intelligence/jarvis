# Zoekt Indexing Input Contract

**Date:** 2026-08-03
**Status:** Approved (design)
**Supersedes:** `2026-08-03-zoekt-shard-pruning-design.md` (rejected — see
`plans/reports/spec-verification-0803-1134-zoekt-shard-pruning-report.md`)
**Scope:** `src/codeintel/index_cli.py`, `src/codeintel/registry.py`,
`src/codeintel/server.py`, `setup.sh`, `.github/workflows/build-zoekt.yml`,
`tests/test_index_cli.py`, `tests/test_registry.py`, `tests/test_server.py`,
`CLAUDE.md`, `docs/project-roadmap.md`

## Problem

`zoekt-index` is handed the raw repo directory and only ignores `.git,.hg,.svn`
by default, so it indexes every gitignored path: `.venv/`, `.local-checkouts/`,
`node_modules/`, `.next/`, build caches, sibling checkouts. Consequences:

1. **Bloat.** codeintel's own index: 241 MB / 7353 documents for a repo with
   133 tracked files. `.venv/` is 1.1 GB of its 1.2 GB directory.
2. **Shard splitting.** Junk pushes corpora past `-shard_limit` (100 MiB), so
   9 of 22 repos produced 2–16 shards.
3. **Misdiagnosis and data loss.** Shard ordinals were read as accumulated
   versions and all but the highest deleted. Confirmed loss: `polaris-ui`
   `src/` 350 of 420 files; `polaris-code-intelligence` `docs/` 615 of 1307.
4. **Silence.** Search returns partial results with no error. Nothing in the
   system can say "my index is incomplete."

Full evidence:
`plans/reports/research-verification-0803-1323-index-corruption-deep-research-report.md`
and `plans/reports/external-research-0803-2146-zoekt-indexing-input-contract-report.md`.

## Decisions

1. **HEAD-only search is acceptable.** `searchCode` need not match uncommitted
   edits or untracked files. This is what makes `zoekt-git-index` viable.
2. **Pin the Zoekt repo name in the repo** — `git config zoekt.name <slug>`.
   Chosen over storing zoekt's derived name in the registry, because the
   derived name comes from the origin remote URL: change a remote and the
   registry silently goes stale, reproducing the exact failure class being
   removed.
3. **Coverage is recorded and surfaced**, not merely warned about. An
   index-time warning would not have caught this incident, because the shards
   were deleted *after* a successful index.
4. **One slug per repo path, enforced.** `zoekt.name` is per-repo, so multiple
   slugs mapping to one path cannot each get a name — last index wins and the
   others silently return zero hits.

## Design

### 1. Input contract: `zoekt-git-index` replaces `zoekt-index`

Both call sites — `_publish_search_only` (`index_cli.py:537-541`) and
`index_repo` (`index_cli.py:681-685`) — become:

```python
_run(
    ["zoekt-git-index", "-index", str(zoekt_dir),
     "-incremental=false", "-submodules=false", str(repo_path)],
    cwd=repo_path,
    step="zoekt-git-index",
)
```

`zoekt-git-index` walks the git tree and reads blobs by SHA, so gitignored
content is absent by construction — no exclusion list, at all. This is
upstream's recommended tool for local git repos
([README](https://github.com/sourcegraph/zoekt),
[issue #996](https://github.com/sourcegraph/zoekt/issues/996)), and Sourcegraph
itself rejected the global-exclude-flag approach in favour of it
([PR #56](https://github.com/sourcegraph/zoekt/pull/56)).

**`-incremental=false`** is deliberate. The default (`true`) skips indexing when
the shard is newer than refs, which would refuse to repair an *incomplete*
shard — exactly the current state. codeintel's registry owns the when-to-reindex
decision; zoekt must not second-guess it.

**`-submodules=false`** is deliberate. `epost-ios-theme-ui` is a submodule of
`epost-ios-theme-showcase` and is separately indexed under its own slug. With
`true`, its files land in both indexes and the coverage count can never be
exact. codeintel already models submodules as separate repos (language
detection ignores them), so `false` is the consistent choice.

### 2. Naming: `_write_zoekt_meta` is replaced by a pinned git config

`zoekt-git-index` has no `-meta` flag, so `_write_zoekt_meta()` is deleted. Its
job moves to a helper called once before indexing, in both publish paths:

```python
def _pin_zoekt_repo_name(repo_path: Path, slug: str) -> None:
    """`git config zoekt.name <slug>` — pins the Zoekt repository name so
    `searchCode`'s `r:<slug>` filter matches.

    Without this, zoekt-git-index derives the name from the `origin` remote
    URL, url-escaped (e.g. `github.com%2Fowner%2Frepo`), falling back to the
    directory basename when there is no remote. Every real repo has a remote,
    so `r:<slug>` would match nothing and searchCode would return zero hits
    with no error -- a silent wrong answer.

    `-shard_prefix_override` is NOT a substitute: it renames the shard file
    only, leaving the indexed repository name untouched.
    """
    _run(["git", "-C", str(repo_path), "config", "zoekt.name", slug],
         cwd=repo_path, step="git config zoekt.name")
```

Idempotent — re-running overwrites the same key.

`_cmd_forget` gains the symmetric teardown, tolerating git's exit code 5
("key not found") so forgetting a repo indexed before this change still
succeeds:

```python
subprocess.run(["git", "-C", path, "config", "--unset", "zoekt.name"],
               capture_output=True, check=False)
```

**Shard filenames are unchanged.** zoekt's `shardName` applies
`url.QueryEscape(Name)`, and `config.repo_slug()` already restricts slugs to
`[a-z0-9._-]`, none of which escape. Files remain exactly
`<slug>_v16.NNNNN.zoekt`, so `_remove_zoekt_shards`'s glob keeps working
untouched.

### 3. One slug per repo path

New registry method, mirroring the existing `get`:

```python
def find_by_path(self, path: str) -> RegisteredRepo | None:
```

`index_repo` checks it immediately after `repo_path.resolve()` and slug
resolution — before `_pin_zoekt_repo_name`, before the `"indexing"` upsert, so
a rejected index mutates nothing. If a row exists with the same resolved path
and a *different* slug:

```
error: /path/to/repo is already indexed as 'epost-ios-theme-showcase'.
       One slug per repo -- run `codeintel forget epost-ios-theme-showcase`
       first, or reindex that slug instead.
```

Same slug at the same path is the normal `reindex`/`watch` case and passes.
Comparison is on resolved paths, since rows written before this change may hold
unresolved ones. No `--force` escape hatch.

The check is not retroactive; existing duplicates are cleaned during recovery.

### 4. Coverage: recorded at index, verified at status

**At index time.** Expected count = tracked non-gitlink blobs:

```python
def _tracked_file_count(repo_path: Path) -> int:
    """Count of git-tracked blobs at HEAD -- the number of files
    zoekt-git-index should index. `git ls-files -s` emits
    `<mode> <sha> <stage>\\t<path>`; mode 160000 is a submodule gitlink, not a
    file, and is excluded because `-submodules=false` means zoekt never
    descends into it."""
```

Stored in a new `repos.tracked_files INTEGER` column on every successful
publish. The indexer's own `attempting to index N total files` line is parsed
and compared; a shortfall prints a stderr warning. A parse failure (upstream
log format change) skips the warning silently — it must never fail a publish.

**At status time.** `getIndexStatus` compares the stored `tracked_files`
against live `Documents` from zoekt's `/api/list` for that repo, adding one
field to its existing response:

```json
"searchCoverage": {"expected": 133, "indexed": 133, "complete": true}
```

This is the half that detects post-hoc shard deletion. Under it,
`polaris-code-intelligence` would have reported `complete: false` instead of
answering searches as though nothing were wrong.

If `zoekt-webserver` is not already running, `getIndexStatus` does **not**
spawn one, reusing `ZoektLifecycle`'s existing health check. Because
`searchCoverage` is then `null` and cannot carry a nested reason, the
explanation goes in a sibling field:

```json
"searchCoverage": null,
"searchCoverageReason": "zoekt-webserver not running"
```

Status stays cheap, and the server is up whenever searches are actually
happening. `searchCoverageReason` is omitted entirely when `searchCoverage` is
populated.

`complete` is `indexed >= expected`. Greater-than is legitimate (multi-branch
content) and not flagged.

### 5. Registry migration

`registry.py` carries five near-identical `_ensure_<name>_column()` functions,
each with the same 16-line docstring. Rather than add a sixth copy, introduce
one helper and route all six columns through it:

```python
def _ensure_column(conn: sqlite3.Connection, name: str, decl: str) -> None:
    """Idempotent `ALTER TABLE repos ADD COLUMN` for databases created before
    `name` existed. A "duplicate column name" OperationalError means a previous
    run (or a fresh `_SCHEMA` create) already added it -- ignored. Any other
    OperationalError (e.g. "database is locked" from a concurrent
    `codeintel watch` reindex) is re-raised rather than swallowed: a lock
    timeout during migration would otherwise look identical to "already
    exists" while actually leaving the column missing."""
```

The re-raise contract those five docstrings describe is preserved exactly and
covered by a test.

## Rollout

`.github/workflows/build-zoekt.yml:57` builds `zoekt-git-index` **instead of**
`zoekt-index` — nothing calls the latter once this lands. `setup.sh` extracts
the new pair (`zoekt-git-index`, `zoekt-webserver`); the tarball is republished
under the existing `zoekt-33f1f18af292` tag with `--clobber`.

If `zoekt-git-index` is absent, indexing raises an `IndexingError` naming
`setup.sh`, mirroring the `_BASH_SHIM_REMEDY` pattern. There is deliberately no
fallback to `zoekt-index`: a silent fallback would quietly reintroduce junk
indexing.

Existing installs must re-run `setup.sh`, so this requires a version bump and
release via the `codeintel-release` skill.

## Recovery

One-time, ordered:

1. Merge and release the code change.
2. Re-run `setup.sh` to install `zoekt-git-index`.
3. `codeintel forget` the four duplicate slugs, keeping the one whose name
   matches its directory:

   | Keep | Forget |
   |---|---|
   | `epost-ios-theme-showcase` | `epost-comp-showcase-sdk`, `post-shell-app` |
   | `epost-ios-theme-ui` | `ios-theme-ui` |
   | `luz_epost_ios` | `luz-epost-ios` |

4. Delete `~/.codeintel/.zoekt/` entirely. Every shard is junk-polluted,
   including the 13 single-shard ones, so a clean slate is simpler and strictly
   correct.
5. Reindex the 18 remaining slugs that currently hold shards (22 with shards,
   minus the 4 forgotten above).
6. Verify: one shard per repo for most, total `.zoekt` size far below 2.2 GB,
   and `getIndexStatus` reporting `complete: true`.

Deleted shard content is unrecoverable — it was `unlink()`ed. Reindexing from
source is the only path to a complete index.

**Out of scope:** the four `failed` and three stuck `indexing` registry rows.
Those fail for unrelated indexer reasons (Kotlin ABI mismatch, Maven bash
shim). Reindexing surfaces them again; that is not this change's problem.

## Error handling

| Condition | Behavior |
|---|---|
| `zoekt-git-index` missing | `IndexingError` naming `setup.sh`. No fallback. |
| `git config zoekt.name` fails | **Hard fail, do not publish.** An unpinned name means search silently returns nothing. |
| Slug/path collision | Error before any mutation, naming the conflicting slug. |
| Coverage shortfall | Warn on stderr, publish anyway — matches the `PARTIAL_STATUS` precedent. Legitimate causes exist (`file_limit` 2 MB, `max_trigram_count`, binary skips). |
| Log line unparseable | Skip the warning. Never fails a publish. |
| `/api/list` unreachable | `searchCoverage: null` with a reason. Not an error. |

`_publish_atomically`, `_retire_scip_artifacts`, the search-only publish, and
the SCIP `current` pointer flip are all unchanged apart from the indexer swap.

## Testing

Unit (mocked subprocess/HTTP, per existing conventions):

- `_pin_zoekt_repo_name` issues `git -C <path> config zoekt.name <slug>`;
  a non-zero exit raises rather than publishing.
- `_cmd_forget` unsets `zoekt.name` and tolerates exit code 5.
- Collision check raises naming the conflicting slug; same-slug reindex passes;
  comparison survives unresolved paths in existing rows.
- `find_by_path` returns `None` for an unknown path.
- `_tracked_file_count` excludes mode-`160000` lines from `git ls-files -s`.
- Coverage warning fires on shortfall; an unparseable log line degrades
  silently without failing.
- `_ensure_column` is idempotent on "duplicate column name" and re-raises any
  other `OperationalError`.
- `getIndexStatus` returns `searchCoverage: null` when the webserver is down,
  and a populated object when it is up.

Integration (`@pytest.mark.integration`, real binaries):

- `zoekt-git-index` on a fixture repo containing a gitignored directory: the
  junk is absent from results, `Documents == _tracked_file_count()`, and
  `r:<slug>` matches after `_pin_zoekt_repo_name`.
- **Incident regression:** index a fixture, delete one shard, assert
  `getIndexStatus` reports `complete: false`. This is the test that would have
  caught the original incident.

## Documentation

- **CLAUDE.md** — the index-pipeline paragraph names `zoekt-index`; update to
  `zoekt-git-index` and note that the read-git-not-filesystem rule established
  by `detect_language()` now covers search too. Add the HEAD-only caveat: nav
  reflects the working tree, search reflects HEAD.
- **`docs/project-roadmap.md`** — GC section stays "not planned", with a note
  that shards are source-only now so there is no bloat to collect.
- **`2026-08-03-zoekt-shard-pruning-design.md`** — mark superseded by this
  document.

## Also folded in

The `.tmp` orphan sweep from the superseded spec — the one sound part of it.
`zoekt-git-index` writes `<name>.<n>.tmp` then renames, so a killed run strands
the temp file (545 MB of them observed once). Two lines after a successful
index:

```python
for tmp in zoekt_dir.glob(f"{slug}_v*.zoekt*.tmp"):
    tmp.unlink(missing_ok=True)
```

## Out of scope

- Consolidating `config.IGNORED_DIRS` and `watch._IGNORED_PATH_PARTS`. This
  change removes the *need* for a Zoekt ignore list rather than adding a fourth
  copy, but the existing two serve language detection, semantic chunking, and
  the file watcher, and are untouched here.
- `codeintel gc` and any keep-N-versions scheme. There are no versions to keep.
- Repairing `failed`/`indexing` registry rows.
- Searching uncommitted work (explicitly decided against).

## Verification evidence

All measured locally against the pinned build `33f1f18af292`, with
`zoekt-git-index` compiled from that exact commit.

| Claim | Evidence |
|---|---|
| `NNNNN` is a shard ordinal | one run emitted `00000` (105 files) + `00001` (55 files) |
| Reindex overwrites in place | two runs, same filename, 1566 → 1788 bytes |
| zoekt self-prunes surplus shards | logged `removing old shard file` |
| `-ignore_dirs` replaces the default | custom value alone → `.git/` indexed, 28 files vs 1 |
| `zoekt-git-index` excludes gitignored content | gitignored `junkdir/` → 0 hits, 2 tracked files indexed |
| Origin URL becomes the name | `origin git@github.com:someone/othername.git` → `github.com%2Fsomeone%2Fothername`, `r:myslug` 0 hits |
| `zoekt.name` fixes it | `r:myslug` → 1 hit, `Repository: myslug` |
| `-shard_prefix_override` is not a substitute | file renamed, `r:myslug` 0 hits, `r:myrepo` 1 hit |
| HEAD-only | unstaged edit and untracked file → 0 hits |
| `git ls-files` as args is non-viable | 3 files → 3 builders, all wrote shard `00000`, last won |
| Coverage math is exact | codeintel: 133 tracked → "attempting to index 133" → 133 processed |
| Size win | codeintel index 241 MB / 7353 docs → **6.4 MB / 133 docs**, 1 shard |

## Unresolved questions

None blocking. Two to confirm during implementation:

- Whether `getIndexStatus`'s `/api/list` call should be scoped with `r:<slug>`
  (cheaper) or fetch all repos once and cache per invocation. Either is
  correct; pick whichever reads more cleanly against `search.py`'s existing
  client.
- Whether any indexed repo has multi-branch content that makes `indexed >
  expected` common enough to be worth reporting rather than ignoring.
