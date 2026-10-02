#!/usr/bin/env python3
"""manage-roadmap: the roadmap plugin for the shared migration engine, plus three verbs of its own.

As a CLI (`python3 tools/docs/roadmap.py <verb>`; RE10): `status [--check]`, `inbox [--add TEXT]`,
`reviewed [--sha SHA]`. Every cross-plugin verb (init, check, upgrade, migrate) lives in docs.py,
which loads this file beside its own plugin.

As a plugin (engine/INTERFACE.md section 3): `name`, `scan` (planning surfaces, open issues, Beads
rows; it scans first and the docs plugin takes the complement, RE4), `dispositions`, `triage`,
`consumers`, `verify`, `approval_lines`, plus the hooks docs.py calls: `init(repo, stage)`,
`maintain(m)` (the daily run: Beads export + status block, with DE3 fingerprints),
`upgrade_layout(repo, version)`, `run_checks(ctx)` (the structural roadmap checks inside the one
`docs.py check` pass, DE5) and `read_test_questions(ctx)` (the {next, milestones} cold-read test).

Canonical home is the manage-roadmap skill folder beside manage-docs; vendored into each repo as
`tools/docs/roadmap.py` beside docs.py, with its templates under `tools/docs/templates/roadmap/`.
Python 3.9+ standard library only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
# Vendored: docslib sits beside this file. Canonical: it is in the sibling manage-docs skill.
DOCS_HOME = HERE if (HERE / "docslib").is_dir() else HERE.parent / "manage-docs"
if str(DOCS_HOME) not in sys.path:
    sys.path.insert(0, str(DOCS_HOME))

from docslib import config  # noqa: E402
from docslib._engine import engine  # noqa: E402

migrate = engine()

# ======================================================================== layout

name = "roadmap"
LAYOUT_VERSION = 1
RM = "docs/roadmap/"
VISION = RM + "VISION.md"
ROADMAP = RM + "ROADMAP.md"
DECISIONS = config.DECISIONS
STOP = RM + "STOP-AND-ASK.md"
REQUIRED_FILES = [VISION, ROADMAP, DECISIONS, STOP]
EXPORT = ".beads/issues.jsonl"
SPEC_RX = re.compile(r"^docs/roadmap/milestones/(m\d+)-[^/]+/SPEC\.md$")
EPIC_RX = re.compile(r"^docs/roadmap/milestones/(m\d+)-[^/]+/epics/[^/]+\.md$")
STATUS_OPEN = "<!-- manage-roadmap:status -->"
STATUS_CLOSE = "<!-- /manage-roadmap:status -->"
PLACEMENTS = ["Now", "Next", "Later", "Shipped"]
ORDER_SECTIONS = ["Now", "Next", "Later"]
CHAT_RX = re.compile(r"confirmed by Max in chat:\s*\S", re.I)
BEADS_RX = re.compile(r"^beads:\s*([A-Za-z0-9][\w.-]*)\s*$", re.M)
TARGET_RX = re.compile(r"^target:\s*(\d{4}-\d{2}-\d{2})\s*$", re.M)
INBOX_DATE = re.compile(r"^- (\d{4}-\d{2}-\d{2})\b")
BD_MARKERS = ("BEGIN BEADS INTEGRATION", "BEGIN BEADS CODEX SETUP")
AGENT_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")
HUMAN_LABELS = {"needs-human", "human"}
IDLE_DAYS = 14
INBOX_DAYS = 30
READY_SHOWN = 5

TEMPLATE_DIR = HERE / "templates" / "roadmap" if (HERE / "templates" / "roadmap").is_dir() else HERE / "templates"
TEMPLATES = {VISION: "VISION.md", ROADMAP: "ROADMAP.md", STOP: "STOP-AND-ASK.md",
             RM + "templates/milestone-SPEC.md": "milestone-SPEC.md", RM + "templates/epic.md": "epic.md"}

# Planning-shaped files (spec "scan": roadmap/todo/program/plan/work-item shaped, frontmatter state:)
PLAN_NAME = re.compile(r"(?i)^(roadmap|todos?|backlog|program|plans?|milestones?|work-?items?)([-_. ].*)?\.(md|mdx|txt)$")
PLAN_SUFFIX = re.compile(r"(?i)[-_](roadmap|plan|backlog|todos?)\.(md|mdx)$")
PLAN_DIR = re.compile(r"(^|/)(work-items|workitems|plans|roadmap|milestones)/[^/]+\.(md|mdx|txt)$", re.I)
VENDORED = "tools/docs/"  # the skill's own vendored copy (templates included), never a planning surface
RECORD_DIR = re.compile(r"(^|/)(handoffs?|runs?|evidence|status|codex-autopilot)/")
STATE_LINE = re.compile(r"^(state|status):\s*(\S+)", re.M | re.I)
TERMINAL = {"done", "closed", "shipped", "complete", "completed", "cancelled", "canceled", "superseded",
            "abandoned", "merged", "dropped", "wontfix", "archived"}
TRACKER_NAMES = {"todo.md", "backlog.md"}

dispositions = {k: dict(migrate.STANDARD[k]) for k in
                ("keep", "move", "task", "record", "delete", "issue:close-tracked", "issue:close-rejected")}


# ======================================================================== small text helpers

def _strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def meat(text: str) -> str:
    """Comparable content: comments and blank lines dropped, trailing spaces trimmed."""
    return "\n".join(ln.rstrip() for ln in _strip_comments(text).splitlines() if ln.strip())


def strip_status(text: str) -> str:
    a, b = text.find(STATUS_OPEN), text.find(STATUS_CLOSE)
    if a == -1 or b == -1 or b < a:
        return text
    return text[:a] + text[b + len(STATUS_CLOSE):]


def h2_sections(text: str) -> Dict[str, str]:
    """{heading: body} for `## ` headings outside fences (the status block must be stripped first)."""
    out: Dict[str, str] = {}
    cur, buf, fence = None, [], False
    for ln in text.splitlines():
        if re.match(r"^\s{0,3}(```|~~~)", ln):
            fence = not fence
        m = None if fence else re.match(r"^##\s+(.+?)\s*#*\s*$", ln)
        if m:
            if cur is not None:
                out.setdefault(cur, "\n".join(buf))
            cur, buf = m.group(1).strip(), []
        elif cur is not None:
            buf.append(ln)
    if cur is not None:
        out.setdefault(cur, "\n".join(buf))
    return out


def section_named(text: str, *prefixes: str) -> Optional[str]:
    for head, body in h2_sections(text).items():
        if any(head.lower().startswith(p.lower()) for p in prefixes):
            return body
    return None


def title_of(text: str, default: str) -> str:
    for ln in text.splitlines():
        if ln.startswith("# "):
            return ln[2:].strip()
    return default


def order_view(roadmap_text: str) -> str:
    """The level-3 part of ROADMAP.md: what sits under Now, Next and Later."""
    secs = h2_sections(strip_status(roadmap_text))
    return "\n".join(f"{s}:\n{meat(secs.get(s, ''))}" for s in ORDER_SECTIONS)


def human_view(roadmap_text: str) -> str:
    """ROADMAP.md outside the Inbox and the generated block (the ruling-paired part)."""
    text = strip_status(roadmap_text)
    secs = h2_sections(text)
    head = text.split("\n## ", 1)[0]
    return meat(head) + "\n" + "\n".join(f"{k}:\n{meat(v)}" for k, v in sorted(secs.items()) if k != "Inbox")


def criteria_problems(text: str) -> List[str]:
    out = []
    for label, prefixes in (("success criteria", ("success criteria", "success bar", "success")),
                            ("exit criteria", ("exit criteria",))):
        body = section_named(text, *prefixes)
        if body is None:
            out.append(f"no `## {label.capitalize()}` section")
        elif not meat(body):
            out.append(f"`## {label.capitalize()}` is empty")
    return out


def beads_ref(text: str) -> Optional[str]:
    m = BEADS_RX.search(_strip_comments(text))
    return m.group(1) if m else None


def parse_export(text: Optional[str]) -> Dict[str, dict]:
    rows: Dict[str, dict] = {}
    for ln in (text or "").splitlines():
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict) and r.get("id") and r.get("_type", "issue") == "issue" \
                and r.get("status") != "tombstone":
            rows[r["id"]] = r
    return rows


def points_back(row: dict, path: str) -> bool:
    if row.get("external_ref") == path:
        return True
    return path in migrate.bead_text(row)


def _date(s) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _git(root: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, errors="replace")


def template_text(rel: str) -> str:
    return (TEMPLATE_DIR / TEMPLATES[rel]).read_text(encoding="utf-8")


# ======================================================================== the roadmap model

class Model:
    """Milestones, epics and Beads rows read through `read(path) -> text|None` (a file tree or a
    checker context), so the status block, the checks and the cold-read oracle share one parse."""

    def __init__(self, paths: Iterable[str], read: Callable[[str], Optional[str]], cfg: dict):
        self.read = read
        self.cfg = cfg
        self.paths = sorted(set(paths))
        self.roadmap = read(ROADMAP) or ""
        self.rows = parse_export(read(EXPORT))
        self.children: Dict[str, List[str]] = {}
        self.blockers: Dict[str, List[str]] = {}
        for rid, r in self.rows.items():
            for d in r.get("dependencies") or []:
                if d.get("type") == "parent-child":
                    self.children.setdefault(d.get("depends_on_id"), []).append(rid)
                elif d.get("type") == "blocks":
                    self.blockers.setdefault(rid, []).append(d.get("depends_on_id"))
        secs = h2_sections(strip_status(self.roadmap))
        self.milestones: List[dict] = []
        for p in self.paths:
            m = SPEC_RX.match(p)
            if not m:
                continue
            text = read(p) or ""
            mid = m.group(1)
            folder = p.rsplit("/", 1)[0]
            place = "unplaced"
            for s in PLACEMENTS:
                body = meat(secs.get(s, ""))
                if re.search(rf"(?<![\w-]){re.escape(mid)}(?![\w])", body) or folder.split("/")[-1] in body:
                    place = s.lower()
                    break
            tm = TARGET_RX.search(text)
            epics = []
            for q in self.paths:
                if q.startswith(folder + "/epics/") and EPIC_RX.match(q):
                    et = read(q) or ""
                    epics.append({"path": q, "title": title_of(et, q.rsplit("/", 1)[-1][:-3]),
                                  "bd": beads_ref(et)})
            title = re.sub(rf"^{re.escape(mid)}\s*[:.-]\s*", "", title_of(text, mid), flags=re.I) or mid
            self.milestones.append({"id": mid, "path": p, "title": title,
                                    "target": tm.group(1) if tm else None, "place": place, "epics": epics})
        order = {"now": 0, "next": 1, "later": 2, "unplaced": 3, "shipped": 4}
        self.milestones.sort(key=lambda x: (order[x["place"]], x["id"]))

    def descendants(self, rid: str) -> List[str]:
        out, stack, seen = [], list(self.children.get(rid, [])), {rid}
        while stack:
            c = stack.pop()
            if c in seen:
                continue
            seen.add(c)
            out.append(c)
            stack.extend(self.children.get(c, []))
        return sorted(out)

    def current(self) -> Optional[dict]:
        for ms in self.milestones:
            if ms["place"] == "now":
                return ms
        return None

    def ready(self, ms: Optional[dict]) -> List[str]:
        """Open, unblocked, non-epic Beads items under the milestone's epics, by (priority, id)."""
        if ms is None:
            return []
        ids = set()
        for ep in ms["epics"]:
            if ep["bd"] in self.rows:
                ids.update(self.descendants(ep["bd"]))
        out = []
        for rid in ids:
            r = self.rows.get(rid)
            if not r or r.get("status") != "open" or r.get("issue_type") == "epic":
                continue
            if any(self.rows.get(b, {}).get("status", "closed") != "closed" for b in self.blockers.get(rid, [])):
                continue
            out.append(rid)
        return sorted(out, key=lambda i: (int(self.rows[i].get("priority", 2)), i))

    def milestone_status(self) -> Dict[str, str]:
        return {ms["id"]: ms["place"] for ms in self.milestones}

    def inbox(self) -> List[str]:
        body = h2_sections(strip_status(self.roadmap)).get("Inbox", "")
        return [ln for ln in _strip_comments(body).splitlines() if ln.startswith("- ")]

    def flags(self, today: dt.date) -> List[str]:
        out = []
        for ms in self.milestones:
            for ep in ms["epics"]:
                r = self.rows.get(ep["bd"] or "")
                if not r or r.get("status") != "in_progress":
                    continue
                last = max((_date(self.rows[i].get("updated_at")) or dt.date.min)
                           for i in [ep["bd"], *self.descendants(ep["bd"])])
                if (today - last).days > IDLE_DAYS:
                    out.append(f'Epic "{ep["title"]}" ({ms["id"]}) is in progress with no Beads activity '
                               f"for more than {IDLE_DAYS} days.")
            t = _date(ms["target"])
            if t and ms["place"] != "shipped" and t < today:
                out.append(f'Milestone {ms["id"]} "{ms["title"]}" is past its target ({ms["target"]}).')
        old = [ln for ln in self.inbox() if INBOX_DATE.match(ln)
               and (today - dt.date.fromisoformat(INBOX_DATE.match(ln).group(1))).days > INBOX_DAYS]
        if old:
            out.append(f"{len(old)} Inbox item{'s are' if len(old) != 1 else ' is'} untriaged for more than "
                       f"{INBOX_DAYS} days.")
        return out


def review_changes(root: Path, model: Model, sha: Optional[str]) -> str:
    """Human-layer files whose content differs from the reviewed commit (status block and Inbox
    ignored, so regenerating the block never counts as a change)."""
    if not sha:
        return "Not reviewed yet: run `tools/docs/roadmap.py reviewed` after Max reviews the roadmap."
    if _git(root, "cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        return f"Last review `{sha[:12]}`: that commit is not in this clone."
    then = set(_git(root, "ls-tree", "-r", "--name-only", sha, RM).stdout.split())
    now = set(model.paths)
    human = lambda ps: {p for p in ps if p in (VISION, ROADMAP) or SPEC_RX.match(p)}  # noqa: E731
    changed = []
    for p in sorted(human(then) | human(now)):
        old = _git(root, "show", f"{sha}:{p}").stdout if p in then else None
        new = model.read(p) if p in now else None
        if p == ROADMAP:
            old = human_view(old) if old is not None else None
            new = human_view(new) if new is not None else None
        if old != new:
            changed.append(p[len(RM):])
    if not changed:
        return f"No human-layer changes since the last review (`{sha[:12]}`)."
    return f"Changed since the last review (`{sha[:12]}`): " + ", ".join(changed) + "."


def status_block(root: Path, model: Model, today: dt.date) -> str:
    lines = [STATUS_OPEN, "## Status", "",
             "_Generated by `tools/docs/roadmap.py status` from the milestone files and `.beads/issues.jsonl`. "
             "Never edit by hand._", ""]
    if model.milestones:
        lines += ["| Milestone | Status | Target | Epics |", "|---|---|---|---|"]
        for ms in model.milestones:
            lines.append(f"| {ms['id']} {ms['title']} | {ms['place']} | {ms['target'] or '-'} | {len(ms['epics'])} |")
    else:
        lines.append("No milestones yet.")
    cur = model.current()
    for ms in model.milestones:
        if ms["place"] == "shipped" or not ms["epics"]:
            continue
        lines += ["", f"### {ms['id']} {ms['title']} ({ms['place']})", ""]
        for ep in ms["epics"]:
            rid = ep["bd"]
            if not rid or rid not in model.rows:
                lines.append(f"- {ep['title']}: no Beads epic linked")
                continue
            counts = {"open": 0, "in_progress": 0, "blocked": 0, "closed": 0}
            for c in model.descendants(rid):
                st = model.rows[c].get("status", "open")
                counts[st if st in counts else "open"] += 1
            lines.append(f"- {ep['title']} ({rid}): {counts['open']} open, {counts['in_progress']} in progress, "
                         f"{counts['blocked']} blocked, {counts['closed']} done")
        if ms is cur:
            ready = model.ready(ms)
            shown = "; ".join(f"{i} (P{model.rows[i].get('priority', 2)}) {model.rows[i].get('title', '')}"
                              for i in ready[:READY_SHOWN])
            lines += ["", f"Ready next: {shown}" if ready else "Ready next: nothing ready."]
    waiting = sorted(i for i, r in model.rows.items() if r.get("status") != "closed"
                     and HUMAN_LABELS & set(r.get("labels") or []))
    lines += ["", "Waiting on a human: " + ("; ".join(f"{i} {model.rows[i].get('title', '')}" for i in waiting)
                                           if waiting else "nothing.")]
    rv = (model.cfg.get("roadmap") or {}).get("reviewed_sha")
    lines += ["", review_changes(root, model, rv)]
    flags = model.flags(today)
    if flags:
        lines += ["", "Flags:", ""] + [f"- {f}" for f in flags]
    lines.append(STATUS_CLOSE)
    return "\n".join(lines)


def with_block(roadmap_text: str, block: str) -> str:
    a, b = roadmap_text.find(STATUS_OPEN), roadmap_text.find(STATUS_CLOSE)
    if a != -1 and b != -1 and b > a:
        return roadmap_text[:a] + block + roadmap_text[b + len(STATUS_CLOSE):]
    return roadmap_text.rstrip("\n") + "\n\n" + block + "\n"


# ======================================================================== repo readers

def tree_paths(root: Path) -> List[str]:
    """Roadmap paths on disk (tracked or not: public repos keep the roadmap local-only)."""
    base = root / "docs" / "roadmap"
    if not base.is_dir():
        return []
    return sorted(p.relative_to(root).as_posix() for p in base.rglob("*") if p.is_file())


def file_reader(root: Path) -> Callable[[str], Optional[str]]:
    def read(rel: str) -> Optional[str]:
        fp = root / rel
        return fp.read_text(encoding="utf-8", errors="replace") if fp.is_file() else None
    return read


def model_for(root: Path, cfg: Optional[dict] = None) -> Model:
    root = Path(root)
    cfg = cfg if cfg is not None else (config.load(root) or {})
    return Model(tree_paths(root), file_reader(root), cfg)


def active(root: Path) -> bool:
    return (Path(root) / ROADMAP).is_file()


def render(root: Path, cfg: dict, today: dt.date) -> Tuple[str, str]:
    """(current ROADMAP.md text, regenerated text)."""
    model = model_for(root, cfg)
    cur = model.roadmap
    return cur, with_block(cur, status_block(Path(root), model, today))


# ======================================================================== plugin: scan / triage

def planning_shaped(path: str, head: str) -> bool:
    if path.startswith(RM):
        return True
    if path.startswith(("work/", VENDORED)) or RECORD_DIR.search(path):
        return False
    base = path.rsplit("/", 1)[-1]
    if not base.lower().endswith((".md", ".mdx", ".txt")):
        return False
    if PLAN_NAME.match(base) or PLAN_SUFFIX.search(base) or PLAN_DIR.search(path):
        return True
    return bool(STATE_LINE.search(head)) and head.startswith("---")


def open_pr_files(root: Path) -> Optional[set]:
    """Paths touched by open PRs; None when gh cannot say (then nothing is auto-deleted)."""
    try:
        data = migrate.gh_json(["pr", "list", "--state", "open", "--limit", "500", "--json", "number,files"], root)
    except Exception:  # noqa: BLE001 (any gh failure means "unknown", never "none")
        return None
    out = set()
    for pr in data or []:
        for f in pr.get("files") or []:
            out.add(f.get("path") if isinstance(f, dict) else str(f))
    return out


PATH_TOKEN = re.compile(r"[\w./-]+\.(?:md|mdx|txt)\b")
LINK_TARGET = re.compile(r"\]\(\s*<?([^)\s>#]+)")


def doc_citations(repo) -> Dict[str, List[str]]:
    """{path: citing docs}: every live (non-record) markdown or text file that names a path, by
    repo-relative path or relative link. A cited planning file is never stale."""
    out: Dict[str, set] = {}
    for src in repo.tracked:
        if src.startswith("work/") or not src.lower().endswith((".md", ".mdx", ".txt")):
            continue
        if (repo.root / src).is_symlink():
            continue
        try:
            text = repo.read_text(src)
        except (OSError, UnicodeDecodeError):
            continue
        d = os.path.dirname(src)
        for tok in set(PATH_TOKEN.findall(text)) | set(LINK_TARGET.findall(text)):
            for cand in {tok.lstrip("/"), os.path.normpath(os.path.join(d, tok)) if not tok.startswith("/") else ""}:
                if cand and cand != src:
                    out.setdefault(cand, set()).add(src)
    return {k: sorted(v) for k, v in out.items()}


def scan(repo):
    pr_files = open_pr_files(repo.root)
    cites = doc_citations(repo)
    roadmap_text = ""
    if (repo.root / ROADMAP).is_file():
        roadmap_text = repo.read_text(ROADMAP)
    bead_text = "\n".join(migrate.bead_text(r) for r in repo.beads)
    for path in repo.tracked:
        if path in repo.claimed:
            continue
        if (repo.root / path).is_symlink():
            continue  # a tracked symlink is code; the docs plugin claims it by its link text
        if path.startswith(".beads/"):
            yield migrate.make_item(name, path, "", "code", repo.read_text(path),
                                    last_commit_date=repo.last_commit_date(path))
            continue
        try:
            text = repo.read_text(path)
        except (OSError, UnicodeDecodeError):
            continue
        head = "\n".join(text.splitlines()[:12])
        if not planning_shaped(path, head):
            continue
        states = {m.group(2).lower().strip("\"'") for m in STATE_LINE.finditer(head)}
        refs = []
        if pr_files is None:
            refs.append("pr:unknown")
        elif path in pr_files:
            refs.append("pr:open")
        if path in bead_text:
            refs.append("beads")
        if path in roadmap_text:
            refs.append("roadmap")
        refs += [f"doc:{c}" for c in cites.get(path, [])]
        yield migrate.make_item(name, path, "", "file", text, terminal=bool(states & TERMINAL),
                                last_commit_date=repo.last_commit_date(path), live_refs=refs)
    for issue in repo.issues:
        yield migrate.make_item(name, "@issues", f"issue/{issue['number']}", "issue", migrate.issue_text(issue))
    for row in repo.beads:
        yield migrate.make_item(name, "@beads", f"bd/{row['id']}", "bead", migrate.canon(row).decode("utf-8"))


def triage(item, cfg, today: Optional[dt.date] = None):
    kind = item["kind"]
    if kind == "bead":
        return "keep"
    if kind in ("issue", "code"):
        return None
    if item["source_path"].startswith(RM):
        return "keep"
    if item["signals"]["terminal"]:
        return "record:work-item"
    stale_days = int((cfg.get("roadmap") or {}).get("stale_days", 60))
    last = _date(item["signals"].get("last_commit_date"))
    today = today or dt.date.today()
    if last and (today - last).days > stale_days and not item["signals"].get("live_refs"):
        return "delete"
    return None


def consumers(repo, paths):
    return []


# ======================================================================== plugin: verify (after apply)

def link_problems(paths: Iterable[str], read: Callable[[str], Optional[str]], rows: Dict[str, dict],
                  only: Optional[Iterable[str]] = None, check_rows: bool = True) -> List[Tuple[str, str]]:
    """(path, message) for every broken epic <-> Beads link."""
    out = []
    targets = sorted(only) if only is not None else sorted(p for p in paths if EPIC_RX.match(p))
    for p in targets:
        if not EPIC_RX.match(p):
            continue
        text = read(p)
        if text is None:
            continue
        rid = beads_ref(text)
        if not rid:
            out.append((p, "epic names no Beads id (`beads: <id>` line)"))
        elif rid not in rows:
            out.append((p, f"epic names Beads id {rid}, which is not in {EXPORT}"))
        elif not points_back(rows[rid], p):
            out.append((p, f"Beads epic {rid} does not point back to {p} (set its external ref to the path)"))
    if check_rows:
        for rid, r in sorted(rows.items()):
            ref = r.get("external_ref") or ""
            if r.get("issue_type") == "epic" and ref.startswith(RM):
                text = read(ref)
                if text is None:
                    out.append((EXPORT, f"Beads epic {rid} points at {ref}, which does not exist"))
                elif beads_ref(text) != rid:
                    out.append((ref, f"Beads epic {rid} points here but the epic names {beads_ref(text) or 'no id'}"))
    return out


def unlinked_epics(rows: Dict[str, dict]) -> List[str]:
    return sorted(rid for rid, r in rows.items()
                  if r.get("issue_type") == "epic" and not (r.get("external_ref") or "").startswith(RM))


def verify(repo, ledger):
    root = Path(repo.root)
    read = file_reader(root)
    paths = tree_paths(root)
    out = []
    if not paths:
        return out
    fail = lambda iid, msg, b="fail": out.append({"item_id": iid, "message": msg, "blocking": b})  # noqa: E731
    # the layout is init's output (`docs.py init --stage migration` runs after apply, before the
    # local verify), so the rehearsal flags a missing file and `docs.py check` fails the PR on it
    for rel in REQUIRED_FILES:
        if read(rel) is None:
            fail(None, f"{rel} missing from the roadmap layout (run `docs.py init --stage migration`)", "warn")
    for p in paths:
        if SPEC_RX.match(p) or EPIC_RX.match(p):
            for prob in criteria_problems(read(p) or ""):
                fail(None, f"{p}: {prob}")
    rows = parse_export(read(EXPORT))
    for p, msg in link_problems(paths, read, rows):
        fail(None, f"{p}: {msg}")
    for rid in unlinked_epics(rows):
        fail(f"bd/{rid}", f"Beads epic {rid} has no epic file under {RM} (link it in a later change)", "warn")
    for b in repo.beads:
        if b["id"] not in rows:
            fail(f"bd/{b['id']}", f"Beads item {b['id']} from beads-before.jsonl missing in {EXPORT}")
    for row in ledger:
        if row.get("disposition") == "issue:close-tracked" and row.get("bd_id") not in rows:
            fail(row["item_id"], f"issue intent names Beads id {row.get('bd_id')}, not in the candidate {EXPORT}")
    return out


# ======================================================================== checker (DE5)

def _active_in(ctx) -> bool:
    if ROADMAP in ctx.tracked or ROADMAP in ctx.changed("AMR"):
        return True
    return ctx.scope == "all" and config.is_public(ctx.cfg) and (ctx.root / ROADMAP).is_file()


def _ctx_paths(ctx) -> List[str]:
    tracked = {p for p in ctx.tracked if p.startswith(RM)} | {p for p in ctx.changed("AMR") if p.startswith(RM)}
    if ctx.scope == "all" and config.is_public(ctx.cfg):
        tracked |= set(tree_paths(ctx.root))
    return sorted(tracked)


def added_entries(ctx) -> List[str]:
    """DECISIONS.md entries (dated `## ` blocks) present after the change and not before."""
    def blocks(text):
        out, cur = [], None
        for ln in (text or "").splitlines():
            if re.match(r"^## \d{4}-\d{2}-\d{2} \S", ln):
                if cur is not None:
                    out.append("\n".join(cur))
                cur = [ln]
            elif cur is not None:
                if ln.startswith("## "):
                    out.append("\n".join(cur))
                    cur = None
                else:
                    cur.append(ln)
        if cur is not None:
            out.append("\n".join(cur))
        return out
    before = blocks(ctx.read_before(DECISIONS))
    return [b for b in blocks(ctx.read(DECISIONS)) if b not in before]


def human_layer_changes(ctx) -> List[Tuple[str, int]]:
    """[(path, level)] where level 3 = human only (vision, milestone order, success criteria)."""
    out = []
    for st, p, old in ctx.changes:
        for q in sorted({p, old} - {None}):
            before = ctx.read_before(q) if st != "A" or q != p else None
            after = ctx.read(q) if q == p and st != "D" else None
            if q in TEMPLATES and before is None and after == template_text(q):
                continue  # the untouched template laid down by init
            if q == VISION:
                if (before or "") != (after or ""):
                    out.append((q, 3))
            elif q == ROADMAP:
                if order_view(before or "") != order_view(after or ""):
                    out.append((q, 3))
                elif human_view(before or "") != human_view(after or ""):
                    out.append((q, 2))
            elif SPEC_RX.match(q):
                if before is not None and after is not None and \
                        meat(section_named(before, "success") or "") != meat(section_named(after, "success") or ""):
                    out.append((q, 3))
                elif (before or "") != (after or ""):
                    out.append((q, 2))
    return out


def run_checks(ctx):
    """Structural roadmap checks (spec "Roadmap checker rules"); fail level unless noted."""
    if not _active_in(ctx):
        return []
    res = lambda path, msg, level="fail": {"check": "roadmap", "path": path, "line": None,  # noqa: E731
                                           "message": msg, "level": level}
    out = []
    excl = ctx.cfg.get("inventory_exclude") or []
    diff = ctx.scope != "all"
    paths = _ctx_paths(ctx)
    changed = set(ctx.changed("ACMR")) if diff else set(paths) | set(ctx.tracked)
    # required layout files and the generated block's markers
    for rel in REQUIRED_FILES:
        if ctx.read(rel) is None:
            out.append(res(rel, "required roadmap file missing (run `tools/docs/docs.py init --stage migration`)"))
    rm = ctx.read(ROADMAP) or ""
    if STATUS_OPEN not in rm or STATUS_CLOSE not in rm:
        out.append(res(ROADMAP, "no generated status block markers (run `tools/docs/roadmap.py status`)"))
    # layout version
    if int(ctx.cfg.get("layout_version", 1)) < LAYOUT_VERSION:
        out.append(res("docs.json", f"layout_version {ctx.cfg.get('layout_version')} is older than the vendored "
                                    f"roadmap layout {LAYOUT_VERSION}; run `tools/docs/docs.py upgrade`"))
    # criteria on changed milestone and epic files
    for p in sorted(changed):
        if SPEC_RX.match(p) or EPIC_RX.match(p):
            for prob in criteria_problems(ctx.read(p) or ""):
                out.append(res(p, prob))
    # epic <-> Beads links
    rows = parse_export(ctx.read(EXPORT))
    only = [p for p in changed if EPIC_RX.match(p)]
    for p, msg in link_problems(paths, ctx.read, rows, only=only, check_rows=(not diff or EXPORT in changed)):
        out.append(res(p, msg))
    # ruling pairing (diff scopes only: a repo-wide pass has no change to pair)
    if diff:
        hl = human_layer_changes(ctx)
        if hl:
            entries = added_entries(ctx)
            if not entries:
                for p, _ in hl:
                    out.append(res(p, f"human-layer change without a dated {DECISIONS} entry "
                                      "(`## YYYY-MM-DD <title>`) in the same change"))
            lvl3 = sorted({p for p, lv in hl if lv == 3})
            if lvl3 and os.environ.get("MANAGE_DOCS_ROUTINE") == "1":
                for p in lvl3:
                    out.append(res(p, "a routine run never makes a level-3 change; file it to the Inbox instead"))
            elif lvl3 and entries and not any(CHAT_RX.search(e) for e in entries):
                for p in lvl3:
                    out.append(res(p, "level-3 change (vision, milestone order or success criteria): the "
                                      f"{DECISIONS} entry must cite the chat (\"confirmed by Max in chat: <where>\")"))
    # no parallel tracker
    for p in sorted(changed if diff else ctx.tracked):
        if migrate.any_glob(excl, p) or p.startswith(("work/", RM)):
            continue
        base = p.rsplit("/", 1)[-1].lower()
        if base in TRACKER_NAMES or re.search(r"(^|/)work-?items/", p):
            out.append(res(p, "parallel tracker: plans live in docs/roadmap/ and tasks in Beads"))
    # no bd AGENTS.md integration markers
    for p in sorted(changed if diff else ctx.tracked):
        if p.rsplit("/", 1)[-1] in AGENT_FILES and not migrate.any_glob(excl, p):
            text = ctx.read(p) or ""
            if any(mk in text for mk in BD_MARKERS):
                out.append(res(p, "bd AGENTS.md integration markers: remove them (Beads is used through "
                                  "manage-roadmap, not bd's own setup)"))
    # repo-wide only: status freshness and staleness flags are warnings, never PR failures
    if not diff and (ctx.root / ROADMAP).is_file():
        today = dt.date.today()
        cur, new = render(ctx.root, ctx.cfg, today)
        if cur != new:
            out.append(res(ROADMAP, "status block is stale; run `tools/docs/roadmap.py status`", "warn"))
        for f in model_for(ctx.root, ctx.cfg).flags(today):
            out.append(res(ROADMAP, f, "warn"))
    return out


# ======================================================================== cold-read questions (DE5)

def _as_json(answer):
    if isinstance(answer, str):
        try:
            return json.loads(answer)
        except json.JSONDecodeError:
            return answer
    return answer


def compare_next(expected: dict, answer) -> bool:
    ans = _as_json(answer)
    if not isinstance(ans, list) or not ans:
        return False
    got = {str(a).strip().strip("`") for a in ans}
    return got <= set(expected["ready"]) and expected["top"] in got


def compare_milestones(expected: dict, answer) -> bool:
    ans = _as_json(answer)
    if not isinstance(ans, dict):
        return False
    return {str(k).strip(): str(v).strip().lower() for k, v in ans.items()} == expected


def read_test_questions(ctx) -> List[dict]:
    root = Path(ctx.root)
    if not active(root):
        return []
    model = model_for(root, ctx.cfg)
    qs = []
    ready = model.ready(model.current())
    if ready:
        qs.append({"id": "roadmap-next",
                   "question": "Which Beads ids should an agent pick up next in the current milestone? Answer a "
                               "JSON list of ids, most important first.",
                   "expected": {"ready": ready, "top": ready[0]}, "compare": compare_next})
    if model.milestones:
        qs.append({"id": "roadmap-milestones",
                   "question": "What is the status of each milestone? Answer a JSON object mapping each milestone "
                               "id (like m01) to its status: now, next, later, shipped or unplaced.",
                   "expected": model.milestone_status(), "compare": compare_milestones})
    return qs


# ======================================================================== hooks docs.py calls (RE10)

def _bd(*args, cwd=None):
    return subprocess.run([migrate.bd_bin(), *args], cwd=cwd, capture_output=True, text=True)


def _prefix(repo: Path) -> str:
    return re.sub(r"[^a-z0-9]+", "-", Path(repo).resolve().name.lower()).strip("-")[:12] or "rm"


def init_beads(repo: Path) -> List[str]:
    """Adopt the DB `bd where` resolves (never a .beads/ dir check, eng F2), else `bd init`."""
    repo = Path(repo)
    where = migrate.beads_where(repo)
    tracked = _git(repo, "ls-files", "--error-unmatch", EXPORT).returncode == 0
    if where is None and tracked:
        return [f"Beads: {EXPORT} is tracked but `bd where` resolves no DB; run `bd import -i {EXPORT}` "
                "into a fresh DB by hand (never re-init over it)"]
    if where is None:
        if shutil_which(migrate.bd_bin()) is None:
            return ["Beads: bd is not installed; install it, then rerun `docs.py init --stage migration`"]
        p = _bd("init", "-q", "--skip-agents", "--skip-hooks", "--prefix", _prefix(repo), cwd=repo)
        if p.returncode != 0:
            return [f"Beads: `bd init` failed: {(p.stderr or p.stdout).strip()[:300]}"]
        where = migrate.beads_where(repo)
        if where is None:
            return ["Beads: `bd init` ran but `bd where` still resolves no DB"]
        done = [f"Beads: initialised a new DB at {where['path']}"]
    else:
        done = [f"Beads: adopted the existing DB at {where['path']} (pinned with --db)"]
    if not (repo / EXPORT).exists():
        rows = migrate.beads_export(where, repo)
        if not rows:
            # an empty DB has nothing to export; writing an empty file here, after the engine's
            # apply recorded the export absent, would break the migration state chain
            return done + [f"{EXPORT} not written (no rows yet; the daily maintain run exports it once there are)"]
        (repo / ".beads").mkdir(exist_ok=True)
        (repo / EXPORT).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
        done.append(f"{EXPORT} exported ({len(rows)} rows)")
    return done


def shutil_which(cmd: str) -> Optional[str]:
    import shutil
    return shutil.which(cmd) or (cmd if Path(cmd).exists() else None)


def init(repo, stage: str) -> List[str]:
    """`docs.py init --stage migration` lays down docs/roadmap/ and Beads (never in PR 1). Public repos
    keep the roadmap local-only and get it at `--stage local`. Existing files are never overwritten."""
    repo = Path(repo)
    cfg = config.load(repo) or {}
    public = config.is_public(cfg)
    if not ((stage == "migration") or (stage == "local" and public)):
        return []
    done = []
    for rel in TEMPLATES:
        fp = repo / rel
        if not fp.exists():
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(template_text(rel), encoding="utf-8")
            done.append(rel)
    dec = repo / DECISIONS
    today = dt.date.today().isoformat()
    entry = (f"\n## {today} roadmap layout adopted\n\n- manage-roadmap layout_version {LAYOUT_VERSION}, laid down by "
             "`docs.py init --stage " + stage + "`.\n")
    if not dec.exists():
        dec.parent.mkdir(parents=True, exist_ok=True)
        dec.write_text((TEMPLATE_DIR / "DECISIONS.md").read_text(encoding="utf-8").rstrip("\n") + "\n" + entry,
                       encoding="utf-8")
        done.append(DECISIONS)
    elif "roadmap layout adopted" not in dec.read_text(encoding="utf-8"):
        dec.write_text(dec.read_text(encoding="utf-8").rstrip("\n") + "\n" + entry, encoding="utf-8")
        done.append(DECISIONS + " (entry appended)")
    (repo / RM / "milestones").mkdir(parents=True, exist_ok=True)
    done += init_beads(repo)
    cur, new = render(repo, cfg, dt.date.today())
    if cur != new:
        (repo / ROADMAP).write_text(new, encoding="utf-8")
    return done


def maintain(m) -> List[dict]:
    """Daily run, phase 3: refresh the Beads export, then the status block. Every change carries a DE3
    fingerprint (op + path + input hash); a fingerprint Max rejected is held back, not written."""
    root = Path(m.root)
    if not active(root):
        return []
    changes = []
    where = migrate.beads_where(root)
    if where is not None:
        rows = migrate.beads_export(where, root)
        text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
        fp = root / EXPORT
        if (fp.exists() or rows) and (fp.read_text(encoding="utf-8") if fp.exists() else None) != text:
            c = {"op": "beads-export", "path": EXPORT, "input": migrate.sha256_text(text), "count": len(rows)}
            if not m.note_suppressed(c):
                fp.parent.mkdir(exist_ok=True)
                fp.write_text(text, encoding="utf-8")
                changes.append(c)
    cur, new = render(root, m.cfg, m.today)
    if cur != new:
        block = new[new.find(STATUS_OPEN):new.find(STATUS_CLOSE)]
        c = {"op": "roadmap-status", "path": ROADMAP, "input": migrate.sha256_text(block)}
        if not m.note_suppressed(c):
            (root / ROADMAP).write_text(new, encoding="utf-8")
            changes.append(c)
    return changes


def upgrade_layout(repo, version: int) -> List[str]:
    """Bring docs/roadmap/ from `version` to LAYOUT_VERSION (run by `docs.py upgrade`, RE10)."""
    repo = Path(repo)
    done = []
    for v in range(int(version), LAYOUT_VERSION):
        step = UPGRADES.get(v)
        if step is not None:
            done += step(repo)
    dj = repo / "docs.json"
    if dj.exists():
        raw = json.loads(dj.read_text(encoding="utf-8"))
        if int(raw.get("layout_version", 1)) < LAYOUT_VERSION:
            raw["layout_version"] = LAYOUT_VERSION
            dj.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
            done.append(f"docs.json layout_version -> {LAYOUT_VERSION}")
    return done


UPGRADES: Dict[int, Callable[[Path], List[str]]] = {}  # {from_version: step}; none yet at layout 1


def approval_lines(repo, diff_summary) -> List[str]:
    """Plain English, product level, human labels and no ids (contract section 1)."""
    root = Path(repo)
    changes = (diff_summary or {}).get("changes") or []
    ops = {c.get("op") for c in changes}
    out = []
    if "roadmap-status" in ops and active(root):
        model = model_for(root)
        cur = model.current()
        lead = f'now working on "{cur["title"]}"' if cur else "no milestone is marked Now"
        out.append(f"Refreshed the roadmap status ({lead}; {len(model.milestones)} milestone"
                   f"{'s' if len(model.milestones) != 1 else ''} tracked).")
        for f in model.flags(dt.date.today()):
            out.append("Flagged: " + re.sub(r"\s*\(m\d+\)", "", f))
    if "beads-export" in ops:
        n = next((c.get("count") for c in changes if c.get("op") == "beads-export"), None)
        out.append("Saved the current task list from Beads into the repo"
                   + (f" ({n} task{'s' if n != 1 else ''})." if n is not None else "."))
    return out


# ======================================================================== CLI verbs

def repo_root(arg) -> Path:
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=arg or ".", capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"roadmap.py: not a git repository: {arg or '.'}")
    return Path(p.stdout.strip())


def cmd_status(a) -> int:
    root = repo_root(a.repo)
    if not active(root):
        print(f"status: no {ROADMAP} (run `tools/docs/docs.py init --stage migration`)")
        return 1
    cfg = config.load(root) or {}
    cur, new = render(root, cfg, dt.date.fromisoformat(a.today) if a.today else dt.date.today())
    if a.check:
        print("status: current" if cur == new else "status: STALE (run `tools/docs/roadmap.py status`)")
        return 0 if cur == new else 1
    if cur != new:
        (root / ROADMAP).write_text(new, encoding="utf-8")
    print(new[new.find(STATUS_OPEN):new.find(STATUS_CLOSE) + len(STATUS_CLOSE)])
    return 0


def cmd_inbox(a) -> int:
    root = repo_root(a.repo)
    if not active(root):
        print(f"inbox: no {ROADMAP}")
        return 1
    fp = root / ROADMAP
    if a.add:
        text = fp.read_text(encoding="utf-8")
        line = f"- {dt.date.today().isoformat()} {' '.join(a.add.split())}"
        m = re.search(r"^## Inbox[ \t]*$", text, re.M)
        if not m:
            text = text.rstrip("\n") + "\n\n## Inbox\n"
            m = re.search(r"^## Inbox[ \t]*$", text, re.M)
        nxt = re.compile(r"^## |" + re.escape(STATUS_OPEN), re.M).search(text, m.end())
        end = nxt.start() if nxt else len(text)
        body = text[m.end():end].rstrip("\n")
        text = text[:m.end()] + body + ("\n" if body else "\n\n") + line + "\n" + ("\n" if nxt else "") + text[end:]
        fp.write_text(text, encoding="utf-8")
        print(f"inbox: added `{line}`")
        return 0
    model = model_for(root)
    items = model.inbox()
    issues: List[dict] = []
    note = ""
    if not a.no_issues:
        try:
            issues = migrate.issues_snapshot(root)
        except Exception as exc:  # noqa: BLE001
            note = f"(GitHub issues unavailable: {str(exc).strip()[:200]})"
    if a.json:
        print(json.dumps({"inbox": items, "issues": [{"number": i["number"], "title": i["title"]} for i in issues]},
                         indent=1))
        return 0
    print(f"Inbox ({len(items)}):")
    for ln in items:
        print("  " + ln)
    print(f"Open GitHub issues ({len(issues)}): " + note)
    for i in issues:
        print(f"  #{i['number']} {i['title']}")
    return 0


def cmd_reviewed(a) -> int:
    root = repo_root(a.repo)
    if os.environ.get("MANAGE_DOCS_ROUTINE") == "1":
        print("reviewed: a routine run never marks the roadmap reviewed (Max does, in an interactive session)")
        return 1
    if not active(root):
        print(f"reviewed: no {ROADMAP}")
        return 1
    ref = a.sha or "HEAD"
    p = _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if p.returncode != 0:
        print(f"reviewed: {ref} is not a commit here")
        return 1
    sha = p.stdout.strip()
    dj = root / "docs.json"
    raw = json.loads(dj.read_text(encoding="utf-8")) if dj.exists() else {}
    raw.setdefault("roadmap", {})["reviewed_sha"] = sha
    dj.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    dec = root / DECISIONS
    old = dec.read_text(encoding="utf-8") if dec.exists() else "# Decisions\n"
    dec.write_text(old.rstrip("\n") + f"\n\n## {dt.date.today().isoformat()} roadmap reviewed\n\n"
                   f"- reviewed through `{sha}`\n", encoding="utf-8")
    cur, new = render(root, config.load(root) or raw, dt.date.today())
    if cur != new:
        (root / ROADMAP).write_text(new, encoding="utf-8")
    print(f"reviewed: through {sha[:12]}; docs.json roadmap.reviewed_sha set, {DECISIONS} entry added")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="roadmap.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status", help="regenerate the ROADMAP.md status block (--check: exit 1 when stale)")
    s.add_argument("--check", action="store_true")
    s.add_argument("--today", help=argparse.SUPPRESS)
    s = sub.add_parser("inbox", help="list the Inbox and open GitHub issues, or --add a dated Inbox line")
    s.add_argument("--add", metavar="TEXT")
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-issues", action="store_true")
    s = sub.add_parser("reviewed", help="mark the roadmap reviewed through a commit (docs.json + DECISIONS)")
    s.add_argument("--sha")
    for p in sub.choices.values():
        p.add_argument("--repo")
    a = ap.parse_args(argv)
    return {"status": cmd_status, "inbox": cmd_inbox, "reviewed": cmd_reviewed}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
