# Phase 4: Swift Failure Signatures - Context

**Gathered:** 2026-08-23
**Status:** Ready for planning

<domain>
## Phase Boundary

Known-unfixable scip-swift failures degrade automatically with no opt-in — matching the Kotlin/AGP pattern — using signatures captured from the pinned binary's real stderr. Delivers SWFT-04: verified scip-swift failure signatures join `_SEARCH_ONLY_SIGNATURES`, each captured from the real scip-swift 0.3.0 binary's stderr against a known-failing repo and pinned by a unit test embedding that exact output. Unmatched Swift failures keep failing hard.

</domain>

<decisions>
## Implementation Decisions

### Signature capture & set (Area 1 — accepted as proposed)
- Capture method: reproduce failures locally — craft known-failing Swift repo shapes (no build system, no IndexStore produced, …), run the real scip-swift 0.3.0, capture **verbatim stderr**. No curation from upstream prose.
- v1 signature set: the two SC1-named classes (no IndexStore produced; no build system detected) plus any other reproducible known-unfixable found during capture — every entry must meet the same real-stderr evidence bar.
- Test pinning: unit tests embed the **exact captured stderr** with a provenance comment (binary version, trigger repo shape); drift in either breaks loudly.
- Matching mechanism: substring match, identical to the existing Kotlin/AGP `_SEARCH_ONLY_SIGNATURES` entries. No regex.

### Semantics & interactions (Area 2 — accepted as proposed)
- Precedence: a matched signature takes the automatic `origin='signature'` permanent search-only path (Kotlin/AGP parity per SC1) regardless of the phase-3 fallback flag; the opt-in `degraded` state remains for unclassified failures only.
- Auto-roll wording drift (future scip-swift releases changing stderr): **fails hard by design** (SC3 safety direction) — drift → unmatched → hard failure, never silent degradation. Documented in the signature constant block; re-capture on version bump.

### Claude's Discretion
None flagged — all areas resolved with explicit answers.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `_SEARCH_ONLY_SIGNATURES` in `src/jarvis/index_cli.py` (~line 87) — the Kotlin/AGP substring list; Swift entries append beside them with per-signature causal reasons (phase-1 D-11: reasons only, no remedies).
- Signature fallback branch (index_cli.py ~867): stamps `origin='signature'`, `status_reason=matched reason`; `recovery_for(ORIGIN_SIGNATURE)` → `jarvis reindex <slug>` — all existing, zero new wiring for matching.
- `tests/test_index_cli.py` signature tests (`test_signature_fallback_stamps_signature_origin_and_matched_reason`, `test_manual_search_only_publish_stamps_manual_origin`) — pin patterns to mirror.
- Phase 2's installed scip-swift 0.3.0 (on PATH here; CI installs latest ≥ 0.3.0) — the capture instrument.
- Phase 2 fixtures: `tests/fixtures/mini_xcode_repo` (working xcodeproj) — base for crafting known-failing variants.

### Established Patterns
- Signature entry shape: `(substring, reason)` pairs scanned against the indexer's real stderr carrier in the failure branch.
- Pre-pipeline vs main-pipeline split: the indexer subprocess failure lands in the main-pipeline except where signatures are consulted (post-build-start by construction).

### Integration Points
- `_SEARCH_ONLY_SIGNATURES` list + the signature-consult code path (one list append per signature; matcher unchanged).
- Watch ignore set already covers scip-swift artifacts (phase 2) — no watch changes needed.

</code_context>

<specifics>
## Specific Ideas

No specific requirements — answers captured above.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>
