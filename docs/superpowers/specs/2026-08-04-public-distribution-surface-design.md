# Public Distribution Surface for a Private jarvis Repo

**Date:** 2026-08-04
**Status:** Approved (design)
**Supersedes:** the first draft of this file (committed `d5f0d41`), which scoped the problem to
zoekt binaries only. That scope was wrong — see "What the first draft got wrong" below.
**Scope:** new repo `phuongddx/jarvis-dist`; new Actions secret; `.github/workflows/build-zoekt.yml`,
`.github/workflows/sync-public-distribution.yml` (new), `.github/workflows/setup-smoke.yml`,
`setup.sh`, `README.md`, `plugin/skills/jarvis-setup/SKILL.md`,
`plugin/skills/jarvis-issues/SKILL.md`, `CLAUDE.md`

## Problem

`phuongddx/jarvis` is private. GitHub honors repo visibility on every anonymous read path,
so **all four channels the project advertises are dead for a real user.** Verified empirically:

| Channel | Anonymous result |
|---|---|
| `curl -fsSL .../phuongddx/jarvis/main/setup.sh \| sh` (`README.md:17`) | **404** — `raw.githubusercontent.com` honors visibility |
| zoekt binaries from `jarvis` releases (`setup.sh:456`) | **404** — release assets inherit repo visibility |
| `/plugin marketplace add phuongddx/jarvis` (`README.md:30`, `:341`) | **404** on all three paths: raw URL, `git ls-remote` ("Repository not found"), `api.github.com` |
| `gh issue create --repo phuongddx/jarvis` (`jarvis-issues/SKILL.md:68`) | Fails — cannot file issues on an invisible repo |

Only PyPI works. There is no header, token-in-URL, or `curl` flag that changes this: GitHub
provides no per-asset access control. Community guidance is explicit — *"If the repository is
public, then the release assets will also be public; if it is private, then they will also be
private... If what you're trying to do is use GitHub with a custom access control system to
allow only [some users] to download it, that's not possible."*

### This is a recent regression, not a longstanding flaw

`setup-smoke.yml:45` runs `sh setup.sh --only zoekt` on real runners — a genuine anonymous
download. Its history:

- 2026-07-26, 07-31, 08-01, 08-02 — **success**, downloading tag `zoekt-33f1f18af292` (which
  has existed since 07-26).
- 2026-08-04 07:00 (run `30886044715`) — **failure**, `curl: (22) ... error: 404` on that same
  tag.

An anonymous download of a private repo's release asset cannot succeed. So the repo was
public through 08-02 and became private between then and 08-04. Visibility-change history is
not queryable for personal repos, so this is inference, not proof — but it is the only
explanation consistent with the evidence.

**Implication for the decision:** keeping the repo private is a *new* constraint that broke a
previously-working, as-documented install path. This design accepts that constraint and works
around it; it is not repairing an original mistake.

CI already caught this at 07:00 on 08-04. It was missed because attention was on the release
PR's `test.yml` checks, not on `setup-smoke`.

### What the first draft got wrong

- **Scope.** It addressed only the zoekt binaries — step 2 of a process whose step 1
  (downloading `setup.sh` at all) is equally dead, and ignored the plugin and issue channels
  entirely.
- **A wrong file citation.** It said to update a comment at `setup.sh:4-6` reading "there is
  no prebuilt zoekt-index/zoekt-webserver...". That text is at `build-zoekt.yml:5-6`;
  `setup.sh:4-6` is the POSIX-sh usage header. The `setup.sh` comment that does need updating
  is the `install_zoekt` block at **`:437-439`**.
- **A half-right "already public" claim.** It argued source privacy was moot because the PyPI
  sdist ships readable `.py` files. Verified against live PyPI v0.4.0 and it is stronger than
  the draft claimed — the **wheel** ships all 17 modules in full readable form too
  (`index_cli.py` 51,951 bytes, `query.py` 20,897, `chunker.py` 18,894, …), so
  `pip download` exposes everything regardless of artifact type. But the draft was wrong that
  this made publishing `setup.sh` free: `setup.sh` is in neither artifact, so it is genuinely
  private today and publishing it is a real, if small, expansion. Accepted deliberately.

Two first-draft claims that **did** hold and are retained: `JARVIS_REPO` is referenced in
exactly one place (`setup.sh:456`), and no test asserts on the repo portion of the download
URL (the `ZOEKT_COMMIT` drift test at `tests/test_setup_sh.py:571-573` compares only the pin
value).

## Decision

Keep `phuongddx/jarvis` private as the development repo. Create a second, **public** repo —
`phuongddx/jarvis-dist` — as the project's *public distribution surface*: the installer script, the
plugin definition, the zoekt binaries, and the issue tracker. `jarvis` remains the single
source of truth; `jarvis` is a publication target, never edited directly.

Rejected alternatives:
- **Embed a PAT in `setup.sh`.** A credential shipped to every installer is a leaked
  credential the moment anyone reads the script — the opposite of what privacy buys.
- **Host on S3/R2/a CDN.** Same outcome, but new infrastructure, cost, and failure modes, when
  a second GitHub repo is free and needs no new tooling.
- **Make `jarvis` public.** Explicitly ruled out by the operator.

### What `jarvis` holds

```
setup.sh                          #  1 file  — synced copy
.claude-plugin/marketplace.json   #  1 file  — synced copy
plugin/**                         # 13 files, 68 KB — synced copy
+ zoekt release assets            # published by build-zoekt.yml
+ Issues enabled                  # so bug reports have a reachable home
```

Deliberately **not** mirrored: `src/` (already on PyPI), `tests/`, `docs/`, `plans/`,
`.github/`, `CHANGELOG.md`, `pyproject.toml`, git history, branches.

`setup.sh` must be a file on a branch, not a release asset, because `curl | sh` needs a stable
URL. Same for `marketplace.json` + `plugin/**`, which a marketplace consumer reads as real
files. This is why "binaries only" was not achievable.

`plugin/.mcp.json` needs no change: it already installs from PyPI
(`uvx --from jarvis-mcp>=0.2.1`), so the plugin has no runtime dependency on
either repo.

## Design

### 1. Auth: one fine-grained PAT

Scoped to **`Contents: write` on `phuongddx/jarvis-dist` only** — no other repo, no other
permission. That single scope covers both creating releases and committing the mirrored files.
Stored as secret `JARVIS_RELEASE_TOKEN` on `phuongddx/jarvis`. One-time manual setup in
GitHub's UI; neither workflow nor `setup.sh` creates it, only consumes it.

### 2. `.github/workflows/build-zoekt.yml`

Unchanged: trigger (`ZOEKT_COMMIT`/workflow file change, or dispatch), the cross-compile
matrix, checksums, the smoke step.

Changed: the `Publish release` step (currently `:92-106`) targets `jarvis`. It has two branches
— upload-if-tag-exists, create-if-not — and **both** need `--repo`:

```yaml
      - name: Publish release
        env:
          GH_TOKEN: ${{ secrets.JARVIS_RELEASE_TOKEN }}
          ZOEKT_COMMIT: ${{ steps.pin.outputs.commit }}
        run: |
          set -eu
          tag="zoekt-${ZOEKT_COMMIT}"
          if gh release view "$tag" --repo phuongddx/jarvis-dist >/dev/null 2>&1; then
            gh release upload "$tag" dist/*.tar.gz dist/*.sha256 --clobber --repo phuongddx/jarvis-dist
          else
            gh release create "$tag" dist/*.tar.gz dist/*.sha256 \
              --repo phuongddx/jarvis-dist \
              --title "zoekt @ ${ZOEKT_COMMIT}" \
              --notes "zoekt-git-index and zoekt-webserver cross-compiled from sourcegraph/zoekt@${ZOEKT_COMMIT}. Consumed by jarvis's setup.sh."
          fi
```

`GH_TOKEN` changes from `secrets.GITHUB_TOKEN` to `secrets.JARVIS_RELEASE_TOKEN` on this step
only — every earlier step keeps the default token, since none needs cross-repo write.

Also update the header comment at `:5-6` ("setup.sh downloads from *our* releases instead") to
name `jarvis` and say why a second repo exists.

### 3. `.github/workflows/sync-public-distribution.yml` (new)

```yaml
on:
  release:
    types: [published]
  workflow_dispatch:
```

Release-triggered because `plugin/.claude-plugin/plugin.json` carries the version — syncing on
release keeps the published plugin's version matched to the published PyPI package. A
`setup.sh` change that lands on `main` without a release therefore does not reach `jarvis`
until the next release; that is intended, not a gap. Published artifacts should correspond to
published versions.

Steps: check out `jarvis`; clone `jarvis` with the PAT into a temp dir; replace `setup.sh`,
`.claude-plugin/`, and `plugin/` there with this repo's copies; commit and push **only if
something changed** (`git diff --quiet || commit`), so a no-op release produces no empty
commit.

### 4. `.github/workflows/setup-smoke.yml`

Fix `:54-56`, stale since the `zoekt-git-index` rename (the comment on `:54` names the old
binary too):

```yaml
          # zoekt-git-index has no -version flag and `-h` exits non-zero.
          "${bin}/zoekt-git-index" -h >help.txt 2>&1 || true
          grep -q "zoekt-git-index" help.txt
```

Without this the job fails on that line as soon as the 404 is fixed and it gets that far.

This workflow is the permanent regression guard for this whole bug class — it exercises the
real anonymous download path, which is exactly why it caught the incident. Worth stating in
the spec so nobody "simplifies" it later into a mocked download.

### 5. `setup.sh`

Add a constant beside the existing `*_REPO` block (near `:23`):

```sh
# The zoekt binaries live in a separate PUBLIC repo because this one is
# private, and GitHub serves release assets only to viewers of the owning
# repo -- an unauthenticated `curl` against a private repo's release 404s.
ZOEKT_RELEASE_REPO="phuongddx/jarvis-dist"
```

`:456` changes from `${JARVIS_REPO}` to `${ZOEKT_RELEASE_REPO}`. `JARVIS_REPO` itself is
left in place, untouched.

Update two comments: the usage URL at `:5`, and the `install_zoekt` block at `:437-439`
("these come from jarvis's own releases") to name `jarvis`.

### 6. Documentation redirects

| File:line | Change |
|---|---|
| `README.md:17` | curl URL → `.../phuongddx/jarvis-dist/main/setup.sh` |
| `README.md:30`, `:341` | `/plugin marketplace add phuongddx/jarvis-dist` |
| `plugin/skills/jarvis-setup/SKILL.md:22` | curl URL → `jarvis` |
| `plugin/skills/jarvis-issues/SKILL.md:11`, `:68` | issue target → `phuongddx/jarvis-dist` |
| `CLAUDE.md` | explain the two-repo split and why |

## Testing

No changes to `tests/test_setup_sh.py`. Verified: it overrides the download URL wholesale via
`ZOEKT_BASE_URL` (`setup.sh:456`'s `${ZOEKT_BASE_URL:-...}` fallback), serving a local fake
tarball, so it never evaluates either `JARVIS_REPO` or the new `ZOEKT_RELEASE_REPO`. No test
asserts on the repo portion of the URL. The `ZOEKT_COMMIT` drift test compares only the pin.

`setup-smoke` gains real coverage: post-change it downloads from a public repo and passes,
and it is the job that fails loudly if this ever regresses.

## Error handling

- Missing/expired/under-scoped PAT → `gh release create` and the sync push both fail loudly in
  Actions with a clear auth error. No silent degradation, no fallback to publishing into the
  private repo.
- `setup.sh` download failure → the existing `log_error`/`FAILED` summary path already handles
  it, unchanged.

## Migration

Ordered, one-time:

1. Create the fine-grained PAT (`Contents: write` on `jarvis` only) and add
   `JARVIS_RELEASE_TOKEN` to `jarvis`'s Actions secrets.
2. Create `phuongddx/jarvis-dist` — public, Issues enabled, minimal README (what it is; that the
   package lives on PyPI; that source is not hosted here).
3. Merge the code/doc changes above.
4. `gh workflow run build-zoekt.yml --repo phuongddx/jarvis` — populates `jarvis` with the
   `zoekt-33f1f18af292` release under the same tag and asset names.
5. `gh workflow run sync-public-distribution.yml --repo phuongddx/jarvis` — populates
   `jarvis`'s files.
6. Verify **unauthenticated** (e.g. `curl -sI` with no credentials, or a logged-out browser):
   `raw.githubusercontent.com/phuongddx/jarvis-dist/main/setup.sh` returns 200, and the zoekt
   tarball URL returns 200.
7. The old `zoekt-33f1f18af292` release inside private `jarvis` is left in place, untouched
   — dead weight once `setup.sh` stops pointing at it. No deletion, nothing that could strand
   an in-flight install.

## Out of scope

- Moving `build-zoekt.yml` into `jarvis`. It stays in `jarvis`, targeting `jarvis` only for
  the publish step.
- Deleting the old private-repo release (see Migration step 7).
- `scip` / `scip-java` / `scip-swift` installation. Verified unaffected: the first two come
  from the public upstream `scip-code/*` org, and `phuongddx/scip-swift` is already public.
- Mirroring `src/`, tests, docs, or git history into `jarvis`.
- Re-publishing PyPI or the MCP Registry entry. v0.4.0 is already live on both; neither depends
  on GitHub repo visibility.

## What privacy is protecting (resolved)

The visibility change was deliberate: the operator does not want to publish source code.

Verified fact that constrains what "private" can actually deliver: **the live PyPI wheel for
v0.4.0 already contains every module in full readable form.** So the private repo is not
protecting source text — it protects git history and commit messages, issues, branches and
WIP, `docs/`, `plans/`, CI workflow definitions, and `CHANGELOG.md`. There is a real
difference between "extractable from a downloaded tarball" and "browsable, greppable, forkable
on github.com with full history," so this is worth something — it is simply not source
secrecy.

Decision on that basis: **the goal is development-process privacy, and source-in-the-PyPI-
package is an accepted cost.** This design proceeds unchanged. Had the goal been genuine source
secrecy, a second GitHub repo would achieve nothing and the only real lever would be to stop
publishing to PyPI — Python offers no good alternative (`.pyc`-only is trivially decompiled;
Cython/Nuitka means per-platform wheels, large build complexity, and is still reversible).

## Unresolved questions

1. **PAT expiry.** Fine-grained PATs cap at 1 year. Expiry surfaces as the loud auth failure
   already designed for, but it is a scheduled future breakage worth a calendar note.
2. **Does `/plugin marketplace add` require anything beyond `marketplace.json` + `plugin/`?**
   The failure mode against a private repo is confirmed (all three anonymous paths 404), but
   the *success* path against a fresh public repo has not been tested end to end. Migration
   step 6 should include an actual `/plugin marketplace add phuongddx/jarvis-dist` from a clean
   Claude Code profile before the docs are considered correct.
