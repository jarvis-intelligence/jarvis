---
name: jarvis-release
description: Cut a new release of the jarvis repo itself (not a user's indexed repo) — bump the version consistently across pyproject.toml, server.json, and uv.lock, add a CHANGELOG.md entry, open and merge a chore/release PR, tag and publish a GitHub Release, and confirm the publish-pypi/publish-mcp-registry pipeline actually completes. Use this whenever the user asks to release a new version, cut a release, ship vX.Y.Z, publish to PyPI, or asks what's needed to release recent changes — even if they only name one or two of these steps, since they're one pipeline and skipping any of them leaves the release half-done.
---

# Releasing jarvis

This captures jarvis's actual release process — every command here was run for real and verified working while cutting v0.3.1. Follow it in order; the steps are load-bearing on each other (CI gates the merge, the merge commit is what gets tagged, the tag is what the GitHub Release publishes, and publishing the release is what triggers PyPI/registry publishing).

## Before starting: what's actually being released?

`main` must already contain everything you're about to release. If there's a pending feature/fix branch that hasn't been merged yet, merge that first (its own PR, its own CI, its own review) — the release step that follows is purely mechanical version-bumping on top of an already-correct `main`. Don't conflate "ship my feature" with "cut a release"; they're sequential, not the same PR.

## 1. Decide the version bump

Semver, and the CHANGELOG is explicit about the reasoning behind past bumps — read `CHANGELOG.md`'s existing entries before picking, they set the calibration:

- **Patch** (`0.3.0` → `0.3.1`): pure bug fixes, no new capability. v0.2.1 and v0.3.1 were both patches.
- **Minor** (`0.2.x` → `0.3.0`): new capability, even if delivered alongside fixes. v0.3.0's own CHANGELOG entry says "Minor rather than patch: Java/Kotlin repos are indexable for the first time, `--search-only` is a new mode..." — that annotation is the model to follow when it's a judgment call.
- **Major**: hasn't happened yet in this repo's history; a breaking change to the MCP tool surface or CLI would warrant it.

If it's genuinely ambiguous (a fix that also quietly changes behavior), say what you're picking and why in one line before proceeding — this is the one place in the whole pipeline that isn't mechanical, and it's cheap to confirm before an irreversible PyPI upload locks it in.

## 2. Bump the version in all 3 files

These must all end up identical, or CI's tag/version guard (see step 6) fails the release:

| File | What to change |
|---|---|
| `pyproject.toml` | `version = "X.Y.Z"` |
| `server.json` | **two** fields: the top-level `"version"` and `packages[0].version` |
| `uv.lock` | **never hand-edit.** Run `uv lock` — it updates this package's own self-referential version entry (`Updated jarvis-mcp vX.Y.Z-1 -> vX.Y.Z` in its output) and re-resolves nothing else changes if no deps moved |

Grep to confirm consistency before committing: `grep -rn '"version"\|^version' pyproject.toml server.json | grep -v uv.lock` — or just run `uv run python scripts/check_versions.py`.

The Claude Code and Codex plugins are NOT part of this bump: their source of truth is
`jarvis-intelligence/jarvis-index` (`plugin/` + `.claude-plugin/` + `.codex-plugin/`
there), versioned independently. When a release changes behavior the plugin skills
describe, update those skills in jarvis-index directly and bump
`plugin/.claude-plugin/plugin.json` and `.codex-plugin/plugin.json` there so installed
plugins see an update. Keep its `.mcp.json` `--from` floor a valid `>=` minimum
against PyPI.

## 3. Add the CHANGELOG.md entry

New `## [X.Y.Z] - YYYY-MM-DD` section at the **top**, above the previous entry. Use `### Added` / `### Fixed` / `### Changed` subsections matching whichever apply. Write the *root cause*, not just "fixed a bug" — every existing entry in this file explains the mechanism (what broke, why, what the fix actually does), because that's what makes the changelog useful to someone debugging a regression months later. Look at the two or three most recent entries for tone and depth before writing a new one.

## 4. Verify locally before pushing anything

```bash
uv run pytest -m "not integration" -q          # must be green
grep -qF "mcp-name: io.github.jarvis-intelligence/jarvis" README.md && echo "marker present"
```

The second check matters because `publish-pypi.yml` hard-fails the release if this marker (which the MCP Registry uses to verify PyPI ownership) is ever missing from `README.md` — cheap to catch here instead of after a tag is already pushed.

## 5. Commit, branch, PR

```bash
git checkout -b chore/release-X.Y.Z
git add CHANGELOG.md pyproject.toml server.json uv.lock
git commit -m "chore: release X.Y.Z"
git push -u origin chore/release-X.Y.Z
gh pr create --base main --head chore/release-X.Y.Z --title "chore: release X.Y.Z" \
  --body "Patch/minor release for <one-line summary>. See CHANGELOG.md."
```

Wait for CI properly instead of hand-rolling a poll loop — `gh pr checks <N> --watch` blocks until every check reaches a final state and is far more reliable than re-checking in a bash `while`/`until` loop (which is easy to get wrong: `gh pr checks` exits non-zero while checks are pending, which trips up naive loop conditions and can look like a clean exit when it wasn't):

```bash
gh pr checks <PR-number> --watch --interval 15
```

If a check fails, stop — investigate the failure (`gh run view <run-id>`), don't force-merge past red CI. This is a public package release; a broken merge here ships broken to PyPI.

Once green:

```bash
gh pr merge <PR-number> --merge --delete-branch=false
```

## 6. Tag the merge commit

```bash
git checkout main && git pull origin main
git tag -a vX.Y.Z -m "vX.Y.Z" <merge-commit-sha>
git push origin vX.Y.Z
```

**Use `-a -m`, not a plain `git tag vX.Y.Z <sha>`.** This repo has `tag.gpgsign = true` in its git config, which forces every tag to be annotated+signed; a bare lightweight-tag invocation fails with a cryptic `fatal: no tag message?` because git tries to open an editor for the annotation message non-interactively. `-a -m "vX.Y.Z"` supplies that message directly and the signing happens automatically. Verify with `git tag -v vX.Y.Z` if anything looks off — it should show a `gpg: Good signature` line.

## 7. Create the GitHub Release

This is the step that actually triggers publishing — `publish-pypi.yml` listens for `release: published`. Look at 2-3 recent releases (`gh release view v0.3.0 --json body -q .body`) for the exact tone/structure before writing a new one; the pattern is consistently: one-line hook, an `## Install` block (Claude Code plugin + standalone paths), a `## What changed`/`## Fixed` section explaining the mechanism, a platform/scope footer sentence, and a link to `CHANGELOG.md`.

```bash
gh release create vX.Y.Z --title "vX.Y.Z — <short summary>" --notes "$(cat <<'EOF'
<one-line hook>

## Install
...

## Fixed / What changed
...

See [CHANGELOG.md](https://github.com/jarvis-intelligence/jarvis/blob/main/CHANGELOG.md).
EOF
)"
```

## 8. Confirm the publish pipeline actually finished

Publishing the release triggers two workflows in sequence — `publish-pypi.yml` (on `release: published`) runs the unit suite, checks the tag matches the packaged version, builds, verifies the wheel and the registry marker, smoke-tests a clean install, and publishes via PyPI trusted publishing; then `publish-mcp-registry.yml` (on `workflow_run`, only after `publish-pypi.yml` succeeds) publishes `server.json` to the official MCP Registry. Don't consider the release done until both show `success` — a red `publish-pypi.yml` run with a tag already pushed is a broken, half-shipped release that needs a *new* patch version to fix (you cannot re-upload or delete a PyPI version):

```bash
gh run list --repo jarvis-intelligence/jarvis --workflow=publish-pypi.yml --limit 1
gh run watch <run-id> --repo jarvis-intelligence/jarvis --exit-status   # or gh run view <run-id> if it already finished
gh run list --repo jarvis-intelligence/jarvis --workflow=publish-mcp-registry.yml --limit 1
```

**Verifying on PyPI itself:** don't trust `https://pypi.org/pypi/<pkg>/json`'s top-level `info.version` field alone — PyPI is eventually consistent and that field lags behind a just-published version by anywhere from seconds to a couple minutes (this is called out in `publish-mcp-registry.yml`'s own comments, which is exactly why that workflow retries on 404 rather than failing on the first miss). Check the full release list instead, which updates immediately:

```bash
curl -s https://pypi.org/pypi/<package-name>/json | python3 -c "import json,sys; print(sorted(json.load(sys.stdin)['releases'].keys()))"
```

## 9. Report back

Summarize concretely: which PR(s) merged (with numbers/URLs), the tag, the release URL, and the final status of both publish workflows. If anything in steps 6-8 failed partway (tag pushed but release workflow red, say), say exactly what state things are in — a half-published release needs a human decision about how to recover, not a silent retry.
