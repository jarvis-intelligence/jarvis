# Brainstorm: Distributing Patched scip (PR#465 fix) to End Users

**Date:** 2026-08-06 | **Decision:** Option A — publish fork-built scip binaries to jarvis-index releases

## Problem

- Upstream `scip v0.9.0` `expt-convert` never populates `global_symbols.relationships` ([scip#464](https://github.com/scip-code/scip/issues/464)) → jarvis `typeHierarchy` returns explicit error on every real index.
- Fix exists: [scip#465](https://github.com/scip-code/scip/pull/465) (`fix/convert-populate-relationships`), authored from public fork `phuongddx/scip`, merged into fork main at `5679165`. Upstream: 0 reviews, 0 comments in 11 days; release cadence 1–2 months. Timeline unbounded.
- Framing correction: public setup.sh does NOT fail — all download sources public (scip-code/scip, jarvis-index, phuongddx/scip-swift, npm, PyPI). Users get working install with broken typeHierarchy. Feature gap, not install failure.
- Verified 2026-08-06: fork-built scip → reindex → published jarvis-mcp 0.5.1 returns real typeHierarchy results. `relationship_data_present()` self-heals; **no jarvis code change needed** — only binary distribution.

## Approaches Evaluated

| Option | Verdict | Notes |
|--------|---------|-------|
| A. Publish fork builds to jarvis-index releases (mirror build-zoekt.yml) | **CHOSEN** | Existing doctrine + token + helper; clean exit ramp |
| B. Releases on phuongddx/scip fork | Rejected | Splits distribution surface, manual/fork-side CI, contradicts "jarvis-index holds all artifacts" |
| C. Wait for upstream merge | Rejected as plan | Users broken for months; kept as convergence path |
| D. `go install` at setup time | Rejected | Requires Go on user machines; breaks POSIX-sh prebuilt-binary contract |

## Agreed Design (Option A)

1. **Pin file** `SCIP_COMMIT` at repo root — full fork commit SHA (mirrors `ZOEKT_COMMIT`).
2. **Workflow** `.github/workflows/build-scip.yml` (template: `build-zoekt.yml`):
   - Triggers: push to main touching `SCIP_COMMIT`/workflow file; `workflow_dispatch`. Never scheduled.
   - **Build differs from zoekt:** scip's go.mod has `replace github.com/scip-code/scip/bindings/go/scip => ./bindings/go/scip` and fork module path ≠ upstream path, so module-mode `go get` fetch won't work. Must `git clone https://github.com/phuongddx/scip` + checkout pin + `go build ./cmd/scip` in-tree.
   - Cross-compile darwin/arm64, darwin/amd64, linux/amd64, linux/arm64 with `CGO_ENABLED=0` (sqlite is pure-Go modernc — verified builds locally).
   - Assets: `scip-<os>-<arch>.tar.gz` (matches `scip_asset_name()` in setup.sh) + `sha256sum` sidecars.
   - Smoke test linux/amd64: `scip --version`, `scip expt-convert --help`.
   - Publish with existing `JARVIS_DIST_TOKEN` to `jarvis-intelligence/jarvis-index`, tag `scip-<commit12>`, notes stating "upstream v0.9.0 + PR#465 fix".
3. **setup.sh**: replace `SCIP_VERSION`/`SCIP_REPO` download base with `SCIP_RELEASE_REPO="jarvis-intelligence/jarvis-index"` + `SCIP_COMMIT_PIN`; base URL `releases/download/scip-<pin>`. Comment: why (upstream #464, fix unmerged), and the exit ramp. `install_tarball_binary` unchanged.
4. **Tests** `tests/test_setup_sh.py`: SCIP_COMMIT file ↔ setup.sh pin never drift (mirror zoekt assertion); release repo ≠ `JARVIS_REPO`.
5. **Convergence/exit ramp**: when upstream merges #465 + cuts release → repoint setup.sh to upstream release, delete `build-scip.yml` + `SCIP_COMMIT`. Also: ping upstream PR now to nudge review.

## Risks

- Fork maintenance until upstream merges (rebase only if newer upstream features needed; pin means no forced action).
- Schema drift: none — fix populates existing column; query path verified compatible end-to-end.
- Users receive updated setup.sh only when `sync-public-distribution.yml` next overwrites jarvis-index (runs on release) — needs a release or manual sync dispatch after implementation.
- Version stamp on fork builds reads `v0.9.0-dev` + SHA (cosmetic).

## Success Criteria

- Fresh `curl | sh` install on clean machine yields `scip` whose `expt-convert` populates relationships.
- `jarvis index` + `typeHierarchy` returns real super/subtypes on a repo with class hierarchies, via published jarvis-mcp.
- `tests/test_setup_sh.py` green; sha256 verification path unchanged.

## Next Steps

1. `/ck:plan` from this report → implement workflow + setup.sh + tests.
2. After merge: dispatch build-scip.yml, verify assets on jarvis-index, cut jarvis release (or manually dispatch sync) so public setup.sh updates.
3. Comment on scip#465 asking for review.

## Unresolved Questions

- Cut a jarvis release immediately after (to sync public setup.sh), or batch with next feature release?
