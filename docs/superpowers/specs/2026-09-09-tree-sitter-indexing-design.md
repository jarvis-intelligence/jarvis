# Tree-sitter Syntax Baseline for the Indexing Pipeline

**Date:** 2026-09-09  
**Status:** Implemented (Tasks 1–7 complete; unit gate green at f03447c and the docs/release-gate commit).
**Scope:** One build-free syntax baseline alongside optional SCIP enrichment, using the existing MCP tools.  
**Decision prefix:** `TSI-01` through `TSI-12` below. Source comments implementing these decisions must reference this specification and the relevant named section.

## 1. Goal and approved constraints — TSI-01

Every `jarvis index`, `reindex`, and debounced watch indexing run builds a syntax index for supported Git-tracked files. The baseline consists of Tree-sitter syntax indexing and Zoekt lexical search. It requires neither the indexed project's compiler/build system nor embeddings. SCIP is optional enrichment: missing tooling or a known indexing failure must not prevent a valid baseline from publishing.

The user approved these choices:

1. Add build-free indexing alongside SCIP, not replace SCIP.
2. Run syntax indexing on every indexing run, including mixed-language repositories whose primary SCIP index succeeds.
3. Expose syntax results through `documentSymbols` and `goToDefinition`, with explicit provenance and ambiguity handling; add no MCP tool.
4. Return exit 0 when the baseline succeeds but SCIP is unavailable. Persist the cause and retry enrichment on explicit subsequent runs unless disabled.
5. Publish syntax and optional genuine SCIP tables in one immutable SQLite navigation snapshot selected by one `current` pointer.
6. Install curated per-language grammar packages as base Python dependencies. Do not vendor grammar binaries into Jarvis's own wheels or download them during indexing.
7. Cover every currently admitted semantic-index language plus JavaScript/JSX.
8. Replace the one-way search-only option with reversible `--scip` / `--no-scip` controls; remove opt-in degradation configuration.

### Non-goals

- Type inference, binding references, resolving imports, or deriving call/type/package relationships from syntax.
- Replacing Zoekt, SCIP, the embedding model, LanceDB, or reciprocal-rank fusion.
- New structural-search tools, editor/LSP integration, user-supplied grammars, or embedded-language injection indexing.
- An in-memory tree cache, edit-by-edit incremental parsing, a new daemon, or a cross-store transaction across SQLite, Zoekt, LanceDB, and the registry.
- Unrelated refactoring, version/release work, plugin changes in another repository, or changes to `.planning/`.

## 2. Existing behavior and evidence

The current implementation already parses source in `chunker.chunk_file`, but only inside optional semantic indexing. Both parser dependencies live in the `semantic` extra. `_DEF_NODE_TYPES` covers Python, TypeScript/TSX, Java, Kotlin, and Swift; other admitted languages fall back to fixed windows. `semantic.index_semantic` carries unchanged file rows forward using file hashes and the embedding-table identity.

`index_repo` currently checks the SCIP binary before resolving search-only mode. `_publish_search_only` writes Zoekt, retires SCIP artifacts, and publishes no navigation pointer. QueryService assumes every live connection has the converter's SCIP tables. Watch's same-commit failure suppression currently returns before the whole indexing run. These are the specific boundaries this design changes.

The locked `tree-sitter-language-pack==1.13.6` is not a self-contained grammar bundle: `get_parser` may download a release manifest and parser archive. An isolated experiment with an empty cache and network access denied failed while fetching that manifest. Its macOS ARM64 loader wheel is 2,037,258 bytes; the release's platform parser archive is 20,920,165 bytes compressed. Its locked Linux wheels are tagged `manylinux_2_34`. Moving that dependency to base requirements alone would not guarantee offline parsing or preserve platform compatibility.

The existing metadata reader derives the JSON sibling from the pointer's full database filename. A temporary-file experiment verified that `index-deadbeef-uniquegeneration.db` resolves `index-deadbeef-uniquegeneration.metadata.json` and recovers the commit. The writer, unlike the reader, currently hardcodes a commit-only metadata filename.

These were investigation checks, not an implementation test run or a complete cross-platform compatibility certification.

## 3. Grammar distribution and language coverage — TSI-02

### Delivery contract

Base requirements include `tree-sitter` and the 16 grammar distributions below. The `semantic` extra retains LanceDB and sentence-transformers, not a second grammar provider. Source and compiled installations share the same curated loader. No runtime downloads, repository grammar compilation, or fallback to `tree-sitter-language-pack` are allowed.

Use bounded, tested dependency versions. The following releases are the initial validation set, selected from current PyPI metadata; changing one requires rerunning the compatibility and extraction gates. A failed gate is a release blocker, not permission to silently omit a language or build a dependency from source on a user's machine.

| Language / parser selection | Accepted extensions | Grammar distribution | Initial version |
|---|---|---|---|
| Python | `.py` | `tree-sitter-python` | 0.25.0 |
| JavaScript, including JSX | `.js`, `.jsx`, `.mjs`, `.cjs` | `tree-sitter-javascript` | 0.25.0 |
| TypeScript | `.ts`, `.mts`, `.cts` | `tree-sitter-typescript` | 0.23.2 |
| TSX | `.tsx` | `tree-sitter-typescript` | 0.23.2 |
| Java | `.java` | `tree-sitter-java` | 0.23.5 |
| Kotlin | `.kt`, `.kts` | `tree-sitter-kotlin` | 1.1.0 |
| Swift | `.swift` | `tree-sitter-swift` | 0.7.3 |
| Go | `.go` | `tree-sitter-go` | 0.25.0 |
| Ruby | `.rb` | `tree-sitter-ruby` | 0.23.1 |
| Rust | `.rs` | `tree-sitter-rust` | 0.24.2 |
| C | `.c`, `.h` | `tree-sitter-c` | 0.24.2 |
| C++ | `.cpp`, `.cc`, `.cxx`, `.hpp`, `.hh`, `.hxx` | `tree-sitter-cpp` | 0.23.4 |
| C# | `.cs` | `tree-sitter-c-sharp` | 0.23.5 |
| PHP | `.php` | `tree-sitter-php` | 0.24.1 |
| Scala | `.scala`, `.sc` | `tree-sitter-scala` | 0.26.2 |
| Bash | `.sh`, `.bash` | `tree-sitter-bash` | 0.25.1 |
| SQL | `.sql` | `tree-sitter-sql` | 0.3.11 |

The initial runtime is `tree-sitter==0.26.0`. TypeScript and TSX are two parser selections from one package, not two packages. JavaScript/JSX uses one grammar. PHP uses the package's PHP-with-tags grammar for `.php` files. SQL coverage means the selected grammar's recognized declarations, not complete support for every database dialect. `.h` remains C, matching the existing extension policy; do not guess its language from neighboring files.

All 16 grammar candidates publish `abi3` wheels for Linux/macOS x86-64/ARM64 according to inspected PyPI metadata. Before dependency changes land, verify actual wheel-only resolution across CPython 3.12, 3.13, and 3.14 and run real parsing on the release targets. Metadata availability alone does not prove grammar/runtime ABI compatibility or extraction correctness.

### Loader boundary

A single internal language-to-package/factory mapping handles grammar loading, including the TypeScript and PHP factory-name exceptions. Load only languages used by the run, reuse a parser within that indexing worker, and do not share a mutable parser across workers. Package versions and the runtime version participate in extraction identity.

A missing package, incompatible grammar ABI, or broken grammar factory is a required-stage failure. It must not enter the semantic chunker's permissive fixed-window fallback and produce an apparently successful syntax baseline.

## 4. File selection, extraction, and reuse — TSI-03

### File manifest

Enumerate with `git ls-files -z`, not a recursive filesystem walk. Apply existing `IGNORED_DIRS`; index supported regular files from their working-tree bytes. Do not follow symlinks outside the repository or index submodule contents as if they belonged to the parent repository. Record excluded entries and reasons. Do not include untracked files.

The existing one-MiB source-size backstop applies to syntax parsing. A supported file skipped by the size limit is a coverage gap. Generated/minified-content heuristics remain semantic-admission policy, not a new syntax exclusion: generated declarations can still be useful navigation data. `--semantic-include` remains an embedding-only override; it does not change the tracked-file syntax boundary.

Persist file-level outcomes:

- `parsed`: supported file parsed without syntax errors, including a valid file with zero declarations.
- `partial`: grammar produced error/missing nodes; independently valid declarations were extracted.
- `failed`: file bytes could not be decoded as UTF-8; no declarations are asserted.
- `skipped`: a supported file was excluded by a declared safety limit, with its reason.
- `unsupported`: no parser selection for the extension; normal lexical-only coverage, not a parser failure.

Ignored-directory entries, symlinks, and submodules are exclusions with separate reasons, not successful parse records. An I/O/storage error reading an eligible file, or a tracked file disappearing during capture, fails the baseline rather than silently deleting its old symbols. Ordinary malformed source is not such an infrastructure failure.

Capture source bytes and hashes consistently for extraction. If admitted source changes during the run, do not publish syntax and SCIP as if they described one captured input. Detect the mismatch before the navigation pointer flips and fail the run; the user/watch can initiate a later run. The Git commit identifies HEAD, not the full working-tree state.

### Declaration contract

Extract named functions, methods, types (including classes, structs, interfaces, protocols, traits, enums, and aliases where the language has them), and enclosing namespaces/modules. Include explicitly named function-valued bindings such as JavaScript arrow functions. Bash functions and SQL `CREATE` declarations for named tables, views, functions, and procedures are in scope where the selected grammar recognizes them. Query-local aliases, parameters, arbitrary local variables, inferred exports, anonymous expressions, and references are not declaration targets for this feature.

Store each declaration's leaf name, qualified name, normalized kind, language, parent declaration/scope, identifier range, declaration range, and file identity. Qualification follows lexical named scopes; it does not infer a package or module from an import/build graph. Preserve case. Overloads and same-name declarations at distinct positions remain distinct.

Reuse the existing `DescriptorKind` vocabulary: named types and SQL tables/views are `TYPE`; functions, methods, procedures, and function-valued bindings are `METHOD`; namespaces/modules are `NAMESPACE`. Preserve the existing SCIP kind values. Do not introduce a parallel syntax-only kind enum.

Walk nested declarations, not just root children. Retain a declaration only when its name and declaration structure are valid; a body containing an unrelated syntax error may still have a valid declaration. Do not invent names for `ERROR`/`MISSING` nodes. Anonymous or invalid enclosing scopes do not supply fabricated qualification components.

Extraction rules are explicitly maintained per grammar, using named fields/query captures and real fixtures. Do not assume that switching from language-pack to individual packages preserves the old chunker's node shapes.

### Coordinates and identity

Retain original UTF-8 bytes; all internal Tree-sitter offsets and half-open byte ranges refer to those bytes. Never slice a Python `str` with `start_byte` or `end_byte`. Convert to text only after byte slicing.

Navigation `Range` line numbers remain zero-based, matching the actual existing `query.py`/`scip_range_to_positions` behavior and `test_query.py` expectations. Syntax navigation columns are zero-based UTF-8 byte columns and are explicitly labeled `positionEncoding: "utf-8"`. SCIP coordinates retain their existing representation; this feature does not silently normalize or relabel unknown SCIP encodings. Search and embedding chunk line numbers remain one-based. A syntax-to-chunk conversion performs the line-base conversion at that boundary only.

Syntax identifiers use a reserved `syntax:` prefix plus a deterministic digest of repository-relative path, raw file hash, declaration identifier byte range, and kind. They are opaque to callers, not SCIP strings and not an alternative query language. An unchanged declaration in an unchanged file retains its identifier across generations; changed or deleted declarations do not redirect an old identifier to a new target.

### Incremental reuse

Reuse previous file rows only when the raw file hash and complete extraction identity match. Identity includes syntax schema/extractor version, Tree-sitter runtime version, grammar distribution/version, and relevant extraction/admission policy. Changed identity forces re-extraction, even if the source is unchanged. Manifest comparison removes deleted, newly unsupported, or newly excluded file rows.

No serialized Tree-sitter trees or long-lived AST cache are introduced. Within a run, share captured bytes and parsed results with semantic chunking where both paths process the same file; do not retain every AST in memory merely to share work.

## 5. One immutable navigation snapshot — TSI-04

### Storage model

Reuse `config.index_dir` and the existing read-only connection cache. The legacy `scip/_/<slug>/_/` directory name remains an internal storage convention; renaming it adds no user value.

Each new snapshot contains namespaced Jarvis-owned tables:

- `syntax_files`: paths, raw hashes, parser identity, parse outcome/reason, and per-file provider coverage.
- `syntax_symbols`: declaration records from section 4, indexed for file outlines, exact identifiers, names, and qualified-name lookup.
- `jarvis_snapshot`: format/generation identifiers, source-manifest identity, and the actual syntax/SCIP capability facts for this generation.

If enrichment succeeds, preserve the genuine converter-produced SCIP tables and occurrence blobs unchanged alongside those tables. Syntax-only snapshots have no fabricated SCIP documents, mentions, relationships, or full symbol strings. Detect capabilities before issuing provider-specific SQL; a missing optional provider is not a missing SQL-table exception to catch after the fact.

A single QueryService operation acquires one snapshot connection and uses it for resolution, locations, and capability/provenance reporting. Do not resolve a symbol on one generation and fetch its location on a newly published generation.

### Artifact names and publication

Use `index-<commit>-<generation>.db`, where generation is a collision-resistant unique run identifier, even for repeated indexing at one commit. The metadata file is the database filename with `.db` replaced by `.metadata.json`. Both the writer and cleanup derive the sibling name from the actual filename, not from commit SHA alone.

Build everything in scratch storage, finish/close the SQLite database, place the database and metadata under their final unique names, and flip `current` using write-temp plus `os.replace`. Never overwrite a published database or metadata file. Keep cache invalidation keyed on pointer content, not mtime. Retire obsolete versioned navigation artifacts only after the new pointer is live.

A syntax-only generation also has a `current` pointer. Pointer existence means a navigation snapshot exists; provider/tool capabilities come from the snapshot. Never copy old SCIP data into a new syntax-only generation.

### Pipeline order and failure boundaries

1. Validate Git input, slug/path ownership, persisted configuration, and required baseline dependencies. Do not require SCIP here.
2. Capture tracked input, extract or reuse syntax declarations, and build a scratch syntax snapshot.
3. Attempt SCIP if enabled and supported, subject only to the watch-specific suppression in section 8. Check SCIP version/toolchain and invoke the language indexer inside this optional boundary. Convert accepted output to a scratch database and combine with the syntax tables.
4. Build Zoekt with the existing `zoekt.name` pin and `zoekt-git-index` command. Zoekt failure fails the run.
5. Run optional semantic indexing with its existing warning/nonfatal behavior. Reuse the common loader; keep the embedding-table identity invariant.
6. Before navigation publication, revalidate captured source and update/clear the repository's outgoing graph edges according to the accepted SCIP data. Graph/SQLite failures are storage failures, not permission to report successful degradation.
7. Publish the navigation snapshot; then record final run state and stage details. Registry failure after pointer publication returns nonzero and explicitly reports that the navigation snapshot is already live. Never enter a degradation/retirement path after publication.

Known optional SCIP failures include absent executables, incompatible supported-tool versions, language build failures, empty/invalid indexer output rejected as an indexing error, and converter command failure. Unexpected Jarvis programming errors, permission errors in shared storage, and corrupt live snapshots remain hard errors. Narrow typed stage boundaries must prevent a broad catch from treating arbitrary infrastructure bugs as expected enrichment failures.

When accepting a generation without usable SCIP package data, clear outgoing edges for every package previously owned by that repo. Leave package identities needed by other repositories intact. Do not synthesize dependency edges from Tree-sitter. Preserve rebuild-not-accumulate and the ordering of failure-prone graph work before navigation publication.

### Atomicity limits

The single pointer guarantees atomic selection of the navigation snapshot. Zoekt, LanceDB, and `registry.db` retain their existing independent storage behavior; this design does not promise a transaction over all stores. A failure after a Zoekt publish can leave newer search shards and an older navigation snapshot. Report what actually published rather than claiming that nothing changed. Freshness/capability reporting must not claim working-tree equivalence from an unchanged Git SHA alone.

## 6. Existing tool routing and output contract — TSI-05

### Coverage precedence

SCIP document presence alone does not establish usable coverage. Determine outline/definition coverage from actual usable definition ranges/occurrences in that file. Empty chunks, empty mentions, or a metadata-only symbol table do not suppress syntax fallback. Record these capability distinctions during snapshot construction rather than treating every file in `documents` as semantically complete.

For a covered file, SCIP remains authoritative for that operation; do not merge an extra syntax outline into it. For an uncovered file, use syntax. This is an observed-data routing rule, not a claim that SCIP has proven complete semantic coverage of every construct in the file.

### Tool behavior

| Tool | Contract |
|---|---|
| `documentSymbols` | Return the SCIP outline when usable for the file; otherwise return syntax declarations in source order, including nested named declarations with scope information. |
| `goToDefinition` | Resolve full SCIP identifiers only through SCIP, opaque syntax identifiers only through syntax, and bare/qualified names through the combined candidate rule below. Return identifier locations for syntax definitions. |
| `findReferences` | Require SCIP occurrence capability. Never implement lexical references or use syntax name matches as references. |
| `callHierarchy` | Require relevant SCIP occurrence/enclosing-range capability. No syntax-derived call edges. |
| `typeHierarchy` | Require actual SCIP relationship capability. Preserve the distinction between absent relationship data and a known empty hierarchy. |
| `getIndexStatus` | Separate latest run outcome from live snapshot capabilities; show syntax coverage and per-tool provider availability. |
| `searchCode` | Remain Zoekt-backed; Tree-sitter does not replace ctags/`sym:` behavior. |
| `semanticSearch` | Preserve vector/Zoekt/SCIP-symbol fusion. Syntax-only snapshots do not masquerade as SCIP inputs to `symbol_search`; a new syntax ranking signal is not part of this feature. |
| `blastRadius` | Remain based on actual stored package edges; no inferred syntax graph. |

Name resolution preserves case-sensitive bare-name and dotted-suffix conventions. Collect genuine SCIP definition candidates plus syntax candidates from files without usable SCIP definition coverage. Group multiple locations for one genuine SCIP symbol as one candidate; do not choose a winner among different declarations merely because one provider is SCIP. Prefer SCIP only when source identity proves both records describe the same declaration. Do not deduplicate uncertain matches by name alone or compare raw columns from unknown encodings as if they were equivalent.

Zero candidates produces a not-found result qualified by searched coverage; multiple distinct candidates use the existing structured ambiguity response. A syntax identifier referring to a changed/deleted declaration returns not found, never a best-effort name redirect. Full SCIP identifiers do not fall back to syntax name matching when unavailable. Passing a syntax identifier to references or hierarchy tools returns an explicit unsupported-identifier/capability error even when other files have SCIP coverage; never reinterpret it as a semantic symbol.

### Additive result fields

Preserve existing top-level fields such as `path`, `symbols`, `symbol`, `resolvedSymbol`, `definitions`, `candidates`, and `candidateTotal`.

- Declaration, definition, and ambiguity-candidate entries carry `source: "scip" | "tree-sitter"`.
- Syntax entries carry `positionEncoding: "utf-8"`, `qualifiedName`, and `parentSymbol` (nullable). Syntax outline entries use `range` for the declaration range and `selectionRange` for the identifier range; existing SCIP range semantics remain unchanged.
- Syntax `symbol`/`resolvedSymbol` values use the opaque identifier defined in section 4. Ambiguity candidates retain `dottedPath` and gain a location so same-named declarations in different files can be distinguished.
- Syntax-backed responses include a `coverage` object: `state`, `reason`, and relevant file counts. States are `complete`, `partial`, `unsupported`, or `not-indexed`. Completeness is relative to the declared supported/tracked-file scope, never all source in the repository.

A parsed zero-declaration file returns `symbols: []` with complete syntax coverage. An unsupported file returns an error with coverage unsupported; an uncaptured/untracked path is not-indexed. A supported skipped/undecodable file returns an error with partial coverage and the recorded reason, not a successful empty outline. Partially parsed files return surviving declarations with partial coverage. Repository-wide syntax name lookup reports partial coverage if any eligible file failed, was size-skipped, or was only partially parsed.

Unavailable precise tools return the established `error` shape plus `requiredCapability`, `reason`, and `recovery`. They must not return an empty array that implies an exhaustive search found no references or relationships. Corrupt snapshots, malformed SQL, and unexpected query failures return errors; fallback handles missing coverage, not arbitrary exceptions. The MCP boundary still catches broad exceptions to keep stdio alive.

## 7. Registry and status schema — TSI-06

### Overall run vocabulary

| `status` | Meaning | Exit |
|---|---|---|
| `indexing` | Current attempt is running; live capabilities still come from the last published snapshot. | Not final |
| `indexed` | Baseline complete; SCIP usable, explicitly disabled, or unsupported for the selected language. | 0 |
| `partial` | Successfully published, with documented syntax/SCIP extraction gaps. | 0 |
| `degraded` | Baseline published, but enabled SCIP failed, is unavailable, or remains suppressed after a known failure. | 0 |
| `failed` | Required stage, storage, publication, or bookkeeping failed. A prior/newly published snapshot may still be live. | Nonzero |

Priority is `failed` then `degraded` then `partial` then `indexed`. Unsupported extensions alone do not make a run partial. A supported size-skipped/undecodable/partially parsed file does. Optional semantic failure retains its current nonfatal behavior and is reported through semantic capability, not mislabeled as a SCIP failure. Stop writing `search-only` as a new run outcome.

### Persisted stage state

Add `scip_enabled` (boolean, default true) as the reversible user choice. Add `scip_state` with `available`, `partial`, `failed`, `unavailable`, `unsupported`, `disabled`, or `unknown` (for historical data with insufficient evidence). Watch suppression is an action on a prior failure, not a new capability state.

Persist `scip_failure_reason`, `scip_failure_stderr`, and `scip_failed_at_sha` separately from the existing overall `status_origin`, `status_reason`, and `status_stderr`. Expected build/conversion failure is `failed`; absent/incompatible tooling is `unavailable`; a language with no SCIP indexer is `unsupported`. Validate the persisted vocabulary on read/write without discarding unknown historical failure text.

A baseline refresh that suppresses SCIP preserves the last SCIP failure fields and state. Successful SCIP acceptance clears them. Explicit disable or an unsupported selected language clears active failure/suppression state because no enrichment attempt is currently expected. Transition to `indexing` must not erase prior failure fields before the stage decision reads them.

For an exit-0 degraded run, `status_reason` mirrors the concise stage cause so `jarvis list`, `status`, and `last_index_run` remain informative; full SCIP stderr remains in the stage field. For a hard run failure, the existing overall fields describe that failure independently. Recovery commands are derived by `recovery_for`, not persisted prose recipes.

### Live capabilities

Retain `last_index_run` with `outcome`, `origin`, `reason`, and `recovery`. Extend `capabilities.navigation` rather than introduce a parallel status endpoint: retain its `available`, `reason`, and `recovery` fields; add `tools` keyed by the five navigation tool names. Each tool has `available`, `providers`, `reason`, and `recovery`, derived from the captured snapshot. Repository-wide availability does not promise file-level coverage.

Add `capabilities.syntax` with availability, parsed/partial/failed/skipped/unsupported file counts and extraction identity. Existing `capabilities.search`, `capabilities.semantic`, and `searchCoverage` keep their meanings. Report the selected snapshot's generation, commit, and publication time separately from latest-run failure state. Continue best-effort null-plus-reason behavior if status derivation fails; never start Zoekt or download/load grammars merely to answer status.

## 8. CLI and watch policy — TSI-07

Expose mutually exclusive `--scip` and `--no-scip` on `index`, `reindex`, and `watch`. Omitted means use the persisted choice, defaulting to enabled for a new repo. Explicit flags update that choice. A watch-suppressed attempt never persists disabled.

`--language` continues selecting only the SCIP enrichment language, using the existing explicit override and detection priority. It must not restrict syntax to that language. With no SCIP-supported language, publish the baseline and record SCIP unsupported without requiring `--no-scip`. An invalid explicit language/configuration remains a user-input error, not an optional indexer failure. `--scheme` remains a SCIP setting.

Remove `--search-only`, `--fallback-search-only`, `--no-fallback-search-only`, and `JARVIS_FALLBACK_SEARCH_ONLY`, their resolvers, obsolete exception/degradation branches, and runtime aliases. Old CLI options are rejected rather than silently mapped; release documentation gives the replacement. Removed environment configuration no longer controls behavior. Keep the existing stdout success line `indexed <slug>`; diagnostics and optional-stage reasons go to stderr.

### Per-stage watch predicate

Skip a SCIP attempt only when all of these hold:

1. The caller is watch, not explicit index/reindex.
2. SCIP is enabled and has a supported selected language.
3. The persisted stage state is failed/unavailable and `scip_failed_at_sha` equals current HEAD.
4. No explicit SCIP re-enable or change to effective SCIP language/scheme invalidated the suppression record.

This decision skips only SCIP. Every debounced event still runs the baseline, optional semantic stage, and publication. A new commit or explicit index/reindex attempts SCIP again; an explicit re-enable clears suppression. Uncommitted edits at the same HEAD do not by themselves retry a known-failing compiler stage, preserving the existing anti-repeat policy. The diagnostic must say that syntax/search were refreshed and how to force a SCIP retry.

## 9. Migration and compatibility — TSI-08

Perform an idempotent, transactional registry migration using the project's SQLite conventions. Copy facts before removing obsolete columns; preserve repo paths, schemes, explicit language choices, embedding settings, tracked counts, and failure evidence.

- Historical `search_only=true` with manual or unknown origin becomes `scip_enabled=false`.
- Historical automatically classified/signature fallback becomes enabled, with the failure copied to stage fields and a retry permitted on the next explicit run.
- Historical degraded state remains enabled; copy its known failed commit/cause into stage state.
- Drop obsolete `search_only` and `fallback_enabled` storage only after their needed data is migrated. Remove their runtime readers/writers and old environment resolution; do not keep dual state models.
- A historical search-only row must not be rewritten into a claim that a syntax snapshot exists. Map its outcome to the new vocabulary according to intentional disable versus failed enrichment, but derive `indexed` and live capabilities from actual artifacts. Until reindex, a pointerless historical row still has no navigation snapshot and reports the reindex recovery.
- Existing immutable SCIP snapshots remain readable as the legacy on-disk format with only their actual SCIP capabilities. Reindex publishes a new-format snapshot; never modify a published legacy database. Legacy-format detection is data compatibility, not a second new writer path.
- Refresh `forget` for all new artifacts and settings while preserving Zoekt unpinning and package-edge teardown.

A new syntax-only publication must replace rather than carry forward old SCIP tables and must clear obsolete outgoing package edges. This also applies when the prior generation had working SCIP navigation. A failure before pointer replacement leaves the old navigation snapshot live; a failure afterward never retires the newly published snapshot.

## 10. Shared chunking and component boundaries — TSI-09

Use small, synchronous modules with frozen result dataclasses, lazy grammar imports, and parameterized SQLite. Do not add async or a general parser-backend plugin framework.

| Component | Responsibility and boundary |
|---|---|
| `syntax.py` (new) | Curated package mapping, parse worker ownership, byte-safe extraction, declaration value objects, and grammar/extractor identity. No registry access. |
| `syntax_index.py` (new) | Build/carry forward namespaced tables; file and name queries over a supplied connection. No CLI or MCP transport. |
| `index_cli.py` | Required/optional stage orchestration, explicit settings, failure classification, and publication using existing path conventions. Remove obsolete branches rather than layering a second pipeline beside them. |
| `index_reader.py` / `config.py` | One-pointer immutable snapshot access, generation identity, metadata naming, and cache lifecycle. |
| `query.py` / `models.py` | Provider capability routing and combined name resolution, one captured snapshot per operation, provenance-aware results. |
| `registry.py` | Settings/stage outcome migration, latest-run state, and derived recovery. Not the symbol store. |
| `server.py` | Existing nine tool wrappers and honest serialization/error/capability output. No extraction logic. |
| `chunker.py` / `semantic.py` | Consume the common grammar provider/captured parse where applicable; preserve embedding-specific admission, bounded chunks, and reuse rules. |
| `graph.py` / `symbol_search.py` | Consume real SCIP data only; no fake syntax occurrences or dependency edges. |

Preserve semantic chunking's existing function/class behavior for its currently structured languages and fixed windows for languages without a structural chunk mapping. Adding syntax declarations does not automatically change the embedding chunk format for every language. The extended syntax extension table does not silently broaden `--semantic-include` or the existing semantic admission set.

Fix byte/string slicing at the shared boundary and adapt grammar-specific name extraction to the selected distributions. If the resulting chunk contents/boundaries change, bump `CONTENT_FORMAT` so unchanged files do not carry old-format embeddings forward. Preserve one-model-per-table identity; no cross-model vector reuse. Do not broadly catch provider installation/ABI failures as ordinary unparseable source.

## 11. Validation and acceptance — TSI-10

Implementation is complete only when the real baseline and existing tool surfaces exercise the approved behavior. Do not substitute mocked parser output or source-text assertions for parsing tests.

| Acceptance | Required evidence |
|---|---|
| Offline base installation | Install without semantic/watch extras using prebuilt dependencies, deny network access during indexing, remove SCIP binaries from the test PATH, index a committed fixture, and call real `documentSymbols`/`goToDefinition`. Baseline succeeds without grammar downloads. |
| Language support | Real parse/extract fixture for every table entry, including both TypeScript and TSX, JSX, Kotlin/Swift, Bash, and SQL. Verify names, scopes, and identifier/declaration ranges. |
| Mixed provider routing | One fixture with usable SCIP-covered files plus syntax-only files. Verify full identifiers, name ambiguity across providers/files, exact-declaration preference, opaque syntax ID round trips, and no false references/hierarchies. |
| Coverage truth | Valid empty outline differs from unsupported/skipped/invalid-UTF-8/partial source. Partial files preserve valid declarations and disclose incomplete coverage. |
| Unicode safety | Multibyte characters before and within declarations, CRLF input, and end-exclusive ranges select the intended bytes/text. Existing zero-based nav and one-based chunk contracts remain intact. |
| Incremental data | Unchanged file identity reuses rows; grammar/extractor identity changes force extraction; changed/deleted files and removed declarations cannot survive through old rows or opaque IDs. |
| Lifecycle | Missing/incompatible SCIP, build failure, later recovery, supported versus unsupported language, disable/re-enable, and hard baseline failure produce the specified status, exit code, cause, and recovery. |
| Watch | Same-commit repeated SCIP failure skips only enrichment; syntax changes still publish. New commit, explicit reindex, and settings changes retry correctly. |
| Snapshot safety | Same-commit reindex uses different immutable filenames and invalidates readers by pointer content; metadata uses the identical generation stem; a single tool operation cannot mix generations. |
| Failure ordering | Inject failure before and after pointer replacement. Keep the prior navigation snapshot when appropriate, do not destroy a new live snapshot, and accurately report any already-published search data. |
| Migration | Cover manual/unknown-origin opt-outs, signature fallback, degraded/failed rows, absent legacy pointers, and existing immutable SCIP snapshots. Repeat migration without changing facts. |
| Graph | Enrichment replaces outgoing edges; syntax-only replacement clears them without deleting other repositories' package identities/edges. |
| Semantic integration | Existing chunk limits/context and optional-install behavior survive; no language-pack fallback or runtime network request; changed chunk format invalidates old rows. |
| Packaging | Wheel-only resolution for CPython 3.12/3.13/3.14 on Linux/macOS x86-64/ARM64, grammar/runtime ABI checks, compiled-wheel content guard, and real parser coverage in the installed-wheel test job. |

Use existing pytest mirrors and `JARVIS_DATA_DIR` isolation. Keep regression tests for plausible behavioral failures above; update tests whose old public contract is intentionally superseded. Remove incidental wording/wiring assertions rather than repinning them. External subprocess/network boundaries can be mocked in unit cases; real Git fixtures and real grammars must defend their own contracts. Run the unit CI gate and focused real CLI/MCP smoke scenarios after implementation. No build/runtime suite was run merely to author this specification.

## 12. Supersession and retained invariants — TSI-11

This specification supersedes only the following conflicting decisions. Historical specs remain history; implementation comments must point here instead of retaining contradictory policy comments.

| Existing decision | Disposition and replacement |
|---|---|
| `FALL-01`: opt-in degradation after a build starts | Superseded by TSI-01/TSI-04: baseline publication is default; expected SCIP failure is isolated as optional enrichment. No broad whole-pipeline degrade catch. |
| `FALL-02`: CLI > persisted fallback > environment > off | Removed by TSI-07/TSI-08. No opt-in fallback switch or fallback-enabled state; only persisted SCIP enable/disable. |
| `FALL-03`: explicit indexing retries, never permanently disabled by failure | Principle retained and extended by TSI-06/TSI-07. Explicit runs retry; failure never changes user enablement. |
| `FALL-04`: missing binaries/bash-shim failures always hard | Superseded only for optional SCIP tooling by TSI-04/TSI-06: usable baseline plus unavailable SCIP is exit-0 degraded with cause/recovery. Missing Zoekt/required grammars and storage failures remain hard. Post-publication failures still cannot degrade/retire a live snapshot. |
| `FALL-05`: same-SHA watch suppression skips the whole run | Superseded by TSI-07's stage-only predicate and `scip_failed_at_sha`; baseline refresh is never suppressed by failed enrichment. |
| `search_only` persistence and `D-10` forget-to-re-enable escape | Superseded by reversible `scip_enabled` and transactional migration in TSI-07/TSI-08. No forget/reindex escape is required to re-enable SCIP. |
| `SEARCH_ONLY_STATUS` and no-pointer search-only publication | Superseded by TSI-04/TSI-06. New baseline generations always publish a navigation pointer; capability-specific errors replace the old blanket no-navigation explanation. |
| `PARTIAL_STATUS` meaning only zero SCIP chunks/mentions | Broadened by TSI-06 to documented syntax or SCIP coverage gaps, with provider-specific facts. The string `partial` remains. |
| `D-04` overall success clearing failure fields | Narrowed by TSI-06: successful baseline bookkeeping cannot erase an active separate SCIP failure. Successful enrichment clears its own failure fields. |
| `D-07`/`D-15`: live artifacts versus last-run truth | Retained. TSI-05/TSI-06 replace pointer-implies-all-navigation with snapshot-derived per-tool capability while keeping the two layers separate. |
| `D-09`: recovery is derived, not stored prose | Retained; recovery maps to the new reversible CLI and stage states. |
| `WR-02`: failure-prone graph retirement before destructive navigation teardown | Principle retained by TSI-04/TSI-08. New baseline publication replaces the snapshot rather than deleting its whole directory; graph/storage errors cannot be disguised as success. |

Retain immutable published databases, pointer-content cache invalidation, role-bitmask filtering, decoder import isolation, bare scip-swift invocation, fixed single-tenant project/branch pins, rebuild-not-accumulate graph behavior, and one embedding identity per LanceDB table. No unrelated invariants are relaxed.

## 13. Documentation, alternatives, and review gate — TSI-12

Implementation must update `README.md`, `docs/system-architecture.md`, `docs/codebase-summary.md`, `docs/code-standards.md`, relevant CLI/setup guidance, and the changelog where they describe prerequisites, language support, publication, status, or removed flags. Preserve the README MCP-name marker and do not rewrite historical design documents as if their old decisions had never existed. Update source decision comments and setup/package messaging alongside behavior.

Alternatives rejected during design:

- Separate syntax/SCIP snapshots: needs a shared-generation manifest and more publication/reader machinery to prevent stale-provider mixing.
- Syntax in mutable `registry.db`: mixes high-volume index data and operational state and abandons the existing immutable-reader model.
- On-demand language-pack provisioning: smaller initial loader install but introduces grammar cache/download lifecycle and offline cold-start failures.
- SCIP replacement or separate syntax MCP tool: respectively loses semantic guarantees or requires agents to discover a second tool surface.

This specification does not authorize unrelated asset cleanup. The implementation plan is a separate, user-gated deliverable.

After this document is written, committed, and self-reviewed, the user must review the written specification. Only after that explicit approval may the `writing-plans` workflow create the implementation plan.

### Primary references

- [Tree-sitter advanced parsing](https://tree-sitter.github.io/tree-sitter/using-parsers/3-advanced-parsing.html)
- [Pinned language-pack release and download behavior](https://github.com/xberg-io/tree-sitter-language-pack/tree/v1.13.6)
- [Pinned language-pack parser manifest](https://github.com/xberg-io/tree-sitter-language-pack/releases/download/v1.13.6/parsers.json)
- [TypeScript package and TSX factory](https://pypi.org/project/tree-sitter-typescript/)
- [Kotlin grammar distribution](https://pypi.org/project/tree-sitter-kotlin/)
- [Swift grammar distribution](https://pypi.org/project/tree-sitter-swift/)
- [Existing SCIP-boosted retrieval contract](2026-08-05-scip-boosted-retrieval-design.md)
- [Existing Java/search-only design, partially superseded here](2026-08-01-scip-java-indexing-design.md)
- [Existing compiled-wheel distribution contract](2026-08-06-compiled-wheel-distribution-design.md)
