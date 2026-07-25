## ADDED Requirements

### Requirement: Swift extension detection
The system SHALL recognize `.swift` as a supported source extension in `detect_language()`'s extension-majority scan, on equal footing with the existing `.ts`/`.tsx`/`.py`/`.java`/`.kt` entries.

#### Scenario: Majority-Swift repo resolves to the Swift indexer
- **WHEN** a repo's source tree contains more `.swift` files than any other supported extension
- **THEN** `detect_language()` SHALL return `("swift", ["scip-swift", "index"])`

#### Scenario: Mixed-language repo still ties-breaks deterministically
- **WHEN** a repo contains an equal count of `.swift` files and another supported extension's files
- **THEN** the existing `_EXT_PRIORITY` tie-break order SHALL determine the winner, with `.swift` participating in that ordering the same way every other extension does

### Requirement: Swift indexing via scip-swift
The system SHALL invoke an external `scip-swift` binary (expected on `PATH`, following the same invocation convention as `scip-typescript`/`scip-python`/`scip-java`) to produce SCIP output for Swift repos, without modifying the downstream `scip expt-convert` → publish pipeline.

#### Scenario: Successful Swift index build
- **WHEN** `index_repo()` runs against a majority-Swift repo and the `scip-swift` binary is present on `PATH` and succeeds
- **THEN** the resulting SCIP output SHALL be converted via the existing `scip expt-convert` step and published via the existing atomic pointer-swap path, identically to how a TypeScript/Python/Java repo is published today

#### Scenario: Missing scip-swift binary
- **WHEN** `index_repo()` runs against a majority-Swift repo and no `scip-swift` binary is found on `PATH`
- **THEN** the system SHALL raise the existing `IndexingError`, following the same failure-marks-status behavior already used when any other language's indexer binary is missing or fails

### Requirement: SCIP nav tools available for Swift repos
Once a Swift repo has been indexed, the system SHALL expose the same 5 SCIP navigation tools and `blastRadius` for it that already exist for TypeScript/Python/Java/Kotlin repos, with no Swift-specific code path in `query.py`, `graph.py`, or `server.py`.

#### Scenario: Nav tools work against a published Swift index
- **WHEN** a Swift repo has been indexed and published
- **THEN** `documentSymbols`, `goToDefinition`, `findReferences`, `callHierarchy`, `typeHierarchy`, `getIndexStatus`, and `blastRadius` SHALL return results for it using the existing `QueryService`/`GraphStore` logic, unchanged
