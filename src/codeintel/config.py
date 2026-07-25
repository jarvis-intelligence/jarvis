"""Data-dir and repo-slug resolution for codeintel's single-tenant layout.

`index_reader.IndexConnectionCache` keys entries on the vendored source's 3-tuple
(project, repo, branch) — vendored unchanged (Phase 1). codeintel has no
project/branch dimension (single user, one index per repo), so those two
are pinned constants here and every repo maps to a bare slug. On disk this
resolves to ``{data_dir}/scip/_/{slug}/_/`` — the two ``_`` segments are an
artifact of reusing the vendored cache's path shape unchanged rather than a
user-facing contract.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from codeintel.index_reader import IndexConnectionCache

DEFAULT_DATA_DIR = Path.home() / ".codeintel"

PROJECT = "_"
BRANCH = "_"

_SLUG_UNSAFE = re.compile(r"[^a-z0-9._-]+")


def data_dir(override: Path | None = None) -> Path:
    if override is not None:
        return override
    raw = os.environ.get("CODEINTEL_DATA_DIR")
    return Path(raw).expanduser() if raw else DEFAULT_DATA_DIR


def repo_slug(name: str) -> str:
    """Normalize a user-chosen repo name into a directory-safe slug.

    Rejects `.`/`..` explicitly (not just `/`) — both survive the
    character-class substitution below unchanged since `.` is an allowed
    slug character, but either one alone is a path-traversal component
    once joined under `index_dir()`."""
    slug = _SLUG_UNSAFE.sub("-", name.strip().lower()).strip("-")
    if not slug or slug in (".", ".."):
        raise ValueError(f"repo name {name!r} does not produce a usable, safe slug")
    return slug


def index_dir(slug: str, root: Path | None = None) -> Path:
    """The directory holding a repo's ``current`` pointer + versioned db files."""
    return (root or data_dir()) / "scip" / PROJECT / slug / BRANCH


def new_connection_cache(root: Path | None = None) -> IndexConnectionCache:
    return IndexConnectionCache(str(root or data_dir()))


def get_connection(cache: IndexConnectionCache, slug: str):
    return cache.get_connection(PROJECT, slug, BRANCH)
