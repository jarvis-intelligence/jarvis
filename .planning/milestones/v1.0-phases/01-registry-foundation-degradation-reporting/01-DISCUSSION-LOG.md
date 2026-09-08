# Phase 1: Registry Foundation & Degradation Reporting - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-08-21
**Phase:** 1-Registry Foundation & Degradation Reporting
**Areas discussed:** Persisted failure record shape, Failed-run registry behavior, Recovery command mapping, Capability field shape

---

## Persisted failure record shape

### Origin taxonomy representation

| Option | Description | Selected |
|--------|-------------|----------|
| String slugs | TEXT column with short slugs ('failed_hard', 'signature', 'manual'), 'degraded' reserved for Phase 3; readable from sqlite3 CLI; additive | ✓ |
| Numeric codes | INTEGER codes + Python enum/mapping; compact but needs lookup and has renumbering risk | |
| Free text only | Richer `status` text only; simplest but origin becomes string-parsing | |

**User's choice:** String slugs
**Notes:** Matches existing TEXT `status` column style.

### Cause detail persisted

| Option | Description | Selected |
|--------|-------------|----------|
| Reason + stderr tail | Classified reason + truncated raw tail (~2KB); bounded size | |
| Classified reason only | Human-readable reason, nothing raw; smallest footprint | |
| Full stderr | Complete indexer stderr, unbounded; max fidelity, multi-MB rows possible on watch loops | ✓ |

**User's choice:** Full stderr
**Notes:** User explicitly chose full fidelity over the recommended capped tail; overwrite churn accepted.

### Cause column composition

| Option | Description | Selected |
|--------|-------------|----------|
| Summary + raw column | `status_reason` one-liner + second column with complete raw stderr | ✓ |
| Raw only | Single column with full stderr; display truncates | |

**User's choice:** Summary + raw column

### Clearing on success

| Option | Description | Selected |
|--------|-------------|----------|
| Clear on success | Next successful index NULLs origin/reason/stderr; registry reflects latest run | ✓ |
| Keep as last_failure | Move to last_failure_* columns; history at cost of columns (NAVS-02 telemetry is v2) | |
| Manual clear only | Persist until `jarvis forget`; stale causes after clean recovery | |

**User's choice:** Clear on success

---

## Failed-run registry behavior

### Failed first index

| Option | Description | Selected |
|--------|-------------|----------|
| Create failed row | Row with failed_hard origin + cause + path/language; visible in list/status; reindex works | ✓ |
| No row (status quo) | Nothing persisted until first success; only the index-time CLI error | |

**User's choice:** Create failed row

### Reindex failure of registered repo

| Option | Description | Selected |
|--------|-------------|----------|
| Mark failed, keep index facts | Overwrite status + failure fields, keep last-good commit_sha/last_indexed | |
| Full overwrite | commit_sha/last_indexed/status all reflect the failed attempt | ✓ |
| Leave row untouched | Status quo; failure visible only in index-run output | |

**User's choice:** Full overwrite

### Stale index under failed status

| Option | Description | Selected |
|--------|-------------|----------|
| Serve stale index | Nav keeps serving last published index; status reports failed-run AND stale-navigation facts; capability truth from on-disk pointer | ✓ |
| Refuse when failed | Nav tools return failure payload even though an old index exists; discards working index | |

**User's choice:** Serve stale index

### `jarvis list` display

| Option | Description | Selected |
|--------|-------------|----------|
| Status markers in list | ✗ failed / ◐ search-only / ✓ ok markers + reason one-liner | ✓ |
| Keep list minimal | Name+language only; detail only in `jarvis status <slug>` | |

**User's choice:** Status markers in list

---

## Recovery command mapping

### Recovery derivation source

| Option | Description | Selected |
|--------|-------------|----------|
| Derived from origin | Per-origin mapping in code; single source of truth; Phase 3 adds one entry | ✓ |
| Persisted per-row | Recovery string stored at failure time; survives code changes but goes stale | |

**User's choice:** Derived from origin

### Manual search-only recovery

| Option | Description | Selected |
|--------|-------------|----------|
| forget + reindex | Report `jarvis forget <slug>` then `jarvis index <path>`; honest under current one-way contract | ✓ |
| reindex (aspirational) | Report `jarvis reindex <slug>` though it keeps search_only=1 today | |
| Prose without command | Explain the escape in prose; least rot, not copy-pasteable | |

**User's choice:** forget + reindex

### Signature remedy reuse

| Option | Description | Selected |
|--------|-------------|----------|
| Reuse remedy texts | Per-signature remedies (Kotlin 2.2.0, bash ≥4.4) feed status cause/recovery | |
| Generic reindex only | One generic recovery for all signature rows: `jarvis reindex <slug>` after toolchain fix | ✓ |

**User's choice:** Generic reindex only

### Hard-failure recovery

| Option | Description | Selected |
|--------|-------------|----------|
| reindex + status pointer | `jarvis reindex <slug>` + point to `jarvis status` for full stderr | |
| Full index command | Report the original `jarvis index <path>` invocation | ✓ |

**User's choice:** Full index command

---

## Capability field shape

### getIndexStatus structure

| Option | Description | Selected |
|--------|-------------|----------|
| Nested per-capability | `capabilities: {navigation: {available, reason, recovery}, search, semantic}`; branchable without prose | ✓ |
| Flat single pair | Top-level `navigation_available`/`reason`/`recovery`; flatter but can't express search/semantic distinctly | |

**User's choice:** Nested per-capability

### Nav-tool error payload

| Option | Description | Selected |
|--------|-------------|----------|
| Structured + prose | Additive `state`/`cause`/`recovery` keys alongside existing `error` string | ✓ |
| Prose only | Single sentence string; no schema growth, parse-only for agents | |

**User's choice:** Structured + prose

### Run-outcome vs capability layering

| Option | Description | Selected |
|--------|-------------|----------|
| Orthogonal layers | Top-level `last_index_run: {outcome, origin, reason, recovery}` + `capabilities.*` reporting on-disk truth | ✓ |
| Folded into reason | One reason string carries the whole story; run outcome not machine-branchable | |

**User's choice:** Orthogonal layers

---

## Claude's Discretion

- Exact column names within locked shapes (suggestions: `status_origin`/`status_reason`/`status_stderr`)
- Marker glyphs and list-column layout
- Key naming/casing within locked payload shapes
- `capabilities.semantic` reporting when the extra isn't installed

## Deferred Ideas

None — discussion stayed within phase scope.
