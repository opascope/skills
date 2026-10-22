#!/usr/bin/env python3
"""manage-roadmap: the roadmap plugin for the shared migration engine, plus three verbs of its own.

As a CLI (`python3 tools/docs/roadmap.py <verb>`; RE10): `status [--check]`, `ready [--json]`,
`inbox [--add TEXT]`, `reviewed [--sha SHA]`, `drift [--json]` (has the planning outrun the roadmap),
`retire <mNN|epic> --as shipped|dropped|superseded --why ...` and `prune` (history leaves the roadmap for
docs/roadmap/HISTORY.md, the roadmap-removal standard). Every cross-plugin verb (init, check, upgrade, migrate) lives in docs.py,
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
import time
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
LAYOUT_VERSION = config.LAYOUT_VERSION  # docs.json's one layout_version; PR 1 seeds the current one
RM = "docs/roadmap/"
VISION = RM + "VISION.md"
# The one live roadmap is the folder's landing page (layout 2). Layout 1 kept it at ROADMAP.md;
# `docs.py upgrade` moves it once (UPGRADES[1]).
ROADMAP = RM + "README.md"
LEGACY_ROADMAP = RM + "ROADMAP.md"
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
# History leaves the roadmap for HISTORY.md (the roadmap-removal standard). The README keeps Now, Next and Later (what
# is left), a short Shipped list (SHIPPED_KEEP_DAYS) and the layout's other sections.
HISTORY = RM + "HISTORY.md"
SHIPPED_KEEP_DAYS = 30
README_LINE_BUDGET = 150
NOW_BULLET_WORDS = 60
NOW_BULLET_PRS = 3
LAYOUT_HEADINGS = ("Now", "Next", "Later", "Shipped", "Status", "Inbox", "How this roadmap works")
RETIRE_HOWS = ("shipped", "dropped", "superseded")
OUT_OF_ROADMAP = ("shipped", "retired")  # milestone places that are history, not plans
# "confirmed by <who> ...: <where>"; <who> is whoever approves in this repo (any name). The words between
# the name and the colon are free ("in chat", "via a sign-off card"): a card answer is
# the owner's sign-off too, so the channel is not part of the wording.
CHAT_RX = re.compile(r"confirmed by [^\n:]{1,100}?:\s*\S", re.I)
BEADS_RX = re.compile(r"^beads:\s*([A-Za-z0-9][\w.-]*)\s*$", re.M)
# A milestone states its entry and exit conditions in plain words. Dates are for a defined
# test period only; `target:` is optional and names that dated window, not a deadline.
STARTS_RX = re.compile(r"^starts_when:\s*(.+?)\s*$", re.M)
EXITS_RX = re.compile(r"^exits_when:\s*(.+?)\s*$", re.M)
TARGET_RX = re.compile(r"^target:\s*(\d{4}-\d{2}-\d{2})\s*$", re.M)
INBOX_DATE = re.compile(r"^- (\d{4}-\d{2}-\d{2})\b")
# A roadmap bullet whose whole text is one of these reads as a bare reference, not plain words:
# a PR/issue number, a prefixed number, a bare URL, or a hyphenated id carrying a digit (fx-e1).
# Milestone ids (m01) have no hyphen and no leading digit, so they never match.
BARE_REF = re.compile(r"(?:#?\d+|(?:pr|issue|gh|bd)-?#?\d+|https?://\S+|[a-z][a-z0-9]*-[a-z0-9]*\d[a-z0-9]*)",
                      re.I)
BD_MARKERS = ("BEGIN BEADS INTEGRATION", "BEGIN BEADS CODEX SETUP")
AGENT_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")
HUMAN_LABELS = {"needs-human", "human"}
# The planning gate: every milestone, epic and Beads item is planned, needs-planning or not-planned.
# Agents pick up only planned work. A milestone or epic file says so in a `plan:` line; a Beads item
# in a `plan:<state>` label. Untagged inherits from its parent (task -> epic -> milestone); an
# untagged bug is planned; anything still untagged needs planning, so the default is safe.
PLAN_STATES = ("planned", "needs-planning", "not-planned")
PLAN_LABEL = "plan:"
PLAN_RX = re.compile(r"^plan:[ \t]*(.*?)[ \t]*$", re.M)  # any value, even blank (blank is invalid)
PLAN_DEFAULT = "needs-planning"
PLAN_SECTION_RX = re.compile(r"^needs\b.*\bplanning$", re.I)  # "## Needs planning", "## Needs <owner>'s planning"
PLANNED_KINDS = {"epic", "feature"}  # the Beads types the planning list and the label check cover
IDLE_DAYS = 14
INBOX_DAYS = 30
READY_SHOWN = 5

TEMPLATE_DIR = HERE / "templates" / "roadmap" if (HERE / "templates" / "roadmap").is_dir() else HERE / "templates"
TEMPLATES = {VISION: "VISION.md", ROADMAP: "ROADMAP.md", STOP: "STOP-AND-ASK.md",
             RM + "templates/milestone-SPEC.md": "milestone-SPEC.md", RM + "templates/epic.md": "epic.md"}
HISTORY_TEMPLATE = "HISTORY.md"  # laid down by the first retire or prune; never a required file

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
    """The level-3 part of the roadmap: what sits under Now, Next and Later."""
    secs = h2_sections(strip_status(roadmap_text))
    return "\n".join(f"{s}:\n{meat(secs.get(s, ''))}" for s in ORDER_SECTIONS)


def human_view(roadmap_text: str) -> str:
    """The roadmap outside the Inbox, the needs-planning list and the generated block (the
    ruling-paired part). Anyone may add to those two lists without a DECISIONS entry."""
    text = strip_status(roadmap_text)
    secs = h2_sections(text)
    head = text.split("\n## ", 1)[0]
    return meat(head) + "\n" + "\n".join(f"{k}:\n{meat(v)}" for k, v in sorted(secs.items())
                                         if k != "Inbox" and not PLAN_SECTION_RX.match(k))


def _bullet_text(line: str) -> Optional[str]:
    """The visible words of a bullet line: the leading marker, an Inbox date and markdown-link
    syntax removed. None when the line is not a bullet."""
    m = re.match(r"^\s*[-*+]\s+(.*\S)\s*$", line)
    if not m:
        return None
    t = re.sub(r"^\d{4}-\d{2}-\d{2}\s+", "", m.group(1))        # drop a leading Inbox date
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)               # [text](url) -> text
    return t.strip().strip(".,;:")


def roadmap_line_problems(roadmap_text: str) -> List[Tuple[str, str]]:
    """(line, token) for roadmap prose bullets whose whole text is a bare reference with no
    plain words (the "Writing for the reader" standard). The generated status block and fenced
    code are skipped; milestone ids like m01 never match BARE_REF."""
    out: List[Tuple[str, str]] = []
    fence = False
    for ln in strip_status(roadmap_text).splitlines():
        if re.match(r"^\s{0,3}(```|~~~)", ln):
            fence = not fence
            continue
        if fence:
            continue
        t = _bullet_text(ln)
        if t and BARE_REF.fullmatch(t):
            out.append((ln.strip(), t))
    return out


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


def plan_line(text: str) -> Optional[str]:
    """The raw `plan:` value of a milestone or epic file (None when it has no line). Two or more
    `plan:` lines come back as one invalid value, so they fail the check and resolve to needs-planning."""
    vals = PLAN_RX.findall(_strip_comments(text or ""))
    if not vals:
        return None
    return vals[0] if len(vals) == 1 else "+".join(vals)


def _valid_plan(v: Optional[str]) -> Optional[str]:
    return v if v in PLAN_STATES else None


def _first_valid(vals: List[str]) -> Optional[str]:
    return next((v for v in vals if v in PLAN_STATES), None)


def _explicit(raw: Optional[str]) -> Optional[str]:
    """A file's explicit `plan:` value, failing safe: absent -> None (inherit), valid -> itself,
    anything else -> needs-planning (a typo never falls through to a label or a parent)."""
    if raw is None:
        return None
    return raw if raw in PLAN_STATES else PLAN_DEFAULT


def _explicit_labels(vals: List[str]) -> Optional[str]:
    """A Beads row's explicit plan labels, failing safe: none -> None (inherit), one valid state ->
    itself, an unknown state or two different states -> needs-planning."""
    if not vals:
        return None
    states = set(vals)
    return vals[0] if len(states) == 1 and vals[0] in PLAN_STATES else PLAN_DEFAULT


def row_plans(row: dict) -> List[str]:
    """Every `plan:<state>` label on a Beads row, raw (more than one, or an unknown state, is a problem)."""
    return [lb[len(PLAN_LABEL):] for lb in (row.get("labels") or []) if str(lb).startswith(PLAN_LABEL)]


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


def vd(root=None) -> str:
    """The repo's vendor_dir (docs.json), for the commands this module prints; tools/docs by default."""
    try:
        return config.vendor_dir_of(Path(root)) if root is not None else config.DEFAULT_VENDOR_DIR
    except Exception:  # an unreadable docs.json is reported elsewhere; the hint stays the default
        return config.DEFAULT_VENDOR_DIR


def template_text(rel: str, root=None) -> str:
    text = (TEMPLATE_DIR / TEMPLATES[rel]).read_text(encoding="utf-8")
    return text.replace("tools/docs/", vd(root) + "/")


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
        self.parent: Dict[str, str] = {}
        self.blockers: Dict[str, List[str]] = {}
        for rid, r in self.rows.items():
            for d in r.get("dependencies") or []:
                if d.get("type") == "parent-child":
                    self.children.setdefault(d.get("depends_on_id"), []).append(rid)
                    self.parent.setdefault(rid, d.get("depends_on_id"))
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
            # A retired milestone keeps its SPEC after it leaves the README (prune moves its Shipped line to
            # HISTORY.md), so its own status line says where it went.
            state = STATE_LINE.search(_strip_comments(text))
            own_in = next((sec.lower() for sec in ORDER_SECTIONS
                           if any(_own_id(t) == mid for _i, _e, t in _top_bullets(secs.get(sec, "")))), None)
            if state and state.group(2).lower() in RETIRE_HOWS:
                # The SPEC's own word wins over a bullet that merely mentions it ("m01 depends on m02" must not
                # put a retired m02 back in Now). A bullet that is the milestone's own keeps it listed, so the
                # stale entry is still reported rather than hidden.
                place = own_in or ("shipped" if state.group(2).lower() == "shipped" else "retired")
            tm = TARGET_RX.search(text)
            sw = STARTS_RX.search(text)
            ew = EXITS_RX.search(text)
            epics = []
            for q in self.paths:
                if q.startswith(folder + "/epics/") and EPIC_RX.match(q):
                    et = read(q) or ""
                    est = STATE_LINE.search(_strip_comments(et))
                    epics.append({"path": q, "title": title_of(et, q.rsplit("/", 1)[-1][:-3]),
                                  "bd": beads_ref(et), "plan_raw": plan_line(et),
                                  "retired": bool(est and est.group(2).lower() in RETIRE_HOWS)})
            title = re.sub(rf"^{re.escape(mid)}\s*[:.-]\s*", "", title_of(text, mid), flags=re.I) or mid
            self.milestones.append({"id": mid, "path": p, "title": title, "plan_raw": plan_line(text),
                                    "target": tm.group(1) if tm else None,
                                    "starts_when": sw.group(1) if sw else None,
                                    "exits_when": ew.group(1) if ew else None,
                                    "place": place, "epics": epics})
        order = {"now": 0, "next": 1, "later": 2, "unplaced": 3, "shipped": 4, "retired": 5}
        self.milestones.sort(key=lambda x: (order[x["place"]], x["id"]))
        # planning gate: resolve milestone, then epic file, then Beads row (parent first)
        self.epic_of_row: Dict[str, Tuple[dict, dict]] = {}
        for ms in self.milestones:
            ms["plan"] = _explicit(ms["plan_raw"]) or PLAN_DEFAULT
            for ep in ms["epics"]:
                own = _explicit(ep["plan_raw"])
                if own is None and ep["bd"] in self.rows:
                    own = _explicit_labels(row_plans(self.rows[ep["bd"]]))
                ep["plan"] = own or ms["plan"]
                if ep["bd"] in self.rows:
                    self.epic_of_row.setdefault(ep["bd"], (ms, ep))
        self._plan_cache: Dict[str, str] = {}

    def plan(self, rid: str) -> str:
        """The effective planning state of a Beads row: its own label, else planned for a bug, else its
        parent's (the parent row, or the epic file it is linked from), else needs-planning."""
        if rid in self._plan_cache:
            return self._plan_cache[rid]
        self._plan_cache[rid] = PLAN_DEFAULT  # cycle guard
        r = self.rows.get(rid) or {}
        # a Beads epic linked from an epic file takes the file's resolved state, the same one the
        # planning list shows (the file's own line wins over the label; plan_problems fails a conflict)
        st = self.epic_of_row[rid][1]["plan"] if rid in self.epic_of_row else _explicit_labels(row_plans(r))
        if st is None and r.get("issue_type") == "bug":
            st = "planned"
        if st is None and self.parent.get(rid) in self.rows:
            st = self.plan(self.parent[rid])
        self._plan_cache[rid] = st or PLAN_DEFAULT
        return self._plan_cache[rid]

    def needs_planning(self) -> List[str]:
        """Plain lines for the topmost unshipped milestone, epic and open Beads epic or feature that
        still needs planning (a child of an item already listed is not listed again)."""
        out = []
        for ms in self.milestones:
            if ms["place"] in OUT_OF_ROADMAP:
                continue
            if ms["plan"] == "needs-planning":
                out.append(f"{ms['id']} {ms['title']} (milestone)")
                continue
            for ep in ms["epics"]:
                if ep["plan"] == "needs-planning" and not ep["retired"]:
                    out.append(f"{ep['title']} (epic in {ms['id']})")
        listed_files = {ep["bd"] for ms in self.milestones for ep in ms["epics"]}
        for rid in sorted(self.rows):
            r = self.rows[rid]
            if r.get("status") == "closed" or r.get("issue_type") not in PLANNED_KINDS or rid in listed_files:
                continue
            if self.plan(rid) != "needs-planning":
                continue
            par = self.rows.get(self.parent.get(rid) or "")
            if par and par.get("status") != "closed" and par.get("issue_type") in PLANNED_KINDS \
                    and self.plan(par["id"]) == "needs-planning":
                continue  # covered by its parent's line
            out.append(f"{rid} {r.get('title', '')}")
        return out

    def planning_section(self) -> List[str]:
        """The bullets of the human-written `## Needs ... planning` section of the roadmap."""
        for head, body in h2_sections(strip_status(self.roadmap)).items():
            if PLAN_SECTION_RX.match(head):
                return [ln for ln in _strip_comments(body).splitlines() if ln.startswith("- ")]
        return []

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
        """Open, unblocked, non-epic, planned Beads items under the milestone's epics, by (priority, id)."""
        if ms is None:
            return []
        ids = set()
        for ep in ms["epics"]:
            if ep["retired"]:
                continue
            if ep["bd"] in self.rows:
                ids.update(self.descendants(ep["bd"]))
        out = []
        for rid in ids:
            r = self.rows.get(rid)
            if not r or r.get("status") != "open" or r.get("issue_type") == "epic":
                continue
            if self.plan(rid) != "planned":
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
            if t and ms["place"] not in OUT_OF_ROADMAP and t < today:
                out.append(f'Milestone {ms["id"]} "{ms["title"]}" has a test window that has passed ({ms["target"]}).')
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
        return f"Not reviewed yet: run `{vd(root)}/roadmap.py reviewed` after the owner reviews the roadmap."
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
             f"_Generated by `{vd(root)}/roadmap.py status` from the milestone files and `.beads/issues.jsonl`. "
             "Never edit by hand._", ""]
    open_ms = [ms for ms in model.milestones if ms["place"] not in OUT_OF_ROADMAP]
    if open_ms:
        lines += ["| Milestone | Status | Starts when | Epics |", "|---|---|---|---|"]
        for ms in open_ms:
            starts = (ms["starts_when"] or "-").replace("|", "\\|")
            lines.append(f"| {ms['id']} {ms['title']} | {ms['place']} | {starts} | {len(ms['epics'])} |")
    else:
        lines.append("No open milestones." if model.milestones else "No milestones yet.")
    cur = model.current()
    for ms in model.milestones:
        if ms["place"] in OUT_OF_ROADMAP or not ms["epics"]:
            continue
        lines += ["", f"### {ms['id']} {ms['title']} ({ms['place']})", ""]
        for ep in ms["epics"]:
            if ep["retired"]:
                continue
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
    np, ideas = model.needs_planning(), model.planning_section()
    lines += ["", "Needs planning before any agent picks it up: " + ("; ".join(np) if np else "nothing.")]
    if ideas:
        lines.append(f"Ideas waiting in the planning list: {len(ideas)}.")
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
    """(current roadmap text, regenerated text)."""
    model = model_for(root, cfg)
    cur = model.roadmap
    return cur, with_block(cur, status_block(Path(root), model, today))


# ======================================================================== plugin: scan / triage

def planning_shaped(path: str, head: str, vendored: Tuple[str, ...] = (VENDORED,)) -> bool:
    if path.startswith(RM):
        return True
    if path.startswith(("work/",) + tuple(vendored)) or RECORD_DIR.search(path):
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
    vendored = tuple({VENDORED, config.vendor_dir_of(Path(repo.root)) + "/"})
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
        if not planning_shaped(path, head, vendored):
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


def plan_problems(paths: Iterable[str], read: Callable[[str], Optional[str]],
                  rows: Dict[str, dict]) -> List[Tuple[str, str, str, Optional[str]]]:
    """(path, message, kind, beads id or None) for the planning gate. kind "bad" is an unknown or
    doubled state (always a failure); "missing" is an unshipped milestone, an epic or an open Beads
    epic or feature with no state of its own (the caller decides fail or warn)."""
    out = []
    states = ", ".join(PLAN_STATES)
    shipped = set()
    roadmap = read(ROADMAP) or ""
    shipped_body = meat(h2_sections(strip_status(roadmap)).get("Shipped", ""))
    labelled_epics = set()
    for p in sorted(paths):
        spec = SPEC_RX.match(p)
        if not (spec or EPIC_RX.match(p)):
            continue
        text = read(p) or ""
        raw = plan_line(text)
        if spec and re.search(rf"(?<![\w-]){re.escape(spec.group(1))}(?![\w])", shipped_body):
            shipped.add(p.rsplit("/", 1)[0])
        if raw is not None and raw not in PLAN_STATES:
            msg = (f"more than one `plan:` line ({raw.replace('+', ', ')}); keep exactly one" if "+" in raw
                   else f"unknown `plan: {raw}` (use one of {states})")
            out.append((p, msg, "bad", None))
        elif raw is None:
            rid = beads_ref(text) if not spec else None
            if rid in rows and _first_valid(row_plans(rows[rid])):
                labelled_epics.add(rid)
                continue
            folder = p.rsplit("/", 1)[0] if spec else p.split("/epics/", 1)[0]
            if folder in shipped:
                continue
            what = "milestone" if spec else "epic"
            out.append((p, f"{what} has no `plan:` line ({states}); untagged reads as needs-planning",
                        "missing", None))
        elif not spec:
            rid = beads_ref(text)
            if rid:
                labelled_epics.add(rid)
                label = _first_valid(row_plans(rows.get(rid) or {}))
                if label and label != raw:
                    out.append((p, f"`plan: {raw}` disagrees with Beads epic {rid}'s label plan:{label}; "
                                   "make them match", "bad", None))
    for rid, r in sorted(rows.items()):
        got = row_plans(r)
        bad = [g for g in got if g not in PLAN_STATES]
        if bad:
            out.append((EXPORT, f"Beads item {rid} has unknown label plan:{bad[0]} (use one of {states})",
                        "bad", rid))
        elif len(got) > 1:
            out.append((EXPORT, f"Beads item {rid} has more than one plan label ({', '.join(got)})", "bad", rid))
        elif not got and r.get("status") != "closed" and r.get("issue_type") in PLANNED_KINDS \
                and rid not in labelled_epics:
            out.append((EXPORT, f"Beads {r.get('issue_type')} {rid} has no plan label ({states}); untagged "
                                "reads as needs-planning", "missing", rid))
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


def _all_verified_shipped(ctx, ids: List[str]) -> bool:
    """Every milestone in `ids` has all its epics' Beads epics closed and its exit criteria ticked."""
    model = Model(_ctx_paths(ctx), ctx.read, ctx.cfg)
    by_id = {m["id"]: m for m in model.milestones}
    return all(i in by_id and not ship_problems(model, by_id[i]) for i in ids)


def human_layer_changes(ctx) -> List[Tuple[str, int]]:
    """[(path, level)] where level 3 = human only (vision, milestone order, success criteria)."""
    out = []
    for st, p, old in ctx.changes:
        for q in sorted({p, old} - {None}):
            before = ctx.read_before(q) if st != "A" or q != p else None
            after = ctx.read(q) if q == p and st != "D" else None
            if q in TEMPLATES and before is None and after == template_text(q, ctx.root):
                continue  # the untouched template laid down by init
            if q == VISION:
                if (before or "") != (after or ""):
                    out.append((q, 3))
            elif q == ROADMAP:
                if order_view(before or "") != order_view(after or ""):
                    moved = shipped_only_move(before or "", after or "")
                    if moved and _all_verified_shipped(ctx, moved):
                        out.append((q, 2))  # retire --as shipped: verified done, so a dated entry is enough
                    else:
                        out.append((q, 3))
                elif human_view(before or "") != human_view(after or ""):
                    if not shipped_pure_removal(before or "", after or "", ctx.read(HISTORY) or ""):
                        out.append((q, 2))
            elif SPEC_RX.match(q):
                if before is not None and after is not None and \
                        meat(section_named(before, "success") or "") != meat(section_named(after, "success") or ""):
                    out.append((q, 3))
                elif (before or "") != (after or ""):
                    out.append((q, 2))
    return out


def planned_transitions(ctx, paths: List[str]) -> List[str]:
    """Paths whose change makes something planned that was not planned before: level 3, so the
    change needs a dated DECISIONS entry citing the owner's chat. Covers milestones, epic files and
    Beads epics and features. Milestones match by id and epics by Beads id (else path), so a rename
    alone is no transition. A task (or other non-epic, non-feature row) is exempt when its nearest
    ancestor that existed before the change was planned then (splitting a planned epic, at any
    depth, is level 1), and a bug is exempt (bugs are planned by default)."""
    olds = {old for _st, _p, old in ctx.changes if old}
    before_paths = [q for q in sorted(set(paths) | olds) if ctx.read_before(q) is not None]
    after = Model(paths, ctx.read, ctx.cfg)
    before = Model(before_paths, ctx.read_before, ctx.cfg)
    key = lambda ep: ("bd", ep["bd"]) if ep["bd"] else ("path", ep["path"])  # noqa: E731
    was = {("ms", ms["id"]): ms["plan"] for ms in before.milestones}
    was.update({("bd", rid): before.plan(rid) for rid in before.rows})  # an epic row that predates its file
    was.update({key(ep): ep["plan"] for ms in before.milestones for ep in ms["epics"]})
    out = set()
    for ms in after.milestones:
        nodes = [(("ms", ms["id"]), ms["path"], ms["plan"])] + \
                [(key(ep), ep["path"], ep["plan"]) for ep in ms["epics"]]
        for k, p, st in nodes:
            if st == "planned" and was.get(k) != "planned":
                out.add(p)
    for rid, r in after.rows.items():
        if r.get("issue_type") == "bug" or after.plan(rid) != "planned":
            continue
        if rid in before.rows and before.plan(rid) == "planned":
            continue
        if r.get("issue_type") not in PLANNED_KINDS and rid not in after.epic_of_row and rid not in before.rows:
            anc, seen = after.parent.get(rid), {rid}
            while anc in after.rows and anc not in before.rows and anc not in seen \
                    and after.rows[anc].get("issue_type") not in PLANNED_KINDS:
                seen.add(anc)
                anc = after.parent.get(anc)
            if anc in before.rows and before.plan(anc) == "planned":
                continue
        out.add(after.epic_of_row[rid][1]["path"] if rid in after.epic_of_row else EXPORT)
    return sorted(out)


def run_checks(ctx):
    """Structural roadmap checks (spec "Roadmap checker rules"); fail level unless noted."""
    behind = []
    if LEGACY_ROADMAP in ctx.tracked and ROADMAP not in ctx.changed("AMR"):
        if ctx.scope != "pr" or LEGACY_ROADMAP in ctx.changed("ACMRT"):
            return [{"check": "roadmap", "path": LEGACY_ROADMAP, "line": None, "level": "fail",
                     "message": f"the roadmap lives at {ROADMAP} since layout 2; run `{vd(ctx.root)}/docs.py upgrade`"}]
        # a PR that never touched the old file still has it only because its branch predates the
        # upgrade on its base, so its roadmap is the old layout: a note, never a failure. A PR that
        # changes no roadmap file (the layout files, a milestone or epic, the Beads export) stops here;
        # one that does still gets every check. Other files that happen to sit under docs/roadmap/
        # (plan folders, generated data) are not roadmap files.
        behind = [{"check": "roadmap", "path": LEGACY_ROADMAP, "line": None, "level": "warn",
                   "message": f"this branch predates the move of the roadmap to {ROADMAP}; merge the base "
                              "branch to pick it up"}]
        if not any(c in REQUIRED_FILES or c == EXPORT or c.startswith(RM + "milestones/")
                   for c in ctx.changed("ACMRDT")):
            return behind
    if not _active_in(ctx):
        return behind
    res = lambda path, msg, level="fail": {"check": "roadmap", "path": path, "line": None,  # noqa: E731
                                           "message": msg, "level": level}
    out = list(behind)
    excl = ctx.cfg.get("inventory_exclude") or []
    diff = ctx.scope != "all"
    paths = _ctx_paths(ctx)
    changed = set(ctx.changed("ACMR")) if diff else set(paths) | set(ctx.tracked)
    # required layout files and the generated block's markers
    for rel in REQUIRED_FILES:
        if ctx.read(rel) is None:
            out.append(res(rel, f"required roadmap file missing (run `{vd(ctx.root)}/docs.py init --stage migration`)"))
    rm = ctx.read(ROADMAP) or ""
    if STATUS_OPEN not in rm or STATUS_CLOSE not in rm:
        out.append(res(ROADMAP, f"no generated status block markers (run `{vd(ctx.root)}/roadmap.py status`)"))
    # layout version
    if int(ctx.cfg.get("layout_version", 1)) < LAYOUT_VERSION:
        out.append(res("docs.json", f"layout_version {ctx.cfg.get('layout_version')} is older than the vendored "
                                    f"roadmap layout {LAYOUT_VERSION}; run `{vd(ctx.root)}/docs.py upgrade`"))
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
    # planning gate: every state is a known one; a changed milestone, epic or Beads epic/feature must
    # carry one (a PR failure); an old untagged one is a repo-wide warning (it reads as needs-planning)
    touched_rows = None
    if diff:
        old = parse_export(ctx.read_before(EXPORT)) if EXPORT in changed else rows
        touched_rows = {rid for rid, r in rows.items() if old.get(rid) != r}
    for p, msg, kind, rid in plan_problems(paths, ctx.read, rows):
        touched = (p in changed) if rid is None else (touched_rows is None or rid in touched_rows)
        if kind == "bad":
            out.append(res(p, msg))
        elif not diff:
            out.append(res(p, msg, "warn"))
        elif touched:
            out.append(res(p, msg))
    # ruling pairing (diff scopes only: a repo-wide pass has no change to pair)
    if diff:
        hl = human_layer_changes(ctx) + [(p, 3) for p in planned_transitions(ctx, paths)]
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
                                      f"{DECISIONS} entry must cite the sign-off (\"confirmed by <who> in chat: "
                                      "<where>\" or \"confirmed by <who> via <card>: <where>\")"))
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
            out.append(res(ROADMAP, f"status block is stale; run `{vd(ctx.root)}/roadmap.py status`", "warn"))
        for f in model_for(ctx.root, ctx.cfg).flags(today):
            out.append(res(ROADMAP, f, "warn"))
        # reader warning: a roadmap line that is only a bare id or number, no plain words
        for w in reader_warnings(rm, ctx.cfg):
            out.append(res(ROADMAP, w, "warn"))
        for line, tok in roadmap_line_problems(rm):
            out.append(res(ROADMAP, f'roadmap line "{line}" is only a reference ({tok}); say what it is '
                                    "in plain words (an id can go in parentheses after a description)", "warn"))
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
    """The repo's slug (its GitHub name, or seed alias), never the checkout folder: a migration usually runs
    in a linked worktree, whose folder name (docs-bootstrap) once became every Beads id's prefix."""
    from docslib import initrepo  # lazy: initrepo is only needed here
    return re.sub(r"[^a-z0-9]+", "-", initrepo.repo_slug(Path(repo)).lower()).strip("-")[:12] or "rm"


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
        done = [f"Beads: initialised a new DB at {where['path']} (prefix {_prefix(repo)})"]
        if not Path(where["path"]).resolve().is_relative_to(repo.resolve()):
            # bd keeps one DB per clone: from a linked worktree it always lands in the main checkout
            done.append(f"Beads: note: the DB is in the clone's main checkout, not this worktree (bd shares one "
                        f"DB across worktrees); it is untracked there")
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
    if (repo / LEGACY_ROADMAP).is_file():
        return [f"{LEGACY_ROADMAP} is the layout 1 roadmap; run `{vd(repo)}/docs.py upgrade` instead"]
    done = []
    for rel in TEMPLATES:
        fp = repo / rel
        if not fp.exists():
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(template_text(rel, repo), encoding="utf-8")
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
    fingerprint (op + path + input hash); a fingerprint the owner rejected is held back, not written."""
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
    # drift is a signal for the daily PR body, never a change and never a failure of the run
    notes = getattr(m, "notes", None)
    if isinstance(notes, list):
        try:
            found, skipped = drift(root, model_for(root, m.cfg), m.cfg, m.today, time.time())
        except Exception as exc:  # noqa: BLE001
            notes.append(f"Roadmap drift check could not run: {str(exc).strip()[:200]}")
        else:
            notes.extend(f"Roadmap drift: {f['message']} Fix: {f['fix']}." for f in found)
            notes.extend(f"Roadmap drift skipped a check: {n}" for n in skipped)
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


def _move(repo: Path, src: str, dst: str) -> None:
    """`git mv` when the file is tracked (history follows it), a plain rename otherwise."""
    (repo / dst).parent.mkdir(parents=True, exist_ok=True)
    if _git(repo, "ls-files", "--error-unmatch", src).returncode == 0:
        p = _git(repo, "mv", src, dst)
        if p.returncode != 0:
            raise RuntimeError(f"git mv {src} {dst} failed: {p.stderr.strip()}")
    else:
        (repo / src).rename(repo / dst)


def _upgrade_1_to_2(repo: Path) -> List[str]:
    """Layout 2: the roadmap becomes docs/roadmap/README.md, the folder's landing page. A README.md
    already there that is not the roadmap is archived first, never deleted or overwritten."""
    if not (repo / LEGACY_ROADMAP).is_file():
        return []
    done = []
    if (repo / ROADMAP).exists():
        dest = f"work/archive/{dt.date.today().year}/legacy/{ROADMAP}"
        if (repo / dest).exists():
            raise RuntimeError(f"cannot archive {ROADMAP}: {dest} already exists")
        _move(repo, ROADMAP, dest)
        done.append(f"{ROADMAP} archived to {dest}")
    _move(repo, LEGACY_ROADMAP, ROADMAP)
    done.append(f"{LEGACY_ROADMAP} -> {ROADMAP}")
    return done


UPGRADES: Dict[int, Callable[[Path], List[str]]] = {1: _upgrade_1_to_2}  # {from_version: step}


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


# ======================================================================== drift (no model)
# `roadmap.py drift`: has the planning outrun the roadmap? Code matches ids; it never interprets text.
# docs.json `roadmap`:
#   drift_since    YYYY-MM-DD; sources dated before it are not checked (default: the last DRIFT_DAYS days)
#   drift_sources  [{"kind": "rulings" | "talk-throughs", "path": ...}]: a rulings file (lines
#                  `- R-YYYY-MM-DD-NN ...`, or table rows `| R-YYYY-MM-DD-NN | ...` as in docs/rulings.md) or a folder of `NN-*-DECISIONS.md` files; relative to the
#                  repo or absolute, with ~ and $VARS expanded. An unreadable source is a note, not a failure.
#   signoff_days   days an epic may cite a ruling or talk-through and still be needs-planning (default SIGNOFF_DAYS)
#   drift_repo     this repo's name for `roadmap:` tags (default: the origin remote's repo name)
#   drift_skip     subject regexes for merged PRs that need no epic (default DRIFT_SKIP)
#   issue_labels   labels that put an open GitHub issue on the roadmap's books (default ["triaged"])
#   issue_grace_days  days such an issue may be open before an uncited one is a finding (default 2)
# A ruling line or a talk-through file tagged `roadmap: none` is not roadmap work; `roadmap: <repo>[, <repo>]`
# names the repos whose roadmap must carry it. Untagged means every repo that lists the source.

DRIFT_DAYS = 7
SIGNOFF_DAYS = 2
RULING_DATE = re.compile(r"R-(\d{4}-\d{2}-\d{2})-\d+")
EXPORT_MAX_AGE_H = 24
RANGE_MAX = 200
# A ledger line `- R-... text` or a table row `| R-... | date | ... |` (the repo-tracked docs/rulings.md).
RULING_LINE = re.compile(r"^(?:- |\| )(R-(\d{4}-\d{2}-\d{2})-\d+)\b(.*)$", re.M)
RULING_REF = re.compile(r"R-(\d{4}-\d{2}-\d{2})-(\d+)((?:\s*(?:,\s*and|,|and|to)\s*-\d+(?![\d-]))*)")
RULING_STEP = re.compile(r"(,\s*and|,|and|to)\s*-(\d+)")
PR_REF = re.compile(r"#(\d+)((?:\s*(?:,\s*and|,|and|to)\s*#\d+)*)")
PR_STEP = re.compile(r"(,\s*and|,|and|to)\s*#(\d+)")
TT_REF = re.compile(r"talk-?throughs?[ /]+(\d{1,2})\b(?:\s*to\s*(\d{1,2})\b)?", re.I)
TT_FILE = re.compile(r"^(\d{1,2})-.*DECISIONS\.md$")
# the tag is the line's last `| roadmap: ...` field, so prose like "roadmap:sync" never reads as one
RULING_TAG = re.compile(r"\|\s*roadmap:\s*([\w.-]+(?:\s*,\s*[\w.-]+)*)\s*\|?\s*$", re.I)
FILE_TAG = re.compile(r"^roadmap:\s*([\w.-]+(?:\s*,\s*[\w.-]+)*)\s*$", re.I | re.M)
HEAD_DATE = re.compile(r"\((\d{4}-\d{2}-\d{2})\)")
SUBJECT_PR = re.compile(r"\(#(\d+)\)\s*$")
DRIFT_SKIP = (r"^docs\(roadmap\)", r"^chore\(release\)", r"^release\b")
UNPLANNED_RX = re.compile(r"\b(still unplanned|not planned)\b", re.I)
ROW_TEXT = ("title", "description", "notes", "design", "acceptance_criteria", "external_ref")


def _expand(first: int, tail: str, step: re.Pattern) -> List[int]:
    """`146 to -149` -> 146..149; `198, -199, and -200` -> 198, 199, 200 (a `to` span is capped)."""
    out, prev = [first], first
    for op, n in step.findall(tail):
        n = int(n)
        if op.strip() == "to" and prev < n <= prev + RANGE_MAX:
            out.extend(range(prev + 1, n + 1))
        else:
            out.append(n)
        prev = n
    return out


def ruling_refs(text: str) -> set:
    return {f"R-{m.group(1)}-{n:02d}" for m in RULING_REF.finditer(text or "")
            for n in _expand(int(m.group(2)), m.group(3), RULING_STEP)}


def pr_refs(text: str) -> set:
    return {n for m in PR_REF.finditer(text or "") for n in _expand(int(m.group(1)), m.group(2), PR_STEP)}


def tt_refs(text: str) -> set:
    out = set()
    for m in TT_REF.finditer(text or ""):
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        out.update(range(a, b + 1) if a <= b <= a + RANGE_MAX else [a])
    return out


def _tags(m: Optional[re.Match]) -> Optional[set]:
    return {t.strip().lower() for t in m.group(1).split(",")} if m else None


def _belongs(tags: Optional[set], repo: str) -> bool:
    return tags is None or ("none" not in tags and repo in tags)


def drift_corpus(model: Model) -> str:
    """Everything a source id can be filed in: the roadmap (status block aside), DECISIONS, HISTORY
    (where a pruned Shipped entry and a retired epic keep their citations), every milestone SPEC and
    epic file, and the text fields of every Beads row."""
    parts = [strip_status(model.roadmap), model.read(DECISIONS) or "", model.read(HISTORY) or ""]
    parts += [model.read(p) or "" for p in model.paths
              if SPEC_RX.match(p) or EPIC_RX.match(p) or p.startswith(RM + "plans/")]
    for r in model.rows.values():
        parts += [str(r.get(k) or "") for k in ROW_TEXT]
    return "\n".join(parts)


def _drift_repo(root: Path, rcfg: dict) -> str:
    if rcfg.get("drift_repo"):
        return str(rcfg["drift_repo"]).lower()
    url = _git(root, "remote", "get-url", "origin").stdout.strip()
    return (re.sub(r"\.git$", "", url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]) or Path(root).name).lower()


def _source_path(root: Path, p: str) -> Path:
    q = Path(os.path.expandvars(os.path.expanduser(str(p))))
    return q if q.is_absolute() else Path(root) / q


def _since(rcfg: dict, today: dt.date) -> dt.date:
    return _date(rcfg.get("drift_since")) or today - dt.timedelta(days=DRIFT_DAYS)


def _merged_prs(root: Path, since: dt.date) -> List[Tuple[int, str]]:
    """(PR number, subject) for squash-merged commits on HEAD since the date (subject ends `(#N)`)."""
    p = _git(root, "log", "--no-merges", f"--since={since.isoformat()} 00:00", "--format=%s", "HEAD")
    out = []
    for subj in p.stdout.splitlines() if p.returncode == 0 else []:
        m = SUBJECT_PR.search(subj)
        if m:
            out.append((int(m.group(1)), subj))
    return out


def _live_beads(root: Path) -> Optional[List[dict]]:
    where = migrate.beads_where(root)
    return None if where is None else migrate.beads_export(where, root)


def _export_age_h(root: Path, now_ts: float) -> Optional[float]:
    """Hours since the export was last written: its commit time, or its mtime when edited since."""
    fp = Path(root) / EXPORT
    if not fp.is_file():
        return None
    head = _git(root, "show", f"HEAD:{EXPORT}")
    if head.returncode == 0 and head.stdout == fp.read_text(encoding="utf-8"):
        ct = _git(root, "log", "-1", "--format=%ct", "--", EXPORT).stdout.strip()
        if ct.isdigit():
            return (now_ts - int(ct)) / 3600
    return (now_ts - fp.stat().st_mtime) / 3600


def _talk_through_days(root: Path, rcfg: dict) -> dict:
    """{talk-through number: date} from the talk-through sources (header date, else file mtime)."""
    out = {}
    for src in rcfg.get("drift_sources") or []:
        if (src or {}).get("kind") != "talk-throughs":
            continue
        sp = _source_path(root, (src or {}).get("path") or "")
        if not sp.is_dir():
            continue
        for f in sorted(sp.iterdir()):
            m = TT_FILE.match(f.name)
            if not m or not f.is_file():
                continue
            hd = HEAD_DATE.search("\n".join(f.read_text(encoding="utf-8", errors="replace").splitlines()[:5]))
            out[int(m.group(1))] = (_date(hd.group(1)) if hd else None) or dt.date.fromtimestamp(f.stat().st_mtime)
    return out


ISSUE_REF = re.compile(
    r"(?:(?<![\w/.-])(?P<q>(?:[\w.-]+/)?[A-Za-z][\w.-]*)\s?#|(?<![\w&])#)(?P<n>\d+)\b"
    r"|\bgh-(?P<g>\d+)\b"
    r"|(?:github\.com/[\w.-]+/(?P<ur>[\w.-]+)/)?issues/(?P<u>\d+)\b")
SIBLING_REPOS: tuple = ()  # the other repos whose `<repo>#N` is no citation here: `roadmap.sibling_repos` in docs.json
ISSUE_LABELS = ["triaged"]
ISSUE_GRACE_DAYS = 2


def issue_refs(text: str, repo: str = "", siblings=SIBLING_REPOS) -> set:
    """Issue numbers a text cites for `repo`: #N, gh-N or an issue URL name this repo. `<repo>#N` and
    `<repo> #N` count only when <repo> is this repo; another repo's `#N` is not a citation here."""
    repo = repo.lower()
    others = {str(x).lower() for x in siblings} - {repo}
    out = set()
    for m in ISSUE_REF.finditer(text):
        if m.group("n"):
            q = (m.group("q") or "").lower().rsplit("/", 1)[-1]
            if q in others:
                continue
            out.add(int(m.group("n")))
        elif m.group("g"):
            out.add(int(m.group("g")))
        else:
            if m.group("ur") and repo and m.group("ur").lower() != repo:
                continue
            out.add(int(m.group("u")))
    return out


def drift(root: Path, model: Model, cfg: dict, today: dt.date, now_ts: float,
          merged: Optional[Callable[[Path, dt.date], List[Tuple[int, str]]]] = None,
          live_beads: Optional[Callable[[Path], Optional[List[dict]]]] = None,
          issues: Optional[Callable[[Path], List[dict]]] = None) -> Tuple[List[dict], List[str]]:
    """(findings, notes). A finding is something the roadmap owes: one dict with `check`, `id`,
    `message` and `fix`. A note is something the check could not look at."""
    root = Path(root)
    rcfg = cfg.get("roadmap") or {}
    since = _since(rcfg, today)
    repo = _drift_repo(root, rcfg)
    corpus = drift_corpus(model)
    cited, cited_tt, cited_pr = ruling_refs(corpus), tt_refs(corpus), pr_refs(corpus)
    findings: List[dict] = []
    notes: List[str] = []

    def find(check, fid, message, fix):
        findings.append({"check": check, "id": fid, "message": message, "fix": fix})

    # 1. a ruling or talk-through decision since the baseline that no roadmap row cites
    for src in rcfg.get("drift_sources") or []:
        kind, path = (src or {}).get("kind"), (src or {}).get("path")
        sp = _source_path(root, path or "")
        if kind == "rulings":
            if not sp.is_file():
                notes.append(f"Rulings source not readable here: {path}.")
                continue
            for m in RULING_LINE.finditer(sp.read_text(encoding="utf-8", errors="replace")):
                rid, day = m.group(1), _date(m.group(2))
                if day and day >= since and rid not in cited and _belongs(_tags(RULING_TAG.search(m.group(3))), repo):
                    find("unfiled-ruling", rid, f"{rid} ({day}) has no roadmap row.",
                         f"cite {rid} in an epic, SPEC, DECISIONS entry or Inbox line, or tag it "
                         f"`roadmap: none` (or the repos it belongs to) in the rulings file")
        elif kind == "talk-throughs":
            if not sp.is_dir():
                notes.append(f"Talk-through folder not readable here: {path}.")
                continue
            for f in sorted(sp.iterdir()):
                m = TT_FILE.match(f.name)
                if not m or not f.is_file():
                    continue
                text = f.read_text(encoding="utf-8", errors="replace")
                hd = HEAD_DATE.search("\n".join(text.splitlines()[:5]))
                day = _date(hd.group(1)) if hd else dt.date.fromtimestamp(f.stat().st_mtime)
                num = int(m.group(1))
                if day and day >= since and num not in cited_tt and f.stem not in corpus \
                        and _belongs(_tags(FILE_TAG.search(text)), repo):
                    find("unfiled-talk-through", f.name, f"Talk-through {f.name} ({day}) has no roadmap row.",
                         f"file its decisions as roadmap rows citing `talk-through {num:02d}`, or add a "
                         f"`roadmap: none` (or `roadmap: <repo>`) line to the file")
        else:
            notes.append(f"Unknown drift source kind {kind!r} ({path or ''}).")

    # 2. a Needs-planning bullet citing a source that a planned epic also cites
    planned_r, planned_tt = set(), set()
    for ms in model.milestones:
        for ep in ms["epics"]:
            if ep["plan"] == "planned":
                t = model.read(ep["path"]) or ""
                planned_r |= ruling_refs(t)
                planned_tt |= tt_refs(t)
        for ln in (h2_sections(model.read(ms["path"]) or "").get("Epics") or "").splitlines():
            if ln.lstrip().startswith("- ") and re.search(r"\bPlanned\b", ln) \
                    and not re.search(r"needs planning|not planned", ln, re.I):
                planned_r |= ruling_refs(ln)
                planned_tt |= tt_refs(ln)
    for b in model.planning_section():
        if UNPLANNED_RX.search(b):
            continue
        hits = sorted(ruling_refs(b) & planned_r) + [f"talk-through {n:02d}" for n in sorted(tt_refs(b) & planned_tt)]
        if hits:
            head = re.sub(r"\*", "", b[2:]).split(".")[0][:80]
            find("planned-in-planning-list", hits[0], f'Needs-planning item "{head}" cites {", ".join(hits)}, '
                 "which a planned epic also cites.",
                 "move it out of the planning list, or say `still unplanned` in the bullet if the overlap is intended")

    # 3a. a Now epic with nothing open or in progress under it
    for ms in model.milestones:
        if ms["place"] != "now":
            continue
        for ep in ms["epics"]:
            r = model.rows.get(ep["bd"] or "")
            if ep["plan"] != "planned" or not r or r.get("status") == "closed":
                continue
            if not any(model.rows[c].get("status") in ("open", "in_progress") for c in model.descendants(ep["bd"])):
                find("idle-now-epic", ep["bd"], f'Now epic "{ep["title"]}" ({ms["id"]}) has no open or in-progress task.',
                     f"file the next task under {ep['bd']} in Beads, or close the epic if it is done")

    # 3c. a row labelled planned under a parent that is not planned: its own label lets agents pick it
    # up although nobody planned the work it belongs to
    for rid, r in sorted(model.rows.items()):
        par = model.parent.get(rid)
        if r.get("status") == "closed" or par not in model.rows or rid in model.epic_of_row:
            continue
        if _explicit_labels(row_plans(r)) == "planned" and model.plan(par) != "planned":
            find("planned-under-unplanned", rid, f'"{r.get("title", "")}" ({rid}) is labelled planned, but its '
                 f"parent {par} is {model.plan(par)}.",
                 f"set {rid} to plan:needs-planning to match its parent, or plan {par} with a DECISIONS entry "
                 "if it was planned")

    # 3b. a PR merged in the last DRIFT_DAYS days (and since the baseline) that maps to no roadmap row
    skip = [re.compile(s) for s in (rcfg.get("drift_skip") or DRIFT_SKIP)]
    window = max(since, today - dt.timedelta(days=DRIFT_DAYS))
    for n, subj in (merged or _merged_prs)(root, window):
        if n not in cited_pr and not any(s.search(subj) for s in skip):
            find("unmapped-pr", f"#{n}", f"Merged PR #{n} maps to no roadmap row: {subj}",
                 f"cite #{n} in its epic, a Beads task, or a DECISIONS or Shipped line")

    # 3d. an epic whose plan or talk-through is decided (it cites a ruling or a talk-through) but is still
    # needs-planning after signoff_days: it is waiting on the owner's sign-off, not on more planning
    days = int(rcfg.get("signoff_days", SIGNOFF_DAYS))
    tt_day = _talk_through_days(root, rcfg)
    for ms in model.milestones:
        for ep in ms["epics"]:
            r = model.rows.get(ep["bd"] or "")
            if ep["plan"] != "needs-planning" or (r and r.get("status") == "closed"):
                continue
            text = model.read(ep["path"]) or ""
            if UNPLANNED_RX.search(text):
                continue
            rulings, tts = ruling_refs(text), tt_refs(text)
            dates = [d for d in (_date(RULING_DATE.search(x).group(1)) for x in rulings) if d]
            dates += [tt_day[n] for n in tts if n in tt_day]
            if not dates or today - max(dates) < dt.timedelta(days=days):
                continue
            cites = sorted(rulings) + [f"talk-through {n:02d}" for n in sorted(tts) if n in tt_day]
            find("awaiting-signoff", ep["bd"] or ep["path"],
                 f'Epic "{ep["title"]}" ({ms["id"]}) cites {", ".join(cites)} (latest {max(dates)}) but is still '
                 f"needs-planning after {(today - max(dates)).days} days: awaiting owner sign-off.",
                 "put it on a sign-off card to the owner (mark planned: yes or no), then file the DECISIONS entry the "
                 "same day; or say `still unplanned` in the epic file if it is meant to wait")

    # 5. an open GitHub issue carrying a triage label that no roadmap row cites
    want = {str(x).lower() for x in (rcfg.get("issue_labels") or ISSUE_LABELS)}
    grace = int(rcfg.get("issue_grace_days", ISSUE_GRACE_DAYS))
    try:
        snap = (issues or migrate.issues_snapshot)(root)
    except Exception as exc:  # noqa: BLE001  gh missing, offline or not logged in: a note, never a finding
        snap = []
        notes.append(f"GitHub issues not readable here, so untracked issues were not checked: {str(exc).strip()[:160]}")
    cited_issue = issue_refs(corpus, repo, rcfg.get("sibling_repos") or SIBLING_REPOS)
    for it in snap:
        got = {str(x).lower() for x in it.get("labels") or []}
        opened = _date((it.get("created_at") or "")[:10])
        if not (got & want) or opened is None or it["number"] in cited_issue:
            continue
        age = (today - opened).days
        if age > grace:
            find("untracked-issue", f"#{it['number']}", f'Open issue #{it["number"]} "{it.get("title", "")[:80]}" '
                 f"({', '.join(sorted(got & want))}) has been open {age} days and no roadmap row cites it.",
                 f"file it as a Beads task, an epic, a Needs-planning bullet or an Inbox line citing #{it['number']}, "
                 "or close it")

    # 4. the status block reads an export that is over a day old and no longer matches Beads
    if (root / EXPORT).is_file():
        rows = (live_beads or _live_beads)(root)
        if rows is None:
            notes.append("Beads is not reachable here, so the export age was not checked.")
        else:
            live = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
            age = _export_age_h(root, now_ts)
            if live != (root / EXPORT).read_text(encoding="utf-8") and age is not None and age > EXPORT_MAX_AGE_H:
                find("stale-export", EXPORT, f"The status block reads a Beads export {int(age)} hours old that "
                     "no longer matches Beads.", "refresh it (`bd export -o .beads/issues.jsonl`), run "
                     f"`{vd(root)}/roadmap.py status`, and commit both (the daily docs run does all three)")
    # 5. done work still listed, and a Shipped list past its window (the roadmap-removal standard)
    findings += retire_findings(root, model, today, cfg)
    # one finding per (check, id): a ruling listed in two sources is one row owed, not two
    seen = set()
    findings = [f for f in findings if (f["check"], f["id"]) not in seen and not seen.add((f["check"], f["id"]))]
    return findings, notes


# ======================================================================== retiring history (the roadmap-removal standard)
#
# The roadmap shows where we are going. A milestone or epic that is done leaves it through `retire`,
# and the Shipped list ages out through `prune`; both write docs/roadmap/HISTORY.md, newest first.

def _top_bullets(body: str) -> List[Tuple[int, int, str]]:
    """(first line, end line, text) for each top-level bullet of a section body: the bullet plus the
    indented or blank lines under it, up to the next top-level bullet."""
    lines = body.split("\n")
    starts = [i for i, ln in enumerate(lines) if re.match(r"^[-*+]\s", ln)]
    out = []
    for n, i in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        while end > i + 1 and not lines[end - 1].strip():
            end -= 1
        out.append((i, end, "\n".join(lines[i:end])))
    return out


def _section_span(text: str, heading: str) -> Optional[Tuple[int, int]]:
    """(first body line, end line) of the `## heading` section of the README, by line index."""
    lines = text.split("\n")
    fence, start = False, None
    for i, ln in enumerate(lines):
        if re.match(r"^\s{0,3}(```|~~~)", ln):
            fence = not fence
        m = None if fence else re.match(r"^##\s+(.+?)\s*#*\s*$", ln)
        if m:
            if start is not None:
                return start, i
            if m.group(1).strip().lower() == heading.lower():
                start = i + 1
    return (start, len(lines)) if start is not None else None


def _entry_date(first_line: str, today: dt.date) -> Optional[dt.date]:
    """The date a Shipped entry names: YYYY-MM-DD, or (MM-DD ...) in the current year (the year
    before when that would be in the future). None when it names none."""
    m = re.search(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", first_line)
    try:
        if m:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.search(r"\(\s*(\d{1,2})-(\d{1,2})(?!\d)", first_line)
        if m:
            d = dt.date(today.year, int(m.group(1)), int(m.group(2)))
            return d if d <= today else dt.date(today.year - 1, d.month, d.day)
    except ValueError:
        return None
    return None


def shipped_due(roadmap_text: str, today: dt.date, keep_days: int) -> List[Tuple[int, int, str, dt.date]]:
    """Shipped entries older than `keep_days`: (first line, end line, text, date), by README line index."""
    span = _section_span(roadmap_text, "Shipped")
    if span is None:
        return []
    lines = roadmap_text.split("\n")
    out = []
    for i, end, text in _top_bullets("\n".join(lines[span[0]:span[1]])):
        day = _entry_date(text.split("\n", 1)[0], today)
        if day is not None and (today - day).days > keep_days:
            out.append((span[0] + i, span[0] + end, text, day))
    return out


def _bullet_groups(lines: List[str]) -> List[List[str]]:
    groups: List[List[str]] = []
    for ln in lines:
        if re.match(r"^[-*+]\s", ln) or not groups:
            groups.append([ln])
        else:
            groups[-1].append(ln)
    return groups


def history_add(history_text: Optional[str], entries: List[str]) -> str:
    """HISTORY.md with `entries` (formatted bullets) merged in by date, newest first. An entry that shares
    a date with an older one goes above it; an undated bullet sorts last."""
    text = history_text if history_text is not None else (TEMPLATE_DIR / HISTORY_TEMPLATE).read_text(encoding="utf-8")
    lines = text.rstrip("\n").split("\n")
    first = next((i for i, ln in enumerate(lines) if re.match(r"^[-*+]\s", ln)), len(lines))
    head, rest = lines[:first], lines[first:]
    while head and not head[-1].strip():
        head.pop()
    groups = _bullet_groups([ln for entry in entries for ln in entry.split("\n")]) + \
        _bullet_groups([ln for ln in rest if ln.strip() or rest])
    groups = [g for g in groups if any(x.strip() for x in g)]

    def key(g):
        m = re.match(r"^[-*+]\s+(\d{4}-\d{2}-\d{2})", g[0])
        return m.group(1) if m else ""
    groups.sort(key=key, reverse=True)  # stable: new entries, listed first, stay above older ones of the same day
    body = [ln.rstrip() for g in groups for ln in g]
    return "\n".join(head + [""] + body).rstrip("\n") + "\n"


def prune_changes(root: Path, today: dt.date, keep_days: int) -> Tuple[Dict[str, str], List[str]]:
    """({path: new text}, plain lines) for `prune`: Shipped entries past the window go to HISTORY.md."""
    root = Path(root)
    rd = file_reader(root)
    text = rd(ROADMAP) or ""
    due = shipped_due(text, today, keep_days)
    if not due:
        return {}, ["prune: nothing older than %d days in Shipped" % keep_days]
    lines = text.split("\n")
    drop = set()
    for i, end, _t, _d in due:
        drop.update(range(i, end))
    kept = [ln for j, ln in enumerate(lines) if j not in drop]
    entries = []
    for _i, _e, t, day in sorted(due, key=lambda x: x[3], reverse=True):
        first, _, rest = t.partition("\n")
        body = re.sub(r"^[-*+]\s+", "", first)
        entries.append(f"- {day.isoformat()} shipped: {body}" + ("\n" + rest if rest else ""))
    new_readme = re.sub(r"\n{3,}", "\n\n", "\n".join(kept))
    return ({ROADMAP: new_readme, HISTORY: history_add(rd(HISTORY), entries)},
            [f"prune: {len(due)} Shipped entr{'ies' if len(due) != 1 else 'y'} older than {keep_days} days moved to {HISTORY}"])


def _set_line(text: str, key: str, value: str) -> str:
    """Set a `key: value` line in a milestone or epic file: replace it, else put it after the plan line
    (or under the title)."""
    lines = text.split("\n")
    rx = re.compile(rf"^{re.escape(key)}:")
    for i, ln in enumerate(lines):
        if rx.match(ln):
            lines[i] = f"{key}: {value}"
            return "\n".join(lines)
    at = next((i for i, ln in enumerate(lines) if re.match(r"^plan:", ln)), None)
    if at is not None:
        lines.insert(at + 1, f"{key}: {value}")
    else:
        t = next((i for i, ln in enumerate(lines) if ln.startswith("# ")), -1)
        lines[t + 1:t + 1] = ["", f"{key}: {value}"]
    return "\n".join(lines)


def _unchecked(text: str) -> Tuple[int, int]:
    """(ticked, unticked) checkbox count of the `## Exit criteria` section."""
    body = section_named(text, "exit criteria") or ""
    return (len(re.findall(r"^\s*[-*]\s+\[[xX]\]", body, re.M)), len(re.findall(r"^\s*[-*]\s+\[ \]", body, re.M)))


def ship_problems(model: Model, ms: dict) -> List[str]:
    """Why a milestone is not verifiably shipped: every epic's Beads epic closed, the exit criteria ticked."""
    out = []
    if not ms["epics"]:
        out.append(f"{ms['id']} has no epic files, so nothing proves it is done")
    for ep in ms["epics"]:
        if ep["retired"]:
            continue  # shipped, dropped or superseded already: it no longer holds the milestone back
        r = model.rows.get(ep["bd"] or "")
        if r is None:
            out.append(f"epic \"{ep['title']}\" has no Beads epic linked")
        elif r.get("status") != "closed":
            out.append(f"epic \"{ep['title']}\" ({ep['bd']}) is {r.get('status')}, not closed")
    ticked, open_boxes = _unchecked(model.read(ms["path"]) or "")
    if open_boxes:
        out.append(f"{open_boxes} exit criteri{'a are' if open_boxes != 1 else 'on is'} not ticked in {ms['path']}")
    elif not ticked:
        out.append(f"no ticked exit criteria in {ms['path']} (tick each `- [x]` once it is true)")
    return out


def _own_id(bullet: str) -> Optional[str]:
    """The milestone a bullet is about: the id it starts with (after bold marks or a link bracket)."""
    m = re.match(r"^[-*+]\s+(?:\*\*|__|\[)?\s*(m\d+)(?![\w-])", bullet)
    return m.group(1) if m else None


def _remove_from_order(text: str, mid: str, folder: str) -> Tuple[str, str]:
    """(README text without the Now, Next or Later bullet that is about the milestone, error). The bullet
    is the one that starts with its id; a bullet that merely mentions it is never taken. With no leading
    id, a single bullet that names it is used; several candidates are refused."""
    lines = text.split("\n")
    own, mention = [], []
    for sec in ORDER_SECTIONS:
        span = _section_span(text, sec)
        if span is None:
            continue
        for i, end, t in _top_bullets("\n".join(lines[span[0]:span[1]])):
            if _own_id(t) == mid:
                own.append((span[0] + i, span[0] + end))
            elif re.search(rf"(?<![\w-]){re.escape(mid)}(?![\w])", t) or folder in t:
                mention.append((span[0] + i, span[0] + end))
    pick = own or (mention if not any(_own_id("\n".join(lines[a:b])) for a, b in mention) else [])
    if len(pick) != 1:
        return text, (f"no Now, Next or Later bullet starts with {mid}" if not pick else
                      f"{len(pick)} bullets could be {mid}'s; make one start with {mid} and retry")
    a, b = pick[0]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines[:a] + lines[b:])), ""


def _add_shipped(text: str, line: str) -> str:
    span = _section_span(text, "Shipped")
    if span is None:
        return text.rstrip("\n") + f"\n\n## Shipped\n\n{line}\n"
    lines = text.split("\n")
    at = span[0]
    while at < span[1] and not lines[at].strip():
        at += 1
    lines[at:at] = [line, ""] if at < span[1] else [line]
    if span[0] == at:
        lines.insert(at, "")
    return "\n".join(lines)


def retire_changes(root: Path, target: str, how: str, why: str, today: dt.date, *, by: Optional[str] = None,
                   confirmed: Optional[str] = None) -> Tuple[Dict[str, str], List[str], List[str]]:
    """({path: new text}, plain lines, problems) for `retire <mNN | epic path>`. Nothing is written here."""
    root = Path(root)
    rd = file_reader(root)
    model = model_for(root)
    problems: List[str] = []
    if how not in RETIRE_HOWS:
        return {}, [], [f"--as must be one of {', '.join(RETIRE_HOWS)}"]
    if not why.strip():
        problems.append("--why needs one plain line saying what happened")
    stub_tail = ""
    if how == "shipped":
        stub_tail = "Verified by roadmap.py retire: every epic's Beads epic is closed and the exit criteria are ticked."
    else:
        if not confirmed or not CHAT_RX.search(f"confirmed by {confirmed}"):
            problems.append(f"{how} needs the owner's word: --confirmed \"<who> in chat: <where>\" (or \"<who> via <card>: <where>\")")
        else:
            stub_tail = f"confirmed by {confirmed}"
    if how == "superseded":
        if not by or not any(m["id"] == by for m in model.milestones):
            problems.append("superseded needs --by <mNN>, an existing milestone")
    changes: Dict[str, str] = {}
    msgs: List[str] = []
    day = today.isoformat()
    epic = EPIC_RX.match(target)
    if re.fullmatch(r"m\d+", target):
        ms = next((m for m in model.milestones if m["id"] == target), None)
        if ms is None:
            return {}, [], [f"no milestone {target} under {RM}milestones/"]
        if ms["place"] not in ("now", "next", "later"):
            problems.append(f"{target} is {ms['place']}, not in Now, Next or Later")
        if how == "shipped":
            problems += ship_problems(model, ms)
        if problems:
            return {}, [], problems
        folder = ms["path"].rsplit("/", 2)[-2]
        readme, err = _remove_from_order(model.roadmap, target, folder)
        if err:
            return {}, [], [err]
        spec = rd(ms["path"]) or ""
        if how == "shipped":
            readme = _add_shipped(readme, f"- {day} {target} {ms['title']}: {why.strip()}")
            spec = _set_line(spec, "status", "shipped")
        else:
            spec = _set_line(spec, "plan", "not-planned")
            spec = _set_line(spec, "status", how)
            if how == "superseded":
                spec = _set_line(spec, "superseded_by", by)
            changes[HISTORY] = history_add(rd(HISTORY), [
                f"- {day} {how}{' by ' + by if by else ''}, {target} {ms['title']}: {why.strip()}"])
        changes[ROADMAP], changes[ms["path"]] = readme, spec
        label = f"{target} {ms['title']}"
    elif epic:
        ep = next((e for m in model.milestones for e in m["epics"] if e["path"] == target), None)
        if ep is None:
            return {}, [], [f"{target} is not an epic file under {RM}milestones/"]
        if how == "shipped":
            r = model.rows.get(ep["bd"] or "")
            if r is None or r.get("status") != "closed":
                problems.append(f"epic \"{ep['title']}\" has no closed Beads epic, so it is not verifiably shipped")
        if problems:
            return {}, [], problems
        text = rd(target) or ""
        if how != "shipped":
            text = _set_line(text, "plan", "not-planned")
        text = _set_line(text, "status", how)
        if how == "superseded":
            text = _set_line(text, "superseded_by", by)
        changes[target] = text
        changes[HISTORY] = history_add(rd(HISTORY), [
            f"- {day} {how}{' by ' + by if by else ''}, epic {ep['title']} ({EPIC_RX.match(target).group(1)}): {why.strip()}"])
        label = f"epic {ep['title']}"
    else:
        return {}, [], [f"{target} is neither a milestone id (m03) nor an epic file path"]
    entry = (f"\n\n## {day} Retired {label} ({how})\n\n{why.strip()}\n\n- {stub_tail}\n")
    changes[DECISIONS] = (rd(DECISIONS) or "# Decisions\n").rstrip("\n") + entry
    msgs.append(f"retire: {label} {how}; changes to {', '.join(sorted(changes))}")
    return changes, msgs, []


def shipped_pure_removal(before: str, after: str, history: str = "") -> bool:
    """True when the only change is Shipped entries taken out (prune): the rest of the README is the
    same and every entry left was already there. That is history leaving, not a decision."""
    def split(text):
        secs = h2_sections(strip_status(text))
        shipped = [t for _i, _e, t in _top_bullets(secs.get("Shipped", ""))]
        rest = {k: v for k, v in secs.items() if k != "Shipped"}
        head = strip_status(text).split("\n## ", 1)[0]
        return shipped, meat(head) + "\n" + "\n".join(f"{k}:\n{meat(v)}" for k, v in sorted(rest.items()))
    b_ship, b_rest = split(before)
    a_ship, a_rest = split(after)
    if not (b_rest == a_rest and set(a_ship) <= set(b_ship) and a_ship != b_ship):
        return False
    flat = " ".join(history.split())
    # every entry taken out must be in HISTORY.md: deleting a Shipped line by hand is a decision
    return all(" ".join(re.sub(r"^[-*+]\s+", "", t.split("\n", 1)[0]).split()) in flat
               for t in b_ship if t not in a_ship)


def shipped_only_move(before: str, after: str) -> Optional[List[str]]:
    """The milestone ids that left Now, Next and Later and are now in Shipped, when that is the only
    change to the order sections (so retire-as-shipped is not a level-3 reorder); None otherwise."""
    def ids(text, sections):
        secs = h2_sections(strip_status(text))
        return set(re.findall(r"(?<![\w-])(m\d+)(?![\w])", "\n".join(secs.get(s, "") for s in sections)))
    # ids that were in the order sections and are now in Shipped (a bullet that still mentions one of them,
    # such as "m06 starts after m04", may stay: the strip below judges whether anything else changed)
    gone = (ids(before, ORDER_SECTIONS) & ids(after, ["Shipped"])) - ids(before, ["Shipped"])
    if not gone:
        return None
    # what remains in the order sections is unchanged once those bullets are taken out
    def strip(text):
        secs = h2_sections(strip_status(text))
        keep = []
        for s in ORDER_SECTIONS:
            kept = [t for _i, _e, t in _top_bullets(meat(secs.get(s, "")))
                    if not (set(re.findall(r"(?<![\w-])(m\d+)(?![\w])", t)) and
                            set(re.findall(r"(?<![\w-])(m\d+)(?![\w])", t)) <= gone)]
            keep.append(s + ":" + "\n".join(kept))
        return keep
    return sorted(gone) if strip(before) == strip(after) else None


# ---- reader warnings and drift findings

def history_in_now(roadmap_text: str) -> List[str]:
    """Now and Next bullets that read as history: long, many PR numbers, or a merge or release stated."""
    out = []
    secs = h2_sections(strip_status(roadmap_text))
    for sec in ("Now", "Next"):
        for _i, _e, t in _top_bullets(secs.get(sec, "")):
            flat = " ".join(re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t).split())
            words, prs = len(flat.split()), len(set(re.findall(r"#\d+", flat)))
            says = re.search(r"\b(?:is|are|was|were|been) (?:merged|shipped)\b|\bshipped in\b|\bmerged in\b|"
                             r"\breleases? \d+\.\d+", flat, re.I)
            why = ("over %d words" % NOW_BULLET_WORDS if words > NOW_BULLET_WORDS else
                   "cites %d PRs" % prs if prs > NOW_BULLET_PRS else "says what already merged or shipped" if says else None)
            if why:
                out.append(f'{sec} bullet "{flat[:70]}..." {why}; say what is left and move the history to HISTORY.md')
    return out


def off_layout_sections(roadmap_text: str, rcfg: dict) -> List[str]:
    """README headings outside the layout, and the line budget (the status block not counted)."""
    allowed = {h.lower() for h in LAYOUT_HEADINGS} | {str(h).lower() for h in rcfg.get("allowed_headings") or []}
    out = [f'README section "## {h}" is outside the layout; move it to docs/roadmap/plans/ with a one-line link from Now'
           for h in h2_sections(strip_status(roadmap_text)) if h.lower() not in allowed and not PLAN_SECTION_RX.match(h)]
    budget = int(rcfg.get("readme_line_budget", README_LINE_BUDGET))
    n = len(strip_status(roadmap_text).rstrip("\n").split("\n"))
    if n > budget:
        out.append(f"README is {n} lines (budget {budget}, status block not counted); retire what is done and trim Now")
    return out


def reader_warnings(roadmap_text: str, cfg: dict) -> List[str]:
    return history_in_now(roadmap_text) + off_layout_sections(roadmap_text, cfg.get("roadmap") or {})


def retire_findings(root: Path, model: Model, today: dt.date, cfg: dict) -> List[dict]:
    """Drift findings: done work still listed, and a Shipped list past its window."""
    rcfg = cfg.get("roadmap") or {}
    vdir = vd(root)
    out = []

    def finding(check, fid, message, fix):
        out.append({"check": check, "id": fid, "message": message, "fix": fix})

    for ms in model.milestones:
        if ms["place"] not in ("now", "next", "later"):
            continue
        live = [ep for ep in ms["epics"] if not ep["retired"]]
        done = bool(ms["epics"]) and all((model.rows.get(ep["bd"] or "") or {}).get("status") == "closed"
                                         for ep in live)
        if done:
            finding("done-but-listed", ms["id"], f"{ms['id']} {ms['title']} is in {ms['place'].title()} but every "
                    "epic's Beads epic is closed.", f"`{vdir}/roadmap.py retire {ms['id']} --as shipped --why \"...\"`")
            continue
        for ep in live:
            if (model.rows.get(ep["bd"] or "") or {}).get("status") == "closed":
                finding("done-but-listed", ep["path"], f"Epic \"{ep['title']}\" in {ms['id']} has a closed Beads "
                        "epic but is still listed.", f"`{vdir}/roadmap.py retire {ep['path']} --as shipped --why \"...\"`")
    due = shipped_due(model.roadmap, today, int(rcfg.get("shipped_keep_days", SHIPPED_KEEP_DAYS)))
    if due:
        finding("prune-due", "shipped", f"{len(due)} Shipped entr{'ies are' if len(due) != 1 else 'y is'} older than "
                f"{int(rcfg.get('shipped_keep_days', SHIPPED_KEEP_DAYS))} days.", f"`{vdir}/roadmap.py prune`")
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
        print(f"status: no {ROADMAP} (run `{vd(root)}/docs.py init --stage migration`)")
        return 1
    cfg = config.load(root) or {}
    cur, new = render(root, cfg, dt.date.fromisoformat(a.today) if a.today else dt.date.today())
    warnings = reader_warnings(cur, cfg)
    if a.check:
        print("status: current" if cur == new else f"status: STALE (run `{vd(root)}/roadmap.py status`)")
        for w in warnings:
            print(f"warning: {w}")
        return 0 if cur == new else 1
    if cur != new:
        (root / ROADMAP).write_text(new, encoding="utf-8")
    print(new[new.find(STATUS_OPEN):new.find(STATUS_CLOSE) + len(STATUS_CLOSE)])
    for w in warnings:
        print(f"warning: {w}")
    return 0


def _apply(root: Path, changes: Dict[str, str], dry_run: bool, today: dt.date) -> None:
    """Show or write the changed files; a real run then refreshes the status block."""
    import difflib
    rd = file_reader(root)
    for path, new in sorted(changes.items()):
        old = rd(path) or ""
        if dry_run:
            sys.stdout.writelines(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                                       f"a/{path}", f"b/{path}"))
        else:
            fp = root / path
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(new, encoding="utf-8")
    if not dry_run and changes:
        cfg = config.load(root) or {}  # the status block reads epics and SPECs too, so any change refreshes it
        cur, new = render(root, cfg, today)
        if cur != new:
            (root / ROADMAP).write_text(new, encoding="utf-8")


def cmd_retire(a) -> int:
    root = repo_root(a.repo)
    if not active(root):
        print(f"retire: no {ROADMAP}")
        return 1
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    changes, msgs, problems = retire_changes(root, a.target, a.how, a.why or "", today, by=a.by, confirmed=a.confirmed)
    if problems:
        for p in problems:
            print(f"retire: refused: {p}")
        return 2
    _apply(root, changes, a.dry_run, today)
    for m in msgs:
        print(("(dry run) " if a.dry_run else "") + m)
    return 0


def cmd_prune(a) -> int:
    root = repo_root(a.repo)
    if not active(root):
        print(f"prune: no {ROADMAP}")
        return 1
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    keep = int(((config.load(root) or {}).get("roadmap") or {}).get("shipped_keep_days", SHIPPED_KEEP_DAYS))
    changes, msgs = prune_changes(root, today, keep)
    _apply(root, changes, a.dry_run, today)
    for m in msgs:
        print(("(dry run) " if a.dry_run else "") + m)
    return 0


def cmd_ready(a) -> int:
    """What an agent may pick up: open, unblocked, planned Beads items in every unshipped milestone
    (Now first), plus open planned items under no milestone; and what waits for planning."""
    root = repo_root(a.repo)
    if not active(root):
        print(f"ready: no {ROADMAP}")
        return 1
    model = model_for(root)
    seen, items = set(), []
    for ms in model.milestones:
        if ms["place"] in OUT_OF_ROADMAP:
            continue
        for rid in model.ready(ms):
            if rid not in seen:
                seen.add(rid)
                items.append((rid, ms["id"]))
    under = {d for ms in model.milestones for ep in ms["epics"] if ep["bd"] in model.rows
             for d in [ep["bd"], *model.descendants(ep["bd"])]}
    loose = sorted((rid for rid, r in model.rows.items() if rid not in under and r.get("status") == "open"
                    and r.get("issue_type") != "epic" and model.plan(rid) == "planned"
                    and not any(model.rows.get(b, {}).get("status", "closed") != "closed"
                                for b in model.blockers.get(rid, []))),
                   key=lambda i: (int(model.rows[i].get("priority", 2)), i))
    items += [(rid, None) for rid in loose]
    if a.json:
        print(json.dumps({"ready": [{"id": i, "milestone": m, "title": model.rows[i].get("title", ""),
                                     "priority": model.rows[i].get("priority", 2)} for i, m in items],
                          "needs_planning": model.needs_planning(),
                          "planning_list": model.planning_section()}, indent=1))
        return 0
    print(f"Ready (planned, open, unblocked): {len(items)}")
    for i, m in items:
        print(f"  {i} (P{model.rows[i].get('priority', 2)}) {model.rows[i].get('title', '')}"
              + (f"  [{m}]" if m else ""))
    np = model.needs_planning()
    print(f"Needs planning (never pick these up): {len(np)}")
    for ln in np:
        print("  " + ln)
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
        print("reviewed: a routine run never marks the roadmap reviewed (the owner does, in an interactive session)")
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


def cmd_drift(a) -> int:
    root = repo_root(a.repo)
    if not active(root):
        print(f"drift: no {ROADMAP}")
        return 1
    cfg = config.load(root) or {}
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    found, notes = drift(root, model_for(root, cfg), cfg, today, time.time())
    if a.json:
        print(json.dumps({"findings": found, "notes": notes}, indent=1))
        return 1 if found else 0
    for f in found:
        print(f"drift [{f['check']}] {f['message']} Fix: {f['fix']}.")
    for n in notes:
        print(f"note: {n}")
    print(f"drift: {len(found)} finding{'s' if len(found) != 1 else ''}" if found else "drift: clean")
    return 1 if found else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="roadmap.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status", help="regenerate the roadmap (docs/roadmap/README.md) status block (--check: exit 1 when stale)")
    s.add_argument("--check", action="store_true")
    s.add_argument("--today", help=argparse.SUPPRESS)
    s = sub.add_parser("ready", help="what an agent may pick up (planned items only) and what needs planning")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("inbox", help="list the Inbox and open GitHub issues, or --add a dated Inbox line")
    s.add_argument("--add", metavar="TEXT")
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-issues", action="store_true")
    s = sub.add_parser("reviewed", help="mark the roadmap reviewed through a commit (docs.json + DECISIONS)")
    s.add_argument("--sha")
    s = sub.add_parser("retire", help="move a done, dropped or superseded milestone (mNN) or epic file out of the roadmap into the history")
    s.add_argument("target", help="a milestone id (m03) or an epic file path")
    s.add_argument("--as", dest="how", required=True, choices=RETIRE_HOWS)
    s.add_argument("--why", help="one plain line: what happened")
    s.add_argument("--by", metavar="MNN", help="superseded: the milestone that replaces it")
    s.add_argument("--confirmed", metavar="WHO_AND_WHERE", help='dropped and superseded: "<who> in chat: <where>" (or "<who> via <card>: <where>")')
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--today", help=argparse.SUPPRESS)
    s = sub.add_parser("prune", help="move Shipped entries older than shipped_keep_days (30) into docs/roadmap/HISTORY.md")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--today", help=argparse.SUPPRESS)
    s = sub.add_parser("drift", help="has the planning outrun the roadmap? (no model; exit 1 on any finding)")
    s.add_argument("--json", action="store_true")
    s.add_argument("--today", help=argparse.SUPPRESS)
    for p in sub.choices.values():
        p.add_argument("--repo")
    a = ap.parse_args(argv)
    return {"status": cmd_status, "ready": cmd_ready, "inbox": cmd_inbox, "reviewed": cmd_reviewed,
            "drift": cmd_drift, "retire": cmd_retire, "prune": cmd_prune}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
