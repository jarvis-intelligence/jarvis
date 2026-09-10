# Tree-sitter Syntax Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every indexing run publish a build-free syntax/Zoekt baseline, with optional SCIP enrichment and truthful fallback through the existing MCP tools.

**Architecture:** One immutable SQLite navigation snapshot contains namespaced syntax tables and, when accepted, genuine SCIP tables. A curated offline parser provider serves syntax extraction and semantic chunking; the existing writer owns publication and persisted stage policy. QueryService captures one generation per operation and routes by actual provider coverage.

**Tech Stack:** Python 3.12–3.14, uv, stdlib SQLite, Tree-sitter 0.26.0 with individual grammar wheels, existing SCIP/Zoekt binaries, pytest, FastMCP, optional LanceDB/sentence-transformers/watchdog.

## Global Constraints

- Specification: [approved Tree-sitter indexing design](../specs/2026-09-09-tree-sitter-indexing-design.md), decisions `TSI-01` through `TSI-12`. This plan implements that spec, not an alternative architecture.
- `requires-python = ">=3.12,<3.15"`; release coverage is CPython 3.12/3.13/3.14 × Linux/macOS × x86-64/ARM64.
- Initial runtime: `tree-sitter==0.26.0`. Grammar versions are copied exactly into Task 1; validate their wheels and ABI before committing dependencies.
- “Base requirements include `tree-sitter` and the 16 grammar distributions below. The `semantic` extra retains LanceDB and sentence-transformers, not a second grammar provider.”
- “No runtime downloads, repository grammar compilation, or fallback to `tree-sitter-language-pack` are allowed.”
- “No serialized Tree-sitter trees or long-lived AST cache are introduced.”
- Navigation syntax ranges use zero-based lines and UTF-8 byte columns; embedding/search lines remain one-based. Preserve the actual existing SCIP coordinate contract rather than relabeling it.
- Preserve immutable published files, pointer-content cache keys, role-bitmask filtering, decoder import isolation, bare scip-swift invocation, `PROJECT = BRANCH = "_"`, graph edge retraction, and one embedding identity per table.
- Use frozen value objects, synchronous APIs, parameterized SQL, deferred grammar imports, and bounded per-worker parser reuse. No async indexing or generic plugin framework.
- Required parser/Zoekt/storage failure is nonzero. Expected optional SCIP failure after a valid baseline yields exit 0 and `degraded`; stdout remains `indexed <slug>`, diagnostics go to stderr.
- No new MCP tool, syntax reference/call/type/package inference, or syntax RRF signal. `--semantic-include` does not change the Git-tracked syntax boundary.
- A snapshot generation is unique even at the same Git commit. JSON metadata uses the same filename stem as its database.
- No cross-store transaction is promised: report search already published if a later navigation stage fails.
- Every stateful test sets `JARVIS_DATA_DIR` before constructing config/registry objects. Use real grammar/Git behavior; mock external boundaries only where that boundary is not the contract under test.
- The current untracked `diagrams/` directory is unrelated user work. Never stage it; stage explicit task paths. Do not modify `.planning/`, release versions, or external plugin repositories.
- Source reads during planning found only Ruff LSP support, without definition/references/rename providers. Recheck available LSP at execution; use symbol references when supported, otherwise inspect all text callsites of changed exported APIs.

---

## Execution shape and file ownership

Execute the seven tasks in order by default. Task 3 and Task 4 share only the completed syntax interfaces and may be researched concurrently, but do not run builds/tests while another agent is mutating the tree. The orchestration owner serializes shared-file changes and runs validation after each integrated task. Never merge or ship an intermediate task as the finished feature.

| Task | Independently reviewable deliverable | Spec sections |
|---|---|---|
| 1 | Offline curated grammar provider and byte-safe existing chunking | TSI-02, TSI-03, TSI-09 |
| 2 | Real declaration extraction for all 17 parser selections | TSI-02, TSI-03, TSI-05 |
| 3 | Captured-source manifest, immutable syntax store, and provider facts | TSI-03, TSI-04, TSI-05 |
| 4 | One-pass parse sharing with optional semantic preparation | TSI-03, TSI-09 |
| 5 | Existing MCP tools route one captured generation with truthful coverage and provenance | TSI-05, TSI-06, TSI-09 |
| 6 | Registry migration, one staged writer pipeline, reversible CLI/watch policy | TSI-01, TSI-04, TSI-06, TSI-07, TSI-08, TSI-11 |
| 7 | Offline CLI/MCP/release evidence and truthful published documentation | TSI-10, TSI-11, TSI-12 |

| File | Responsibility |
|---|---|
| Create `src/jarvis/syntax.py` | Grammar factories, parser pool, byte spans, syntax identities and extraction. No database/registry access. |
| Create `src/jarvis/syntax_index.py` | Source capture/revalidation, namespaced tables, syntax queries and snapshot/provider facts. No MCP transport or user settings. |
| Modify `src/jarvis/chunker.py` | Shared provider, byte-safe slicing, optional supplied tree; preserve semantic admission and chunk bounds. |
| Modify `src/jarvis/semantic.py` | Separate input/reuse preparation from embedding/write completion so already-parsed trees can supply chunks. Preserve RRF and table identity. |
| Modify `src/jarvis/registry.py` | Transactional migration, SCIP intent/outcome, independent overall failure state, recovery. |
| Modify `src/jarvis/index_cli.py` | Replace three writer paths with one pipeline; stage guards, unique publication, CLI/watch/forget. |
| Modify `src/jarvis/config.py` | Remove obsolete fallback env policy; retain path/cache conventions. |
| Modify `src/jarvis/index_reader.py` | Extend immutable metadata fields and preserve legacy read compatibility; do not add a second pointer. |
| Modify `src/jarvis/query.py`, `models.py`, `symbols.py`, `server.py` | Provider-aware result objects, candidate resolution, capability errors, status serialization. |
| Modify `src/jarvis/graph.py` | Reusable clearing of all repo-owned outgoing edges without deleting package identities. |
| Modify `pyproject.toml`, `uv.lock` | Base parser dependencies and installed-wheel grammar test coverage. |
| Create `tests/test_syntax.py`, `tests/test_syntax_index.py`, `tests/test_syntax_integration.py` | Grammar, storage/reuse, and real baseline transport regressions. |
| Create `tests/fixtures/syntax_cases.py` | Named-language source fixtures and expected declarations; not mocked parser output. |
| Modify matching existing tests | Migrate changed public contracts; preserve real failure stories, remove wording/wiring-only assertions. |
| Modify setup/release smoke workflows and current docs | Baseline versus enrichment prerequisites and all required release target checks, after local smoke proof. |

The new modules are not packages or frameworks. Keep SQL in `syntax_index.py`, node traversal in `syntax.py`, and orchestration in the existing CLI. Task-local private helpers may be added only to implement the named public contracts below.

## Verification discipline

Each task provides a red case, the implementation recipe, a focused green command, and a scoped commit. Expected outputs below are execution expectations, not claims that tests have run while planning. A missing import is an acceptable initial red signal for a new API; before completing the task, tests must fail on the actual plausible behavior regression, not just API absence.

The final task runs the project gate once. Do not run project-wide suites or formatters in research/parallel workers. No formatter is configured for this repository; match surrounding style rather than introducing one. Keep tests only for observable contracts, boundaries, transitions, errors, or data safety. Do not assert source text, private helper disappearance, forwarding arguments, or exact diagnostic prose.

## Task 1: Install an offline parser provider and repair byte-safe chunking

**Files:**
- Create: `src/jarvis/syntax.py`, `tests/test_syntax.py`.
- Modify: `pyproject.toml:52-73,125-131`, `src/jarvis/chunker.py:80-92,230-244,319-357,385-409`, `tests/test_chunker.py:1-8` and relevant parsing cases.
- Regenerate: `uv.lock` with uv only.

**Interfaces:**
- Produces `SyntaxDependencyError(RuntimeError)` in `syntax.py`.
- Produces `ParserPool.parse(language: str, source: bytes) -> Tree`, `grammar_identity(language: str) -> str`, and `slice_text(source: bytes, start: int, end: int) -> str`.
- Changes `chunk_file(rel_path: str, source: str, file_hash: str, language: str, *, tree: Tree | None = None, pool: ParserPool | None = None) -> list[Chunk]`. Existing callers remain valid by omission; a supplied tree must be used rather than reparsed.
- `Tree` is `tree_sitter.Tree`, imported under `TYPE_CHECKING` for annotations; native imports occur only when a grammar is actually needed. The pool is owned by one indexing worker, never a process-global mutable parser cache.

- [ ] **Step 1: Add the dependency/Unicode regression before changing the provider.**

Put the following in `tests/test_syntax.py`; existing chunker tests use the current `Chunk` API:

```python
from __future__ import annotations

from jarvis.chunker import chunk_file
from jarvis.syntax import ParserPool


def test_python_tree_and_chunk_preserve_multibyte_source():
    source = "# café\ndef greet():\n    return '你好'\n"
    raw = source.encode("utf-8")
    tree = ParserPool().parse("python", raw)
    chunks = chunk_file("greet.py", source, "hash", "python", tree=tree)
    assert [(c.start_line, c.end_line, c.symbol_name) for c in chunks] == [(2, 3, "greet")]
    assert chunks[0].content.endswith("def greet():\n    return '你好'")
    assert "é\ndef" not in chunks[0].content
```

Run `uv run pytest tests/test_syntax.py -q`. Initially expect the new provider import to fail. After the provider exists but before byte slicing is fixed, the selected text assertion must expose the old byte-offset/string-index defect.

- [ ] **Step 2: Replace the language-pack extra with these exact candidate base dependencies.**

Keep existing base dependencies. Move none of the embedding packages into base. Add this list to `[project].dependencies`, remove both parser lines from the semantic extra, and update the compiled-wheel comment so grammar tests are no longer described as skipped:

```toml
"tree-sitter==0.26.0",
"tree-sitter-python==0.25.0",
"tree-sitter-javascript==0.25.0",
"tree-sitter-typescript==0.23.2",
"tree-sitter-java==0.23.5",
"tree-sitter-kotlin==1.1.0",
"tree-sitter-swift==0.7.3",
"tree-sitter-go==0.25.0",
"tree-sitter-ruby==0.23.1",
"tree-sitter-rust==0.24.2",
"tree-sitter-c==0.24.2",
"tree-sitter-cpp==0.23.4",
"tree-sitter-c-sharp==0.23.5",
"tree-sitter-php==0.24.1",
"tree-sitter-scala==0.26.2",
"tree-sitter-bash==0.25.1",
"tree-sitter-sql==0.3.11",
```

Run `uv lock`, then wheel-only resolution for the Cartesian product below. A Python one-shot driver can invoke the verified uv flags without shell loops:

```python
from itertools import product
from subprocess import run
from tempfile import TemporaryDirectory
from pathlib import Path

platforms = (
    "aarch64-apple-darwin", "x86_64-apple-darwin",
    "aarch64-manylinux_2_28", "x86_64-manylinux_2_28",
)
with TemporaryDirectory(prefix="jarvis-wheel-resolution-") as directory:
    for version, platform in product(("3.12", "3.13", "3.14"), platforms):
        output = Path(directory) / f"{version}-{platform}.txt"
        run([
            "uv", "pip", "compile", "pyproject.toml",
            "--python-version", version, "--python-platform", platform,
            "--only-binary", ":all:", "--output-file", str(output),
        ], check=True)
```

Expected: all 12 resolves succeed without a source build. Do not change the supported-platform floor or silently drop a grammar to bypass a failure. Then `uv sync` installs the base provider in the execution environment.

- [ ] **Step 3: Implement the curated provider using one finite map.**

Use this exact factory mapping; do not instantiate/query any repository-supplied grammar code:

```python
FACTORIES = {
    "python": ("tree-sitter-python", "tree_sitter_python", "language"),
    "javascript": ("tree-sitter-javascript", "tree_sitter_javascript", "language"),
    "typescript": ("tree-sitter-typescript", "tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree-sitter-typescript", "tree_sitter_typescript", "language_tsx"),
    "java": ("tree-sitter-java", "tree_sitter_java", "language"),
    "kotlin": ("tree-sitter-kotlin", "tree_sitter_kotlin", "language"),
    "swift": ("tree-sitter-swift", "tree_sitter_swift", "language"),
    "go": ("tree-sitter-go", "tree_sitter_go", "language"),
    "ruby": ("tree-sitter-ruby", "tree_sitter_ruby", "language"),
    "rust": ("tree-sitter-rust", "tree_sitter_rust", "language"),
    "c": ("tree-sitter-c", "tree_sitter_c", "language"),
    "cpp": ("tree-sitter-cpp", "tree_sitter_cpp", "language"),
    "csharp": ("tree-sitter-c-sharp", "tree_sitter_c_sharp", "language"),
    "php": ("tree-sitter-php", "tree_sitter_php", "language_php"),
    "scala": ("tree-sitter-scala", "tree_sitter_scala", "language"),
    "bash": ("tree-sitter-bash", "tree_sitter_bash", "language"),
    "sql": ("tree-sitter-sql", "tree_sitter_sql", "language"),
}
```

`ParserPool` imports `Language`/`Parser` and the selected module lazily, wraps missing/ABI-invalid factories in `SyntaxDependencyError` with package/version/language context, and stores the successfully constructed parser in an instance dictionary. The core load operation is:

```python
module = importlib.import_module(module_name)
language_object = Language(getattr(module, factory_name)())
parser = Parser(language_object)
```

Catch `ImportError`, metadata package absence, and `TypeError`/`ValueError` from this factory/construction boundary only. Do not wrap the entire caller or storage operation. Unknown internal language names raise a configuration error, not empty parsing output. `grammar_identity` is a deterministic JSON/digest of extractor version, runtime version, distribution/version, and parser selection; do not derive identity from an installed-package directory mtime.

- [ ] **Step 4: Make every AST slice byte-safe and preserve chunk behavior.**

Implement `slice_text` by decoding `source[start:end]`. In `chunk_file`, encode once, use the supplied tree or pool, and pass the same bytes into `_split_class` and every node-range slicing path. Keep `_split_oversized_def` operating on an already correctly decoded string. For a language without `_DEF_NODE_TYPES`, produce the existing windows without constructing an unnecessary parser. Missing provider/ABI errors must propagate; ordinary parsed source with no mapped definitions still uses windows.

Do not remove context headers or change MAX_TOKENS/MIN_TOKENS/overlap. For a node whose end point is column zero on the following line, the inclusive chunk end is that end row; otherwise it is `end_row + 1`. Skip empty spans. Adapt Kotlin names to `identifier`/the `name` field rather than the prior language-pack's positional assumption. Bump `CONTENT_FORMAT` from 1 to 2 because Unicode slicing and end-line boundaries change stored contents/ranges.

- [ ] **Step 5: Prove dependency failures stay explicit and supplied trees do not require a second parse.**

Remove the module-level language-pack `importorskip` from `test_chunker.py`; parser support is base. Add a missing-grammar case using `tests.conftest.BlockImportFinder` with a fresh pool and restored module state, asserting `SyntaxDependencyError`. Keep malformed-source/no-definition window behavior and existing chunk bounds tests. For supplied-tree behavior, block fresh grammar loading after constructing a real tree and verify chunk output remains correct; do not merely assert a mock invocation count.

Run `uv run pytest tests/test_syntax.py tests/test_chunker.py -q`. Also exercise `ParserPool.parse` for a tiny valid source for each factory from Task 2's fixture map; do not commit dependency pins until construction succeeds locally. Record cross-platform execution as a later release gate, not as proven by wheel metadata.

- [ ] **Step 6: Commit the usable offline-provider cutover.**

```sh
git add pyproject.toml uv.lock src/jarvis/syntax.py src/jarvis/chunker.py tests/test_syntax.py tests/test_chunker.py
git commit -m "feat(index): install offline grammar providers and fix byte-safe chunking"
```

## Task 2: Extract actual declarations across all supported grammars

**Files:**
- Modify: `src/jarvis/syntax.py`, `tests/test_syntax.py`.
- Create: `tests/fixtures/syntax_cases.py`.

**Interfaces:**
- Consumes `ParserPool`, `grammar_identity`, `slice_text` from Task 1 and existing `symbols.DescriptorKind`.
- Produces `Span`, `SyntaxSymbol`, `ParsedSyntax` frozen dataclasses shown below.
- Produces `language_for_path(path: str) -> str | None` using the exact spec extension table.
- Produces `extract_file(file_path: str, source: bytes, language: str, *, pool: ParserPool, tree: Tree | None = None) -> ParsedSyntax`.
- `ParsedSyntax.tree` is only an ephemeral parse handle for the current worker/callback, never persisted; invalid UTF-8 returns state `failed`, a reason, `tree=None`, and no symbols. Dependency failures raise.

- [ ] **Step 1: Establish the value objects and a behavioral Unicode/scope test.**

```python
from dataclasses import dataclass
from jarvis.symbols import DescriptorKind


@dataclass(frozen=True)
class Span:
    start_byte: int
    end_byte: int
    start_line: int
    start_character: int
    end_line: int
    end_character: int


@dataclass(frozen=True)
class SyntaxSymbol:
    symbol: str
    name: str
    qualified_name: str
    kind: DescriptorKind
    parent_symbol: str | None
    declaration: Span
    selection: Span


@dataclass(frozen=True)
class ParsedSyntax:
    file_path: str
    file_hash: str
    language: str
    parser_identity: str
    state: str
    reason: str | None
    symbols: tuple[SyntaxSymbol, ...]
    tree: Tree | None
```

Put the real test in `tests/test_syntax.py`:

```python
def test_nested_declarations_keep_byte_locations_and_stable_ids():
    raw = "@dec\nclass Café:\n    def run(self):\n        def inner():\n            pass\n".encode()
    first = extract_file("cafe.py", raw, "python", pool=ParserPool())
    second = extract_file("cafe.py", raw, "python", pool=ParserPool())
    by_name = {s.qualified_name: s for s in first.symbols}
    assert set(by_name) == {"Café", "Café.run", "Café.run.inner"}
    assert raw[by_name["Café"].selection.start_byte:by_name["Café"].selection.end_byte] == "Café".encode()
    assert (by_name["Café"].selection.start_line, by_name["Café"].selection.start_character, by_name["Café"].selection.end_character) == (1, 6, 11)
    assert by_name["Café.run"].parent_symbol == by_name["Café"].symbol
    assert by_name["Café.run.inner"].parent_symbol == by_name["Café.run"].symbol
    assert [s.symbol for s in first.symbols] == [s.symbol for s in second.symbols]
```

Run `uv run pytest tests/test_syntax.py::test_nested_declarations_keep_byte_locations_and_stable_ids -q`; expect failure until extraction exists.

- [ ] **Step 2: Implement a deterministic named-declaration traversal, not a tags-query union.**

Use an explicit stack of node/scope frames to avoid Python recursion limits on deep files. A frame carries the current qualified prefix and nearest emitted parent ID. A recognized node produces one or more declaration candidates; validate the candidate head/name independently from errors in its body. Build the selection span from the actual identifier node. Python decorators and declaration export/template wrappers extend the declaration span but never replace the identifier span. Prevent a wrapped inner declaration from being emitted twice.

The identifier recipe is fully deterministic:

```python
def syntax_id(file_path, file_hash, selection, kind):
    payload = json.dumps(
        [file_path, file_hash, selection.start_byte, selection.end_byte, str(kind)],
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    return "syntax:" + hashlib.sha256(payload).hexdigest()
```

Use `TYPE`, `METHOD`, and `NAMESPACE` from the existing enum for declared types, callable declarations, and modules/namespaces. For an explicitly named alias whose namespace-versus-type target cannot be determined syntactically (C# using aliases), use existing `TERM` instead of falsely asserting a type. Preserve overloads at different selection spans. Do not emit references, enum values, parameters, arbitrary variable assignments, or anonymous expression nodes.

`root.has_error` sets file state partial, not automatic rejection of every child. An error-free identifier/head can survive a broken body. Missing/error identifiers are rejected; invalid UTF-8 is a file outcome, not decoding with replacement into false names.

- [ ] **Step 3: Implement the complete pinned grammar rule table and special adapters.**

The following table is based on pinned upstream source inspection; run it against the actual installed parsers. Tags files are incomplete and sometimes include references, so they are not the authority for the symbol set.

| Parser | Candidate nodes and identifier selection | Required special handling |
|---|---|---|
| python | `class_definition.name`, `function_definition.name`; `type_alias_statement.left` base identifier | `decorated_definition.definition`; direct identifier assignment to `lambda`; no assignment-to-call inference. |
| javascript | class/function/generator declarations and named expressions `.name`; `method_definition.name`; identifier `variable_declarator.name` with function/arrow/generator `.value` | Callable class `field_definition.property` and static object `pair.key`; JSX tags are not declarations. |
| typescript | JS family plus `function_signature`, method/abstract-method signatures; class/abstract-class/interface/type-alias `.name`; enum `.name`; `module`/`internal_module.name` | Class callable property is `public_field_definition.name`, not JS's field node. TS is generated against a different JS grammar revision. |
| tsx | Same pinned TS rules using the distinct TSX factory | Arrow-bound JSX components are callable bindings; JSX elements add no scopes. |
| java | class/interface/enum/record/annotation-type `.name`; method/constructor/compact-constructor/annotation-element `.name`; module `.name` | Explicit package header is unfielded and governs siblings; no module inferred from filesystem. |
| kotlin | class/object/function `.name`; named companion `.name`; `type_alias.type` | Names are `identifier`, not Swift-style simple identifiers. Package header is an unfielded qualified identifier; direct property lambda bindings require descending `variable_declaration`. |
| swift | class node covers class/struct/actor/enum; protocol/typealias/associatedtype `.name`; function/protocol-function `.name` | `declaration_kind` distinguishes extension context, which must not create a new type row. Simple named property with lambda value is callable. |
| go | function/method `.name`; `type_spec`/`type_alias.name`; interface `method_elem.name` | Package header governs siblings. Direct var/short-var function literals require one-to-one syntactic binding. A receiver spelling supplies context, not a link to a resolved type declaration. |
| ruby | class/module `.name`; method/singleton-method `.name`; explicit method alias `.name` | Qualified constant name leaf comes from `scope_resolution.name`; singleton class is context, not a new type. Direct `lambda` value is callable; `proc`/`lambda` calls are not inferred constructors. |
| rust | struct/enum/union/trait/type/associated-type `.name`; function/signature `.name`; module `.name` | `impl_item` has no name; its spelled target supplies context only. Simple let closure bindings are callable. |
| c | function-bearing declarations and definitions via `.declarator`; typedef `.declarator`; named struct/union/enum | Follow declarator chains, not first descendant identifier; distinguish pointer variables and type references from real declarations. |
| cpp | C family plus class, using type alias, namespace and namespace alias | Templates wrap declarations; qualified/operator/destructor names are valid; anonymous namespaces do not gain invented names. |
| csharp | class/struct/enum/interface/record/delegate `.name`; method/constructor/destructor/local-function `.name`; namespace `.name` | File-scoped namespace governs siblings. Callable initializer is an unfielded expression in `variable_declarator`; no type inference for using-alias target. |
| php | class/interface/trait/enum/function/method `.name`; namespace `.name` | Braced versus semicolon namespaces; callable assignment selects the variable's inner name (without `$`), only for direct anonymous-function/arrow values. |
| scala | class/trait/enum/object/package-object `.name`; function definition/declaration `.name`; type definition `.name`; package `.name` | Header-only packages govern siblings. Direct simple val/var lambda values are callable; no arbitrary val/parameter captures. |
| bash | `function_definition.name` is `word` | Both shell function spellings and nested functions; no command names become declarations. |
| sql | immediate `object_reference` of `create_table`, `create_view`, `create_materialized_view`, `create_function`, `create_type`; explicit `create_schema` identifier | Read the CREATE node's direct target, not a referenced object in its body. `function_declaration` is a local SQL variable form, not a function definition. No CREATE PROCEDURE production exists in 0.3.11. |

For C/C++, collect the declarator path from the owning declaration toward its leaf. Ignore parentheses/attributes/qualified-name wrappers when classifying the first derived operation encountered from the identifier outward: function first means callable; pointer/reference/array first means variable. Typedef ownership takes precedence and produces TYPE. Thus `int (*factory(void))(int);` is callable and `int (*slot)(int);` is not. A named type specifier with a body or explicit standalone forward declaration is a TYPE; `struct S *p;` is only a type use.

For callable bindings in all applicable grammars, accept only a syntactically explicit literal function/closure, a simple named target, and an unambiguous name/value pairing. Do not zip a multi-return call into multiple inferred functions.

Resolve scope details now, consistently across fixtures:

- Explicit Java/Kotlin/Go package headers, C# file-scoped namespaces, PHP semicolon namespaces, and unbraced Scala packages create a named scope for following siblings. The header symbol's declaration range remains its actual header; its lexical scope can extend beyond that range. Do not infer headers from paths/imports.
- Rust impl targets, Swift extensions, Go receiver targets, Ruby singleton contexts and out-of-line C++ qualified members may supply source-spelled qualification, but create no duplicate TYPE row. `parent_symbol` refers only to the nearest actual emitted lexical declaration, never a guessed declaration elsewhere.
- Preserve explicit qualified components (`A.B`, `A::B`, SQL schema components) separately from the leaf identifier. Do not infer parent identity from spelling.
- Identifier names preserve source spelling, including quoted/backtick/private/operator forms. Selection spans are the exact source bytes. Dynamic computed names are omitted; a statically spelled literal property can use its literal spelling. No evaluation/unescaping framework is introduced.
- `const publicName = function internalName() {};` records the callable binding and its separately named inner expression at their distinct selection spans; the inner expression is qualified beneath the binding. Anonymous values do not add another symbol.

- [ ] **Step 4: Add the complete cross-language source corpus below.**

Create `tests/fixtures/syntax_cases.py` with `CASES` as a tuple of `(language, path, source, expected_qualified_names)` values. These initial snippets contain no dependency on project toolchains; source analysis is not a claim that they have already executed successfully.

```python
CASES = (
    ("python", "a.py", "class Box:\n    def run(self):\n        return 1\ntype Alias = Box\n", {"Box", "Box.run", "Alias"}),
    ("javascript", "a.jsx", "const View = () => <div/>; class Box { field = () => 1; run() {} }", {"View", "Box", "Box.field", "Box.run"}),
    ("typescript", "a.ts", "namespace N { export interface P { call(): void; } export type Alias = P; }", {"N", "N.P", "N.P.call", "N.Alias"}),
    ("tsx", "a.tsx", "namespace UI { export const View = () => <div/>; }", {"UI", "UI.View"}),
    ("java", "A.java", "class Box { Box() {} void run() {} } record R(int x) {}", {"Box", "Box.Box", "Box.run", "R"}),
    ("kotlin", "a.kt", "class Box { fun run() {} }\ntypealias Alias = Box\n", {"Box", "Box.run", "Alias"}),
    ("swift", "a.swift", "struct Box { func run() {} }\nprotocol P { associatedtype Item; func call() }\ntypealias Alias = Box\n", {"Box", "Box.run", "P", "P.Item", "P.call", "Alias"}),
    ("go", "a.go", "package p\ntype Box struct{}\ntype Alias = Box\ntype P interface { Run() }\nfunc outer() {}\n", {"p", "p.Box", "p.Alias", "p.P", "p.P.Run", "p.outer"}),
    ("ruby", "a.rb", "module N\n class Box\n  def run; end\n  def self.make; end\n end\nend\n", {"N", "N.Box", "N.Box.run", "N.Box.make"}),
    ("rust", "a.rs", "mod n { struct Box; type Alias = Box; trait P { type Item; fn run(&self); } }", {"n", "n.Box", "n.Alias", "n.P", "n.P.Item", "n.P.run"}),
    ("c", "a.c", "struct Box { int value; }; typedef int (*Handler)(void); int (*factory(void))(int); int (*slot)(int);", {"Box", "Handler", "factory"}),
    ("cpp", "a.cpp", "namespace N { using Alias = int; class Box { public: int run(); }; }", {"N", "N.Alias", "N.Box", "N.Box.run"}),
    ("csharp", "a.cs", "namespace N { class Box { void Run() { void Inner() {} } } record R(int X); delegate void D(); }", {"N", "N.Box", "N.Box.Run", "N.Box.Run.Inner", "N.R", "N.D"}),
    ("php", "a.php", "<?php namespace N { class Box { function run() {} } enum E { case A; } }", {"N", "N.Box", "N.Box.run", "N.E"}),
    ("scala", "a.scala", "package n { object Box { type Alias = Int; def run(): Unit = () }; trait P { def call(): Unit } }", {"n", "n.Box", "n.Box.Alias", "n.Box.run", "n.P", "n.P.call"}),
    ("bash", "a.sh", "outer() { function inner { :; }; }\nfunction other() { :; }\n", {"outer", "outer.inner", "other"}),
    ("sql", "a.sql", "CREATE TABLE users (id integer); CREATE VIEW active_users AS SELECT id FROM users; CREATE FUNCTION answer() RETURNS integer LANGUAGE SQL AS 'SELECT 1';", {"users", "active_users", "answer"}),
)
```

Parametrize a real extraction test over `CASES`, assert the complete expected name set, and assert that each selection span decodes to that symbol's `name`. Add targeted TYPE/METHOD/NAMESPACE assertions so a name-only implementation cannot pass. Do not weaken a failing source fixture to a no-error/nonempty assertion; inspect the actual node tree and correct the rule or invalid fixture with evidence.

Add separate cases for invalid UTF-8, valid empty files, a broken function body beside a valid class, CRLF, forward declarations versus type uses, header scopes, quoted/operator names, unnamed companions, extension/impl contexts, and the dual-name function expression. For SQL CREATE PROCEDURE, assert partial coverage and no invented procedure symbol; it is not supported by the selected grammar.

- [ ] **Step 5: Run the complete extraction contract and commit.**

Run `uv run pytest tests/test_syntax.py tests/test_chunker.py -q`. Verify every parser selection appears in the collected cases. Provider shape/ABI failures block the task; no import skips or language-pack fallback.

```sh
git add src/jarvis/syntax.py tests/test_syntax.py tests/fixtures/syntax_cases.py
git commit -m "feat(index): extract scoped syntax declarations across supported languages"
```

## Task 3: Capture source and build immutable syntax/provider snapshots

**Files:**
- Create: `src/jarvis/syntax_index.py`, `tests/test_syntax_index.py`.
- Modify: `src/jarvis/index_reader.py:44-98`, `tests/test_index_reader.py`.
- Reuse: `tests/fixtures/synthetic_index.py`, `scip_decoder` public decoders/role constants, and `config.IGNORED_DIRS`.

**Interfaces:**
- Consumes Task 2's `ParsedSyntax`, `SyntaxSymbol`, `ParserPool`, and `language_for_path`.
- Produces `SourceChangedError(RuntimeError)` and `SourceCaptureError(RuntimeError)`.
- Produces the following frozen values in `syntax_index.py`: `CapturedFile(file_path: str, language: str | None, file_hash: str | None, source_path: Path | None, in_scope: bool, reason: str | None)`, `SourceManifest(files: tuple[CapturedFile, ...], source_hash: str)`, `CoverageCounts(parsed: int, partial: int, failed: int, skipped: int, unsupported: int)`, and `SyntaxBuildReport(counts: CoverageCounts, reused_files: int)`.
- Produces `capture_sources(repo_path: Path, scratch_dir: Path) -> SourceManifest` and `validate_sources(repo_path: Path, manifest: SourceManifest) -> None`.
- Produces `build_syntax_index(db_path: Path, manifest: SourceManifest, *, pool: ParserPool, previous: sqlite3.Connection | None = None, parse_for: frozenset[str] = frozenset(), on_parsed: Callable[[CapturedFile, bytes, Tree], None] | None = None) -> SyntaxBuildReport`.
- Produces `copy_syntax_tables(source_path: Path, target: sqlite3.Connection) -> None` and `finalize_snapshot(conn: sqlite3.Connection, *, generation: str, commit_sha: str, published_at: str, source_hash: str, scip_state: str) -> SnapshotFacts`.
- Produces `read_snapshot_facts(conn: sqlite3.Connection) -> SnapshotFacts`, `file_symbols(conn: sqlite3.Connection, file_path: str) -> tuple[SyntaxSymbol, ...]`, `find_syntax_symbols(conn: sqlite3.Connection, query: str, *, uncovered_only: bool = True) -> tuple[SyntaxSymbol, ...]`, `get_syntax_symbol(conn: sqlite3.Connection, symbol: str) -> SyntaxSymbol | None`, and `file_coverage(conn: sqlite3.Connection, path: str) -> tuple[str, str | None]`.
- `SnapshotFacts` contains `generation: str | None`, `commit_sha: str | None`, `source_hash: str | None`, `scip_state: str`, `syntax_counts: CoverageCounts | None`, and booleans `scip_outlines`, `scip_definitions`, `scip_references`, `scip_calls`, `scip_types`. It represents one immutable generation, not registry last-run state.
- Extend `IndexMetadata` with nullable generation/source_hash fields after existing fields. Old metadata remains readable; do not change the `get_connection` pair return contract in this task.

- [ ] **Step 1: Write a real-Git source capture and invalidation test.**

Use this helper only inside `test_syntax_index.py`; it creates a real repository and never touches the user's data directory:

```python
import subprocess
from pathlib import Path
import pytest

from jarvis.syntax_index import SourceChangedError, capture_sources, validate_sources


def make_repo(root: Path) -> Path:
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    (root / "tracked.py").write_text("def before():\n    return 1\n")
    subprocess.run(["git", "-C", str(root), "add", "tracked.py"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
    return root


def test_capture_uses_tracked_worktree_bytes_and_detects_change(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = make_repo(tmp_path / "repo")
    (repo / "tracked.py").write_text("def edited():\n    return 2\n")
    (repo / "untracked.py").write_text("def hidden():\n    return 3\n")
    manifest = capture_sources(repo, tmp_path / "capture")
    included = {f.file_path: f for f in manifest.files if f.source_path is not None}
    assert set(included) == {"tracked.py"}
    assert included["tracked.py"].source_path.read_bytes().startswith(b"def edited")
    (repo / "tracked.py").write_text("def later():\n    return 4\n")
    with pytest.raises(SourceChangedError):
        validate_sources(repo, manifest)
```

Run `uv run pytest tests/test_syntax_index.py -q`; expect the new capture API red case.

- [ ] **Step 2: Capture a bounded scratch copy, not all source bytes/trees in RAM.**

Use `git ls-files --stage -z`: split each record at its first tab so spaces/newlines in paths survive; parse mode/object/stage separately. Require a valid stage-0 tracked entry, exclude gitlinks (160000), symlinks (120000 or working-tree lstat symlink), and existing ignored relative path components. Do not follow a link or accept an escaping relative path. Record reasons rather than inserting zero-symbol success records for exclusions. A tracked-file disappearance or eligible read error raises `SourceCaptureError`.

For eligible supported regular files up to 1 MiB, read raw bytes, hash them, and write a scratch copy under a unique safe relative path. Decode only in extraction. Unsupported paths need no parser/source copy. Supported oversized files have state skipped and reason, not an invented hash. The manifest digest uses ordered path/mode/admission/hash facts, not filesystem mtimes. Revalidation repeats tracked-entry membership and verifies captured files' bytes before publication; additions/deletions/mutations of eligible sources fail with `SourceChangedError`.

Leave the semantic walk's special force-include policy in semantic preparation; do not reuse it as syntax admission.

- [ ] **Step 3: Create these namespaced tables and exact declaration columns.**

The table ownership boundaries are concrete. Run schema DDL on a fresh destination connection before its data transaction; never call `executescript` inside a transaction that must preserve caller atomicity.

```sql
CREATE TABLE syntax_files (
    file_path TEXT PRIMARY KEY,
    language TEXT,
    file_hash TEXT,
    parser_identity TEXT,
    in_scope INTEGER NOT NULL CHECK (in_scope IN (0, 1)),
    state TEXT NOT NULL CHECK (state IN ('parsed','partial','failed','skipped','unsupported')),
    reason TEXT,
    scip_outline INTEGER NOT NULL DEFAULT 0 CHECK (scip_outline IN (0, 1)),
    scip_definition INTEGER NOT NULL DEFAULT 0 CHECK (scip_definition IN (0, 1))
);
CREATE TABLE syntax_symbols (
    symbol TEXT PRIMARY KEY,
    file_path TEXT NOT NULL REFERENCES syntax_files(file_path),
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    parent_symbol TEXT REFERENCES syntax_symbols(symbol) DEFERRABLE INITIALLY DEFERRED,
    declaration_start_byte INTEGER NOT NULL,
    declaration_end_byte INTEGER NOT NULL,
    declaration_start_line INTEGER NOT NULL,
    declaration_start_character INTEGER NOT NULL,
    declaration_end_line INTEGER NOT NULL,
    declaration_end_character INTEGER NOT NULL,
    selection_start_byte INTEGER NOT NULL,
    selection_end_byte INTEGER NOT NULL,
    selection_start_line INTEGER NOT NULL,
    selection_start_character INTEGER NOT NULL,
    selection_end_line INTEGER NOT NULL,
    selection_end_character INTEGER NOT NULL
);
CREATE INDEX syntax_symbols_file ON syntax_symbols(file_path, selection_start_byte);
CREATE INDEX syntax_symbols_name ON syntax_symbols(name);
CREATE INDEX syntax_symbols_qualified ON syntax_symbols(qualified_name);
CREATE TABLE jarvis_snapshot (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    format_version INTEGER NOT NULL,
    generation TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    published_at TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    scip_state TEXT NOT NULL,
    scip_outlines INTEGER NOT NULL,
    scip_definitions INTEGER NOT NULL,
    scip_references INTEGER NOT NULL,
    scip_calls INTEGER NOT NULL,
    scip_types INTEGER NOT NULL,
    syntax_counts TEXT NOT NULL
);
```

For excluded tracked entries use `in_scope=0`, state skipped and the exclusion reason; coverage counts filter to `in_scope=1`. Distinguish supported size-limit skips from unsupported extensions. Insert declarations with bound values and serialize/deserialize `Span` exactly; do not store a Python pickle or source AST.

Build the new file manifest from scratch on every run. For a hash+identity match in a compatible previous snapshot, copy that file's rows into the new database. Never copy deleted/excluded rows merely because they still exist in the previous snapshot. `previous=None` or a legacy snapshot without syntax tables is a normal first extraction, not an exception-driven fallback. If a reused file is in `parse_for`, parse it once for the callback without re-extracting its already-valid declaration rows; otherwise no AST is needed. Deliver callbacks while the current bytes/tree are live and release them before the next file. Task 4 catches its optional consumer failure so it cannot abort required syntax extraction.

- [ ] **Step 4: Derive actual SCIP coverage and finish snapshot metadata.**

`copy_syntax_tables` creates the same namespaced schema in a converted SCIP connection and copies only Jarvis-owned rows; it never edits converter tables. Use an attached scratch database with bound path and an explicit transaction, detach after commit, and propagate storage failures.

`finalize_snapshot` resets all file provider flags, inspects actual SCIP data if present, and writes the singleton. A file has outline coverage when it has valid enclosing definition ranges or valid decoded nonlocal definition occurrences. Definition coverage requires usable definition locations the definition query can actually return; an empty metadata symbol table or a document row alone is insufficient. Decode each chunk once, filter raw roles with bitwise AND, and match mentions to decoded symbol identities. Reuse public `scip_decoder` functions, never import protobuf/zstd here.

Reference capability requires usable occurrence data. Call capability also requires usable enclosing-definition ranges. Type capability follows actual nonempty relationship data, preserving current `relationship_data_present` semantics. Do not make an empty list mean “known no relationships” when the provider cannot establish that capability.

For new-format reads, `read_snapshot_facts` reads the singleton; malformed/missing required fields raise corruption errors. For legacy snapshots only, derive SCIP-only facts from the real tables. Cache expensive legacy derivation by immutable database path with the existing bounded OrderedDict convention; in-memory connections are never cached by an empty path. Per-file syntax lookup filters `scip_definition=0` only when `uncovered_only=True`. Use case-sensitive equality and the existing dotted-suffix matching rule, not SQL wildcard LIKE over unescaped query text.

- [ ] **Step 5: Add storage/reuse and legacy compatibility regressions.**

Build two scratch snapshots using the real capture/extraction APIs. Check `file_symbols` and `get_syntax_symbol` before/after a rename/delete, then verify the old opaque ID is absent from the new connection and the old open connection still returns the old declaration. Change the extractor identity with unchanged bytes and verify the new stored identity replaces the old one rather than carrying old rows. Check parser/invalid-source distinctions through `file_coverage`, not by asserting SQL implementation text.

Use `build_synthetic_index_db` to create genuine SCIP data, copy syntax tables into it, and assert `read_snapshot_facts` plus lookup routing for covered/uncovered files. Do not use CLI's `_make_index_db(chunks=..., mentions=...)` count-only fixture: its tables are not valid evidence of occurrence coverage. Keep the existing legacy published fixture and assert old metadata still reads. Add generation metadata fields without changing existing positional arguments.

Run `uv run pytest tests/test_syntax_index.py tests/test_index_reader.py -q`.

- [ ] **Step 6: Commit the standalone store/capture boundary.**

```sh
git add src/jarvis/syntax_index.py src/jarvis/index_reader.py tests/test_syntax_index.py tests/test_index_reader.py
git commit -m "feat(index): build immutable syntax snapshots with source coverage"
```

## Task 4: Share parsed input with optional semantic indexing

**Files:**
- Modify: `src/jarvis/semantic.py:256-338`, `src/jarvis/chunker.py` supplied-tree path, `tests/test_semantic.py`, `tests/test_chunker.py`.
- Consume, do not redesign: Task 3's manifest and parse callback.

**Interfaces:**
- Produces `SemanticInput(file_path: str, source_path: Path, file_hash: str, language: str)` and internal frozen `SemanticWork` holding the model, store, table identity, admitted/skipped facts, previous rows/vector map, carried rows, and `inputs: tuple[SemanticInput, ...]` requiring new chunks.
- Produces `prepare_semantic(repo_path: Path, slug: str, *, root: Path | None = None, model: EmbeddingModel | None = None, include_prefixes: tuple[str, ...] = (), manifest: SourceManifest | None = None) -> SemanticWork`.
- Produces `finish_semantic(work: SemanticWork, *, prepared_chunks: dict[str, list[Chunk]], pool: ParserPool) -> SemanticIndexReport`.
- Retains the existing standalone `index_semantic` public signature and implements it by prepare/finish with an empty supplied-chunk map. This remains needed by the existing interactive post-install path; it is not an obsolete API alias.
- Produces `semantic_parse_paths(work: SemanticWork) -> frozenset[str]`, restricted to changed admitted files whose chunking actually uses AST nodes. Fixed-window languages need no extra parse request.

- [ ] **Step 1: Add a regression for the shared-tree versus standalone result.**

Use existing `FakeEmbedder` and `lancedb_available` fixtures in `test_semantic.py`. Create a real tracked Python file containing a multibyte prefix and two sizeable functions. Prepare a work item from Task 3's manifest; let the syntax build callback create chunks with the supplied real tree. Finish embedding with those chunks. Independently index an identical repo/slug with standalone `index_semantic`, then compare observable stored file/range/content/symbol rows, ignoring generated row UUIDs. The results and embedding identities must agree.

The callback has a concrete form in the future CLI:

```python
prepared_chunks = {}
semantic_error = None


def collect_chunks(file, raw, tree):
    nonlocal semantic_error
    if semantic_error is not None:
        return
    try:
        prepared_chunks[file.file_path] = chunk_file(
            file.file_path, raw.decode("utf-8"), file.file_hash,
            file.language, tree=tree,
        )
    except Exception as exc:
        semantic_error = exc
```

This callback is nested inside orchestration, so its `nonlocal` is valid there. The broad catch isolates the optional consumer only; it must not enclose the parser factory, required extraction, database write, or source capture. If chunk collection fails, discard this run's semantic work and warn once at its stage; continue the required baseline.

Run `uv run pytest tests/test_semantic.py -q` with semantic extras installed and verify the new shared-preparation test is initially red.

- [ ] **Step 2: Split the existing semantic function at its natural prepare/encode boundary.**

Move identity/previous-table setup and admission/hash decisions into `prepare_semantic`. `default_model().identity()` and `.prefixes()` do not load model weights; actual encode remains in `finish_semantic`. Preserve the original `iter_source_files` and force-include policy. For paths present in the syntax manifest use captured scratch bytes; semantic-only/untracked force-included inputs keep their existing admission behavior and do not enter navigation.

Prepare `carried` rows and pending `SemanticInput` values once. Do not load the previous Lance table twice. At finish, use `prepared_chunks[file_path]` only for the matching captured file/hash; obtain missing chunks through the same Task 1 provider and existing windows. Never reparse a file whose supplied chunks are valid. Preserve content-hash vector deduplication, unique embedding calls, model/prefix/CONTENT_FORMAT identity, token statistics, truncated reporting, and atomic Lance table replacement behavior.

The encoding operation remains the current algorithm, with explicit prepared chunk selection:

```python
pending = []
for item in work.inputs:
    chunks = prepared_chunks.get(item.file_path)
    if chunks is None:
        text = item.source_path.read_bytes().decode("utf-8", errors="replace")
        chunks = chunk_file(item.file_path, text, item.file_hash, item.language, pool=pool)
    pending.extend(chunks)
unique = [c for c in {c.content_hash: c for c in pending}.values()
          if c.content_hash not in work.vector_by_hash]
for chunk, vector in zip(unique, work.model.embed_texts([c.content for c in unique])):
    work.vector_by_hash[chunk.content_hash] = vector
```

`work.vector_by_hash` is an internal mutable collection owned by this work item; the public dataclass cannot have its fields reassigned. The prepared chunks map is scoped to one manifest/run and must not be reused across runs. Report generation/source mismatch rather than borrowing stale chunks.

- [ ] **Step 3: Preserve optionality and model identity with behavioral tests.**

Cover three outcomes: missing semantic dependencies skip without breaking syntax; a chunk/embedding failure preserves the previous semantic table and does not stop baseline publication; changed CONTENT_FORMAT forces re-chunk/re-embed even when file hashes are unchanged. Retain the existing model/prefix mismatch tests. Ensure fixed-window-only languages remain admitted as before without changing the embedding extension table.

For unchanged semantic files, verify persisted contents/ranges and zero new embedding inputs using the established FakeEmbedder contract; do not introduce source-text or simple forwarding assertions. Base test collection must still succeed without importing LanceDB or sentence-transformers eagerly.

Run `uv run pytest tests/test_semantic.py tests/test_chunker.py tests/test_syntax_index.py -q`.

- [ ] **Step 4: Commit the complete shared-input path.**

```sh
git add src/jarvis/semantic.py src/jarvis/chunker.py tests/test_semantic.py tests/test_chunker.py
git commit -m "refactor(semantic): share captured parsing with syntax indexing"
```

## Task 5: Route navigation queries by per-file provider coverage

**Files:**
- Modify: `src/jarvis/query.py`, `src/jarvis/models.py`, `src/jarvis/symbols.py` (if the combined candidate rule extends the resolution ladder), `src/jarvis/server.py:155-234,324-403`, `tests/test_query.py`, `tests/test_server_tools.py`, `tests/test_index_status.py`.
- Depends on: Task 3 snapshot facts and lookup API; do not re-implement them here.

**Interfaces:**
- Routing is automatic and per-file — no `with_syntax` flag. A file with usable SCIP coverage for an operation is served by SCIP exactly as today; a file without it is served by syntax from the same snapshot connection. One `QueryService` operation acquires one snapshot connection and uses it for resolution, locations, and provenance; never resolve on one generation and fetch locations on another.
- `findReferences`, `callHierarchy`, `typeHierarchy` require the relevant SCIP capability and never fall back to syntax. A syntax `syntax:` identifier passed to them returns an explicit unsupported-identifier error even when other files have SCIP coverage. A full SCIP identifier resolves only through SCIP — never through syntax name matching. Bare/qualified names use the combined candidate rule.
- Result dataclasses in `models.py` gain additive optional fields (`source: "scip" | "tree-sitter"`, `position_encoding: "utf-8"`, `qualified_name`, `parent_symbol`, and for outlines `range`/`selection_range` instead of a single span); ambiguity candidates retain `dotted_path` and gain a location. Syntax-backed responses include a `coverage` object (`state` ∈ complete/partial/unsupported/not-indexed, `reason`, file counts). Existing top-level response keys are unchanged.
- A tool whose required SCIP capability is absent returns the established error shape plus `required_capability`, `reason`, `recovery` — never an empty array that implies an exhaustive search. Corrupt snapshots, malformed SQL, and unexpected failures return errors; fallback handles missing coverage only, not arbitrary exceptions.

- [ ] **Step 1: Add red per-file routing tests for every navigation tool.**

Using `build_synthetic_index_db` plus Task 3's builders, construct one snapshot where some files have genuine SCIP outline/definition coverage and others (same tracked repo, distinct extensions or parse outcomes) have syntax rows only. Then assert:

- `document_symbols`: SCIP-covered file → exactly today's SCIP result (no syntax merge, no dedup drift); syntax-only parsed file → source-order syntax declarations with `source: "tree-sitter"`, `qualified_name`, `parent_symbol`, `range`/`selection_range`, zero-based lines and `position_encoding="utf-8"`; parsed file with zero declarations → `symbols: []` plus coverage complete; unsupported extension → error with coverage `unsupported`; uncaptured/untracked path → error with coverage `not-indexed`; size-skipped or `failed` (undecodable) file → error with coverage `partial` and the recorded reason; partially parsed file → surviving declarations plus coverage `partial`.
- `go_to_definition`: full SCIP identifier → SCIP only; `syntax:` identifier → syntax lookup at that exact identifier, not a name search, and not-found when the declaration changed/deleted (never a name redirect); bare/qualified name → combined candidates: genuine SCIP definition candidates plus syntax candidates from files without usable SCIP definition coverage, multiple locations of one genuine SCIP symbol grouped as one candidate, no cross-provider winner-picking, zero candidates → not-found qualified by searched coverage, multiple distinct candidates → the existing structured ambiguity response with locations.
- `find_references`/`call_hierarchy`/`type_hierarchy`: absent SCIP occurrence/enclosing-range/relationship capability → `required_capability` error (not empty arrays); `syntax:` identifier input → unsupported-identifier error; type hierarchy preserves absent-data versus known-empty distinction.
- `get_index_status`: `capabilities.tools` keyed by the five navigation tool names, each with `available`, `providers`, `reason`, `recovery`; `capabilities.syntax` with counts and extraction identity; snapshot `generation`/`commit`/`published_at` reported separately from latest-run state; existing keys unchanged; never spawns Zoekt or loads grammars; derivation failure degrades to null-plus-reason ("never raises" contract).

- [ ] **Step 2: Implement routing and provenance in `query.py`/`models.py`.**

- Read `read_snapshot_facts` once per connection acquisition (existing `index_reader.py` cache seam), not per query; per-file coverage comes from `syntax_files.scip_outline`/`scip_definition` recorded at snapshot construction.
- `document_symbols`: query the file's SCIP outline when its coverage flag is usable, else map `file_symbols` ordered by `selection_start_byte` into the existing result shape with `DescriptorKind` kinds and the additive fields.
- `go_to_definition`: keep the `symbols.resolve` ladder for SCIP strings; add a syntax resolution branch using `find_syntax_symbols` with the same case-sensitive bare-name/dotted-suffix rules; build combined candidates per the Step 1 contract.
- Capability checks precede provider-specific SQL: detect the relevant capability fact first, so a missing optional provider is a typed capability error, never a caught missing-table exception.
- Keep the MCP boundary's broad `except Exception → {"error": ...}` behavior; below it, unexpected failures raise typed errors.

- [ ] **Step 3: Update tool wrappers and their tests honestly.**

`server.py` serializes the additive fields key-by-key per existing conventions (`dataclasses.asdict` plus explicit renames); syntax-served responses carry `coverage`; capability errors render `requiredCapability`/`reason`/`recovery`. Update `tests/test_server_tools.py` SCIP-missing assertions to the new per-file behavior, and keep one case asserting the plain `{"error": ...}` dict still appears for genuinely unexpected failures. Docstrings state the routing rule and that `findReferences`/hierarchies are SCIP-only.

- [ ] **Step 4: Run and commit.**

Run `uv run pytest tests/test_query.py tests/test_server_tools.py tests/test_index_status.py -q`.

```sh
git add src/jarvis/query.py src/jarvis/models.py src/jarvis/server.py tests/test_query.py tests/test_server_tools.py tests/test_index_status.py
git commit -m "feat(query): route navigation by per-file provider coverage"
```

## Task 6: Wire the syntax baseline into one staged writer pipeline

**Files:**
- Modify: `src/jarvis/index_cli.py:297-591` (pipeline), `src/jarvis/index_cli.py` CLI argument surface, `src/jarvis/registry.py:18-379`, `src/jarvis/config.py` (fallback env removal), `src/jarvis/graph.py`, `src/jarvis/watch.py` caller in `index_cli.py`, `tests/test_index_cli.py`, `tests/test_registry.py`, `tests/test_graph.py`, `tests/test_config.py` (if it pins the env).
- Create: `tests/test_syntax_integration.py` — real-baseline acceptance (spec §11) using real grammars and real Git fixtures.
- Depends on: Tasks 1-5 complete and green.

**Interfaces:**
- CLI gains mutually exclusive `--scip` / `--no-scip` on `index`, `reindex`, and `watch`. Omitted → use the persisted choice, defaulting to enabled for a new repo; an explicit flag updates the persisted choice; a watch-suppressed attempt never persists disabled. `--language` keeps selecting only the SCIP enrichment language (it never restricts syntax coverage); with no SCIP-supported language, publish the baseline and record `scip_state="unsupported"` without requiring `--no-scip`; an invalid explicit language stays a user-input error; `--scheme` stays a SCIP setting.
- Remove `--search-only`, `--fallback-search-only`, `--no-fallback-search-only`, and `JARVIS_FALLBACK_SEARCH_ONLY` — resolvers, `SearchPublishedButIncomplete` degradation branches, runtime aliases, and `config.py` fallback policy. Old options are rejected with a stderr message naming the replacement, never silently mapped. Stdout stays `indexed <slug>`; diagnostics go to stderr.
- Registry status vocabulary (priority `failed` > `degraded` > `partial` > `indexed`, plus transient `indexing`): `indexed` = baseline complete with SCIP usable/explicitly-disabled/unsupported (exit 0); `partial` = published with documented syntax/SCIP extraction gaps (exit 0); `degraded` = baseline published but enabled SCIP failed/unavailable/suppressed (exit 0); `failed` = required stage/storage/publication/bookkeeping failure (nonzero). Stop writing `search-only` as an outcome.
- New registry columns: `scip_enabled` (boolean, default true — the reversible user choice), `scip_state` ∈ `available|partial|failed|unavailable|unsupported|disabled|unknown`, and `scip_failure_reason`/`scip_failure_stderr`/`scip_failed_at_sha` kept separate from overall `status_origin`/`status_reason`/`status_stderr`. Transition to `indexing` must not erase prior failure fields; a suppressed baseline refresh preserves the last SCIP failure fields and state; successful SCIP acceptance clears them; explicit disable or unsupported language clears active failure/suppression state. Exit-0 `degraded` mirrors the concise stage cause into `status_reason`. Recovery strings stay derived by `recovery_for`.
- Snapshot artifacts are named `index-<commit>-<generation>.db` where generation is a collision-resistant unique run identifier (unique even at the same commit); the metadata sibling is the database filename with `.db` → `.metadata.json`, derived from the actual filename in both the writer and cleanup (the writer currently hardcodes a commit-only metadata name — fix that here). Syntax-only generations also publish the `current` pointer; pointer existence means a navigation snapshot exists. Never copy old SCIP data into a syntax-only generation.
- Pipeline order per spec §5: (1) validate git/slug/config/baseline deps — SCIP not required here; (2) capture + syntax build/reuse; (3) optional SCIP when enabled+supported, subject only to the watch suppression predicate; (4) Zoekt — its failure fails the run; (5) optional semantic — existing nonfatal warning behavior; (6) `validate_sources` + graph edge update — a generation without usable SCIP package data clears outgoing edges for every package previously owned by the repo (leaving identities other repos need), and graph/SQLite failures are storage failures, not permission to claim degradation; (7) publish via scratch build → final unique names → write-temp + `os.replace` pointer flip, then record final registry state, then retire superseded versioned artifacts only after the new pointer is live. Registry failure after pointer publication returns nonzero and explicitly reports the snapshot already live; never enter a degradation/retirement path after publication. Uncommitted-edit detection at the same HEAD is not a retry trigger by itself.
- Watch suppression skips only the SCIP attempt, and only when: caller is watch (not explicit index/reindex) AND SCIP enabled with a supported language AND stage state is `failed`/`unavailable` with `scip_failed_at_sha` == current HEAD AND no explicit re-enable or effective language/scheme change invalidated the record. Every debounced event still runs baseline, semantic, and publication; the diagnostic states syntax/search were refreshed and how to force a SCIP retry.
- Known pitfall being fixed: today old versioned files are deleted *before* `published=True` is durably recorded, so a crash between pointer flip and registry success orphans the cleanup. Cleanup moves strictly after the registry write.

- [ ] **Step 1: Add pipeline regression tests before touching `index_repo`.**

Cover, with monkeypatched `_run`/subprocess seams per existing conventions and `JARVIS_DATA_DIR` isolation:

1. Happy path: syntax + SCIP + Zoekt succeed → one `index-<commit>-<generation>.db` with SCIP tables and syntax tables; `current` flips once; metadata sibling named from the actual db filename; registry row `indexed` with `scip_state="available"`.
2. SCIP failure, syntax fine → exit 0 `degraded`; pointer published with syntax-only snapshot (`scip_state="failed"` in the singleton), `scip_failure_reason`/`stderr`/`failed_at_sha` recorded, no fabricated SCIP tables.
3. `--no-scip` → exit 0 `indexed`, `scip_enabled=false` persisted, `scip_state="disabled"`, no indexer invocation; a later omitted-flag run stays disabled; `--scip` re-enables and attempts SCIP.
4. SCIP-unsupported language → exit 0 `indexed`, `scip_state="unsupported"`, no `--no-scip` required.
5. Zoekt failure → exit nonzero `failed`; nothing published this run; prior live snapshot untouched.
6. Registry write fails after pointer flip → exit nonzero with the "snapshot already live" report; old generations not yet deleted; a later successful run cleans them.
7. Same-commit suppression: watch run with prior `failed` stage at same HEAD → SCIP skipped, baseline/semantic/publication still run, failure fields preserved, diagnostic names the retry command; new commit or explicit index/reindex attempts SCIP again; explicit re-enable clears suppression.
8. Snapshot safety: same-commit reindex produces *different* immutable `index-<commit>-<generation>.db` filenames with matching metadata stems; reader cache invalidates by pointer content; one tool operation can never mix generations. Plus syntax reuse: unchanged `source_hash` + parser identity → file rows carried, no re-parse (assert via parse callback counts), new generation id still unique.
9. Graph: SCIP-less generation clears all repo-owned outgoing edges even when the prior generation had working SCIP edges; package identities shared with other repos survive; `forget` unpins zoekt.name and clears edges (fixing the evidence-found gap).
10. Legacy compatibility: pre-migration registry row with `search_only=true`+manual origin maps to `scip_enabled=false`; auto-fallback rows map to enabled with failure copied to stage fields; pointerless historical row reports no navigation snapshot + reindex recovery; a published legacy SCIP db is never modified and remains readable.
11. `--search-only` and `JARVIS_FALLBACK_SEARCH_ONLY` are rejected/ignored respectively, with the stderr replacement message.

- [ ] **Step 2: Restructure `index_repo` and the CLI surface.**

Implement the interface contract above exactly. Apply every disposition in the spec's supersession table (§12): FALL-01 whole-pipeline degrade catch removed (baseline publication is default; SCIP failure isolated as optional enrichment); FALL-02 fallback-switch precedence chain removed; FALL-03 retained (explicit runs retry; failure never changes user enablement); FALL-04 narrowed (missing/incompatible *optional SCIP* tooling → exit-0 degraded; missing Zoekt/grammars and storage failures stay hard; post-publication failures never degrade/retire); FALL-05 replaced by the stage-only predicate; `search_only` persistence and the D-10 forget/reindex re-enable escape replaced by reversible `scip_enabled`; `SEARCH_ONLY_STATUS`/no-pointer publication removed (baseline generations always publish the pointer); `PARTIAL_STATUS` broadened to documented syntax-or-SCIP coverage gaps; D-04 narrowed (successful baseline bookkeeping cannot erase an active SCIP failure; successful enrichment clears its own fields); D-07/D-15, D-09, WR-02 retained. Update each corresponding `D-xx`/`FALL-xx` comment to point at its TSI section or delete it when the decision is gone — no contradictory policy comments remain. Fix the transitional upsert that erases failure fields: upsert takes explicit stage-scoped field groups. The semantic stage consumes Task 4's prepare/finish + `collect_chunks` callback; its failure stays a warning.

- [ ] **Step 3: Registry migration.**

One idempotent, transactional migration (consolidating the current per-column migration commits per spec §7/TSI-08 — preserve idempotency for databases already carrying the per-column form): add the new columns, copy `search_only`/`fallback_enabled` facts per the Step 1 case-10 mapping, then drop the obsolete columns and their readers/writers — no dual state models. `tests/test_registry.py` gains migration cases: fresh schema, pre-migration schema, per-column intermediate schema; field-preservation across failed runs; vocabulary validation on read/write without discarding unknown historical failure text.

- [ ] **Step 4: Watch integration.**

`watch.py` stays pure/thread-free with its injectable clock; only the caller in `index_cli.py` changes to apply the four-condition SCIP-only suppression predicate. Debounce semantics unchanged. Tests drive the predicate through the existing thread-free conventions.

- [ ] **Step 5: Real-baseline acceptance tests (`tests/test_syntax_integration.py`).**

Marked `@pytest.mark.integration` with a module-level `shutil.which("zoekt-git-index")` skip (grammars are base deps — never skipped for their absence; SCIP binaries are deliberately *removed* from the test PATH via monkeypatch to prove the baseline needs no SCIP tooling). Each test uses a real committed Git fixture in `tmp_path` with `JARVIS_DATA_DIR` isolation:

1. Offline baseline: PATH stripped of `scip`/`scip-python` → `index_repo` succeeds (exit-0 `degraded`), publishes the pointer, and real `documentSymbols`/`goToDefinition` through `QueryService` return correct syntax declarations. The provider never touches the network (no download code path exists; assert no `urllib`/`httpx` calls by construction of `syntax.py`).
2. Missing Zoekt binary additionally stripped → hard `failed`, nothing published.
3. Full pipeline with real binaries when available: syntax + SCIP + Zoekt in one snapshot; mixed-provider routing per Task 5 over the real data; `jarvis` CLI round trip (`index` → `status` → `forget`).

- [ ] **Step 6: Full local verification and commit.**

Run `uv run pytest -m "not integration" -rs` (whole suite) plus `uv run pytest -m integration` if binaries are present (self-skips otherwise). Verify `uv run jarvis index tests/fixtures/mini_py_repo --slug syntax-smoke --no-scip` against a temp `JARVIS_DATA_DIR` end-to-end, then `jarvis status syntax-smoke` showing syntax counts and `scipState: disabled`, then a real `jarvis index tests/fixtures/mini_py_repo --slug syntax-smoke --scip` with binaries available, and `jarvis-server` MCP `documentSymbols` answering from syntax on the `--no-scip` snapshot. Clean up with `jarvis forget syntax-smoke`.

```sh
git add src/jarvis/index_cli.py src/jarvis/registry.py src/jarvis/config.py src/jarvis/graph.py src/jarvis/watch.py tests/test_index_cli.py tests/test_registry.py tests/test_graph.py tests/test_syntax_integration.py
git commit -m "feat(index): staged syntax baseline pipeline with reversible scip controls"
```

## Task 7: Distribution, docs, and release gates

**Files:**
- Modify: `pyproject.toml` (already from Task 1), `README.md`, `docs/system-architecture.md`, `docs/codebase-summary.md`, `docs/code-standards.md`, `.github/workflows/test.yml`, `.github/workflows/publish-pypi.yml`, `scripts/check_wheel_contents.py` (only if grammar wheels need exemption rules), `.claude/skills/jarvis-release/SKILL.md` (runbook note only). `setup.sh` intentionally unchanged — verify, don't edit.
- Depends on: Task 6 green.

- [ ] **Step 1: Confirm no binary bootstrap changes are needed.**

The provider is pip-only; `setup.sh` gains nothing. Verify by asserting a fresh `uv sync` (no extras) imports `jarvis.syntax` and constructs `ParserPool` for python. If any grammar wheel turns out to be source-only on a supported platform, stop and re-evaluate the Task 1 pin with the user — do not silently drop the grammar or lower the platform floor.

- [ ] **Step 2: Update wheel-content and CI gates.**

`check_wheel_contents.py`: grammar `.so` files arrive as *installed dependencies*, not jarvis-owned modules — verify the current script only checks jarvis's own compiled modules and does not reject dependency wheels; extend only if it actually breaks. `test.yml`: no change needed beyond Task 1's base deps (verify the semantic extra is still synced for LanceDB tests; the matrix now exercises grammar imports implicitly — add a comment saying so). `publish-pypi.yml`: the wheel smoke currently runs the MCP JSON-RPC handshake; extend it to also import `jarvis.syntax` and run one real `ParserPool().parse("python", b"def f():\n    pass\n")` inside the installed-wheel environment, satisfying spec §11's "real parser coverage in the installed-wheel test job". Also confirm the wheel-only resolution matrix from Task 1 Step 2 is recorded as a release gate (rerun it here for the final pin).

- [ ] **Step 3: Docs.**

- `README.md`: requirements section notes pip-installed grammars (no external binary); tool table documents `source`/`coverage` fields for `documentSymbols`/`goToDefinition` and the SCIP-required contract for `findReferences`/`callHierarchy`/`typeHierarchy`; CLI section documents `--scip`/`--no-scip` and the removed `--search-only`/fallback flags with their replacement; keep line-3 marker intact.
- `docs/system-architecture.md`: new syntax-baseline layer between capture and SCIP; staged-pipeline/single-snapshot-publication diagram; registry status vocabulary table.
- `docs/codebase-summary.md` and `docs/code-standards.md`: module index gains `syntax.py`/`syntax_index.py`; call-graph walkthrough for the query routing seam; code-standards gains the curated-grammar-provider pattern (lazy import, frozen dataclasses, byte-span slicing) and the staged-publication/registry-vocabulary patterns.
- `docs/superpowers/specs/2026-09-09-tree-sitter-indexing-design.md`: flip Status to "Implemented" at the end of Task 6, not before.
- Version bump per lockstep runbook (`.claude/skills/jarvis-release/SKILL.md`) — 3 files + `uv lock`; CHANGELOG entry describing the syntax baseline, per-language coverage, reversible `--scip` controls, and per-file provider routing.

- [ ] **Step 4: Final full verification.**

```sh
uv run pytest -m "not integration" -rs
uv run python scripts/check_versions.py
```

Both must pass. Tag/release per the runbook is a user decision, out of scope for this plan.

```sh
git add README.md docs/ .github/workflows/ scripts/ .claude/skills/jarvis-release/SKILL.md CHANGELOG.md pyproject.toml uv.lock
git commit -m "docs: document tree-sitter syntax baseline and release gates"
```

## Verification and completion

- Task 6 Step 6 is the explicit end-to-end smoke (`jarvis index` → `status` → MCP `documentSymbols` over a SCIP-less snapshot); Task 6 Step 5 adds the real-baseline integration acceptance (spec §11).
- No task may leave import-skipped grammar tests, silent language-pack fallbacks, or invented coverage facts.
