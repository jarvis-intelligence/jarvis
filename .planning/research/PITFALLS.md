# Pitfalls Research

**Domain:** Indexing-pipeline fallback/degraded-mode features, indexer version bumps, and CLI install prompts in a local-first code-intelligence MCP server (jarvis)
**Researched:** 2026-08-16
**Confidence:** HIGH — every jarvis-specific claim below was verified against the current source (`index_cli.py`, `registry.py`, `watch.py`, `setup.sh`) and against the `jarvis-intelligence/scip-swift` releases/source at tags v0.1.2, v0.2.0, v0.2.1 via the GitHub API. uv behavior claims are MEDIUM and flagged inline.

## Critical Pitfalls

### Pitfall 1: Naive scip-swift pin bump — the release asset contract changed under you

**What goes wrong:**
Bumping `SCIP_SWIFT_VERSION="v0.1.2"` → `"v0.2.1"` in `setup.sh` fails twice over, verified against the live releases:
1. **Asset name pattern changed.** v0.1.2 published `scip-swift-v0.1.2-macos-arm64.tar.gz`; v0.2.0/v0.2.1 publish `scip-swift-0.2.1.tar.gz` — no `v` prefix, no platform suffix. `setup.sh:545` constructs `scip-swift-${SCIP_SWIFT_VERSION}-macos-arm64.tar.gz`, which 404s against v0.2.x.
2. **The `.sha256` sidecar is gone.** v0.1.2 shipped a `.tar.gz.sha256` asset; v0.2.x releases have none. `install_tarball_binary` (`setup.sh:258`) hard-requires the sidecar and fails at "checksum download failed" even if the tarball URL is fixed.

**Why it happens:**
The scip-swift release workflow changed its packaging between 0.1.x and 0.2.x. A pin bump looks like a one-line change, so nobody re-inspects the asset contract the installer depends on.

**How to avoid:**
- Fix the **scip-swift release workflow** to restore platform-suffixed assets + `.sha256` sidecars (preferred — jarvis controls that repo), then cut a release with conforming assets, then bump the pin. Do **not** weaken `setup.sh` by dropping checksum verification; that is a security regression for every binary it installs.
- Verify the tarball's internal member name still matches what `install_tarball_binary` extracts (`scip-swift`) — the layout may have changed along with the naming.
- CLI compatibility is already confirmed safe: v0.2.1 keeps `IndexCommand` as `defaultSubcommand` (bare `scip-swift` invocation works), and `--output`, `--build-tool`, `--scheme` are unchanged. New in 0.2.x: `--cache-dir`, `--index-only`, and an `index-many` subcommand.

**Warning signs:**
`.github/workflows/setup-smoke.yml` is the only guard on the pin resolving (`--only scip-swift` + `--version` check). Run it (or `JARVIS_BIN_DIR=/tmp/bin sh setup.sh --only scip-swift` locally) in the same PR as the bump — a green smoke run is the acceptance test.

**Phase to address:**
The scip-swift version-bump phase — and it likely needs a scip-swift-repo sub-task (release workflow fix + new release) *before* the jarvis-side pin change.

---

### Pitfall 2: scip-swift v0.2.x writes `.scip-cache/` inside the repo — watch self-trigger loop and new stateful failure modes

**What goes wrong:**
v0.2.x's incremental cache defaults to `<repo>/.scip-cache/` (`IndexCommand.swift`: "Directory for the incremental index cache. Defaults to <repo>/.scip-cache/"). Two consequences:
1. **`jarvis watch` self-trigger loop.** `watch.py`'s `_IGNORED_PATH_PARTS` is `{".git", "node_modules", ".venv", "__pycache__", "dist", "build"}` — no `.scip-cache`. Every reindex writes into the watched tree, the watcher fires, and the loop repeats every debounce cycle. (Note: `.build` and `DerivedData` are in `config.IGNORED_DIRS` but *not* in the watch ignore list either — swiftpm builds writing `.build/` in-repo can already do this today; the cache makes it universal.)
2. **Stateful retries.** A corrupted or stale cache can make every rebuild fail identically. Combined with the new generic fallback, a bad cache converts into *permanent-looking* degradation that "self-healing" never heals — the retry re-reads the same poisoned state.

Also pollutes users' `git status` in repos that don't ignore it.

**Why it happens:**
Version bumps are reviewed for CLI-flag compatibility, not for new side effects on disk. The cache is invisible until watch mode or a reindex-after-failure exposes it.

**How to avoid:**
- Pass `--cache-dir` explicitly, pointing under `~/.jarvis/` (e.g. keyed by slug) so the repo tree stays untouched. This kills the watch loop, the git-status pollution, and gives `jarvis forget` a cache to clean up.
- If keeping the in-repo default, add `.scip-cache` (and `.build`, `DerivedData`) to `_IGNORED_PATH_PARTS`.
- Degraded-state remedy text for Swift should mention clearing the cache as a recovery step.

**Warning signs:**
`jarvis watch` on a Swift repo reindexes continuously with no edits; `git status` in an indexed Swift repo shows an untracked `.scip-cache/`.

**Phase to address:**
Version-bump phase (choose the `--cache-dir` strategy alongside the pin), with the watch ignore-list fix in the same change.

---

### Pitfall 3: Reusing `search_only=1` for the generic fallback — the one-way trap eats the self-healing property

**What goes wrong:**
The verified mechanics: the automatic signature fallback persists `search_only=True` (`index_cli.py:827-829`), and `_resolve_search_only` (`:537`) makes every later `reindex`/`watch` **skip the SCIP build entirely** when that flag is set. If the new generic fallback writes the same flag, one transient failure permanently converts the repo to search-only — the exact opposite of the milestone's self-healing requirement. The escape today is `jarvis forget` + full reindex, losing overrides (`--scheme`, `--language`, `--semantic-include`) with it.

**Why it happens:**
The persistence machinery already exists and works; reusing `upsert(..., search_only=True)` is the path of least resistance. The comment at `index_cli.py:116-124` documents that the flag is "settable but never clearable" — deliberate for permanently-unindexable repos, poison for retryable ones.

**How to avoid:**
- Distinct additive registry state (e.g. a `degraded` marker + reason column via the existing `_ensure_column` migration pattern) that `_resolve_search_only` **never reads**. `search_only=1` keeps meaning "never attempt the build"; the new state means "last build failed, publish was search-only, retry next time".
- Old registries must keep working: new columns need defaults compatible with existing rows, and persisted `search_only=1` repos must be untouched (PROJECT.md compatibility constraint).
- The opt-in flag itself must be clearable. `--search-only` is `store_true`/`default=None` — settable, never un-settable. Use an explicit on/off form (`argparse.BooleanOptionalAction` or `--fallback {on,off}`) so `jarvis index --no-fallback` can clear a persisted opt-in without `forget`.

**Warning signs:**
A test that opts in, fails once, then reindexes with a working build must end at status `indexed` — if it ends at `search-only`, the trap is live. Grep for every `search_only=True` write in the new code path.

**Phase to address:**
Generic-fallback phase — the registry state design is the first decision, before any pipeline wiring.

---

### Pitfall 4: The degrade path violates the never-half-published invariant — retire-then-fail leaves a repo with nothing

**What goes wrong:**
`_publish_search_only` calls `_retire_scip_artifacts` **first** — `shutil.rmtree` on the pointer directory plus clearing the repo's outgoing graph edges — and only then runs `zoekt-git-index`. If Zoekt (or anything after) fails, the repo has lost its previously-good navigation *and* has no refreshed search: strictly worse than the failure that triggered the fallback. The main pipeline was carefully built to never do this (`zoekt-git-index` runs before the pointer swap precisely so failure leaves the previous index live — `index_repo` docstring, `:734-739`). The degrade path predates that discipline being load-bearing: today it fires only on permanently-doomed repos (signatures) or explicit `--search-only`, where there is usually no good index to lose. A generic fallback firing on a *previously-good* repo makes the ordering matter.

Second-order problem: a repo with an intermittent build (flaky scheme resolution, signing, network-dependent SPM resolution) **flaps** — nav destroyed on failure, rebuilt on success, destroyed again — churning state and confusing status.

**Why it happens:**
`_retire_scip_artifacts` exists for a good reason (stale nav answering from old data while status says search-only — its docstring documents the `{"indexed": true, "status": "search-only"}` contradiction). Reusing `_publish_search_only` as-is inherits the retire-first ordering without re-examining it for the retryable case.

**How to avoid:**
- Reorder for the fallback path: run Zoekt (and semantic) first, retire SCIP artifacts only once the search publish succeeded — mirroring the main pipeline's prepare-everything-then-commit shape.
- Make the stale-nav-vs-no-nav trade-off a *deliberate decision* for the retryable case: keeping the old SCIP index live means navigation serves pre-failure positions (wrong lines after new commits); retiring means explicit errors. Either is defensible; whichever is chosen must be reflected coherently in status (see Pitfall 8) rather than emerging from code order.
- For flapping: at minimum report "degraded since <sha>, last good <sha>" so oscillation is visible; consider only retiring nav after N consecutive failures if flapping proves real.

**Warning signs:**
A unit test: previously-`indexed` repo + opted-in fallback + indexer failure + *Zoekt failure* → the old `current` pointer must still exist. If it doesn't, the invariant is broken.

**Phase to address:**
Generic-fallback phase — this is the core sequencing design of that phase.

---

### Pitfall 5: A catch-all fallback launders missing binaries and environment breakage into "degraded" publishes

**What goes wrong:**
`_run` converts `FileNotFoundError` (indexer not on PATH) into a plain `IndexingError` ("`scip-swift not found on PATH — run setup.sh`", `index_cli.py:345-348`). A generic fallback that catches "any `IndexingError` from the indexer step" therefore degrades a repo because *setup.sh wasn't run* — precisely the "transient breaks and missing binaries must fail loudly" case the design-intent comment (`:84-86`) forbids. Same for the bash-shim failure (`:815-816`, deliberately a hard failure with a remedy) and out-of-scratch-disk/keychain/simulator-runtime environment errors: opt-in or not, degrading on these hides a one-command fix behind a degraded state the user may never look at.

**Why it happens:**
At the catch site, all failures are the same exception type carrying a string. "Opt-in makes it OK to catch everything" feels true — but the user opted into *degrade when the build genuinely fails*, not *degrade when jarvis is misconfigured*.

**How to avoid:**
- Exclude from the fallback trigger, even when opted in: missing-executable failures (distinguishable at the `_run` boundary — carry a marker or check before the generic catch), `check_scip_version` failures, and `_bash_shim_failure` matches (keep its existing precedence *above* any fallback, as it already is above the signature check).
- Trigger the generic fallback only on a real non-zero indexer exit.
- Keep the automatic signature list narrow and multi-token per the existing convention ("EVERY substring must be present").

**Warning signs:**
Unit test: opted-in repo + `scip-swift` absent from PATH → must raise, not publish search-only. If it degrades, the laundering is live.

**Phase to address:**
Generic-fallback phase (trigger-condition design); signature phase for the automatic list's narrowness.

---

### Pitfall 6: New scip-swift signatures verified against the wrong binary version — the bump invalidates the strings in the same milestone

**What goes wrong:**
Failure signatures are substring matches against the indexer's stderr/stdout embedded in the exception message (`_run` embeds full, untruncated `stdout + stderr`; `_search_only_reason` matches on `str(exc)` — verified). CONCERNS.md already flags this as the most fragile area: "any upstream wording change breaks detection and turns a known-doomed build into a hard failure." This milestone makes it worse in a specific way: signatures collected from **v0.1.2** failure output may not match **v0.2.1** wording — the bump and the signature work land together, so signatures can be dead on arrival. The inverse failure also applies: too-broad substrings (single generic tokens like "error: no such module") match *transient* failures and launder them into `search_only=1` permanent degradation via the automatic (non-opt-in) path — a much worse outcome than a false negative.

**Why it happens:**
Signatures get copied from an old bug report or a failure reproduced on the currently-installed (old) binary rather than the newly-pinned one.

**How to avoid:**
- Reproduce each candidate failure against the **v0.2.x binary** and copy the literal strings from that output; land the signatures after (or with) the pin bump, never before.
- Follow the existing convention strictly: multiple co-occurring tokens per signature, each signature tied to a confirmed *permanent* failure class (e.g. a build backend that can never work), never to anything a user can fix (scheme typo, signing, missing simulator runtime) — those belong to the opt-in generic fallback or hard failure with a remedy, matching the bash-shim precedent.
- Add each signature with a unit test pinning the exact matched output (existing pattern in `tests/test_index_cli.py`).

**Warning signs:**
A signature with a single required token; a signature whose reason text a user could fix themselves; a signature added without a captured real v0.2.x output in the test.

**Phase to address:**
Signature phase, sequenced after the version-bump phase.

---

### Pitfall 7: Env-var opt-in vs persisted per-repo flag — precedence ambiguity and split-brain across process environments

**What goes wrong:**
The fallback is opt-in via CLI flag (persisted per-repo) *and* env var (global default). Two traps:
1. **Undefined precedence.** When the env var says on and the persisted per-repo value says off (or vice versa), behavior must be specified, not emergent. The existing `_resolve_*` contract is "explicit CLI > persisted > default" with `None` meaning "leave persisted alone" — the env var must slot in as the *default of last resort* (CLI > persisted > env > off), or a user's explicit per-repo `--no-fallback` gets silently overridden by a forgotten `export`.
2. **Split-brain environments.** The env var is read from whichever process runs the index: the user's shell (`jarvis index`), the watch process's inherited env, or the MCP client's environment for MCP-launched runs (`uvx --from jarvis-mcp jarvis-server` is spawned by Claude Code/Codex with *its* env, not the user's current shell). The same repo can behave differently per trigger path, and the user has no idea why.

**Why it happens:**
Env vars look like a cheap global toggle; nobody writes down where each indexing process actually gets its environment.

**How to avoid:**
- Write the precedence rule into the resolver's docstring and test all combinations (env on/off × persisted on/off/absent × CLI on/off/absent) — it's a small table, enumerate it.
- Persist the *effective* decision on the registry row at index time so `jarvis status` can report "fallback enabled (via env)" — making the split-brain visible instead of mysterious.
- Follow the `JARVIS_` prefix convention and document the variable next to the others.

**Warning signs:**
A repo degrades under `watch` but hard-fails under manual `jarvis reindex` (or vice versa) — that's the split-brain in action.

**Phase to address:**
Generic-fallback phase (resolver + precedence tests).

---

### Pitfall 8: Degraded state reported as a bare status string — no persisted reason, and unhandled status consumers

**What goes wrong:**
Today the *reason* for degradation exists only as a stderr note at index time (`index_cli.py:820-825`); the registry persists only `status="search-only"` + `search_only=1`. A status surface that says "degraded" without **why** and **how to recover** fails the milestone's core value ("the system explains why and how to recover"). Separately, status strings have consumers beyond `jarvis status`: `server.py` special-cases the search-only state to explain nav-tool failures (`:138-143`), and `_retire_scip_artifacts`'s docstring documents the self-contradictory `{"indexed": true, "status": "search-only"}` shape this machinery exists to prevent. A new status value (or a new degraded dimension on an old status) that any consumer doesn't handle produces wrong explanations at the MCP boundary — the tool tells the agent "run jarvis forget" when the right answer is "the next reindex will retry automatically."

**Why it happens:**
Status starts as a single enum column; degradation is a *multi-field fact* (what published, why, since when, what recovers it) squeezed into it.

**How to avoid:**
- Persist the reason (and the failing step / timestamp or sha) in the registry, additively. The status text for the new state must be distinct from `search-only` — they have different recovery stories (automatic retry vs `forget`).
- Enumerate every consumer of registry status before adding a state: `jarvis status`/`list` CLI output, `getIndexStatus`, and the nav-tool error explanations in `server.py`. Each needs an explicit branch for the new state with its specific recovery text.
- Keep `indexed`/`status`/`searchCoverage` mutually coherent in `getIndexStatus` — a degraded repo with live search should not read as either fully indexed or fully failed.

**Warning signs:**
A nav tool on a fallback-degraded repo returns the permanent search-only explanation ("no SCIP index" with no retry mention); `jarvis status` shows `search-only` for a repo the user never asked to be search-only.

**Phase to address:**
Degraded-state-reporting phase — but the *data* (reason column) must be written by the generic-fallback phase, so sequence reporting after fallback.

---

### Pitfall 9: The install prompt blocks or misfires outside a TTY — and "auto-install on yes" mutates the wrong Python environment

**What goes wrong:**
Two independent failure classes in the semantic-extra prompt:

1. **Blocking/misfiring prompts.** `input()` in a non-TTY context either hangs forever (parent holds the pipe open — an MCP-spawned or scripted run just stalls) or raises `EOFError` (closed stdin). `jarvis watch` and MCP-triggered reindexes must keep today's silent-skip + stderr hint (`_run_semantic_stage`, `:583-612`). A subtler case: `uvx --from jarvis-mcp jarvis index` run *interactively* — TTY present, but the environment is uv's ephemeral cache env, so an in-place install evaporates on the next run; prompting there offers an install that cannot stick.

2. **Wrong-environment installs.** jarvis reaches users at least four ways, verified in `setup.sh`/`embeddings.py`: `uv tool install jarvis-mcp` (tool venv under uv's tools dir — setup.sh installs this to pre-warm the cache), `uvx --from jarvis-mcp` (ephemeral cached env — the plugin's MCP launch path), a source checkout (`uv sync`), and potentially pipx. Each needs a *different* install command (`uv tool install --force 'jarvis-mcp[semantic]'` [MEDIUM confidence on exact reinstall flags — verify at plan time], `uv sync --extra semantic`, `pipx inject`), and a naive `uv pip install lancedb ...` targets whatever venv is active in the shell — likely not the one jarvis runs from. Installing into the wrong env produces "installed successfully" followed by the same skip message forever.

3. **In-process import after install is fragile.** If the install recreates the tool venv, re-importing within the already-running process may or may not pick the packages up; asserting it works is a trap.

**Why it happens:**
`isatty` gating looks sufficient until the uvx-interactive case; environment detection is skipped because "uv handles it".

**How to avoid:**
- Gate on `sys.stdin.isatty()` (and wrap `input()` in `EOFError`/`KeyboardInterrupt` handling as defense in depth); prompt text to stderr, preserving the "stdout is just `indexed <slug>`" scripting contract (`:562-563`).
- Detect the running environment from `sys.prefix` before offering install: uv tools dir → `uv tool install` with the extra, pinned to the *running* version to avoid a surprise jarvis upgrade mid-index; project `.venv` with `pyproject.toml` → `uv sync --extra semantic`; uv cache path (ephemeral uvx) → don't offer an install, print the durable command instead; unknown → print the command, don't run it.
- After a successful install, prefer "re-run indexing" (or re-exec) over asserting in-process importability; if attempting in-process, `importlib.invalidate_caches()` and treat import failure as the normal skip path.
- Persist the decline per-repo via a new additive registry column (`_ensure_column` pattern), same contract as other persisted per-repo state.

**Warning signs:**
A watch/MCP run that never completes an index (hung on stdin); "installed" followed by the semantic-skipped hint on the very next run (wrong env); the prompt appearing in CI logs.

**Phase to address:**
Semantic-prompt phase; the environment-detection matrix is the bulk of its design.

---

### Pitfall 10: Self-healing retry under `watch` re-runs a doomed multi-minute build every debounce cycle

**What goes wrong:**
"Retry the full build on every reindex" is correct for manual `jarvis reindex`, but `watch` reindexes on every debounced file change. For a repo whose build persistently fails (the population the fallback exists for), each save triggers a full xcodebuild/swiftpm attempt — minutes of CPU per cycle — followed by the degrade path (which, per Pitfall 4, may also tear down and rebuild search state each time). `_resolve_search_only`'s docstring names this exact cost as the reason the permanent flag exists: "stops a repo that already proved un-indexable from re-running a doomed multi-minute build."

**Why it happens:**
Self-healing and retry-cost are in direct tension; the design conversation usually resolves the trap (Pitfall 3) and forgets the treadmill.

**How to avoid:**
- Key the retry on change, not on time: persist the HEAD sha (or working-tree state) of the last failed attempt and skip the full build when nothing relevant changed — retry when the sha differs. This preserves self-healing (a fix is a change) without the treadmill.
- Alternatively a simple cooldown/backoff for watch-triggered retries; sha-keyed is cleaner and uses state the registry already stores (`commit_sha`).

**Warning signs:**
`watch` output showing repeated multi-minute reindex cycles ending in the same fallback note; laptop fans.

**Phase to address:**
Generic-fallback phase (retry-policy design), verified in the watch integration path.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Reuse `search_only=1` + `SEARCH_ONLY_STATUS` for the generic fallback | No schema change | One-way trap kills self-healing; status consumers conflate two recovery stories | Never (explicitly out of scope in PROJECT.md) |
| Reuse `_publish_search_only` unmodified for the fallback | No new publish path | Retire-before-Zoekt ordering destroys good nav on transient failure | Never for the retryable path |
| Drop `.sha256` verification in setup.sh to match v0.2.x assets | Unblocks the pin bump | Unverified binary installs for a tool that runs arbitrary builds | Never — fix the release workflow instead |
| Encode degradation only in the `status` string | One column, no migration | Reason/recovery unavailable to status surfaces; consumer branches guess | MVP-acceptable only if the reason column lands in the same milestone |
| Signature strings copied from issue reports instead of reproduced output | Faster to write | Dead-on-arrival matching after the version bump | Never — reproduce against the pinned binary |
| Prompt gated on `isatty` only, no env detection | Simple | Wrong-env installs that "succeed" and change nothing | Acceptable only if the fallback is print-the-command instead of run-it |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| GitHub release assets (scip-swift) | Assuming asset naming is stable across releases; pin bump = one-line change | Treat asset name + sidecar + tarball layout as an interface; smoke-test `--only scip-swift` in the bump PR |
| scip-swift v0.2.x CLI | Assuming new flags are inert | `--cache-dir` defaults *inside the repo* — must be pointed elsewhere or watch loops (Pitfall 2) |
| uv environments | `uv pip install` from a prompt, targeting the active shell venv | Detect jarvis's own `sys.prefix`; use `uv tool install --force 'jarvis-mcp[semantic]'` / `uv sync --extra semantic` / print-only for uvx ephemeral envs |
| MCP stdio (`jarvis-server`) | Any code path reading stdin in a process whose stdin is the MCP protocol channel | Never `input()` in library code reachable from the server; prompts live only behind the TTY gate in CLI command handlers |
| argparse persisted flags | `store_true`/`default=None` for a persisted per-repo toggle | `BooleanOptionalAction` so the persisted value is clearable without `jarvis forget` (the documented `--search-only` trap, `index_cli.py:116-124`) |
| watchdog (`jarvis watch`) | Indexer-written in-repo state triggering the watcher | Keep indexer outputs out of the repo, or extend `_IGNORED_PATH_PARTS` (`.scip-cache`, `.build`, `DerivedData`) |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Full-build retry per watch debounce on a persistently-failing repo | Multi-minute CPU spikes on every save | Sha-keyed retry: full build only when HEAD/tree changed since last failure | Immediately, on any repo with a broken build + watch |
| Degrade path re-running Zoekt + semantic on every failed retry | Repeated full search/semantic reindex of unchanged content | Same sha-keying; skip re-publish when tracked state unchanged | Large repos (semantic embedding is the slow stage) |
| `_retire_scip_artifacts` rmtree + graph-edge clearing on every fallback fire | Registry/graph churn, transient windows with no nav | Retire only on first transition into degraded, not on every retry | Flapping builds |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Removing checksum verification to accommodate v0.2.x's missing `.sha256` sidecars | Unverified binary executed against user source trees | Fix scip-swift's release workflow to publish sidecars; keep `verify_sha256` mandatory |
| Auto-running install commands from a prompt without pinning | `uv tool install jarvis-mcp[semantic]` resolves latest — silently upgrades jarvis itself mid-workflow | Pin to the running version (`jarvis-mcp[semantic]==X.Y.Z`); show the exact command before running it |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| Same status word for permanent search-only and retryable degraded | User runs `jarvis forget` (loses overrides) when a plain reindex would recover — or waits for a retry that will never come | Distinct states with state-specific recovery text in both `jarvis status` and `getIndexStatus` |
| Degradation reason only on stderr at index time | MCP agents and later status checks can't explain *why* nav is gone | Persist reason + failing step + since-sha in the registry |
| Prompt re-asking on every index run | Nagging; users script around it | Decline remembered per-repo (planned); "yes" also remembered implicitly by the extra being present |
| Fallback note only in scrollback for watch runs | Silent degradation — user discovers nav loss days later inside their editor | Status surfaces carry the state; watch prints a distinct, loud line on the *transition* into degraded |
| Env-var opt-in invisible in status output | "Why did this repo degrade? I never opted in" | Status shows the effective fallback setting and its source (flag/persisted/env) |

## "Looks Done But Isn't" Checklist

- [ ] **Pin bump:** setup-smoke.yml green on the PR (asset name, sidecar, tarball member all verified) — not just a local `scip-swift --version`
- [ ] **Pin bump:** `--cache-dir` decision made and tested under `jarvis watch` (no self-trigger loop)
- [ ] **Generic fallback:** previously-`indexed` repo + fallback + *Zoekt failure* → old `current` pointer intact (half-published invariant test)
- [ ] **Generic fallback:** missing indexer binary + opt-in → hard failure, not degraded publish
- [ ] **Generic fallback:** degrade → fix build → reindex → status returns to `indexed` and nav works (the actual self-healing round trip)
- [ ] **Generic fallback:** persisted opt-in is clearable via CLI without `jarvis forget`
- [ ] **Signatures:** each new signature's test embeds real output captured from the *pinned* scip-swift version
- [ ] **Status:** every consumer of the status string (CLI status/list, `getIndexStatus`, server.py nav-tool error explanations) branches on the new state with correct recovery text
- [ ] **Prompt:** `jarvis index </dev/null`, under `watch`, and via MCP all complete without blocking; `EOFError` handled
- [ ] **Prompt:** "yes" path verified in a real `uv tool install` environment AND a source checkout — and the uvx-interactive case doesn't offer a non-durable install
- [ ] **Registry:** new columns added via `_ensure_column`; an old registry.db opens and lists cleanly

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Pin bump shipped against wrong asset contract | LOW | Revert pin; setup.sh keeps installing v0.1.2; fix release workflow, re-bump |
| Fallback wrote `search_only=1` (trap sprung in the field) | MEDIUM | Ship fix + one-time registry migration clearing the flag for rows with the new degraded marker; without a marker, users need `forget` + reindex (override loss) |
| Retire-then-fail left repos with no nav and stale search | MEDIUM | Reindex recovers each repo; fix ordering; no data is unrecoverable (indexes are derived state) |
| Signature false positive laundering transient failures | MEDIUM | Remove/narrow signature; affected repos need the `search_only=1` escape (`forget` + reindex) — which is why false positives are worse than false negatives here |
| Wrong-env semantic install | LOW | Print correct command for the detected env; no cleanup needed (extra packages in an unused venv are inert) |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| 1. Asset-contract pin bump | scip-swift bump phase (with upstream release-workflow sub-task first) | setup-smoke.yml green; local `--only scip-swift` install + `--version` |
| 2. In-repo `.scip-cache` / watch loop | scip-swift bump phase | watch a Swift repo through one reindex: exactly one cycle fires |
| 3. `search_only=1` trap | Generic-fallback phase (registry state design first) | degrade→fix→reindex round-trip test ends `indexed` |
| 4. Retire-then-fail ordering | Generic-fallback phase | Zoekt-failure-after-degrade test: old pointer intact |
| 5. Laundered missing binaries | Generic-fallback phase | missing-binary + opt-in → raises |
| 6. Stale signature strings | Signature phase, sequenced after the bump | tests embed v0.2.x-captured output |
| 7. Env/flag precedence | Generic-fallback phase | full precedence-combination test table |
| 8. Status reporting gaps | Reporting phase, after fallback (reason column written by fallback phase) | each status consumer branch tested per state |
| 9. Prompt blocking / wrong env | Semantic-prompt phase | non-TTY paths complete; install verified per environment class |
| 10. Watch retry treadmill | Generic-fallback phase (retry policy) | unchanged-sha watch cycle skips the full build |

**Suggested phase ordering implied by the above:** bump scip-swift (1, 2) → generic fallback (3, 4, 5, 7, 10) → signatures (6) → degraded-state reporting (8) → semantic prompt (9). Signatures depend on the bumped binary's wording; reporting depends on the fallback's persisted state; the prompt is independent and can float.

## Sources

- `src/jarvis/index_cli.py` — `_SEARCH_ONLY_SIGNATURES` (:87), design-intent comment (:84-86), bash-shim precedence (:116-136, :815-816), `_run` output embedding (:325-351), `_publish_atomically` (:354), `_resolve_search_only` (:537-545), `_run_semantic_stage` (:583-612), `_retire_scip_artifacts` (:615-645), `_publish_search_only` (:648-679), fallback branch (:810-833), `_cmd_watch` (:1047-1114) — HIGH (primary source)
- `src/jarvis/registry.py` — `search_only` column, `_ensure_column` additive migrations, `SEARCH_ONLY_STATUS` — HIGH
- `src/jarvis/watch.py` — `_IGNORED_PATH_PARTS` (:18) lacks `.scip-cache`/`.build`/`DerivedData` — HIGH
- `setup.sh` — `SCIP_SWIFT_VERSION` (:48), asset construction (:545), `install_tarball_binary` sidecar requirement (:254-290), jarvis-mcp install via `uv tool install` (:643-671) — HIGH
- `gh release list/view --repo jarvis-intelligence/scip-swift` — v0.2.1 latest (2026-08-15); v0.1.2 assets `scip-swift-v0.1.2-macos-arm64.tar.gz` + `.sha256`; v0.2.0/v0.2.1 assets `scip-swift-0.2.x.tar.gz`, no sidecar — HIGH (primary source, checked 2026-08-16)
- `jarvis-intelligence/scip-swift` source at tags v0.1.2/v0.2.1 — `ScipSwiftCommand.swift` (defaultSubcommand IndexCommand), `IndexCommand.swift` (`--output`/`--build-tool`/`--scheme` stable; `--cache-dir` defaulting to `<repo>/.scip-cache/` and `--index-only` new in 0.2.x) — HIGH
- uv docs (context7 `/astral-sh/uv`) — uvx runs in temporary isolated environments; `uv tool install --with` for extra packages; exact reinstall-with-extras flags MEDIUM, verify at plan time
- `.planning/codebase/CONCERNS.md`, `.planning/PROJECT.md` — prior analysis, cross-checked against source — HIGH

---
*Pitfalls research for: jarvis indexing robustness & scip-swift update milestone*
*Researched: 2026-08-16*
