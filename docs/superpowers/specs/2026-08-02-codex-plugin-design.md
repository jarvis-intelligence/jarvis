# codeintel as a Codex CLI plugin — Design

## Problem

codeintel already ships as a Claude Code plugin (`plugin/.claude-plugin/plugin.json` +
`plugin/.mcp.json` + `plugin/skills/`), installable from the root marketplace manifest at
`.claude-plugin/marketplace.json`. The same package, MCP server (`codeintel-server`), and
skill content are equally useful under the **Codex CLI** — but Codex cannot consume the
Claude manifest. A Codex user today has no plugin to install, so they get no skills and must
hand-write `codex mcp add codeintel -- codeintel-server`.

Two facts establish feasibility, both verified against the Codex plugin cache rather than
assumed:

1. **Codex plugins use the same `.mcp.json` stdio schema as Claude.** The curated
   `build-ios-apps` plugin ships `plugins/build-ios-apps/.mcp.json` with the identical
   `{"mcpServers": {<name>: {"command": ..., "args": [...], "env": {...}}}}` shape that
   `plugin/.mcp.json` already uses (`uvx --from codeintel-navigation-mcp>=0.2.1
   codeintel-server`). Codex auto-registers an MCP server declared in a plugin's `.mcp.json`;
   no `codex mcp add` is needed for the base case.
2. **The Codex skill format is a superset-compatible neighbor of Claude's.** A Codex skill is
   a directory with `SKILL.md` (frontmatter needs `name` + `description`; Claude's extra
   `version` field is ignored harmlessly) plus an `agents/openai.yaml` sibling carrying
   Codex-specific agent metadata (`display_name`, `short_description`, `default_prompt`).
   Optional `references/` works identically in both. The existing `plugin/skills/` tree
   therefore serves both clients once each skill gains an `agents/openai.yaml`.

The skills' actual content (decision matrix, symbol-format rules, gotchas,
`references/tool-roster.md`) is client-agnostic; divergence is confined to a handful of
command lines and the new agent-metadata files.

## Scope

In scope (local scaffold; marketplace publishing is a separate later milestone):

- New Codex plugin manifest at the repo root: `.codex-plugin/plugin.json` (with full
  `interface` block), pointing at `./plugin/skills/` and `./plugin/assets/`.
- New `agents/openai.yaml` for each of the three skills (`codeintel-setup`,
  `codeintel-use`, `codeintel-issues`).
- Minimal client-aware edits to two `SKILL.md` files (`codeintel-setup` registration step,
  `codeintel-use` `semanticSearch` second-server workaround). `codeintel-issues/SKILL.md`
  needs no edit — it has no client-specific commands. Shared content stays untouched.
- New `plugin/assets/` with placeholder `codeintel-small.svg` + `app-icon.png` for the
  manifest's `interface` block.
- New `plugin/README.md` (covers both clients + a short Privacy section).
- New `plugin/LICENSE` (verbatim copy of the repo-root MIT LICENSE).
- Register `.codex-plugin/plugin.json` in `scripts/check_versions.py` so its `version`
  field cannot drift from the other four sources.

Out of scope (deferred to the "marketplace later" milestone):

- Submitting to the Codex curated marketplace (needs OpenAI review).
- A real brand mark (the SVG/PNG are placeholders).
- Codex `commands/*.md` slash-commands.
- A plugin-level top-level `agents/openai.yaml`.
- Rewriting the `codeintel-release` skill runbook prose (the version guard enforces
  correctness regardless; the runbook text is a documentation-only follow-up).
- Any change to `setup.sh`, the external binaries, or the Python package.

## Architecture

The Codex plugin reuses the existing `plugin/` content root. A single `.codex-plugin/`
manifest sits at the repo root (same level as the existing root `.claude-plugin/`
marketplace manifest) and points inward at the shared `plugin/skills/` and `plugin/assets/`
trees. Like the Claude plugin, the Codex plugin bundles **no runtime** — `plugin/.mcp.json`
points at the published PyPI package via `uvx`, so the plugin ships only manifests, skills,
assets, a README, and a LICENSE copy.

```
codeintel/
├── .claude-plugin/                  # existing — Claude MARKETPLACE
│   └── marketplace.json
├── .codex-plugin/                   # NEW — Codex PLUGIN manifest
│   └── plugin.json                  #   name/version/author + interface block + skills ptr
├── plugin/                          # shared plugin content root
│   ├── .claude-plugin/
│   │   └── plugin.json              # existing — Claude plugin manifest (untouched)
│   ├── .mcp.json                    # existing — auto-consumed by BOTH clients (untouched)
│   ├── assets/                      # NEW
│   │   ├── app-icon.png             #   logo (placeholder, rasterized from the svg)
│   │   └── codeintel-small.svg      #   composerIcon (placeholder mark)
│   ├── skills/                      # shared by both clients
│   │   ├── codeintel-setup/
│   │   │   ├── SKILL.md             # touched (client-aware registration step)
│   │   │   └── agents/openai.yaml   # NEW — Codex agent metadata
│   │   ├── codeintel-use/
│   │   │   ├── SKILL.md             # touched (semanticSearch gotcha)
│   │   │   ├── agents/openai.yaml   # NEW
│   │   │   └── references/
│   │   │       └── tool-roster.md   # existing, shared, untouched
│   │   └── codeintel-issues/
│   │       ├── SKILL.md             # untouched (no client-specific commands)
│   │       └── agents/openai.yaml   # NEW
│   ├── README.md                    # NEW — both clients + privacy
│   └── LICENSE                      # NEW — verbatim copy of repo-root MIT LICENSE
├── src/, tests/, scripts/, ...
```

Three load-bearing consequences:

- **One `.mcp.json`, two clients.** Codex reads `plugin/.mcp.json` automatically on plugin
  install (verified: `build-ios-apps` uses the same stdio `command`/`args`/`env` schema). No
  duplicate `codex mcp add` is needed for the base case. The `[semantic]`-extra second server
  is the only case requiring a manual `codex mcp add`, mirroring the existing Claude
  workaround.
- **One `skills/` tree, two clients.** Codex SKILL.md frontmatter needs `name` + `description`
  and harmlessly ignores the extra `version` field Claude uses. The same Markdown serves both;
  divergence is carried by the new `agents/openai.yaml` files plus a few client-branching
  command lines inside the skills.
- **Version lockstep widens to five sources.** `.codex-plugin/plugin.json` declares a
  `"version"`, so the existing version-consistency guard must learn about it. The release
  skill's "four files in lockstep" rule becomes five; the guard enforces this mechanically.

## Component designs

### `.codex-plugin/plugin.json`

Modeled on the curated `superpowers` and `build-macos-apps` manifests. Fields:

- Base identity (`name`, `version`, `description`, `author`, `homepage`, `repository`,
  `license`, `keywords`) mirror `plugin/.claude-plugin/plugin.json` so the two manifests stay
  obviously parallel.
- `version` is `"0.3.1"`, matching the other four version sources; the guard enforces this.
- `"skills": "./plugin/skills/"` points at the shared skills tree.
- `interface` block (Codex-specific): `displayName`, `shortDescription`, `longDescription`,
  `developerName`, `category` (`"Developer Tools"`), `capabilities`
  (`["Interactive", "Read", "Write"]` — `Write` because the skills run `codeintel index`/
  `reindex` which write under `~/.codeintel/`; `Interactive` because the issues skill prompts
  for confirmation before filing), `defaultPrompt` (3 example prompts), `websiteURL`,
  `privacyPolicyURL`, `termsOfServiceURL`, `brandColor` (`#3B82F6` placeholder),
  `composerIcon` (`./plugin/assets/codeintel-small.svg`), `logo`
  (`./plugin/assets/app-icon.png`), `screenshots: []`.

  The `privacyPolicyURL` and `termsOfServiceURL` point at the repo's own `plugin/README.md`
  privacy section and the MIT `LICENSE` respectively — more honest for a local-first tool
  that makes no network calls during queries than borrowing GitHub's generic policy URLs.

### `agents/openai.yaml` (one per skill)

Codex per-skill agent metadata. Each is a 4-line YAML file with an `interface` object holding
`display_name`, `short_description`, `default_prompt` (the `$skill-name` invocation form, per
the `build-macos-apps` convention). The `default_prompt` for each skill:

- `codeintel-setup`: "Use $codeintel-setup to install codeintel, register the MCP server, and
  index a repo for the first time."
- `codeintel-use`: "Use $codeintel-use to answer structural code questions via SCIP navigation
  (definition, references, call hierarchy) and search."
- `codeintel-issues`: "Use $codeintel-issues to gather context, classify against known
  limitations, and file a well-formed GitHub issue for codeintel."

### `SKILL.md` edits (three, minimal)

The skills are ~95% client-agnostic. Exactly two edits:

1. **`codeintel-setup/SKILL.md` §3 "Register the MCP server"** — currently Claude-only.
   Rewrite to: if the Codex (or Claude Code) plugin is installed, `.mcp.json` auto-registers
   the server, so skip. For a manual Codex registration: `codex mcp add codeintel --
   codeintel-server`. For Claude Code: `claude mcp add codeintel --scope user --
   codeintel-server`.
2. **`codeintel-use/SKILL.md` "semanticSearch" gotcha** — the documented second-server
   workaround currently shows only the `claude mcp add codeintel-semantic` form. Add the Codex
   equivalent: `codex mcp add codeintel-semantic -- uvx --from
   "codeintel-navigation-mcp[semantic]" codeintel-server`. The surrounding explanation (why
   the default registration is intentionally extra-free) is unchanged.
`codeintel-issues/SKILL.md` needs no edit — it has no client-specific commands; only its
new `agents/openai.yaml` sibling is added.

All other skill content — prerequisites, `setup.sh`, index commands, decision matrix,
symbol-format rules, freshness checks, known limitations, trigger examples — is identical
across clients and stays untouched. No new `references/` files.

### `plugin/assets/`

Two placeholder assets, both referenced by the manifest's `interface` block:

- `codeintel-small.svg` — a minimal monochrome SVG mark used as `composerIcon`. Placeholder;
  replace with a real brand mark before marketplace publishing.
- `app-icon.png` — a 256×256 (or 512×512) PNG used as `logo`, rasterized from the SVG (via
  `rsvg-convert`/`qlmanage`, with a hand-written minimal PNG fallback if neither tool is
  available). Placeholder.

The Claude plugin ships no assets; these are net-new and referenced only by the Codex
manifest.

### `plugin/README.md`

A single README covering both clients. Structure: title + one-line pitch; the nine MCP tools
in one sentence each (lifted from `references/tool-roster.md`, no detail duplication);
**Install** with two short subsections (Codex CLI: `codex plugin marketplace add <repo>` →
`codex plugin add codeintel`, noting `.mcp.json` auto-registers the server; Claude Code: the
existing `/plugin` flow, unchanged); optional extras (`[semantic]`, `[watch]`) with the
Codex second-server command for `semanticSearch`; a one-paragraph **Privacy** section
confirming codeintel is local-first (the only network egress is `uvx` fetching the wheel on
first server start, plus the optional embedding-model download if `[semantic]` is installed
— no telemetry, no query-time network); links to repo, `CHANGELOG.md`, issue tracker, and the
setup skill.

### `plugin/LICENSE`

A verbatim copy of the repo-root `LICENSE` (MIT). Codex marketplace tooling reads LICENSE
from the plugin root; the copy removes ambiguity. Maintenance note added to keep in sync if
the repo LICENSE ever changes.

### `scripts/check_versions.py`

Register `.codex-plugin/plugin.json`'s `"version"` field in the guard alongside the existing
four sources (`pyproject.toml`, `server.json` ×2, `plugin/.claude-plugin/plugin.json`).
`tests/test_check_versions.py` already exercises the guard's full file list, so it covers
the new source automatically once registered.

## Testing and validation

- **Unit tests:** no Python source changes; `uv run pytest -m "not integration"` stays green
  by construction. The only test-adjacent change is registering
  `.codex-plugin/plugin.json` in `check_versions.py`, which `test_check_versions.py` already
  exercises.
- **`test_setup_sh.py`:** unaffected (`ZOEKT_COMMIT`/`ZOEKT_COMMIT_PIN` untouched).
- **No new repo-side test for the plugin manifest:** the plugin is static JSON/YAML/Markdown.
  The reference Codex plugins ship no plugin-manifest tests; manifest validity is verified by
  Codex at install time (below).
- **Local validation (manual, pre-marketplace):**
  1. `codex plugin marketplace add /path/to/codeintel --name codeintel-local`
     → `codex plugin add codeintel@codeintel-local` → `codex plugin list` — confirms
     `.codex-plugin/plugin.json` parses, skills are discovered, `.mcp.json` is picked up.
  2. `codex mcp list` — should show `codeintel` auto-registered; then a tool call (e.g.
     `getIndexStatus`) confirms end-to-end.
  3. `uv run python scripts/check_versions.py` && `uv run pytest -m "not integration" -q`.

## Decisions

- **Reuse the existing `plugin/` root, add a sibling `.codex-plugin/` at the repo root.**
  Mirrors the Claude layout (root `.claude-plugin/` marketplace + `plugin/` content). One
  skills tree serves both clients; divergence lives in `agents/openai.yaml` and three
  command-line edits.
- **No bundled runtime.** `.mcp.json` points at `uvx`, identical to the Claude plugin. Keeps
  the plugin tiny and version-decoupled from the server.
- **Honest about `.mcp.json` auto-registration.** Codex reads it (verified against
  `build-ios-apps`); the setup skill says so plainly, with `codex mcp add` documented only as
  the manual alternative and for the `[semantic]` second server.
- **Placeholder assets, not gold-plating.** A minimal SVG + rasterized PNG satisfy the
  manifest's `interface` block for local install; real brand work waits for marketplace.
- **Version guard extended, release runbook noted.** The guard makes drift impossible; the
  runbook prose update is a follow-up, not a blocker.

## Risks

- **Codex's plugin/marketplace schema is underdocumented.** Mitigation: modeled on two real
  curated plugins (`superpowers`, `build-macos-apps`) and the curated marketplace README,
  which explicitly lists `.mcp.json`, `skills/`, `assets/`, and `agents/` as valid plugin
  surfaces. Local install validation (above) is the source of truth, not the docs.
- **Asset generation may fail on machines without `rsvg-convert`/`qlmanage`.** Mitigation:
  fall back to a hand-written minimal PNG; flag the placeholder for replacement.
- **Shared `SKILL.md` edits could drift from the Claude-only intent.** Mitigation: the three
  edits are explicitly client-branching (they mention both clients), not rewrites; shared
  content is untouched. The version guard and `test_setup_sh.py` are unaffected.
