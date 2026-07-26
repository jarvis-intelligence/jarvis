"""`codeintel` CLI: detect language -> run the matching SCIP indexer ->
`scip expt-convert` -> populate package graph -> zoekt-index -> atomic
pointer swap -> registry update.

Subcommands: index, list, status, reindex, forget, watch.
"""

from __future__ import annotations

import argparse
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

from codeintel import config
from codeintel.graph import GraphStore, populate_graph_for_repo
from codeintel.registry import Registry
from codeintel.watch import Debouncer, should_ignore_path

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

_IGNORED_DIRS = {".git", "node_modules", ".venv", "__pycache__", "dist", "build", "DerivedData", ".build"}

# `scip expt-convert` below this version cannot read scip.proto's `typed_range`
# oneof (its Go bindings predate occurrence_range.go), so it silently writes a
# schema-valid database with zero chunks and zero mentions -- every navigation
# query then returns empty. Verified: the same .scip file yields chunks=0/
# mentions=0 under v0.7.0 and chunks=1/mentions=14 under v0.9.0.
MIN_SCIP_VERSION = (0, 9, 0)


class UnsupportedLanguageError(Exception):
    """Raised when no supported source extension is found under a repo."""


class IndexingError(Exception):
    """Raised when an indexing pipeline step (indexer/convert/zoekt) fails."""


def detect_language(repo_path: Path) -> tuple[str, list[str]]:
    """Scan `repo_path` for supported source extensions; return
    `(language, indexer_command)` for whichever extension has the most
    files, ties broken by `_EXT_PRIORITY` order."""
    counts: Counter[str] = Counter()
    for path in repo_path.rglob("*"):
        if any(part in _IGNORED_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix in _LANGUAGE_INDEXERS:
            counts[path.suffix] += 1

    present = [ext for ext in _EXT_PRIORITY if counts[ext] > 0]
    if not present:
        raise UnsupportedLanguageError(
            f"no supported source files (.ts/.tsx/.py/.java/.kt/.swift) found under {repo_path}"
        )
    best_ext = max(present, key=lambda ext: (counts[ext], -_EXT_PRIORITY.index(ext)))
    return _LANGUAGE_INDEXERS[best_ext]


def _git_head(repo_path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _run(cmd: list[str], *, cwd: Path, step: str) -> None:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise IndexingError(f"{step} failed ({' '.join(cmd)}):\n{result.stdout}\n{result.stderr}")


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


def index_repo(repo_path: Path, *, slug: str | None = None, root: Path | None = None) -> str:
    """Runs the full pipeline for one repo; returns the slug it was
    published under. Registry status is `indexing` while running, `indexed`
    on success, `failed` (with the exception's message) on any step's
    failure.

    `zoekt-index` runs BEFORE the pointer swap: if it fails, no repo was
    ever left half-published — the previous version (if any) is still the
    live `current` pointer, matching the `failed` status. Only once both
    the SCIP index and the Zoekt shard are ready does `_publish_atomically`
    flip the pointer (old files are only deleted after the new pointer is
    live)."""
    repo_path = repo_path.resolve()
    slug = config.repo_slug(slug or repo_path.name)
    language, indexer_cmd = detect_language(repo_path)
    sha = _git_head(repo_path)
    check_scip_version()

    registry = Registry(config.data_dir(root) / "registry.db")
    registry.upsert(slug, str(repo_path), language, None, "indexing")

    try:
        with tempfile.TemporaryDirectory(prefix="codeintel-index-") as scratch:
            scip_path = Path(scratch) / "index.scip"
            db_path = Path(scratch) / "index.db"

            _run([*indexer_cmd, "--output", str(scip_path)], cwd=repo_path, step=f"{indexer_cmd[0]} index")
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
            finally:
                index_conn.close()
                graph_store.close()

            zoekt_dir = config.data_dir(root) / ".zoekt"
            zoekt_dir.mkdir(parents=True, exist_ok=True)
            _run(["zoekt-index", "-index", str(zoekt_dir), str(repo_path)], cwd=repo_path, step="zoekt-index")

            target_dir = config.index_dir(slug, root)
            target_dir.mkdir(parents=True, exist_ok=True)
            versioned_name = f"index-{sha}.db"
            shutil.copy(db_path, target_dir / versioned_name)
            (target_dir / f"index-{sha}.metadata.json").write_text(
                json.dumps({"commit_sha": sha, "published_at": datetime.now(UTC).isoformat()}), encoding="utf-8"
            )
            _publish_atomically(target_dir, versioned_name, sha)

        registry.upsert(slug, str(repo_path), language, sha, "indexed")
    except Exception as exc:
        registry.mark_status(slug, "failed")
        raise IndexingError(str(exc)) from exc
    finally:
        registry.close()

    return slug


def _cmd_index(args: argparse.Namespace) -> int:
    try:
        slug = index_repo(Path(args.path), slug=args.slug)
    except (UnsupportedLanguageError, IndexingError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"indexed {slug}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    registry = Registry(config.data_dir() / "registry.db")
    try:
        for repo in registry.list():
            print(f"{repo.slug}\t{repo.status}\t{repo.language}\t{repo.commit_sha or '-'}\t{repo.path}")
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
    return _cmd_index(argparse.Namespace(path=repo.path, slug=repo.slug))


def _cmd_forget(args: argparse.Namespace) -> int:
    try:
        slug = config.repo_slug(args.slug)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    registry = Registry(config.data_dir() / "registry.db")
    try:
        existed = registry.forget(slug)
    finally:
        registry.close()
    if not existed:
        print(f"error: no such repo: {slug}", file=sys.stderr)
        return 1
    index_dir = config.index_dir(slug)
    if index_dir.exists():
        shutil.rmtree(index_dir)
    print(f"forgot {slug}")
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    """Watch `path` for source-file changes and debounce-reindex it.

    Not a daemon requirement — an optional foreground command a user runs
    while actively editing. `watchdog` is an optional dependency (`uv sync
    --extra watch`); its import is deferred so a base install never needs
    it just to run `codeintel index`/`list`/`status`."""
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        print(
            "error: `watchdog` is required for `codeintel watch` — install with `uv sync --extra watch`",
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
            index_repo(repo_path, slug=slug)
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
    parser = argparse.ArgumentParser(prog="codeintel")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="index a repo")
    index_parser.add_argument("path", help="path to the repo to index")
    index_parser.add_argument("--slug", help="override the auto-derived slug")
    index_parser.set_defaults(func=_cmd_index)

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
    watch_parser.add_argument("--debounce", type=float, default=5.0, help="quiet-period seconds (default: 5.0)")
    watch_parser.set_defaults(func=_cmd_watch)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
