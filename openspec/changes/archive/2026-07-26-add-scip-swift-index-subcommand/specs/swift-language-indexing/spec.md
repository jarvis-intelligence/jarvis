## MODIFIED Requirements

### Requirement: Swift indexing via scip-swift
The system SHALL invoke an external `scip-swift` binary (expected on `PATH`, following the same invocation convention as `scip-typescript`/`scip-python`/`scip-java`) to produce SCIP output for Swift repos, without modifying the downstream `scip expt-convert` → publish pipeline.

#### Scenario: Successful Swift index build
- **WHEN** `index_repo()` runs against a majority-Swift repo and the `scip-swift` binary is present on `PATH` and succeeds
- **THEN** the resulting SCIP output SHALL be converted via the existing `scip expt-convert` step and published via the existing atomic pointer-swap path, identically to how a TypeScript/Python/Java repo is published today

#### Scenario: Missing scip-swift binary
- **WHEN** `index_repo()` runs against a majority-Swift repo and no `scip-swift` binary is found on `PATH`
- **THEN** the system SHALL raise the existing `IndexingError`, following the same failure-marks-status behavior already used when any other language's indexer binary is missing or fails

#### Scenario: End-to-end verified for a real Swift repo
- **WHEN** `index_repo()` runs against a real SwiftPM fixture repo, with a `scip-swift` binary on `PATH` that supports the `index` subcommand
- **THEN** the resulting registry entry SHALL report `status == "indexed"` and `language == "swift"`, and the published SQLite database SHALL contain at least one row in `global_symbols`, proving the full detect → build → convert → publish → query pipeline works for Swift, not just that the binary is invoked
