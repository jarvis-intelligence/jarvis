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
from typing import TYPE_CHECKING

from codeintel import config
from codeintel.graph import GraphStore, populate_graph_for_repo
from codeintel.registry import Registry
from codeintel.watch import Debouncer, should_ignore_path

if TYPE_CHECKING:
    from codeintel.semantic import SemanticIndexReport

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

_IGNORED_DIRS = config.IGNORED_DIRS

# `scip expt-convert` below this version cannot read scip.proto's `typed_range`
# oneof (its Go bindings predate occurrence_range.go), so it silently writes a
# schema-valid database with zero chunks and zero mentions -- every navigation
# query then returns empty. Verified: the same .scip file yields chunks=0/
# mentions=0 under v0.7.0 and chunks=1/mentions=14 under v0.9.0.
MIN_SCIP_VERSION = (0, 9, 0)

# Registry status for an index that published real symbols but no navigable
# positions -- the fingerprint of a converter or indexer that dropped every
# occurrence range. Publishing still proceeds (the symbol table is useful),
# but the status must not claim unqualified success.
PARTIAL_STATUS = "partial"


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


def _prefers_xcodebuild(repo_path: Path) -> bool:
    """True when `repo_path` has a checked-in `.xcodeproj`/`.xcworkspace`
    alongside `Package.swift`. `scip-swift`'s own `BuildBackendDetector`
    picks `swiftpm` whenever `Package.swift` exists, even when that can't
    build — e.g. a UIKit-only iOS package with no macOS platform support,
    where plain `swift build` fails with "no such module 'UIKit'" on the
    macOS host destination it defaults to."""
    return any(repo_path.glob("*.xcodeproj")) or any(repo_path.glob("*.xcworkspace"))


def _swift_indexer_cmd(base_cmd: list[str], repo_path: Path, scheme: str | None) -> list[str]:
    """Extend `base_cmd` (`["scip-swift"]`) with `--build-tool xcodebuild`
    (and `--scheme`, if given) when `repo_path` has a checked-in Xcode
    project — see `_prefers_xcodebuild`. Non-Swift callers never reach
    this function; Swift repos without a checked-in Xcode project get
    `base_cmd` back unchanged, identical to today's behavior."""
    if not _prefers_xcodebuild(repo_path):
        return base_cmd
    cmd = [*base_cmd, "--build-tool", "xcodebuild"]
    if scheme:
        cmd += ["--scheme", scheme]
    return cmd


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


def _write_zoekt_meta(scratch: Path, slug: str) -> Path:
    """Write the `.meta` file that names the Zoekt shard after `slug`.

    Without this, zoekt-index derives the repository name from the indexed
    directory's basename. `searchCode(repo=<slug>)` builds a Zoekt `r:<slug>`
    filter, so a slug that differs from the directory name matches nothing
    and the tool returns zero hits with no error -- a silent wrong answer.
    """
    meta_path = scratch / "zoekt.meta.json"
    meta_path.write_text(json.dumps({"Name": slug}), encoding="utf-8")
    return meta_path


def _resolve_scheme(registry: Registry, slug: str, scheme: str | None) -> str | None:
    """`scheme=None` means "leave the persisted override alone" (e.g. a
    `codeintel watch` reindex, which never repeats `--scheme`) rather than
    "clear it" -- looks up the existing registry row and falls back to its
    `scheme_override` when the caller passed nothing explicit."""
    if scheme is not None:
        return scheme
    existing = registry.get(slug)
    return existing.scheme_override if existing is not None else None


def _resolve_semantic_include(
    registry: Registry, slug: str, include: tuple[str, ...] | None
) -> tuple[str, ...]:
    """`include=None` means "leave the persisted value alone" (a `codeintel
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
    if report.truncated is None:
        print("semantic: could not measure truncation", file=sys.stderr)
    elif report.truncated > 0:
        print(
            f"warning: {report.truncated} chunks exceeded the model's token limit "
            "and were truncated",
            file=sys.stderr,
        )


def _run_semantic_stage(repo_path: Path, slug: str, root: Path | None,
                        include_prefixes: tuple[str, ...] = ()) -> bool:
    """Chunk + embed + write the LanceDB table. Optional and non-fatal:
    a missing `semantic` extra skips with a hint, any other failure warns
    and lets the SCIP/Zoekt publish proceed — the previous semantic table
    (if any) stays live."""
    try:
        from codeintel import semantic
        from codeintel.embeddings import SemanticExtraMissingError
    except ImportError:
        print(
            "semantic indexing skipped — install with `uv sync --extra semantic`",
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


def index_repo(
    repo_path: Path, *, slug: str | None = None, root: Path | None = None,
    scheme: str | None = None, semantic_include: tuple[str, ...] | None = None,
) -> str:
    """Runs the full pipeline for one repo; returns the slug it was
    published under. Registry status is `indexing` while running, `indexed`
    on success, `failed` (with the exception's message) on any step's
    failure, or `PARTIAL_STATUS` ("partial") on success when
    `index_has_navigation_data()` finds symbols published but `chunks` and
    `mentions` both empty.

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
    scheme = _resolve_scheme(registry, slug, scheme)
    semantic_include = _resolve_semantic_include(registry, slug, semantic_include)

    if language == "swift":
        indexer_cmd = _swift_indexer_cmd(indexer_cmd, repo_path, scheme)

    registry.upsert(slug, str(repo_path), language, None, "indexing", scheme_override=scheme,
                    semantic_include=semantic_include)

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
                has_nav = index_has_navigation_data(index_conn)
            finally:
                index_conn.close()
                graph_store.close()

            zoekt_dir = config.data_dir(root) / ".zoekt"
            zoekt_dir.mkdir(parents=True, exist_ok=True)
            meta_path = _write_zoekt_meta(Path(scratch), slug)
            _run(
                ["zoekt-index", "-index", str(zoekt_dir), "-meta", str(meta_path), str(repo_path)],
                cwd=repo_path,
                step="zoekt-index",
            )

            semantic_ok = _run_semantic_stage(repo_path, slug, root, semantic_include)

            target_dir = config.index_dir(slug, root)
            target_dir.mkdir(parents=True, exist_ok=True)
            versioned_name = f"index-{sha}.db"
            shutil.copy(db_path, target_dir / versioned_name)
            (target_dir / f"index-{sha}.metadata.json").write_text(
                json.dumps({"commit_sha": sha, "published_at": datetime.now(UTC).isoformat()}), encoding="utf-8"
            )
            _publish_atomically(target_dir, versioned_name, sha)

        final_status = "indexed" if has_nav else PARTIAL_STATUS
        registry.upsert(slug, str(repo_path), language, sha, final_status, scheme_override=scheme,
                        semantic_include=semantic_include)
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
        registry.mark_status(slug, "failed")
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
        )
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
    semantic = repo.semantic_indexed_at.isoformat() if repo.semantic_indexed_at else "-"
    print(f"semantic: {semantic}")
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
    ))


def _remove_zoekt_shards(slug: str, root: Path | None = None) -> list[Path]:
    """Delete the Zoekt shards belonging to `slug`.

    Zoekt names each shard `<repo-name>_v<N>.<NNNNN>.zoekt`, and Task 4 makes
    `<repo-name>` the slug. The `_v` in the glob is deliberate: a bare
    `slug*` would let "api" also match "api-gateway"'s shard.

    Without this, `forget` left the shard in place and `searchCode` kept
    returning hits for a repo codeintel no longer knows about.
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
        existed = registry.forget(slug)
    finally:
        registry.close()
    if not existed:
        print(f"error: no such repo: {slug}", file=sys.stderr)
        return 1
    index_dir = config.index_dir(slug)
    if index_dir.exists():
        shutil.rmtree(index_dir)
    _remove_zoekt_shards(slug)
    shutil.rmtree(config.lancedb_dir() / f"{slug}.lance", ignore_errors=True)
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
            index_repo(repo_path, slug=slug, scheme=args.scheme)
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
    watch_parser.add_argument(
        "--scheme", help="Xcode scheme to build (Swift repos using xcodebuild with more than one scheme)"
    )
    watch_parser.add_argument("--debounce", type=float, default=5.0, help="quiet-period seconds (default: 5.0)")
    watch_parser.set_defaults(func=_cmd_watch)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
