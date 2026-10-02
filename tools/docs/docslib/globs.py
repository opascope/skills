"""The one glob matcher (manage-docs v2 section 6; T7).

Gitignore-style, anchored at the repo root: `**` is zero or more directories, `*` and `?` never
cross `/`, a pattern ending in `/` matches everything under that directory. It is the engine's
matcher (`migrate.glob_match`), so the engine and every docs check agree. stdlib fnmatch is never
used: it gets `**` wrong (`fnmatch("README.md", "**/*.md")` is False).
"""
from __future__ import annotations

from typing import Iterable, Optional

from docslib._engine import engine


def match(pattern: str, path: str) -> bool:
    return engine().glob_match(pattern, path)


def first(patterns: Iterable[str], path: str) -> Optional[str]:
    for pat in patterns:
        if match(pat, path):
            return pat
    return None


def any_match(patterns: Iterable[str], path: str) -> bool:
    return first(patterns, path) is not None
