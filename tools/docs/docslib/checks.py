"""The docs checks (manage-docs v2 section 6; contract section 5; T2, T4, T11).

Three scopes, one code path:
- `pr`: diff-scoped against a base ref (CI, `docs.py check --pr <base>`); file text read from the
  working tree of the checked-out head.
- `staged`: diff-scoped against HEAD for the pre-commit hook; file text read from the index.
- `all`: repo-wide (local `docs.py check`, maintain).

Every result is {check, path, line, message, level} with level "fail" or "warn". Drift is never a
failure (R7): it is a warn-level annotation. `check --pr` exits 0 when only warnings exist.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import posixpath
import re
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from docslib import config, frontmatter, globs, receipts
from docslib._engine import engine

GUARDED = [".github/workflows/docs-verify.yml", "tools/docs/"]
GUARD_BRANCHES = re.compile(r"^docs/(bootstrap|upgrade-[0-9a-f]{7,40})$")
DATED_HEADING = re.compile(r"^## \d{4}-\d{2}-\d{2} \S", re.M)
DONE_STATUS = re.compile(r"^status:\s*done\s*$", re.M)


def result(check, path, message, level="fail", line=None) -> dict:
    return {"check": check, "path": path, "line": line, "message": message, "level": level}


def _git(root: Path, *args, check=True) -> str:
    p = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, errors="replace")
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout


class Ctx:
    """What every check sees: the repo, its config, the change set and two text readers."""

    def __init__(self, root: Path, cfg: dict, scope: str = "all", base: Optional[str] = None,
                 head_ref: Optional[str] = None, local: bool = False):
        self.root = Path(root)
        self.cfg = cfg
        self.scope = scope
        self.local = local
        self.head_ref = head_ref
        self.index = receipts.Index(self.root)
        self.tracked = set(self.index.paths())
        self.base: Optional[str] = None
        self.changes: List[Tuple[str, str, Optional[str]]] = []  # (status, path, old_path)
        if scope == "pr":
            self.base = _git(self.root, "merge-base", base, "HEAD").strip()
            self.changes = self._diff(["diff", "--name-status", "-M", "-z", f"{self.base}...HEAD"])
        elif scope == "staged":
            has_head = subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=self.root,
                                      capture_output=True).returncode == 0
            self.base = "HEAD" if has_head else None
            empty = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
            self.changes = self._diff(["diff", "--cached", "--name-status", "-M", "-z",
                                       "HEAD" if has_head else empty])
            self.tracked = set(_git(self.root, "ls-files", "-z").split("\0")) - {""}
        self._decisions_added: Optional[bool] = None

    def _diff(self, args: List[str]) -> List[Tuple[str, str, Optional[str]]]:
        parts = _git(self.root, *args).split("\0")
        out, i = [], 0
        while i < len(parts) and parts[i]:
            st = parts[i]
            if st[0] in "RC":
                out.append((st[0], parts[i + 2], parts[i + 1]))
                i += 3
            else:
                out.append((st[0], parts[i + 1], None))
                i += 2
        return out

    # ---- readers
    def read(self, path: str) -> Optional[str]:
        """Text on the AFTER side (None when absent)."""
        if self.scope == "staged":
            p = subprocess.run(["git", "show", f":{path}"], cwd=self.root, capture_output=True)
            return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else None
        fp = self.root / path
        if not fp.is_file():
            return None
        return fp.read_text(encoding="utf-8", errors="replace")

    def read_before(self, path: str) -> Optional[str]:
        if not self.base:
            return None
        p = subprocess.run(["git", "show", f"{self.base}:{path}"], cwd=self.root, capture_output=True)
        return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else None

    # ---- change-set helpers
    def changed(self, statuses: str = "ACMRT") -> List[str]:
        return [p for st, p, _ in self.changes if st in statuses]

    def scoped_docs(self) -> List[str]:
        """Docs a diff-scoped check looks at (changed), or every doc (repo-wide)."""
        paths = self.changed() if self.scope != "all" else sorted(self.tracked)
        return [p for p in paths if config.is_doc(self.cfg, p)]

    def all_docs(self) -> List[str]:
        return sorted(p for p in self.tracked if config.is_doc(self.cfg, p))

    def decisions_entry_added(self) -> bool:
        """A dated `## YYYY-MM-DD <title>` heading ADDED to DECISIONS.md in this change set."""
        if self._decisions_added is None:
            after = self.read(config.DECISIONS) or ""
            before = self.read_before(config.DECISIONS) or ""
            if self.scope == "all":
                self._decisions_added = False
            else:
                self._decisions_added = bool(set(DATED_HEADING.findall(after)) - set(DATED_HEADING.findall(before))) \
                    or len(DATED_HEADING.findall(after)) > len(DATED_HEADING.findall(before))
        return self._decisions_added


# ------------------------------------------------------------------ helpers

def live_docs_for_caps(ctx: Ctx, paths) -> List[str]:
    """Docs counted against caps.docs: hand-written docs under docs/ (roadmap and generated are not)."""
    out = []
    for p in paths:
        if not p.startswith("docs/") or p.startswith(("docs/roadmap/", "docs/generated/")):
            continue
        if not config.is_doc(ctx.cfg, p) or p == "docs/README.md":
            continue
        out.append(p)
    return out


def body_lines(text: str) -> int:
    try:
        return len(frontmatter.body(text).splitlines())
    except frontmatter.FrontmatterError:
        return len(text.splitlines())


def _strip_code(text: str) -> str:
    """Blank fenced code blocks and inline code so links inside them are not checked."""
    out, fence = [], None
    for ln in text.split("\n"):
        m = re.match(r"^\s{0,3}(```|~~~)", ln)
        if fence:
            if m and m.group(1) == fence:
                fence = None
            out.append("")
            continue
        if m:
            fence = m.group(1)
            out.append("")
            continue
        out.append(re.sub(r"`[^`]*`", "", ln))
    return "\n".join(out)


def link_targets(text: str) -> List[str]:
    rx = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)|^[ ]{0,3}\[[^\]]+\]:[ \t]*(\S+)", re.M)
    return [m.group(1) or m.group(2) for m in rx.finditer(_strip_code(text))]


def resolve_link(rel: str, target: str) -> Optional[str]:
    """Repo-relative path a relative link points at, or None for external / pure-anchor links."""
    tgt = target.split("#", 1)[0].split("?", 1)[0]
    if not tgt or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", tgt) or tgt.startswith("//"):
        return None
    if tgt.startswith("/"):
        return posixpath.normpath(tgt[1:])
    return posixpath.normpath(posixpath.join(posixpath.dirname(rel), tgt))


def exists_fn(ctx: Ctx) -> Callable[[str], bool]:
    tracked = ctx.tracked
    dirs = set()
    for p in tracked:
        d = posixpath.dirname(p)
        while d:
            dirs.add(d)
            d = posixpath.dirname(d)

    def exists(path: str) -> bool:
        path = path.rstrip("/")
        return path in tracked or path in dirs or (ctx.scope != "staged" and (ctx.root / path).exists()
                                                    and not config.is_public(ctx.cfg))
    return exists


def is_generated_path(ctx: Ctx, path: str) -> bool:
    text = ctx.read(path)
    return bool(text) and receipts.is_generated(text)


def record_done(ctx: Ctx, path: str, text_before: Optional[str]) -> bool:
    """Done records are immutable (v2 section 3). Done = status: done, or pr set and merged,
    or a migrated record (no frontmatter, no record.json), or anything under work/archive/."""
    if path.startswith("work/archive/"):
        return True
    parts = path.split("/")
    folder_rec = None
    if len(parts) >= 3 and re.match(r"\d{4}-\d{2}-\d{2}-", parts[2] if len(parts) > 2 else ""):
        folder_rec = "/".join(parts[:3]) + "/record.json"
    if folder_rec and folder_rec != path and len(parts) > 3:
        before = ctx.read_before(folder_rec)
        if before is None:
            return True  # migrated record folder: no record.json, implicitly done
        try:
            rec = json.loads(before)
        except json.JSONDecodeError:
            return False
        return rec.get("status") == "done" or bool(rec.get("pr") and pr_merged(rec["pr"]))
    if text_before is None:
        return False
    try:
        fm, _ = frontmatter.parse(text_before)
    except frontmatter.FrontmatterError:
        return False
    if fm is None:
        return True  # migrated record: byte-identical, no frontmatter, implicitly done
    return fm.get("status") == "done" or bool(fm.get("pr") and pr_merged(fm["pr"]))


def pr_merged(url: str) -> bool:
    gh = os.environ.get("MANAGE_DOCS_GH", "gh")
    p = subprocess.run([gh, "pr", "view", url, "--json", "state"], capture_output=True, text=True)
    if p.returncode != 0:
        return False
    try:
        return json.loads(p.stdout).get("state") == "MERGED"
    except json.JSONDecodeError:
        return False


# ------------------------------------------------------------------ the checks

def check1_placement(ctx: Ctx) -> List[dict]:
    out = []
    paths = ctx.changed("ACR") if ctx.scope != "all" else sorted(ctx.tracked)
    for p in paths:
        if config.is_doc(ctx.cfg, p) and not config.placed(ctx.cfg, p):
            out.append(result(1, p, f"doc outside the docs layout: {p} (run `docs.py where \"<what it is>\"`)"))
    return out


def check2_links(ctx: Ctx) -> List[dict]:
    out = []
    exists = exists_fn(ctx)
    for p in ctx.scoped_docs():
        if config.is_record(p):
            continue  # links inside records are exempt (K4)
        text = ctx.read(p)
        if text is None:
            continue
        before = set(link_targets(ctx.read_before(p) or "")) if ctx.scope != "all" else set()
        for tgt in link_targets(text):
            if tgt in before:
                continue
            res = resolve_link(p, tgt)
            if res is None or res.startswith(".."):
                continue
            if config.is_public(ctx.cfg) and config.local_only(res):
                continue
            if not exists(res):
                out.append(result(2, p, f"link does not resolve: {tgt}"))
    return out


def check3_frontmatter(ctx: Ctx) -> List[dict]:
    out = []
    added = set(ctx.changed("AC")) if ctx.scope != "all" else set(ctx.tracked)
    for p in ctx.scoped_docs():
        if config.is_record(p):
            continue
        if not config.frontmatter_required(ctx.cfg, p):
            continue  # its frontmatter, if any, is another schema's (a SKILL.md, a README): not ours to parse
        text = ctx.read(p)
        if text is None:
            continue
        try:
            fm, _ = frontmatter.parse(text)
        except frontmatter.FrontmatterError as exc:
            out.append(result(3, p, str(exc)))
            continue
        if receipts.is_generated(text):
            continue
        if fm is None:
            if p in added and ctx.scope != "all":
                out.append(result(3, p, "new doc in docs/ needs frontmatter (id, title, owner, covers)"))
            elif ctx.scope == "all":
                out.append(result(3, p, "doc in docs/ has no frontmatter (id, title, owner, covers)", "warn"))
            continue
        missing = [k for k in ("id", "title", "owner", "covers") if k not in fm]
        if missing:
            out.append(result(3, p, f"frontmatter missing {', '.join(missing)}"))
            continue
        if not isinstance(fm["covers"], list):
            out.append(result(3, p, "covers must be an inline list of quoted globs"))
            continue
        if ctx.scope != "staged":
            for prob in receipts.covers_problems(ctx.root, ctx.index, fm["covers"]):
                out.append(result(3, p, prob))
    # ids are unique across docs (verify packets are keyed by id)
    seen: Dict[str, str] = {}
    for p in ctx.all_docs():
        if not config.frontmatter_required(ctx.cfg, p):
            continue
        try:
            fm, _ = frontmatter.parse(ctx.read(p) or "")
        except frontmatter.FrontmatterError:
            continue
        if fm and "id" in fm:
            if fm["id"] in seen and (p in added or seen[fm["id"]] in added or ctx.scope == "all"):
                out.append(result(3, p, f"duplicate frontmatter id {fm['id']} (also {seen[fm['id']]})"))
            seen.setdefault(fm["id"], p)
    return out


def check4_generated(ctx: Ctx) -> List[dict]:
    out = []
    paths = ctx.changed("ACMRT") if ctx.scope != "all" else sorted(ctx.tracked)
    for p in paths:
        text = ctx.read(p)
        if text is None:
            continue
        if receipts.is_generated(text) and not receipts.generated_ok(text):
            out.append(result(4, p, "generated file edited by hand (content does not match its header hash); "
                                    "change the generator or its sources, then `docs.py generate`"))
        elif ctx.scope != "all":
            before = ctx.read_before(p)
            if before and receipts.is_generated(before) and not receipts.is_generated(text):
                out.append(result(4, p, "generated file edited by hand (generator header removed)"))
    return out


def check5_caps(ctx: Ctx) -> List[dict]:
    out = []
    caps = ctx.cfg.get("caps", {})
    warn_old = caps.get("overage") == "warn"
    cap_docs, cap_lines = caps.get("docs"), caps.get("lines")
    after = live_docs_for_caps(ctx, ctx.tracked)
    if cap_docs and len(after) > cap_docs:
        if ctx.scope == "all":
            out.append(result(5, "docs/", f"doc count {len(after)} over cap {cap_docs}",
                              "warn" if warn_old else "fail"))
        else:
            removed = set(ctx.changed("D")) | {o for st, _, o in ctx.changes if st == "R" and o}
            added = set(ctx.changed("ACR"))
            before_n = len([p for p in after if p not in added]) + len(live_docs_for_caps(ctx, removed))
            if len(after) > before_n:
                out.append(result(5, "docs/", f"doc count {len(after)} over cap {cap_docs} (new overage)"))
            elif not warn_old:
                out.append(result(5, "docs/", f"doc count {len(after)} over cap {cap_docs}"))
    if cap_lines:
        for p in live_docs_for_caps(ctx, ctx.scoped_docs()):
            text = ctx.read(p)
            if text is None or receipts.is_generated(text):
                continue
            n = body_lines(text)
            if n <= cap_lines:
                continue
            before = ctx.read_before(p) if ctx.scope != "all" else None
            n0 = body_lines(before) if before is not None else 0
            new_overage = ctx.scope != "all" and n > n0
            if new_overage or not warn_old:
                out.append(result(5, p, f"{n} lines over cap {cap_lines}" + (" (new overage)" if new_overage else "")))
            else:
                out.append(result(5, p, f"{n} lines over cap {cap_lines} (pre-existing)", "warn"))
    return out


def check6_done_records(ctx: Ctx) -> List[dict]:
    if ctx.scope == "all":
        return []
    out = []
    for st, p, old in ctx.changes:
        src = old if st == "R" else p
        if not config.is_record(src) or src.endswith("/README.md") and src.count("/") == 1:
            continue
        before = ctx.read_before(src)
        if st == "A" or before is None:
            continue
        if not record_done(ctx, src, before):
            continue
        if st == "R" and p.startswith("work/archive/") and ctx.read(p) == before:
            continue  # ledgered sweep move into work/archive/, byte-identical
        out.append(result(6, src, "done record modified (records are immutable once done)"))
    return out


def check7_loosening(ctx: Ctx, force_local: bool = False) -> List[dict]:
    """Private repos in CI; public repos only locally (pre-commit / routine / --local)."""
    if config.is_public(ctx.cfg) and not (force_local or ctx.local or ctx.scope == "staged"):
        return []
    if ctx.scope == "all" or "docs.json" not in ctx.changed("AMR"):
        return []
    try:
        new_raw = json.loads(ctx.read("docs.json") or "{}")
        before = ctx.read_before("docs.json")
        old_raw = json.loads(before) if before else None
    except json.JSONDecodeError as exc:
        return [result(7, "docs.json", f"docs.json is not valid JSON: {exc}")]
    loose = config.loosenings(old_raw, new_raw)
    if loose and not ctx.decisions_entry_added():
        return [result(7, "docs.json", f"loosens a rule without a dated {config.DECISIONS} entry "
                                       f"(`## YYYY-MM-DD <title>` in the same change): " + "; ".join(loose))]
    return []


def check8_local_only(ctx: Ctx) -> List[dict]:
    if not config.is_public(ctx.cfg):
        return []
    paths = ctx.changed("ACMRT") if ctx.scope != "all" else sorted(ctx.tracked)
    return [result(8, p, "tracked file under a local-only path in a public repo (gitignore it)")
            for p in paths if config.local_only(p)]


def drift_annotations(ctx: Ctx) -> List[dict]:
    """R7: covered paths changed without the doc; or a verified doc's body changed. Warn only."""
    if ctx.scope == "all":
        return []
    changed = set(ctx.changed("ACMRTD"))
    out = []
    for p in ctx.all_docs():
        if not config.frontmatter_required(ctx.cfg, p):
            continue
        text = ctx.read(p)
        try:
            fm, body = frontmatter.parse(text or "")
        except frontmatter.FrontmatterError:
            continue
        if not fm:
            continue
        if p in changed:
            rec = fm.get("verified")
            if rec and rec.get("doc_hash") != receipts.sha256_text(body):
                out.append(result("drift", p, f"{p}: receipt void (body changed); maintain re-verifies it", "warn"))
            continue
        covers = fm.get("covers") or []
        if any(globs.any_match(covers, c) for c in changed):
            out.append(result("drift", p, f"{p} may be stale: covered paths changed", "warn"))
    return out


def path_guard(ctx: Ctx) -> List[dict]:
    """X6: ONLY the docs verify workflow and tools/docs/ are guarded; they change only in a
    bootstrap or upgrade PR. Other workflows change normally."""
    if ctx.scope != "pr":
        return []
    ref = ctx.head_ref or os.environ.get("GITHUB_HEAD_REF") or ""
    if GUARD_BRANCHES.match(ref):
        return []
    out = []
    for st, p, old in ctx.changes:
        for q in {p, old} - {None}:
            if any(globs.match(g, q) for g in GUARDED):
                out.append(result("guard", q, f"{q} changes only in a docs/bootstrap or docs/upgrade-<sha> PR "
                                              f"(this branch: {ref or 'unknown'})"))
    return out


def pin_problems(root: Path) -> List[str]:
    """tools/docs/PIN: {"skill_sha", "files": {relpath: sha256}}; every vendored file must match."""
    pin = Path(root) / "tools" / "docs" / "PIN"
    if not pin.exists():
        return []
    try:
        data = json.loads(pin.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"tools/docs/PIN is not valid JSON: {exc}"]
    probs = []
    files = data.get("files") or {}
    base = Path(root) / "tools" / "docs"
    for rel, want in sorted(files.items()):
        fp = base / rel
        if not fp.is_file():
            probs.append(f"vendored file missing: tools/docs/{rel}")
        elif hashlib.sha256(fp.read_bytes()).hexdigest() != want:
            probs.append(f"vendored file does not match PIN: tools/docs/{rel}")
    on_disk = set()
    for fp in base.rglob("*"):
        if fp.is_file() and "__pycache__" not in fp.parts:
            on_disk.add(fp.relative_to(base).as_posix())
    for extra in sorted(on_disk - set(files) - {"PIN"}):
        probs.append(f"vendored file not in PIN: tools/docs/{extra}")
    return probs


def check_pin(ctx: Ctx) -> List[dict]:
    if ctx.scope == "pr" and not any(p.startswith("tools/docs/") for _, p, _ in ctx.changes):
        return []
    return [result("pin", "tools/docs/PIN", m) for m in pin_problems(ctx.root)]


def local_receipts(ctx: Ctx) -> List[dict]:
    """--local: receipt states as warnings (age, void, drift, unverified). Maintain fixes them."""
    out = []
    max_age = ctx.cfg.get("receipt_max_age_days", 30)
    for p in ctx.all_docs():
        if not config.frontmatter_required(ctx.cfg, p):
            continue
        try:
            fm, _ = frontmatter.parse(ctx.read(p) or "")
        except frontmatter.FrontmatterError:
            continue
        if not fm or "covers" not in fm:
            continue
        st = receipts.receipt_state(ctx.root, ctx.index, p, ctx.cfg)
        if st["status"] != "verified":
            out.append(result("receipt", p, f"{st['status']}: {st['reason']}", "warn"))
        elif st["age_days"] is not None and st["age_days"] > max_age:
            out.append(result("receipt", p, f"receipt {st['age_days']} days old (max {max_age})", "warn"))
    return out


DOCS_CHECKS = [check1_placement, check2_links, check3_frontmatter, check4_generated, check5_caps,
               check6_done_records, check7_loosening, check8_local_only, drift_annotations, path_guard,
               check_pin]


def run(ctx: Ctx, plugins=()) -> List[dict]:
    out: List[dict] = []
    for fn in DOCS_CHECKS:
        out.extend(fn(ctx))
    if ctx.local:
        out.extend(local_receipts(ctx))
    for plug in plugins:
        fn = getattr(plug, "run_checks", None)
        if fn is not None:
            for r in fn(ctx) or []:
                r.setdefault("level", "fail")
                r.setdefault("line", None)
                out.append(r)
    return out


def exit_code(results: List[dict], scope: str) -> int:
    """`check --pr`: 0 when only warnings (R7); local: 0 pass, 1 fail, 4 warnings only."""
    if any(r["level"] == "fail" for r in results):
        return 1
    if results and scope != "pr":
        return 4
    return 0


def render(results: List[dict], annotate: bool = False) -> str:
    lines = []
    for r in results:
        loc = r["path"] + (f":{r['line']}" if r.get("line") else "")
        if annotate:
            kind = "error" if r["level"] == "fail" else "warning"
            ln = f",line={r['line']}" if r.get("line") else ""
            lines.append(f"::{kind} file={r['path']}{ln}::[{r['check']}] {r['message']}")
        else:
            lines.append(f"{r['level'].upper()} [{r['check']}] {loc}: {r['message']}")
    return "\n".join(lines)


# ------------------------------------------------------------------ where (7.4, E2)

WHERE = [
    ("handoff", ["handoff", "hand off", "pick up", "session note"], "work/handoffs/{date}-{slug}/"),
    ("run", ["run log", "loop", "routine", "migration run", "autopilot"], "work/runs/{date}-{slug}/"),
    ("evidence", ["evidence", "qa", "receipt", "verification", "screenshot"], "work/evidence/{date}-{slug}/"),
    ("audit", ["audit", "review findings", "assessment"], "work/audits/{date}-{slug}.md"),
    ("guide", ["how to", "how-to", "guide", "setup", "tutorial"], "docs/guides/{slug}.md"),
    ("runbook", ["runbook", "incident", "rollback", "on-call", "ops"], "docs/runbooks/{slug}.md"),
    ("architecture", ["architecture", "design of", "how it works", "data model"], "docs/architecture/{slug}.md"),
    ("rule", ["rule", "convention", "standard", "policy"], "docs/rules/{slug}.md"),
    ("decision", ["decision", "ruling", "decided"], "docs/roadmap/DECISIONS.md (append)"),
    ("roadmap item", ["milestone", "epic", "feature spec", "plan"], "docs/roadmap/ (manage-roadmap)"),
    ("generated", ["generated", "status", "inventory"], "never by hand: add a generator in docs.json"),
    ("scratch", ["verify packet", "cache", "temp"], ".docs-cache/"),
]


STOP_WORDS = {"a", "an", "the", "for", "to", "of", "on", "in", "about", "my", "our", "this", "that", "new",
              "and", "with", "from", "up", "we", "i", "is", "it", "write", "add", "doc", "file", "note"}


def _kw_rx(kw: str) -> re.Pattern:
    return re.compile(r"(?<![\w-])" + r"\s+".join(re.escape(w) for w in kw.split()) + r"(?![\w-])", re.I)


def where(query: str, today: Optional[str] = None) -> Tuple[int, List[Tuple[str, str]]]:
    """(exit code, [(kind, path)]): 0 one kind, 1 none, 2 several (candidates)."""
    q = query.strip()
    hits = []
    rest = q
    for kind, kws, path in WHERE:
        matched = [kw for kw in kws if _kw_rx(kw).search(q)]
        if matched:
            hits.append((kind, path))
            for kw in matched:
                rest = _kw_rx(kw).sub(" ", rest)
    words = [w for w in re.findall(r"[a-z0-9]+", rest.lower()) if w not in STOP_WORDS]
    slug = "-".join(words[:6]) or "<slug>"
    date = today or dt.date.today().isoformat()
    filled = [(k, p.format(date=date, slug=slug)) for k, p in hits]
    if not filled:
        return 1, []
    return (0 if len(filled) == 1 else 2), filled
