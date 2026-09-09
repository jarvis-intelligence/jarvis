"""Curated offline tree-sitter grammar provider.

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
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Parser, Tree

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
