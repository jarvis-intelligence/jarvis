# Migrate distribution repo: phuongddx/jarvis-dist → jarvis-intelligence/jarvis-index

**Status:** COMPLETE (2026-08-06).
- A: jarvis-index created, seeded `a0ac274` (clean identity), tag pushed ✓
- B: PR #27 merged (`882aea8`); stray `rename-visual.html` swept in by `git add -A`, removed in `9c36bec` ✓
- C: zoekt release copied (8 assets, all 200) ✓; raw setup.sh 200 with new repo pin ✓; plugin 0.5.1 ✓; PAT re-scoped by user, sync dispatched and succeeded (genuine no-op) ✓
- D: phuongddx/jarvis-dist deleted, API returns 404 ✓ (stale contributor listing gone with it)
**Decisions (user):** name `jarvis-index`; delete old repo after verification; copy existing zoekt assets (no rebuild).

## Phases

### A — Create and seed the new repo
1. `gh repo create jarvis-intelligence/jarvis-index --public` (description matches old repo's role).
2. Assemble seed content locally: `setup.sh` + `.claude-plugin/` + `plugin/` from the private repo (after Phase B ref updates) + a rewritten dist README (owner refs → `jarvis-intelligence/jarvis-index`).
3. Single seed commit authored `phuongddx <95doanphuong@gmail.com>`; tag `zoekt-33f1f18af292` on it (setup.sh derives the release URL from this tag name).

### B — Update references in the private repo (PR)
4. Replace `phuongddx/jarvis-dist` → `jarvis-intelligence/jarvis-index` in the functional + living-doc files:
   `setup.sh`, `.github/workflows/sync-public-distribution.yml`, `.github/workflows/build-zoekt.yml`,
   `README.md`, `CLAUDE.md`, `plugin/README.md`, `plugin/.claude-plugin/plugin.json`,
   `.codex-plugin/plugin.json`, `plugin/skills/jarvis-issues/SKILL.md`, `plugin/skills/jarvis-setup/SKILL.md`,
   `docs/codebase-summary.md`, `docs/index.html`, `docs/project-overview-pdr.md`, `docs/project-roadmap.md`.
5. Leave `docs/superpowers/plans|specs/*` untouched (historical records).
6. PR → CI green → merge.

### C — Assets, secret, sync verification
7. Copy the 6 zoekt assets (3 platforms × tarball+sha256) from the old release to a new `zoekt-33f1f18af292` release on `jarvis-index`.
8. **User action:** mint a fine-grained PAT (Contents: read/write on `jarvis-intelligence/jarvis-index`) and update the `JARVIS_DIST_TOKEN` secret.
9. `workflow_dispatch` the sync → verify it pushes current content to `jarvis-index`.
10. Smoke checks: raw `setup.sh` URL 200, release asset URL 200, plugin manifest version 0.5.1.

### D — Delete the old repo (last, after C verified)
11. `gh repo delete phuongddx/jarvis-dist` (needs `delete_repo` scope — may require `gh auth refresh` or web UI). Also removes the stale contributor listing.

## Acceptance criteria
- `curl -fsSL https://raw.githubusercontent.com/jarvis-intelligence/jarvis-index/main/setup.sh` → 200, contains `ZOEKT_RELEASE_REPO="jarvis-intelligence/jarvis-index"`.
- `https://github.com/jarvis-intelligence/jarvis-index/releases/download/zoekt-33f1f18af292/zoekt-darwin-arm64.tar.gz` → 200.
- Sync workflow run pushes to jarvis-index (with the new PAT).
- `grep -rn "phuongddx/jarvis-dist"` in the private repo matches only `docs/superpowers/*` historical files.
- Old repo gone; new history contains only `phuongddx <95doanphuong@gmail.com>` / bot identities.

## Risks / notes
- Old install URLs break on delete (accepted — early stage).
- Sync workflow is broken between B-merge and step 8 (secret still scoped to old repo) — harmless; no release planned in that window.
- MCP registry `server.json` has no jarvis-dist refs (verified).
