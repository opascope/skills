#!/usr/bin/env python3
"""The "what users get" check: every change carries one plain changelog line.

Part of the Opascope release standard (B-003, Max ruling R-2026-10-06-09/-21).
Shaped like the manage-docs frontmatter check: a cheap, deterministic gate that
runs on a pull request and either passes or fails with a one-line fix.

The rule: a PR must add at least one bullet under `## [Unreleased]` in
CHANGELOG.md, written in plain product language a user could understand. A change
that genuinely shows nothing to a user (a pure refactor, a test-only change)
takes the escape: the words "no user-visible change" or "changelog: none" in the
PR body or any commit message in the range.

Comparison is by bullet SET between the base and the head CHANGELOG, so a reorder
is not mistaken for a new entry, and the bootstrap case (no CHANGELOG on the base
yet) is handled: any `[Unreleased]` bullet on the head passes, and a repo with no
CHANGELOG on either side is skipped.

Exit codes: 0 pass (or skip), 1 fail. `--annotate` emits a GitHub Actions error
line, the same shape manage-docs uses.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

CHANGELOG = "CHANGELOG.md"
ESCAPES = ("no user-visible change", "changelog: none")
_HEADER = re.compile(r"^##\s+\[", re.M)


# --------------------------------------------------------------------------- #
# Pure logic (unit-tested)
# --------------------------------------------------------------------------- #
def unreleased_section(text: str) -> str:
    """The body under `## [Unreleased]`, up to the next `## [` header.

    Empty string when there is no Unreleased header (including an empty file)."""
    if not text:
        return ""
    m = re.search(r"^##\s+\[Unreleased\]\s*$", text, re.M | re.I)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = _HEADER.search(rest)
    return rest[: nxt.start()] if nxt else rest


def bullets(section: str) -> set[str]:
    """The set of bullet lines in a section (lines whose first non-space is `-`).

    `### Added`/`### Fixed` subsection headers and blank lines are ignored, so
    the comparison is over the actual user-facing lines."""
    out: set[str] = set()
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") and len(stripped) > 2:
            out.add(stripped)
    return out


def has_new_entry(base_text: str, head_text: str) -> bool:
    """True when the head adds an Unreleased bullet the base did not have."""
    return bool(bullets(unreleased_section(head_text))
                - bullets(unreleased_section(base_text)))


def exemption(texts: list[str]) -> str | None:
    """The first escape phrase found across the given texts, or None."""
    blob = "\n".join(t for t in texts if t).lower()
    for phrase in ESCAPES:
        if phrase in blob:
            return phrase
    return None


# --------------------------------------------------------------------------- #
# Edges (git reads)
# --------------------------------------------------------------------------- #
def _git_show(root: Path, ref_path: str) -> str | None:
    """`git show ref:path`, or None when the path is absent at that ref."""
    proc = subprocess.run(["git", "-C", str(root), "show", ref_path],
                          capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def _commit_messages(root: Path, base: str, head: str) -> str:
    rng = f"{base}..{head}" if base else head
    proc = subprocess.run(["git", "-C", str(root), "log", "--format=%B", rng],
                          capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["check"])
    ap.add_argument("--base", required=True, help="base ref or sha")
    ap.add_argument("--head", default="HEAD", help="head ref (default HEAD)")
    ap.add_argument("--repo", default=".", help="repo root (default .)")
    ap.add_argument("--body-file", help="PR body file, scanned for the escape")
    ap.add_argument("--annotate", action="store_true",
                    help="emit a GitHub Actions error annotation on failure")
    args = ap.parse_args(argv)
    root = Path(args.repo).resolve()

    base_text = _git_show(root, f"{args.base}:{CHANGELOG}")
    head_text = _git_show(root, f"{args.head}:{CHANGELOG}")
    if head_text is None:
        # Fall back to the working tree (uncommitted changelog edit).
        wt = root / CHANGELOG
        head_text = wt.read_text(encoding="utf-8") if wt.exists() else None

    if head_text is None and base_text is None:
        print("changelog-guard: no CHANGELOG.md on either side; skipping.")
        return 0

    if has_new_entry(base_text or "", head_text or ""):
        print("changelog-guard: OK, a new [Unreleased] line is present.")
        return 0

    body = ""
    if args.body_file and Path(args.body_file).exists():
        body = Path(args.body_file).read_text(encoding="utf-8")
    commits = _commit_messages(root, args.base, args.head)
    hit = exemption([body, commits])
    if hit:
        print(f"changelog-guard: OK, change declared no user-visible change "
              f"(matched \"{hit}\").")
        return 0

    msg = ('add one plain "what users get" line under "## [Unreleased]" in '
           'CHANGELOG.md, or declare "no user-visible change" in the PR body '
           'or a commit message.')
    if args.annotate:
        print(f"::error file={CHANGELOG}::changelog-guard: {msg}")
    print(f"changelog-guard: FAIL, {msg}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
