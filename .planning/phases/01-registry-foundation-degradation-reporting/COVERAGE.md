# API Coverage — Phase 1: Registry Foundation & Degradation Reporting

**Checkpoint:** API Coverage Decision Checkpoint (plan-phase, 2026-08-21)
**Detection result:** No external API / SDK / service integration in this phase's scope.

This phase integrates no external API: all work is internal — raw `sqlite3` registry schema extension, CLI output formatting in `index_cli.py`, and additive keys on the existing FastMCP stdio tool payloads in `server.py`. The only subprocess interaction (SCIP indexers) already exists and is unchanged; no new client SDKs, HTTP endpoints, or third-party services are introduced, so there are no capabilities to cover or opt out of.

**Package Legitimacy Audit:** zero external packages installed this phase (stdlib + already-pinned deps only, per PROJECT.md "no new runtime deps without discussion") — nothing to audit, no blocking checkpoints required.
