"""`codeintel` CLI: detect language -> run the matching SCIP indexer ->
`scip expt-convert` -> zoekt-index -> atomic pointer swap -> registry update.

Subcommands: index, list, status, reindex, forget.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from codeintel import config
from codeintel.registry import Registry

_LANGUAGE_INDEXERS: dict[str, tuple[str, list[str]]] = {
    ".ts": ("typescript", ["scip-typescript", "index"]),
    ".tsx": ("typescript", ["scip-typescript", "index"]),
    ".py": ("python", ["scip-python", "index"]),
    ".java": ("java", ["scip-java", "index"]),
    ".kt": ("java", ["scip-java", "index"]),
}
# Priority order for tie-breaking when extension counts are equal.
_EXT_PRIORITY = [".ts", ".tsx", ".py", ".java", ".kt"]

_IGNORED_DIRS = {".git", "node_modules", ".venv", "__pycache__", "dist", "build"}


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
        raise UnsupportedLanguageError(f"no supported source files (.ts/.tsx/.py/.java/.kt) found under {repo_path}")
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

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
