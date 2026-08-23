# API Coverage — Phase 5: Semantic Install Onboarding

**Checkpoint:** API Coverage Decision Checkpoint (plan-phase, 2026-08-23)
**Detection result:** probe (`bin/lib/api-coverage.cjs --json`, run on the concatenated phase scope) detected **no external API surface**.

No external API integration: only network action is the user-consented `uv pip install jarvis-mcp[semantic]` subprocess — the project's own PyPI distribution, fixed argv, no HTTP client in jarvis code; probe detected none.

## Disposition Notes

| Item | Reason |
|------|--------|
| `uv pip install --python <sys.executable> jarvis-mcp[semantic]` (runtime, consented) | Package-manager invocation of the project's own trusted-publisher distribution — a package legitimacy question, not an API integration. Audit verdict in 05-RESEARCH.md: **Approved (self-distribution, fixed argv, no shell)**; no `[ASSUMED]`/`[SUS]` packages, no blocking checkpoints |
| `importlib.util.find_spec` / `importlib.invalidate_caches` / `shutil.which` / `subprocess.run` | stdlib only; no new dependencies, no `pyproject.toml` change (05-RESEARCH Standard Stack) |
| watch/MCP/reindex paths | Touched by zero code this phase — SEMA-02 is a structural property plus regression pins (05-RESEARCH SEMA-02 Structural Proof) |
