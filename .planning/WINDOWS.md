---
schema_version: 1
open_count: 2
waived_count: 0
fixed_count: 0
total_count: 2
last_updated: 2026-08-21T18:50:12.537Z
---

# Broken Windows Ledger

> Cross-phase defect register. With `workflow.windows_enforce` enabled, `/gsd-ship` blocks while `open_count > 0`.
> Waive with `gsd-tools windows waive <id> "<reason>"` (reason required).
> Mark fixed with `gsd-tools windows fixed <id>`.

| id | phase | kind | file | line | description | status | reason | recorded_at | resolved_at |
|----|-------|------|------|------|-------------|--------|--------|-------------|-------------|
| 1 | 02 | unrun-verify | .github/workflows/setup-smoke.yml |  | Post-push setup-smoke CI proof of scip-swift resolution+floor+digest (SWFT-01 flagged assumption A1) deferred to Plan 02-03's CI step | open |  | 2026-08-21T18:32:29.542Z |  |
| 2 | 02 | unrun-verify | .github/workflows/setup-smoke.yml |  | First real CI run of the new macOS-leg Swift index smoke step is pending (local structural + verbatim simulation passed; runner environment A1 unexercised) — resolves flagged assumption A1 / SWFT-01 CI half | open |  | 2026-08-21T18:50:12.537Z |  |

````json
[
  {
    "id": 1,
    "kind": "unrun-verify",
    "phase": "02",
    "file": ".github/workflows/setup-smoke.yml",
    "line": null,
    "description": "Post-push setup-smoke CI proof of scip-swift resolution+floor+digest (SWFT-01 flagged assumption A1) deferred to Plan 02-03's CI step",
    "status": "open",
    "reason": "",
    "recorded_at": "2026-08-21T18:32:29.542Z",
    "resolved_at": null
  },
  {
    "id": 2,
    "kind": "unrun-verify",
    "phase": "02",
    "file": ".github/workflows/setup-smoke.yml",
    "line": null,
    "description": "First real CI run of the new macOS-leg Swift index smoke step is pending (local structural + verbatim simulation passed; runner environment A1 unexercised) — resolves flagged assumption A1 / SWFT-01 CI half",
    "status": "open",
    "reason": "",
    "recorded_at": "2026-08-21T18:50:12.537Z",
    "resolved_at": null
  }
]
````
