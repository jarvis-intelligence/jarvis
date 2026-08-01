"""codeintel MCP stdio server: thin tool wrappers around QueryService, the
Zoekt search client, and the package dependency graph.

Registers 9 tools: documentSymbols, goToDefinition, findReferences,
callHierarchy, typeHierarchy, getIndexStatus, searchCode, semanticSearch, blastRadius.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from codeintel import config
from codeintel.graph import GraphStore, blast_radius
from codeintel.index_reader import IndexNotFoundError
from codeintel.query import FreshnessSnapshot, QueryService
from codeintel.registry import SEARCH_ONLY_STATUS
from codeintel.search import ZoektLifecycle, search_zoekt

mcp = FastMCP("codeintel")
_query_service: QueryService | None = None
_zoekt_lifecycle: ZoektLifecycle | None = None
_graph_store: GraphStore | None = None


def _service() -> QueryService:
    global _query_service
    if _query_service is None:
        _query_service = QueryService(config.new_connection_cache())
    return _query_service


def _zoekt() -> ZoektLifecycle:
    global _zoekt_lifecycle
    if _zoekt_lifecycle is None:
        data_dir = config.data_dir()
        _zoekt_lifecycle = ZoektLifecycle(index_dir=data_dir / ".zoekt", data_dir=data_dir)
    return _zoekt_lifecycle


def _graph() -> GraphStore:
    global _graph_store
    if _graph_store is None:
        _graph_store = GraphStore(config.data_dir() / "registry.db")
    return _graph_store


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _freshness_fields(snapshot: FreshnessSnapshot) -> dict[str, Any]:
    return _json_safe(asdict(snapshot))


def _registry_status(repo: str) -> str | None:
    """Best-effort registry lookup for error messaging only. Any failure
    returns None so a broken registry degrades the message rather than
    replacing one error with another."""
    try:
        from codeintel.registry import Registry

        registry = Registry(config.data_dir() / "registry.db")
        try:
            entry = registry.get(repo)
        finally:
            registry.close()
    except Exception:
        return None
    return entry.status if entry is not None else None


def _error_payload(repo: str, exc: Exception) -> dict[str, Any]:
    """Turn a missing SCIP index into an explanation when the repo was
    deliberately published search-only. Every other error passes through
    unchanged, so this never hides a real fault."""
    if isinstance(exc, IndexNotFoundError) and _registry_status(repo) == SEARCH_ONLY_STATUS:
        return {
            "error": (
                f"{repo} is indexed search-only: it has no SCIP index, so navigation "
                "tools cannot answer. searchCode and semanticSearch do work on it. "
                "This happens when the language's indexer cannot build the repo — "
                "for example an Android/Gradle project."
            )
        }
    return {"error": str(exc)}


@mcp.tool(name="documentSymbols")
def document_symbols(repo: str, path: str) -> dict[str, Any]:
    """List every top-level symbol (with its range) defined in `path` within `repo`."""
    try:
        entries, freshness = _service().get_document_symbols(repo, path)
    except Exception as exc:
        # Broad on purpose — keeps every tool's error shape the same {"error": ...} dict.
        return _error_payload(repo, exc)
    return {"path": path, "symbols": [_json_safe(asdict(e)) for e in entries], **_freshness_fields(freshness)}


@mcp.tool(name="goToDefinition")
def go_to_definition(repo: str, symbol: str) -> dict[str, Any]:
    """Resolve `symbol`'s definition location(s) within `repo`."""
    try:
        locations, freshness = _service().get_definitions(repo, symbol)
    except Exception as exc:
        # Broad on purpose — keeps every tool's error shape the same {"error": ...} dict.
        return _error_payload(repo, exc)
    return {"symbol": symbol, "definitions": [_json_safe(asdict(loc)) for loc in locations], **_freshness_fields(freshness)}


@mcp.tool(name="findReferences")
def find_references(repo: str, symbol: str) -> dict[str, Any]:
    """Every occurrence of `symbol` within `repo`, definition sites included."""
    try:
        locations, freshness = _service().find_references(repo, symbol)
    except Exception as exc:
        # Broad on purpose — keeps every tool's error shape the same {"error": ...} dict.
        return _error_payload(repo, exc)
    return {"symbol": symbol, "references": [_json_safe(asdict(loc)) for loc in locations], **_freshness_fields(freshness)}


@mcp.tool(name="callHierarchy")
def call_hierarchy(repo: str, symbol: str) -> dict[str, Any]:
    """Single-level incoming/outgoing call hierarchy for `symbol` within `repo`."""
    try:
        incoming, outgoing, freshness = _service().call_hierarchy(repo, symbol)
    except Exception as exc:
        # Broad on purpose — keeps every tool's error shape the same {"error": ...} dict.
        return _error_payload(repo, exc)
    return {
        "symbol": symbol,
        "incomingCalls": [_json_safe(asdict(e)) for e in incoming],
        "outgoingCalls": [_json_safe(asdict(e)) for e in outgoing],
        **_freshness_fields(freshness),
    }


@mcp.tool(name="typeHierarchy")
def type_hierarchy(repo: str, symbol: str) -> dict[str, Any]:
    """Single-level super/subtypes for `symbol` within `repo`.

    Returns an explicit error when the index carries no relationship data —
    `scip expt-convert` does not populate `global_symbols.relationships`, so
    an empty result would wrongly imply the symbol has no supertypes."""
    try:
        supertypes, subtypes, freshness, available = _service().type_hierarchy(repo, symbol)
    except Exception as exc:
        # Broad on purpose — keeps every tool's error shape the same {"error": ...} dict.
        return _error_payload(repo, exc)
    if not available:
        return {
            "error": (
                "typeHierarchy unavailable for this index: no symbol carries relationship "
                "data. `scip expt-convert` declares global_symbols.relationships but never "
                "writes it, so super/subtypes cannot be determined. This is an upstream "
                "converter limitation, not a missing symbol — do not read it as "
                "'this type has no supertypes'."
            ),
            "symbol": symbol,
            **_freshness_fields(freshness),
        }
    return {
        "symbol": symbol,
        "supertypes": [_json_safe(asdict(e)) for e in supertypes],
        "subtypes": [_json_safe(asdict(e)) for e in subtypes],
        **_freshness_fields(freshness),
    }


@mcp.tool(name="getIndexStatus")
def get_index_status(repo: str, repo_path: str | None = None) -> dict[str, Any]:
    """Whether `repo` has a published index, and its freshness. Pass
    `repo_path` (the repo's local git working directory) to compare the
    published commit against `git rev-parse HEAD`; omitted, freshness is
    reported without a staleness comparison."""
    try:
        indexed, freshness = _service().get_index_status(repo, repo_path)
    except Exception as exc:
        return {"error": str(exc)}
    return {"repo": repo, "indexed": indexed, "status": _registry_status(repo),
            **_freshness_fields(freshness)}


@mcp.tool(name="searchCode")
def search_code(query: str, repo: str | None = None) -> dict[str, Any]:
    """Lexical code search via an embedded Zoekt index (lazy-started on
    first call). `repo`, if given, is applied as a Zoekt `r:` query filter
    scoping results to that one indexed repo; omitted, results span every
    indexed repo."""
    scoped_query = f"r:{repo} {query}" if repo else query
    try:
        base_url = _zoekt().ensure_running()
        hits = search_zoekt(base_url, scoped_query)
    except Exception as exc:
        # Broad on purpose — keeps every tool's error shape the same {"error": ...} dict.
        return {"error": str(exc)}
    return {
        "query": query,
        "hits": [
            {"repo": hit.repo, "path": hit.path, "lineNumber": hit.line_number, "lineText": hit.line_text}
            for hit in hits
        ],
        "total": len(hits),
    }


def _zoekt_base_url_or_none() -> str | None:
    """Hybrid search wants Zoekt but must not require it — a Zoekt spawn
    failure degrades semanticSearch to vector-only rather than erroring."""
    try:
        return _zoekt().ensure_running()
    except Exception:
        return None


@mcp.tool(name="semanticSearch")
def semantic_search_tool(repo: str, query: str, limit: int = 10) -> dict[str, Any]:
    """Natural-language code search over `repo`: embeds `query`, retrieves
    top vector matches from the repo's semantic index, fuses them with
    Zoekt lexical hits via reciprocal rank fusion. Requires the repo to
    have been indexed with the `semantic` extra installed."""
    from codeintel import semantic  # deferred: tool must exist even without the extra

    try:
        return semantic.semantic_search(repo, query, limit,
                                        zoekt_base_url=_zoekt_base_url_or_none())
    except Exception as exc:
        # Broad on purpose — keeps every tool's error shape the same {"error": ...} dict.
        return {"error": str(exc)}


@mcp.tool(name="blastRadius")
def blast_radius_tool(repo: str, symbol_or_package: str) -> dict[str, Any]:
    """2-hop bounded BFS over the package dependency graph: every other
    indexed repo whose package directly (1 hop) or transitively through one
    intermediary (2 hops) depends on `symbol_or_package` as registered for
    `repo` (built by `codeintel index`, e.g. `"npm:@scope/name"`). The
    graph has no per-node timestamp, so freshness is always reported as
    `unknown` here — an honest limitation, not a bug."""
    try:
        result = blast_radius(_graph(), repo, symbol_or_package)
    except Exception as exc:
        # Broad on purpose — keeps every tool's error shape the same {"error": ...} dict.
        return {"error": str(exc)}
    return {
        "repo": repo,
        "symbolOrPackage": symbol_or_package,
        "dependents": [_json_safe(asdict(d)) for d in result.dependents],
        **_freshness_fields(result.freshness),
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
