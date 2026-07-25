---
title: 'codeintel Phase 0-3: Personal Code Intelligence MCP Server'
description: >-
  Local-first MCP stdio server with SCIP navigation, Zoekt search, indexer CLI,
  and dependency graph — vendored from the source project
status: pending
priority: P2
branch: ''
tags:
  - mcp
  - scip
  - zoekt
  - tdd
blockedBy: []
blocks: []
created: '2026-07-24T16:27:21.431Z'
createdBy: 'ck:plan'
source: skill
---

# codeintel Phase 0-3: Personal Code Intelligence MCP Server

## Overview

Greenfield build of `codeintel`: single-process, local-first MCP stdio server exposing 8 tools (searchCode, documentSymbols, goToDefinition, findReferences, callHierarchy, typeHierarchy, getIndexStatus, blastRadius) backed by SCIP SQLite indexes + embedded Zoekt. Core modules vendored near-verbatim from `the source project` (path below). TDD mode: every phase ports/writes tests before implementation.

**Context:**
- Brainstorm report (decisions + scout evidence): `../reports/brainstorm-0724-2316-codeintel-phase0-3-implementation-report.md`
- Source plan: `<source-plan>`
- Architecture diagrams: `../../docs/assets/codeintel-system-architecture.png`
- Vendor source (`$SOURCE_REPO` in phase files): `<source-project>`

**Key decisions (approved):** Phase 0-3 scope; Zoekt embedded (full toolchain installed); deps = `mcp[cli]` + `protobuf` + `zstandard` + `httpx` + `watchdog` (NO SQLAlchemy/aiosqlite — source core is stdlib sqlite3); argparse CLI; validate on a TS validation repo (TS) + the source project (Python); register user-scope in Claude Code.

**Out of scope:** cloud deploy (plan Phase 4), stats/dashboard endpoints, monorepo language-merge polish.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Scaffold and Vendor Core](./phase-01-scaffold-and-vendor-core.md) | Completed |
| 2 | [MCP Server and SCIP Navigation](./phase-02-mcp-server-and-scip-navigation.md) | Completed |
| 3 | [Indexer CLI and Zoekt Search](./phase-03-indexer-cli-and-zoekt-search.md) | Completed |
| 4 | [Graph Blast Radius and Auto-Reindex](./phase-04-graph-blast-radius-and-auto-reindex.md) | Completed |

## Acceptance (whole plan)

- From Claude Code (user-scope MCP), on a TS validation repo + the source project: all 8 tools return correct results; nav results hand-verified on known symbols.
- `codeintel index .` end-to-end: detect language → scip-* → `scip expt-convert` → zoekt-index → atomic pointer swap → registry update.
- `getIndexStatus` flags stale after a new commit; reindex has zero query downtime.
- `uv run pytest` green at every phase gate.

## Dependencies

None (no other unfinished plans in scope).
