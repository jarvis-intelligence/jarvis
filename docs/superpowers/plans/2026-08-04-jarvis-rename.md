# codeintel → jarvis Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the project from `codeintel` to `jarvis` across every surface — source package, CLIs, environment variables, data directory, PyPI distribution, MCP registry entry, plugin metadata, skills, both GitHub repositories, and all documentation including dated history.

**Architecture:** Tasks 1–9 are ordered text and path renames on a single branch, each gated by the existing test suite. Task 10 runs the full verification battery. Task 11 is a manual runbook for the irreversible parts (PyPI publisher, repo renames, release). The correctness risk is not difficulty but *substitution order*: several old→new mappings are prefixes of each other, and applying them in the wrong sequence silently produces names like `jarvis-navigation-mcp` or `jarvis-dist-dist`.

**Tech Stack:** Python 3.12+, `uv` (build backend + runner), pytest, POSIX sh (`setup.sh`), GitHub Actions, PyPI trusted publishing, MCP Registry.

**Spec:** `docs/superpowers/specs/2026-08-04-jarvis-rename-design.md`

## Global Constraints

- **Version for this release: `0.5.0`.** It must be identical in exactly five places: `pyproject.toml` `[project] version`, `server.json` `version`, `server.json` `packages[0].version`, `plugin/.claude-plugin/plugin.json` `version`, `.codex-plugin/plugin.json` `version`. `scripts/check_versions.py` enforces this.
- **PyPI distribution name: `jarvis-mcp`.** Not `jarvis` (taken by an unrelated package), not `jarvis-navigation-mcp`.
- **Import package name: `jarvis`.** The distribution name deliberately differs, which only works via `[tool.uv.build-backend] module-name = "jarvis"` in `pyproject.toml`.
- **`plugin/.mcp.json` `--from` floor: `jarvis-mcp>=0.5.0`.** It must equal the release version, not lag behind it: `jarvis-mcp` is a brand-new PyPI project with no release history, so any lower floor resolves to nothing installable.
- **Private dev repo (this one) becomes `phuongddx/jarvis`.** Used for: the dev-setup `git clone` line, the PyPI trusted publisher binding, `server.json`'s `repository.url`.
- **Public distribution repo becomes `phuongddx/jarvis-dist`.** Used for: the `curl` installer URL, the plugin marketplace path, zoekt release assets, the issue tracker, and every user-facing plugin URL.
- **Data directory: `~/.jarvis`.** No migration. It starts empty; `~/.codeintel` is left in place untouched.
- **Environment variable prefix: `JARVIS_`** for all 13 variables.
- **macOS `sed` requires the BSD form `sed -i ''`** (empty backup suffix as a separate argument). GNU `sed -i` without an argument fails here.
- **Never rename these:** the two references to *Komodo Edit CodeIntel* (`pyproject.toml:3`, `CHANGELOG.md:161`) — a real third-party package; `phuongddx/scip-swift` (`setup.sh:38`); the MCP tool names (`goToDefinition`, `findReferences`, `callHierarchy`, `typeHierarchy`, `documentSymbols`, `searchCode`, `semanticSearch`, `blastRadius`, `getIndexStatus`); the `io.github.phuongddx/*` namespace itself; the on-disk `scip/_/<slug>/_/` path shape.
- **Never rewrite:** this plan, or `docs/superpowers/specs/2026-08-04-jarvis-rename-design.md`. Both must keep saying `codeintel` to remain intelligible.

### The canonical substitution order

Every text-sweeping task in this plan uses this exact chain, in this exact order. Applying it out of order corrupts names. Copy it verbatim.

```bash
# The order is load-bearing. Two hazards it defuses:
#   1. `phuongddx/jarvis` is a PREFIX of `phuongddx/jarvis-dist`. Pass 1 must run
#      before any -dist string exists, or it produces `jarvis-dist-dist`.
#   2. `codeintel` is a SUBSTRING of `codeintel-navigation-mcp`. The bare pass
#      must run last, or the dist name becomes `jarvis-navigation-mcp`.
sweep() {
  for f in "$@"; do
    [ -f "$f" ] || continue
    sed -i '' \
      -e 's|phuongddx/jarvis|phuongddx/jarvis-dist|g' \
      -e 's|raw\.githubusercontent\.com/phuongddx/codeintel|raw.githubusercontent.com/phuongddx/jarvis-dist|g' \
      -e 's|io\.github\.phuongddx/codeintel|io.github.phuongddx/jarvis|g' \
      -e 's|phuongddx/codeintel|phuongddx/jarvis|g' \
      -e 's/codeintel-navigation-mcp/jarvis-mcp/g' \
      -e 's/\.codeintel/.jarvis/g' \
      -e 's/CODEINTEL_/JARVIS_/g' \
      -e 's/CodeIntel/Jarvis/g' \
      -e 's/Codeintel/Jarvis/g' \
      -e 's/codeintel/jarvis/g' \
      "$f"
  done
}
```

Pass 4 (`phuongddx/codeintel` → `phuongddx/jarvis`) is correct for dev-clone and trusted-publisher contexts but **wrong** for user-facing `github.com` URLs, which need `jarvis-dist`. Pass 2 catches `raw.githubusercontent.com` automatically; the remaining `github.com` cases are fixed by hand in Tasks 5 and 8, which name each one explicitly.

---

### Task 1: Move the Python package and repoint entry points

**Files:**
- Move: `src/codeintel/` → `src/jarvis/` (17 `.py` modules)
- Modify: `pyproject.toml:11` (name), `pyproject.toml:77-79` (`[project.scripts]`), `pyproject.toml:85-87` (`module-name`), `pyproject.toml:1-10` (rationale comment)
- Modify: `src/jarvis/server.py:16-22` (imports), `src/jarvis/server.py:24` (`FastMCP` name)
- Modify: `src/jarvis/index_cli.py:1118` (`argparse` prog)
- Modify: `src/jarvis/__init__.py` (delete dead scaffold)
- Test: `tests/test_server_tools.py` (new test), all `tests/*.py` import lines

**Interfaces:**
- Consumes: nothing — this is the first task.
- Produces: the import root `jarvis`. Every later task imports `from jarvis import config`, `from jarvis.query import QueryService`, etc. The two console scripts are `jarvis = "jarvis.index_cli:main"` and `jarvis-server = "jarvis.server:main"`. The advertised MCP server name is the string `"jarvis"`, readable as `mcp.name`.

- [ ] **Step 1: Write the failing test for the advertised MCP server name**

This name is what users see in `/mcp` output and is currently untested. Append to `tests/test_server_tools.py`:

```python
def test_mcp_server_advertises_the_jarvis_name():
    """The FastMCP instance name is user-visible in `/mcp` output and in a
    client's server list, so it is part of the public surface, not an
    implementation detail."""
    from jarvis.server import mcp

    assert mcp.name == "jarvis"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_server_tools.py::test_mcp_server_advertises_the_jarvis_name -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'jarvis'`.

- [ ] **Step 3: Move the package directory**

```bash
git mv src/codeintel src/jarvis
```

- [ ] **Step 4: Rewrite import statements across source and tests**

Only the import root changes in this step. Environment variables and data-dir strings are Task 2.

```bash
sed -i '' \
  -e 's/^from codeintel/from jarvis/' \
  -e 's/^from codeintel\./from jarvis./' \
  -e 's/^import codeintel/import jarvis/' \
  -e 's/from codeintel import/from jarvis import/g' \
  -e 's/from codeintel\./from jarvis./g' \
  -e 's/import codeintel\./import jarvis./g' \
  -e 's/patch("codeintel\./patch("jarvis./g' \
  -e "s/patch('codeintel\./patch('jarvis./g" \
  -e 's/monkeypatch\.setattr("codeintel\./monkeypatch.setattr("jarvis./g' \
  src/jarvis/*.py tests/*.py tests/fixtures/*.py
```

Then find any remaining import-shaped references the patterns above missed:

```bash
grep -rn "codeintel" src/jarvis/*.py tests/*.py tests/fixtures/*.py | grep -E "import|patch|setattr|monkeypatch"
```

Expected: no output. Fix any hits by hand.

- [ ] **Step 5: Repoint `pyproject.toml`**

Replace the rationale comment and name (`pyproject.toml:1-11`) with:

```toml
[project]
# The plain `jarvis` name on PyPI is taken by an unrelated package, so the
# distribution is published as `jarvis-mcp`. This MUST stay in sync with the
# project name on the PyPI trusted publisher: if the two disagree, uploads
# fail with "Non-user identities cannot create new projects" rather than
# anything that names the real problem.
#
# The import package, both CLIs, and the MCP server are all `jarvis` — only
# the name you install differs.
#
# (Before 0.5.0 this project was called codeintel and shipped as
# `codeintel-navigation-mcp`, whose plain name was likewise taken — by the
# abandoned Komodo Edit CodeIntel package. That distribution is still
# installable at 0.4.0 and receives no further releases.)
name = "jarvis-mcp"
```

Replace `[project.scripts]` (`pyproject.toml:77-79`):

```toml
[project.scripts]
jarvis = "jarvis.index_cli:main"
jarvis-server = "jarvis.server:main"
```

Replace the build-backend block (`pyproject.toml:85-87`):

```toml
[tool.uv.build-backend]
# The distribution is `jarvis-mcp`, which would otherwise make the backend
# look for `src/jarvis_mcp/`. The import package is and stays `jarvis`.
module-name = "jarvis"
```

- [ ] **Step 6: Rename the two user-visible name strings**

`src/jarvis/server.py:24`:

```python
mcp = FastMCP("jarvis")
```

`src/jarvis/index_cli.py:1118`:

```python
    parser = argparse.ArgumentParser(prog="jarvis")
```

- [ ] **Step 7: Delete the dead scaffold in `__init__.py`**

`src/jarvis/__init__.py` contains a `main()` printing `"Hello from codeintel!"`, left over from `uv init`. Nothing references it — `[project.scripts]` points at `index_cli:main` and `server:main`. Renaming the string would preserve dead code; delete the function so the file is empty:

```bash
: > src/jarvis/__init__.py
```

- [ ] **Step 8: Re-sync the environment and run the full unit suite**

The package directory moved, so the editable install must be rebuilt.

Run:
```bash
uv sync
uv run pytest -m "not integration" -q
```

Expected: all tests pass, including `test_mcp_server_advertises_the_jarvis_name`.

- [ ] **Step 9: Verify both console scripts resolve**

Run:
```bash
uv run jarvis --help
uv run jarvis-server --help 2>&1 | head -5
```

Expected: `jarvis --help` prints usage with `usage: jarvis`. `jarvis-server` starts (it is a stdio server; `--help` may error, but it must not fail with `ModuleNotFoundError` or `No such command`).

- [ ] **Step 10: Commit**

```bash
git add -A src/jarvis pyproject.toml tests/ uv.lock
git commit -m "refactor: rename the codeintel package to jarvis"
```

---

### Task 2: Rename the data directory and all environment variables

**Files:**
- Modify: `src/jarvis/config.py:20` (`DEFAULT_DATA_DIR`), `src/jarvis/config.py:52` (`JARVIS_DATA_DIR`), plus its module docstring
- Modify: `src/jarvis/*.py` (remaining `CODEINTEL_*` reads)
- Test: `tests/test_config.py`, `tests/test_embeddings.py`, `tests/test_semantic.py`, `tests/test_search.py`, `tests/test_index_cli.py`

**Interfaces:**
- Consumes: the `jarvis` import root from Task 1.
- Produces: `jarvis.config.DEFAULT_DATA_DIR == Path.home() / ".jarvis"`, and the 13 variables `JARVIS_DATA_DIR`, `JARVIS_EMBEDDING_MODEL`, `JARVIS_EMBEDDING_BATCH_SIZE`, `JARVIS_EMBEDDING_QUERY_PREFIX`, `JARVIS_EMBEDDING_DOC_PREFIX`, `JARVIS_ZOEKT_BIN`, `JARVIS_ZOEKT_KEEP_VERSIONS`, `JARVIS_SCIP_BIN`, `JARVIS_INDEXER_TIMEOUT`, `JARVIS_BIN_DIR`, `JARVIS_SETUP_SOURCED`, `JARVIS_MARKER`, `JARVIS_REPO`. The signature of `config.data_dir(override: Path | None = None) -> Path` is unchanged.

- [ ] **Step 1: Write the failing tests**

`tests/test_config.py` currently imports only `pytest` and `config`, so add the
`pathlib` import at the top of the file first (after `from __future__ import
annotations`):

```python
from pathlib import Path
```

Then append to `tests/test_config.py`:

```python
def test_default_data_dir_is_dot_jarvis():
    """The data dir is user-visible and documented; a silent change would
    orphan every published index."""
    assert config.DEFAULT_DATA_DIR == Path.home() / ".jarvis"


def test_data_dir_honours_the_jarvis_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    assert config.data_dir() == tmp_path


def test_data_dir_ignores_the_old_codeintel_env_var(monkeypatch):
    """The old prefix must not keep working: a stale CODEINTEL_DATA_DIR left in
    a shell profile would silently point jarvis at the abandoned tree."""
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    monkeypatch.setenv("CODEINTEL_DATA_DIR", "/tmp/should-be-ignored")
    assert config.data_dir() == config.DEFAULT_DATA_DIR
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k "jarvis or dot_jarvis" -v`

Expected: FAIL — `DEFAULT_DATA_DIR` is still `~/.codeintel` and `data_dir()` still reads `CODEINTEL_DATA_DIR`.

- [ ] **Step 3: Apply the data-dir and env-var passes to source and tests**

These are passes 6 and 7 of the canonical chain, applied on their own because Task 1 deliberately left them alone:

```bash
sed -i '' \
  -e 's/\.codeintel/.jarvis/g' \
  -e 's/CODEINTEL_/JARVIS_/g' \
  src/jarvis/*.py tests/*.py tests/fixtures/*.py
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`

Expected: PASS, all three new tests included.

- [ ] **Step 5: Run the full unit suite**

Run: `uv run pytest -m "not integration" -q`

Expected: all pass. If `test_embeddings.py` or `test_semantic.py` fail, they are asserting on an env var name the sweep missed — check with `grep -rn "CODEINTEL" src/jarvis tests`.

- [ ] **Step 6: Confirm no `CODEINTEL_` survives in code**

Run: `grep -rn "CODEINTEL\|\.codeintel" src/jarvis tests/`

Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add -A src/jarvis tests/
git commit -m "refactor: move the data dir to ~/.jarvis and env vars to JARVIS_"
```

---

### Task 3: Rename `setup.sh`

**Files:**
- Modify: `setup.sh` (26 references: `JARVIS_REPO`, `ZOEKT_RELEASE_REPO`, bin/shim dirs, banner, usage text, test seam)
- Test: `tests/test_setup_sh.py` (49 references)

**Interfaces:**
- Consumes: the `JARVIS_*` variable names from Task 2.
- Produces: `JARVIS_REPO="phuongddx/jarvis"`, `ZOEKT_RELEASE_REPO="phuongddx/jarvis-dist"`, install dir `~/.jarvis/bin`, shim dir `~/.jarvis/shims`, test seam `JARVIS_SETUP_SOURCED=1`, override `JARVIS_BIN_DIR`.

**Note:** `ensure_on_path` (`setup.sh:161-188`) is **not** modified. It already dedups on the directory string and `test_ensure_on_path_is_idempotent` covers it. See the spec's *Code changes* section for why the earlier plan to change it was dropped.

- [ ] **Step 1: Apply the canonical sweep to both files**

Paste the `sweep()` function from *Global Constraints* into your shell, then:

```bash
sweep setup.sh tests/test_setup_sh.py
```

This gets every case right on its own, including the two literals in `tests/test_setup_sh.py:860` (`"phuongddx/jarvis"` → `"phuongddx/jarvis-dist"`, correct because that assertion is about the public release repo) and `:872` (`$CODEINTEL_REPO` → `$JARVIS_REPO`).

- [ ] **Step 2: Verify `phuongddx/scip-swift` was not touched**

Run: `grep -n "SCIP_SWIFT_REPO" setup.sh`

Expected: `SCIP_SWIFT_REPO="phuongddx/scip-swift"` — unchanged. It is a genuinely separate third-party repo.

- [ ] **Step 3: Verify the two repo constants still differ**

Run: `grep -nE '^(JARVIS_REPO|ZOEKT_RELEASE_REPO)=' setup.sh`

Expected exactly:
```
JARVIS_REPO="phuongddx/jarvis"
ZOEKT_RELEASE_REPO="phuongddx/jarvis-dist"
```

This invariant is what `test_zoekt_release_repo_is_not_the_private_repo` guards: GitHub serves release assets only to viewers of the owning repo, so pointing zoekt downloads at the private dev repo 404s for every real user.

- [ ] **Step 4: Run the `setup.sh` unit tests**

Run: `uv run pytest tests/test_setup_sh.py -q`

Expected: all pass.

- [ ] **Step 5: Exercise `setup.sh` against a redirected bin dir**

Run:
```bash
JARVIS_BIN_DIR=/tmp/jarvis-plan-check sh setup.sh --only scip
/tmp/jarvis-plan-check/scip --version
```

Expected: the installer prints `jarvis setup`, installs into `/tmp/jarvis-plan-check`, and `scip --version` succeeds. This proves the renamed `JARVIS_BIN_DIR` seam works end to end.

- [ ] **Step 6: Clean up and commit**

```bash
rm -rf /tmp/jarvis-plan-check
git add setup.sh tests/test_setup_sh.py
git commit -m "refactor: rename setup.sh env seams and repo constants to jarvis"
```

---

### Task 4: Bump to 0.5.0 and repoint the version checker

**Files:**
- Modify: `pyproject.toml` (`version`), `server.json` (2 version fields, `name`, `repository.url`, `packages[0].identifier`), `plugin/.claude-plugin/plugin.json` (`version`), `.codex-plugin/plugin.json` (`version`)
- Modify: `plugin/.mcp.json` (server key + `--from` spec)
- Modify: `scripts/check_versions.py:30` (`PACKAGE`), `scripts/check_versions.py:66` (`mcpServers` key lookup)
- Test: `tests/test_check_versions.py`

**Interfaces:**
- Consumes: the distribution name `jarvis-mcp` established in Task 1's `pyproject.toml`.
- Produces: `scripts/check_versions.py` exposing `PACKAGE == "jarvis-mcp"`, `read_declared_versions(root) -> dict[str, str]`, `read_floor(root) -> str`, and `check(root) -> list[str]` (empty list means consistent). All four signatures are unchanged from before.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_check_versions.py`:

```python
def test_package_constant_is_jarvis_mcp():
    """The checker asserts plugin/.mcp.json names the right distribution. If
    this constant drifts from pyproject's name, the check passes while the
    plugin installs nothing."""
    assert check_versions.PACKAGE == "jarvis-mcp"


def test_floor_is_read_from_the_jarvis_server_key(tmp_path):
    """The mcpServers key is the MCP server name users see, and the checker
    looks the floor up by it. A stale key raises KeyError, not a clear error."""
    (tmp_path / "plugin").mkdir()
    (tmp_path / "plugin" / ".mcp.json").write_text(
        '{"mcpServers": {"jarvis": {"command": "uvx",'
        ' "args": ["--from", "jarvis-mcp>=0.5.0", "jarvis-server"]}}}'
    )
    assert check_versions.read_floor(tmp_path) == "0.5.0"
```

Do **not** add a test that runs `check(REPO_ROOT)` against the real tree. This
file's docstring states the convention deliberately — each test builds a
miniature repo under `tmp_path` "so a failure here reflects the checker's logic
and never whatever the working tree happens to hold at the time." The real tree
is already gated by running `scripts/check_versions.py` directly, in Task 4
Step 7 and Task 10 Step 2, and in CI via `test.yml`.

`check_versions` is available at module level in this file — it is loaded by
path at import time via `_load_checker()`, since `scripts/` is not an importable
package.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_check_versions.py -v`

Expected: FAIL — `PACKAGE` is still `codeintel-navigation-mcp` and `read_floor` still looks up the `codeintel` key.

- [ ] **Step 3: Repoint `scripts/check_versions.py`**

Line 30:

```python
PACKAGE = "jarvis-mcp"
```

Line 66, inside `read_floor`:

```python
    args = mcp["mcpServers"]["jarvis"]["args"]
```

- [ ] **Step 4: Rewrite `plugin/.mcp.json` in full**

The floor must equal the release version — `jarvis-mcp` has no releases below 0.5.0, so any lower floor resolves to nothing installable:

```json
{
  "mcpServers": {
    "jarvis": {
      "command": "uvx",
      "args": ["--from", "jarvis-mcp>=0.5.0", "jarvis-server"]
    }
  }
}
```

- [ ] **Step 5: Rewrite `server.json` in full**

```json
{
  "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
  "name": "io.github.phuongddx/jarvis",
  "description": "Local-first SCIP code navigation and Zoekt search over your own indexed repositories",
  "repository": {
    "url": "https://github.com/phuongddx/jarvis",
    "source": "github"
  },
  "version": "0.5.0",
  "packages": [
    {
      "registryType": "pypi",
      "identifier": "jarvis-mcp",
      "version": "0.5.0",
      "transport": {
        "type": "stdio"
      }
    }
  ]
}
```

`repository.url` points at the **private** repo (`jarvis`, not `jarvis-dist`): it is the source of record for the registry entry and matches the trusted-publisher binding.

- [ ] **Step 6: Set the version to 0.5.0 in the remaining three places**

```bash
sed -i '' 's/^version = "0.4.0"/version = "0.5.0"/' pyproject.toml
sed -i '' 's/"version": "0.4.0"/"version": "0.5.0"/' \
  plugin/.claude-plugin/plugin.json .codex-plugin/plugin.json
```

- [ ] **Step 7: Run the version checker and its tests**

Run:
```bash
uv run python scripts/check_versions.py
uv run pytest tests/test_check_versions.py -v
```

Expected: `versions consistent`, and all tests pass.

- [ ] **Step 8: Refresh the lockfile**

`uv.lock` records the project version and package name, so it drifts after Task 1 and this task.

Run: `uv sync`

Then confirm: `grep -n "jarvis" uv.lock | head -5` shows `jarvis-mcp` at `0.5.0`.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml server.json plugin/.mcp.json plugin/.claude-plugin/plugin.json \
  .codex-plugin/plugin.json scripts/check_versions.py tests/test_check_versions.py uv.lock
git commit -m "build: rename the distribution to jarvis-mcp and bump to 0.5.0"
```

---

### Task 5: Rename the plugin and marketplace metadata

**Files:**
- Modify: `plugin/.claude-plugin/plugin.json` (name, homepage, repository)
- Modify: `.claude-plugin/marketplace.json` (two `name` fields, descriptions)
- Modify: `.codex-plugin/plugin.json` (name, `displayName`, four URLs, `composerIcon`)
- Move: `plugin/assets/codeintel-small.svg` → `plugin/assets/jarvis-small.svg`
- Modify: `plugin/README.md`

**Interfaces:**
- Consumes: the version `0.5.0` set in Task 4 — do not change it here.
- Produces: plugin name `jarvis` (the string users see in `/plugin`), and every user-facing URL pointing at `https://github.com/phuongddx/jarvis-dist`.

- [ ] **Step 1: Rename the icon asset**

```bash
git mv plugin/assets/codeintel-small.svg plugin/assets/jarvis-small.svg
```

- [ ] **Step 2: Sweep the four metadata files and the plugin README**

First paste the `sweep()` function from *Global Constraints* into your shell — the substitution order inside it is load-bearing.

```bash
sweep plugin/.claude-plugin/plugin.json .claude-plugin/marketplace.json \
  .codex-plugin/plugin.json plugin/README.md
```

- [ ] **Step 3: Fix the `github.com` URLs the sweep pointed at the private repo**

This is the hand-fix the canonical chain cannot make. `.codex-plugin/plugin.json` previously pointed all four of its user-facing URLs at the *private* repo — they 404 for real users today — so pass 4 rewrote them to `phuongddx/jarvis`, which is still private. They must be `jarvis-dist`:

```bash
sed -i '' 's|github\.com/phuongddx/jarvis/|github.com/phuongddx/jarvis-dist/|g; s|github\.com/phuongddx/jarvis"|github.com/phuongddx/jarvis-dist"|g' \
  .codex-plugin/plugin.json
```

- [ ] **Step 4: Verify every plugin URL is reachable-by-a-user**

Run:
```bash
grep -n "phuongddx/" plugin/.claude-plugin/plugin.json .claude-plugin/marketplace.json \
  .codex-plugin/plugin.json plugin/README.md
```

Expected: **every** hit says `phuongddx/jarvis-dist`. Any bare `phuongddx/jarvis` here is a bug — it points at a private repo, which is exactly the class of defect this task fixes.

- [ ] **Step 5: Verify the icon reference matches the renamed file**

Run: `grep -n "composerIcon\|assets/" .codex-plugin/plugin.json`

Expected: `"composerIcon": "./plugin/assets/jarvis-small.svg"`.

- [ ] **Step 6: Verify all four JSON files still parse**

Run:
```bash
for f in plugin/.claude-plugin/plugin.json .claude-plugin/marketplace.json \
         .codex-plugin/plugin.json plugin/.mcp.json; do
  python3 -c "import json,sys; json.load(open('$f')); print('ok $f')"
done
```

Expected: `ok` for all four.

- [ ] **Step 7: Re-run the version checker**

The checker reads two of these files, so a malformed edit surfaces here.

Run: `uv run python scripts/check_versions.py`

Expected: `versions consistent`.

- [ ] **Step 8: Commit**

```bash
git add -A plugin/ .claude-plugin/ .codex-plugin/
git commit -m "build: rename plugin metadata to jarvis and point URLs at jarvis-dist"
```

---

### Task 6: Rename the skill directories

**Files:**
- Move: `plugin/skills/codeintel-setup/` → `plugin/skills/jarvis-setup/`
- Move: `plugin/skills/codeintel-use/` → `plugin/skills/jarvis-use/`
- Move: `plugin/skills/codeintel-issues/` → `plugin/skills/jarvis-issues/`
- Move: `.claude/skills/codeintel-release/` → `.claude/skills/jarvis-release/`
- Modify: every `SKILL.md`, `agents/openai.yaml`, and `references/tool-roster.md` inside them

**Interfaces:**
- Consumes: nothing from earlier tasks; skills are documentation.
- Produces: the user-facing skill names `jarvis-setup`, `jarvis-use`, `jarvis-issues`, and the maintainer-only `jarvis-release`. `jarvis-issues` files against `phuongddx/jarvis-dist`.

- [ ] **Step 1: Move the four directories**

```bash
git mv plugin/skills/codeintel-setup plugin/skills/jarvis-setup
git mv plugin/skills/codeintel-use plugin/skills/jarvis-use
git mv plugin/skills/codeintel-issues plugin/skills/jarvis-issues
git mv .claude/skills/codeintel-release .claude/skills/jarvis-release
```

- [ ] **Step 2: Sweep every file inside them**

First paste the `sweep()` function from *Global Constraints* into your shell — the substitution order inside it is load-bearing.

```bash
sweep $(find plugin/skills .claude/skills -type f)
```

- [ ] **Step 3: Verify the `name:` frontmatter matches each directory**

A skill whose frontmatter name disagrees with its directory will not load.

Run: `grep -n "^name:" plugin/skills/*/SKILL.md .claude/skills/*/SKILL.md`

Expected: `jarvis-setup`, `jarvis-use`, `jarvis-issues`, `jarvis-release` — each matching its parent directory.

- [ ] **Step 4: Verify the issue tracker target is the public repo**

Run: `grep -n "gh issue create\|phuongddx/" plugin/skills/jarvis-issues/SKILL.md`

Expected: every repo reference is `phuongddx/jarvis-dist`. A user cannot file an issue against a repo they cannot see.

- [ ] **Step 5: Verify the installer URL in the setup skill**

Run: `grep -n "raw.githubusercontent.com" plugin/skills/jarvis-setup/SKILL.md`

Expected: `https://raw.githubusercontent.com/phuongddx/jarvis-dist/main/setup.sh`.

- [ ] **Step 6: Verify the tool roster still lists the unchanged MCP tool names**

Run: `grep -c "goToDefinition\|findReferences\|blastRadius" plugin/skills/jarvis-use/references/tool-roster.md`

Expected: a non-zero count. The tool names carry no product name and must survive the sweep unchanged.

- [ ] **Step 7: Commit**

```bash
git add -A plugin/skills .claude/skills
git commit -m "docs: rename the skills to jarvis-*"
```

---

### Task 7: Rename the CI workflows

**Files:**
- Modify: `.github/workflows/publish-pypi.yml` (environment URL, wheel guard, marker assertion, comments)
- Modify: `.github/workflows/sync-public-distribution.yml` (clone target, secret name, commit message, comments)
- Modify: `.github/workflows/build-zoekt.yml` (release repo, comments)
- Modify: `.github/workflows/setup-smoke.yml` (three `JARVIS_BIN_DIR` uses)
- Modify: `.github/workflows/test.yml`, `.github/workflows/publish-mcp-registry.yml` (comments only)

**Interfaces:**
- Consumes: `jarvis-mcp` (Task 4), the `jarvis/` wheel layout (Task 1), the README marker rewritten in Task 8.
- Produces: workflows that publish `jarvis-mcp` from repo `jarvis` and push the distribution surface to `jarvis-dist`.

- [ ] **Step 1: Sweep all six workflow files**

First paste the `sweep()` function from *Global Constraints* into your shell — the substitution order inside it is load-bearing.

```bash
sweep .github/workflows/*.yml
```

- [ ] **Step 2: Verify the PyPI environment URL and wheel guard**

Run: `grep -n "pypi.org/p/\|startswith(" .github/workflows/publish-pypi.yml`

Expected:
- `url: https://pypi.org/p/jarvis-mcp`
- `modules = [n for n in names if n.startswith("jarvis/") and n.endswith(".py")]`

The wheel guard is the only check that catches a broken `module-name` producing a wheel that imports nothing.

- [ ] **Step 3: Verify the registry ownership marker assertion**

Run: `grep -n "mcp-name" .github/workflows/publish-pypi.yml`

Expected: `marker='mcp-name: io.github.phuongddx/jarvis'` — matching `server.json`'s `name` from Task 4 and the README marker set in Task 8.

- [ ] **Step 4: Verify the sync workflow targets the public repo**

Run: `grep -n "phuongddx/\|clone" .github/workflows/sync-public-distribution.yml`

Expected: every repo reference is `phuongddx/jarvis-dist`. Pushing the public distribution surface into the private repo would leave users with nothing.

- [ ] **Step 5: Rename the release secret reference**

The fine-grained PAT scopes by repository *ID*, so it keeps working through the rename — but its name should not read as "the token for the repo we are in":

```bash
sed -i '' 's/JARVIS_RELEASE_TOKEN/JARVIS_DIST_TOKEN/g' \
  .github/workflows/sync-public-distribution.yml
```

Then confirm: `grep -n "secrets\." .github/workflows/sync-public-distribution.yml`

Expected: `${{ secrets.JARVIS_DIST_TOKEN }}` in both places.

**This requires a matching GitHub settings change in Task 11 — the workflow will fail with an empty token until the secret is re-created under the new name.**

- [ ] **Step 6: Verify the zoekt release repo**

Run: `grep -n "\-\-repo" .github/workflows/build-zoekt.yml`

Expected: all three occurrences are `--repo phuongddx/jarvis-dist`.

- [ ] **Step 7: Verify all six workflows are valid YAML**

Run:
```bash
for f in .github/workflows/*.yml; do
  python3 -c "
import sys
try:
    import yaml
except ImportError:
    sys.exit(0)
yaml.safe_load(open('$f'))
print('ok $f')"
done
```

Expected: `ok` for each, or no output if PyYAML is unavailable (it is not a project dependency). If unavailable, visually confirm indentation was untouched with `git diff .github/workflows/`.

- [ ] **Step 8: Commit**

```bash
git add .github/workflows/
git commit -m "ci: publish jarvis-mcp and sync to jarvis-dist"
```

---

### Task 8: Rename the living documentation

**Files:**
- Modify: `README.md` (83 refs, plus the `mcp-name` marker at line 3 and the `git clone` line at 64)
- Modify: `CLAUDE.md` (29), `AGENTS.md` (27)
- Modify: `docs/index.html` (35), `docs/system-architecture.md` (33), `docs/code-standards.md` (29), `docs/project-roadmap.md` (43), `docs/codebase-summary.md` (17), `docs/project-overview-pdr.md` (22)
- Modify: `CHANGELOG.md` (new 0.5.0 entry; existing entries swept in Task 9)

**Interfaces:**
- Consumes: every name established in Tasks 1–7.
- Produces: the `<!-- mcp-name: io.github.phuongddx/jarvis -->` marker that `publish-pypi.yml` asserts and the MCP Registry reads from the published PyPI description.

- [ ] **Step 1: Sweep the living documents**

First paste the `sweep()` function from *Global Constraints* into your shell — the substitution order inside it is load-bearing.

```bash
sweep README.md CLAUDE.md AGENTS.md docs/index.html \
  docs/system-architecture.md docs/code-standards.md \
  docs/project-roadmap.md docs/codebase-summary.md docs/project-overview-pdr.md
```

- [ ] **Step 2: Verify the registry ownership marker**

Run: `sed -n 1,5p README.md`

Expected: line 3 is `<!-- mcp-name: io.github.phuongddx/jarvis -->`. If this is wrong or missing, `publish-pypi.yml` fails the release, and losing it silently breaks registry publishing long after the fact.

- [ ] **Step 3: Fix the landing page's installer URL**

`docs/index.html:782` pointed the public `curl` command at the *private* repo — a pre-existing bug. Pass 2 of the sweep rewrites `raw.githubusercontent.com` URLs to `jarvis-dist` automatically. Confirm:

```bash
grep -n "raw.githubusercontent.com" docs/index.html README.md
```

Expected: every hit is `phuongddx/jarvis-dist/main/setup.sh`.

- [ ] **Step 4: Verify the dev-clone line points at the private repo**

Run: `grep -n "git clone" README.md`

Expected: `git clone https://github.com/phuongddx/jarvis && cd jarvis`. This one is correct as `jarvis` — it is the development repo, cloned by a maintainer with access, not a user-facing install path.

- [ ] **Step 5: Verify the marketplace path and MCP registration command**

Run: `grep -n "plugin marketplace add\|mcp add" README.md CLAUDE.md`

Expected: `/plugin marketplace add phuongddx/jarvis-dist`, and a registration line reading
`claude mcp add jarvis --scope user -- uv --directory /path/to/jarvis run jarvis-server`.

- [ ] **Step 6: Add the 0.5.0 changelog entry**

Insert directly below the `# Changelog` heading in `CHANGELOG.md`. This entry deliberately keeps saying `codeintel` — it is how someone holding the old package finds their way here:

```markdown
## 0.5.0

Renamed the project from `codeintel` to `jarvis`. This is a breaking rename with
no automatic migration path.

**What you must do**

- Reinstall: the PyPI distribution is now `jarvis-mcp` (was
  `codeintel-navigation-mcp`), and the CLIs are `jarvis` and `jarvis-server`
  (were `codeintel` and `codeintel-server`).
- Re-add the plugin: it is now `jarvis`, served from
  `phuongddx/jarvis-dist` (was `phuongddx/jarvis`).
- Re-register the MCP server: `claude mcp remove codeintel` then
  `claude mcp add jarvis --scope user -- uv --directory /path/to/jarvis run jarvis-server`.
- Re-index your repos. The data directory moved from `~/.codeintel` to
  `~/.jarvis` and starts empty; nothing is migrated. The old tree is left
  untouched, so `mv ~/.codeintel ~/.jarvis` recovers existing indexes if you
  prefer — published index files carry no absolute paths — but that is a manual
  step, not a supported code path.
- Rename any `CODEINTEL_*` environment variables to `JARVIS_*`. The old names
  are ignored, not honoured, so a stale `CODEINTEL_DATA_DIR` in a shell profile
  fails loudly rather than silently pointing at the abandoned tree.

**Distribution**

- `codeintel-navigation-mcp` remains installable at 0.4.0 and receives no
  further releases. It is not yanked: a yank surfaces to existing installs as a
  resolver error, which is worse than a stale but working pin.
- The MCP Registry entry is now `io.github.phuongddx/jarvis`. The old
  `io.github.phuongddx/codeintel` entry stays published and continues to point
  at a package that still resolves.
```

- [ ] **Step 7: Commit**

```bash
git add README.md CLAUDE.md AGENTS.md docs/ CHANGELOG.md
git commit -m "docs: rename the living documentation to jarvis"
```

---

### Task 9: Sweep the historical record and rename stale paths

**Files:**
- Move + modify: `plans/0724-2316-codeintel-mcp-implementation/` (directory + 5 files)
- Move + modify: 3 files under `plans/reports/`
- Move + modify: 4 files under `docs/superpowers/{plans,specs}/`
- Move + modify: `docs/diagrams/codeintel-7layer-architecture.{excalidraw,png}`
- Modify: all remaining tracked `.md` files under `plans/` and `docs/` (~1,850 refs)
- Restore: the two Komodo references

**Interfaces:**
- Consumes: nothing — these are dated records.
- Produces: nothing consumed by later tasks.

**Scope note:** this rewrites dated documents so a 2026-07-26 spec will read as though it designed "jarvis", a name that did not exist until 2026-08-04. That trade — consistency over literal historical fidelity — was decided during brainstorming and is recorded in the spec's *Decisions* section.

- [ ] **Step 1: Rename the directories and files carrying the old name**

```bash
git mv plans/0724-2316-codeintel-mcp-implementation plans/0724-2316-jarvis-mcp-implementation
git mv plans/reports/brainstorm-0724-2316-codeintel-phase0-3-implementation-report.md \
       plans/reports/brainstorm-0724-2316-jarvis-phase0-3-implementation-report.md
git mv plans/reports/code-reviewer-0725-0010-codeintel-phase2-3-implementation-review-report.md \
       plans/reports/code-reviewer-0725-0010-jarvis-phase2-3-implementation-review-report.md
git mv plans/reports/distribution-strategy-0731-2335-codeintel-mcp-adoption-report.md \
       plans/reports/distribution-strategy-0731-2335-jarvis-mcp-adoption-report.md
git mv docs/superpowers/plans/2026-07-28-codeintel-skills.md \
       docs/superpowers/plans/2026-07-28-jarvis-skills.md
git mv docs/superpowers/plans/2026-08-01-codeintel-claude-plugin.md \
       docs/superpowers/plans/2026-08-01-jarvis-claude-plugin.md
git mv docs/superpowers/specs/2026-07-28-codeintel-skills-design.md \
       docs/superpowers/specs/2026-07-28-jarvis-skills-design.md
git mv docs/superpowers/specs/2026-08-01-codeintel-claude-plugin-design.md \
       docs/superpowers/specs/2026-08-01-jarvis-claude-plugin-design.md
git mv docs/diagrams/codeintel-7layer-architecture.excalidraw \
       docs/diagrams/jarvis-7layer-architecture.excalidraw
git mv docs/diagrams/codeintel-7layer-architecture.png \
       docs/diagrams/jarvis-7layer-architecture.png
```

- [ ] **Step 2: Build the sweep file list, excluding the two rename documents**

This plan and its spec must keep saying `codeintel` — a swept rename spec describes nothing.

```bash
git ls-files 'plans/**' 'docs/**' \
  | grep -v 'docs/superpowers/specs/2026-08-04-jarvis-rename-design.md' \
  | grep -v 'docs/superpowers/plans/2026-08-04-jarvis-rename.md' \
  | grep -v '\.png$' \
  > /tmp/jarvis-sweep-list.txt
wc -l /tmp/jarvis-sweep-list.txt
```

Expected: roughly 90 files.

- [ ] **Step 3: Sweep them**

First paste the `sweep()` function from *Global Constraints* into your shell — the substitution order inside it is load-bearing.

```bash
sweep $(cat /tmp/jarvis-sweep-list.txt)
```

- [ ] **Step 4: Restore the Komodo reference in `CHANGELOG.md`**

`CHANGELOG.md:161` explains why the old distribution name existed by naming *Komodo Edit CodeIntel*, a real unrelated package. The sweep turned it into "Komodo Edit Jarvis", which is false. Find and fix it:

```bash
grep -n "Komodo" CHANGELOG.md pyproject.toml
```

Restore both to read `Komodo Edit CodeIntel`:

```bash
sed -i '' 's/Komodo Edit Jarvis/Komodo Edit CodeIntel/g' CHANGELOG.md pyproject.toml
```

Then re-check `grep -n "Komodo" CHANGELOG.md pyproject.toml` — both must say `Komodo Edit CodeIntel`.

- [ ] **Step 5: Re-export the architecture diagram**

`jarvis-7layer-architecture.png` is a raster image with "codeintel" in its pixels — no sweep can fix it. The `.excalidraw` beside it is JSON and was swept in Step 3, so it now says "jarvis".

Open `docs/diagrams/jarvis-7layer-architecture.excalidraw` in Excalidraw (excalidraw.com or the VS Code extension), confirm the labels read "jarvis", and export as PNG over the existing file.

Then verify the image changed: `git status --short docs/diagrams/`

Expected: both files listed as modified/renamed.

If you cannot re-export right now, say so rather than committing a diagram that contradicts its own filename — leave the PNG staged as a rename only and note it as outstanding.

- [ ] **Step 6: Confirm only the expected files still mention the old name**

```bash
git ls-files | xargs grep -il codeintel
```

Expected exactly four files:
- `pyproject.toml` (Komodo)
- `CHANGELOG.md` (Komodo, plus the 0.5.0 entry naming the old package deliberately)
- `docs/superpowers/specs/2026-08-04-jarvis-rename-design.md`
- `docs/superpowers/plans/2026-08-04-jarvis-rename.md`

Any other file is a miss — sweep it and re-check.

- [ ] **Step 7: Commit**

```bash
rm -f /tmp/jarvis-sweep-list.txt
git add -A plans/ docs/ CHANGELOG.md pyproject.toml
git commit -m "docs: rename the historical record to jarvis"
```

---

### Task 10: Run the full verification battery

**Files:** none modified — this task only runs gates. Any failure is fixed in the task that owns the file.

**Interfaces:**
- Consumes: the complete renamed tree from Tasks 1–9.
- Produces: evidence the rename is complete and installable, and a green branch ready to merge.

- [ ] **Step 1: Unit suite**

Run: `uv run pytest -m "not integration" -q`

Expected: all pass, zero errors.

- [ ] **Step 2: Version consistency**

Run: `uv run python scripts/check_versions.py`

Expected: `versions consistent`.

- [ ] **Step 3: Residual-name scan**

Run: `git ls-files | xargs grep -il codeintel`

Expected: exactly the four files listed in Task 9 Step 6.

- [ ] **Step 4: Build the wheel and confirm it ships the right modules**

This mirrors `publish-pypi.yml`'s guard and is the only gate that catches a `module-name` mismatch producing a wheel that imports nothing.

```bash
rm -rf dist
uv build
python3 - <<'PY'
import zipfile, glob, sys
wheel = glob.glob("dist/*.whl")[0]
names = zipfile.ZipFile(wheel).namelist()
modules = [n for n in names if n.startswith("jarvis/") and n.endswith(".py")]
if not modules:
    tops = sorted({n.split("/")[0] for n in names})
    sys.exit(f"{wheel} ships no jarvis/ modules; top-level entries: {tops}")
print(f"{wheel} ships {len(modules)} jarvis/ modules")
PY
```

Expected: `dist/jarvis_mcp-0.5.0-*.whl ships 17 jarvis/ modules`.

- [ ] **Step 5: Install the wheel into a clean environment and complete an MCP handshake**

Resolving from a clean environment (no lockfile) is the only way to catch a broken dependency *range*, which is how 0.2.0 shipped a server that could not start.

```bash
uv venv /tmp/jarvis-smoke --python 3.12
uv pip install --python /tmp/jarvis-smoke/bin/python dist/*.whl
/tmp/jarvis-smoke/bin/python -c "
from jarvis.server import mcp
print('server name:', mcp.name)
import jarvis.config as c
print('data dir:', c.DEFAULT_DATA_DIR)
"
```

Expected: `server name: jarvis` and a data dir ending in `/.jarvis`.

- [ ] **Step 6: Confirm the installed console scripts exist**

```bash
ls /tmp/jarvis-smoke/bin | grep -E "^jarvis"
```

Expected: both `jarvis` and `jarvis-server`. Neither `codeintel` nor `codeintel-server` may appear.

- [ ] **Step 7: Clean up the smoke environment**

```bash
rm -rf /tmp/jarvis-smoke dist
```

- [ ] **Step 8: Commit any fixes and open the pull request**

If Steps 1–6 required fixes, commit them to the owning task's files first, then:

```bash
git push -u origin HEAD
gh pr create --title "refactor: rename codeintel to jarvis" \
  --body "Implements docs/superpowers/specs/2026-08-04-jarvis-rename-design.md.

Renames every surface: package, CLIs, env vars, data dir, PyPI distribution
(jarvis-mcp), MCP registry entry, plugin metadata, skills, and all docs
including history.

Breaking: no data migration (~/.jarvis starts empty), new PyPI project, new
plugin name. Repo renames and the PyPI pending publisher are manual steps
tracked in the plan's Task 11 and must happen before the release is tagged."
```

---

### Task 11: Manual runbook — publisher, repo renames, release

**Files:** none. Every step here is an action in a web UI, in GitHub settings, or on the local machine. Nothing in the repository can perform them.

**Interfaces:**
- Consumes: a merged `main` containing Tasks 1–10.
- Produces: a published `jarvis-mcp` 0.5.0, a registry entry, a synced `jarvis-dist`, and a working local install.

**Ordering is mandatory.** Step 2 fails outright if Step 3 runs first. Step 5 fails if Step 1 was skipped.

**Where rollback stops working.** Everything through Step 4 is undoable leaving no trace: the branch can be reverted, and both repository renames can be reversed by renaming back in the opposite order (`jarvis` → `codeintel` first, then `jarvis-dist` → `jarvis`). **Step 5 is the point of no return** — a PyPI filename can never be re-uploaded even after deleting the release, and the MCP Registry likewise rejects re-publishing a version with no way to amend one. Once `jarvis-mcp` 0.5.0 and `io.github.phuongddx/jarvis` 0.5.0 exist, they exist permanently. The *decision* stays reversible — returning to `codeintel` would mean publishing `codeintel-navigation-mcp` 0.5.0, a version still free — but you would then own two published names instead of one.

- [ ] **Step 1: Create the PyPI pending publisher (do this first)**

`publish-pypi.yml` stores no API token — PyPI trusts the workflow's OIDC identity, bound to a `(project, owner, repo, workflow, environment)` tuple. This rename changes two legs at once, and `jarvis-mcp` does not exist on PyPI, so there is no project to attach a publisher to. A *pending* publisher pre-authorizes the first upload to create it.

On pypi.org → Account settings → Publishing → add a new pending publisher:

| Field | Value |
|---|---|
| PyPI Project Name | `jarvis-mcp` |
| Owner | `phuongddx` |
| Repository name | `jarvis` |
| Workflow name | `publish-pypi.yml` |
| Environment name | `pypi` |

PyPI does not verify the repository exists, so this is safe to do before the renames in Step 3.

**Skip this and the release fails at upload with `Non-user identities cannot create new projects` — an error that does not name the real cause.** The same failure is already recorded in `pyproject.toml`'s header comment from when `codeintel-navigation-mcp` was first set up.

- [ ] **Step 2: Merge the pull request**

```bash
gh pr merge --squash --delete-branch
git checkout main && git pull
```

- [ ] **Step 3: Rename both GitHub repositories, public first**

GitHub will not hold two repos of one name under one owner, and the redirect it creates on rename is dropped the moment another repo claims the freed name. Reversing this order fails.

1. In `phuongddx/jarvis` → Settings → rename to **`jarvis-dist`**
2. In `phuongddx/codeintel` → Settings → rename to **`jarvis`**
3. Repoint the local clone:

```bash
git remote set-url origin git@github.com:phuongddx/jarvis.git
git remote -v
git fetch origin
```

Expected: `origin` shows `phuongddx/jarvis` and the fetch succeeds.

After this, `phuongddx/jarvis` resolves to a private repo, so previously published install URLs and marketplace paths 404 rather than redirecting. That cost was accepted during brainstorming — the public paths were days old with no real users.

- [ ] **Step 4: Re-create the release secret under its new name**

Task 7 changed the workflow to read `secrets.JARVIS_DIST_TOKEN`. The existing PAT keeps working — fine-grained PATs scope by repository ID, so it followed the public repo through its rename — but the secret name must match.

In `phuongddx/jarvis` → Settings → Secrets and variables → Actions:
1. Copy the value of `JARVIS_RELEASE_TOKEN`
2. Create `JARVIS_DIST_TOKEN` with that value
3. Delete `JARVIS_RELEASE_TOKEN`

Confirm the PAT still grants `Contents: write` on `phuongddx/jarvis-dist` (its repository selection follows the rename, but verify rather than assume).

- [ ] **Step 5: Tag and publish the release**

```bash
git tag v0.5.0
git push origin v0.5.0
gh release create v0.5.0 --title "v0.5.0 — renamed to jarvis" \
  --notes "See CHANGELOG.md. Breaking: new PyPI distribution (jarvis-mcp), new plugin name (jarvis), new data directory (~/.jarvis, starts empty), JARVIS_* environment variables."
```

- [ ] **Step 6: Watch all three release workflows succeed**

```bash
gh run watch
```

Three workflows must go green, in this order:
1. `publish-pypi` — uploads `jarvis-mcp` 0.5.0, creating the project via the pending publisher
2. `publish-mcp-registry` — fires on `publish-pypi` completing, publishes `io.github.phuongddx/jarvis`; it retries through PyPI's eventual consistency, so an early 404 is expected and self-healing
3. `sync-public-distribution` — pushes `setup.sh`, `.claude-plugin/`, and `plugin/` into `jarvis-dist`

If `publish-pypi` fails at the upload step, Step 1 was missed or one of its five fields disagrees.

- [ ] **Step 7: Verify the published artifacts**

```bash
curl -s -o /dev/null -w "jarvis-mcp on PyPI: %{http_code}\n" https://pypi.org/pypi/jarvis-mcp/json
curl -s -o /dev/null -w "installer reachable: %{http_code}\n" \
  https://raw.githubusercontent.com/phuongddx/jarvis-dist/main/setup.sh
```

Expected: `200` for both. A `404` on the installer means the sync workflow did not run or pushed to the wrong repo.

- [ ] **Step 8: Hand-edit the public repo's own README and description**

`jarvis-dist`'s `README.md` is deliberately excluded from the sync — it describes the distribution repo itself and has no counterpart here — so no workflow can reach it. It currently reads *"Public distribution surface for codeintel: installer, Claude Code plugin, and prebuilt zoekt binaries… the package is on PyPI as codeintel-navigation-mcp."*

In `phuongddx/jarvis-dist`, update:
- `README.md` — say `jarvis`, and `jarvis-mcp` as the PyPI package
- the repository description (Settings, or the "About" gear on the repo home page)

- [ ] **Step 9: Clean the stale PATH lines from your shell profile**

`~/.zshrc` holds four `# added by codeintel setup` blocks — `~/.codeintel/bin` plus three `/tmp/codeintel-b5-*/bin` left by test runs. All four are dead. Remove each two-line block (the comment and the `export PATH=` line beneath it):

```bash
grep -n "codeintel" ~/.zshrc
```

Edit those lines out, then `exec $SHELL` and confirm:

```bash
echo "$PATH" | tr ':' '\n' | grep codeintel
```

Expected: no output.

- [ ] **Step 10: Reinstall locally**

```bash
sh setup.sh
claude mcp remove codeintel
claude mcp add jarvis --scope user -- uv --directory "$(pwd)" run jarvis-server
```

Expected: binaries land in `~/.jarvis/bin`, and `claude mcp list` shows `jarvis`.

- [ ] **Step 11: Re-index and confirm navigation works from an empty data dir**

```bash
uv run jarvis index .
uv run jarvis list
uv run jarvis status jarvis
```

Expected: `list` shows slug `jarvis` with status `indexed`, and `status` reports a non-zero tracked-file count with search coverage.

Then confirm a real navigation query through the server — in Claude Code, ask for the definition of `repo_slug` in repo `jarvis`, or call `goToDefinition` directly. Expected: a location in `src/jarvis/config.py`.

- [ ] **Step 12: Run the integration suite against the real binaries**

```bash
uv run pytest -m integration -q
```

Expected: all pass (or skip cleanly if a language indexer is absent). This is the last gate — it exercises `setup.sh`-installed binaries through the renamed `JARVIS_*` seams end to end.

- [ ] **Step 13: Re-index your remaining repos**

`~/.jarvis` started empty, so the other 11 repos previously registered under `~/.codeintel` need re-indexing. For each, run `uv run jarvis index /path/to/repo`. The Swift repos built via `xcodebuild` are the slowest; the old registry at `~/.codeintel/registry.db` still lists every path and language if you need the list:

```bash
sqlite3 ~/.codeintel/registry.db "select slug, path, language from repos"
```

Once you are satisfied nothing is missing, `~/.codeintel` (5.0 GB) can be deleted.

---

## Outstanding questions

None. Every decision the spec left open was resolved during brainstorming; the two judgment calls made while writing this plan — deleting the dead `__init__.main` scaffold rather than renaming its string, and dropping the `ensure_on_path` change after finding it already idempotent — are recorded in Task 1 Step 7 and the Task 3 note respectively.
