#!/usr/bin/env python3
"""The "what users get" check: every change carries one plain changelog line.

Part of the Opascope release standard (B-003, Max ruling R-2026-10-06-09/-21).
Shaped like the manage-docs frontmatter check: a cheap, deterministic gate that
runs on a pull request and either passes or fails with a one-line fix.

Two modes, picked by the repo, not by the PR:

Fragment mode (the repo has a `changelog.d/` directory; Max ruling
R-2026-10-11-08). A PR adds exactly ONE fragment file under `changelog.d/`
instead of editing CHANGELOG.md, so two PRs never touch the same lines and the
CHANGELOG.md merge conflict class is gone. The release step folds the fragments
into CHANGELOG.md. A fragment is a Keep a Changelog section header and one
bullet:

    ### Fixed

    - Plain product language, one or two sentences, continuation lines
      indented two spaces.

The guard refuses a PR that adds two fragments, edits or removes another PR's
fragment, or edits the `## [Unreleased]` section directly. A release cut (a PR
that adds a new `## [x.y.z]` section to CHANGELOG.md) is exempt, because that is
the step that folds the fragments in, but it must remove every fragment it
folded, publish each removed fragment's line in the new section, add no
fragment of its own, and keep `changelog.d/README.md`; it is judged against the
current base tip, so a fragment that landed after the cut branched must be
folded too. For the cutover, a PR opened before `LEGACY_CUTOVER` may still add
its own bullet straight to `[Unreleased]` until `LEGACY_UNTIL`, so in-flight PRs
land without a rewrite; it must keep every existing line of that section
(headers and wrapped lines included) unchanged and in order, and every existing
entry whole under its own section header.

Legacy mode (no `changelog.d/`): a PR must add at least one bullet under
`## [Unreleased]` in CHANGELOG.md. Comparison is by bullet SET, so a reorder is
not mistaken for a new entry; a repo with no CHANGELOG on either side is skipped.

In both modes a change that genuinely shows nothing to a user (a pure refactor,
a test-only change) takes the escape: the words "no user-visible change" or
"changelog: none" in the PR body or any commit message in the range.

In fragment mode the base for every comparison is the merge base of `--base`
and `--head`, so lines and fragments that `working` gained after the PR branched
never count as the PR's own. Legacy mode compares against `--base` as it always
has.

Exit codes: 0 pass (or skip), 1 fail. `--annotate` emits a GitHub Actions error
line, the same shape manage-docs uses.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

CHANGELOG = "CHANGELOG.md"
FRAGMENT_DIR = "changelog.d"
ESCAPES = ("no user-visible change", "changelog: none")
SECTIONS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")

# The fragment cutover (coordinated with UPL · releases). A PR opened before
# LEGACY_CUTOVER may still add its line straight to [Unreleased] until
# LEGACY_UNTIL; after that every PR uses a fragment.
LEGACY_CUTOVER = datetime(2026, 10, 12, 0, 0, tzinfo=timezone.utc)
LEGACY_UNTIL = datetime(2026, 10, 19, 0, 0, tzinfo=timezone.utc)

_HEADER = re.compile(r"^##\s+\[", re.M)
_VERSION_HEADER = re.compile(r"^##\s+\[(\d+\.\d+\.\d+[^\]]*)\]", re.M)
_FRAGMENT_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*\.md$")


class FragmentError(ValueError):
    """A fragment file that does not follow the one-section, one-bullet shape."""


@dataclass(frozen=True)
class Fragment:
    section: str
    line: str


@dataclass(frozen=True)
class Change:
    """One `git diff --name-status` row: a status letter and a path."""

    status: str
    path: str


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


def unreleased_edited(base_text: str, head_text: str) -> bool:
    """True when the head's Unreleased section differs from the base's.

    Compares the section text with blank lines dropped, so a pure whitespace
    reflow of blank lines is not an edit but a reworded, added or removed line
    is."""
    def norm(text: str) -> list[str]:
        return [ln.rstrip() for ln in unreleased_section(text).splitlines()
                if ln.strip()]
    return norm(base_text) != norm(head_text)


def versions(text: str) -> set[str]:
    """Every numbered `## [x.y.z]` section header in a CHANGELOG."""
    return set(_VERSION_HEADER.findall(text or ""))


def is_release_cut(base_text: str, head_text: str) -> bool:
    """A release cut adds a numbered version section the base did not have."""
    return bool(versions(head_text) - versions(base_text))


def _squash(text: str) -> str:
    """Whitespace runs collapsed to one space, so a rewrapped line compares equal."""
    return " ".join(text.split())


def released_lines(base_text: str, head_text: str) -> set[str]:
    """Every bullet in the version sections the head adds, continuation lines
    joined and whitespace squashed: what a release cut actually published."""
    new = versions(head_text) - versions(base_text)
    out: set[str] = set()
    current: list[str] | None = None
    inside = False
    for line in (head_text or "").splitlines():
        if _HEADER.match(line):
            if current:
                out.add(_squash(" ".join(current)))
            current = None
            vm = _VERSION_HEADER.match(line)
            inside = bool(vm and vm.group(1) in new)
            continue
        if not inside:
            continue
        if line.startswith("- "):
            if current:
                out.add(_squash(" ".join(current)))
            current = [line[2:]]
        elif current is not None and line.startswith("  ") and line.strip():
            current.append(line.strip())
        else:
            if current:
                out.add(_squash(" ".join(current)))
            current = None
    if current:
        out.add(_squash(" ".join(current)))
    return out


def entries(text: str) -> list[tuple[str, tuple[str, ...]]]:
    """The Unreleased section as (section header, entry) pairs, where an entry is
    a bullet with its continuation lines, or any other non-blank line on its own."""
    out: list[tuple[str, tuple[str, ...]]] = []
    section = ""
    block: list[str] = []

    def flush() -> None:
        if block:
            out.append((section, tuple(block)))
            block.clear()

    for raw in unreleased_section(text).splitlines():
        line = raw.rstrip()
        if line.startswith("  ") and line.strip() and block:
            block.append(line)
            continue
        flush()
        if not line.strip():
            continue
        if line.startswith("###"):
            section = line
            continue
        block.append(line)
        if not line.startswith("- "):
            flush()
    flush()
    return out


def only_adds(base_text: str, head_text: str) -> bool:
    """True when the head's Unreleased section keeps every non-blank line of the
    base's, in order and unchanged, keeps every existing entry whole under its
    own section header, and adds at least one new bullet."""
    def norm(text: str) -> list[str]:
        return [ln.rstrip() for ln in unreleased_section(text).splitlines()
                if ln.strip()]
    remaining = iter(norm(head_text))
    kept = all(line in remaining for line in norm(base_text))
    head_entries = entries(head_text)
    whole = all(entry in head_entries for entry in entries(base_text))
    return kept and whole and has_new_entry(base_text, head_text)


def check_legacy_cut(base_text: str, head_text: str) -> tuple[bool, str]:
    """The legacy-mode verdict for a release cut: (passed, message).

    A cut moves the base's `[Unreleased]` bullets into a new numbered section
    and leaves `[Unreleased]` empty, so it adds no new bullet. The release skill
    has the cut reword each line, so lines are counted, not matched: it passes
    when `[Unreleased]` is left empty and the new section holds at least as many
    bullets as the base had under `[Unreleased]`. A leftover bullet or a dropped
    line (fewer bullets than the base) fails."""
    leftover = sorted(bullets(unreleased_section(head_text)))
    if leftover:
        return False, ("a release cut leaves '## [Unreleased]' empty; still "
                       f"there: {'; '.join(leftover)}. Fold them into the new "
                       "section (rerun release.py cut) or land them in their "
                       "own PR.")
    wanted = sum(1 for _section, block in entries(base_text)
                 if block[0].startswith("- "))
    published = len(released_lines(base_text, head_text))
    if published < wanted:
        return False, (f"the new release section has {published} bullet(s) but "
                       f"the base had {wanted} under '## [Unreleased]'; a cut "
                       "publishes every line it removes (reword them, do not "
                       "drop or merge them).")
    return True, "OK, release cut (a new numbered section holds the Unreleased lines)."


def exemption(texts: list[str]) -> str | None:
    """The first escape phrase found across the given texts, or None."""
    blob = "\n".join(t for t in texts if t).lower()
    for phrase in ESCAPES:
        if phrase in blob:
            return phrase
    return None


def is_fragment_path(path: str) -> bool:
    """A path the guard treats as a fragment: `changelog.d/<name>.md`, not the
    README and not nested."""
    parts = path.split("/")
    return (len(parts) == 2 and parts[0] == FRAGMENT_DIR
            and parts[1].endswith(".md") and parts[1].lower() != "readme.md")


def parse_fragment(text: str) -> Fragment:
    """Parse one fragment: a `### <Section>` header, then exactly one bullet.

    The bullet may wrap onto continuation lines indented by two spaces. Blank
    lines are allowed around the header and the bullet, never inside it."""
    lines = text.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i == len(lines):
        raise FragmentError("the fragment is empty")
    header = re.fullmatch(r"###\s+(\S.*?)\s*", lines[i])
    if not header:
        raise FragmentError(
            f"the first line must be a section header such as '### Fixed', "
            f"got {lines[i].strip()!r}")
    section = header.group(1)
    if section not in SECTIONS:
        raise FragmentError(
            f"unknown section {section!r}; use one of {', '.join(SECTIONS)}")
    i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i == len(lines) or not lines[i].startswith("- ") or len(lines[i].strip()) < 3:
        raise FragmentError("the section header must be followed by one '- ' bullet")
    parts = [lines[i][2:].strip()]
    i += 1
    while i < len(lines) and lines[i].startswith("  ") and lines[i].strip():
        parts.append(lines[i].strip())
        i += 1
    rest = [ln for ln in lines[i:] if ln.strip()]
    if rest:
        if rest[0].lstrip().startswith("- "):
            raise FragmentError("a fragment holds exactly one bullet; split the "
                                "change or merge the lines")
        raise FragmentError(f"unexpected text after the bullet: {rest[0].strip()!r}")
    return Fragment(section=section, line=" ".join(parts))


def legacy_allowed(pr_created_at: datetime | None, now: datetime) -> bool:
    """A direct [Unreleased] line is still accepted for a PR opened before the
    cutover, until the window closes. With no PR time (a local run) the PR is
    treated as opened now."""
    opened = pr_created_at or now
    return opened < LEGACY_CUTOVER and now < LEGACY_UNTIL


def check_fragments(
    changes: list[Change],
    read_head: dict[str, str],
    base_changelog: str,
    head_changelog: str,
    escape: str | None,
    pr_created_at: datetime | None,
    now: datetime,
    base_fragments: tuple[str, ...] = (),
    read_base: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """The fragment-mode verdict: (passed, message).

    `changes` are the PR's own changes under changelog.d/; `read_head` maps each
    added fragment path to its text at the head; `base_fragments` are the
    fragment paths present at the base, which a release cut must all remove;
    `read_base` maps each removed fragment to its text at the base, so the cut
    can be checked to have published its line."""
    readme = f"{FRAGMENT_DIR}/README.md"
    if any(c.status == "D" and c.path == readme for c in changes):
        return False, (f"keep {readme}; removing it takes the repo out of "
                       f"fragment mode for every later PR.")

    if is_release_cut(base_changelog, head_changelog):
        added_here = sorted(c.path for c in changes
                            if c.status == "A" and is_fragment_path(c.path))
        if added_here:
            return False, (f"a release cut adds no fragments; fold "
                           f"{', '.join(added_here)} into the cut (rerun "
                           f"release.py cut) or land it in its own PR.")
        removed = {c.path for c in changes if c.status == "D"}
        kept = sorted(p for p in base_fragments if p not in removed)
        if kept:
            return False, (f"a release cut folds every fragment and removes it; "
                           f"still present: {', '.join(kept)}. Commit the "
                           f"removals release.py made in the same PR.")
        released = released_lines(base_changelog, head_changelog)
        missing = []
        for path in sorted(p for p in base_fragments if p in removed):
            try:
                fragment = parse_fragment((read_base or {}).get(path, ""))
            except FragmentError as err:
                return False, f"{path} at the base: {err}; fix it before cutting."
            if _squash(fragment.line) not in released:
                missing.append(path)
        if missing:
            return False, (f"the new release section is missing the line from "
                           f"{', '.join(missing)}; a cut publishes every fragment "
                           f"it removes (rerun release.py cut).")
        return True, "OK, release cut (a new numbered section folds the fragments in)."

    fragment_changes = [c for c in changes if is_fragment_path(c.path)]
    added = [c.path for c in fragment_changes if c.status == "A"]
    touched = [c for c in fragment_changes if c.status != "A"]
    if touched:
        names = ", ".join(c.path for c in touched)
        return False, (f"a PR adds its own fragment and leaves the others alone; "
                       f"this one edits or removes {names}.")
    bad_names = [p for p in added if not _FRAGMENT_NAME.match(p.split("/")[1])]
    if bad_names:
        return False, (f"fragment names are lowercase letters, digits, '.', '_' "
                       f"and '-' ending in .md; rename {', '.join(bad_names)}.")
    if len(added) > 1:
        return False, (f"add exactly one fragment per PR; this one adds "
                       f"{len(added)} ({', '.join(added)}).")

    edited = unreleased_edited(base_changelog, head_changelog)
    if len(added) == 1:
        path = added[0]
        try:
            parse_fragment(read_head.get(path, ""))
        except FragmentError as err:
            return False, f"{path}: {err}."
        if edited:
            return False, (f"the line belongs in {path} only; leave "
                           f"'## [Unreleased]' in {CHANGELOG} as it is.")
        return True, f"OK, one fragment ({path})."

    if edited:
        if legacy_allowed(pr_created_at, now):
            if only_adds(base_changelog, head_changelog):
                return True, ("OK, a direct [Unreleased] line, accepted for a PR "
                              "opened before the fragment cutover.")
            return False, (f"a PR opened before the cutover may only add its own "
                           f"bullet to '## [Unreleased]'; this one removes or "
                           f"rewrites a line already there. Restore it, or move "
                           f"the change into one new file under {FRAGMENT_DIR}/.")
        return False, (f"'## [Unreleased]' in {CHANGELOG} is assembled at release; "
                       f"move the line into one new file under {FRAGMENT_DIR}/ "
                       f"(see {FRAGMENT_DIR}/README.md). A direct line is accepted "
                       f"only for a PR opened before the cutover; a PR opened "
                       f"again after it, even for the same branch, uses a "
                       f"fragment.")
    if escape:
        return True, f'OK, change declared no user-visible change (matched "{escape}").'
    return False, (f'add one fragment under {FRAGMENT_DIR}/ (a "### Section" header '
                   f'and one plain "what users get" bullet; see '
                   f'{FRAGMENT_DIR}/README.md), or declare "no user-visible '
                   f'change" in the PR body or a commit message.')


# --------------------------------------------------------------------------- #
# Edges (git reads)
# --------------------------------------------------------------------------- #
def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True)


def _git_show(root: Path, ref_path: str) -> str | None:
    """`git show ref:path`, or None when the path is absent at that ref."""
    proc = _git(root, "show", ref_path)
    return proc.stdout if proc.returncode == 0 else None


def _merge_base(root: Path, base: str, head: str) -> str:
    """The merge base of base and head, or base itself when there is none."""
    proc = _git(root, "merge-base", base, head)
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else base


def _fragment_changes(root: Path, base: str, head: str) -> list[Change]:
    # -z: NUL-separated and unquoted, so a non-ASCII or odd name arrives as the
    # real path instead of git's quoted "\303\251" form. With --no-renames each
    # change is exactly "<status>\0<path>\0".
    proc = _git(root, "diff", "--name-status", "--no-renames", "-z", base, head,
                "--", f"{FRAGMENT_DIR}/")
    fields = proc.stdout.split("\0")
    return [Change(status=fields[i][:1], path=fields[i + 1])
            for i in range(0, len(fields) - 1, 2)]


def _fragments_at(root: Path, ref: str) -> tuple[str, ...]:
    """The fragment paths present at a ref (empty when there is no folder)."""
    proc = _git(root, "ls-tree", "--name-only", "-z", ref, f"{FRAGMENT_DIR}/")
    return tuple(p for p in proc.stdout.split("\0") if is_fragment_path(p))


def _has_fragment_dir(root: Path, head: str) -> bool:
    return _git(root, "cat-file", "-e", f"{head}:{FRAGMENT_DIR}").returncode == 0


def _commit_messages(root: Path, base: str, head: str) -> str:
    rng = f"{base}..{head}" if base else head
    proc = _git(root, "log", "--format=%B", rng)
    return proc.stdout if proc.returncode == 0 else ""


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _fail(msg: str, annotate: bool, file: str) -> int:
    if annotate:
        print(f"::error file={file}::changelog-guard: {msg}")
    print(f"changelog-guard: FAIL, {msg}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["check"])
    ap.add_argument("--base", required=True, help="base ref or sha")
    ap.add_argument("--head", default="HEAD", help="head ref (default HEAD)")
    ap.add_argument("--repo", default=".", help="repo root (default .)")
    ap.add_argument("--body-file", help="PR body file, scanned for the escape")
    ap.add_argument("--pr-created-at",
                    help="ISO time the PR was opened (fragment cutover window)")
    ap.add_argument("--annotate", action="store_true",
                    help="emit a GitHub Actions error annotation on failure")
    args = ap.parse_args(argv)
    root = Path(args.repo).resolve()
    now = now or datetime.now(timezone.utc)

    merge_base = _merge_base(root, args.base, args.head)
    # Fragment mode holds when the head, the merge base or the base tip has
    # changelog.d/: a PR that deletes the whole folder is still judged as a
    # fragment PR (its removals are caught), and a PR that branched before the
    # folder existed cannot keep using legacy mode once the base has it.
    # Fragment mode compares against the merge base; legacy mode keeps its
    # original base-tip comparison unchanged.
    fragment_mode = (_has_fragment_dir(root, args.head)
                     or _has_fragment_dir(root, merge_base)
                     or _has_fragment_dir(root, args.base))
    base = merge_base if fragment_mode else args.base
    base_text = _git_show(root, f"{base}:{CHANGELOG}")
    head_text = _git_show(root, f"{args.head}:{CHANGELOG}")
    if head_text is None:
        # Fall back to the working tree (uncommitted changelog edit).
        wt = root / CHANGELOG
        head_text = wt.read_text(encoding="utf-8") if wt.exists() else None

    body = ""
    if args.body_file and Path(args.body_file).exists():
        body = Path(args.body_file).read_text(encoding="utf-8")
    escape = exemption([body, _commit_messages(root, base, args.head)])

    if fragment_mode:
        if is_release_cut(base_text or "", head_text or ""):
            # A release cut is judged against the current base tip, not the
            # merge base: a fragment that landed after the cut branched shows up
            # as one the cut neither folded nor kept, so a stale cut is refolded.
            base = args.base
            base_text = _git_show(root, f"{base}:{CHANGELOG}")
        changes = _fragment_changes(root, base, args.head)
        read_head = {c.path: _git_show(root, f"{args.head}:{c.path}") or ""
                     for c in changes if c.status == "A"}
        passed, msg = check_fragments(
            changes, read_head, base_text or "", head_text or "", escape,
            _parse_time(args.pr_created_at), now,
            base_fragments=_fragments_at(root, base),
            read_base={c.path: _git_show(root, f"{base}:{c.path}") or ""
                       for c in changes if c.status == "D"})
        if passed:
            print(f"changelog-guard: {msg}")
            return 0
        return _fail(msg, args.annotate, FRAGMENT_DIR)

    if head_text is None and base_text is None:
        print("changelog-guard: no CHANGELOG.md on either side; skipping.")
        return 0

    if base_text and is_release_cut(base_text, head_text or ""):
        passed, msg = check_legacy_cut(base_text or "", head_text or "")
        if passed:
            print(f"changelog-guard: {msg}")
            return 0
        return _fail(msg, args.annotate, CHANGELOG)

    if has_new_entry(base_text or "", head_text or ""):
        print("changelog-guard: OK, a new [Unreleased] line is present.")
        return 0

    if escape:
        print(f"changelog-guard: OK, change declared no user-visible change "
              f"(matched \"{escape}\").")
        return 0

    return _fail('add one plain "what users get" line under "## [Unreleased]" in '
                 'CHANGELOG.md, or declare "no user-visible change" in the PR body '
                 'or a commit message.', args.annotate, CHANGELOG)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
