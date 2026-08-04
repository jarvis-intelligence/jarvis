"""Plain dataclasses for the nav-tool result shapes.

Trimmed from an internal reference implementation's pydantic `models.py` (that file also
carries FastAPI/registration/stats/blast-radius models jarvis doesn't
need). Dataclasses instead of pydantic here: MCP tool functions in
`server.py` return dicts built from these directly (`dataclasses.asdict`),
and jarvis has no HTTP layer that would benefit from pydantic validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Freshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Position:
    line: int
    character: int


@dataclass(frozen=True)
class Range:
    start: Position
    end: Position


@dataclass(frozen=True)
class Location:
    path: str
    range: Range


@dataclass(frozen=True)
class SymbolInfo:
    symbol: str
    displayName: str | None = None
    kind: str | None = None


@dataclass(frozen=True)
class DocumentSymbolEntry:
    symbol: str
    displayName: str | None
    kind: str | None
    range: Range


@dataclass(frozen=True)
class CallHierarchyEntry:
    symbol: SymbolInfo
    location: Location


@dataclass(frozen=True)
class TypeHierarchyEntry:
    symbol: SymbolInfo
    location: Location
