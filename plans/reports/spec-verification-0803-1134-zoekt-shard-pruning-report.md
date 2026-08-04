# Spec Verification — Zoekt Shard Pruning on Publish

**Spec:** `docs/superpowers/specs/2026-08-03-zoekt-shard-pruning-design.md` (Status: Approved)
**Verdict:** **REJECT — do not implement.** Core premise is factually wrong; the proposed
mechanism destroys live index data.
**Date:** 2026-08-03

---

## Verdict summary

| Spec claim | Reality | Evidence |
|---|---|---|
| `<NNNNN>` is a version counter | **Shard ordinal** | one run emitted 00000 + 00001 |
| Old shard versions accumulate | **They don't** — reindex overwrites | 2 runs, same filename |
| Publish path never cleans up | **zoekt-index cleans up itself** | logs `removing old shard file` |
| `sorted()[:-keep]` keeps latest | **Deletes most of a large repo** | 105 of 160 files in shard 00000 |
| `.tmp` orphans stranded by kills | **True** | only valid part of the spec |

---

## Fatal finding 1 — `NNNNN` is a shard ordinal, not a version

`<slug>_v16.<NNNNN>.zoekt`: `_v16` is the index *format* version — the only version in the
name. `NNNNN` is the **shard ordinal**, assigned when a repo's corpus exceeds
`zoekt-index -shard_limit` (default `104857600` = 100 MiB, help text: *"maximum corpus size
for a shard"*).

Proof — one single `zoekt-index` invocation over a 154 MB corpus of 160 files:

```
finished shard idx2/bigslug_v16.00001.zoekt: 185748039 index bytes,  55 files processed
finished shard idx2/bigslug_v16.00000.zoekt: 354425876 index bytes, 105 files processed
```

Both shards, one run, one repo. `_prune_old_zoekt_shards` as specified would delete
`00000` — **105 of 160 files, 354 MB of the index** — and keep the 55-file remainder.

## Fatal finding 2 — there is no accumulation to fix

Reindexing the same repo overwrites the same shard names in place:

```
run 1:  testslug_v16.00000.zoekt  1566 bytes
run 2:  testslug_v16.00000.zoekt  1788 bytes   (same name, updated)
```

And when a repo shrinks so it needs fewer shards, `zoekt-index` deletes the surplus itself:

```
finished shard idx2/bigslug_v16.00000.zoekt: 20 files processed
removing old shard file: idx2/bigslug_v16.00001.zoekt
```

The publish path does not need to prune final `.zoekt` shards. That work is already done.

## Fatal finding 3 — the 11 GB was misdiagnosed

"58 shards for 22 repos" were multi-shard large repos, not stale versions.
`polaris-code-intelligence` did not have "16 versions" — it had **16 shards**, because
`zoekt-index` is handed the raw repo directory and only ignores `.git,.hg,.svn` by default:

| repo | dir size | dominant content |
|---|---|---|
| `polaris-code-intelligence` | 3.4 GB | `.local-checkouts/` 2.4 GB, `.local-filestore/` 567 MB |
| `codeintel` | 1.2 GB | `.venv/` 1.1 GB |

`config.IGNORED_DIRS` exists but is wired only into language detection
(`index_cli.py:170`) and semantic chunking (`chunker.py:187`) — **never passed to
zoekt-index**. That is the actual root cause of the disk bloat.

## Fatal finding 4 — the manual cleanup already corrupted the live index

The spec treats the user's ~8.8 GB manual reclaim as out of scope. It was not a reclaim —
it was data loss, and it is still in effect. Measured against
`polaris-code-intelligence` (only shard `00015` of 16 survives):

- files under `docs/` containing `def ` **on disk: 1307**
- files under `docs/` returning hits from the index: **615**
- of the 1307, files exceeding zoekt's 2 MB `-file_limit`: **0**

~692 files (~53% of that directory) are silently unsearchable. `src/` and `tests/`
survived only by luck of walk order — gitignored junk dirs sort earlier and filled the
low-numbered shards. That is coincidence, not a property to rely on.

## Non-fatal: spec-internal errors

- **The proposed test locks in the bug.** `test_prune_old_zoekt_shards_keeps_latest` seeds
  `00000/00001/00002` *as if* they were versions and asserts only `00002` survives. It
  would pass against the wrong model and cement it.
- **`keep=0` claim is wrong.** Spec says `keep=0` "behaves like `_remove_zoekt_shards`".
  `list[:-0]` is `list[:0]` → empty, so `keep=0` deletes **nothing**, not everything.
- **`.tmp` sweep is the one sound idea.** `zoekt-index` writes `<name>.<n>.tmp` then
  renames; a killed run does strand it. The glob `{slug}_v*.zoekt*.tmp` matches the
  observed shape.

## What is accurate in the spec

Line references and plumbing all check out: `_publish_search_only`'s zoekt-index call at
`index_cli.py:537-541`, `index_repo`'s at `681-685`, `_remove_zoekt_shards` at `787`,
`_cmd_forget` at `807`, roadmap section at `docs/project-roadmap.md:365`. `root` is in
scope at both proposed call sites, and `config.data_dir(root) / ".zoekt"` matches existing
usage. `_publish_atomically` does already delete superseded `index-<sha>.db`
(`index_cli.py:314-317`).

---

## Recommendation

1. **Do not implement `_prune_old_zoekt_shards`.** Mark the spec superseded.
2. **Fix the real cause: pass ignore dirs to `zoekt-index`.** Wire `config.IGNORED_DIRS`
   into both call sites via `-ignore_dirs`. Verified effective — a 39 MB tree with one
   real source file:

   | | index size | files |
   |---|---|---|
   | without `-ignore_dirs` | 127 MB | 41 |
   | with `-ignore_dirs` | 4 KB | 1 |

   This attacks the bloat at source and would have prevented nearly all of the 11 GB.
   Caveat: `-ignore_dirs` is a flat list of directory *names*, matched at any depth.
3. **Keep the `.tmp` sweep** as a small standalone change, independent of any pruning.
4. **Reindex every repo.** The current on-disk index is silently incomplete after the
   manual cleanup — searches return partial results with no error. This is the most
   urgent item.
5. **Roadmap:** leave the GC section as "not planned". With `-ignore_dirs` in place there
   is no bloat problem to garbage-collect.

## Open questions

- Should `-ignore_dirs` reuse `config.IGNORED_DIRS` verbatim, or take a wider
  zoekt-specific list (`.local-checkouts`, `.local-filestore`, `.mypy_cache`,
  `.ruff_cache`, `.worktrees`)? The observed offenders are not all in `IGNORED_DIRS`.
- Should zoekt index gitignored content at all? Feeding it `git ls-files` output instead
  of a directory walk would match `detect_language()`'s already-established
  read-git-not-filesystem policy — a larger change, but the principled one.
