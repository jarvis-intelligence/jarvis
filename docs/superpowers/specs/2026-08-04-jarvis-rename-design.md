# Rename codeintel → jarvis

**Date:** 2026-08-04
**Status:** Approved, not yet implemented
**Release:** 0.5.0

Rename the project from `codeintel` to `jarvis` across every surface: source
package, CLIs, environment variables, data directory, PyPI distribution, MCP
registry entry, plugin and marketplace metadata, skills, both GitHub
repositories, and all documentation including dated historical records.

## Decisions

Five choices shape the work. Each was decided explicitly; the rejected
alternatives are recorded because the reasoning matters more than the outcome.

**Full rename, including PyPI and the plugin.** The breaking edge is the
distribution: renaming the PyPI project and the plugin orphans anyone already
installed. Accepted, because there are no users yet — the public install paths
are days old.

**The private dev repo takes the `jarvis` name; the public one is renamed.**
`phuongddx/jarvis` already exists as the public distribution surface, so both
repos cannot hold the name. The private repo becomes `phuongddx/jarvis` and the
public one becomes `phuongddx/jarvis-dist`.

The known cost: the repo users type is the public one, so users end up typing
the less clean name while `phuongddx/jarvis` points at a repo they cannot read.
GitHub's rename redirect also stops helping the moment the private repo claims
`jarvis` — already-installed marketplace entries and cached curl URLs will 404
rather than redirect. Accepted for the same reason as above.

**PyPI distribution is `jarvis-mcp`.** Plain `jarvis` on PyPI is taken by an
unrelated package, so the distribution name must again differ from the import
package — the same shape of problem as `codeintel-navigation-mcp`, a different
cause. `jarvis-mcp` is the shortest option and appears in `plugin/.mcp.json` as
`uvx --from jarvis-mcp jarvis-server`.

**No data-directory migration.** `~/.jarvis` starts empty. The existing
`~/.codeintel` (5.0 GB, 12 indexed repos) is left in place, untouched, and repos
are re-indexed from scratch.

Migration would in fact have been cheap — verified: `current` pointer files hold
a bare filename, and `registry.db` stores only *source* repo paths, never
data-dir paths, so a plain `mv` would preserve every index. But with a single
local user there is nothing to migrate for, and no migration code is simpler
than any migration code. Anyone who wants the 5 GB back can `mv ~/.codeintel
~/.jarvis` by hand; that is documented, not a supported code path.

**Historical documents are rewritten uniformly.** All ~2,900 references change,
including dated specs, plans, journals, and past `CHANGELOG.md` entries, and
file paths carrying the old name are renamed too so paths and content agree.

The cost, accepted knowingly: a spec dated 2026-07-26 will read as though it
designed something called "jarvis", a name that did not exist until 2026-08-04.
The alternative — rewriting only living documents — would have kept the
historical record literally true at the price of `grep jarvis` missing every
historical rationale. Consistency was chosen over literal historical fidelity.

## Naming map

| Surface | Before | After |
|---|---|---|
| Private dev repo | `phuongddx/codeintel` | `phuongddx/jarvis` |
| Public dist repo | `phuongddx/jarvis` | `phuongddx/jarvis-dist` |
| PyPI distribution | `codeintel-navigation-mcp` | `jarvis-mcp` |
| Import package | `src/codeintel/` | `src/jarvis/` |
| CLI (indexer) | `codeintel` | `jarvis` |
| CLI (server) | `codeintel-server` | `jarvis-server` |
| Data directory | `~/.codeintel` | `~/.jarvis` |
| Env var prefix | `CODEINTEL_` | `JARVIS_` |
| MCP registry name | `io.github.phuongddx/codeintel` | `io.github.phuongddx/jarvis` |
| MCP server key | `codeintel` | `jarvis` |
| Plugin + marketplace | `codeintel` | `jarvis` |
| User skills | `codeintel-{setup,use,issues}` | `jarvis-{setup,use,issues}` |
| Maintainer skill | `codeintel-release` | `jarvis-release` |
| Release secret | `JARVIS_RELEASE_TOKEN` | `JARVIS_DIST_TOKEN` |
| Version | 0.4.0 | 0.5.0 |

MCP *tool* names (`goToDefinition`, `searchCode`, `blastRadius`, …) carry no
product name and do not change. Neither does the on-disk `scip/_/<slug>/_/`
path shape, nor the `git config zoekt.name <slug>` pin, both of which are keyed
to a repo slug rather than the product.

## Code changes

`src/codeintel/` becomes `src/jarvis/` — 17 modules, no churn beyond import
lines. In `pyproject.toml`: `name = "jarvis-mcp"`, `[tool.uv.build-backend]
module-name = "jarvis"`, and both `[project.scripts]` entries repointed to
`jarvis.index_cli:main` and `jarvis.server:main`.

The naming-rationale comment at the top of `pyproject.toml` is rewritten. Its
current text explains that plain `codeintel` is held by the abandoned Komodo
Edit CodeIntel package; the new text explains that plain `jarvis` is held by an
unrelated package. The warning it carries — that this name must stay in sync
with the PyPI trusted publisher, or uploads fail with `Non-user identities
cannot create new projects` — stays, because it is about to matter (see
*Trusted publishing* below).

`config.py`: `DEFAULT_DATA_DIR = Path.home() / ".jarvis"` and the env var
becomes `JARVIS_DATA_DIR`. The `PROJECT`/`BRANCH` single-tenant pins are
untouched.

All 13 environment variables move to the `JARVIS_` prefix, not only the 5 that
`CLAUDE.md` documents: `DATA_DIR`, `EMBEDDING_MODEL`, `EMBEDDING_BATCH_SIZE`,
`EMBEDDING_QUERY_PREFIX`, `EMBEDDING_DOC_PREFIX`, `ZOEKT_BIN`,
`ZOEKT_KEEP_VERSIONS`, `SCIP_BIN`, `INDEXER_TIMEOUT`, plus the test-only seams
`BIN_DIR`, `SETUP_SOURCED`, `MARKER`, and `REPO`.

`setup.sh`: `JARVIS_REPO="phuongddx/jarvis"` and
`ZOEKT_RELEASE_REPO="phuongddx/jarvis-dist"` — `test_setup_sh.py`'s assertion
that the two differ still holds. Banner, help text, and `~/.jarvis/bin` paths
follow.

`setup.sh`'s shell-rc writer (`ensure_on_path`) is **not** changed. An earlier
draft of this spec claimed it appends unconditionally and that stale entries
would shadow new binaries. Both claims were wrong, and the correction is
recorded here because it removes work from the plan:

- It already dedups, on the directory string (`setup.sh:178`), and
  `test_ensure_on_path_is_idempotent` covers exactly that.
- The four `# added by codeintel setup` blocks in the developer's `.zshrc` are
  four *different* directories — `~/.codeintel/bin` plus three
  `/tmp/codeintel-b5-*/bin` from test runs — not repeated appends of one.
- Shadowing runs the other way. Each block is `export PATH="$dir:$PATH"` and
  `.zshrc` executes top-to-bottom, so the *last* line written lands *first* on
  `PATH`. A `~/.jarvis/bin` line appended after the stale `~/.codeintel/bin` one
  therefore wins.

What remains is clutter plus one narrow case: a binary present in
`~/.codeintel/bin` but absent from `~/.jarvis/bin` (e.g. after
`setup.sh --only zoekt`) would still resolve from the stale directory. Deleting
the four lines by hand in phase 1 covers it, without a code change or new test.

`scripts/check_versions.py`: `PACKAGE = "jarvis-mcp"`, and the `mcpServers` key
lookup becomes `"jarvis"`.

`plugin/.mcp.json`'s `--from` compatibility floor becomes `jarvis-mcp>=0.5.0`.
It cannot stay at `0.2.1`: `jarvis-mcp` is a brand-new PyPI project with no
release history, so a 0.2.1 floor would resolve to nothing installable. Setting
the floor equal to the release version keeps `check_versions.py`'s
`floor <= version` invariant satisfied, and the floor resumes drifting behind
normally from 0.6.0 on.

Roughly 500 test references follow mechanically, concentrated in
`test_index_cli.py` (195), `test_setup_sh.py` (49), and `test_semantic.py` (37).

## Distribution changes

### Trusted publishing (highest risk)

`publish-pypi.yml` uses PyPI trusted publishing, which binds a project to a
`(owner, repo, workflow file, environment)` tuple. This rename changes two legs
at once: the repo becomes `jarvis`, and the project becomes `jarvis-mcp`, which
does not exist on PyPI. An OIDC identity cannot create a new project on first
upload.

**Mandatory manual prerequisite:** on pypi.org, create a *pending publisher*
for project `jarvis-mcp` bound to owner `phuongddx`, repo `jarvis`, workflow
`publish-pypi.yml`, environment `pypi`. With it, the first release creates the
project automatically. Without it, the release fails at upload with an error
that does not name the real problem.

`publish-pypi.yml` also needs `environment.url` →
`https://pypi.org/p/jarvis-mcp`, its wheel-content guard changed from
`startswith("codeintel/")` to `"jarvis/"`, and its README marker assertion →
`mcp-name: io.github.phuongddx/jarvis`.

### The old PyPI project

`codeintel-navigation-mcp` 0.4.0 stays as its final version: still installable,
still working, not yanked. No deprecation release — its trusted publisher is
bound to repo `codeintel`, which stops existing, so a final upload would mean
reconfiguring a publisher for a package nobody installs. The rename is recorded
in `CHANGELOG.md` instead.

Yanking was rejected: yanks surface to existing installs as resolver errors,
which is worse than a stale-but-working pin.

### MCP registry

The `io.github.phuongddx/*` namespace is owner-scoped via OIDC, so the repo
rename does not disturb it. `server.json` gets `name:
io.github.phuongddx/jarvis`, repository URL `phuongddx/jarvis`, and package
identifier `jarvis-mcp`. `README.md`'s `<!-- mcp-name: … -->` marker follows.

The old `io.github.phuongddx/codeintel` entry stays published and harmless: it
points at a package that still resolves.

### GitHub repositories

Ordering is load-bearing. GitHub will not hold two repos of one name under one
owner, and the redirect created on rename is dropped when another repo claims
the freed name:

1. Rename public `phuongddx/jarvis` → `phuongddx/jarvis-dist`
2. Rename private `phuongddx/codeintel` → `phuongddx/jarvis`
3. `git remote set-url origin` locally

Out of order, step 2 fails outright. Between steps 1 and 2 the old install URLs
still redirect; after step 2 they 404.

`secrets.JARVIS_RELEASE_TOKEN` keeps working untouched — fine-grained PATs scope
by repository ID, not name, so it follows the public repo through its rename.
It is renamed to `JARVIS_DIST_TOKEN` only so it does not read as "the token for
the repo we are in".

### Workflows

- `sync-public-distribution.yml` — clone target `phuongddx/jarvis-dist`, commit
  message `from jarvis@${SOURCE_SHA}`, header comment rewritten
- `build-zoekt.yml` — `--repo phuongddx/jarvis-dist` in three places, plus
  comments
- `setup-smoke.yml` — the binary-name assertion becomes `jarvis`
- `publish-mcp-registry.yml` — comments only; its logic is name-agnostic

### Plugin surfaces

- `plugin/.claude-plugin/plugin.json` — `name: jarvis`, homepage and repository
  → `phuongddx/jarvis-dist`
- `.claude-plugin/marketplace.json` — both `name` fields
- `.codex-plugin/plugin.json` — `name`, `displayName`, and all four
  user-facing URLs. These currently point at the *private* repo and 404 for
  real users today; this rename incidentally fixes that live bug.
- `plugin/assets/codeintel-small.svg` → `jarvis-small.svg`, and the
  `composerIcon` reference to it
- `plugin/skills/codeintel-{setup,use,issues}/` → `jarvis-*/`, including each
  skill's `agents/openai.yaml`
- `.claude/skills/codeintel-release/` → `jarvis-release/`
- The issues skill's `gh issue create --repo` target →
  `phuongddx/jarvis-dist`

### One edit outside this repo

`jarvis-dist`'s own `README.md` is deliberately excluded from the sync — it
describes the distribution repo itself and has no counterpart here — so no
workflow can reach it. It currently reads "Public distribution surface for
codeintel… the package is on PyPI as codeintel-navigation-mcp". It and the
repo's GitHub description need a hand edit in the public repo.

## Documentation sweep

### Replacement order

Longest token first, or the sweep corrupts names — a naive `codeintel` →
`jarvis` turns `codeintel-navigation-mcp` into `jarvis-navigation-mcp` rather
than the chosen `jarvis-mcp`:

1. `codeintel-navigation-mcp` → `jarvis-mcp`
2. `io.github.phuongddx/codeintel` → `io.github.phuongddx/jarvis`
3. `phuongddx/codeintel` → `phuongddx/jarvis`
4. `.codeintel` → `.jarvis`
5. `CODEINTEL_` → `JARVIS_`
6. bare `codeintel` → `jarvis`

### Deliberate exclusions

Two occurrences must survive, both naming *Komodo Edit CodeIntel* — a real,
unrelated third-party package:

- `pyproject.toml:3` — inside the comment being rewritten anyway; the new text
  keeps naming Komodo as the reason the old dist name existed
- `CHANGELOG.md:161` — a historical entry explaining that same rationale

Separately, the documents *about* this rename must keep saying `codeintel` — a
rename spec that has been swept describes nothing. These are excluded wholesale:

- this spec, `docs/superpowers/specs/2026-08-04-jarvis-rename-design.md`
- its implementation plan under `plans/`
- `CHANGELOG.md`'s 0.5.0 entry, which names the old PyPI package precisely so a
  user who finds `codeintel-navigation-mcp` can follow it here

### Path renames

- `src/codeintel/` → `src/jarvis/`
- `plugin/skills/codeintel-{setup,use,issues}/`, `.claude/skills/codeintel-release/`
- `plugin/assets/codeintel-small.svg`
- `plans/0724-2316-codeintel-mcp-implementation/` and its 5 files
- `plans/reports/`: the 3 files carrying the old name
- `docs/superpowers/plans/2026-07-28-codeintel-skills.md`,
  `2026-08-01-codeintel-claude-plugin.md`, and their two `specs/` counterparts
- `docs/diagrams/codeintel-7layer-architecture.{excalidraw,png}`

The `.png` is the only artifact a text sweep cannot fix — "codeintel" is baked
into its pixels. The `.excalidraw` beside it is editable JSON, so the source is
edited and the PNG re-exported from it.

### Living documents

`README.md`, `CLAUDE.md`, `AGENTS.md`, `docs/index.html`,
`docs/system-architecture.md`, `docs/code-standards.md`,
`docs/codebase-summary.md`, `docs/project-roadmap.md`,
`docs/project-overview-pdr.md`, `plugin/README.md`. `README.md`'s dev-setup
`git clone` line points at `phuongddx/jarvis`.

`CHANGELOG.md` gets a 0.5.0 entry recording the rename, the old PyPI name and
that it stays installable, the repo renames, and that `~/.jarvis` starts empty
with no migration path.

## Verification

1. `uv run pytest -m "not integration"` green
2. `uv run python scripts/check_versions.py` green — proves the 5-file version
   agreement and that the new `jarvis-mcp>=0.5.0` floor parses
3. `git ls-files | xargs grep -il codeintel` returns only the four expected
   files: `pyproject.toml` and `CHANGELOG.md` (Komodo plus the 0.5.0 entry),
   this spec, and its implementation plan
4. `uv build`, install the wheel into a clean venv, complete a real MCP
   handshake — the only gate that catches a broken `module-name`
5. `sh setup.sh` with `JARVIS_BIN_DIR` redirected — proves the renamed env
   seams (`JARVIS_BIN_DIR`, `JARVIS_DATA_DIR`, `JARVIS_SETUP_SOURCED`)
6. `uv run pytest -m integration`, after a real `setup.sh` run populates
   `~/.jarvis/bin`
7. End-to-end: `uv run jarvis index .`, then `goToDefinition` through the
   server, confirming `~/.jarvis` works from empty

## Sequencing

Phases 1–4 are one branch, fully revertible.

1. **Prep** (manual, outside the repo) — create the PyPI pending publisher for
   `jarvis-mcp`; delete the four stale `codeintel` PATH blocks from `~/.zshrc`
2. **Code** — package dir, `pyproject.toml`, `config.py`, env vars, `setup.sh`,
   `check_versions.py`, tests → gates 1–2
3. **Distribution metadata** — `server.json`, three plugin JSONs, `.mcp.json`
   floor, four workflows, skill directories → gate 2
4. **Docs and history sweep** — replacement passes, path renames, PNG
   re-export, `CHANGELOG.md` 0.5.0 entry → gates 3–5
5. Merge to `main`
6. **Repo renames** — public → `jarvis-dist`, private → `jarvis`,
   `git remote set-url`, rename the secret, hand-edit `jarvis-dist`'s README
   and GitHub description
7. **Release** — tag `v0.5.0` → `publish-pypi` → `publish-mcp-registry` →
   `sync-public-distribution`
8. **Local reinstall** — `setup.sh`, `claude mcp remove codeintel` and
   `claude mcp add jarvis …`, re-index repos → gates 6–7

Phase 6 must precede 7: the trusted publisher expects repo `jarvis`, and the
sync workflow pushes to a `jarvis-dist` that has to exist.

## Rollback

Everything through phase 5 is a branch or a revertible commit. Phase 6 repo
renames are reversible by renaming back, in reverse order.

**Phase 7 leaves permanent public artifacts.** A PyPI filename can never be
re-uploaded, even after deleting the release, and the MCP registry likewise
rejects re-publishing a version with no way to amend one. So once `jarvis-mcp`
0.5.0 and `io.github.phuongddx/jarvis` 0.5.0 land, they exist forever, and
anyone who installed in the meantime keeps what they got.

The *decision* stays reversible: going back to `codeintel` would mean publishing
`codeintel-navigation-mcp` 0.5.0, a version still free since 0.4.0 was its last.
What cannot be undone is the debris — two published names instead of one.

The distinction that matters operationally: phases 1–6 are undoable leaving no
trace, phase 7 is not.

## Risks

| Risk | Consequence | Mitigation |
|---|---|---|
| PyPI pending publisher not created | Release fails at upload, error does not name the cause | Phase 1, before anything else |
| Repo renames done out of order | Step 2 fails outright | Public first, always |
| Unordered text replacement | `jarvis-navigation-mcp` instead of `jarvis-mcp` | Longest token first |
| Komodo references swept | Two comments assert a false history | Named exclusions, checked by gate 3 |
| This spec or its plan swept | The rename record describes nothing | Excluded wholesale, checked by gate 3 |
| `.mcp.json` floor left at 0.2.1 | Plugin resolves to nothing installable | Floor = 0.5.0, gate 2 |
| Stale `~/.codeintel/bin` on `PATH` | A binary missing from `~/.jarvis/bin` resolves from the old dir | Delete the four `.zshrc` lines in phase 1 |
| Old install URLs 404 after phase 6 | Any existing install breaks | Accepted; no users yet |
| PNG left un-rendered | Diagram contradicts its own filename | Edit `.excalidraw`, re-export |
