"""Curated offline tree-sitter grammar provider and syntax declaration
extraction.

Shared loader for the syntax baseline and the semantic chunker (spec
TSI-02 "Grammar distribution and language coverage", TSI-09 "Shared
chunking and component boundaries"). Grammars are base dependencies,
resolved through one finite language -> distribution/module/factory map;
native tree_sitter imports happen only when a grammar is actually needed,
so importing this module stays cheap and cycle-free.

The provider fails loudly: a missing distribution, an ABI-incompatible
grammar, or a broken factory raises SyntaxDependencyError instead of
degrading to generic chunking. ParserPool instances are owned by one
indexing worker -- never a process-global mutable parser cache.

No runtime downloads and no repository grammar compilation: every
selection resolves from prebuilt abi3 wheels (spec TSI-02).

`extract_file` walks a parsed Tree with an explicit stack (never Python
recursion) and extracts named function/method/type/namespace declarations
per spec TSI-03 "Declaration contract". Extraction is a curated per-grammar
rule table derived from real parses of the pinned grammar versions, not a
tags-query union (tags files are incomplete and include references).
Dropped/simplified: no type inference, no reference/call-site tracking, no
binding resolution across files -- pure syntactic declaration discovery.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from jarvis.symbols import DescriptorKind

if TYPE_CHECKING:
    from tree_sitter import Node, Parser, Tree

# Folded into grammar_identity(): bump when extraction policy changes so
# stored rows re-extract instead of carrying stale rows forward (spec
# TSI-03 "Incremental reuse").
SYNTAX_EXTRACTOR_VERSION = 1

# The one curated map (spec TSI-02 "Loader boundary"): internal language
# name -> (distribution, module, factory). TypeScript and TSX are two
# factory selections from one distribution; PHP selects the PHP-with-tags
# factory. Repository-supplied grammar code is never instantiated.
FACTORIES: dict[str, tuple[str, str, str]] = {
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


class SyntaxDependencyError(RuntimeError):
    """A required grammar runtime/distribution is missing or incompatible."""


def _selection(language: str) -> tuple[str, str, str]:
    """Map an internal language name. Unknown names are configuration
    errors -- the caller asked for a language the curation never offered --
    not something to answer with empty parse output (spec TSI-09)."""
    try:
        return FACTORIES[language]
    except KeyError:
        raise ValueError(
            f"no grammar selection for language {language!r}; expected one of: "
            + ", ".join(sorted(FACTORIES))) from None


def slice_text(source: bytes, start: int, end: int) -> str:
    """Decode one byte range of captured source bytes (spec TSI-03
    "Coordinates and identity": never slice a Python str with tree-sitter
    byte offsets; convert to text only after byte slicing)."""
    return source[start:end].decode("utf-8")


def grammar_identity(language: str) -> str:
    """Deterministic digest of extractor version, runtime version, grammar
    distribution/version, and parser selection (spec TSI-03 "Incremental
    reuse"). Computed from declared package metadata only -- never from
    installed-file paths or mtimes -- so identical installs hash
    identically."""
    distribution, module_name, factory_name = _selection(language)
    try:
        payload = json.dumps({
            "extractor_version": SYNTAX_EXTRACTOR_VERSION,
            "runtime_version": importlib.metadata.version("tree-sitter"),
            "distribution": distribution,
            "distribution_version": importlib.metadata.version(distribution),
            "parser_selection": f"{module_name}.{factory_name}",
        }, sort_keys=True)
    except importlib.metadata.PackageNotFoundError as error:
        raise SyntaxDependencyError(
            f"grammar distribution {distribution!r} (language {language!r}) "
            "is not installed") from error
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ParserPool:
    """Per-worker grammar loader and parser cache (spec TSI-02 "Loader
    boundary"): load only the languages a run uses, reuse each parser
    within one indexing worker, and never share a mutable parser across
    workers."""

    def __init__(self) -> None:
        self._parsers: dict[str, Parser] = {}

    def parse(self, language: str, source: bytes) -> Tree:
        """Parse UTF-8 source bytes. All offsets in the returned tree refer
        to those bytes (spec TSI-03 "Coordinates and identity")."""
        parser = self._parsers.get(language)
        if parser is None:
            parser = self._build_parser(language)
            self._parsers[language] = parser
        return parser.parse(source)

    def _build_parser(self, language: str) -> Parser:
        distribution, module_name, factory_name = _selection(language)
        try:
            from tree_sitter import Language, Parser
        except ImportError as error:
            raise SyntaxDependencyError(
                f"tree-sitter runtime is not installed "
                f"(required for language {language!r})") from error
        try:
            distribution_version = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError as error:
            raise SyntaxDependencyError(
                f"grammar distribution {distribution!r} (language {language!r}) "
                "is not installed") from error
        try:
            # Spec TSI-02 "Loader boundary": the curated factory call is the
            # only route to a language object, and only failures at exactly
            # this boundary -- absent module, ABI-invalid grammar, broken
            # factory -- are dependency errors rather than parse failures.
            module = importlib.import_module(module_name)
            language_object = Language(getattr(module, factory_name)())
            parser = Parser(language_object)
        except (ImportError, TypeError, ValueError) as error:
            raise SyntaxDependencyError(
                f"grammar {distribution}=={distribution_version} for language "
                f"{language!r} failed to load via "
                f"{module_name}.{factory_name}(): {error}") from error
        return parser


# ---------------------------------------------------------------------------
# Declaration extraction (spec TSI-03 "Declaration contract", "Coordinates
# and identity"). `extract_file` walks a parsed Tree with an explicit stack
# of sibling-iteration frames -- never Python recursion, so a deeply nested
# file cannot exhaust the interpreter's recursion limit. A frame carries the
# qualification text and nearest emitted-declaration id in effect for the
# sibling list it is iterating; a header declaration (package/namespace with
# no body) mutates its own frame in place so later siblings inherit it,
# exactly matching spec TSI-03's "governs siblings" rule for Java/Kotlin/Go
# package headers, C# file-scoped namespaces, PHP semicolon namespaces, and
# unbraced Scala packages.
# ---------------------------------------------------------------------------


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
    file_path: str
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


# Extension -> internal language name (spec TSI-02 section 3, "Delivery
# contract" table). `.h` always resolves to C, matching the existing
# extension policy -- never guessed from neighboring files.
LANGUAGE_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin",
    ".swift": "swift",
    ".go": "go",
    ".rb": "ruby",
    ".rs": "rust",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".scala": "scala", ".sc": "scala",
    ".sh": "bash", ".bash": "bash",
    ".sql": "sql",
}


def language_for_path(path: str) -> str | None:
    """Repository-relative path -> internal language name, or `None` for an
    unsupported/absent extension (spec TSI-02 "Delivery contract")."""
    dot = path.rfind(".")
    slash = max(path.rfind("/"), path.rfind("\\"))
    if dot <= slash:
        return None
    return LANGUAGE_EXTENSIONS.get(path[dot:])


def syntax_id(file_path: str, file_hash: str, selection: Span, kind: DescriptorKind) -> str:
    """Deterministic `syntax:`-prefixed digest of repository-relative path,
    raw file hash, declaration identifier byte range, and kind (spec TSI-03
    "Coordinates and identity"). An unchanged declaration in an unchanged
    file retains its identifier across generations; a changed or deleted
    declaration never redirects an old identifier to a new target."""
    payload = json.dumps(
        [file_path, file_hash, selection.start_byte, selection.end_byte, str(kind)],
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    return "syntax:" + hashlib.sha256(payload).hexdigest()


_TYPE = DescriptorKind.TYPE
_METHOD = DescriptorKind.METHOD
_NAMESPACE = DescriptorKind.NAMESPACE
_TERM = DescriptorKind.TERM


def _is_valid_identifier(node: Node | None) -> bool:
    """MISSING/ERROR identifiers are rejected (spec TSI-03): a syntax error
    at the head of a declaration is not a name to invent evidence for."""
    return node is not None and node.type != "ERROR" and not node.is_missing


def _child_of_type(node: Node, types: frozenset[str]) -> Node | None:
    """First direct child matching one of `types`, for grammars that leave
    a declaration's target unfielded (positional-only children)."""
    for child in node.children:
        if child.type in types:
            return child
    return None


def _field_children(node: Node, field_name: str) -> list[Node]:
    return [node.children[i] for i in range(len(node.children))
            if node.field_name_for_child(i) == field_name]


def _text(source: bytes, node: Node) -> str:
    return slice_text(source, node.start_byte, node.end_byte)


@dataclass
class _Candidate:
    """One declaration produced by a node: `identifier` supplies the name
    and selection span; `qualifier_prefix` is extra source-spelled text
    (e.g. a Go receiver type, a C++ `N::` out-of-line qualifier) folded into
    this candidate's own qualified name without creating a linked parent
    row (spec TSI-03 section "Resolve scope details")."""
    identifier: Node
    kind: DescriptorKind
    qualifier_prefix: str = ""


@dataclass
class _RuleOutcome:
    """What a recognized node contributes. `candidates` holds zero or one
    emitted symbol (a node type that can legitimately emit more than one --
    e.g. JavaScript's dual-name function expression -- falls out of the
    generic walk instead: the inner named expression is independently a
    candidate the next time the walk visits it, see `_rule_javascript`).
    `is_header` mutates the current frame in place rather than opening a
    new one (package/namespace headers with no body). `qualifier_extension`
    lets a context-only node (unnamed Kotlin companion, Swift extension,
    Rust `impl`, Ruby singleton class) extend source-spelled qualification
    for its children without emitting a row of its own."""
    candidates: list[_Candidate] = field(default_factory=list)
    is_header: bool = False
    qualifier_extension: str = ""


def _candidate(identifier: Node | None, kind: DescriptorKind, qualifier_prefix: str = "") -> _Candidate | None:
    if not _is_valid_identifier(identifier):
        return None
    return _Candidate(identifier, kind, qualifier_prefix)


def _simple(node: Node, field_name: str, kind: DescriptorKind) -> _RuleOutcome | None:
    """The common shape: `node.child_by_field_name(field_name)` is the
    declared identifier, with no extra handling required."""
    candidate = _candidate(node.child_by_field_name(field_name), kind)
    return _RuleOutcome(candidates=[candidate]) if candidate else None


# -- Wrapper unwrapping (spec TSI-03: "decorator/export/template wrappers
# extend the declaration span, never replace the identifier span" and
# "Prevent a wrapped inner declaration from being emitted twice"). A wrapper
# is resolved to its real declaration node *before* dispatch ever runs, so
# only the real node is ever classified -- no separate no-op branch needed
# to avoid double emission.
_UNWRAP_FIELD: dict[str, dict[str, str]] = {
    "python": {"decorated_definition": "definition"},
    "javascript": {"export_statement": "declaration"},
    "typescript": {"export_statement": "declaration"},
    "tsx": {"export_statement": "declaration"},
}

_CPP_TEMPLATE_INNER = frozenset({
    "class_specifier", "function_definition", "declaration",
    "alias_declaration", "struct_specifier", "union_specifier", "enum_specifier",
})


def _unwrap(language: str, node: Node) -> Node | None:
    field_map = _UNWRAP_FIELD.get(language)
    if field_map is not None and node.type in field_map:
        return node.child_by_field_name(field_map[node.type])
    if language == "cpp" and node.type == "template_declaration":
        return _child_of_type(node, _CPP_TEMPLATE_INNER)
    return None


# -- Python (spec TSI-03 rule table row "python"). --------------------------

def _rule_python(node: Node, source: bytes) -> _RuleOutcome | None:
    node_type = node.type
    if node_type == "class_definition":
        return _simple(node, "name", _TYPE)
    if node_type == "function_definition":
        return _simple(node, "name", _METHOD)
    if node_type == "type_alias_statement":
        left = node.child_by_field_name("left")
        if left is not None and left.type == "type" and left.children:
            left = left.children[0]
        candidate = _candidate(left, _TYPE)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    if node_type == "assignment":
        # Direct identifier assignment to `lambda`; no assignment-to-call
        # inference (spec TSI-03 rule table).
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is None or left.type != "identifier" or right is None or right.type != "lambda":
            return None
        candidate = _candidate(left, _METHOD)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    return None


# -- JavaScript / TypeScript / TSX (spec TSI-03 rule table rows
# "javascript", "typescript", "tsx"). TS and TSX share the JS grammar's
# node shapes for class/function declarations, method_definition,
# variable_declarator, field_definition, and pair; TS-only node types
# (interfaces, type aliases, enums, namespaces, signatures, and the
# differently-named `public_field_definition`) are checked first, falling
# back to the JS rule for everything the two grammars share. --------------

_JS_CALLABLE_VALUES = frozenset({"function_expression", "arrow_function", "generator_function"})


def _rule_javascript(node: Node, source: bytes) -> _RuleOutcome | None:
    node_type = node.type
    if node_type in ("class_declaration", "function_declaration", "generator_function_declaration"):
        return _simple(node, "name", _TYPE if node_type == "class_declaration" else _METHOD)
    if node_type in ("class", "function_expression", "generator_function"):
        # Named expressions only -- an anonymous function/class expression
        # is not itself a declaration target.
        name = node.child_by_field_name("name")
        candidate = _candidate(name, _TYPE if node_type == "class" else _METHOD)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    if node_type == "method_definition":
        return _simple(node, "name", _METHOD)
    if node_type == "variable_declarator":
        # Identifier bound directly to a function/arrow/generator value;
        # class-valued bindings are not function-valued so they are
        # excluded here (a named class expression is still picked up on
        # its own via the `class` branch above when the walk reaches it).
        name = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        if name is None or name.type != "identifier" or value is None or value.type not in _JS_CALLABLE_VALUES:
            return None
        candidate = _candidate(name, _METHOD)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    if node_type == "field_definition":
        # Callable class field (spec TSI-03: "Callable class
        # field_definition.property").
        prop = node.child_by_field_name("property")
        value = node.child_by_field_name("value")
        if prop is None or value is None or value.type not in _JS_CALLABLE_VALUES:
            return None
        candidate = _candidate(prop, _METHOD)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    if node_type == "pair":
        # Static object key with a function value (spec TSI-03: "static
        # object pair.key"). Dynamic/computed keys are excluded.
        key = node.child_by_field_name("key")
        value = node.child_by_field_name("value")
        if key is None or key.type not in ("property_identifier", "string") or value is None \
                or value.type not in _JS_CALLABLE_VALUES:
            return None
        candidate = _candidate(key, _METHOD)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    return None


def _rule_typescript(node: Node, source: bytes) -> _RuleOutcome | None:
    node_type = node.type
    if node_type in ("abstract_class_declaration", "interface_declaration",
                      "type_alias_declaration", "enum_declaration"):
        return _simple(node, "name", _TYPE)
    if node_type in ("internal_module", "module"):
        return _simple(node, "name", _NAMESPACE)
    if node_type in ("function_signature", "method_signature", "abstract_method_signature"):
        return _simple(node, "name", _METHOD)
    if node_type == "public_field_definition":
        # TS class bodies use public_field_definition, not JS's
        # field_definition, for the callable-property check (spec TSI-03
        # rule table: "Class callable property is public_field_definition
        # .name, not JS's field node").
        name = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        if name is None or value is None or value.type not in _JS_CALLABLE_VALUES:
            return None
        candidate = _candidate(name, _METHOD)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    return _rule_javascript(node, source)


# -- Java (spec TSI-03 rule table row "java"). -------------------------------

_JAVA_HEADER_NAME_TYPES = frozenset({"identifier", "scoped_identifier"})


def _rule_java(node: Node, source: bytes) -> _RuleOutcome | None:
    node_type = node.type
    if node_type in ("class_declaration", "interface_declaration", "enum_declaration",
                      "record_declaration", "annotation_type_declaration"):
        return _simple(node, "name", _TYPE)
    if node_type in ("method_declaration", "constructor_declaration",
                      "compact_constructor_declaration", "annotation_type_element_declaration"):
        return _simple(node, "name", _METHOD)
    if node_type == "package_declaration":
        # Explicit package header governs siblings (spec TSI-03 "Resolve
        # scope details"); its declaration range is the header itself.
        name = _child_of_type(node, _JAVA_HEADER_NAME_TYPES)
        candidate = _candidate(name, _NAMESPACE)
        return _RuleOutcome(candidates=[candidate], is_header=True) if candidate else None
    if node_type == "module_declaration":
        return _simple(node, "name", _NAMESPACE)
    return None


# -- Kotlin (spec TSI-03 rule table row "kotlin"). ---------------------------

_KOTLIN_HEADER_NAME_TYPES = frozenset({"identifier", "qualified_identifier"})


def _rule_kotlin(node: Node, source: bytes) -> _RuleOutcome | None:
    node_type = node.type
    if node_type in ("class_declaration", "object_declaration"):
        return _simple(node, "name", _TYPE)
    if node_type == "function_declaration":
        return _simple(node, "name", _METHOD)
    if node_type == "companion_object":
        # Unnamed companion is context only, not a new type row (spec
        # TSI-03 "Resolve scope details"); a named companion is a type.
        name = node.child_by_field_name("name")
        if not _is_valid_identifier(name):
            return _RuleOutcome()
        candidate = _candidate(name, _TYPE)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    if node_type == "type_alias":
        return _simple(node, "type", _TYPE)
    if node_type == "package_header":
        name = _child_of_type(node, _KOTLIN_HEADER_NAME_TYPES)
        candidate = _candidate(name, _NAMESPACE)
        return _RuleOutcome(candidates=[candidate], is_header=True) if candidate else None
    if node_type == "property_declaration":
        # Direct property lambda binding requires descending
        # variable_declaration (spec TSI-03 rule table).
        variable_declaration = _child_of_type(node, frozenset({"variable_declaration"}))
        value = node.child_by_field_name("value")
        if variable_declaration is None or value is None or value.type != "lambda_literal":
            return None
        identifier = _child_of_type(variable_declaration, frozenset({"identifier"}))
        candidate = _candidate(identifier, _METHOD)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    return None


# -- Swift (spec TSI-03 rule table row "swift"). -----------------------------

def _rule_swift(node: Node, source: bytes) -> _RuleOutcome | None:
    node_type = node.type
    if node_type == "class_declaration":
        # `declaration_kind` distinguishes class/struct/actor/enum from
        # extension; extension context must not create a new type row
        # (spec TSI-03 rule table and "Resolve scope details").
        declaration_kind = node.child_by_field_name("declaration_kind")
        keyword = _text(source, declaration_kind) if declaration_kind is not None else ""
        name = node.child_by_field_name("name")
        if name is not None and name.type == "user_type":
            name = _child_of_type(name, frozenset({"type_identifier"}))
        if keyword == "extension":
            if not _is_valid_identifier(name):
                return None
            return _RuleOutcome(qualifier_extension=_text(source, name) + ".")
        candidate = _candidate(name, _TYPE)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    if node_type in ("protocol_declaration", "typealias_declaration", "associatedtype_declaration"):
        return _simple(node, "name", _TYPE)
    if node_type in ("function_declaration", "protocol_function_declaration"):
        return _simple(node, "name", _METHOD)
    if node_type == "property_declaration":
        # Simple named property with a lambda value is callable (spec
        # TSI-03 rule table).
        name = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        if name is None or value is None or value.type != "lambda_literal":
            return None
        if name.type == "pattern":
            name = _child_of_type(name, frozenset({"bound_identifier", "simple_identifier"}))
        elif name.type not in ("bound_identifier", "simple_identifier"):
            name = _child_of_type(name, frozenset({"bound_identifier", "simple_identifier"})) or name
        candidate = _candidate(name, _METHOD)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    return None


# -- Go (spec TSI-03 rule table row "go"). -----------------------------------

def _rule_go(node: Node, source: bytes) -> _RuleOutcome | None:
    node_type = node.type
    if node_type == "package_clause":
        name = _child_of_type(node, frozenset({"package_identifier"}))
        candidate = _candidate(name, _NAMESPACE)
        return _RuleOutcome(candidates=[candidate], is_header=True) if candidate else None
    if node_type in ("type_spec", "type_alias"):
        return _simple(node, "name", _TYPE)
    if node_type == "function_declaration":
        return _simple(node, "name", _METHOD)
    if node_type == "method_declaration":
        # A receiver spelling supplies context, not a link to a resolved
        # type declaration (spec TSI-03 "Resolve scope details").
        name = node.child_by_field_name("name")
        if not _is_valid_identifier(name):
            return None
        prefix = ""
        receiver = node.child_by_field_name("receiver")
        if receiver is not None:
            declaration = _child_of_type(receiver, frozenset({"parameter_declaration"}))
            if declaration is not None:
                receiver_type = declaration.child_by_field_name("type")
                if receiver_type is not None and receiver_type.type == "pointer_type":
                    receiver_type = _child_of_type(receiver_type, frozenset({"type_identifier"}))
                if receiver_type is not None and receiver_type.type == "type_identifier":
                    prefix = _text(source, receiver_type) + "."
        candidate = _candidate(name, _METHOD, prefix)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    if node_type == "method_elem":
        return _simple(node, "name", _METHOD)
    if node_type == "var_spec":
        # Direct var function literal requires one-to-one syntactic
        # binding (spec TSI-03 rule table).
        names = _field_children(node, "name")
        value = node.child_by_field_name("value")
        if len(names) != 1 or names[0].type != "identifier" or value is None:
            return None
        if value.type != "expression_list" or len(value.children) != 1 or value.children[0].type != "func_literal":
            return None
        candidate = _candidate(names[0], _METHOD)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    if node_type == "short_var_declaration":
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is None or left.type != "expression_list" or len(left.children) != 1 \
                or left.children[0].type != "identifier":
            return None
        if right is None or right.type != "expression_list" or len(right.children) != 1 \
                or right.children[0].type != "func_literal":
            return None
        candidate = _candidate(left.children[0], _METHOD)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    return None


# -- Ruby (spec TSI-03 rule table row "ruby"). -------------------------------

def _rule_ruby(node: Node, source: bytes) -> _RuleOutcome | None:
    node_type = node.type
    if node_type in ("class", "module"):
        name = node.child_by_field_name("name")
        if name is not None and name.type == "scope_resolution":
            # Qualified constant name leaf comes from scope_resolution.name
            # (spec TSI-03 rule table).
            leaf = name.child_by_field_name("name")
            scope = name.child_by_field_name("scope")
            prefix = (_text(source, scope) + ".") if scope is not None else ""
            candidate = _candidate(leaf, _TYPE, prefix)
        else:
            candidate = _candidate(name, _TYPE)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    if node_type in ("method", "singleton_method"):
        return _simple(node, "name", _METHOD)
    if node_type == "alias":
        # Explicit method alias: `name` is the newly declared alias.
        return _simple(node, "name", _METHOD)
    if node_type == "singleton_class":
        # `class << self` is context, not a new type (spec TSI-03 "Resolve
        # scope details").
        return _RuleOutcome()
    if node_type == "assignment":
        # Direct `lambda` (`->`) value is callable; `proc`/`lambda` calls
        # are not inferred constructors (spec TSI-03 rule table).
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is None or left.type != "identifier" or right is None or right.type != "lambda":
            return None
        candidate = _candidate(left, _METHOD)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    return None


# -- Rust (spec TSI-03 rule table row "rust"). -------------------------------

def _rule_rust(node: Node, source: bytes) -> _RuleOutcome | None:
    node_type = node.type
    if node_type in ("struct_item", "enum_item", "union_item", "trait_item",
                      "type_item", "associated_type"):
        return _simple(node, "name", _TYPE)
    if node_type in ("function_item", "function_signature_item"):
        return _simple(node, "name", _METHOD)
    if node_type == "mod_item":
        return _simple(node, "name", _NAMESPACE)
    if node_type == "impl_item":
        # impl_item has no name; its spelled target supplies context only
        # (spec TSI-03 rule table).
        target = node.child_by_field_name("type")
        if not _is_valid_identifier(target):
            return None
        return _RuleOutcome(qualifier_extension=_text(source, target) + ".")
    if node_type == "let_declaration":
        # Simple let closure binding is callable (spec TSI-03 rule table).
        pattern = node.child_by_field_name("pattern")
        value = node.child_by_field_name("value")
        if pattern is None or pattern.type != "identifier" or value is None \
                or value.type != "closure_expression":
            return None
        candidate = _candidate(pattern, _METHOD)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    return None


# -- Scala (spec TSI-03 rule table row "scala"). -----------------------------

def _rule_scala(node: Node, source: bytes) -> _RuleOutcome | None:
    node_type = node.type
    if node_type in ("class_definition", "trait_definition", "object_definition",
                      "type_definition", "package_object"):
        return _simple(node, "name", _TYPE)
    if node_type in ("function_definition", "function_declaration"):
        return _simple(node, "name", _METHOD)
    if node_type == "package_clause":
        # Header-only (unbraced) packages govern siblings (spec TSI-03
        # "Resolve scope details"); a braced package creates its own scope.
        name = node.child_by_field_name("name")
        candidate = _candidate(name, _NAMESPACE)
        if candidate is None:
            return None
        has_body = node.child_by_field_name("body") is not None
        return _RuleOutcome(candidates=[candidate], is_header=not has_body)
    if node_type == "val_definition":
        # Direct simple val/var lambda value is callable; no arbitrary
        # val/parameter captures (spec TSI-03 rule table).
        pattern = node.child_by_field_name("pattern")
        value = node.child_by_field_name("value")
        if pattern is None or pattern.type != "identifier" or value is None \
                or value.type != "lambda_expression":
            return None
        candidate = _candidate(pattern, _METHOD)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    return None


# -- Bash (spec TSI-03 rule table row "bash"). -------------------------------

def _rule_bash(node: Node, source: bytes) -> _RuleOutcome | None:
    if node.type == "function_definition":
        return _simple(node, "name", _METHOD)
    return None


# -- SQL (spec TSI-03 rule table row "sql"). No CREATE PROCEDURE production
# exists in the pinned 0.3.11 grammar -- `CREATE PROCEDURE` parses as an
# ERROR node, so no symbol is ever invented for it; coverage is simply
# partial for that statement (spec TSI-03 "File manifest": partial state).

_SQL_CREATE_WITH_TARGET = frozenset({
    "create_table", "create_view", "create_materialized_view",
    "create_function", "create_type",
})


def _rule_sql(node: Node, source: bytes) -> _RuleOutcome | None:
    node_type = node.type
    if node_type in _SQL_CREATE_WITH_TARGET:
        # Read the CREATE node's *direct* target only -- a reference deeper
        # in its body (e.g. a CREATE VIEW's FROM clause) is a separate,
        # non-direct object_reference the walk never classifies on its own.
        reference = _child_of_type(node, frozenset({"object_reference"}))
        if reference is None:
            return None
        name = reference.child_by_field_name("name") or _child_of_type(reference, frozenset({"identifier"}))
        candidate = _candidate(name, _TYPE)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    if node_type == "create_schema":
        name = _child_of_type(node, frozenset({"identifier"}))
        candidate = _candidate(name, _TYPE)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    return None


# -- C / C++ declarator chains (spec TSI-03 rule table rows "c", "cpp"). A
# C/C++ declaration's identifier can be arbitrarily wrapped by pointer,
# array, and function operators (`int (*factory(void))(int);`). The walk
# below follows the declarator chain from the owning declaration toward its
# leaf, classifying by the *first* derived operation encountered walking
# from the identifier outward -- i.e. the last operation the descent
# passes through before reaching the leaf identifier. Parentheses/attribute
# wrappers are transparent and never count as an operation. Typedef
# ownership overrides the derived-operation classification entirely. -------

_C_TRANSPARENT_WRAPPERS = frozenset({"parenthesized_declarator", "attributed_declarator"})
_C_FUNCTION_WRAPPERS = frozenset({"function_declarator"})
_C_VARIABLE_WRAPPERS = frozenset({"pointer_declarator", "array_declarator", "reference_declarator"})
_QUALIFIED_LEAF_TYPES = frozenset({"qualified_identifier"})
_C_SPECIFIER_TYPES = frozenset({"struct_specifier", "union_specifier", "enum_specifier"})
_C_DECLARATOR_OWNERS = frozenset({"declaration", "function_definition", "type_definition"})
_CPP_DECLARATOR_OWNERS = _C_DECLARATOR_OWNERS | {"field_declaration"}


def _flatten_qualified_name(node: Node, source: bytes) -> tuple[Node, str]:
    """Ruby's `scope_resolution` and C++'s `qualified_identifier` both
    nest `scope`/`name` fields outward-in; flatten to (leaf, dotted
    prefix) so the leaf alone becomes `name`/selection while the full
    qualification is preserved separately (spec TSI-03 "Resolve scope
    details": "Preserve explicit qualified components ... separately from
    the leaf identifier")."""
    prefix = ""
    while node is not None and node.type in _QUALIFIED_LEAF_TYPES:
        scope = node.child_by_field_name("scope")
        name = node.child_by_field_name("name")
        if scope is None or name is None:
            break
        prefix += _text(source, scope) + "."
        node = name
    return node, prefix


def _declarator_inner(node: Node) -> Node | None:
    inner = node.child_by_field_name("declarator")
    if inner is not None:
        return inner
    for child in node.children:
        if child.type not in ("(", ")"):
            return child
    return None


def _c_family_declarator_walk(
    declarator: Node, source: bytes, is_typedef: bool,
) -> tuple[Node, str, DescriptorKind] | None:
    node: Node | None = declarator
    first_op: str | None = None
    while node is not None:
        if node.type in _C_TRANSPARENT_WRAPPERS:
            node = _declarator_inner(node)
            continue
        if node.type in _C_FUNCTION_WRAPPERS:
            first_op = "function"
            node = node.child_by_field_name("declarator")
            continue
        if node.type in _C_VARIABLE_WRAPPERS:
            first_op = "variable"
            node = node.child_by_field_name("declarator")
            continue
        break
    if node is None:
        return None
    leaf, prefix = _flatten_qualified_name(node, source)
    if not _is_valid_identifier(leaf):
        return None
    if is_typedef:
        kind = _TYPE
    elif first_op == "function":
        kind = _METHOD
    else:
        return None
    return leaf, prefix, kind


def _c_specifier_rule(node: Node) -> _RuleOutcome | None:
    """A named type specifier with a body, or a standalone forward
    declaration, is a TYPE; a bare reference used inside another
    declaration's declarator (`struct S *p;`) is only a use (spec TSI-03
    rule table)."""
    name = node.child_by_field_name("name")
    if not _is_valid_identifier(name):
        return None
    if node.child_by_field_name("body") is not None:
        candidate = _candidate(name, _TYPE)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    parent = node.parent
    if parent is not None and parent.type == "declaration" and parent.child_by_field_name("declarator") is not None:
        return None
    candidate = _candidate(name, _TYPE)
    return _RuleOutcome(candidates=[candidate]) if candidate else None


def _c_declarator_owner_rule(node: Node, source: bytes) -> _RuleOutcome | None:
    declarator = node.child_by_field_name("declarator")
    if declarator is None:
        return None
    result = _c_family_declarator_walk(declarator, source, node.type == "type_definition")
    if result is None:
        return None
    leaf, prefix, kind = result
    candidate = _candidate(leaf, kind, prefix)
    return _RuleOutcome(candidates=[candidate]) if candidate else None


def _rule_c(node: Node, source: bytes) -> _RuleOutcome | None:
    if node.type in _C_SPECIFIER_TYPES:
        return _c_specifier_rule(node)
    if node.type in _C_DECLARATOR_OWNERS:
        return _c_declarator_owner_rule(node, source)
    return None


def _rule_cpp(node: Node, source: bytes) -> _RuleOutcome | None:
    node_type = node.type
    if node_type in _C_SPECIFIER_TYPES or node_type == "class_specifier":
        return _c_specifier_rule(node)
    if node_type in _CPP_DECLARATOR_OWNERS:
        return _c_declarator_owner_rule(node, source)
    if node_type == "namespace_definition":
        # Anonymous namespaces do not gain invented names (spec TSI-03 rule
        # table); they are context only.
        name = node.child_by_field_name("name")
        if not _is_valid_identifier(name):
            return _RuleOutcome()
        candidate = _candidate(name, _NAMESPACE)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    if node_type == "namespace_alias_definition":
        return _simple(node, "name", _NAMESPACE)
    if node_type == "alias_declaration":
        return _simple(node, "name", _TYPE)
    return None


# -- C# (spec TSI-03 rule table row "csharp"). -------------------------------

_CS_CALLABLE_VALUES = frozenset({"lambda_expression", "anonymous_method_expression"})


def _rule_csharp(node: Node, source: bytes) -> _RuleOutcome | None:
    node_type = node.type
    if node_type in ("class_declaration", "struct_declaration", "enum_declaration",
                      "interface_declaration", "record_declaration", "delegate_declaration"):
        return _simple(node, "name", _TYPE)
    if node_type in ("method_declaration", "constructor_declaration", "destructor_declaration",
                      "local_function_statement"):
        return _simple(node, "name", _METHOD)
    if node_type in ("namespace_declaration", "file_scoped_namespace_declaration"):
        # File-scoped namespace governs siblings (spec TSI-03 rule table).
        candidate = _candidate(node.child_by_field_name("name"), _NAMESPACE)
        if candidate is None:
            return None
        return _RuleOutcome(candidates=[candidate], is_header=node_type == "file_scoped_namespace_declaration")
    if node_type == "using_directive":
        # Namespace-versus-type target cannot be determined syntactically
        # for a using alias; use TERM (spec TSI-03 "Kind for C# using
        # aliases").
        candidate = _candidate(node.child_by_field_name("name"), _TERM)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    if node_type == "variable_declarator":
        # Callable initializer is an unfielded expression in
        # variable_declarator (spec TSI-03 rule table).
        name = node.child_by_field_name("name")
        value = None
        for index, child in enumerate(node.children):
            if node.field_name_for_child(index) is None and child.type in _CS_CALLABLE_VALUES:
                value = child
                break
        if name is None or value is None:
            return None
        candidate = _candidate(name, _METHOD)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    return None


# -- PHP (spec TSI-03 rule table row "php"). ---------------------------------

_PHP_CALLABLE_VALUES = frozenset({"anonymous_function", "arrow_function"})


def _rule_php(node: Node, source: bytes) -> _RuleOutcome | None:
    node_type = node.type
    if node_type in ("class_declaration", "interface_declaration", "trait_declaration", "enum_declaration"):
        return _simple(node, "name", _TYPE)
    if node_type in ("function_definition", "method_declaration"):
        return _simple(node, "name", _METHOD)
    if node_type == "namespace_definition":
        # Braced versus semicolon namespaces (spec TSI-03 rule table): the
        # semicolon form governs siblings like a header.
        name = node.child_by_field_name("name")
        if not _is_valid_identifier(name):
            return None
        leaf = _child_of_type(name, frozenset({"name"})) or name
        candidate = _candidate(leaf, _NAMESPACE)
        if candidate is None:
            return None
        has_body = node.child_by_field_name("body") is not None
        return _RuleOutcome(candidates=[candidate], is_header=not has_body)
    if node_type == "assignment_expression":
        # Callable assignment selects the variable's inner name (without
        # `$`), only for direct anonymous-function/arrow values (spec
        # TSI-03 rule table).
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is None or left.type != "variable_name" or right is None or right.type not in _PHP_CALLABLE_VALUES:
            return None
        identifier = _child_of_type(left, frozenset({"name"}))
        candidate = _candidate(identifier, _METHOD)
        return _RuleOutcome(candidates=[candidate]) if candidate else None
    return None


# language -> rule function; internal dispatch table, deliberately
# untyped beyond dict[str, ...] to avoid importing Callable just for an
# internal detail.
_DISPATCH = {
    "python": _rule_python,
    "javascript": _rule_javascript,
    "typescript": _rule_typescript,
    "tsx": _rule_typescript,
    "java": _rule_java,
    "kotlin": _rule_kotlin,
    "swift": _rule_swift,
    "go": _rule_go,
    "ruby": _rule_ruby,
    "rust": _rule_rust,
    "c": _rule_c,
    "cpp": _rule_cpp,
    "csharp": _rule_csharp,
    "php": _rule_php,
    "scala": _rule_scala,
    "bash": _rule_bash,
    "sql": _rule_sql,
}


class _Frame:
    """One sibling-iteration context on the explicit walk stack. `qual_prefix`
    and `parent_id` are the qualification text and nearest-emitted-symbol id
    in effect for `children`; a header candidate mutates them in place so
    later siblings in the *same* frame inherit the change (spec TSI-03
    "Resolve scope details"). `extension` is a pending (start_byte, end_byte)
    declaration-span extension from an unwrapped decorator/export/template
    wrapper, propagated through non-candidate pass-through frames and
    consumed by the next candidate found at this level."""

    __slots__ = ("children", "index", "qual_prefix", "parent_id", "extension")

    def __init__(self, children: list[Node], qual_prefix: str, parent_id: str | None,
                 extension: tuple[int, int] | None) -> None:
        self.children = children
        self.index = 0
        self.qual_prefix = qual_prefix
        self.parent_id = parent_id
        self.extension = extension


def _node_span(node: Node) -> Span:
    return Span(
        start_byte=node.start_byte, end_byte=node.end_byte,
        start_line=node.start_point.row, start_character=node.start_point.column,
        end_line=node.end_point.row, end_character=node.end_point.column,
    )


def _walk_declarations(language: str, root: Node, source: bytes,
                        file_path: str, file_hash: str) -> list[SyntaxSymbol]:
    dispatch = _DISPATCH.get(language)
    symbols: list[SyntaxSymbol] = []
    stack = [_Frame([root], "", None, None)]
    while stack:
        frame = stack[-1]
        if frame.index >= len(frame.children):
            stack.pop()
            continue
        child = frame.children[frame.index]
        frame.index += 1

        # Unwrap decorator/export/template wrappers before dispatch ever
        # sees the node, so the real declaration is classified exactly
        # once (spec TSI-03: "Prevent a wrapped inner declaration from
        # being emitted twice").
        node = child
        outer_start, outer_end = node.start_byte, node.end_byte
        unwrapped = False
        while True:
            inner = _unwrap(language, node)
            if inner is None:
                break
            unwrapped = True
            node = inner
            outer_start = min(outer_start, node.start_byte)
            outer_end = max(outer_end, node.end_byte)
        wrapper_extension = (outer_start, outer_end) if unwrapped else None

        pending = frame.extension
        if pending is not None and wrapper_extension is not None:
            effective_extension = (min(pending[0], wrapper_extension[0]),
                                    max(pending[1], wrapper_extension[1]))
        else:
            effective_extension = wrapper_extension if wrapper_extension is not None else pending

        outcome = dispatch(node, source) if dispatch is not None else None

        if outcome is None:
            # Ordinary structural node: descend unchanged, propagating any
            # pending wrapper extension to this level's direct children.
            stack.append(_Frame(list(node.children), frame.qual_prefix, frame.parent_id, effective_extension))
            continue

        if not outcome.candidates:
            # Context-only node (unnamed companion, extension/impl/
            # singleton-class context): no symbol, but children may still
            # get extra source-spelled qualification (spec TSI-03
            # "Resolve scope details").
            new_prefix = frame.qual_prefix + outcome.qualifier_extension
            stack.append(_Frame(list(node.children), new_prefix, frame.parent_id, effective_extension))
            continue

        candidate = outcome.candidates[0]
        name = _text(source, candidate.identifier)
        qualified_name = frame.qual_prefix + candidate.qualifier_prefix + name
        selection = _node_span(candidate.identifier)
        decl_start, decl_end = node.start_byte, node.end_byte
        if effective_extension is not None:
            decl_start = min(decl_start, effective_extension[0])
            decl_end = max(decl_end, effective_extension[1])
        declaration = Span(
            start_byte=decl_start, end_byte=decl_end,
            start_line=node.start_point.row, start_character=node.start_point.column,
            end_line=node.end_point.row, end_character=node.end_point.column,
        )
        symbol_id = syntax_id(file_path, file_hash, selection, candidate.kind)
        symbols.append(SyntaxSymbol(
            symbol=symbol_id, file_path=file_path, name=name, qualified_name=qualified_name, kind=candidate.kind,
            parent_symbol=frame.parent_id, declaration=declaration, selection=selection,
        ))

        if outcome.is_header:
            frame.qual_prefix = qualified_name + "."
            frame.parent_id = symbol_id
        else:
            stack.append(_Frame(list(node.children), qualified_name + ".", symbol_id, None))

    return symbols


def extract_file(file_path: str, source: bytes, language: str, *,
                  pool: ParserPool, tree: Tree | None = None) -> ParsedSyntax:
    """Extract named declarations from one captured file (spec TSI-03
    "Declaration contract"). Grammar/runtime dependency failures raise
    (`SyntaxDependencyError`/`ValueError`) rather than degrading to a
    successful-looking empty result. Invalid UTF-8 is a file outcome, not a
    partial parse: it returns `state="failed"` with no tree and no symbols,
    decoded strictly with no `errors="replace"` fallback. `tree` is an
    ephemeral parse handle for the current worker/callback -- callers must
    not persist it."""
    identity = grammar_identity(language)  # raises for missing/invalid grammars
    file_hash = hashlib.sha256(source).hexdigest()
    try:
        source.decode("utf-8")
    except UnicodeDecodeError as error:
        return ParsedSyntax(
            file_path=file_path, file_hash=file_hash, language=language,
            parser_identity=identity, state="failed", reason=str(error),
            symbols=(), tree=None,
        )
    if tree is None:
        tree = pool.parse(language, source)
    symbols = tuple(_walk_declarations(language, tree.root_node, source, file_path, file_hash))
    if tree.root_node.has_error:
        state, reason = "partial", "grammar produced error or missing nodes"
    else:
        state, reason = "parsed", None
    return ParsedSyntax(
        file_path=file_path, file_hash=file_hash, language=language,
        parser_identity=identity, state=state, reason=reason,
        symbols=symbols, tree=tree,
    )
