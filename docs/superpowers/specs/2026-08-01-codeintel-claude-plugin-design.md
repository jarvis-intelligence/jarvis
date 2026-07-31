# codeintel as a Claude Code plugin — Design

## Problem

The three agent skills built in `2026-07-28-codeintel-skills-design.md` live in
`.claude/skills/`, which Claude Code loads **only when the working directory is inside this
repo**. That is exactly backwards from what the skills are for: codeintel's purpose is
navigating *other* repositories. On this machine 21 repos are registered
(`polaris-app`, `epost-ios-theme-showcase`, `agent-kernel`, …) and `codeintel-use` is silent
in every one of them. The skill only fires where it is least needed.

A Claude Code plugin installs user-wide, which fixes availability. It also fixes a second
problem: today a stranger wanting codeintel must clone the repo, `uv sync`, and hand-write a
`claude mcp add` invocation containing a machine-specific absolute path. Both problems have
the same solution shape — publish the package, then ship a plugin that carries the skills and
registers the server declaratively.

Two facts establish feasibility, both verified rather than assumed:

1. **The server is published and installable.** `codeintel-navigation-mcp` **0.2.1** is live on
   PyPI, shipped by `.github/workflows/publish-pypi.yml` (release-triggered, with
   `workflow_dispatch` as a manual fallback). `uv tool install codeintel-navigation-mcp` puts
   both entry points — `codeintel` and `codeintel-server` — on `PATH`. Note the distribution
   name is **not** `codeintel-mcp` (unpublished, and never used) and not `codeintel` (squatted
   by an abandoned Komodo Edit package); only the import package, CLI, and server binary keep
   the plain `codeintel` name.
2. **`${CLAUDE_PLUGIN_ROOT}` works in a bundled `.mcp.json`.** The official `telegram` and
   `imessage` plugins use `bun run --cwd "${CLAUDE_PLUGIN_ROOT}"` in exactly that file. This
   design does *not* rely on it (see Architecture), but it is the fallback if the `uvx` path
   proves too slow.

The README's install steps (lines 13-25) already describe this exact flow —
`uv tool install codeintel-navigation-mcp` → `codeintel index …` → `claude mcp add codeintel
--scope user -- codeintel-server` — and are correct as written. This design adds the plugin
alongside that path; it does not replace or rewrite it.

## Scope

In scope:

- Plugin scaffolding: `.claude-plugin/marketplace.json`, `plugin/.claude-plugin/plugin.json`,
  `plugin/.mcp.json`.
- Move the three skills to `plugin/skills/` as the single source of truth.
- Delete `scripts/link_skills.py` and `tests/test_link_skills.py` (ZCode is retired).
- Add a version-consistency check script + unit test, wired into `test.yml`.
- Rewrite README's "Agent skills" section (lines 289-301); add the plugin install lines.
- Fix the `codeintel-issues` version command (a real bug — see Component designs).
- Convert end-user command forms in the skills from `uv run codeintel …` to `codeintel …`.

Already done, and therefore not tasks here — recorded because earlier drafts of this spec
carried them and a reader of the git history will otherwise wonder where they went:

- PyPI release infrastructure (`publish-pypi.yml`) and the 0.2.1 publish itself (#8, #9, #10).
- Official MCP Registry listing — `server.json` (`io.github.phuongddx/codeintel`, `registryType:
  pypi`) plus `publish-mcp-registry.yml` (#11).

Out of scope — these are the other tiers of
`plans/reports/distribution-strategy-0731-2335-codeintel-mcp-adoption-report.md` and each
deserves its own spec:

- Community plugin-marketplace submission (clau.de/plugin-directory-submission).
- README funnel restructure, Docker/OCI image, launch posts.
- Any change to `setup.sh`. The external binaries (`scip`, `zoekt-index`, `zoekt-webserver`,
  per-language indexers) remain a separate manual install step. Neither PyPI nor a plugin can
  carry them; this is codeintel's known install-weight disadvantage and this design does not
  pretend to solve it.

## Architecture

The plugin does **not** bundle a runtime. `.mcp.json` points at the published PyPI package via
`uvx`, so the plugin ships only skills plus two small manifests.

```
codeintel/
├── .claude-plugin/
│   └── marketplace.json         # marketplace "codeintel"; plugins[0].source = "./plugin"
├── plugin/                      # ← plugin root
│   ├── .claude-plugin/
│   │   └── plugin.json          # name/version/description/author/homepage/license/keywords
│   ├── .mcp.json                # registers the codeintel server via uvx
│   └── skills/
│       ├── codeintel-setup/SKILL.md
│       ├── codeintel-use/SKILL.md
│       │   └── references/tool-roster.md
│       └── codeintel-issues/SKILL.md
├── src/ tests/ docs/ scripts/   # unchanged
└── .claude/skills/              # removed; contents moved into plugin/skills/
```

### Why `plugin/` and not the repo root

The prior strategy report proposes `"source": "./"`. This design deviates, for a concrete
reason: a `.mcp.json` at a repository root is *also* Claude Code's project-scoped MCP
configuration. With the plugin at the root, every contributor who opens this repo would be
prompted to register a `codeintel` server that runs the **published** package — silently
shadowing their own working tree and making local changes appear to have no effect. A
subdirectory removes that failure mode at the cost of one level of nesting. Ship weight is
unchanged either way: the marketplace clones the whole repository regardless, and `source`
merely selects which subtree is the plugin root.

### `plugin/.mcp.json`

```json
{
  "mcpServers": {
    "codeintel": {
      "command": "uvx",
      "args": ["--from", "codeintel-navigation-mcp>=0.2.1", "codeintel-server"]
    }
  }
}
```

Deliberately **without** the `semantic` extra. Including it would pull
`lancedb`/`sentence-transformers`/`torch` — gigabytes — into the first MCP server start, risking
a startup timeout for a feature that is optional by design. `semanticSearch` already fails soft
with an install hint, so users opt in with `uvx --from "codeintel-navigation-mcp[semantic]" codeintel-server`.
This preserves the existing "gated behind the optional extra, non-fatal" architecture.

The `>=0.2.1` floor is the plugin's compatibility contract. `plugin.json` has no field capable
of expressing a dependency on a PyPI package, so `--from` is the only place a floor actually
binds — verified: `uvx --from "codeintel-navigation-mcp>=0.2.1" codeintel-server` resolves and
installs. 0.2.1 is the correct floor rather than an arbitrary one: 0.2.0's uncapped `mcp`
dependency lets a `mcp>=2.0` resolve in, and the server then fails to start (the bug #10 fixed).
A plugin that could silently install 0.2.0 would reintroduce exactly that failure.

### Versioning

Two different kinds of version live in this repo, and conflating them is the trap:

**Lockstep set** — `pyproject.toml`'s `version`, `server.json`'s two `version` fields, and
`plugin.json`'s `version`. All four are "what this release *is*" and must be identical
(currently **0.2.1**). A release-time check enforces this; see Release.

**Compatibility floor** — `.mcp.json`'s `>=0.2.1`. This is "the oldest package this plugin can
tolerate," a different question entirely. It must **not** be auto-synced to the current version:
bumping it on every release would force needless upgrades and, worse, make the check green while
saying nothing. It moves only when a real incompatibility appears. The release check must
therefore assert `floor <= version`, not `floor == version`.

### Naming

Marketplace `codeintel`, plugin `codeintel`, giving:

```
/plugin marketplace add phuongddx/codeintel
/plugin install codeintel@codeintel
```

Skill names are unchanged, so they surface as `codeintel:codeintel-setup` etc. The prefix
repetition is mildly redundant but conventional (`axiom:axiom-swiftui`,
`codex:codex-cli-runtime`), and renaming would discard the trigger-description tuning
validated by the iteration-1 evals (`codeintel-use` scored 100% with-skill vs 57.5% baseline).

## Component designs

### `plugin/skills/` — moved, not copied

`git mv` each of `.claude/skills/codeintel-{setup,use,issues}` to preserve history. `.claude/skills/`
is then gone: once the plugin is installed user-wide it serves these skills in *every* repo
including this one, so a second in-repo copy would be redundant and would drift. Dogfooding
happens by installing from a local marketplace path, which has the side benefit of exercising
the same install path strangers use.

The eval workspaces (`codeintel-use-workspace/`, `codeintel-setup-workspace/`) currently sit
*inside* `.claude/skills/`, so deleting that directory would destroy them. They are build
artifacts of skill development — not plugin content — but they hold the iteration-1 benchmark
and graded runs, which are the evidence for the skills' current wording. Relocate them to
`evals/` at the repo root and add `evals/` to `.gitignore`: preserved, never shipped inside
`plugin/`, and still untracked as they are today. Do **not** place them as siblings of
`plugin/skills/` (the skill-creator default), because anything under `plugin/` ships to users.

### End-user command forms

An end user gets a real `codeintel` on `PATH` via `uv tool install codeintel-navigation-mcp`, so `uv run`
is wrong in user-facing skill text. Six occurrences change:

| File | Line | Current |
|---|---|---|
| `codeintel-setup/SKILL.md` | 43, 44, 45 | `uv run codeintel index …` |
| `codeintel-setup/SKILL.md` | 53 | `uv run codeintel status <slug>` |
| `codeintel-use/SKILL.md` | 54 | `uv run codeintel reindex <slug>` |
| `codeintel-issues/SKILL.md` | 17 | `uv run codeintel status <slug>` |

`codeintel-issues` line 20 also loses its `uv run` prefix, but it needs a substantive fix as
well and is handled in the next subsection — do not treat this table as the complete list of
`uv run` removals.

`CLAUDE.md` keeps `uv run codeintel …` throughout — that file is contributor-facing and `uv run`
is correct there. The distinction to hold onto: **skills address end users, CLAUDE.md addresses
contributors.** Do not "harmonize" them.

### `codeintel-issues` version command — a real bug

Line 20 currently instructs:

```bash
uv run python -c "import importlib.metadata; print(importlib.metadata.version('codeintel'))"
```

The distribution is `codeintel-navigation-mcp`; `codeintel` is only the import package. Under PEP 503
normalization these are different names, so on a clean `uv tool install codeintel-navigation-mcp` this
raises `PackageNotFoundError`. It appears to work in this repo only because the dev venv has
accumulated **three** distributions across two renames — verified:

```
$ uv run python -c "import importlib.metadata as m; print([d.metadata['Name'] for d in m.distributions() if 'codeintel' in (d.metadata['Name'] or '').lower()])"
['codeintel', 'codeintel-mcp', 'codeintel-navigation-mcp']
```

Only the third is real; `codeintel` and `codeintel-mcp` are stale leftovers from the earlier
names. So the command a new user is told to run when filing their first bug report is broken
for everyone except the author — and the accumulating leftovers mean the dev venv will keep
masking it. Worth a `uv sync --reinstall` locally to confirm the fix against a clean
distribution set.
Fix to `importlib.metadata.version('codeintel-navigation-mcp')`, and since the user has the CLI on PATH,
drop the `uv run` prefix.

### `scripts/link_skills.py` — delete

The script exists solely to symlink the skills into `~/.zcode/skills/` for a ZCode agent that is
no longer in use. Repointing it at `plugin/skills/` would mean maintaining a loader for a
consumer that does not exist, so delete it along with `tests/test_link_skills.py`.

This is a deliberate reduction in scope, not an oversight: the plugin now *is* the distribution
mechanism, and a second, hand-rolled one competing with it is the kind of thing that quietly
rots. If a non-Claude-Code agent ever needs these skills again, `plugin/skills/` is a plain
directory of Markdown and a three-line `ln -s` loop recreates the capability.

Remove the corresponding README instruction (the `uv run python scripts/link_skills.py` block)
in the same change, or the README will document a script that no longer exists.

### `.claude-plugin/marketplace.json`

Schema per the installed examples (`axiom-marketplace`, `claude-plugins-official`): top-level
`name`, `metadata.description`, `owner{name,email}`, and `plugins[]` with
`{name, version, source, description, author}`. `source` is `"./plugin"`. Author details come
from `pyproject.toml`'s existing `authors` entry (phuongddx, 95doanphuong@gmail.com) so there is
one identity, not two.

### Release

No new workflow. `publish-pypi.yml` already ships the package on GitHub release (with
`workflow_dispatch` as a manual fallback), and `publish-mcp-registry.yml` updates the MCP
Registry entry from `server.json`.

The plugin itself needs no build or upload — the marketplace consumes it straight from the git
repository. What it does need is a guard against version drift, since `plugin.json` adds a
fourth file to the three that already move together.

**Add a version-consistency check**, run in CI on every pull request (not only at release, so
drift is caught when it is introduced rather than when it ships). It asserts:

1. `pyproject.toml.version` == `server.json.version` == `server.json.packages[0].version` ==
   `plugin.json.version`.
2. The `.mcp.json` floor is `<=` that version — a floor *ahead* of the published package means
   the plugin installs nothing, which is the one failure mode that breaks every user at once.

Implement as a small Python script under `scripts/` with a unit test, wired into `test.yml` as a
separate step. Python rather than shell because it parses TOML and JSON, and `tomllib` is in the
3.12 standard library this project already requires. A step is cheaper than a new workflow and
inherits the existing three-leg matrix for free.

### README

Lines 13-25 (install steps 1-4) need **no change** — they already describe this design. Rewrite
the "Agent skills" section: correct the path from `.claude/skills/` to `plugin/skills/`, lead
with the two-line plugin install as the way to get the skills, and **drop the
`uv run python scripts/link_skills.py` block entirely** along with the script.

Line numbers in this spec were accurate when written but the README has since changed (#9
renamed the package in three places). Locate the section by its `## Agent skills` heading rather
than by line number.

## Error handling

- Skills remain documentation. With `link_skills.py` deleted, the only executable this change
  adds is the version-consistency check, whose failure must name **which** files disagree and
  what each holds — a bare "versions differ" forces the reader to go diff four files by hand.
- A missing `uvx` (no `uv` installed) surfaces as a Claude Code MCP startup failure, not a
  codeintel error. `codeintel-setup` already lists `uv` as a prerequisite in step 1; keep that
  ordering so the failure is pre-empted rather than diagnosed.
- Publishing is irreversible: a released version cannot be replaced, only yanked. Clean-env
  verification therefore gates the tag, not the reverse.

## Testing

1. `claude plugin validate` on `plugin/` — the same check the community marketplace runs at
   submission, so failing it here is strictly cheaper.
2. **Clean-environment smoke test:** in a directory with no `.venv` and no repo checkout,
   `uvx --from codeintel-navigation-mcp codeintel-server` starts and answers one tool call
   (`getIndexStatus` against an already-indexed slug is sufficient and read-only). This is also
   what proves the stale-dist-info bug above is genuinely fixed, since a clean env has only the
   `codeintel-navigation-mcp` distribution. Time the first (cold) start and record it.
3. **Dogfood the real install path:** `/plugin marketplace add /Users/ddphuong/Projects/codeintel`,
   install, then confirm all three skills load **from inside a different repository** — e.g.
   `polaris-app`. This is the acceptance criterion for the original problem; verifying inside
   the codeintel repo would pass vacuously and prove nothing.
4. Version-consistency check fails when any one of the four versions is edited in isolation, and
   when the `.mcp.json` floor is pushed above the package version. A check that cannot fail is
   worth nothing, so prove both directions before trusting it.
5. `uv run pytest -m "not integration"` green — nothing in this change should touch `src/`, so a
   regression here means the change leaked out of scope. Since #6, `test.yml` runs exactly this
   on every pull request across three legs (ubuntu 3.12/3.13, macOS 3.13), so this check is
   automated; run it locally only for a faster signal before pushing. Note the suite gets
   *smaller* here — deleting `test_link_skills.py` removes tests, so confirm the drop is exactly
   those and nothing else.

## Risks

- **Cold `uvx` resolve latency** on first MCP start. Mitigated by excluding `semantic`; quantified
  by test 2. If it proves too slow, the fallback is the `${CLAUDE_PLUGIN_ROOT}` approach whose
  feasibility is already confirmed.
- **Irreversible publish.** Ordering discipline: tests 1-2 pass before the tag is cut.
- **Version drift** across the four lockstep files — now mechanically enforced by the CI check
  rather than left to discipline. The residual risk moves to the check itself being wrong, which
  test 4 addresses by proving it fails when it should.
- **The floor going stale in the other direction.** Nothing forces `>=0.2.1` upward, so if a
  future release makes the plugin genuinely require newer, an untouched floor lets users install
  a package too old for it. The CI check deliberately cannot catch this — only a human noticing
  a compatibility break can. Accepted: the alternative (auto-bumping the floor) trades a rare
  failure for a guaranteed stream of forced upgrades.
- **`setup.sh` remains manual**, so "install the plugin" is not sufficient to get a working
  codeintel. The skills must not imply otherwise — `codeintel-setup` keeps the binaries step
  first and prominent.

## Resolved questions

All three questions this spec opened have been decided:

1. **Version drift → automate.** A CI check on every pull request, not a release-time-only
   assertion. See Release.
2. **Declare a floor.** `>=0.2.1`, expressed in `.mcp.json`'s `--from` rather than `plugin.json`,
   which has no field able to bind a PyPI dependency. See `plugin/.mcp.json`.
3. **ZCode is retired → delete** `scripts/link_skills.py` and its test rather than repoint them.

No open questions remain; the design is ready to plan against.
