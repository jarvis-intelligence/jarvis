# Verification — "Codeintel Index Corruption: Deep Research & Remediation Plan"

**Source:** `~/Downloads/codeintel-index-corruption-deep-research.html` (2026-08-03)
**Verdict:** **Diagnosis correct. Damage numbers wrong. Recommended fix incomplete.**
**Method:** direct `zoekt-webserver` queries (port 6070) + on-disk `grep` counts, per repo.

---

## 1. What the doc gets right

All confirmed against my own instrumented tests and on-disk state:

- Causal chain: over-index → shard split → misdiagnosis → deletion → silent failure. ✓
- `NNNNN` is a shard ordinal, not a version counter. ✓
- Reindex overwrites shard names in place; zoekt deletes its own surplus. ✓
- Root cause: `-ignore_dirs` never passed at either `zoekt-index` call site. ✓
- `config.IGNORED_DIRS` exists, wired only into `detect_language()` and the chunker. ✓
- Repo inventory: 9 multi-shard, 13 single-shard, 22 with shards. Every shard ordinal and
  surviving byte size in the table matches `ls` exactly. ✓
- Rejecting append-only/tombstones/audited-compaction at this scale. ✓ Sound.
- `.tmp` orphans currently on disk: **0**. ✓
- The `-ignore_dirs` impact figures (127 MB → 4 KB) are my measurements, quoted correctly. ✓

## 2. Damage table is shard-count loss mislabeled as data loss

The doc's headline — *"9 of 22 repositories have lost the majority of their searchable
content"*, *"93% loss"* — is an **inference from shard ordinals, never measured**. I measured it.

| Repo | Doc claims | Measured (indexed / on disk) | Real source loss |
|---|---|---|---|
| `vane` | 80% | `src/` 156 / 156 | **0%** |
| `agent_runtime` | 66% | `app/` 51 / 48 | **0%** |
| `epost-ios-theme-showcase` | 66% | 717 / 381 | **0%** |
| `post-shell-app` | 66% | 718 / 381 | **0%** |
| `epost-comp-showcase-sdk` | 66% | 718 / 381 | **0%** |
| `codeintel` | 66% | all probes hit | **0%** |
| `polaris-code-intelligence` | 93% | `src` 38/35, `tests` 35/32 intact; `docs` 615 / 1307 | docs only, ~53% |
| `polaris-ui` | 80% | `src/` 350 / 420 | **~17%** ← only real source loss |
| `ios-theme-ui` | 50% | not isolated | unknown |

**Why:** zoekt walks in directory order, and the junk that caused the split (`node_modules/`,
`.venv/`, `.local-checkouts/`, `.next/`) sorts *before* real source. Junk filled shards
0..N-1; source landed in the surviving final shard. That is luck, not design — but it means
the harm is roughly one order of magnitude smaller than the doc asserts.

Related: the doc's claim that the surviving shard holds *"files from the end of the alphabet
(t/u/v directories)"* is invented detail. It holds the end of the **walk**, which is why
source survived.

**Corrected statement:** one repo (`polaris-ui`) has confirmed source loss at ~17%; one
(`polaris-code-intelligence`) lost ~53% of `docs/`. The rest show no measurable source loss.
Reindexing is still warranted — coverage cannot be trusted — but this is not a 9-repo
catastrophe.

## 3. The recommended `-ignore_dirs` list is demonstrably incomplete

Proposed list = `IGNORED_DIRS ∪ {.local-checkouts, .local-filestore, .mypy_cache,
.ruff_cache, .worktrees, .spm, .build, Pods}`.

Junk **currently in the live index** that this list does **not** exclude:

| Repo | Unexcluded dir | Files in index |
|---|---|---|
| `vane` | `.next/` (760 MB on disk) | — |
| `ios-theme-ui` | `vendor/` | 677 |
| `ios-theme-ui` | `.claude/` | 676 |
| `polaris-ui` | `out/` | 57 |
| `polaris-code-intelligence` | `okuro-ref/` | 4 |

The list was reverse-engineered from one snapshot of one user's machine. It needs perpetual
maintenance and will silently drift. See §5.

## 4. Concrete defects in the proposed code and procedure

**`-ignore_dirs` REPLACES the default, it does not append.** Verified: passing
`-ignore_dirs 'vendorjunk'` on a 1-source-file repo indexed **28 files** — it swallowed
`.git/`. The doc never mentions this. Its snippet is safe only because `IGNORED_DIRS`
happens to contain `.git`; it silently drops `.hg`/`.svn`, and any implementer who trims
the list re-indexes `.git`.

**`codeintel search` does not exist.** The verification recipe in §9 runs
`codeintel search "def " --repo polaris-code-intelligence`. Actual CLI:
`index, list, status, reindex, forget, watch`. Search is MCP-only. The recipe cannot be run
as written — use a direct `POST localhost:6070/api/search`.

**Solution C's code contradicts its own description.** Text says "if a repo that previously
had N shards now has fewer … emit a warning." Code is `if len(shards) > 8`. A `>8` threshold
would have flagged **1 of the 9** damaged repos — the other eight had 2–5 shards. Useless
as written.

**Time and risk estimates are optimistic.** "Reindex 9 repos, ~10 min, Risk: None" — five
are Swift (scip-swift/xcodebuild) or Java (Gradle/Maven); the registry already holds 4
`failed` and 3 stuck `indexing`. Realistically hours, with re-failures. Reindex also runs
`_retire_scip_artifacts`, so a currently-working repo can land in `failed`.

**"One-line fix"** (banner) vs "~8 LOC" (§6) vs "8 LOC" (§10) — inconsistent, and it is not
one line.

**"2.2 GB → ~200–400 MB"** is an unverified prediction stated as expectation.

## 5. Blind spot — denylist vs. reading git

The doc treats `-ignore_dirs` as self-evidently correct and never considers the alternative.
But:

- `-ignore_dirs` is a **denylist of bare directory names**, matched at any depth. It cannot
  exclude large gitignored *files*, and cannot express paths.
- It requires knowing every junk directory in advance — already failed (§3).
- `detect_language()` already established the opposite policy in this codebase: **read git,
  not the filesystem**, precisely because a walk counts gitignored scratch dirs. That
  reasoning applies verbatim to zoekt.

Feeding zoekt `git ls-files` output is self-maintaining, matches existing policy, and needs
no list. Cost: argv limits on large repos, and changed behavior for untracked/submodule
files. This trade-off deserves an explicit decision, not silence.

## 6. Recommendation

Adopt the doc's **diagnosis** and its ordering (fix source → reindex → verify). Reject its
**damage figures** and treat its ignore-list as a starting point, not the answer.

1. Decide the indexing input contract first — `git ls-files` vs `-ignore_dirs`. This is the
   one real design question and the doc skips it.
2. If `-ignore_dirs`: include `.git,.hg,.svn` explicitly (replace semantics), and add
   `.next, vendor, .claude, out` at minimum.
3. Reindex all 22 repos, not just 9 — single-shard repos are also polluted with junk, just
   not truncated.
4. Replace Solution C with a real coverage check (indexed file count vs `git ls-files`
   count), or drop it. The `>8` threshold is noise.
5. Keep the `.tmp` sweep (Solution D). Correct and harmless.

## Unresolved questions

- `ios-theme-ui`: source coverage not isolated — needs its own probe before/after.
- `epost-*` and `post-shell-app` index **more** files than exist on disk (718 vs 381),
  suggesting stale entries from an older commit. Separate issue from shard loss; worth a
  look during reindex.
- Three registry rows stuck in `indexing` and four in `failed`, plus three slugs pointing at
  the same directory. Reindexing will surface these; no plan exists for them.
- The "previous research report" the doc argues against (GitHub/Meta/Google patterns) was
  not provided, so its characterization is unverified.
