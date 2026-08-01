# codeintel Claude Code Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship codeintel's three agent skills as a Claude Code plugin so they load in every repository, not only inside the codeintel checkout.

**Architecture:** A light plugin in a `plugin/` subdirectory — three skills plus a `.mcp.json` that registers the server via `uvx` from the already-published PyPI package. No runtime is bundled. The subdirectory (rather than repo root) is deliberate: a root `.mcp.json` doubles as Claude Code's project-scoped MCP config and would silently point contributors at the published package instead of their working tree.

**Tech Stack:** JSON manifests (`plugin.json`, `marketplace.json`, `.mcp.json`), Markdown skills, Python 3.12 (`tomllib`) for the version-drift guard, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-01-codeintel-claude-plugin-design.md`

## Global Constraints

- Published package name is **`codeintel-navigation-mcp`** — never `codeintel-mcp` (unpublished) or `codeintel` (squatted on PyPI by an abandoned package). The import package, CLI, and server binary keep the plain `codeintel` name.
- Current release version is **`0.2.1`**. The four files declaring it must be identical.
- The `.mcp.json` compatibility floor is **`>=0.2.1`** and is checked with `<=`, never `==`. 0.2.1 is the correct floor for a reason: 0.2.0's uncapped `mcp` dependency lets `mcp>=2.0` resolve in and the server fails to start.
- `.mcp.json` must **not** include the `semantic` extra — it would pull gigabytes into first MCP start.
- Marketplace name `codeintel`, plugin name `codeintel`, `source` is `"./plugin"`.
- Author identity everywhere: `phuongddx`, `95doanphuong@gmail.com` (matches `pyproject.toml`).
- Skills address **end users** (bare `codeintel …`); `CLAUDE.md` addresses **contributors** (`uv run codeintel …`). Do not harmonize them.
- `setup.sh` remains a required manual step. No skill may imply the plugin alone yields a working codeintel.
- Never mutate a published `index-<sha>.db`; queries open read-only.

## File Structure

**Create:**
- `.claude-plugin/marketplace.json` — marketplace manifest; one entry pointing at `./plugin`.
- `plugin/.claude-plugin/plugin.json` — plugin identity and version.
- `plugin/.mcp.json` — registers the `codeintel` MCP server via `uvx`.
- `scripts/check_versions.py` — version-drift guard.
- `tests/test_check_versions.py` — its unit test.

**Move:**
- `.claude/skills/codeintel-{setup,use,issues}/` → `plugin/skills/…`
- `.claude/skills/codeintel-{use,setup}-workspace/` → `evals/…` (untracked, gitignored)

**Modify:**
- `README.md` — "Agent skills" section.
- `.github/workflows/test.yml` — add version-check step.
- `.gitignore` — add `evals/`.
- The three `SKILL.md` files — command forms, install flow, version-command bug.

**Delete:**
- `scripts/link_skills.py`, `tests/test_link_skills.py` (ZCode retired).

---

### Task 1: Plugin manifests

**Files:**
- Create: `.claude-plugin/marketplace.json`
- Create: `plugin/.claude-plugin/plugin.json`
- Create: `plugin/.mcp.json`

**Interfaces:**
- Consumes: nothing.
- Produces: `plugin/` as a valid plugin root; `plugin.json` `version` field and `.mcp.json` `--from` floor, both read by Task 3's checker.

- [ ] **Step 1: Create the plugin manifest**

`plugin/.claude-plugin/plugin.json`:

```json
{
  "name": "codeintel",
  "description": "Local-first SCIP code navigation and Zoekt search over your own indexed repositories, with skills that teach Claude to prefer structural queries over grep.",
  "version": "0.2.1",
  "author": {
    "name": "phuongddx",
    "email": "95doanphuong@gmail.com"
  },
  "homepage": "https://github.com/phuongddx/codeintel",
  "repository": "https://github.com/phuongddx/codeintel",
  "license": "MIT",
  "keywords": [
    "mcp",
    "code-intelligence",
    "code-search",
    "code-navigation",
    "scip",
    "zoekt"
  ]
}
```

- [ ] **Step 2: Create the MCP server registration**

`plugin/.mcp.json`. The floor and the absence of `[semantic]` are both load-bearing — see Global Constraints:

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

- [ ] **Step 3: Create the marketplace manifest**

`.claude-plugin/marketplace.json`. Note the plugin entry deliberately has **no** `version` field: it is optional (of 276 plugins in the official marketplace, only 14 declare it), and omitting it removes a fifth place for the version to drift. `claude plugin tag` validates agreement only when the field is present.

```json
{
  "name": "codeintel",
  "metadata": {
    "description": "Local-first code intelligence for Claude Code — SCIP navigation and Zoekt search over your own repositories."
  },
  "owner": {
    "name": "phuongddx",
    "email": "95doanphuong@gmail.com"
  },
  "plugins": [
    {
      "name": "codeintel",
      "source": "./plugin",
      "description": "SCIP navigation and Zoekt search MCP server, plus skills for setup, everyday structural queries, and bug reporting."
    }
  ]
}
```

- [ ] **Step 4: Validate both manifests**

Run:
```bash
claude plugin validate plugin
claude plugin validate .
```
Expected: both report valid. The plugin currently has no `skills/` directory — that is fine, Task 2 adds it. If validation *requires* at least one component, note the error and proceed to Task 2, then re-run here.

- [ ] **Step 5: Commit**

```bash
git add .claude-plugin/marketplace.json plugin/.claude-plugin/plugin.json plugin/.mcp.json
git commit -m "feat(plugin): add plugin and marketplace manifests

Registers the codeintel MCP server via uvx against the published
codeintel-navigation-mcp package. The >=0.2.1 floor is deliberate: 0.2.0's
uncapped mcp dependency allows mcp>=2.0 to resolve in, which stops the server
from starting.

The manifests live under plugin/ rather than the repo root because a root
.mcp.json is also Claude Code's project-scoped MCP config, which would point
contributors at the published package instead of their working tree."
```

---

### Task 2: Relocate the skills and retire the ZCode loader

**Files:**
- Move: `.claude/skills/codeintel-{setup,use,issues}/` → `plugin/skills/`
- Move: `.claude/skills/codeintel-{use,setup}-workspace/` → `evals/`
- Delete: `scripts/link_skills.py`, `tests/test_link_skills.py`
- Modify: `README.md` ("## Agent skills" section), `.gitignore`

**Interfaces:**
- Consumes: `plugin/` from Task 1.
- Produces: `plugin/skills/{codeintel-setup,codeintel-use,codeintel-issues}/SKILL.md`, edited in Task 4.

- [ ] **Step 1: Commit the pending troubleshooting fix first**

`.claude/skills/codeintel-setup/SKILL.md` has an uncommitted edit (the `--only zoekt` troubleshooting row). Commit it *before* moving, so the move records as a clean rename rather than a delete-plus-add:

```bash
git add .claude/skills/codeintel-setup/SKILL.md
git commit -m "docs(skills): name the correct --only value for reinstalling zoekt

setup.sh's --only accepts 'zoekt', not 'zoekt-index'; the table previously
left the value unstated, which led to the invalid form being guessed."
```

- [ ] **Step 2: Move the skills**

```bash
mkdir -p plugin/skills
git mv .claude/skills/codeintel-setup plugin/skills/codeintel-setup
git mv .claude/skills/codeintel-use plugin/skills/codeintel-use
git mv .claude/skills/codeintel-issues plugin/skills/codeintel-issues
```

- [ ] **Step 3: Rescue the eval workspaces, then remove `.claude/skills/`**

These are untracked and hold the iteration-1 benchmarks — the evidence behind the skills' current wording. Moving them out before deleting the directory is the whole point of this step:

```bash
mkdir -p evals
mv .claude/skills/codeintel-use-workspace evals/
mv .claude/skills/codeintel-setup-workspace evals/
rmdir .claude/skills 2>/dev/null || ls -A .claude/skills
```

If `rmdir` fails, something unexpected remains — inspect it rather than forcing removal.

- [ ] **Step 4: Gitignore the workspaces**

Append to `.gitignore`:

```
# Skill eval workspaces (benchmark output, not plugin content)
evals/
```

- [ ] **Step 5: Delete the ZCode loader and its test**

```bash
git rm scripts/link_skills.py tests/test_link_skills.py
```

- [ ] **Step 6: Rewrite the README "Agent skills" section**

Locate by the `## Agent skills` heading, not line number (the README shifted when #9 renamed the package). Replace the entire section through to `## Standards` with:

````markdown
## Agent skills

Three agent skills ship in the Claude Code plugin, under `plugin/skills/`:

- `codeintel-setup` — install, register, index, verify.
- `codeintel-use` — prefer codeintel for structural queries (find references, go-to-definition, hierarchy).
- `codeintel-issues` — file codeintel bugs/features via `gh`.

Install them, and register the MCP server, with:

```bash
/plugin marketplace add phuongddx/codeintel
/plugin install codeintel@codeintel
```

The plugin registers the `codeintel` MCP server itself, so the `claude mcp add`
step above is only needed if you are not using the plugin.
````

(The four-backtick fence above is only this plan's quoting of a block that itself contains a fenced example — write three backticks into the README.)

- [ ] **Step 7: Verify the suite shrank by exactly the deleted tests**

Run:
```bash
uv run pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: PASS. The count drops by the number of tests in the deleted `test_link_skills.py` (it had 95 lines; confirm the drop matches its test count and nothing else disappeared). If any *other* test fails, something referenced `link_skills` or the old skills path — fix that before committing.

- [ ] **Step 8: Confirm the plugin now exposes the skills**

Run:
```bash
claude plugin validate plugin
```
Expected: valid, with three skills detected.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "refactor(plugin): move skills into the plugin, drop the ZCode loader

Skills in .claude/skills/ only load inside this repo, which is backwards for a
tool meant to navigate other repositories. They now live in plugin/skills/ as
the single source of truth, served user-wide once the plugin is installed.

scripts/link_skills.py existed only to symlink them into ~/.zcode/skills/ for a
ZCode agent that is no longer used; repointing it would mean maintaining a
loader for a consumer that does not exist. Eval workspaces move to a gitignored
evals/ so they are neither shipped inside plugin/ nor lost."
```

---

### Task 3: Version-drift guard

**Files:**
- Create: `scripts/check_versions.py`
- Test: `tests/test_check_versions.py`
- Modify: `.github/workflows/test.yml`

**Interfaces:**
- Consumes: `plugin/.claude-plugin/plugin.json` and `plugin/.mcp.json` from Task 1.
- Produces: `check_versions.check(root: Path) -> list[str]` (empty list means consistent), `read_declared_versions(root) -> dict[str, str]`, `read_floor(root) -> str`.

- [ ] **Step 1: Write the failing test**

`tests/test_check_versions.py`:

```python
"""Tests for scripts/check_versions.py -- the version-drift guard.

Each test builds a miniature repo under tmp_path rather than reading the real
files, so a failure here reflects the checker's logic and never whatever the
working tree happens to hold at the time.
"""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "check_versions.py"


def _load_checker():
    """Import the script by path -- scripts/ is not an importable package."""
    spec = importlib.util.spec_from_file_location("check_versions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_versions = _load_checker()


def _build(root: Path, *, version="0.2.1", plugin_version=None, floor="0.2.1"):
    """Write the four version-bearing files plus .mcp.json into root."""
    plugin_version = plugin_version or version
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "codeintel-navigation-mcp"\nversion = "{version}"\n'
    )
    (root / "server.json").write_text(
        json.dumps(
            {
                "version": version,
                "packages": [
                    {"identifier": "codeintel-navigation-mcp", "version": version}
                ],
            }
        )
    )
    plugin_dir = root / "plugin" / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "codeintel", "version": plugin_version})
    )
    (root / "plugin" / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "codeintel": {
                        "command": "uvx",
                        "args": [
                            "--from",
                            f"codeintel-navigation-mcp>={floor}",
                            "codeintel-server",
                        ],
                    }
                }
            }
        )
    )


def test_consistent_versions_pass(tmp_path):
    _build(tmp_path)
    assert check_versions.check(tmp_path) == []


def test_plugin_version_drift_is_reported(tmp_path):
    _build(tmp_path, version="0.2.1", plugin_version="0.2.0")
    problems = check_versions.check(tmp_path)
    assert len(problems) == 1
    # A bare "versions differ" would force the reader to diff four files by
    # hand, so the message must name the files and show both values.
    assert "plugin.json" in problems[0]
    assert "0.2.0" in problems[0] and "0.2.1" in problems[0]


def test_floor_ahead_of_release_is_reported(tmp_path):
    _build(tmp_path, version="0.2.1", floor="0.3.0")
    problems = check_versions.check(tmp_path)
    assert any("0.3.0" in p and "ahead" in p for p in problems)


def test_floor_behind_release_is_allowed(tmp_path):
    # The floor is the oldest tolerated package, not the current one, so it is
    # expected to lag. Flagging this would make the check useless.
    _build(tmp_path, version="0.3.0", floor="0.2.1")
    assert check_versions.check(tmp_path) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_check_versions.py -v`
Expected: FAIL — `FileNotFoundError` / import error, because `scripts/check_versions.py` does not exist yet.

- [ ] **Step 3: Write the checker**

`scripts/check_versions.py`:

```python
"""Assert every file declaring the release version agrees.

Four files carry the release version and must be identical:

    pyproject.toml                     [project] version
    server.json                        version
    server.json                        packages[0].version
    plugin/.claude-plugin/plugin.json  version

.claude-plugin/marketplace.json deliberately omits a version for its plugin
entry. The field is optional, and leaving it out removes a fifth place to drift.

plugin/.mcp.json carries something different in kind: the OLDEST package the
plugin tolerates, in its `--from` specifier. That is a compatibility floor, not
a release version, so it is checked with <= rather than ==. Auto-syncing it to
the current version would force needless upgrades on users and would make this
check assert nothing.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PACKAGE = "codeintel-navigation-mcp"


def _version_tuple(version: str) -> tuple[int, ...]:
    """Parse a plain X.Y.Z version for ordering.

    Deliberately does not handle pre-release or local segments: this project
    ships plain semver, and an int tuple avoids depending on `packaging`, which
    is only ever present transitively here. A non-numeric segment raises rather
    than silently comparing wrong.
    """
    return tuple(int(part) for part in version.split("."))


def read_declared_versions(root: Path) -> dict[str, str]:
    """Map a human-readable location -> the version string declared there."""
    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    server = json.loads((root / "server.json").read_text())
    plugin = json.loads(
        (root / "plugin" / ".claude-plugin" / "plugin.json").read_text()
    )
    return {
        "pyproject.toml [project] version": pyproject["project"]["version"],
        "server.json version": server["version"],
        "server.json packages[0].version": server["packages"][0]["version"],
        "plugin/.claude-plugin/plugin.json version": plugin["version"],
    }


def read_floor(root: Path) -> str:
    """Extract the >= floor from plugin/.mcp.json's `--from` specifier."""
    mcp = json.loads((root / "plugin" / ".mcp.json").read_text())
    args = mcp["mcpServers"]["codeintel"]["args"]
    spec = args[args.index("--from") + 1]
    name, separator, floor = spec.partition(">=")
    if not separator:
        raise ValueError(f"--from spec {spec!r} declares no >= floor")
    if name != PACKAGE:
        raise ValueError(f"--from names {name!r}, expected {PACKAGE!r}")
    return floor


def check(root: Path) -> list[str]:
    """Return a list of problems. An empty list means everything agrees."""
    problems: list[str] = []

    declared = read_declared_versions(root)
    if len(set(declared.values())) > 1:
        detail = "\n".join(f"    {where}: {what}" for where, what in declared.items())
        problems.append(f"release version differs between files:\n{detail}")

    version = declared["pyproject.toml [project] version"]
    floor = read_floor(root)
    if _version_tuple(floor) > _version_tuple(version):
        problems.append(
            f"plugin/.mcp.json floor {floor} is ahead of the released version "
            f"{version}: the plugin would resolve to nothing installable"
        )

    return problems


def main() -> int:
    problems = check(REPO_ROOT)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        return 1
    print("versions consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_check_versions.py -v`
Expected: 4 passed.

- [ ] **Step 5: Run it against the real repo**

Run: `uv run python scripts/check_versions.py`
Expected: `versions consistent`. If it reports drift, the manifests from Task 1 disagree with `pyproject.toml`/`server.json` — fix the manifest, not the checker.

- [ ] **Step 6: Prove it actually fails (a check that cannot fail is worth nothing)**

```bash
python3 - <<'EOF'
import json, pathlib
p = pathlib.Path("plugin/.claude-plugin/plugin.json")
d = json.loads(p.read_text()); d["version"] = "9.9.9"
p.write_text(json.dumps(d, indent=2) + "\n")
EOF
uv run python scripts/check_versions.py; echo "exit=$?"
git checkout plugin/.claude-plugin/plugin.json
uv run python scripts/check_versions.py; echo "exit=$?"
```
Expected: first run exits 1 and names `plugin.json` with both `9.9.9` and `0.2.1`; after restore, exits 0.

- [ ] **Step 7: Wire it into CI**

Append to `.github/workflows/test.yml`, after the `Run the unit suite` step, at the same indentation:

```yaml
      - name: Check version consistency
        run: |
          set -eu
          # Runs on every pull request, not only at release, so drift is caught
          # when it is introduced rather than when it ships.
          uv run python scripts/check_versions.py
```

- [ ] **Step 8: Commit**

```bash
git add scripts/check_versions.py tests/test_check_versions.py .github/workflows/test.yml
git commit -m "ci: guard the release version against drift across four files

pyproject.toml, server.json (twice), and plugin.json must all agree. The
.mcp.json floor is checked with <= instead, since it states the oldest
tolerated package rather than the current release -- syncing it to the current
version would force needless upgrades and make the check assert nothing."
```

---

### Task 4: Update the skills for installed-package usage

**Files:**
- Modify: `plugin/skills/codeintel-setup/SKILL.md`
- Modify: `plugin/skills/codeintel-use/SKILL.md`
- Modify: `plugin/skills/codeintel-issues/SKILL.md`

**Interfaces:**
- Consumes: skills relocated in Task 2.
- Produces: nothing consumed by later tasks.

Match on the quoted strings below rather than line numbers — the files moved in Task 2.

- [ ] **Step 1: Rewrite `codeintel-setup` step 2 (install)**

The current step tells the user to `uv sync`, which only works from a clone. Replace the code block and add the trailing paragraph:

Find:
```bash
curl -fsSL https://raw.githubusercontent.com/phuongddx/codeintel/main/setup.sh | sh
uv sync
```
Replace with:
```bash
curl -fsSL https://raw.githubusercontent.com/phuongddx/codeintel/main/setup.sh | sh
uv tool install codeintel-navigation-mcp
```

Then, after the existing "Binaries installed:" paragraph, add:

```markdown
`uv tool install` puts `codeintel` (the CLI) and `codeintel-server` (the MCP server) on `PATH`. Optional extras: `uv tool install "codeintel-navigation-mcp[semantic]"` for `semanticSearch`, `[watch]` for `codeintel watch`.
```

- [ ] **Step 2: Rewrite `codeintel-setup` step 3 (register)**

Find:
```bash
claude mcp add codeintel --scope user -- uv --directory /path/to/codeintel run codeintel-server
```
Replace with:
```bash
claude mcp add codeintel --scope user -- codeintel-server
```

Immediately under the `## 3. Register the MCP server` heading, insert:

```markdown
If you installed the Claude Code plugin, the server is already registered — skip this step.
```

And replace the "Cursor / other MCP clients" sentence's command, changing `uv --directory /path/to/codeintel run codeintel-server` to `codeintel-server`.

- [ ] **Step 3: Drop the `uv run` prefix from end-user commands**

In `plugin/skills/codeintel-setup/SKILL.md` (four occurrences):
- `uv run codeintel index /path/to/your/repo` → `codeintel index /path/to/your/repo`
- `uv run codeintel index /path/to/your/repo --slug foo` → `codeintel index /path/to/your/repo --slug foo`
- `uv run codeintel index /path/to/your/repo --scheme MyScheme` → `codeintel index /path/to/your/repo --scheme MyScheme`
- `uv run codeintel status <slug>` → `codeintel status <slug>`

In `plugin/skills/codeintel-use/SKILL.md` (one occurrence):
- `uv run codeintel reindex <slug>` → `codeintel reindex <slug>`

In `plugin/skills/codeintel-issues/SKILL.md` (one occurrence):
- `uv run codeintel status <slug>` → `codeintel status <slug>`

- [ ] **Step 4: Fix the broken version command in `codeintel-issues`**

This is a real bug: the distribution is `codeintel-navigation-mcp`, so `version('codeintel')` raises `PackageNotFoundError` on any clean install. It only appears to work in this repo because the dev venv has accumulated three distributions across two renames.

Find:
```
- codeintel version: `uv run python -c "import importlib.metadata; print(importlib.metadata.version('codeintel'))"` (codeintel has no `--version` flag; this reads it from package metadata).
```
Replace with:
```
- codeintel version: `python3 -c "import importlib.metadata; print(importlib.metadata.version('codeintel-navigation-mcp'))"` (codeintel has no `--version` flag; this reads it from the distribution metadata — note the distribution is `codeintel-navigation-mcp`, not `codeintel`).
```

- [ ] **Step 5: Verify no stale command forms remain**

Run:
```bash
grep -rn "uv run\|uv sync\|uv --directory\|version('codeintel')" plugin/skills/
```
Expected: no output. Any hit is a missed occurrence.

- [ ] **Step 6: Verify the version command against a clean distribution set**

Run:
```bash
uv sync --reinstall -q
uv run python -c "import importlib.metadata as m; print([d.metadata['Name'] for d in m.distributions() if 'codeintel' in (d.metadata['Name'] or '').lower()])"
```
Expected: the stale `codeintel` and `codeintel-mcp` entries are gone, leaving only `codeintel-navigation-mcp`. If stale entries survive `--reinstall`, delete `.venv` and re-run `uv sync` — they are leftovers from the pre-rename builds and will keep masking this bug locally.

- [ ] **Step 7: Commit**

```bash
git add plugin/skills
git commit -m "docs(skills): target the installed package rather than a clone

End users install via 'uv tool install codeintel-navigation-mcp', which puts
codeintel and codeintel-server on PATH, so 'uv run' and 'uv --directory' are
wrong in user-facing text. CLAUDE.md keeps 'uv run' -- it addresses
contributors working from a checkout.

Also fixes the version command in codeintel-issues: it queried the 'codeintel'
distribution, which does not exist on a clean install. It only appeared to work
because this repo's dev venv still carries distributions from two earlier
names."
```

---

### Task 5: End-to-end verification

**Files:** none modified — this task proves the previous four work.

**Interfaces:**
- Consumes: everything.
- Produces: a verified install path.

- [ ] **Step 1: Clean-environment server smoke test**

The plugin is worthless if `uvx` cannot start the server. Run from a directory with no checkout and no `.venv`:

```bash
cd /tmp && time uvx --from "codeintel-navigation-mcp>=0.2.1" codeintel-server </dev/null
```

**The `</dev/null` is required, not decorative.** `codeintel-server` is a stdio MCP server with no `--help`: given a terminal it blocks waiting for a client and the step never returns. Redirecting from `/dev/null` feeds it immediate EOF, so it starts, finds no client, and exits.

Expected: exit status 0 with no output. Silence is the pass condition here — it proves `uvx` resolved the package and every import succeeded. An import error or missing dependency surfaces as a traceback instead.

Record the cold-start duration. If it is slow enough to risk an MCP startup timeout, the fallback is the `${CLAUDE_PLUGIN_ROOT}` approach described in the spec. (Do not reach for `timeout` to bound this — it is not installed on this machine.)

- [ ] **Step 2: Install the plugin from a local marketplace**

This exercises the same path a stranger uses:

```bash
claude plugin marketplace add /Users/ddphuong/Projects/codeintel
claude plugin install codeintel@codeintel
claude plugin list
```
Expected: `codeintel` listed as installed.

- [ ] **Step 3: Confirm the skills load from a *different* repository**

This is the acceptance criterion for the original problem. Verifying inside the codeintel repo would pass vacuously, because the old in-repo skills would have satisfied it too. Start a Claude Code session in another indexed repo:

```bash
cd /Users/ddphuong/Projects/epost-workspace/polaris-ai-plaform/polaris-app
```
Confirm `codeintel:codeintel-setup`, `codeintel:codeintel-use`, and `codeintel:codeintel-issues` appear in the available-skills list, and that the `codeintel` MCP server is connected.

- [ ] **Step 4: Confirm the server answers a real query through the plugin**

Still in `polaris-app`, call:
```
getIndexStatus(repo: "polaris-app", repo_path: "<that repo's path>")
```
Expected: a non-error response reporting `indexed`. This proves manifests, `uvx` resolution, and the shared `~/.codeintel` data directory all line up.

- [ ] **Step 5: Full unit suite and version check**

Run:
```bash
uv run pytest -m "not integration" -q 2>&1 | tail -3
uv run python scripts/check_versions.py
```
Expected: PASS, and `versions consistent`.

- [ ] **Step 6: Push and open the pull request**

```bash
git push -u origin feat/claude-code-plugin
gh pr create --title "feat: ship codeintel's agent skills as a Claude Code plugin" --body "$(cat <<'BODY'
Skills in `.claude/skills/` only load inside this repo, which is backwards for a
tool whose purpose is navigating *other* repositories — 21 are indexed on this
machine and the skills were silent in all of them.

- Plugin manifests under `plugin/`, registering the MCP server via `uvx` against
  the published `codeintel-navigation-mcp` package.
- Skills moved to `plugin/skills/` as the single source of truth.
- `scripts/link_skills.py` deleted — it loaded skills for a retired ZCode agent.
- CI guard against the release version drifting across the four files that
  declare it.
- Skills retargeted at the installed package, fixing a version command that was
  broken on every clean install.

Design: `docs/superpowers/specs/2026-08-01-codeintel-claude-plugin-design.md`
Plan: `docs/superpowers/plans/2026-08-01-codeintel-claude-plugin.md`
BODY
)"
```

- [ ] **Step 7: Confirm CI is green**

Run: `gh pr checks --watch`
Expected: the three `unit` legs and the version-consistency step all pass.

---

## Notes for the implementer

**The `plugin/` subdirectory is not arbitrary.** Moving these manifests to the repo root would make `.mcp.json` act as project-scoped MCP config for anyone opening this repo, registering a server that runs the *published* package instead of their working tree. Local changes would then appear to have no effect — a confusing failure that costs hours. Leave it where it is.

**The floor and the version are different kinds of thing.** If a future change makes the version check simpler by treating `.mcp.json`'s floor as a fifth lockstep field, that is a regression, not a simplification. The floor lags on purpose.

**Skills and CLAUDE.md disagree on purpose.** Skills say `codeintel …` (end users, installed package); CLAUDE.md says `uv run codeintel …` (contributors, working tree). A future reader will be tempted to unify them. Do not.
