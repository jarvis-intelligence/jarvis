# API Coverage — Phase 2: scip-swift Toolchain Update

**Checkpoint:** API Coverage Decision Checkpoint (plan-phase, 2026-08-22)
**Detection result:** ONE external API surface in this phase's scope — the GitHub Releases REST API (anonymous), consumed by `setup.sh`'s rewritten `install_scip_swift`.

## Coverage Matrix

| # | Capability | Source (endpoint/field) | Consumer | Disposition | Verification |
|---|------------|------------------------|----------|-------------|--------------|
| 1 | Latest-release resolution | `GET https://api.github.com/repos/jarvis-intelligence/scip-swift/releases/latest` → `tag_name` | `setup.sh` `install_scip_swift` (D-01) | **INTEGRATE** | `tests/test_setup_sh.py` seam-served JSON tests (`SCIP_SWIFT_API_URL`) + live `sh setup.sh --only scip-swift` smoke + real run in setup-smoke CI |
| 2 | Asset URL discovery | same response → `assets[].browser_download_url` (first `.tar.gz` asset) | `setup.sh` (SWFT-02 — asset naming changed upstream in v0.2.0) | **INTEGRATE** | seam test asserts install from the JSON-supplied URL; never constructed from the tag |
| 3 | Asset checksum | same response → `assets[].digest` (`sha256:<64 hex>`, server-computed, immutable since 2025-06-03) | `setup.sh` `verify_sha256` after prefix strip (SWFT-02) | **INTEGRATE** | tampered-digest unit test (checksum mismatch fails install) + live byte-exact install |
| 4 | Asset download | `curl -fsSL --retry 3` of the discovered `browser_download_url` (release CDN) | `setup.sh` `download_to` (existing helper, reused) | **INTEGRATE** | live install smoke + CI macOS leg downloads for real |

## OPT-OUT Rows (unused capabilities, with reasons)

| Capability | Reason for opt-out |
|------------|--------------------|
| Redirect-based latest resolution (`curl -fsSI …/releases/latest` → `Location` header) | Would be a second resolution path; the API call is needed anyway for the digest — single-path principle (RESEARCH §Alternatives; one request per install also respects the anonymous rate limit) |
| Release creation / asset upload endpoints (D-03's cut-a-release contingency) | v0.3.0 already published with the xcodebuild-dispatch fix (2026-08-18, Latest) — no release ops this phase (orchestrator resolution #6) |
| `.sha256` sidecar asset download | Digest-only checksum per orchestrator resolution #1 — the API `digest` field satisfies SWFT-02 for every release, past and future; two checksum sources would mean two code paths |
| Prerelease/draft release fields | Not applicable — `releases/latest` excludes prereleases and drafts by definition (flagged assumption A2 in RESEARCH) |

**Rate-limit posture:** exactly one anonymous API call per `--only scip-swift` invocation (resolution + digest in the same fetch); failures surface the HTTP status loudly rather than silently falling back.

**Package Legitimacy Audit:** no npm/PyPI/crates installs this phase. The sole external artifact is the scip-swift v0.3.0 release tarball from the org-owned `jarvis-intelligence/scip-swift` repo — provenance established in RESEARCH (byte-exact sha256 match against the API digest + end-to-end functional verification); verdict OK, no blocking checkpoints required.
