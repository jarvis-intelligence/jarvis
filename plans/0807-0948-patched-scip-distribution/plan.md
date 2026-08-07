# Patched-scip Distribution Implementation Plan

**Brainstorm:** `plans/reports/brainstorm-0806-1610-patched-scip-distribution-report.md` (Option A approved)
**Status:** pending

**Goal:** End users installing via the public setup.sh get a `scip` binary with the scip#465 fix (populated `global_symbols.relationships`), so `typeHierarchy` works — without waiting for upstream to merge.

**Approach:** Mirror the zoekt pattern exactly: private-repo CI cross-compiles from the public fork and publishes tarball+sha256 assets to `jarvis-intelligence/jarvis-index` releases; setup.sh downloads from there instead of upstream scip releases.

## Facts (verified)

- Fork `phuongddx/scip` is PUBLIC; fix merged at `56791658a873bced14470a735d0f6821ee972cb0`.
- Fork's go.mod: module path `github.com/scip-code/scip` + `replace ... => ./bindings/go/scip` → must `git clone` + build in-tree; module-mode `go get` cannot work.
- Pure-Go sqlite (modernc) → `CGO_ENABLED=0` cross-compile works (verified locally on darwin/arm64, binary runs).
- `install_tarball_binary <url> <sha_url> <member> <dest>` extracts member `scip` from tar.gz root; `scip_asset_name` = `scip-<os>-<arch>.tar.gz` — keep both unchanged, our assets must match this layout.
- Sidecar format `"<digest>  <filename>"` from `sha256sum`.
- `ZOEKT_COMMIT` file = 12-char short SHA; `build-zoekt.yml` tags `zoekt-<commit>`, publishes via fine-grained `JARVIS_DIST_TOKEN` (already exists, Contents:write on jarvis-index).
- `setup-smoke.yml` runs on push AND pull_request → **release assets must exist on jarvis-index BEFORE the PR opens** or its CI 404s.
- `tests/test_setup_sh.py:324` `test_scip_version_is_pinned_not_latest` asserts `$SCIP_VERSION` starts with "v" — replaced by pin-drift test.

## Changes

1. **`SCIP_COMMIT`** (new, repo root): `56791658a873` — 12-char pin, mirrors `ZOEKT_COMMIT`.
2. **`.github/workflows/build-scip.yml`** (new, template `build-zoekt.yml`):
   - Triggers: push to main touching `SCIP_COMMIT`/workflow file; `workflow_dispatch`. Never scheduled.
   - Read pin → `git clone https://github.com/phuongddx/scip` → `git checkout <pin>` → for darwin/arm64, darwin/amd64, linux/amd64, linux/arm64: `CGO_ENABLED=0 GOOS/GOARCH go build -trimpath -o stage/scip ./cmd/scip` → `tar -czf dist/scip-<os>-<arch>.tar.gz -C stage scip`.
   - `sha256sum` sidecars; smoke-test linux/amd64 (`./scip --version`, `expt-convert --help`).
   - Publish tag `scip-<pin>` to `jarvis-intelligence/jarvis-index` with `JARVIS_DIST_TOKEN` (create or clobber, like zoekt).
   - Comment block: why this exists (scip#464 upstream never populates relationships; fix PR#465 unmerged; binaries built from public fork), exit ramp (upstream merges + releases → repoint setup.sh at upstream, delete this workflow + `SCIP_COMMIT`).
3. **`setup.sh`**: replace `SCIP_VERSION`/`SCIP_REPO` with `SCIP_COMMIT_PIN="56791658a873"` + `SCIP_RELEASE_REPO="jarvis-intelligence/jarvis-index"`; `install_scip` base URL → `releases/download/scip-${SCIP_COMMIT_PIN}`; comments explain why + exit ramp; failure message points at jarvis-index releases. `scip_asset_name` and `install_tarball_binary` untouched.
4. **`tests/test_setup_sh.py`**: replace `test_scip_version_is_pinned_not_latest` with (a) `SCIP_COMMIT` file ↔ `$SCIP_COMMIT_PIN` drift assertion (mirror zoekt's at :571), (b) `SCIP_RELEASE_REPO == "jarvis-intelligence/jarvis-index"` (mirror :860), (c) `SCIP_RELEASE_REPO != JARVIS_REPO` (mirror :869).
5. **`CLAUDE.md`**: "Known gap" paragraph updated — setup.sh now ships a fork build with the #465 fix; typeHierarchy works on fresh indexes; upstream PR still open; exit ramp noted.

## Rollout order (chicken-and-egg guard)

1. Implement in worktree, tests green.
2. **Before opening the PR:** cross-compile the 4 tarballs locally from the fork at the pin, `gh release create scip-56791658a873` on jarvis-intelligence/jarvis-index (phuongddx account) with assets + sidecars. Verify one URL with `curl -fsSL -o /dev/null -w '%{http_code}'`.
3. Open PR → setup-smoke CI now resolves the new URLs → merge.
4. Post-merge push triggers `build-scip.yml` (SCIP_COMMIT is new in that push) → CI rebuilds/clobbers the same tag; verifies the workflow works for future pin bumps.

## Success criteria

- `uv run pytest tests/test_setup_sh.py -q` green.
- setup-smoke CI green on the PR (real download+install of the fork scip).
- Fresh `sh setup.sh` on this machine installs scip whose `expt-convert` populates relationships.
- `shellcheck setup.sh` no new warnings (if shellcheck available).

## Risks

- jarvis-index release namespace: `scip-<pin>` tags coexist with `zoekt-<commit>` tags — no collision.
- Fork rebase burden until upstream merges (pin means no forced action; bumping the pin re-runs the workflow).
- macOS Gatekeeper: curl-downloaded binaries carry no quarantine xattr — same as zoekt today, no change.
