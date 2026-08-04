# Codex CLI Plugin for jarvis — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Codex CLI plugin manifest + per-skill `agents/openai.yaml` files so `jarvis` installs under Codex via `codex plugin marketplace add` / `codex plugin add`, reusing the existing `plugin/` content root.

**Architecture:** Add `.codex-plugin/plugin.json` at the repo root (sibling of the existing root `.claude-plugin/` marketplace). It points at the shared `./plugin/skills/` tree and new `./plugin/assets/`. The existing `plugin/.mcp.json` is auto-consumed by Codex unchanged (verified: `build-ios-apps` uses the same stdio `command`/`args` schema). Each of the three skills gains a Codex-specific `agents/openai.yaml` sibling; two `SKILL.md` files get minimal client-aware edits. The version-consistency guard gains a fifth source.

**Tech Stack:** JSON manifests, YAML agent metadata, Markdown skills, SVG/PNG placeholder assets, Python `scripts/check_versions.py`.

**Spec:** `docs/superpowers/specs/2026-08-02-codex-plugin-design.md`

---

## File Structure

- Create: `.codex-plugin/plugin.json` — Codex plugin manifest + `interface` block.
- Create: `plugin/skills/jarvis-setup/agents/openai.yaml` — Codex agent metadata for setup.
- Create: `plugin/skills/jarvis-use/agents/openai.yaml` — Codex agent metadata for use.
- Create: `plugin/skills/jarvis-issues/agents/openai.yaml` — Codex agent metadata for issues.
- Create: `plugin/assets/jarvis-small.svg` — placeholder `composerIcon`.
- Create: `plugin/assets/app-icon.png` — placeholder `logo` (rasterized from the SVG).
- Create: `plugin/README.md` — two-client install/usage + Privacy section.
- Create: `plugin/LICENSE` — verbatim copy of repo-root MIT LICENSE.
- Modify: `plugin/skills/jarvis-setup/SKILL.md:36-43` — §3 "Register the MCP server" becomes client-aware.
- Modify: `plugin/skills/jarvis-use/SKILL.md:63-66` — `semanticSearch` gotcha adds the Codex second-server command.
- Modify: `scripts/check_versions.py` — register `.codex-plugin/plugin.json` as a fifth version source.

---

### Task 1: Version-consistency guard learns the Codex manifest

This comes first because it makes every later version drift impossible to miss. The guard already enforces four sources; we add the new fifth source before the file that declares it exists.

**Files:**
- Modify: `scripts/check_versions.py` (module docstring + `read_declared_versions()`)

- [ ] **Step 1: Write the failing test**

`tests/test_check_versions.py` already iterates the guard's full declared-version map, so extend its expected-location list. Open the test file and find the assertion listing the four expected locations (search for `pyproject.toml [project] version`). Add `".codex-plugin/plugin.json version"` to that expected set/list so the test demands the new source exist.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_check_versions.py -v`
Expected: FAIL — the guard does not yet read `.codex-plugin/plugin.json`, so either the key is missing or the expected-location assertion mismatches.

- [ ] **Step 3: Update the guard**

In `scripts/check_versions.py`:

1. Update the module docstring. The opening list currently names four files; add a fifth line:
   ```
   .codex-plugin/plugin.json          version
   ```
   And update the "Four files" sentence to "Five files".

2. In `read_declared_versions()`, after the `plugin = json.loads(...)` line that reads `plugin/.claude-plugin/plugin.json`, add a read of the Codex manifest and a new dict entry. The final return block becomes:
   ```python
   return {
       "pyproject.toml [project] version": pyproject["project"]["version"],
       "server.json version": server["version"],
       "server.json packages[0].version": server["packages"][0]["version"],
       "plugin/.claude-plugin/plugin.json version": plugin["version"],
       ".codex-plugin/plugin.json version": codex_plugin["version"],
   }
   ```
   And add the load immediately before the `return`:
   ```python
   codex_plugin = json.loads(
       (root / ".codex-plugin" / "plugin.json").read_text()
   )
   ```

- [ ] **Step 4: Run test to verify it still fails (file does not exist yet)**

Run: `uv run pytest tests/test_check_versions.py -v`
Expected: FAIL — `FileNotFoundError` on `.codex-plugin/plugin.json` (created in Task 2). This is the correct red state: the guard now demands the file exist.

- [ ] **Step 5: Commit (guard-only)**

```bash
git add scripts/check_versions.py tests/test_check_versions.py
git commit -m "feat(check-versions): track .codex-plugin/plugin.json as fifth version source"
```

---

### Task 2: The Codex plugin manifest

**Files:**
- Create: `.codex-plugin/plugin.json`

- [ ] **Step 1: Create the manifest**

Create `.codex-plugin/plugin.json` with exactly this content:

```json
{
  "name": "jarvis",
  "version": "0.3.1",
  "description": "Local-first SCIP code navigation and Zoekt search over your own indexed repositories, with skills that teach the agent to prefer structural queries over grep.",
  "author": {
    "name": "phuongddx",
    "email": "95doanphuong@gmail.com",
    "url": "https://github.com/phuongddx"
  },
  "homepage": "https://github.com/phuongddx/jarvis",
  "repository": "https://github.com/phuongddx/jarvis",
  "license": "MIT",
  "keywords": [
    "mcp",
    "code-intelligence",
    "code-search",
    "code-navigation",
    "scip",
    "zoekt"
  ],
  "skills": "./plugin/skills/",
  "interface": {
    "displayName": "jarvis",
    "shortDescription": "Local-first SCIP code navigation & Zoekt search",
    "longDescription": "Indexes your own git repositories with SCIP indexers and Zoekt, then answers structural questions — go-to-definition, find-references, call/type hierarchy, document symbols, lexical and semantic search, and cross-repo blast radius — through nine MCP tools. Skills steer the agent toward structural queries over grep and handle setup, everyday use, and bug reporting.",
    "developerName": "phuongddx",
    "category": "Developer Tools",
    "capabilities": ["Interactive", "Read", "Write"],
    "defaultPrompt": [
      "Find all callers of a function in this repo.",
      "Where is this symbol defined?",
      "Index this repo for code navigation."
    ],
    "websiteURL": "https://github.com/phuongddx/jarvis",
    "privacyPolicyURL": "https://github.com/phuongddx/jarvis/blob/main/plugin/README.md#privacy",
    "termsOfServiceURL": "https://github.com/phuongddx/jarvis/blob/main/plugin/LICENSE",
    "brandColor": "#3B82F6",
    "composerIcon": "./plugin/assets/jarvis-small.svg",
    "logo": "./plugin/assets/app-icon.png",
    "screenshots": []
  }
}
```

The `version` is `"0.3.1"` to match the other four sources (enforced by the guard from Task 1).

- [ ] **Step 2: Verify the guard now passes**

Run: `uv run python scripts/check_versions.py`
Expected: `versions consistent`

- [ ] **Step 3: Commit**

```bash
git add .codex-plugin/plugin.json
git commit -m "feat(codex-plugin): add .codex-plugin/plugin.json manifest"
```

---

### Task 3: Per-skill `agents/openai.yaml` files

Three small YAML siblings, one per skill directory. These carry Codex-specific agent metadata; `SKILL.md` stays the source of truth for skill content.

**Files:**
- Create: `plugin/skills/jarvis-setup/agents/openai.yaml`
- Create: `plugin/skills/jarvis-use/agents/openai.yaml`
- Create: `plugin/skills/jarvis-issues/agents/openai.yaml`

- [ ] **Step 1: Create the setup agent file**

`plugin/skills/jarvis-setup/agents/openai.yaml`:
```yaml
interface:
  display_name: "jarvis Setup"
  short_description: "Install and configure jarvis for Codex"
  default_prompt: "Use $jarvis-setup to install jarvis, register the MCP server, and index a repo for the first time."
```

- [ ] **Step 2: Create the use agent file**

`plugin/skills/jarvis-use/agents/openai.yaml`:
```yaml
interface:
  display_name: "jarvis Navigation"
  short_description: "Prefer jarvis MCP tools over grep for structural queries"
  default_prompt: "Use $jarvis-use to answer structural code questions via SCIP navigation (definition, references, call hierarchy) and search."
```

- [ ] **Step 3: Create the issues agent file**

`plugin/skills/jarvis-issues/agents/openai.yaml`:
```yaml
interface:
  display_name: "jarvis Issues"
  short_description: "File bugs and feature requests for jarvis"
  default_prompt: "Use $jarvis-issues to gather context, classify against known limitations, and file a well-formed GitHub issue for jarvis."
```

- [ ] **Step 4: Verify all three parse as YAML**

Run: `uv run python -c "import yaml; [yaml.safe_load(open(f'plugin/skills/{s}/agents/openai.yaml')) for s in ('jarvis-setup','jarvis-use','jarvis-issues')]" && echo OK`
Expected: `OK` (no parse errors). If `yaml` is unavailable in the base env, run `uv run --with pyyaml python -c "..."` instead.

- [ ] **Step 5: Commit**

```bash
git add plugin/skills/jarvis-setup/agents/openai.yaml \
        plugin/skills/jarvis-use/agents/openai.yaml \
        plugin/skills/jarvis-issues/agents/openai.yaml
git commit -m "feat(codex-plugin): add agents/openai.yaml for the three skills"
```

---

### Task 4: Client-aware edits to two SKILL.md files

Only two skills need content edits. `jarvis-issues/SKILL.md` has no client-specific commands and is left untouched (only its new `agents/openai.yaml` from Task 3 applies).

**Files:**
- Modify: `plugin/skills/jarvis-setup/SKILL.md` (§3 "Register the MCP server", around line 36)
- Modify: `plugin/skills/jarvis-use/SKILL.md` (the `semanticSearch` gotcha, around lines 63-66)

- [ ] **Step 1: Read the exact current §3 block in the setup skill**

Run: `sed -n '36,50p' plugin/skills/jarvis-setup/SKILL.md`
Note the exact current text spanning from `## 3. Register the MCP server` through the start of `## 4. Index a repo` so the replacement is precise.

- [ ] **Step 2: Replace §3 with the client-aware version**

In `plugin/skills/jarvis-setup/SKILL.md`, replace the §3 block (from the `## 3. Register the MCP server` heading through the blank line before `## 4. Index a repo`) with:

```markdown
## 3. Register the MCP server

If you installed the Codex **or** Claude Code plugin, the bundled `plugin/.mcp.json` auto-registers the `jarvis` MCP server — skip this step. (Both clients consume the same stdio `.mcp.json`.)

For a manual registration without the plugin:

Codex CLI:

```bash
codex mcp add jarvis -- jarvis-server
```

Claude Code:

```bash
claude mcp add jarvis --scope user -- jarvis-server
```

Cursor / other MCP clients: point them at the stdio command `jarvis-server`. No HTTP server, no auth, no network.
```

- [ ] **Step 3: Add the Codex second-server command to the use skill's `semanticSearch` gotcha**

In `plugin/skills/jarvis-use/SKILL.md`, the gotcha block currently ends with a single Claude command:

```bash
  claude mcp add jarvis-semantic --scope user -- uvx --from "jarvis-mcp[semantic]" jarvis-server
```

Replace that single command line with both clients:

```markdown
  claude mcp add jarvis-semantic --scope user -- uvx --from "jarvis-mcp[semantic]" jarvis-server
  codex mcp add jarvis-semantic -- uvx --from "jarvis-mcp[semantic]" jarvis-server
```

Leave the surrounding explanation (why the default registration is intentionally extra-free, and that `jarvis` is already taken by the plugin's registration) unchanged.

- [ ] **Step 4: Verify no other client-specific commands were missed**

Run: `rg -n "claude mcp add" plugin/skills/`
Expected: exactly two hits — the §3 setup command and the gotcha second-server command — each now accompanied by a matching `codex mcp add` line.

- [ ] **Step 5: Commit**

```bash
git add plugin/skills/jarvis-setup/SKILL.md plugin/skills/jarvis-use/SKILL.md
git commit -m "feat(codex-plugin): make setup and use skills client-aware"
```

---

### Task 5: Placeholder assets

Two files referenced by the manifest's `interface` block. Both are placeholders to be replaced before marketplace publishing.

**Files:**
- Create: `plugin/assets/jarvis-small.svg`
- Create: `plugin/assets/app-icon.png`

- [ ] **Step 1: Create the SVG**

Create `plugin/assets/jarvis-small.svg` with a minimal monochrome mark:

```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <rect width="64" height="64" rx="12" fill="#0F172A"/>
  <circle cx="28" cy="28" r="12" fill="none" stroke="#3B82F6" stroke-width="4"/>
  <line x1="37" y1="37" x2="48" y2="48" stroke="#3B82F6" stroke-width="5" stroke-linecap="round"/>
  <text x="32" y="58" font-family="ui-monospace, monospace" font-size="10" fill="#3B82F6" text-anchor="middle" font-weight="700">ci</text>
</svg>
```

The `#3B82F6` matches the manifest's `brandColor`.

- [ ] **Step 2: Rasterize to PNG**

Try in order, stopping at the first that produces a PNG:

```bash
mkdir -p plugin/assets
rsvg-convert -w 256 -h 256 plugin/assets/jarvis-small.svg -o plugin/assets/app-icon.png \
  || qlmanage -t -s 256 -o plugin/assets plugin/assets/jarvis-small.svg \
     && mv plugin/assets/jarvis-small.svg.png plugin/assets/app-icon.png 2>/dev/null
```

Verify the PNG exists and is non-empty:
```bash
test -s plugin/assets/app-icon.png && file plugin/assets/app-icon.png
```
Expected: `... PNG image data, 256 x 256, ...` (or similar). If neither `rsvg-convert` nor `qlmanage` produced a file, stop and flag "manual PNG creation needed" in the task handoff — do not commit an empty file.

- [ ] **Step 3: Commit**

```bash
git add plugin/assets/jarvis-small.svg plugin/assets/app-icon.png
git commit -m "feat(codex-plugin): add placeholder composerIcon and logo assets"
```

---

### Task 6: `plugin/README.md` and `plugin/LICENSE`

**Files:**
- Create: `plugin/README.md`
- Create: `plugin/LICENSE` (verbatim copy of repo-root LICENSE)

- [ ] **Step 1: Copy the LICENSE**

```bash
cp LICENSE plugin/LICENSE
```

Verify: `diff -q LICENSE plugin/LICENSE` should produce no output.

- [ ] **Step 2: Create the README**

Create `plugin/README.md`:

```markdown
# jarvis

Local-first code intelligence: SCIP navigation and Zoekt search over your own indexed repositories. Installs as a plugin for **Codex CLI** and **Claude Code**, exposing nine MCP tools and three agent skills.

## What it gives you

Nine MCP tools (all take `repo` = the slug from `jarvis index`):

- `documentSymbols` — every top-level symbol in a file, each with its range.
- `goToDefinition` — resolve a symbol's definition site(s).
- `findReferences` — every occurrence of a symbol.
- `callHierarchy` — single-level incoming + outgoing calls.
- `typeHierarchy` — super/subtypes (errors on real indexes; see the use skill's gotchas).
- `getIndexStatus` — whether a repo has a published index, plus freshness.
- `searchCode` — lexical search via Zoekt (lazy-started webserver).
- `semanticSearch` — vector + Zoekt hybrid via reciprocal rank fusion (needs the `[semantic]` extra).
- `blastRadius` — 2-hop package-dependency BFS across indexed repos.

Full signatures and return shapes: see the `jarvis-use` skill's `references/tool-roster.md`.

## Install

### Codex CLI

The plugin's bundled `plugin/.mcp.json` auto-registers the `jarvis` MCP server on install — no `codex mcp add` needed for the base case.

```bash
codex plugin marketplace add https://github.com/phuongddx/jarvis --ref main
codex plugin add jarvis
```

Then run the `jarvis-setup` skill (or follow its steps manually): install external binaries via `setup.sh`, then `jarvis index /path/to/repo`.

### Claude Code

```text
/plugin marketplace add phuongddx/jarvis
/plugin install jarvis@jarvis
```

Then the same `setup.sh` + `jarvis index` flow.

### Optional extras

- `[semantic]` for `semanticSearch` — install with `uv tool install "jarvis-mcp[semantic]"`, then register a second MCP server (the `jarvis` name is already taken by the plugin's default registration):
  ```bash
  codex mcp add jarvis-semantic -- uvx --from "jarvis-mcp[semantic]" jarvis-server
  claude mcp add jarvis-semantic --scope user -- uvx --from "jarvis-mcp[semantic]" jarvis-server
  ```
- `[watch]` for `jarvis watch` (foreground auto-reindex on file changes).

## Privacy

jarvis is local-first. The only network egress is `uvx` fetching the published wheel on first server start, and — if you install the optional `[semantic]` extra — the one-time embedding-model download by `sentence-transformers`. No telemetry, no analytics, and no outbound calls during queries. Published indexes are opened read-only (`mode=ro&immutable=1`).

## Links

- Repository: <https://github.com/phuongddx/jarvis>
- Changelog: [`CHANGELOG.md`](https://github.com/phuongddx/jarvis/blob/main/CHANGELOG.md)
- Issues: <https://github.com/phuongddx/jarvis/issues>
- Full onboarding: the `jarvis-setup` skill.
```

- [ ] **Step 3: Commit**

```bash
git add plugin/README.md plugin/LICENSE
git commit -m "feat(codex-plugin): add plugin README (both clients) and LICENSE copy"
```

---

### Task 7: Full validation

No new code; this task exists to run the repo's existing gates and the manual local-install check before declaring done.

**Files:** none

- [ ] **Step 1: Version guard**

Run: `uv run python scripts/check_versions.py`
Expected: `versions consistent`

- [ ] **Step 2: Unit tests (CI gate)**

Run: `uv run pytest -m "not integration" -q`
Expected: all pass. No Python source changed; this is a regression sanity check plus `test_check_versions.py` confirming the fifth source.

- [ ] **Step 3: Local Codex install + MCP auto-registration**

```bash
codex plugin marketplace add /Users/ddphuong/Projects/jarvis --name jarvis-local
codex plugin add jarvis@jarvis-local
codex plugin list
codex mcp list
```
Expected: `codex plugin list` shows `jarvis` installed; `codex mcp list` shows `jarvis` auto-registered (proof that Codex consumed `plugin/.mcp.json`). If `codex mcp list` is empty, the plugin install did not pick up `.mcp.json` — investigate before proceeding.

- [ ] **Step 4: Tool call smoke test**

With a repo already indexed, invoke a tool through the now-registered server, e.g. `getIndexStatus(repo="<an-existing-slug>")`. A non-error response confirms the pipeline works end-to-end under Codex.

- [ ] **Step 5: No final commit unless validation surfaced changes**

If steps 1-4 are clean, there is nothing to commit. If a fix was needed, commit it with `fix(codex-plugin): ...`.

---

## Notes for the implementer

- **No `codex mcp add` for the base case.** Codex auto-consumes `plugin/.mcp.json` on plugin install (verified against the curated `build-ios-apps` plugin). The setup skill and README say so; do not add a manual-registration step for the default server.
- **`jarvis-issues/SKILL.md` is untouched.** It has no client-specific commands (`rg "claude|codex|mcp add" plugin/skills/jarvis-issues/SKILL.md` returns nothing). Only its new `agents/openai.yaml` is added.
- **Asset generation may fail on a headless machine.** If neither `rsvg-convert` nor `qlmanage` is available, flag it — do not hand-craft a binary PNG by hand. The manifest paths still resolve once a real asset is dropped in; local install validation (Task 7 step 3) tolerates a missing icon.
- **The release runbook is a follow-up, not a task here.** The version guard makes drift impossible; updating `.claude/skills/jarvis-release/SKILL.md` prose to say "five files" is a documentation task tracked separately.
