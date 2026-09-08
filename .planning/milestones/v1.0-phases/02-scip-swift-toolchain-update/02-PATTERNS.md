# Phase 2: scip-swift Toolchain Update - Pattern Map

**Mapped:** 2026-08-22
**Files analyzed:** 7
**Analogs found:** 7 / 7

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `setup.sh` (install_scip_swift rewrite) | config/utility | request-response (API fetch + download) | `setup.sh:438-475` (installed_scip_matches_pin) + `setup.sh:500-536` (install_zoekt with ZOEKT_BASE_URL seam) | exact |
| `src/jarvis/index_cli.py` (runtime floor check) | service/controller | request-response | `src/jarvis/index_cli.py:380-415` (parse_scip_version + check_scip_version + _scip_version_output) | exact |
| `src/jarvis/index_cli.py` (--cache-dir argv plumbing) | controller | request-response | `src/jarvis/index_cli.py:203-214` (_swift_indexer_cmd) + `src/jarvis/index_cli.py:793-797` (call site) | exact |
| `src/jarvis/index_cli.py` (forget cache sweep) | controller | CRUD (delete) | `src/jarvis/index_cli.py:1096-1117` (_cmd_forget lancedb sweep) | exact |
| `src/jarvis/config.py` (swift_cache_dir helper) | utility | transform | `src/jarvis/config.py:33-37` (lancedb_dir) | exact |
| `src/jarvis/watch.py` (Swift artifact ignores) | middleware/utility | event-driven | `src/jarvis/watch.py:18-24` (should_ignore_path + _IGNORED_PATH_PARTS) | exact |
| `tests/test_setup_sh.py` (resolution/floor/digest tests) | test | — | `tests/test_setup_sh.py:718-748` (test_install_zoekt_extracts_both_binaries with ZOEKT_BASE_URL seam) | exact |
| `tests/test_index_cli.py` (floor + cache-dir + forget tests) | test | — | `tests/test_index_cli.py:612-658` (version check tests) + `tests/test_index_cli.py:229-280` (_swift_indexer_cmd tests) | exact |
| `tests/test_watch.py` (Swift ignore cases) | test | — | `tests/test_watch.py:91-98` (test_should_ignore_path_skips_vendor_and_git_dirs) | exact |
| `.github/workflows/setup-smoke.yml` (post-install Swift index) | config | request-response | `.github/workflows/setup-smoke.yml:48-58` (Install scip-swift via setup.sh step) | role-match |
| `tests/fixtures/mini_xcode_repo/` | fixture | — | `tests/fixtures/mini_swift_repo/` + upstream `Fixtures/XcodeTestProject` | role-match |

## Pattern Assignments

### `setup.sh` — install_scip_swift rewrite (config/utility, request-response)

**Analog 1:** `setup.sh:438-446` — `installed_scip_matches_pin` (version-aware skip gate)

**Version-aware skip pattern** (lines 438-446):
```sh
installed_scip_matches_pin() {
	if [ -x "$(bin_dir)/scip" ]; then
		"$(bin_dir)/scip" --version 2>/dev/null | grep -q "$SCIP_COMMIT_PIN"
	elif have_cmd scip; then
		scip --version 2>/dev/null | grep -q "$SCIP_COMMIT_PIN"
	else
		return 1
	fi
}
```

**Why:** D-02/Pitfall-2 require a version-aware skip (not mere presence check). The new `install_scip_swift` must compare installed `scip-swift --version` against the resolved latest — reusing the `bin_dir`-first, `PATH`-fallback resolution order from this exact function.

---

**Analog 2:** `setup.sh:500-507` — `ZOEKT_BASE_URL` test seam + `install_zoekt` platform gate order

**Test-seam pattern** (lines 505-507):
```sh
	# ZOEKT_BASE_URL is overridable so tests can serve a local tarball.
	_base="${ZOEKT_BASE_URL:-https://github.com/${ZOEKT_RELEASE_REPO}/releases/download/zoekt-${ZOEKT_COMMIT_PIN}}"
```

**Platform gate first pattern** (lines 497-503, 544-558):
```sh
install_zoekt() {
	_os=$1
	_arch=$2
	# ... platform-specific asset selection ...

install_scip_swift() {
	_os=$1
	_arch=$2
	if [ "$_os" != "darwin" ] || [ "$_arch" != "arm64" ]; then
		log_info "scip-swift: not available for ${_os}/${_arch} (macOS arm64 only) — skipping"
		return 0
	fi
```

**Why:** The new `SCIP_SWIFT_API_URL` seam follows this exact `${VAR:-default}` pattern. The darwin/arm64 skip check must remain FIRST (Pitfall-4: don't gate install behind the API call on non-macOS).

---

**Analog 3:** `setup.sh:258-304` — `install_tarball_binary` + `verify_sha256`

**Download + checksum + extract pattern** (lines 250-304):
```sh
download_to() {
	curl -fsSL --retry 3 -o "$2" "$1"
}

verify_sha256() {
	_file=$1
	_expected=$2
	_actual=$(sha256_of "$_file") || return 1
	if [ "$_actual" != "$_expected" ]; then
		log_error "checksum mismatch for ${_file}"
		log_error "  expected: ${_expected}"
		log_error "  actual:   ${_actual}"
		return 1
	fi
}

install_tarball_binary() {
	_tar_url=$1; _sha_url=$2; _member=$3; _dest_name=$4
	_tmp=$(mktemp -d)
	trap "rm -rf '$_tmp'" EXIT
	if ! download_to "$_tar_url" "${_tmp}/archive.tar.gz"; then ...; fi
	if ! download_to "$_sha_url" "${_tmp}/archive.sha256"; then ...; fi
	_expected=$(cut -d' ' -f1 <"${_tmp}/archive.sha256")
	if ! verify_sha256 "${_tmp}/archive.tar.gz" "$_expected"; then ...; fi
	if ! tar -xzf "${_tmp}/archive.tar.gz" -C "$_tmp" "$_member" 2>/dev/null; then ...; fi
	ensure_bin_dir
	mv "${_tmp}/${_member}" "$(bin_dir)/${_dest_name}"
	chmod +x "$(bin_dir)/${_dest_name}"
	rm -rf "$_tmp"; trap - EXIT
}
```

**Why:** The digest-verified install reuses `download_to`, `verify_sha256`, the `mktemp`/`trap` cleanup idiom, and the pinned-member `tar -xzf` — but replaces the sidecar download+cut with the API-sourced `$_expected` hash directly. The tarball member is always the literal `scip-swift` (security: never from network input).

---

### `src/jarvis/index_cli.py` — Runtime floor check (service/controller, request-response)

**Analog:** `src/jarvis/index_cli.py:380-415` — `parse_scip_version` + `_scip_version_output` + `check_scip_version`

**Shared version parser** (lines 380-389):
```python
def parse_scip_version(output: str) -> tuple[int, int, int] | None:
    """Parse `scip --version` output, e.g. "scip version v0.9.0".

    Returns None when the format is unrecognized, so an unexpected build
    string degrades to "cannot verify" rather than blocking indexing.
    """
    match = re.search(r"v?(\d+)\.(\d+)\.(\d+)", output)
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
```

**Isolated version-output function** (lines 392-398):
```python
def _scip_version_output() -> str:
    """Isolated for tests to monkeypatch."""
    try:
        result = subprocess.run(["scip", "--version"], capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise IndexingError("scip not found on PATH — run setup.sh") from exc
    return f"{result.stdout}\n{result.stderr}"
```

**Floor-check function** (lines 401-415):
```python
MIN_SCIP_VERSION = (0, 9, 0)

def check_scip_version() -> None:
    """Raise IndexingError when `scip` is too old to preserve ranges."""
    version = parse_scip_version(_scip_version_output())
    if version is None:
        return
    if version < MIN_SCIP_VERSION:
        current = ".".join(str(p) for p in version)
        required = ".".join(str(p) for p in MIN_SCIP_VERSION)
        raise IndexingError(
            f"scip v{current} is too old (need >= v{required}): it cannot read scip.proto's "
            "typed_range oneof, so occurrence positions are dropped and navigation returns "
            "empty results. Re-run setup.sh, and remove any older scip earlier on PATH."
        )
```

**Why:** `parse_scip_version` is reused directly (its `v?` regex already handles scip-swift's no-`v` format `0.3.0 (swift 6.2.4)`). The new `check_scip_swift_version()` mirrors this structure: `MIN_SCIP_SWIFT_VERSION = (0, 3, 0)`, a `_scip_swift_version_output()` isolated for monkeypatching, warn-by-omission on unparseable, `IndexingError` with setup.sh re-run hint. Call site: inside `index_repo` at line ~797, guarded by `if language == "swift"` (not unconditional like `check_scip_version()` at line 770).

---

### `src/jarvis/index_cli.py` — --cache-dir argv plumbing (controller, request-response)

**Analog:** `src/jarvis/index_cli.py:203-214` — `_swift_indexer_cmd`

**Current argv construction** (lines 203-214):
```python
def _swift_indexer_cmd(base_cmd: list[str], repo_path: Path, scheme: str | None) -> list[str]:
    if not _prefers_xcodebuild(repo_path):
        return base_cmd
    cmd = [*base_cmd, "--build-tool", "xcodebuild"]
    if scheme:
        cmd += ["--scheme", scheme]
    return cmd
```

**Call site** (line 796-797):
```python
        if language == "swift":
            indexer_cmd = _swift_indexer_cmd(indexer_cmd, repo_path, scheme)
```

**Why:** `--cache-dir` must be injected on EVERY Swift invocation (both the xcodebuild and swiftpm paths). The injection point is this same call site — add `--cache-dir` before the `_swift_indexer_cmd` call so it applies to both paths, or append it inside `_swift_indexer_cmd` after the existing args. The slug is available at the call site (resolved earlier in `index_repo`).

---

### `src/jarvis/index_cli.py` — forget cache sweep (controller, CRUD delete)

**Analog:** `src/jarvis/index_cli.py:1096-1117` — `_cmd_forget`

**Forget sweep pattern** (lines 1112-1115):
```python
def _cmd_forget(args: argparse.Namespace) -> int:
    # ... slug resolution, registry.forget ...
    index_dir = config.index_dir(slug)
    if index_dir.exists():
        shutil.rmtree(index_dir)
    _remove_zoekt_shards(slug)
    shutil.rmtree(config.lancedb_dir() / f"{slug}.lance", ignore_errors=True)
    print(f"forgot {slug}")
    return 0
```

**Why:** D-06's cache sweep adds one line after the lancedb sweep:
```python
    shutil.rmtree(config.swift_cache_dir(slug), ignore_errors=True)
```
Same `ignore_errors=True` pattern — the cache may not exist if the repo was never Swift-indexed.

---

### `src/jarvis/config.py` — swift_cache_dir helper (utility, transform)

**Analog:** `src/jarvis/config.py:33-37` — `lancedb_dir`

**Path helper pattern** (lines 33-37):
```python
def lancedb_dir(root: Path | None = None) -> Path:
    """Directory holding one LanceDB table per repo (semantic search vectors)."""
    return data_dir(root) / "lancedb"
```

**Why:** The new helper follows this exact shape — a one-liner delegating to `data_dir()` with optional override:
```python
def swift_cache_dir(slug: str, root: Path | None = None) -> Path:
    return (root or data_dir()) / "cache" / "scip-swift" / slug
```

---

### `src/jarvis/watch.py` — Swift artifact ignores (utility, event-driven)

**Analog:** `src/jarvis/watch.py:18-24` — `should_ignore_path` + `_IGNORED_PATH_PARTS`

**Current ignore pattern** (lines 18-24):
```python
_IGNORED_PATH_PARTS = {".git", "node_modules", ".venv", "__pycache__", "dist", "build"}


def should_ignore_path(path: str) -> bool:
    """True if any path component is a vendor/VCS directory this watcher
    should never trigger a reindex for."""
    return any(part in _IGNORED_PATH_PARTS for part in path.split("/"))
```

**Why:** D-07/D-08 extend the existing set. Note: `build` (no dot) is already covered; `.build` (SwiftPM) is a distinct entry. Additions: `{".scip-cache", ".build", "DerivedData", ".index-store", "IndexStore", ".swiftpm"}`. Pure set extension — no logic change to the function.

---

### `tests/test_setup_sh.py` — Resolution/floor/digest tests (test)

**Analog:** `tests/test_setup_sh.py:718-748` — `test_install_zoekt_extracts_both_binaries` with `ZOEKT_BASE_URL` seam

**Local-tarball seam test pattern** (lines 718-748):
```python
def test_install_zoekt_extracts_both_binaries(tmp_path):
    """Verify both members land, using a local tarball over file://."""
    stage = tmp_path / "stage"
    stage.mkdir()
    for name in ("zoekt-git-index", "zoekt-webserver"):
        p = stage / name
        p.write_text("#!/bin/sh\ntrue\n")
    tar_path = tmp_path / "zoekt-darwin-arm64.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        for name in ("zoekt-git-index", "zoekt-webserver"):
            tf.add(stage / name, arcname=name)
    sha_path = tmp_path / "zoekt-darwin-arm64.tar.gz.sha256"
    digest = hashlib.sha256(tar_path.read_bytes()).hexdigest()
    sha_path.write_text(f"{digest}  zoekt-darwin-arm64.tar.gz\n")

    bin_path = tmp_path / "bin"
    result = run_func(
        f'ZOEKT_BASE_URL="file://{tmp_path}" install_zoekt darwin arm64',
        env={
            "JARVIS_BIN_DIR": str(bin_path),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "FORCE": "1",
        },
    )
    assert result.returncode == 0, result.stderr
    assert (bin_path / "zoekt-git-index").is_file()
```

**`run_func` harness** (lines 35-48):
```python
def run_func(snippet: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Source setup.sh, then run `snippet`. Returns the completed process."""
    full_env = {"JARVIS_SETUP_SOURCED": "1", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    if env:
        full_env.update(env)
    return subprocess.run(
        [POSIX_SH, "-c", f". {SETUP_SH}\n{snippet}"],
        capture_output=True, text=True, env=full_env,
    )
```

**Why:** The new resolution/floor/digest tests serve a local JSON file via `SCIP_SWIFT_API_URL="file://..."` (same seam pattern as `ZOEKT_BASE_URL="file://..."`), then call `install_scip_swift darwin arm64`. Test cases: (1) valid latest above floor → installs, (2) latest below floor → loud failure, (3) tampered digest → checksum mismatch. The `run_func` harness + `JARVIS_SETUP_SOURCED=1` + `JARVIS_BIN_DIR` env pattern applies verbatim.

---

### `tests/test_index_cli.py` — Floor + cache-dir + forget tests (test)

**Analog 1:** `tests/test_index_cli.py:612-658` — version check tests

**Monkeypatched version check test pattern** (lines 627-658):
```python
def test_check_scip_version_rejects_v070(monkeypatch):
    """v0.7.0 converts successfully but silently drops every range."""
    import jarvis.index_cli as cli

    monkeypatch.setattr(cli, "_scip_version_output", lambda: "scip version v0.7.0")
    with pytest.raises(cli.IndexingError) as exc:
        cli.check_scip_version()
    message = str(exc.value)
    assert "0.7.0" in message
    assert "0.9.0" in message, "must state the required floor"


def test_check_scip_version_accepts_v090(monkeypatch):
    import jarvis.index_cli as cli

    monkeypatch.setattr(cli, "_scip_version_output", lambda: "scip version v0.9.0")
    cli.check_scip_version()  # must not raise


def test_check_scip_version_tolerates_unparseable(monkeypatch):
    """An unrecognized format must not block indexing outright."""
    import jarvis.index_cli as cli

    monkeypatch.setattr(cli, "_scip_version_output", lambda: "weird build")
    cli.check_scip_version()  # must not raise
```

**Why:** The new scip-swift floor tests mirror these exactly: monkeypatch `_scip_swift_version_output`, test reject/accept/tolerate-unparseable. The version string format differs (`0.3.0 (swift 6.2.4)` vs `scip version v0.9.0`) but `parse_scip_version` handles both via its `v?` regex.

---

**Analog 2:** `tests/test_index_cli.py:229-280` — `_swift_indexer_cmd` argv tests

**Argv assertion test pattern** (lines 252-277):
```python
def test_swift_indexer_cmd_adds_xcodebuild_when_xcodeproj_present(tmp_path: Path):
    from jarvis.index_cli import _swift_indexer_cmd

    (tmp_path / "Package.swift").write_text("// swift-tools-version: 6.0\n")
    (tmp_path / "MyLib.xcodeproj").mkdir()
    assert _swift_indexer_cmd(["scip-swift"], tmp_path, scheme=None) == [
        "scip-swift", "--build-tool", "xcodebuild",
    ]
```

**Why:** The `--cache-dir` tests extend these: same tmp_path fixture, same function call, now assert that `--cache-dir <slug-path>` appears in the returned argv for BOTH the xcodebuild and swiftpm paths.

---

### `tests/test_watch.py` — Swift ignore cases (test)

**Analog:** `tests/test_watch.py:91-98` — `test_should_ignore_path_skips_vendor_and_git_dirs`

**Ignore test pattern** (lines 91-98):
```python
def test_should_ignore_path_skips_vendor_and_git_dirs():
    from jarvis.watch import should_ignore_path

    assert should_ignore_path("/repo/.git/index") is True
    assert should_ignore_path("/repo/node_modules/pkg/index.js") is True
    assert should_ignore_path("/repo/.venv/lib/foo.py") is True
    assert should_ignore_path("/repo/__pycache__/foo.pyc") is True
    assert should_ignore_path("/repo/src/main.py") is False
```

**Why:** Extend this exact test function (or add a sibling) with Swift artifact cases: `/repo/.build/x86_64-apple-macosx/debug/...`, `/repo/DerivedData/Build/...`, `/repo/.scip-cache/index-db/...`, etc.

---

### `.github/workflows/setup-smoke.yml` — Post-install Swift index step (config, request-response)

**Analog:** `.github/workflows/setup-smoke.yml:48-58` — existing scip-swift install + verify step

**Existing scip-swift CI step** (lines 48-58):
```yaml
      - name: Install scip-swift via setup.sh
        run: |
          set -eu
          JARVIS_BIN_DIR="${RUNNER_TEMP}/ci-bin" sh setup.sh --only scip-swift
          if [ "$RUNNER_OS" = "macOS" ]; then
            "${RUNNER_TEMP}/ci-bin/scip-swift" --version
          fi
```

**Why:** The new post-install index step follows this pattern: gated on `$RUNNER_OS = "macOS"`, uses `RUNNER_TEMP/ci-bin` on PATH, sets `JARVIS_DATA_DIR` to a temp dir. Add after the existing scip-swift verify step.

---

### `tests/fixtures/mini_xcode_repo/` — New .xcodeproj fixture (fixture)

**Analog 1:** `tests/fixtures/mini_swift_repo/` — existing Swift fixture structure

**Why:** Same `tests/fixtures/` location, same git-tracked requirement (language detection reads `git ls-files`). But `mini_swift_repo` is Package.swift-only — the new fixture needs a `.xcodeproj` to exercise `_prefers_xcodebuild()` → xcodebuild dispatch.

**Analog 2:** Upstream `Fixtures/XcodeTestProject` at tag v0.3.0 (proven to index green through jarvis this session)

**Why:** Template contents: `scip-swift-test.xcodeproj/project.pbxproj` (9,095 bytes) + `project.xcworkspace/contents.xcworkspacedata` (135 bytes) + `scip-swift-test/SwiftFile.swift` (517 bytes). Single `com.apple.product-type.tool` target, no signing, one auto-resolvable scheme.

---

## Shared Patterns

### POSIX sh test seam (env-var override with fallback)
**Source:** `setup.sh:507` (`ZOEKT_BASE_URL`)
**Apply to:** `setup.sh` new `SCIP_SWIFT_API_URL` for `install_scip_swift` rewrite
```sh
_base="${SCIP_SWIFT_API_URL:-https://api.github.com/repos/${SCIP_SWIFT_REPO}/releases/latest}"
```

### Version-aware skip gate (not presence-gated)
**Source:** `setup.sh:438-446` (`installed_scip_matches_pin`) + `setup.sh:452-460` (scip installer version-gate comment)
**Apply to:** `setup.sh` new `install_scip_swift` — skip only when installed version satisfies the resolved latest

### `run_func` test harness
**Source:** `tests/test_setup_sh.py:35-48`
**Apply to:** All new `test_setup_sh.py` tests — source setup.sh with `JARVIS_SETUP_SOURCED=1`, call function, assert on stdout/stderr/returncode

### Monkeypatched version-output isolation
**Source:** `tests/test_index_cli.py:627-658` + `src/jarvis/index_cli.py:392-398`
**Apply to:** New `check_scip_swift_version` tests — `_scip_swift_version_output()` isolated function, monkeypatched in tests

### IndexingError with setup.sh recovery hint
**Source:** `src/jarvis/index_cli.py:408-415` (`check_scip_version` error message)
**Apply to:** New `check_scip_swift_version` — same `IndexingError` subclass, same "re-run setup.sh" remedy wording

### `shutil.rmtree(..., ignore_errors=True)` for optional cleanup
**Source:** `src/jarvis/index_cli.py:1115` (`_cmd_forget` lancedb sweep)
**Apply to:** New forget cache sweep — same pattern, cache dir may not exist

### `data_dir()` delegation in config helpers
**Source:** `src/jarvis/config.py:33-37` (`lancedb_dir`)
**Apply to:** New `swift_cache_dir(slug, root)` — same `data_dir(root) / <subpath>` shape

## No Analog Found

All files have close analogs in the codebase. No file requires the planner to fall back on RESEARCH.md patterns alone.

## Metadata

**Analog search scope:** `setup.sh`, `src/jarvis/index_cli.py`, `src/jarvis/config.py`, `src/jarvis/watch.py`, `tests/test_setup_sh.py`, `tests/test_index_cli.py`, `tests/test_watch.py`, `.github/workflows/setup-smoke.yml`, `tests/fixtures/`
**Files scanned:** 9
**Pattern extraction date:** 2026-08-22
