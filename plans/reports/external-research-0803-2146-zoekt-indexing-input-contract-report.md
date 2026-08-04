# External Research — Zoekt Indexing Input Contract

**Scope:** how sourcegraph/zoekt is *intended* to be fed input, and which option fits codeintel.
**Method:** Exa web search/fetch on upstream source + issues, then every claim re-verified locally
against the pinned build (`33f1f18af292`) — including building `zoekt-git-index` from that exact
commit.
**Note:** `agent_run` (Exa Agent) is not available in this environment; used `web_search_exa` /
`web_fetch_exa` instead. Appropriate here — documentation research, not list-building.

---

## Findings

### Q1 — `zoekt-git-index` exists and excludes gitignored content structurally

Upstream README documents it as *the* tool for "Indexing a local git repo". In
[issue #996](https://github.com/sourcegraph/zoekt/issues/996) a maintainer recommends it
specifically for indexing **multiple local git repos in a loop** — codeintel's exact pattern:

> "I recommend using this tool. You can write a script to sequentially run it on each
> repository. […] zoekt-indexserver is really just a for loop around clone/fetch +
> zoekt-git-index."

It reads the git **tree** (`gitindex.IndexGitRepo`, blobs by SHA), not the filesystem, so
gitignored content is absent by construction — no flag needed.

**Verified locally:** repo with `junkdir/` gitignored → "2 total files" indexed (only the two
tracked files); `junktoken_beta` returns 0 hits.

Extras: honors in-tree `.gitignore` and `.sourcegraph/ignore`
([PR #56](https://github.com/sourcegraph/zoekt/pull/56)); `-incremental` (default **true**) skips
unchanged repos; `-delta` builds.

### Q2 — `-ignore_dirs` REPLACES the default

From source (`cmd/zoekt-index/main.go:65`):
`flag.String("ignore_dirs", ".git,.hg,.svn", …)` — one string, one default. Supplying your own
list drops `.git`/`.hg`/`.svn` unless re-listed. Matches my earlier empirical test (passing only a
custom name indexed 28 files, including `.git/`).

Matching is on `filepath.Base(path)` for directories — **bare names at any depth**, no globs, no
paths (`main.go:50-55`).

### Q3 — Sourcegraph does not solve this with `-ignore_dirs`; it doesn't use `zoekt-index`

Production indexing goes through `zoekt-git-index` / `zoekt-sourcegraph-indexserver` — all
git-based. PR #56's discussion is decisive: the maintainer weighed exactly the two options in
front of us —

> "2. Use a flag like we do for large files … allows global excludes (eg exclude node_modules)"
> vs. "1. … doing *all* indexing with `zoekt-git-index` … Then `zoekt-git-index` can directly
> read the file from the git repo."
> → "I went for alternative 1."

**Upstream evaluated the denylist-flag approach the HTML research doc recommends, and rejected it
in favor of git-based indexing.**

### Q4 — `zoekt-index` is documented as non-git-aware by design

`// Command zoekt-index indexes a directory of files.` — plain `filepath.Walk`, zero gitignore
awareness. README calls it "Indexing a local directory (**not git-specific**)". Using it on a
working tree and being surprised by junk is using it as designed.

### Q5 — Multi-shard is normal; codeintel's index size is diagnostic

`ShardMax` default 100 MB corpus; `Builder.Add` flushes when `b.size > b.opts.ShardMax`. Splitting
is correct behavior. GitLab's Zoekt runbook: index is worst-case ~2.8× source size, **typically
~0.4×**. A healthy source-only index is *smaller* than the source — so 2.2 GB across 22 personal
repos is itself evidence of junk, not scale.

---

## New blocker — `zoekt-git-index` has no `-meta`, and defaults to the ORIGIN URL

Not mentioned in the HTML doc or my earlier report. Repo-name resolution
(`gitindex/index.go:247-288`), in order:

1. `zoekt.name` git config → used verbatim
2. else `origin` remote URL → **URL-escaped path**
3. else directory basename

`searchCode(repo=<slug>)` builds a Zoekt `r:<slug>` filter. Verified locally:

| Setup | Resulting repo Name | `r:myslug` |
|---|---|---|
| no remote | `myrepo` (dir basename) | 0 hits |
| `origin = git@github.com:someone/othername.git` | `github.com%2Fsomeone%2Fothername` | 0 hits |
| `git config zoekt.name myslug` | `myslug` | **1 hit** ✓ |

Every real repo has an origin, so a naive swap to `zoekt-git-index` makes **all 22 repos return
zero hits with no error** — precisely the silent-wrong-answer `_write_zoekt_meta`'s docstring
exists to prevent.

`-shard_prefix_override` is **not** a substitute: it renames the file only. With it set to
`myslug`, the file is `myslug_v16.00000.zoekt` but `r:myslug` → 0 hits and `r:myrepo` → 1.

Workaround that works: `git config zoekt.name <slug>` — but that **writes into the user's
`.git/config`**, a side effect codeintel does not currently have.

## Second blocker — `zoekt-git-index` indexes HEAD only

Verified: after appending `uncommitted_gamma_token` to a tracked file and adding untracked
`src/b.py`, reindex → only committed content searchable.

| probe | hits |
|---|---|
| `sourcetoken_alpha` (committed) | 1 |
| `uncommitted_gamma_token` (unstaged edit) | **0** |
| `newtoken_delta` (untracked) | **0** |

`codeintel watch` exists to debounce-reindex **while actively editing**. Under `zoekt-git-index`
search would only ever reflect the last commit, while SCIP nav still indexes the working tree —
the two engines would disagree. This is a genuine functional regression, and the strongest
argument for keeping a walk-based indexer.

## Third blocker — the binary isn't built

`.github/workflows/build-zoekt.yml:57` builds only `zoekt-index` and `zoekt-webserver`. Adopting
`zoekt-git-index` needs: workflow change → new tarball under tag `zoekt-33f1f18af292` → `setup.sh`
extract/install change.

## Closed off — `git ls-files` piped into `zoekt-index` does NOT work

`indexArg` runs one `index.NewBuilder` + `builder.Finish()` **per PATH argument**
(`cmd/zoekt-index/main.go:130-136`). Verified: 3 files passed with `-meta` produced three builders
all writing `listslug_v16.00000.zoekt`, **each overwriting the last** — final shard held 1 file.
Silent data loss. This option is dead.

A materialized clean tree would work instead, but **must use hardlinks, not symlinks**: the walk
filters on `info.Mode().IsRegular()` (`main.go:57`) and `filepath.Walk` uses `Lstat`, so symlinked
files are silently skipped.

---

## Verified design space

| Option | Sees uncommitted work | Exclusion | Blocker / cost |
|---|---|---|---|
| **A** `zoekt-index` + `-ignore_dirs` | ✅ | manual denylist | must re-list `.git,.hg,.svn`; misses unlisted junk (already proven: `.next`, `vendor`, `.claude`, `out`) |
| **B** `zoekt-git-index` | ❌ HEAD only | automatic | build binary; write `git config zoekt.name`; breaks `watch` |
| **C** `git ls-files` → `zoekt-index` args | ✅ | automatic | **not viable** — shards overwrite |
| **D** `zoekt-index` over hardlink tree of tracked + untracked-not-ignored | ✅ | automatic | extra tree materialization per run; most new code |

Upstream's own preference is B. codeintel's `watch` feature argues for A or D. There is no
free option — this is the decision to make in brainstorming.

## Unresolved questions

- Is `codeintel watch` / uncommitted-work search actually valued, or is HEAD-only acceptable?
  This single answer collapses the design space.
- If B: is writing `zoekt.name` into each repo's `.git/config` acceptable, or a dealbreaker?
- If D: hardlink tree cost on the largest repo (polaris-code-intelligence, ~1 GB tracked) is
  unmeasured.
- Does SCIP nav index the working tree or HEAD? If HEAD, B's regression is smaller than it looks.
