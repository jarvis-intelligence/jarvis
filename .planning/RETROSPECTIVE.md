# Retrospective

## Milestone: v1.0 — Indexing Robustness & scip-swift Update

**Shipped:** 2026-08-23
**Phases:** 5 | **Plans:** 11 | **Tasks:** 28

### What Was Built

- Registry failure persistence (origin + reason + untruncated stderr) with additive migration, read-time recovery derivation, and reporting across `jarvis status`/`list` + all MCP surfaces (`last_index_run`, `capabilities`, structured nav errors)
- scip-swift toolchain auto-roll (≥ 0.3.0, digest-verified install), `--cache-dir` isolation under the data dir, runtime floor, watch artifact ignores, byte-identical upstream fixture + CI macOS index smoke
- Opt-in self-healing fallback: tri-state CLI flag + env var, post-build-start degrade to a queryable search-only publish (exit 0 + one warning), full-build retry on every reindex, sha-keyed watch anti-treadmill
- Two scip-swift failure signatures (verbatim-captured from the real binary) joining the automatic Kotlin/AGP-style degradation
- TTY semantic-install onboarding: post-publish y/N offer, same-invocation enablement, per-repo decline memory, watch/MCP provably prompt-free

### What Worked

- Smart-discuss grey-area tables front-loaded every product decision; all five phases' planners translated them without re-litigating (checkers verified decision-by-decision)
- The edge-probe + prohibition-recall pipeline made verification honest: 47 judgment-tier prohibitions across phases all carried mechanical evidence, and human sign-off was a 30-second decision each time
- Live UAT execution by the orchestrator (real binaries, real ptys, real network) closed the loop the unit suite structurally cannot — degrade smoke with zoekt answering, watch-vs-index race, y-consent install
- Code review `--auto` loops converged: phase 3 found failure-of-failure paths two layers deep (published-flag gate, best-effort record_failure); every fix falsification-proven before acceptance

### What Was Inefficient

- CI-exposed test hermeticity (unpatched `check_scip_swift_version`) shipped green locally and failed on runners — the suite only proved hermeticity after the first real CI run (PR #39)
- The anonymous GitHub-API call in setup.sh 403-rate-limited on a shared runner IP once; hardening (GH_TOKEN/retry) deferred to debt
- Decision-coverage gate could not parse smart-discuss area-format CONTEXT.md (no D-NN ids) on three phases — checker verification substituted; format alignment is owed
- Five VALIDATION.md files seeded but never reconciled by validate-phase (Nyquist coverage TODOs at close)

### Patterns Established

- Additive registry columns + dedicated setters, never upsert conflict-list writes (NULL-collapse hazard) — three phases of proof
- Offer/prompt gating by argparse command identity, not isatty (watch is also a TTY)
- Publish-then-retire ordering inside `_publish_search_only`; `published`-flag gate around the degrade branch; `_record_failure_best_effort` never masks the primary error
- Verbatim-token signature capture with provenance comments; wording drift fails hard by design

### Key Lessons

- A stale planning blocker (phase 2's upstream gate) can outlive its resolution — verify against shipped reality at audit time
- Local green ≠ CI green when a test implicitly depends on a developer-machine binary; hermeticity must be forced (restricted PATH) in at least one local run
- pty-driven interactive UATs are cheap and decisive; the "human-only" surface shrinks to genuine judgment calls (prohibition sign-offs, risk acceptance)

### Cost Observations

- Model mix: gsd_light/gsd_standard/gsd_heavy tiering per ~/.omp config (mapper/checkers light; executors/verifiers standard; planner heavy)
- Sessions: 2 (2026-08-21/22 execute+verify of phases 1-2; 2026-08-22/23 autonomous completion of phases 3-5 + lifecycle)
- Notable: phase executors 6-27 min each; the two `--auto` review loops (phase 3) cost ~45 min and eliminated 4 shipped defects

## Cross-Milestone Trends

| Milestone | Phases | Plans | Tasks | Requirements | Status |
|-----------|--------|-------|-------|--------------|--------|
| v1.0 | 5 | 11 | 28 | 14/14 satisfied | shipped 2026-08-23 |
