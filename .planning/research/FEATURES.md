# Feature Research

**Domain:** Local-first code-intelligence tooling — indexing failure fallback, degraded-mode reporting, optional-dependency onboarding
**Researched:** 2026-08-16
**Confidence:** MEDIUM (comparator behavior cross-checked across docs/issues; scip-swift version verified empirically via `gh release list` — HIGH)

## How Comparable Tools Behave (context for categorization)

- **Sourcegraph** is the closest analogue: precise (SCIP) navigation is used when an index
  exists, and it **automatically supplements with search-based navigation** per-file when it
  doesn't — including mixed states where the repo has an index but a dependency doesn't. The UI
  labels results as *precise* vs *search-based*, so degradation is visible, never silent.
- **GitHub code navigation** ships precise (stack-graphs) per-language and falls back to fuzzy
  search-based navigation everywhere else. Fallback is the default posture of the whole product,
  not an error state.
- **clangd** with no `compile_commands.json` degrades to a heuristic "fallback command" per
  file and logs it; it keeps serving what it can. **rust-analyzer** surfaces "Failed to load
  workspace" as an explicit error carrying the underlying `cargo metadata` stderr verbatim, and
  keeps running with degraded analysis. Pattern in both: *serve what's servable, surface the
  raw cause, never pretend full health*.
- **Doctor-pattern CLIs** (`flutter doctor`, `brew doctor`): per-capability sections, pass/fail
  markers, and — critically — **every failure paired with the exact remediation command**.
- **CLI prompting norms** (clig.dev, gh, cargo, pip, uv, npm): prompt only when stdin is a TTY,
  never in CI/pipelines, always provide a flag/env non-interactive equivalent, and print an
  actionable hint when skipping the prompt. Package managers do **not** auto-prompt to install
  optional extras — they hint and let the user act; jarvis prompting at all is already ahead of
  the ecosystem norm. Homebrew's first-run analytics consent (asked once, persisted) is the
  reference pattern for remembering a decline; gh's prompt-less telemetry default is the
  cautionary counter-example.

Key divergence to note: Sourcegraph/GitHub degrade **per-query at read time** (they always have
search as a substrate), while jarvis degrades **at publish time** (a failed SCIP build decides
what exists on disk). That's why jarvis's opt-in + self-healing design is the right adaptation
rather than a deviation: publish-time degradation is stickier, so it must be consented-to and
must retry.

## Feature Landscape

### Table Stakes (Users Expect These)

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Something publishes when SCIP fails (search-only fallback) | Sourcegraph/GitHub never leave a repo with *nothing*; nav failure zeroing out search violates the universal "serve what's servable" norm — this is the motivating gap (first-time Swift index fails → no Zoekt either) | MEDIUM | Reuses existing `_publish_search_only` path; new part is the opt-in gate + distinct registry state |
| Degradation is visible, never silent | Sourcegraph labels precise vs search-based; clangd logs fallback; rust-analyzer shows the error. A repo quietly serving search-only would be read as "jarvis is broken/inaccurate" | LOW | Nav tools already return `{"error": ...}` in search-only mode; extend message with cause + recovery |
| Status reports the *cause* verbatim + a recovery command | rust-analyzer carries cargo stderr; flutter/brew doctor pair every ✗ with the fix command. "degraded" without why/how is a support burden | MEDIUM | Persist the matched signature / indexer stderr excerpt in registry; `jarvis status` + `getIndexStatus` render it with "run `jarvis reindex <slug>`" style remediation |
| No prompts outside a TTY | clig.dev hard rule; every surveyed CLI checks `isatty` and CI env. A blocking prompt under `watch` or MCP-triggered reindex would hang the pipeline | LOW | Gate on `sys.stdin.isatty() and sys.stdout.isatty()`; keep today's stderr hint on the non-TTY path |
| Non-interactive equivalent for every interactive choice | clig.dev: prompts are conveniences, flags/env are the contract | LOW | Already designed: `--fallback` flag + env var; semantic install remains achievable via `uv sync --extra semantic` |
| Known-failure signatures degrade automatically | Users expect known-unfixable cases (like existing Kotlin/AGP) to not require ceremony; scip-swift is the most failure-prone indexer | LOW–MEDIUM | Extends `_SEARCH_ONLY_SIGNATURES`; the work is *collecting* trustworthy signatures for scip-swift v0.2.1, not the mechanism. Signatures must be verified against the new version's actual stderr, not v0.1.2's |
| Indexer pin kept current (scip-swift v0.1.2 → v0.2.1) | Stale pinned toolchains rot; v0.2.1 released 2026-08-15 (verified via `gh release list --repo jarvis-intelligence/scip-swift`) | LOW–MEDIUM | Bump `setup.sh:48`; risk is CLI-surface drift vs `_swift_indexer_cmd` and *changed stderr wording* invalidating planned signatures — verify both together |

### Differentiators (Competitive Advantage)

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Self-healing fallback (retry full build every reindex) | No surveyed tool does this at publish time. Sourcegraph's fallback is stateless per-query; jarvis's persisted `search_only=1` is a one-way trap. Auto-retry that re-promotes to full nav when the build heals is genuinely better than both | MEDIUM | Requires new additive registry state (`degraded` distinct from `search_only`); reindex/watch must attempt full build first, fall back again only on fresh failure |
| Opt-in degradation (flag + env), not default | Preserves "fail loudly" for transient breaks/missing binaries — the exact failure mode Sourcegraph's always-fallback can't distinguish. Consent makes degradation a decision, not an accident | LOW | Persist per-repo like `--scheme`/`--language`; env var covers MCP/fleet use |
| Degraded-mode *reason taxonomy* in status | flutter doctor shows check results; jarvis can show *which* failure class (matched signature vs opted-in generic vs manual `--search-only`), when it last retried, and what changed. Doctor-grade output from a status command | MEDIUM | Distinguish the three degradation origins in `getIndexStatus` — they have different recovery stories (none / retry-automatic / `jarvis forget`) |
| TTY install offer with remembered decline | Ahead of ecosystem norm (pip/uv/npm only hint). Homebrew-style ask-once-persist respects the user; per-repo memory avoids the nag treadmill | MEDIUM | Prompt y/N only on interactive `jarvis index`; on yes run the `uv sync --extra semantic`-equivalent; on no persist decline per-repo in registry |
| Agent-legible degradation via MCP | MCP consumers (Claude Code) can *adapt* — use searchCode instead of goToDefinition — if `getIndexStatus` states capability per tool class. No comparator exposes machine-readable degradation | LOW | Structured fields (`navigation: unavailable`, `reason`, `since`, `recovery`) beat prose for agent consumers |

### Anti-Features (Commonly Requested, Often Problematic)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| Silent auto-fallback on *any* failure (no opt-in) | "Just make it work" — Sourcegraph does it | Launders transient failures (missing binary, wrong scheme, network blip) into apparent success; user never learns nav is gone; violates the repo's documented fail-loudly intent (`index_cli.py:84`) | Opt-in generic fallback + automatic fallback only for *verified-unfixable* signatures |
| Reusing permanent `search_only=1` for the generic fallback | Code already exists | One-way trap: only `jarvis forget` escapes; a transient failure would permanently strand a repo in search-only | New additive `degraded` state that self-heals on reindex |
| Prompting in non-TTY contexts (watch, MCP reindex) | "Users should always be asked" | Hangs pipelines and MCP sessions on stdin; universal CLI anti-pattern | TTY-gated prompt; stderr hint elsewhere; env/flag as the non-interactive contract |
| Global (not per-repo) decline memory for semantic install | Simpler to implement | User may want semantic on repo A but not repo B; a global "no" makes the feature undiscoverable forever | Per-repo decline in registry, consistent with `--scheme`/`--language` persistence |
| Re-prompting on every index run after a decline | "Maybe they changed their mind" | Nag treadmill; erodes trust in all future prompts | Ask once, persist; mention re-enable path in the decline confirmation ("pass --semantic to revisit") |
| Auto-updating scip-swift (unpinned "latest") | Never stale | Unvetted CLI/stderr changes break `_swift_indexer_cmd` and signature matching silently; pin-and-verify is the established repo pattern (zoekt, scip fork) | Bump the pin deliberately; `setup-smoke.yml` guards resolution |
| Signature matching by build-file parsing instead of stderr | "More robust than string matching" | Repo already learned this lesson: build files lie about what the indexer will actually do; stderr is ground truth | Keep the `_SEARCH_ONLY_SIGNATURES` stderr-pattern approach; verify each signature against the pinned indexer version |

## Feature Dependencies

```
[scip-swift pin bump v0.2.1]
    └──must precede──> [scip-swift failure signatures]
                            (signatures verified against v0.2.1 stderr, not v0.1.2)

[Generic opt-in fallback]
    └──requires──> [New additive registry state (degraded ≠ search_only)]
                       └──requires──> [Registry backward compatibility]
    └──requires──> [Self-healing retry in reindex/watch]

[Status/getIndexStatus degradation reporting]
    └──requires──> [Persisted failure cause + degradation origin in registry]
    ──enhances──> [Generic opt-in fallback]   (fallback without reporting = silent degradation)
    ──enhances──> [scip-swift signatures]

[Semantic install prompt]
    └──requires──> [TTY detection gate]
    └──requires──> [Per-repo decline persistence in registry]
    (independent of the fallback features — shares only the registry surface)

[Silent auto-fallback default] ──conflicts──> [fail-loudly design intent]
```

### Dependency Notes

- **Signatures depend on the pin bump:** stderr wording is version-specific; writing signatures
  against v0.1.2 output then bumping to v0.2.1 risks dead patterns. Bump first, capture real
  failure output, then encode signatures.
- **Reporting is the safety valve for fallback:** the fallback features are only acceptable
  (per the fail-loudly constraint) because status makes degradation loud somewhere else.
  Shipping fallback without the reporting upgrade re-creates the silent-degradation anti-feature.
- **Both fallback state and semantic-decline memory touch `registry.py`:** additive columns/keys,
  same migration discipline — natural to design the registry changes together even if shipped in
  separate phases.

## MVP Definition

### Launch With (v1)

- [ ] scip-swift pin → v0.2.1 + `_swift_indexer_cmd` compatibility verification — cheapest item, unblocks signature work, closes the stale-toolchain gap
- [ ] Generic opt-in fallback with self-healing registry state — the core value ("a failure never leaves a repo with nothing")
- [ ] Degradation reporting in `jarvis status` + `getIndexStatus` (cause + recovery) — safety valve that makes fallback acceptable
- [ ] scip-swift signatures in `_SEARCH_ONLY_SIGNATURES` — extends proven mechanism to the most failure-prone language

### Add After Validation (v1.x)

- [ ] Semantic install TTY prompt with per-repo decline memory — independent UX feature; trigger: fallback features stable, registry schema for per-repo prefs settled
- [ ] Structured machine-readable capability fields in `getIndexStatus` — trigger: agent consumers observed misusing degraded repos

### Future Consideration (v2+)

- [ ] Read-time supplementation (Sourcegraph-style: nav tool answers from search when SCIP absent) — much larger design; publish-time fallback must prove itself first
- [ ] Degradation history/timestamps ("degraded since, N retries") — polish; needs retry telemetry that doesn't exist yet

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Generic opt-in self-healing fallback | HIGH | MEDIUM | P1 |
| Status/MCP degradation reporting | HIGH | MEDIUM | P1 |
| scip-swift pin bump to v0.2.1 | MEDIUM | LOW | P1 |
| scip-swift automatic signatures | MEDIUM | LOW–MEDIUM | P1 |
| Semantic install prompt + decline memory | MEDIUM | MEDIUM | P2 |
| Machine-readable capability fields | MEDIUM | LOW | P2 |
| Read-time search supplementation of nav | HIGH | HIGH | P3 |

**Priority key:**
- P1: Must have for launch
- P2: Should have, add when possible
- P3: Nice to have, future consideration

## Competitor Feature Analysis

| Feature | Sourcegraph | clangd / rust-analyzer | flutter doctor / brew doctor | Jarvis approach |
|---------|-------------|------------------------|------------------------------|-----------------|
| Fallback on index failure | Automatic, per-query, always-on (search substrate always exists) | Degrade in place, keep serving heuristic results | n/a | Opt-in at publish time + automatic for verified signatures; self-heals on reindex |
| Degradation visibility | Results badged precise vs search-based | Logs / explicit editor error with raw cause | Sectioned ✗ with remediation command | Nav tools return explanatory error; status shows origin, cause, recovery command |
| Failure cause fidelity | Index-job logs in admin UI | Raw stderr surfaced verbatim | Human-readable diagnosis | Persist matched signature / stderr excerpt; render verbatim in status |
| Optional-feature onboarding | n/a | n/a | n/a (pip/uv/npm: hint only, never prompt) | TTY-gated y/N prompt, auto-install on yes, per-repo remembered decline (Homebrew consent pattern) |
| Recovery from degraded | Automatic on next successful index upload | Automatic once config fixed | Manual, guided | Automatic: every reindex retries the full build |

## Sources

- [Sourcegraph: Precise Code Navigation](https://sourcegraph.com/docs/code-search/code-navigation/precise_code_navigation) — automatic search-based fallback and supplementation (MEDIUM)
- [Sourcegraph: Code Navigation overview](https://sourcegraph.com/docs/code-navigation) (MEDIUM)
- [GitHub blog: Introducing stack graphs](https://github.blog/open-source/introducing-stack-graphs/) and [Precise code navigation for Python](https://github.blog/news-insights/product-news/precise-code-navigation-python-code-navigation-pull-requests/) — fuzzy fallback where precise unavailable (MEDIUM)
- [clangd fallback-command behavior (clangd#2025)](https://github.com/clangd/clangd/issues/2025); [rust-analyzer workspace-load failure reporting (rust-analyzer#14042)](https://github.com/rust-lang/rust-analyzer/issues/14042) (MEDIUM)
- [Command Line Interface Guidelines (clig.dev)](https://github.com/cli-guidelines/cli-guidelines); [Improving CLIs with isatty](https://blog.jez.io/cli-tty/) — TTY-gated prompting, non-interactive equivalents (MEDIUM)
- [Flutter doctor troubleshooting docs](https://docs.flutter.dev/install/troubleshoot) — doctor-pattern status reporting (MEDIUM)
- [gh config / telemetry docs](https://cli.github.com/manual/gh_config), [GitHub CLI opt-out telemetry changelog](https://github.blog/changelog/2026-04-22-github-cli-opt-out-usage-telemetry/) — persisted preference in config; Homebrew first-run consent as the ask-once precedent (MEDIUM)
- `gh release list --repo jarvis-intelligence/scip-swift` run 2026-08-16: latest **v0.2.1** (2026-08-15) (HIGH — empirical)
- `/Users/ddphuong/Projects/jarvis-ai/jarvis/.planning/PROJECT.md` and `CLAUDE.md` — existing fallback mechanism, fail-loudly intent, registry persistence patterns (HIGH — first-party)

---
*Feature research for: local-first code-intelligence indexing robustness*
*Researched: 2026-08-16*
