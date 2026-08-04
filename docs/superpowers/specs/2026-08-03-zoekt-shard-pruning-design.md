# Zoekt Shard Pruning on Publish

**Date:** 2026-08-03
**Status:** SUPERSEDED — rejected, do not implement. Its premise is wrong:
`NNNNN` is a shard ordinal, not a version counter, so keeping only the highest
deletes most of a multi-shard repo. See
`plans/reports/spec-verification-0803-1134-zoekt-shard-pruning-report.md` for the
verification, and `2026-08-03-zoekt-indexing-input-contract-design.md` for the
replacement design.
**Scope:** `src/codeintel/index_cli.py`, `tests/test_index_cli.py`, `docs/project-roadmap.md`

## Problem

Every `codeintel index` / `reindex` / `watch` cycle runs `zoekt-index`,
which writes a new versioned shard `<slug>_v16.<NNNNN>.zoekt` and leaves
the previous shard on disk. The publish path never removes old Zoekt
versions, so they accumulate indefinitely.

Observed impact: `.zoekt/` grew to **11 GB / 58 shards** for 22 repos
(`polaris-code-intelligence` alone had 16 versions totaling 3.5 GB). The
SCIP side is unaffected because `_publish_atomically` already deletes old
`index-<sha>.db` files on the pointer flip; the entire `scip/` tree was
74 MB.

The only existing shard-deletion code, `_remove_zoekt_shards`, is wired
exclusively into `_cmd_forget` (full repo drop). The reindex/search-only
publish paths never call it.

Two orphaned `.tmp` shards (~545 MB combined) from killed `zoekt-index`
runs were also present — `zoekt-index` writes to a `.tmp` name and
renames on success, so a killed run strands a `.tmp` that is never
usable and never cleaned up.

## Decisions

1. **Keep only the latest shard** per repo after a successful publish
   (`keep=1`). Maximum disk reclaim; matches the user's choice.
2. **Sweep `.tmp` orphans for the slug** on every successful publish, in
   the same step. A successful reindex is the natural "healthy" moment
   to clean up stranded temp files for that repo.
3. **No `codeintel gc` command** and **no `CODEINTEL_ZOEKT_KEEP_VERSIONS`
   config knob** — YAGNI for a single-user local-first tool. The roadmap
   item is narrowed accordingly (see Documentation).

## Design

### New helper: `_prune_old_zoekt_shards`

Co-located after `_remove_zoekt_shards` in `src/codeintel/index_cli.py`
(same module, same concern, distinct intent documented by name):

```python
def _prune_old_zoekt_shards(slug: str, root: Path | None = None, keep: int = 1) -> list[Path]:
    """Delete superseded Zoekt shards for `slug`, keeping the `keep`
    highest-versioned ones, and sweep in-progress `.tmp` orphans.

    Version identification relies on Zoekt's zero-padded 5-digit suffix
    (`<slug>_v16.<NNNNN>.zoekt`): lexical sort == version sort, so the
    latest shard(s) are the last `keep` entries of `sorted(...)`; everything before them is deleted. `.tmp` orphans
    from a killed `zoekt-index` are always swept regardless of `keep`,
    since the rename to the final name is what publishes them.
    """
    zoekt_dir = config.data_dir(root) / ".zoekt"
    if not zoekt_dir.is_dir():
        return []
    removed: list[Path] = []
    final_shards = sorted(zoekt_dir.glob(f"{slug}_v*.zoekt"))
    for shard in final_shards[:-keep]:
        shard.unlink(missing_ok=True)
        removed.append(shard)
    for tmp in zoekt_dir.glob(f"{slug}_v*.zoekt*.tmp"):
        tmp.unlink(missing_ok=True)
        removed.append(tmp)
    return removed
```

Invariants and edge cases:

- **Slug scoping** uses the same `{slug}_v*.zoekt` glob as
  `_remove_zoekt_shards`, preserving the no-prefix-collision guarantee
  already validated by
  `test_remove_zoekt_shards_does_not_prefix_match_other_slugs`.
- **`keep=1`** (default): when there are ≤ 1 final shards,
  `final_shards[:-1]` is empty, so a first index prunes nothing.
- **`keep=0`** behaves like `_remove_zoekt_shards` for that slug, but
  `forget` continues to call `_remove_zoekt_shards` directly — its name
  documents "drop everything" intent better than a `keep=0` argument
  would.
- **`.tmp` glob** `{slug}_v*.zoekt*.tmp` matches the observed on-disk
  shape (`codeintel_v16.00000.zoekt.1150994447.tmp`).

### Call sites

Both call sites run the prune **strictly after** the successful
`zoekt-index` `_run(...)`, never before — so a failed index run never
deletes the currently-served shard. This mirrors `_publish_atomically`'s
"both stages succeeded, then publish/cleanup" discipline.

1. **`index_repo`** — after the `zoekt-index` `_run(...)` at
   `src/codeintel/index_cli.py:682-685`, before the semantic stage:

   ```python
   _run(
       ["zoekt-index", "-index", str(zoekt_dir), "-meta", str(meta_path), str(repo_path)],
       cwd=repo_path,
       step="zoekt-index",
   )
   _prune_old_zoekt_shards(slug, root)
   ```

2. **`_publish_search_only`** — after its `zoekt-index` `_run(...)` at
   `src/codeintel/index_cli.py:538-541`:

   ```python
   _run(
       ["zoekt-index", "-index", str(zoekt_dir), "-meta", str(meta_path), str(repo_path)],
       cwd=repo_path,
       step="zoekt-index",
   )
   _prune_old_zoekt_shards(slug, root)
   ```

### What stays unchanged

- **`_cmd_forget`** still calls `_remove_zoekt_shards(slug)` — "drop
  everything for this repo" semantics, already tested.
- **`_remove_zoekt_shards`** is not modified. Two helpers coexist:
  `_remove_zoekt_shards` = delete-all (for `forget`);
  `_prune_old_zoekt_shards` = keep-latest (for publish).
- **SCIP publish** (`_publish_atomically`) is untouched — it already
  prunes old `index-<sha>.db` files.

## Testing

Three required + one recommended unit test in `tests/test_index_cli.py`,
mirroring the existing `_remove_zoekt_shards` test pattern. All
unit-level (no `@pytest.mark.integration`, no real `zoekt-index`
binary) — they create fake shard files under `tmp_path / ".zoekt"`:

- **`test_prune_old_zoekt_shards_keeps_latest`** — seed
  `<slug>_v16.00000.zoekt`, `...00001.zoekt`, `...00002.zoekt`; call
  `_prune_old_zoekt_shards("slug", root=tmp_path)`; assert only
  `...00002.zoekt` remains.
- **`test_prune_old_zoekt_shards_sweeps_tmp_orphans`** — seed a real
  shard + `<slug>_v16.00001.zoekt.12345.tmp`; assert the `.tmp` is
  deleted and the real shard survives.
- **`test_prune_old_zoekt_shards_keeps_all_when_fewer_than_keep`** —
  single shard with `keep=1`; assert nothing is deleted (no spurious
  prune on a first index).
- **`test_prune_does_not_touch_other_slugs`** *(recommended)* — seed
  shards for `api` and `api-gateway`; prune `api`; assert
  `api-gateway`'s shards survive. Re-asserts the slug-scoped glob
  invariant for the new helper.

## Documentation

Update `docs/project-roadmap.md` "Garbage Collection for Old Indexes"
section (line 365): it currently claims GC is "Not planned; disk is
cheap" and only mentions `index-<sha>.db` files. Narrow it to reflect
that Zoekt shard pruning is now handled on publish (this change), and
note that SCIP dbs were already pruned by `_publish_atomically`. No
remaining open item is left for default behavior; a future `codeintel gc`
command remains optional and out of scope here.

## Out of Scope

- `codeintel gc` subcommand (deferred — YAGNI).
- `CODEINTEL_ZOEKT_KEEP_VERSIONS` config knob (hardcoded `keep=1`).
- Any change to `_cmd_forget`, `_remove_zoekt_shards`,
  `_publish_atomically`, or the SCIP `current` pointer flip.
- Backfill/cleanup of existing on-disk bloat (the user already reclaimed
  ~8.8 GB manually via a one-off script; this design only prevents
  recurrence).
