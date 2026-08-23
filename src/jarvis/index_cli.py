"""`jarvis` CLI: detect language -> run the matching SCIP indexer ->
`scip expt-convert` -> populate package graph -> zoekt-git-index -> atomic
pointer swap -> registry update.

Subcommands: index, list, status, reindex, forget, watch.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from jarvis import config
from jarvis.graph import GraphStore, populate_graph_for_repo
from jarvis.registry import (
    DEGRADED_STATUS,
    ORIGIN_FAILED_HARD,
    ORIGIN_FALLBACK,
    ORIGIN_MANUAL,
    ORIGIN_SIGNATURE,
    SEARCH_ONLY_STATUS,
    Registry,
    RegisteredRepo,
    origin_of,
    recovery_for,
)
from jarvis.watch import Debouncer, should_ignore_path

if TYPE_CHECKING:
    from jarvis.semantic import SemanticIndexReport

_LANGUAGE_INDEXERS: dict[str, tuple[str, list[str]]] = {
    ".ts": ("typescript", ["scip-typescript", "index"]),
    ".tsx": ("typescript", ["scip-typescript", "index"]),
    ".py": ("python", ["scip-python", "index"]),
    ".java": ("java", ["scip-java", "index"]),
    ".kt": ("java", ["scip-java", "index"]),
    # No "index" token, unlike the others: scip-swift gained its `index`
    # subcommand only after v0.1.0 was released, so `scip-swift index …` fails
    # against that binary (it parses "index" as the repo path). The bare form
    # works on every version -- old binaries default the repo path to cwd, and
    # newer ones dispatch to `index` as their default subcommand. Verified
    # against both v0.1.0 and v0.1.1. Do not add "index" back.
    ".swift": ("swift", ["scip-swift"]),
}
# Priority order for tie-breaking when extension counts are equal.
_EXT_PRIORITY = [".ts", ".tsx", ".py", ".java", ".kt", ".swift"]

# Reverse of `_LANGUAGE_INDEXERS`, derived from it so the two cannot drift.
# Several extensions share a language (.ts/.tsx, .java/.kt) and map to the
# same command, so the collapse is lossless. Its keys are the valid
# `--language` values.
_INDEXER_BY_LANGUAGE: dict[str, list[str]] = {
    language: cmd for language, cmd in _LANGUAGE_INDEXERS.values()
}

_IGNORED_DIRS = config.IGNORED_DIRS

# `scip expt-convert` below this version cannot read scip.proto's `typed_range`
# oneof (its Go bindings predate occurrence_range.go), so it silently writes a
# schema-valid database with zero chunks and zero mentions -- every navigation
# query then returns empty. Verified: the same .scip file yields chunks=0/
# mentions=0 under v0.7.0 and chunks=1/mentions=14 under v0.9.0.
MIN_SCIP_VERSION = (0, 9, 0)
# scip-swift before 0.3.0 mis-dispatches xcodebuild for .xcodeproj repos
# (upstream fix 9bcf1688, first released in 0.3.0), silently producing
# broken indexes. Since 02-01, setup.sh auto-rolls installs to the latest
# release, so this runtime gate is the defense against a stale binary left
# earlier on PATH -- the same PATH-shadowing hazard the scip floor above
# guards against.
MIN_SCIP_SWIFT_VERSION = (0, 3, 0)


# Registry status for an index that published real symbols but no navigable
# positions -- the fingerprint of a converter or indexer that dropped every
# occurrence range. Publishing still proceeds (the symbol table is useful),
# but the status must not claim unqualified success.
PARTIAL_STATUS = "partial"

# Recorded as the language when a search-only repo has no SCIP-indexable
# source at all (a Go or Ruby repo). `registry.language` is NOT NULL, so this
# has to be a value rather than NULL.
UNKNOWN_LANGUAGE = "unknown"

# Indexer failures that are known to be unfixable from here, and so degrade to
# a search-only publish instead of a hard failure. Each entry is
# (required substrings, human reason) — EVERY substring must be present, which
# is what keeps a generic AbstractMethodError from some unrelated library out.
#
# Deliberately narrow. This is not "fall back on any failure": a transient
# Gradle break or a missing binary must still fail loudly rather than be
# laundered into an apparent success.
_SEARCH_ONLY_SIGNATURES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("AbstractMethodError", "org.jetbrains.kotlin.fir"),
        "scip-kotlinc is compiled against one exact Kotlin version and this repo uses another "
        "(the compiler-plugin API is internal and unstable)",
    ),
    (
        ("NoSuchMethodError", "org.jetbrains.kotlin.fir"),
        "scip-kotlinc is compiled against one exact Kotlin version and this repo uses another "
        "(the compiler-plugin API is internal and unstable)",
    ),
    (
        ("No SCIP shards found",),
        "the build produced no SCIP shards — for Android/AGP this is expected, because "
        "scip-java's Gradle plugin keys off standard source sets that AGP replaces with "
        "variants (upstream scip-java#177)",
    ),
    # --- scip-swift 0.3.0 (captured 2026-08-23) -------------------------------
    # Captured from the pinned scip-swift 0.3.0 against known-failing repo
    # shapes. The tokens are deliberately path-free: both scip-swift error
    # lines interpolate the repo/cache paths, which would pin a signature to
    # one machine. On a scip-swift version bump these must be re-captured —
    # wording drift fails hard (unmatched → hard failure) and must never
    # silently degrade.
    (
        ("Could not detect a build system", "no Package.swift and no .xcodeproj/.xcworkspace found"),
        "the repo has Swift sources but neither a Package.swift nor an Xcode project, so "
        "scip-swift has no build system to run",
    ),
    (
        ("Build succeeded but no IndexStore was produced",),
        "the build completed but produced no index store — scip-swift cannot extract "
        "symbols from a build that emits none",
    ),
)


def _search_only_reason(output: str) -> str | None:
    """Match indexer output against `_SEARCH_ONLY_SIGNATURES`; None means the
    failure is not recognized and must propagate."""
    for required, reason in _SEARCH_ONLY_SIGNATURES:
        if all(token in output for token in required):
            return reason
    return None


# Not a _SEARCH_ONLY_SIGNATURES entry on purpose: that path persists
# search_only=1, and --search-only is store_true/default=None -- settable but
# never clearable, so the only escape is `jarvis forget` + reindex. Correct
# for Android/AGP, which is permanently unindexable; a trap for this, which one
# `brew install bash` fixes. Failing loudly with the remedy keeps the repo
# `failed` and recoverable by a plain reindex.
#
# Upstream: https://github.com/scip-code/scip-java/issues/987 -- remove this
# workaround once scip-java emits a bash-3.2-safe wrapper.
_BASH_SHIM_TOKENS = ("LAUNCHER_ARGS[@]", "unbound variable")

_BASH_SHIM_REMEDY = (
    "scip-java's generated javac wrapper requires bash >= 4.4, but this machine's "
    "default bash is older (macOS ships 3.2). Install a newer bash "
    "(`brew install bash`), re-run setup.sh to create the shim, then reindex."
)


def _bash_shim_failure(output: str) -> bool:
    """True when the indexer died on bash < 4.4 expanding an empty array."""
    return all(token in output for token in _BASH_SHIM_TOKENS)


class UnsupportedLanguageError(Exception):
    """Raised when no supported source extension is found under a repo."""


class IndexingError(Exception):
    """Raised when an indexing pipeline step (indexer/convert/zoekt) fails."""


class MissingBinaryError(IndexingError):
    """A pipeline step's executable was not on PATH.

    Deliberately its own type beside IndexingError (FALL-04): a missing
    binary is a setup.sh problem, and the opt-in fallback must keep it a
    loud failure instead of laundering "run setup.sh" into a published
    index. IS-A IndexingError, so every existing except-site keeps working.
    """


class SearchPublishedButIncomplete(IndexingError):
    """`_publish_search_only` wrote its zoekt shards and then failed a later
    step (SCIP retirement) -- a PARTIAL publish, not "nothing published".
    The degrade handler catches it separately so its message states what
    actually landed (WR-02); every other caller treats it as the
    IndexingError it IS-A, so their except-clauses keep working unchanged.
    """


class NotAGitRepositoryError(Exception):
    """Raised when a repo path is not a git working tree.

    Indexing already required git -- `_git_head()` reads the commit SHA --
    so this is not a new restriction, just an early and explicit one.
    """


def detect_language(repo_path: Path) -> tuple[str, list[str]]:
    """Scan `repo_path`'s git-tracked files for supported source
    extensions; return `(language, indexer_command)` for whichever
    extension has the most files, ties broken by `_EXT_PRIORITY` order.

    Git-tracked, not a filesystem walk: a walk also counts gitignored
    vendored checkouts and sibling clones, which can outnumber the repo's
    own code and pick a language the repo does not use.

    `_IGNORED_DIRS` is still applied on top, because git does not exclude
    build output a repo happens to commit (a checked-in `dist/` or a
    vendored `node_modules`).
    """
    counts: Counter[str] = Counter()
    for name in _git_tracked_files(repo_path):
        path = Path(name)
        if any(part in _IGNORED_DIRS for part in path.parts):
            continue
        if path.suffix in _LANGUAGE_INDEXERS:
            counts[path.suffix] += 1

    present = [ext for ext in _EXT_PRIORITY if counts[ext] > 0]
    if not present:
        raise UnsupportedLanguageError(
            f"no supported source files (.ts/.tsx/.py/.java/.kt/.swift) tracked under {repo_path}"
        )
    best_ext = max(present, key=lambda ext: (counts[ext], -_EXT_PRIORITY.index(ext)))
    return _LANGUAGE_INDEXERS[best_ext]


def _prefers_xcodebuild(repo_path: Path) -> bool:
    """True when `repo_path` has a checked-in `.xcodeproj`/`.xcworkspace`
    alongside `Package.swift`. `scip-swift`'s own `BuildBackendDetector`
    picks `swiftpm` whenever `Package.swift` exists, even when that can't
    build — e.g. a UIKit-only iOS package with no macOS platform support,
    where plain `swift build` fails with "no such module 'UIKit'" on the
    macOS host destination it defaults to."""
    return any(repo_path.glob("*.xcodeproj")) or any(repo_path.glob("*.xcworkspace"))


def _swift_indexer_cmd(
    base_cmd: list[str], repo_path: Path, scheme: str | None, cache_dir: Path
) -> list[str]:
    """Extend `base_cmd` (`["scip-swift"]`) with `--build-tool xcodebuild`
    (and `--scheme`, if given) when `repo_path` has a checked-in Xcode
    project — see `_prefers_xcodebuild`. Non-Swift callers never reach
    this function.

    `--cache-dir` rides every invocation on both build paths (D-05): it
    keeps the incremental cache, build scratch, and derived data out of
    the repo tree (and out of ~/Library/Developer/Xcode/DerivedData),
    under a per-repo directory whose lifecycle jarvis owns."""
    if not _prefers_xcodebuild(repo_path):
        return [*base_cmd, "--cache-dir", str(cache_dir)]
    cmd = [*base_cmd, "--build-tool", "xcodebuild"]
    if scheme:
        cmd += ["--scheme", scheme]
    return cmd + ["--cache-dir", str(cache_dir)]


def _java_indexer_env() -> dict[str, str]:
    """scip-java's Gradle plugin races against itself when Gradle runs tasks in
    parallel: two modules' `scipPrintDependencies` mutate shared state and the
    build dies with java.util.ConcurrentModificationException. Forcing
    single-threaded execution avoids it.

    Appended to any existing GRADLE_OPTS rather than replacing it, so a user's
    heap settings survive. Reported upstream.

    PATH gets the shim dir prepended when it holds a bash: scip-java's
    generated javac wrapper is `#!/usr/bin/env bash` (so bash comes from PATH)
    with `set -eu` and an unguarded `"${LAUNCHER_ARGS[@]}"`, which is an error on
    bash < 4.4. macOS ships 3.2, so every Maven build fails at
    maven-compiler-plugin's version probe without this. Remove once the pinned
    scip-java emits a bash-3.2-safe wrapper.

    Only the shim dir, never a general bin dir: prepending e.g. Homebrew's bin
    would also shadow java/mvn/git for the build.
    """
    existing = os.environ.get("GRADLE_OPTS", "")
    env = {"GRADLE_OPTS": f"{existing} -Dorg.gradle.parallel=false".strip()}
    shims = config.shim_dir()
    if (shims / "bash").exists():
        env["PATH"] = f"{shims}{os.pathsep}{os.environ.get('PATH', '')}"
    return env


def _git_tracked_files(repo_path: Path) -> list[str]:
    """Repo-relative paths of git-tracked files.

    Git is the source of truth for "what belongs to this repo". A
    filesystem walk also counts gitignored scratch directories -- vendored
    checkouts, sibling clones, worktrees -- which can outnumber the repo's
    own code and flip language detection to a language the repo does not
    actually use.

    `-z` (NUL-delimited) is required, not stylistic: with the default
    newline separator git quotes non-ASCII names, which would corrupt
    suffix parsing downstream.
    """
    result = subprocess.run(
        ["git", "-C", str(repo_path), "ls-files", "-z"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise NotAGitRepositoryError(
            f"{repo_path} is not a git repository (git ls-files: {result.stderr.strip()})"
        )
    return [name for name in result.stdout.split("\0") if name]


_GITLINK_MODE = "160000"


def _tracked_blob_count(repo_path: Path) -> int:
    """How many git-tracked blobs exist at HEAD — the number of files
    `zoekt-git-index` should index, and so the expected search coverage.

    Not built on `_git_tracked_files`: that uses plain `ls-files -z`, which
    emits paths with no mode, and a submodule gitlink is indistinguishable
    from a file in that output. `-s` prefixes each entry with
    `<mode> <sha> <stage>\\t`, letting mode 160000 (gitlink) be dropped —
    required because `-submodules=false` means zoekt never descends into a
    submodule, so counting its gitlink would make the expectation unreachable.
    """
    result = subprocess.run(
        ["git", "-C", str(repo_path), "ls-files", "-s", "-z"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise NotAGitRepositoryError(
            f"{repo_path} is not a git repository (git ls-files -s: {result.stderr.strip()})"
        )
    count = 0
    for entry in result.stdout.split("\0"):
        if not entry:
            continue
        if not entry.startswith(f"{_GITLINK_MODE} "):
            count += 1
    return count


def _git_head(repo_path: Path) -> str:
    """Current commit SHA.

    Distinguishes "not a git repository at all" from "git repository with
    no commits": `git rev-parse HEAD` fails identically in both cases, so
    a directory that isn't a git repo at all would otherwise be
    misdiagnosed as "has no commits yet". Checking `--is-inside-work-tree`
    first raises `NotAGitRepositoryError` for the former; only a real
    git repo with no commits reaches the `IndexingError` below, naming the
    cause rather than letting a bare CalledProcessError escape."""
    check = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        raise NotAGitRepositoryError(
            f"{repo_path} is not a git repository "
            f"(git rev-parse --is-inside-work-tree: {check.stderr.strip()})"
        )
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise IndexingError(
            f"{repo_path} has no commits yet (git rev-parse HEAD: {result.stderr.strip()})"
        )
    return result.stdout.strip()


def _watch_should_retry_full_build(entry: RegisteredRepo | None, current_sha: str) -> bool:
    """FALL-05 anti-treadmill: the watch driver skips the full-build retry
    only when the repo is degraded AND the source sha is still the one the
    last full-build attempt failed at — the degraded terminal write
    persists that attempt sha in commit_sha. Everything else retries: a
    missing row, a failed row (record_failure stores commit_sha=NULL, and
    NULL never equals a real sha), an indexed/search-only row, or any sha
    change. The skip is a WATCH-DRIVER policy only — index_repo itself
    always retries (FALL-03: an explicit `jarvis index` never skips), so
    the predicate must never move into it. Kept pure (no subprocess, no
    Registry, no watchdog) per watch.py's own philosophy so the full
    decision matrix is unit-testable without the observer machinery."""
    return not (
        entry is not None
        and entry.status == DEGRADED_STATUS
        and entry.commit_sha is not None
        and entry.commit_sha == current_sha
    )


def _watch_skip_check(repo_path: Path, slug: str, root: Path | None = None) -> bool:
    """FALL-05 consult for the watch driver: True means "retry the full
    build now". Opens a short-lived Registry over the row, reads the
    current HEAD sha, and applies `_watch_should_retry_full_build`.
    Read-only by design (T-3-06): no status churn, no counters — only the
    attempt sha the degraded terminal write already persisted is
    consulted. ANY failure (a locked registry.db, a git hiccup) fails
    OPEN to retry (T-3-07) — the safe direction for self-heal: the worst
    outcome of a broken consult is one extra build, never a suppressed
    one."""
    try:
        registry = Registry(config.data_dir(root) / "registry.db")
        try:
            entry = registry.get(slug)
        finally:
            registry.close()
        return _watch_should_retry_full_build(entry, _git_head(repo_path))
    except Exception:
        return True


def _run(cmd: list[str], *, cwd: Path, step: str,
         env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """`env`, when given, is merged OVER a copy of `os.environ` rather than
    replacing it — a bare replacement would drop PATH and break the very
    subprocess lookup that finds the indexer.

    Returns the completed process so callers can read output on success;
    `zoekt-git-index` reports its file count on stderr, which the search
    coverage check parses.

    A missing executable raises `FileNotFoundError`, not a non-zero exit, so
    it is translated into an `IndexingError` naming `setup.sh` — the same
    remedy `_scip_version_output` gives. This matters most for
    `zoekt-git-index`: every install predating the switch has `zoekt-index`
    instead, and there is deliberately no fallback to it, because falling back
    would silently reintroduce indexing of gitignored content.
    """
    merged = {**os.environ, **env} if env else None
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=merged)
    except FileNotFoundError as exc:
        raise MissingBinaryError(
            f"{step} failed: {cmd[0]} not found on PATH — run setup.sh"
        ) from exc
    if result.returncode != 0:
        raise IndexingError(f"{step} failed ({' '.join(cmd)}):\n{result.stdout}\n{result.stderr}")
    return result


def _publish_atomically(target_dir: Path, versioned_name: str, sha: str) -> None:
    """Write the pointer file via write-temp-then-rename (atomic on the
    same filesystem, POSIX `rename(2)`) so a concurrent reader never
    observes a half-written pointer — it either sees the old versioned
    filename or the new one, never a partial write."""
    pointer_file = target_dir / "current"
    old_pointer = pointer_file.read_text(encoding="utf-8").strip() if pointer_file.exists() else None

    tmp_pointer = target_dir / f".current.tmp-{os.getpid()}"
    tmp_pointer.write_text(versioned_name, encoding="utf-8")
    os.replace(tmp_pointer, pointer_file)

    if old_pointer and old_pointer != versioned_name:
        (target_dir / old_pointer).unlink(missing_ok=True)
        old_metadata = old_pointer.removesuffix(".db") + ".metadata.json"
        (target_dir / old_metadata).unlink(missing_ok=True)


def parse_scip_version(output: str) -> tuple[int, int, int] | None:
    """Parse `scip --version` output, e.g. "scip version v0.9.0".

    Returns None when the format is unrecognized, so an unexpected build
    string degrades to "cannot verify" rather than blocking indexing.
    """
    match = re.search(r"v?(\d+)\.(\d+)\.(\d+)", output)
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _scip_version_output() -> str:
    """Isolated for tests to monkeypatch."""
    try:
        result = subprocess.run(["scip", "--version"], capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise IndexingError("scip not found on PATH — run setup.sh") from exc
    return f"{result.stdout}\n{result.stderr}"


def _scip_swift_version_output() -> str:
    """Isolated for tests to monkeypatch."""
    try:
        result = subprocess.run(["scip-swift", "--version"], capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise IndexingError("scip-swift not found on PATH — run setup.sh") from exc
    return f"{result.stdout}\n{result.stderr}"


def check_scip_swift_version() -> None:
    """Raise IndexingError when `scip-swift` is too old for the argv contract.

    Reuses parse_scip_version verbatim -- its v-optional regex already
    parses scip-swift's output, which prints `0.3.0 (swift 6.2.4)` with
    no `v` prefix.
    """
    version = parse_scip_version(_scip_swift_version_output())
    if version is None:
        # Unknown format: warn-by-omission rather than block, exactly as
        # check_scip_version does -- a wrong guess here would make Swift
        # indexing impossible against a valid future build.
        return
    if version < MIN_SCIP_SWIFT_VERSION:
        current = ".".join(str(p) for p in version)
        required = ".".join(str(p) for p in MIN_SCIP_SWIFT_VERSION)
        raise IndexingError(
            f"scip-swift v{current} is too old (need >= v{required}): versions before "
            "0.3.0 dispatch xcodebuild incorrectly for .xcodeproj repos and produce "
            "broken indexes. Re-run setup.sh, and remove any older scip-swift "
            "earlier on PATH."
        )


def check_scip_version() -> None:
    """Raise IndexingError when `scip` is too old to preserve ranges."""
    version = parse_scip_version(_scip_version_output())
    if version is None:
        # Unknown format: warn-by-omission rather than block. A wrong guess
        # here would make indexing impossible against a valid future build.
        return
    if version < MIN_SCIP_VERSION:
        current = ".".join(str(p) for p in version)
        required = ".".join(str(p) for p in MIN_SCIP_VERSION)
        raise IndexingError(
            f"scip v{current} is too old (need >= v{required}): it cannot read scip.proto's "
            "typed_range oneof, so occurrence positions are dropped and navigation returns "
            "empty results. Re-run setup.sh, and remove any older scip earlier on PATH."
        )


def index_has_navigation_data(conn: sqlite3.Connection) -> bool:
    """True when the index carries positional data, not just symbols.

    `chunks` holds the occurrence blobs and `mentions` the symbol/role rows
    that every per-file nav tool reads. Both empty while `global_symbols` is
    populated means positions were dropped somewhere upstream -- the index
    looks healthy and answers every nav query with an empty list.
    """
    chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    mentions = conn.execute("SELECT COUNT(*) FROM mentions").fetchone()[0]
    return chunks > 0 and mentions > 0


def _zoekt_index_cmd(zoekt_dir: Path, repo_path: Path) -> list[str]:
    """The `zoekt-git-index` invocation shared by both publish paths.

    `zoekt-git-index`, not `zoekt-index`: it walks the git tree and reads
    blobs by SHA, so gitignored content — `.venv/`, `node_modules/`,
    vendored checkouts — is absent by construction rather than by a
    hand-maintained denylist. This is upstream's recommended tool for local
    git repos, and the same reasoning `detect_language()` already applies:
    read git, not the filesystem.

    Consequence: search reflects HEAD, while SCIP navigation reflects the
    working tree. Uncommitted edits are searchable only after a commit.

    `-incremental=false`: the default (true) skips indexing when the shard is
    newer than refs, which would refuse to repair an already-published
    incomplete shard. jarvis's registry owns the when-to-reindex decision.

    `-submodules=false`: submodules are indexed under their own slugs, so
    including them here would duplicate content across two indexes and make
    the coverage expectation from `_tracked_blob_count` unreachable.
    """
    return [
        "zoekt-git-index",
        "-index", str(zoekt_dir),
        "-incremental=false",
        "-submodules=false",
        str(repo_path),
    ]


_INDEXED_FILE_COUNT_RE = re.compile(r"attempting to index (\d+) total files")


def _parse_indexed_file_count(output: str) -> int | None:
    """How many files `zoekt-git-index` reported indexing, or None when the
    line is absent.

    Returns None rather than raising so an upstream log-format change
    degrades the coverage check to "unknown" instead of failing an otherwise
    healthy publish. The authoritative post-index count comes from zoekt's
    own `/api/list` at status time; this is the cheap index-time signal.
    """
    match = _INDEXED_FILE_COUNT_RE.search(output)
    return int(match.group(1)) if match else None


def _warn_on_coverage_shortfall(slug: str, expected: int, output: str) -> None:
    """Warn when the indexer saw fewer files than git tracks.

    Warns rather than failing: legitimate causes exist — zoekt skips files
    over its 2 MB `-file_limit`, files exceeding `-max_trigram_count`, and
    binaries. Mirrors how `index_has_navigation_data()` publishes a degraded
    index with a warning instead of refusing.
    """
    indexed = _parse_indexed_file_count(output)
    if indexed is None or indexed >= expected:
        return
    print(
        f"warning: {slug} indexed {indexed} of {expected} git-tracked files — "
        "searchCode results will be incomplete. Large files (>2MB) and binaries "
        "are skipped by design; a larger gap suggests a problem.",
        file=sys.stderr,
    )


def _sweep_zoekt_tmp_orphans(slug: str, root: Path | None = None) -> list[Path]:
    """Delete stranded `.tmp` shards for `slug`.

    `zoekt-git-index` writes `<name>.<n>.tmp` and renames on success, so a
    killed run (Ctrl-C, OOM) strands a temp file that is never usable and was
    never cleaned up — 545 MB of them accumulated once. A successful index is
    the natural moment to sweep this repo's leftovers.

    Slug-scoped like `_remove_zoekt_shards`: the `_v` in the glob stops "api"
    from matching "api-gateway"'s files.

    Runs between a successful `zoekt-git-index` run and `_publish_atomically`,
    so any failure here must never propagate: an `EACCES`/`EBUSY`/`EPERM` on
    `unlink()` would otherwise bubble up to `index_repo()`'s outer handler and
    discard a fully-successful publish over a cleanup-step failure.
    """
    zoekt_dir = config.data_dir(root) / ".zoekt"
    if not zoekt_dir.is_dir():
        return []
    removed: list[Path] = []
    for tmp in sorted(zoekt_dir.glob(f"{slug}_v*.zoekt*.tmp")):
        with contextlib.suppress(OSError):
            tmp.unlink()
            removed.append(tmp)
    return removed


def _resolve_scheme(registry: Registry, slug: str, scheme: str | None) -> str | None:
    """`scheme=None` means "leave the persisted override alone" (e.g. a
    `jarvis watch` reindex, which never repeats `--scheme`) rather than
    "clear it" -- looks up the existing registry row and falls back to its
    `scheme_override` when the caller passed nothing explicit."""
    if scheme is not None:
        return scheme
    existing = registry.get(slug)
    return existing.scheme_override if existing is not None else None


def _resolve_language(registry: Registry, slug: str, language: str | None) -> str | None:
    """`language=None` means "leave the persisted override alone" (a
    `jarvis watch` reindex never repeats the flag) rather than "clear
    it" — the same contract as `_resolve_scheme`. Returning None means no
    override is in force and detection should run."""
    if language is not None:
        return language
    existing = registry.get(slug)
    return existing.language_override if existing is not None else None


def _resolve_search_only(registry: Registry, slug: str, search_only: bool | None) -> bool:
    """`search_only=None` means "leave the persisted value alone" (a `jarvis
    watch` reindex never repeats the flag) rather than "clear it" — the same
    contract as `_resolve_scheme`. This is what stops a repo that already
    proved un-indexable from re-running a doomed multi-minute build."""
    if search_only is not None:
        return search_only
    existing = registry.get(slug)
    return existing.search_only if existing is not None else False


def _resolve_fallback(registry: Registry, slug: str, cli: bool | None) -> bool:
    """Fallback resolution, precedence locked by FALL-02: CLI > persisted >
    env > off. `cli=None` means the flag was not passed and the persisted
    value (if ever set) decides; a NULL persisted value defers to the
    JARVIS_FALLBACK_SEARCH_ONLY env tier, which reads off when unset.

    Deliberate deviation from the `_resolve_scheme` idiom: the RESOLVED
    bool is never written back. Persisting it would collapse NULL to 0 on
    the first env-off run and permanently lock a later env-on out
    (persisted outranks env — Pitfall 1). Only the explicit CLI value is
    ever persisted, via `Registry.set_fallback_enabled`."""
    if cli is not None:
        return cli
    existing = registry.get(slug)
    if existing is not None and existing.fallback_enabled is not None:
        return existing.fallback_enabled
    return config.fallback_search_only_from_env()


def _resolve_semantic_include(
    registry: Registry, slug: str, include: tuple[str, ...] | None
) -> tuple[str, ...]:
    """`include=None` means "leave the persisted value alone" (a `jarvis
    watch` reindex never repeats the flag) rather than "clear it" — the
    same contract as `_resolve_scheme`."""
    if include is not None:
        return include
    existing = registry.get(slug)
    return existing.semantic_include if existing is not None else ()


def _print_semantic_report(report: "SemanticIndexReport") -> None:
    """Index-time semantic summary. Everything goes to stderr, consistent
    with the other semantic notices, so stdout stays just `indexed <slug>`
    for scripting."""
    print(f"semantic: {report.rows} chunks from {report.files} files", file=sys.stderr)
    for skipped in report.skipped:
        print(f"semantic: skipped {skipped.file_path} ({skipped.reason})", file=sys.stderr)
    if report.token_stats is not None:
        stats = report.token_stats
        print(f"semantic: chunk tokens p50={stats.p50} p90={stats.p90} "
              f"max={stats.max}", file=sys.stderr)
    if report.prefix_warning:
        print(f"warning: {report.prefix_warning}", file=sys.stderr)
    if report.truncated is None:
        print("semantic: could not measure truncation", file=sys.stderr)
    elif report.truncated > 0:
        print(
            f"warning: {report.truncated} chunks exceeded the model's token limit "
            "and were truncated",
            file=sys.stderr,
        )

def _semantic_extra_missing() -> bool:
    """Isolated for tests to monkeypatch."""
    # The same top-level modules the semantic stage itself imports
    # lazily: semantic.py's `import lancedb` and embeddings.py's
    # `from sentence_transformers import SentenceTransformer`.
    # tree_sitter_language_pack is deliberately excluded — chunker.py
    # falls back to fixed-window chunking on any failure, so it is not
    # a hard requirement for semantic search.
    return (
        importlib.util.find_spec("lancedb") is None
        or importlib.util.find_spec("sentence_transformers") is None
    )


def _at_interactive_tty() -> bool:
    """Isolated for tests to monkeypatch."""
    # pip's convention: either stream redirected means automation, and
    # automation must never block on stdin — the non-TTY defense behind
    # the structural offer_semantic gate (SEMA-02).
    return sys.stdin.isatty() and sys.stdout.isatty()


def _install_semantic_extra() -> bool:
    """Isolated for tests to monkeypatch."""
    # The locked install command as a fixed argv list — never a shell
    # string, never built from the prompt answer. Deliberately not _run:
    # an install failure must warn and continue, not raise. torch-scale
    # downloads can take minutes, so a stalled network is bounded at
    # 600s (the index is already published; only the offer waits).
    uv = shutil.which("uv")
    if uv is None:
        return False
    try:
        result = subprocess.run(
            [uv, "pip", "install", "--python", sys.executable, "jarvis-mcp[semantic]"],
            capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def _run_semantic_stage(repo_path: Path, slug: str, root: Path | None,
                        include_prefixes: tuple[str, ...] = ()) -> bool:
    """Chunk + embed + write the LanceDB table. Optional and non-fatal:
    a missing `semantic` extra skips with a hint, any other failure warns
    and lets the SCIP/Zoekt publish proceed — the previous semantic table
    (if any) stays live."""
    try:
        from jarvis import semantic
        from jarvis.embeddings import SemanticExtraMissingError
    except ImportError:
        print(
            "semantic indexing skipped — install jarvis-mcp[semantic] "
            "(uv tool install), or `uv sync --extra semantic` in a source checkout",
            file=sys.stderr,
        )
        return False
    try:
        report = semantic.index_semantic(repo_path, slug, root=root,
                                         include_prefixes=include_prefixes)
        _print_semantic_report(report)
    except SemanticExtraMissingError as exc:
        print(f"semantic indexing skipped — {exc}", file=sys.stderr)
        return False
    except Exception as exc:
        print(
            f"warning: semantic indexing failed (SCIP/Zoekt index still published): {exc}",
            file=sys.stderr,
        )
        return False
    return True


def _retire_scip_artifacts(slug: str, root: Path | None) -> None:
    """Tear down a previously published SCIP index for `slug` before a
    search-only publish, so a repo that once had real navigation doesn't
    keep silently serving it once it degrades to search-only (explicit
    `--search-only`, or the automatic ABI-mismatch/no-shards fallback).

    Without this, `_publish_search_only` writes no `current` pointer but
    also never touches an OLD one left by an earlier successful index —
    navigation tools would keep answering from stale data instead of
    raising IndexNotFoundError, and `getIndexStatus` would report the
    self-contradictory `{"indexed": true, "status": "search-only"}`.

    Mirrors `_cmd_forget`'s own teardown for the SCIP-specific artifacts
    only: the pointer directory (`config.index_dir` holds nothing but the
    `current` pointer plus versioned `.db`/`.metadata.json` files) and this
    repo's outgoing graph edges (cleared via the same store method
    `populate_graph_for_repo` uses to make a reindex rebuild-not-accumulate,
    package rows themselves stay so a later real reindex still resolves by
    name). Zoekt shards and the semantic LanceDB table are deliberately left
    alone — this same publish republishes both further down, `_cmd_forget`'s
    full deletion of those does not apply here."""
    # Graph teardown BEFORE the rmtree (WR-02): the GraphStore writes are
    # the failure-prone half — a locked registry.db, the same race that
    # triggers this publish, raises right here — and the rmtree is the
    # irreversible half. Clearing edges first keeps a retire that fails
    # partway in the "failed run with a live pointer" state the system
    # already models and reports, instead of destroying the previous
    # navigation index on a run that is about to fail anyway; stale edges
    # are rebuilt by the next successful run's rebuild-not-accumulate
    # populate.
    graph_store = GraphStore(config.data_dir(root) / "registry.db")
    try:
        for package in graph_store.list_packages(repo=slug):
            graph_store.clear_outgoing_edges(package.id)
    finally:
        graph_store.close()

    index_dir = config.index_dir(slug, root)
    if index_dir.exists():
        shutil.rmtree(index_dir)


def _publish_search_only(repo_path: Path, slug: str, root: Path | None,
                         semantic_include: tuple[str, ...]) -> tuple[bool, int]:
    """Zoekt + semantic only: no SCIP indexer, no `scip expt-convert`, no graph
    population, and deliberately no `current` pointer. Without a pointer,
    `read_pointer` raises IndexNotFoundError and every navigation tool fails
    safely — server.py turns that into an explanation.

    Publishes zoekt first and retires any previously published SCIP index
    for this repo (see `_retire_scip_artifacts`) only AFTER the zoekt
    publish provably succeeded — a zoekt failure must leave an existing
    index untouched, so a failed run never destroys a good one. Retiring
    at all matters because this function is reachable not just from an
    explicit `--search-only` but from the automatic signature-based
    fallback and the Phase 3 degraded publish, both of which can trigger
    on a repo that indexed fine before (e.g. a Kotlin version bump hitting
    the ABI-mismatch signature on reindex). A failure in that retire step
    raises `SearchPublishedButIncomplete`, so callers can tell a partial
    publish (search live, the previous navigation index untouched) from a
    zoekt-step failure that published nothing.

    Returns whether the semantic stage succeeded (matching
    `_run_semantic_stage`'s contract) alongside the git-tracked file count;
    the caller records both on the registry row."""
    _pin_zoekt_repo_name(repo_path, slug)
    zoekt_dir = config.data_dir(root) / ".zoekt"
    zoekt_dir.mkdir(parents=True, exist_ok=True)
    tracked = _tracked_blob_count(repo_path)
    result = _run(_zoekt_index_cmd(zoekt_dir, repo_path), cwd=repo_path,
                  step="zoekt-git-index")
    _warn_on_coverage_shortfall(slug, tracked, result.stderr)
    _sweep_zoekt_tmp_orphans(slug, root)
    # The zoekt shards are live from here on, so a retire failure is a
    # PARTIAL publish (search on disk, previous SCIP index untouched), not
    # "nothing published" — the marker lets the degrade handler say which
    # one happened (WR-02).
    try:
        _retire_scip_artifacts(slug, root)
    except Exception as exc:
        raise SearchPublishedButIncomplete(str(exc)) from exc
    semantic_ok = _run_semantic_stage(repo_path, slug, root, semantic_include)
    print(
        f"note: {slug} published search-only — searchCode and semanticSearch work, "
        "navigation tools do not (no SCIP index).",
        file=sys.stderr,
    )
    return semantic_ok, tracked


def _reject_duplicate_slug_for_path(registry: Registry, slug: str, repo_path: Path) -> None:
    """One slug per repo path.

    `_pin_zoekt_repo_name` re-pins `zoekt.name` before every single index run,
    so two slugs indexing the same path don't actually corrupt each other's
    shard name — each stays correctly pinned at the moment it runs. The rule
    exists for three other reasons instead: (a) duplicate disk usage from
    indexing near-identical content twice under two slugs, (b) ambiguity
    about which slug is "the" search index for that path, and (c) on a
    linked worktree, `git config` writes to the repo's *shared* config
    (see CLAUDE.md's `zoekt.name` caveat), so two slugs on two worktrees of
    the same repo could otherwise race to set it — this per-path check
    prevents the common single-worktree case.

    Compares resolved paths because rows written before this check existed
    may hold unresolved ones. Same slug at the same path is the normal
    reindex/watch case and passes.
    """
    for existing in registry.list():
        if existing.slug == slug:
            continue
        try:
            same = Path(existing.path).resolve() == repo_path
        except OSError:
            # A registered path that no longer exists cannot collide.
            continue
        if same:
            raise IndexingError(
                f"{repo_path} is already indexed as {existing.slug!r}. "
                f"One slug per repo — run `jarvis forget {existing.slug}` first, "
                f"or reindex that slug instead."
            )


def _record_failure_best_effort(
    registry: Registry, slug: str, repo_path: Path, language: str,
    reason: str, text: str,
) -> None:
    """`Registry.record_failure` demoted to a stderr warning when the write
    itself raises (locked registry.db, disk-full -- often the very condition
    that triggered the handler). The failure row is bookkeeping ABOUT a
    failure: losing it must never replace or swallow the error the handler
    is about to raise, so every caller still raises its original error
    right after this, converted or not (WR-03)."""
    try:
        registry.record_failure(slug, str(repo_path), language,
                                ORIGIN_FAILED_HARD, reason, text)
    except Exception as recexc:
        rec_reason = next(
            (line for line in str(recexc).splitlines() if line.strip()),
            recexc.__class__.__name__)
        print(
            f"warning: recording the failed run for {slug} also failed — "
            f"{rec_reason}; the original failure ({reason}) is still raised "
            "and reported, but the registry row was not updated.",
            file=sys.stderr,
        )


def index_repo(
    repo_path: Path, *, slug: str | None = None, root: Path | None = None,
    scheme: str | None = None, semantic_include: tuple[str, ...] | None = None,
    language: str | None = None, search_only: bool | None = None,
    fallback_search_only: bool | None = None,
) -> str:
    """Runs the full pipeline for one repo; returns the slug it was
    published under. Registry status is `indexing` while running, `indexed`
    on success, `failed` (with the exception's message) on any step's
    failure, or `PARTIAL_STATUS` ("partial") on success when
    `index_has_navigation_data()` finds symbols published but `chunks` and
    `mentions` both empty.

    An explicit `language` (or one persisted from an earlier `--language`)
    bypasses `detect_language()` entirely -- an override means "do not
    guess", not "guess then correct". The registry's `language` column
    still records the effective language, so `list`/`status` show what was
    actually indexed.

    `zoekt-git-index` runs BEFORE the pointer swap: if it fails, no repo was
    ever left half-published — the previous version (if any) is still the
    live `current` pointer, matching the `failed` status. Only once both
    the SCIP index and the Zoekt shard are ready does `_publish_atomically`
    flip the pointer (old files are only deleted after the new pointer is
    live)."""
    repo_path = repo_path.resolve()
    slug = config.repo_slug(slug or repo_path.name)
    sha = _git_head(repo_path)

    registry = Registry(config.data_dir(root) / "registry.db")
    try:
        _reject_duplicate_slug_for_path(registry, slug, repo_path)
    except Exception:
        registry.close()
        raise
    # After the duplicate-slug gate, not before: rejecting a request for a
    # repo path already registered under another slug shouldn't depend on
    # `scip` being installed at all -- there's no point checking a tool
    # version for a call that's about to be refused anyway.
    # D-05: every failure from here until the run's first upsert predates
    # any registry write, so without this wrap a hard failure would leave
    # no row at all -- nothing could explain it and `jarvis reindex
    # <slug>` would report "no such repo". `_git_head` above stays outside
    # deliberately: it runs before the Registry exists, and a repo with no
    # commits is a malformed request, not a failed index run.
    resolved_language: str | None = None
    try:
        check_scip_version()
        # Resolved before language detection (not after, alongside scheme/
        # semantic_include) because the except branch below reads it: a
        # `jarvis reindex`/`watch` of a persisted search-only repo never
        # re-passes `--search-only`, so this must already reflect the
        # persisted value by the time detect_language() can raise.
        search_only = _resolve_search_only(registry, slug, search_only)
        # Resolved here (not at the degrade site) so the flag is settled
        # before any pipeline step can raise, and per-run env reads happen
        # exactly once (config owns the env tier).
        fallback_enabled = _resolve_fallback(registry, slug, fallback_search_only)
        language_override = _resolve_language(registry, slug, language)
        if language_override is not None:
            if language_override not in _INDEXER_BY_LANGUAGE:
                raise UnsupportedLanguageError(
                    f"{slug!r} has a persisted language override {language_override!r} that is no "
                    f"longer supported (expected one of {sorted(_INDEXER_BY_LANGUAGE)})"
                )
            language, indexer_cmd = language_override, _INDEXER_BY_LANGUAGE[language_override]
        else:
            try:
                language, indexer_cmd = detect_language(repo_path)
            except UnsupportedLanguageError:
                if not search_only:
                    raise
                language, indexer_cmd = UNKNOWN_LANGUAGE, []
        resolved_language = language
        scheme = _resolve_scheme(registry, slug, scheme)
        semantic_include = _resolve_semantic_include(registry, slug, semantic_include)

        if language == "swift" and not search_only:
            # Swift-only floor check (D-04): raising here flows through the
            # pre-pipeline failure wrap above, so the run is persisted as a
            # failed_hard row with the cause -- no new wiring needed.
            # Search-only runs never invoke the language indexer, and
            # setup.sh skips scip-swift entirely off darwin/arm64, so
            # probing here would break --search-only Swift repos on Linux
            # hosts (and reindex of a persisted search-only Swift repo).
            check_scip_swift_version()
            # Cache outside the repo tree keeps scip-swift's build
            # products out of the working copy and out of
            # ~/Library/Developer/Xcode/DerivedData.
            indexer_cmd = _swift_indexer_cmd(
                indexer_cmd, repo_path, scheme, config.swift_cache_dir(slug)
            )
    except Exception as exc:
        # `resolved_language` is None until resolution completes -- the
        # honest record for a run that died before establishing one (D-06:
        # the failed attempt's facts, never the last good run's).
        text = str(exc)
        reason = next((line for line in text.splitlines() if line.strip()),
                      exc.__class__.__name__)
        _record_failure_best_effort(
            registry, slug, repo_path,
            resolved_language if resolved_language is not None else UNKNOWN_LANGUAGE,
            reason, text)
        registry.close()
        raise

    if search_only:
        registry.upsert(slug, str(repo_path), language, None, "indexing",
                        scheme_override=scheme, semantic_include=semantic_include,
                        language_override=language_override, search_only=True)
        # Persist the explicit CLI flag the moment the row exists: the two
        # branches diverge before their first upsert, so both need the
        # write. Only the CLI value (never the resolved bool) is persisted
        # — see _resolve_fallback. Pre-pipeline failures intentionally
        # leave it unpersisted, matching --scheme/--language semantics.
        if fallback_search_only is not None:
            registry.set_fallback_enabled(slug, fallback_search_only)
        try:
            semantic_ok, tracked = _publish_search_only(repo_path, slug, root, semantic_include)
            registry.upsert(slug, str(repo_path), language, sha, SEARCH_ONLY_STATUS,
                            scheme_override=scheme, semantic_include=semantic_include,
                            language_override=language_override, search_only=True,
                            status_origin=ORIGIN_MANUAL)
            registry.mark_tracked_files(slug, tracked)
            if semantic_ok:
                registry.mark_semantic_indexed(slug)
        except Exception as exc:
            # Full failure record, not a bare status flip: a search-only run
            # whose own publish failed is still a failed run the status
            # surfaces must explain and `reindex` must find (D-05).
            text = str(exc)
            reason = next((line for line in text.splitlines() if line.strip()),
                          exc.__class__.__name__)
            _record_failure_best_effort(registry, slug, repo_path, language,
                                        reason, text)
            raise IndexingError(str(exc)) from exc
        finally:
            registry.close()
        return slug

    registry.upsert(slug, str(repo_path), language, None, "indexing", scheme_override=scheme,
                    semantic_include=semantic_include, language_override=language_override)
    # Same explicit-only persistence as the search-only branch above — the
    # branches diverge before their first upsert, so both sites are needed.
    if fallback_search_only is not None:
        registry.set_fallback_enabled(slug, fallback_search_only)

    # Flips to True the moment `_publish_atomically` makes the new index
    # live. Everything after that point in the try below is registry
    # bookkeeping, not indexing, and the degrade gate reads this flag to
    # refuse those failures -- the index is already published, so degrading
    # would destroy it.
    published = False

    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-index-") as scratch:
            scip_path = Path(scratch) / "index.scip"
            db_path = Path(scratch) / "index.db"

            try:
                _run([*indexer_cmd, "--output", str(scip_path)], cwd=repo_path,
                     step=f"{indexer_cmd[0]} index",
                     env=_java_indexer_env() if language == "java" else None)
            except IndexingError as exc:
                if language == "java" and _bash_shim_failure(str(exc)):
                    raise IndexingError(f"{_BASH_SHIM_REMEDY}\n\n{exc}") from exc
                reason = _search_only_reason(str(exc))
                if reason is None:
                    raise
                print(
                    f"note: {slug} cannot be SCIP-indexed — {reason}. "
                    "Falling back to search-only; this is remembered, so reindex/watch "
                    "will not repeat the build.",
                    file=sys.stderr,
                )
                semantic_ok, tracked = _publish_search_only(repo_path, slug, root, semantic_include)
                registry.upsert(slug, str(repo_path), language, sha, SEARCH_ONLY_STATUS,
                                scheme_override=scheme, semantic_include=semantic_include,
                                language_override=language_override, search_only=True,
                                status_origin=ORIGIN_SIGNATURE, status_reason=reason)
                registry.mark_tracked_files(slug, tracked)
                if semantic_ok:
                    registry.mark_semantic_indexed(slug)
                return slug
            _run(
                ["scip", "expt-convert", "--output", str(db_path), str(scip_path)],
                cwd=repo_path,
                step="scip expt-convert",
            )

            # Package graph lives in registry.db (a single RW database),
            # never in index.db — keeps the published SCIP index immutable.
            # Reads the just-built db_path directly, before it's even
            # copied to its published location, so a graph-population
            # failure is caught before anything is published.
            graph_store = GraphStore(config.data_dir(root) / "registry.db")
            index_conn = sqlite3.connect(db_path)
            try:
                populate_graph_for_repo(graph_store, slug, index_conn)
                has_nav = index_has_navigation_data(index_conn)
            finally:
                index_conn.close()
                graph_store.close()

            zoekt_dir = config.data_dir(root) / ".zoekt"
            zoekt_dir.mkdir(parents=True, exist_ok=True)
            _pin_zoekt_repo_name(repo_path, slug)
            tracked = _tracked_blob_count(repo_path)
            zoekt_result = _run(_zoekt_index_cmd(zoekt_dir, repo_path), cwd=repo_path,
                                step="zoekt-git-index")
            _warn_on_coverage_shortfall(slug, tracked, zoekt_result.stderr)
            _sweep_zoekt_tmp_orphans(slug, root)

            semantic_ok = _run_semantic_stage(repo_path, slug, root, semantic_include)

            target_dir = config.index_dir(slug, root)
            target_dir.mkdir(parents=True, exist_ok=True)
            versioned_name = f"index-{sha}.db"
            shutil.copy(db_path, target_dir / versioned_name)
            (target_dir / f"index-{sha}.metadata.json").write_text(
                json.dumps({"commit_sha": sha, "published_at": datetime.now(UTC).isoformat()}), encoding="utf-8"
            )
            _publish_atomically(target_dir, versioned_name, sha)
            # The pointer is live from here on; the remaining statements in
            # this try are registry bookkeeping, not indexing. `published`
            # keeps the degrade gate out of any failure they raise.
            published = True

        final_status = "indexed" if has_nav else PARTIAL_STATUS
        registry.upsert(slug, str(repo_path), language, sha, final_status, scheme_override=scheme,
                        semantic_include=semantic_include, language_override=language_override)
        registry.mark_tracked_files(slug, tracked)
        if semantic_ok:
            registry.mark_semantic_indexed(slug)
        if not has_nav:
            print(
                f"warning: {slug} published with symbols but no navigable positions "
                "(chunks/mentions empty) — per-file navigation will return no results. "
                "Check that the indexer emits occurrence ranges and that scip is >= v0.9.0.",
                file=sys.stderr,
            )
    except Exception as exc:
        text = str(exc)
        # One-line classified reason (D-03): the carrier's first non-empty
        # line is "{step} failed ({cmd})". The complete text is persisted
        # verbatim and unbounded (D-02) -- truncation is display-only.
        reason = next((line for line in text.splitlines() if line.strip()),
                      exc.__class__.__name__)
        # Degrade gate (FALL-01/FALL-04): post-build-start failures with the
        # opt-in fallback resolved on publish search-only instead of leaving
        # the repo with nothing. Missing binaries and bash-shim failures are
        # excluded -- environment misconfiguration must stay a loud hard
        # failure, never laundered into a published index. Pre-pipeline
        # failures never reach this handler at all (they raise inside the
        # wrap above). Runs whose pointer already flipped (`published`) are
        # excluded too: everything after `_publish_atomically` is registry
        # bookkeeping, and a failure there (locked registry.db from a
        # concurrent watch reindex, disk-full) must fall through to the
        # hard-failure record below -- the index IS live, so degrading would
        # rmtree a just-published good index and persist the sqlite error as
        # the indexer failure.
        if (not published
                and fallback_enabled
                and not isinstance(exc, MissingBinaryError)
                and not _bash_shim_failure(text)):
            try:
                semantic_ok, tracked = _publish_search_only(
                    repo_path, slug, root, semantic_include)
            except SearchPublishedButIncomplete as pexc:
                # Zoekt shards ARE live; what failed is retiring the prior
                # SCIP artifacts (ordered last inside _retire_scip_artifacts,
                # so the previous navigation index still stands). "Nothing
                # published" would be false. State what landed on stderr AND
                # in the row's status_stderr, then fall through to the
                # hard-failure record: a failed degraded publish stays a
                # hard failure (locked constraint) -- never a further
                # degrade, never an exit 0.
                retire_text = str(pexc)
                retire_reason = next(
                    (line for line in retire_text.splitlines() if line.strip()),
                    pexc.__class__.__name__)
                print(
                    f"warning: fallback publish did not complete for {slug} — "
                    f"search shards ARE published, but retiring the previous "
                    f"SCIP index failed ({retire_reason}); the previous "
                    "navigation index is untouched. Recording the original "
                    "failure.",
                    file=sys.stderr,
                )
                text = (
                    f"{text}\n— degraded publish did not complete —\n"
                    "search shards ARE published; retiring the previous SCIP "
                    f"index failed:\n{retire_text}\n"
                    "the previous navigation index is untouched"
                )
            except Exception:
                # The degraded publish failed at-or-before its zoekt step:
                # nothing was published, so the fallback promise is void --
                # fall through to the ordinary hard-failure record below.
                print(
                    f"warning: fallback publish failed for {slug} — nothing "
                    "published; recording the original failure.",
                    file=sys.stderr,
                )
            else:
                try:
                    registry.upsert(slug, str(repo_path), language, sha, DEGRADED_STATUS,
                                    scheme_override=scheme, semantic_include=semantic_include,
                                    language_override=language_override,
                                    status_origin=ORIGIN_FALLBACK, status_reason=reason,
                                    status_stderr=text)
                    registry.mark_tracked_files(slug, tracked)
                    if semantic_ok:
                        registry.mark_semantic_indexed(slug)
                except Exception as bkexc:
                    # Search-only IS on disk by this point (zoekt shards
                    # written, any retired SCIP index gone), so what failed
                    # is the status write, not the publish: claiming "nothing
                    # published" would be false, and exiting 0 would strand
                    # the row at 'indexing' while the run looks fine. Report
                    # what landed, record the bookkeeping failure -- the
                    # run's proximate cause -- and fail loudly. The
                    # search-only index survives either way.
                    bk_text = str(bkexc)
                    bk_reason = next((line for line in bk_text.splitlines() if line.strip()),
                                     bkexc.__class__.__name__)
                    print(
                        f"warning: {slug} degraded to search-only — {reason} "
                        "(search-only IS published), but recording the "
                        f"degraded status failed — {bk_reason}.",
                        file=sys.stderr,
                    )
                    _record_failure_best_effort(registry, slug, repo_path,
                                                language, bk_reason, bk_text)
                    raise IndexingError(str(bkexc)) from bkexc
                print(
                    f"warning: {slug} degraded to search-only — {reason}. "
                    "Navigation tools are unavailable; the next reindex retries "
                    "the full build.",
                    file=sys.stderr,
                )
                return slug
        _record_failure_best_effort(registry, slug, repo_path, language,
                                    reason, text)
        raise IndexingError(str(exc)) from exc
    finally:
        registry.close()

    return slug


def _cmd_index(args: argparse.Namespace) -> int:
    raw_include = getattr(args, "semantic_include", None)
    try:
        slug = index_repo(
            Path(args.path), slug=args.slug, scheme=getattr(args, "scheme", None),
            semantic_include=tuple(raw_include) if raw_include is not None else None,
            language=getattr(args, "language", None),
            search_only=getattr(args, "search_only", None),
            fallback_search_only=getattr(args, "fallback_search_only", None),
        )
    except (UnsupportedLanguageError, NotAGitRepositoryError, IndexingError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"indexed {slug}")
    # SEMA-01/SEMA-02: the semantic-extra install offer. Post-publish by
    # design — the index is already live before anything interactive can
    # delay or risk it (a consented install re-runs the stage below and
    # stamps the row in the same invocation). Four gates, cheapest and
    # most structural first: (1) offer_semantic — set only by the index
    # subparser, so reindex/watch/MCP can never reach this; (2) the
    # extra must actually be missing; (3) both streams must be TTYs;
    # (4) this repo must not have declined before.
    if (
        getattr(args, "offer_semantic", False)
        and _semantic_extra_missing()
        and _at_interactive_tty()
    ):
        registry = Registry(config.data_dir() / "registry.db")
        try:
            entry = registry.get(slug)
        finally:
            registry.close()
        if entry is None or not entry.semantic_declined:
            try:
                answer = input("Install semantic search support for this repo? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                # Locked: EOF/Ctrl-C at the prompt is a decline —
                # remembered, no traceback, index already complete.
                answer = ""
            if answer in ("y", "yes"):
                if _install_semantic_extra():
                    importlib.invalidate_caches()
                    include = tuple(entry.semantic_include) if entry is not None else ()
                    if _run_semantic_stage(Path(args.path), slug, None, include):
                        registry = Registry(config.data_dir() / "registry.db")
                        try:
                            registry.mark_semantic_indexed(slug)
                        finally:
                            registry.close()
                else:
                    print(
                        "warning: semantic extra install failed — index completed "
                        f"without semantic; tried: uv pip install --python {sys.executable} "
                        '"jarvis-mcp[semantic]"',
                        file=sys.stderr,
                    )
                    # Failure is not a refusal (locked): no decline bit,
                    # so the next TTY index offers again.
            else:
                registry = Registry(config.data_dir() / "registry.db")
                try:
                    registry.set_semantic_declined(slug, True)
                finally:
                    registry.close()
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    registry = Registry(config.data_dir() / "registry.db")
    try:
        for repo in registry.list():
            # D-08: the glyph prefixes the status field so the 5-column
            # TSV order stays parseable by scripts; failed and degraded
            # rows gain a 6th field carrying the reason one-liner —
            # degraded joins the ◐ family (search still answers) with the
            # failure cause riding that reason field. `partial` is a
            # success variant and stays in the ✓ family.
            if repo.status == "failed":
                marker = "✗"
            elif repo.status == SEARCH_ONLY_STATUS:
                marker = "◐"
            elif repo.status == DEGRADED_STATUS:
                marker = "◐"
            else:
                marker = "✓"
            line = (f"{repo.slug}\t{marker} {repo.status}\t{repo.language}"
                    f"\t{repo.commit_sha or '-'}\t{repo.path}")
            if repo.status in ("failed", DEGRADED_STATUS):
                line += f"\t{repo.status_reason or repo.status}"
            print(line)
    finally:
        registry.close()
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    try:
        slug = config.repo_slug(args.slug)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    registry = Registry(config.data_dir() / "registry.db")
    try:
        repo = registry.get(slug)
    finally:
        registry.close()
    if repo is None:
        print(f"error: no such repo: {slug}", file=sys.stderr)
        return 1
    print(f"slug: {repo.slug}\npath: {repo.path}\nlanguage: {repo.language}\nstatus: {repo.status}")
    print(f"commit: {repo.commit_sha or '-'}\nlast_indexed: {repo.last_indexed.isoformat()}")
    semantic = repo.semantic_indexed_at.isoformat() if repo.semantic_indexed_at else "-"
    print(f"semantic: {semantic}")
    recovery = recovery_for(repo)
    if repo.status_origin or repo.status_reason or recovery is not None:
        print(f"origin: {origin_of(repo)}")
        # Legacy failed rows predate status_reason; the status string is
        # all the cause they carry.
        print(f"cause: {repo.status_reason or repo.status}")
        if recovery is not None:
            print(f"recovery: {recovery}")
    if repo.status_stderr:
        # Display shows only the tail (resolution #4); the status_stderr
        # column itself is persisted unbounded (D-02) -- the full text is
        # one registry read away.
        print()
        for line in repo.status_stderr.splitlines()[-20:]:
            print(line)
        print("full log: persisted in the registry (status_stderr column)")
    return 0


def _cmd_reindex(args: argparse.Namespace) -> int:
    try:
        slug = config.repo_slug(args.slug)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    registry = Registry(config.data_dir() / "registry.db")
    try:
        repo = registry.get(slug)
    finally:
        registry.close()
    if repo is None:
        print(f"error: no such repo: {slug}", file=sys.stderr)
        return 1
    return _cmd_index(argparse.Namespace(
        path=repo.path, slug=repo.slug, scheme=repo.scheme_override,
        semantic_include=list(repo.semantic_include),
        language=repo.language_override,
    ))


def _pin_zoekt_repo_name(repo_path: Path, slug: str) -> None:
    """Pin the Zoekt repository name to `slug` via `git config zoekt.name`.

    `zoekt-git-index` has no `-meta` flag, so this replaces
    `_write_zoekt_meta`. Its name resolution order is: `zoekt.name` git
    config, else the `origin` remote URL url-escaped (e.g.
    `github.com%2Fowner%2Frepo`), else the directory basename. Every real repo
    has a remote, so without this `searchCode`'s `r:<slug>` filter matches
    nothing and the tool returns zero hits with no error — a silent wrong
    answer.

    `-shard_prefix_override` is NOT a substitute: it renames the shard file
    while leaving the indexed repository name untouched.

    Raises rather than warning: publishing an index whose name cannot be
    pinned produces exactly the silent failure this exists to prevent.
    """
    _run(["git", "-C", str(repo_path), "config", "zoekt.name", slug],
         cwd=repo_path, step="git config zoekt.name")


def _unpin_zoekt_repo_name(repo_path: Path) -> None:
    """Remove the `zoekt.name` pin, so `forget` leaves no footprint in the
    user's repo.

    Best-effort by design: `git config --unset` exits 5 when the key is
    absent (a repo indexed before pinning existed) and non-zero when the
    directory is gone (the user deleted the repo). Neither should fail a
    `forget` whose real work — dropping the registry row, index, and shards —
    has nothing to do with this key.
    """
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "--unset", "zoekt.name"],
        capture_output=True, text=True, check=False,
    )


def _remove_zoekt_shards(slug: str, root: Path | None = None) -> list[Path]:
    """Delete the Zoekt shards belonging to `slug`.

    Zoekt names each shard `<repo-name>_v<N>.<NNNNN>.zoekt`, and Task 4 makes
    `<repo-name>` the slug. The `_v` in the glob is deliberate: a bare
    `slug*` would let "api" also match "api-gateway"'s shard.

    Without this, `forget` left the shard in place and `searchCode` kept
    returning hits for a repo jarvis no longer knows about.
    """
    zoekt_dir = config.data_dir(root) / ".zoekt"
    if not zoekt_dir.is_dir():
        return []
    removed: list[Path] = []
    for shard in sorted(zoekt_dir.glob(f"{slug}_v*.zoekt")):
        shard.unlink(missing_ok=True)
        removed.append(shard)
    return removed


def _cmd_forget(args: argparse.Namespace) -> int:
    try:
        slug = config.repo_slug(args.slug)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    registry = Registry(config.data_dir() / "registry.db")
    try:
        entry = registry.get(slug)
        existed = registry.forget(slug)
    finally:
        registry.close()
    if not existed:
        print(f"error: no such repo: {slug}", file=sys.stderr)
        return 1
    if entry is not None:
        _unpin_zoekt_repo_name(Path(entry.path))
    index_dir = config.index_dir(slug)
    if index_dir.exists():
        shutil.rmtree(index_dir)
    _remove_zoekt_shards(slug)
    shutil.rmtree(config.lancedb_dir() / f"{slug}.lance", ignore_errors=True)
    # D-06: forgetting a repo removes everything jarvis stored for it. The
    # scip-swift cache legitimately may not exist (never-Swift repo, or the
    # binary never ran), hence ignore_errors like the lancedb sweep above.
    shutil.rmtree(config.swift_cache_dir(slug), ignore_errors=True)
    print(f"forgot {slug}")
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    """Watch `path` for source-file changes and debounce-reindex it.

    Not a daemon requirement — an optional foreground command a user runs
    while actively editing. `watchdog` is an optional dependency (`uv sync
    --extra watch`); its import is deferred so a base install never needs
    it just to run `jarvis index`/`list`/`status`."""
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        print(
            "error: `watchdog` is required for `jarvis watch` — install with `uv sync --extra watch`",
            file=sys.stderr,
        )
        return 1

    repo_path = Path(args.path).resolve()
    try:
        slug = config.repo_slug(args.slug or repo_path.name)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    def _reindex() -> None:
        print(f"[watch] change detected, reindexing {slug} ...")
        try:
            # FALL-05 anti-treadmill: a persistently-failing degraded repo
            # is not rebuilt on every debounced save at an unchanged sha —
            # only a source change (new sha) or an explicit `jarvis index`
            # (which never consults this) re-triggers the full build.
            if not _watch_skip_check(repo_path, slug):
                print(
                    f"[watch] {slug} still degraded at the same commit — "
                    "skipping full-build retry",
                    file=sys.stderr,
                )
                return
            index_repo(
                repo_path, slug=slug, scheme=args.scheme, language=args.language,
                fallback_search_only=getattr(args, "fallback_search_only", None),
            )
            print(f"[watch] {slug} reindexed")
        except Exception as exc:
            # Broad on purpose: index_repo() can raise before its own
            # try/except is even entered (e.g. `_git_head()`'s subprocess
            # call, or the first Registry() connection) — anything short
            # of catching Exception here would let a single transient
            # failure (a locked registry.db, a momentarily-corrupt .git)
            # kill the whole watch process instead of just skipping this
            # one reindex and continuing to watch.
            print(f"[watch] reindex failed: {exc}", file=sys.stderr)

    debouncer = Debouncer(delay_seconds=args.debounce, on_fire=_reindex)

    class _Handler(FileSystemEventHandler):
        def on_any_event(self, event) -> None:
            if event.is_directory or should_ignore_path(event.src_path):
                return
            debouncer.notify()

    observer = Observer()
    observer.schedule(_Handler(), str(repo_path), recursive=True)
    observer.start()
    print(f"[watch] watching {repo_path} (slug={slug}, debounce={args.debounce}s) — Ctrl+C to stop")
    try:
        while True:
            time.sleep(0.5)
            try:
                debouncer.poll()
            except Exception as exc:
                # Second line of defense: _reindex() already catches
                # broadly, but the watch loop itself must never die from
                # an unexpected error — that would silently stop watching
                # with no obvious signal beyond a scrollback line.
                print(f"[watch] unexpected error, still watching: {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jarvis")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="index a repo")
    index_parser.add_argument("path", help="path to the repo to index")
    index_parser.add_argument("--slug", help="override the auto-derived slug")
    index_parser.add_argument(
        "--scheme", help="Xcode scheme to build (Swift repos using xcodebuild with more than one scheme)"
    )
    index_parser.add_argument(
        "--semantic-include",
        action="append",
        metavar="PATH",
        help="force-include a path prefix the generated-file filter would skip "
             "(repeatable; persisted and reused by reindex/watch)",
    )
    index_parser.add_argument(
        "--language",
        choices=sorted(_INDEXER_BY_LANGUAGE),
        help="force the indexer language instead of detecting it from git-tracked files "
             "(persisted and reused by reindex/watch)",
    )
    index_parser.add_argument(
        "--search-only",
        action="store_true",
        default=None,
        help="skip SCIP indexing and publish only Zoekt + semantic search "
             "(persisted and reused by reindex/watch)",
    )
    index_parser.add_argument(
        "--fallback-search-only",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="on a post-build-start indexer failure, publish search-only and "
             "retry the full build on the next reindex (persisted per-repo; "
             "JARVIS_FALLBACK_SEARCH_ONLY sets the global default)",
    )
    index_parser.set_defaults(func=_cmd_index)
    # SEMA-02 structural gate: only `jarvis index` offers — reindex's
    # synthetic Namespace, watch's index_repo call, and MCP paths all
    # read False via getattr's default.
    index_parser.set_defaults(offer_semantic=True)

    list_parser = subparsers.add_parser("list", help="list indexed repos")
    list_parser.set_defaults(func=_cmd_list)

    status_parser = subparsers.add_parser("status", help="show a repo's index status")
    status_parser.add_argument("slug")
    status_parser.set_defaults(func=_cmd_status)

    reindex_parser = subparsers.add_parser("reindex", help="re-run indexing for a registered repo")
    reindex_parser.add_argument("slug")
    reindex_parser.set_defaults(func=_cmd_reindex)

    forget_parser = subparsers.add_parser("forget", help="remove a repo's registration and published index")
    forget_parser.add_argument("slug")
    forget_parser.set_defaults(func=_cmd_forget)

    watch_parser = subparsers.add_parser("watch", help="watch a repo and debounce-reindex on change")
    watch_parser.add_argument("path", help="path to the repo to watch")
    watch_parser.add_argument("--slug", help="override the auto-derived slug")
    watch_parser.add_argument(
        "--scheme", help="Xcode scheme to build (Swift repos using xcodebuild with more than one scheme)"
    )
    watch_parser.add_argument("--debounce", type=float, default=5.0, help="quiet-period seconds (default: 5.0)")
    watch_parser.add_argument(
        "--language",
        choices=sorted(_INDEXER_BY_LANGUAGE),
        help="force the indexer language instead of detecting it from git-tracked files "
             "(persisted and reused by reindex/watch)",
    )
    watch_parser.add_argument(
        "--fallback-search-only",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="on a post-build-start indexer failure, publish search-only and "
             "retry the full build on the next reindex (persisted per-repo; "
             "JARVIS_FALLBACK_SEARCH_ONLY sets the global default)",
    )
    watch_parser.set_defaults(func=_cmd_watch)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
