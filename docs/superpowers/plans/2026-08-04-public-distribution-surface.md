# Public Distribution Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make jarvis installable by a real user again — every advertised channel (installer script, zoekt binaries, Claude Code plugin, issue tracker) currently 404s because `phuongddx/jarvis` is private.

**Architecture:** `phuongddx/jarvis` stays private as the development repo and single source of truth. A new public repo, `phuongddx/jarvis-dist`, becomes the public distribution surface: it holds a synced copy of `setup.sh`, the plugin definition, the zoekt release assets, and an issue tracker. `jarvis`'s CI publishes into it with a narrowly-scoped PAT. `jarvis` is a publication target, never edited by hand.

**Tech Stack:** GitHub Actions, GitHub CLI (`gh`), POSIX `sh` (`setup.sh` must parse under `dash`), Python 3.12+/uv/pytest for the test suite.

**Spec:** `docs/superpowers/specs/2026-08-04-public-distribution-surface-design.md`

**Base:** `main` at `30ff7b8`, clean working tree.

## Global Constraints

- `setup.sh` is **STRICTLY POSIX sh** — no arrays, no `[[ ]]`, no bashisms. `curl | sh` ignores its shebang and runs under the system `sh` (dash on most Linux). `setup-smoke.yml` runs `sh -n setup.sh` on real Linux + macOS runners as the guard.
- The PAT is scoped to **`Contents: write` on `phuongddx/jarvis-dist` only** — no other repo, no other permission. Secret name: `JARVIS_RELEASE_TOKEN`, stored on `phuongddx/jarvis`.
- **No credential may ever appear in `setup.sh`** or any file synced to `jarvis`. `setup.sh` is fetched and executed by every user; a token in it is a leaked token.
- `JARVIS_REPO` in `setup.sh` is **left in place, untouched.** The new constant is additive.
- Conventional commit messages, no AI references.
- Test files mirror source modules 1:1. `setup.sh`'s tests live in `tests/test_setup_sh.py`, which sources `setup.sh` with `JARVIS_SETUP_SOURCED=1` (suppresses `main()`) and calls one function in isolation via the `run_func()` helper.
- Existing zoekt tag/asset naming is unchanged: tag `zoekt-<commit-pin>`, assets `zoekt-<os>-<arch>.tar.gz` + `.sha256`.

## Manual Prerequisites (Runbook A — do these before Task 1)

These are GitHub UI/API operations, not code. They must exist before Task 1's workflow can be tested end to end, but Tasks 1–5 can all be *written and committed* first — only the Migration runbook at the end actually requires them.

- [ ] **A1. Create the public repo.** `gh repo create phuongddx/jarvis-dist --public --description "Public distribution surface for jarvis: installer, Claude Code plugin, and prebuilt zoekt binaries. Source lives elsewhere; the package is on PyPI as jarvis-mcp."` then enable Issues (on by default for new repos — verify in Settings).

- [ ] **A2. Seed `jarvis` with a README** so the repo isn't empty and visitors aren't confused about where the source is:

```markdown
# jarvis

Public distribution surface for [jarvis](https://pypi.org/project/jarvis-mcp/).

This repo holds no source. It exists because GitHub serves release assets and raw
files only to viewers of the owning repo, and jarvis's development repo is private.

- **Install:** see the installer in this repo (`setup.sh`) and the package on PyPI
  (`uv tool install jarvis-mcp`).
- **Claude Code plugin:** `/plugin marketplace add phuongddx/jarvis-dist`
- **Bugs / feature requests:** file them in this repo's Issues.

Contents here are published automatically from the development repo. Do not edit
files here directly — changes will be overwritten on the next release.
```

- [ ] **A3. Create the fine-grained PAT.** GitHub Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token. **Resource owner:** `phuongddx`. **Repository access:** Only select repositories → `phuongddx/jarvis-dist`. **Permissions:** Repository permissions → Contents → **Read and write**. Nothing else. Note the expiry date (max 1 year) — see Unresolved Questions in the spec.

- [ ] **A4. Store it as a secret on the private repo:** `gh secret set JARVIS_RELEASE_TOKEN --repo phuongddx/jarvis` (paste the token when prompted). Verify with `gh secret list --repo phuongddx/jarvis`.

---

### Task 1: Point the zoekt release publish at `jarvis`

**Files:**
- Modify: `.github/workflows/build-zoekt.yml:1-6` (header comment), `:92-106` (`Publish release` step)

**Interfaces:**
- Consumes: secret `JARVIS_RELEASE_TOKEN` (Runbook A4)
- Produces: zoekt releases under tag `zoekt-<commit>` in `phuongddx/jarvis-dist`, with assets `zoekt-<os>-<arch>.tar.gz` and `.sha256`. Task 3's `setup.sh` downloads from exactly this location.

This workflow has no automated test (workflow YAML isn't unit-testable in this repo), so its verification steps are a YAML parse check plus a grep audit — and ultimately the Migration runbook, which runs it for real.

- [ ] **Step 1: Update the header comment**

`.github/workflows/build-zoekt.yml` lines 1–6 currently read:

```yaml
# Cross-compiles zoekt for the platforms setup.sh supports and publishes the
# binaries to this repo's releases.
#
# Why this exists: upstream sourcegraph/zoekt publishes no releases and no tags,
# so there is no prebuilt zoekt-index/zoekt-webserver to download on any
# platform. setup.sh downloads from *our* releases instead.
```

Replace with:

```yaml
# Cross-compiles zoekt for the platforms setup.sh supports and publishes the
# binaries to phuongddx/jarvis-dist — a separate PUBLIC repo, not this one.
#
# Why this exists: upstream sourcegraph/zoekt publishes no releases and no tags,
# so there is no prebuilt zoekt-git-index/zoekt-webserver to download on any
# platform. setup.sh downloads from our own releases instead.
#
# Why jarvis and not here: this repo is private, and GitHub serves release
# assets only to viewers of the owning repo — an unauthenticated `curl` against
# a private repo's release 404s, which is exactly what every user's setup.sh run
# would hit. jarvis is public and holds nothing but published artifacts.
```

- [ ] **Step 2: Update the `Publish release` step**

The step at `:92-106` currently reads:

```yaml
      - name: Publish release
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          ZOEKT_COMMIT: ${{ steps.pin.outputs.commit }}
        run: |
          set -eu
          tag="zoekt-${ZOEKT_COMMIT}"
          if gh release view "$tag" >/dev/null 2>&1; then
            gh release upload "$tag" dist/*.tar.gz dist/*.sha256 --clobber
          else
            gh release create "$tag" dist/*.tar.gz dist/*.sha256 \
              --title "zoekt @ ${ZOEKT_COMMIT}" \
              --notes "zoekt-git-index and zoekt-webserver cross-compiled from sourcegraph/zoekt@${ZOEKT_COMMIT}. Consumed by setup.sh."
          fi
```

Replace with (note `--repo` on **all three** `gh` invocations, and the token swap):

```yaml
      - name: Publish release
        env:
          # JARVIS_RELEASE_TOKEN, not GITHUB_TOKEN: the default token cannot
          # write to a different repo. Fine-grained, Contents:write on
          # phuongddx/jarvis-dist only.
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

- [ ] **Step 3: Verify the YAML still parses and the audit is clean**

Run:

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/build-zoekt.yml')); print('YAML valid')"
grep -c "phuongddx/jarvis-dist" .github/workflows/build-zoekt.yml
grep -n "secrets.GITHUB_TOKEN" .github/workflows/build-zoekt.yml
```

Expected: `YAML valid`; the `jarvis` count is **4** (3 `gh` calls + 1 comment mention); the `GITHUB_TOKEN` grep prints **nothing** (that step was the only consumer).

- [ ] **Step 4: Confirm `permissions: contents: write` is still correct**

Run: `sed -n '18,20p' .github/workflows/build-zoekt.yml`

Expected: still `permissions:` / `  contents: write`. Leave it — `actions/checkout` on a private repo needs it, and it governs *this* repo, not `jarvis` (which the PAT governs). Do not remove it.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/build-zoekt.yml
git commit -m "build: publish zoekt binaries to the public jarvis repo

This repo is private, so GitHub serves its release assets only to viewers
-- an unauthenticated curl from setup.sh 404s. Publishes to
phuongddx/jarvis-dist instead, a public repo holding only artifacts, via a
fine-grained PAT scoped to Contents:write on that one repo."
```

---

### Task 2: Sync the distribution files into `jarvis`

**Files:**
- Create: `.github/workflows/sync-public-distribution.yml`

**Interfaces:**
- Consumes: secret `JARVIS_RELEASE_TOKEN` (Runbook A4)
- Produces: `setup.sh`, `.claude-plugin/marketplace.json`, and `plugin/**` present on `phuongddx/jarvis-dist`'s default branch. Task 4's documentation redirects point users at exactly these paths.

Release-triggered, not push-triggered: `plugin/.claude-plugin/plugin.json` carries the version, so syncing on release keeps the published plugin's version matched to the published PyPI package. A `setup.sh` change landing on `main` without a release therefore does not reach `jarvis` until the next release — intended, not a gap.

- [ ] **Step 1: Create the workflow**

Create `.github/workflows/sync-public-distribution.yml`:

```yaml
# Publishes jarvis's public distribution surface into phuongddx/jarvis-dist.
#
# Why this exists: this repo is private, so raw.githubusercontent.com and the
# plugin marketplace both 404 for anyone without access -- which is every real
# user. jarvis is public and holds only what a user needs to install and report
# bugs: the installer script, the Claude Code plugin definition, and (via
# build-zoekt.yml) the prebuilt zoekt binaries.
#
# Deliberately NOT synced: src/ (already on PyPI), tests/, docs/, plans/,
# .github/, CHANGELOG.md, pyproject.toml, git history, branches.
#
# Release-triggered because plugin/.claude-plugin/plugin.json carries the
# version: syncing on release keeps the published plugin matched to the
# published PyPI package. A change landing on main without a release does not
# reach jarvis until the next release, which is intended.
name: sync-public-distribution

on:
  release:
    types: [published]
  workflow_dispatch:

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - name: Check out this repo
        uses: actions/checkout@v4

      - name: Clone jarvis
        env:
          # Fine-grained PAT, Contents:write on phuongddx/jarvis-dist only. The
          # default GITHUB_TOKEN cannot write to another repo.
          GH_TOKEN: ${{ secrets.JARVIS_RELEASE_TOKEN }}
        run: |
          set -eu
          git clone "https://x-access-token:${GH_TOKEN}@github.com/phuongddx/jarvis-dist.git" "${RUNNER_TEMP}/jarvis"

      - name: Copy the distribution surface over jarvis's contents
        run: |
          set -eu
          dest="${RUNNER_TEMP}/jarvis"
          # rsync --delete so a file removed here is removed there, but never
          # touch .git or jarvis's own README (which describes jarvis itself and
          # has no counterpart in this repo).
          rsync -a --delete --exclude '.git/' setup.sh "${dest}/setup.sh"
          rsync -a --delete --exclude '.git/' .claude-plugin/ "${dest}/.claude-plugin/"
          rsync -a --delete --exclude '.git/' plugin/ "${dest}/plugin/"
          ls -la "$dest"

      - name: Commit and push only if something changed
        env:
          GH_TOKEN: ${{ secrets.JARVIS_RELEASE_TOKEN }}
          SOURCE_SHA: ${{ github.sha }}
        run: |
          set -eu
          cd "${RUNNER_TEMP}/jarvis"
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add -A
          # An empty commit on a no-op release is noise, not a record.
          if git diff --cached --quiet; then
            echo "jarvis already up to date; nothing to push"
            exit 0
          fi
          git commit -m "chore: sync distribution surface from jarvis@${SOURCE_SHA}"
          git push
```

- [ ] **Step 2: Verify the YAML parses and the token is never echoed**

Run:

```bash
python3 -c "import yaml; d=yaml.safe_load(open('.github/workflows/sync-public-distribution.yml')); print('YAML valid'); print('triggers:', list(d['on'].keys()))"
grep -nE '\becho\b.*GH_TOKEN|\bset -x\b' .github/workflows/sync-public-distribution.yml
```

Expected: `YAML valid`, `triggers: ['release', 'workflow_dispatch']`; the second grep prints **nothing** (the token is interpolated into a clone URL, so `set -x` would leak it into logs — the workflow deliberately uses `set -eu`, never `set -eux`).

- [ ] **Step 3: Confirm the synced set matches the spec exactly**

Run:

```bash
find setup.sh .claude-plugin plugin -type f | sort
find setup.sh .claude-plugin plugin -type f | wc -l
```

Expected: 15 files (`setup.sh`, `.claude-plugin/marketplace.json`, and 13 under `plugin/`). Confirm nothing under `src/`, `tests/`, `docs/`, or `plans/` appears.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/sync-public-distribution.yml
git commit -m "build: sync installer and plugin into the public jarvis repo

A stable \`curl | sh\` URL needs setup.sh to be a file on a branch, not a
release asset, and the plugin marketplace reads marketplace.json plus
plugin/** as real files -- so binaries alone were never enough to make
this repo's privacy survivable for users. Syncs on release so the
published plugin version matches the published package."
```

---

### Task 3: Point `setup.sh` at `jarvis`, with a regression guard

**Files:**
- Modify: `setup.sh:5` (usage comment), `:23` area (new constant), `:437-439` (`install_zoekt` comment), `:456` (URL construction)
- Test: `tests/test_setup_sh.py`

**Interfaces:**
- Consumes: the release location Task 1 publishes to (`phuongddx/jarvis-dist`, tag `zoekt-<pin>`)
- Produces: shell constant `ZOEKT_RELEASE_REPO="phuongddx/jarvis-dist"`, consumed only by `install_zoekt`'s URL construction.

**Deviation from the spec, deliberate:** the spec says "no test changes required." That is true of *existing* tests, and remains true — but this task **adds** two, because the whole failure mode was "the binaries are served from a private repo and nobody noticed." A test that pins the invariant is the cheapest possible guard against a future revert. Adding coverage the spec didn't ask for is justified here; silently *reducing* it would not be.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setup_sh.py`:

```python
def test_zoekt_release_repo_is_set():
    """The zoekt binaries come from a dedicated public repo, not this one."""
    assert run_func('echo "$ZOEKT_RELEASE_REPO"').stdout.strip() == "phuongddx/jarvis-dist"


def test_zoekt_release_repo_is_not_the_private_repo():
    """The invariant, not just the value: GitHub serves release assets only to
    viewers of the owning repo, so pointing zoekt downloads at the private
    development repo 404s for every real user. This test is the guard against
    that regression -- it is how the original outage would have been caught.

    The non-empty assertion comes first deliberately: without it, an unset
    ZOEKT_RELEASE_REPO makes `"" != "phuongddx/jarvis"` true and the test
    passes vacuously, guarding nothing."""
    release_repo = run_func('echo "$ZOEKT_RELEASE_REPO"').stdout.strip()
    private_repo = run_func('echo "$JARVIS_REPO"').stdout.strip()
    assert release_repo, "ZOEKT_RELEASE_REPO is unset"
    assert private_repo, "JARVIS_REPO is unset"
    assert release_repo != private_repo
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_setup_sh.py -k zoekt_release_repo -v`

Expected: **both** FAIL. `ZOEKT_RELEASE_REPO` is unset, so `echo "$ZOEKT_RELEASE_REPO"` prints an empty string — the first test fails asserting `"" == "phuongddx/jarvis-dist"`, and the second fails on `assert release_repo` with `ZOEKT_RELEASE_REPO is unset`. If either passes at this stage, stop: the constant already exists somewhere and the rest of this task needs rechecking.

- [ ] **Step 3: Add the constant**

`setup.sh` currently has, around line 20–23:

```sh
# Kept in sync with the repo-root ZOEKT_COMMIT file that CI builds from.
# tests/test_setup_sh.py asserts the two never drift.
ZOEKT_COMMIT_PIN="33f1f18af292"
JARVIS_REPO="phuongddx/jarvis"
```

Append immediately after `JARVIS_REPO`:

```sh

# The zoekt binaries are published to a separate PUBLIC repo. jarvis's own
# repo is private, and GitHub serves release assets only to viewers of the
# owning repo -- an unauthenticated `curl` against a private repo's release
# 404s, which is every user running this script. Do not point this back at
# JARVIS_REPO; tests/test_setup_sh.py asserts the two differ.
ZOEKT_RELEASE_REPO="phuongddx/jarvis-dist"
```

- [ ] **Step 4: Use it in the URL**

Line 456 currently reads:

```sh
	_base="${ZOEKT_BASE_URL:-https://github.com/${JARVIS_REPO}/releases/download/zoekt-${ZOEKT_COMMIT_PIN}}"
```

Change `${JARVIS_REPO}` to `${ZOEKT_RELEASE_REPO}`:

```sh
	_base="${ZOEKT_BASE_URL:-https://github.com/${ZOEKT_RELEASE_REPO}/releases/download/zoekt-${ZOEKT_COMMIT_PIN}}"
```

Leave the `# ZOEKT_BASE_URL is overridable so tests can serve a local tarball.` comment above it as-is.

- [ ] **Step 5: Update the two comments**

Line 5 (the usage example in the file header) currently reads:

```sh
#   curl -fsSL https://raw.githubusercontent.com/phuongddx/jarvis-dist/main/setup.sh | sh
```

Change to:

```sh
#   curl -fsSL https://raw.githubusercontent.com/phuongddx/jarvis-dist/main/setup.sh | sh
```

Lines 437–439 (the `install_zoekt` block comment) currently read:

```sh
# zoekt ships as one tarball containing both binaries. Upstream
# sourcegraph/zoekt publishes no releases at all, so these come from
# jarvis's own releases (see .github/workflows/build-zoekt.yml).
```

Change to:

```sh
# zoekt ships as one tarball containing both binaries. Upstream
# sourcegraph/zoekt publishes no releases at all, so these come from our own
# releases in the public phuongddx/jarvis-dist repo -- NOT from jarvis's own
# repo, which is private and would 404 (see build-zoekt.yml, and
# ZOEKT_RELEASE_REPO above).
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_setup_sh.py -k zoekt_release_repo -v`

Expected: both PASS.

- [ ] **Step 7: Verify POSIX compliance and the full setup.sh suite**

Run:

```bash
sh -n setup.sh && echo "parses under system sh"
dash -n setup.sh && echo "parses under dash"
uv run pytest tests/test_setup_sh.py -q
```

Expected: both parse checks print their success line; the suite is green. If `dash` is missing, `brew install dash` — the suite prefers it because macOS `/bin/sh` is bash in POSIX mode and *accepts* bashisms, giving false confidence.

- [ ] **Step 8: Confirm no stale private-repo reference remains in the zoekt path**

Run: `grep -n "JARVIS_REPO" setup.sh`

Expected: exactly **one** line — the `JARVIS_REPO="phuongddx/jarvis"` definition itself. Any remaining *use* of it means the URL edit was missed.

- [ ] **Step 9: Commit**

```bash
git add setup.sh tests/test_setup_sh.py
git commit -m "fix(setup): download zoekt from the public jarvis repo

Release assets inherit their repo's visibility, so serving them from the
private development repo 404s for every unauthenticated installer -- which
is every real user. Adds ZOEKT_RELEASE_REPO and a test asserting it never
equals JARVIS_REPO, so the regression cannot land again silently."
```

---

### Task 4: Redirect every advertised install path

**Files:**
- Modify: `README.md:17`, `:30`, `:341`; `plugin/skills/jarvis-setup/SKILL.md:22`; `plugin/skills/jarvis-issues/SKILL.md:11`, `:68`; `CLAUDE.md:98` area

**Interfaces:**
- Consumes: the `jarvis` paths Tasks 1–3 establish (`raw.githubusercontent.com/phuongddx/jarvis-dist/main/setup.sh`, `phuongddx/jarvis-dist` as marketplace + issue target)
- Produces: nothing consumed by later tasks

All six user-facing references currently name the private repo. Each is a documented instruction that fails when followed.

- [ ] **Step 1: Redirect the two `curl | sh` URLs**

`README.md:17` and `plugin/skills/jarvis-setup/SKILL.md:22` are byte-identical lines:

```bash
curl -fsSL https://raw.githubusercontent.com/phuongddx/jarvis-dist/main/setup.sh | sh
```

Change both to:

```bash
curl -fsSL https://raw.githubusercontent.com/phuongddx/jarvis-dist/main/setup.sh | sh
```

- [ ] **Step 2: Redirect the two marketplace references**

`README.md:30` and `README.md:341` both read:

```
/plugin marketplace add phuongddx/jarvis
```

Change both to:

```
/plugin marketplace add phuongddx/jarvis-dist
```

The `/plugin install jarvis@jarvis` line directly below each is **unchanged** — that names the marketplace entry and plugin from `marketplace.json` (`"name": "jarvis"`) and the plugin's own `plugin.json`, neither of which is the repo name.

- [ ] **Step 3: Redirect the issue-filing target**

`plugin/skills/jarvis-issues/SKILL.md:11` reads:

> File well-formed bug reports and feature requests against **phuongddx/jarvis** on GitHub. This skill targets only the jarvis project itself, not other repos.

Change to:

> File well-formed bug reports and feature requests against **phuongddx/jarvis-dist** on GitHub — jarvis's public issue tracker. (Development happens in a private repo; `jarvis` is where the installer, plugin, and binaries are published, and where issues are filed.) This skill targets only the jarvis project itself, not other repos.

`plugin/skills/jarvis-issues/SKILL.md:68` reads:

```bash
gh issue create --repo phuongddx/jarvis --title "<title>" --body "<body>"
```

Change to:

```bash
gh issue create --repo phuongddx/jarvis-dist --title "<title>" --body "<body>"
```

- [ ] **Step 4: Document the split in CLAUDE.md**

Insert a new paragraph after the `searchCoverage`/shard-ordinal paragraph that currently ends at `CLAUDE.md:98` (`...serving partial results.`) and before `**Language detection reads git, not the filesystem:**`:

```markdown
**Two repos: private development, public distribution.** This repo is private, and
GitHub serves raw files, release assets, and marketplace metadata only to viewers of
the owning repo — so every install path advertised from here 404s for a real user.
`phuongddx/jarvis-dist` is a public repo holding the public distribution surface: a synced
copy of `setup.sh`, the plugin definition (`.claude-plugin/` + `plugin/`), the zoekt
release assets, and the issue tracker. It is a publication target, never edited by
hand — `sync-public-distribution.yml` overwrites it on every release, and
`build-zoekt.yml` publishes the binaries there. Nothing else is mirrored: `src/` is
already on PyPI, and history, issues, `docs/`, `plans/`, and CI definitions stay
private. Privacy here protects the development process, not the source — the
published PyPI wheel already contains every module in readable form.
```

- [ ] **Step 5: Verify no user-facing reference to the private repo remains**

Run:

```bash
grep -rn "phuongddx/jarvis" README.md plugin/ setup.sh
```

Expected: **no output.** Every hit is a user-facing instruction that would 404.

Then confirm the intended references are present:

```bash
grep -rc "phuongddx/jarvis-dist" README.md plugin/skills/jarvis-setup/SKILL.md plugin/skills/jarvis-issues/SKILL.md setup.sh
```

Expected: `README.md:3` (curl + two marketplace lines), `jarvis-setup/SKILL.md:1` (curl),
`jarvis-issues/SKILL.md:2` (prose + `gh` command), `setup.sh:3` (the `:5` usage comment, the
`ZOEKT_RELEASE_REPO` constant, and the rewritten `install_zoekt` comment from Task 3 Step 5).

- [ ] **Step 6: Confirm the suite is unaffected**

Run: `uv run pytest -m "not integration" -q`

Expected: green, same count as before this task (documentation-only changes plus Task 3's two new tests).

- [ ] **Step 7: Commit**

```bash
git add README.md plugin/skills/jarvis-setup/SKILL.md plugin/skills/jarvis-issues/SKILL.md CLAUDE.md
git commit -m "docs: point every install path at the public jarvis repo

The curl URL, both plugin-marketplace references, and the issue-filing
target all named the private repo, so each one 404d when followed.
Documents the two-repo split in CLAUDE.md, including that privacy here
protects the development process rather than the source, which the PyPI
wheel already ships in full."
```

---

### Task 5: Fix the smoke test that caught this

**Files:**
- Modify: `.github/workflows/setup-smoke.yml:54-56`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces: nothing consumed by later tasks

`setup-smoke.yml` performs a **real anonymous download** on Linux and macOS runners. It is what detected this outage (run `30886044715`, `curl: (22) ... error: 404`) and it is the permanent guard for this bug class. Its binary-name assertions are stale since the `zoekt-index` → `zoekt-git-index` rename: once the 404 is fixed and the job gets that far, it fails on line 55 instead.

Do not "simplify" this job into a mocked download. Exercising the real public URL is the entire point.

- [ ] **Step 1: Update the stale binary name**

Lines 54–56 currently read:

```yaml
          # zoekt-index has no -version flag and `-h` exits non-zero.
          "${bin}/zoekt-index" -h >help.txt 2>&1 || true
          grep -q "zoekt-index" help.txt
```

Replace with:

```yaml
          # zoekt-git-index has no -version flag and `-h` exits non-zero.
          "${bin}/zoekt-git-index" -h >help.txt 2>&1 || true
          grep -q "zoekt-git-index" help.txt
```

- [ ] **Step 2: Verify the YAML parses and no stale name remains**

Run:

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/setup-smoke.yml')); print('YAML valid')"
grep -n "zoekt-index" .github/workflows/setup-smoke.yml | grep -v "zoekt-git-index"
```

Expected: `YAML valid`; the grep prints **nothing**.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/setup-smoke.yml
git commit -m "ci: assert the real binary name in the setup smoke test

Stale since the zoekt-index -> zoekt-git-index rename: the job downloads
the correct binary but then greps for a name setup.sh no longer installs,
so it would fail on that line as soon as the download itself succeeds."
```

---

## Migration Runbook (Runbook B — after Tasks 1–5 are merged)

Ordered, one-time. Runbook A (the repo, README, PAT, and secret) must already be done.

- [ ] **B1. Confirm the secret is visible to Actions**

```bash
gh secret list --repo phuongddx/jarvis | grep JARVIS_RELEASE_TOKEN
```

Expected: one line. If absent, redo Runbook A4 — every later step fails without it.

- [ ] **B2. Publish the zoekt release into `jarvis`**

```bash
gh workflow run build-zoekt.yml --repo phuongddx/jarvis
gh run list --repo phuongddx/jarvis --workflow=build-zoekt.yml --limit 1
gh run watch <run-id> --repo phuongddx/jarvis --exit-status
```

Expected: success. Then confirm the assets landed:

```bash
gh release view "zoekt-$(tr -d '[:space:]' < ZOEKT_COMMIT)" --repo phuongddx/jarvis-dist --json assets -q '.assets[].name'
```

Expected: 8 names — `zoekt-{darwin,linux}-{amd64,arm64}.tar.gz` and their `.sha256`.

- [ ] **B3. Sync the distribution files into `jarvis`**

```bash
gh workflow run sync-public-distribution.yml --repo phuongddx/jarvis
gh run list --repo phuongddx/jarvis --workflow=sync-public-distribution.yml --limit 1
gh run watch <run-id> --repo phuongddx/jarvis --exit-status
```

- [ ] **B4. Verify unauthenticated access — the actual acceptance test**

Every prior verification in this plan used authenticated tooling. This step is the one that reproduces a real user's position. Run it with **no credentials** (a plain `curl` sends none; do not substitute `gh`, which is authenticated and will mask a failure):

```bash
curl -sI "https://raw.githubusercontent.com/phuongddx/jarvis-dist/main/setup.sh" | head -1
curl -sI "https://github.com/phuongddx/jarvis-dist/releases/download/zoekt-$(tr -d '[:space:]' < ZOEKT_COMMIT)/zoekt-darwin-arm64.tar.gz" | head -1
curl -s -o /dev/null -w "%{http_code}\n" "https://api.github.com/repos/phuongddx/jarvis-dist"
```

Expected: `HTTP/2 200` for the raw file; a `302` (GitHub redirects release assets to its CDN) for the tarball; `200` for the API. Any `404` means the repo is not actually public or the file/asset is missing — stop and fix before touching the docs' claims.

- [ ] **B5. Run the installer end to end as a user would**

In a scratch directory, with `JARVIS_BIN_DIR` pointed somewhere disposable so your real `~/.jarvis/bin` is untouched:

```bash
tmp=$(mktemp -d)
curl -fsSL "https://raw.githubusercontent.com/phuongddx/jarvis-dist/main/setup.sh" | JARVIS_BIN_DIR="$tmp/bin" sh -s -- --only zoekt
ls -la "$tmp/bin"
"$tmp/bin/zoekt-git-index" -h 2>&1 | head -2
rm -rf "$tmp"
```

Expected: both binaries present; `-h` prints `Usage of .../zoekt-git-index:`. This is the exact command the README tells users to run.

- [ ] **B6. Verify the plugin marketplace path**

Spec Unresolved Question 2: the *failure* mode against a private repo is confirmed, but the *success* path against a fresh public repo has not been tested. From a clean Claude Code profile:

```
/plugin marketplace add phuongddx/jarvis-dist
/plugin install jarvis@jarvis
```

Expected: both succeed and the MCP server registers. If the marketplace needs anything beyond `marketplace.json` + `plugin/**`, this is where it surfaces — file what's missing and extend the sync in Task 2's workflow rather than hand-adding it to `jarvis`.

- [ ] **B7. Leave the old release alone**

The `zoekt-<pin>` release inside the private `jarvis` repo stays in place, untouched. It is dead weight the moment `setup.sh` stops pointing at it. Deleting it is not part of this migration — nothing should be able to strand an in-flight install.

---

## Verification Summary

Facts this plan rests on, all verified against the live repo and network at authoring time:

| Fact | Evidence |
|---|---|
| Private release assets are unreachable anonymously | `curl -sI` on the zoekt tarball → `HTTP/2 404` |
| `raw.githubusercontent.com` honors repo visibility | `curl -sI` on `jarvis/main/setup.sh` → `HTTP/2 404` |
| The marketplace path is dead three ways | raw URL 404, `git ls-remote` "Repository not found", `api.github.com` 404 |
| This is a recent regression | `setup-smoke` passed 07-26/07-31/08-01/08-02, failed 08-04 with `curl: (22) ... 404` on the same tag |
| `JARVIS_REPO` has exactly one use | `grep -n JARVIS_REPO setup.sh` → definition + `:456` only |
| No existing test asserts the URL's repo portion | `grep -n "JARVIS_REPO\|releases/download" tests/test_setup_sh.py` → no output |
| The `ZOEKT_COMMIT` drift test compares only the pin | `tests/test_setup_sh.py:571-573` |
| The synced set is 15 files / ~72 KB | `find setup.sh .claude-plugin plugin -type f \| wc -l` |
| `plugin/.mcp.json` needs no change | installs via `uvx --from jarvis-mcp>=0.2.1` — PyPI, not either repo |
| Privacy does not protect source | live PyPI wheel v0.4.0 ships all 17 modules readable (`index_cli.py` 51,951 bytes) |
| Publishing `setup.sh` is a real, small expansion | it is in neither the sdist nor the wheel |
