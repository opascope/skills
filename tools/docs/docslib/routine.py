"""The daily routine and maintain (manage-docs v2 section 8; contract sections 6 and 9; T3, T10).

Two layers:
- `routine(...)` runs from the dedicated clone of the skill's home repo (latest skill): quota for THIS runtime, one
  flock (a second scheduler exits 5), per-repo skip reasons, a fresh worktree from
  origin/<base_branch>, the public-repo private state dir, the upgrade PR when the vendored PIN is
  behind, the vendored `docs.py maintain --here`, and the heartbeat issue for every repo.
- `maintain_here(...)` runs inside that worktree with the repo's VENDORED docs.py: sweeps; drift
  re-derive + claim checks within budget and receipt ages; roadmap plugin maintain; unclosed issue
  intents; generators LAST; one commit per phase and per receipt (the file index is refreshed after
  every commit, OV2); then the ONE daily PR on `docs/maintain` (auto-merge off, label
  `docs-approval-pending`, approval section). A second run on an unchanged repo makes no diff.

Rejections (ER18 / D9): every change carries a fingerprint {op, path, input}; a PR closed with
`manage-docs:reject <reason>` suppresses all of its fingerprints until the input changes or 30 days
pass, stored in `~/.local/share/manage-docs/state/<repo>/rejected.json` plus a work/runs record.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from docslib import config, frontmatter, generate, receipts, records, site

BRANCH = "docs/maintain"
LABEL = "docs-approval-pending"
HEARTBEAT_TITLE = "manage-docs heartbeat"
APPROVAL_OPEN, APPROVAL_CLOSE = "<!-- manage-docs:approval -->", "<!-- /manage-docs:approval -->"
CHANGES_RX = re.compile(r"<!-- manage-docs:changes (.*?) -->", re.S)
INFLIGHT = re.compile(r"^docs/(bootstrap|migrate-|port-|upgrade-)")
SUPPRESS_DAYS = 30
SUPERSEDED = "manage-docs: superseded by a fresh run (conflict)"


# ------------------------------------------------------------------ homes + small helpers

def data_home() -> Path:
    return Path(os.environ.get("MANAGE_DOCS_DATA_HOME", Path.home() / ".local" / "share" / "manage-docs"))


def config_home() -> Path:
    return Path(os.environ.get("MANAGE_DOCS_CONFIG_HOME", Path.home() / ".config" / "manage-docs"))


def state_dir(slug: str) -> Path:
    d = data_home() / "state" / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def private_dir(slug: str) -> Path:
    return data_home() / "private" / slug


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def git(root: Path, *args, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, errors="replace")
    if check and p.returncode != 0:
        raise RoutineError(f"git {' '.join(args)}: {(p.stderr or p.stdout).strip()[:500]}")
    return p


def gh(root: Path, *args, check: bool = True, input: Optional[str] = None) -> subprocess.CompletedProcess:
    p = subprocess.run([os.environ.get("MANAGE_DOCS_GH", "gh"), *args], cwd=root, capture_output=True,
                       text=True, input=input)
    if check and p.returncode != 0:
        raise RoutineError(f"gh {' '.join(args[:3])}: {(p.stderr or p.stdout).strip()[:500]}")
    return p


def gh_json(root: Path, *args):
    out = gh(root, *args).stdout
    return json.loads(out) if out.strip() else None


class RoutineError(Exception):
    pass


# ------------------------------------------------------------------ quota (8.2 step 1; OV3, R14)

def _band(used: float) -> str:
    if used > 90:
        return "pause"
    if used > 80:
        return "checkpoint"
    if used > 70:
        return "small"
    return "ok"


def quota(runtime: str, clone_root: Optional[Path] = None, now: Optional[float] = None,
          max_age: int = 3600) -> dict:
    """{band: ok|small|checkpoint|pause|unknown, used, resets_at, reason} for THIS runtime only.

    Claude Code reads ~/.claude/quota-state.json and counts the five_hour window only: the operator
    switches accounts for the weekly window, so seven_day never gates (a standing rule; it replaces
    R14's both-windows reading). Codex reads scripts/lib/codex_quota.py from the dedicated home clone,
    all its native windows. Missing, stale, null or no current five_hour window = unknown (run one
    repo, then recheck; AGENTS.md rule 10), never exhaustion."""
    now = now if now is not None else time.time()
    windows: List[Tuple[float, Optional[int]]] = []
    if runtime == "codex":
        cmd = os.environ.get("MANAGE_DOCS_CODEX_QUOTA")
        argv = cmd.split() if cmd else [sys.executable, str(Path(clone_root or ".") / "scripts" / "lib" / "codex_quota.py")]
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            data = json.loads(p.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {"band": "unknown", "used": None, "resets_at": None, "reason": "codex telemetry unreadable"}
        if data.get("status") != "known":
            return {"band": "unknown", "used": None, "resets_at": None, "reason": data.get("reason", "unknown")}
        for w in data.get("windows") or []:
            windows.append((float(w["used_percent"]), w.get("resets_at")))
        if data.get("action") == "native_limit_reached":
            windows.append((100.0, max((w[1] or 0) for w in windows) if windows else None))
    else:
        path = Path(os.environ.get("MANAGE_DOCS_QUOTA_FILE", Path.home() / ".claude" / "quota-state.json"))
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"band": "unknown", "used": None, "resets_at": None, "reason": "quota file missing or unreadable"}
        try:
            upd = dt.datetime.fromisoformat(str(data.get("updated_at")).replace("Z", "+00:00")).timestamp()
        except ValueError:
            upd = 0
        if now - upd > max_age:
            return {"band": "unknown", "used": None, "resets_at": None, "reason": "quota file stale"}
        rl = data.get("rate_limits")
        if not isinstance(rl, dict):
            return {"band": "unknown", "used": None, "resets_at": None, "reason": "rate_limits is null"}
        for name in ("five_hour",):
            w = rl.get(name)
            if not isinstance(w, dict) or w.get("used_percentage") is None:
                continue
            if w.get("resets_at") and w["resets_at"] <= now:
                continue  # Claude Code drops a window once it has reset
            windows.append((float(w["used_percentage"]), w.get("resets_at")))
    if not windows:
        return {"band": "unknown", "used": None, "resets_at": None, "reason": "no current window"}
    used, resets = max(windows, key=lambda w: w[0])
    return {"band": _band(used), "used": used, "resets_at": resets, "reason": ""}


# ------------------------------------------------------------------ lock (T3: second scheduler exits 5)

class Busy(Exception):
    pass


class RoutineLock:
    def __init__(self):
        data_home().mkdir(parents=True, exist_ok=True)
        self.path = data_home() / "routine.lock"
        self.fd: Optional[int] = None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self.fd)
            self.fd = None
            raise Busy(f"another routine run holds {self.path}") from exc
        os.ftruncate(self.fd, 0)
        os.write(self.fd, json.dumps({"pid": os.getpid(), "started_at": iso(now_utc())}).encode())
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None


# ------------------------------------------------------------------ skips + heartbeat (8.2 step 3, 8.4)

def load_repos() -> List[dict]:
    p = config_home() / "repos.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def migration_lock(repo_path: Path) -> Path:
    common = git(repo_path, "rev-parse", "--git-common-dir").stdout.strip()
    c = Path(common)
    if not c.is_absolute():
        c = (Path(repo_path) / c).resolve()
    return c / "manage-docs" / "migration.lock"


def skip_reason(entry: dict) -> Optional[str]:
    path, slug, base = Path(entry["path"]), entry["slug"], entry.get("base_branch", "main")
    if git(path, "cat-file", "-e", f"origin/{base}:docs.json", check=False).returncode != 0:
        return "no docs.json"
    if (config_home() / "active" / slug).exists():
        return "active-migration marker"
    if migration_lock(path).exists():
        return "migration-lock"
    prs = gh_json(path, "pr", "list", "--state", "open", "--json", "headRefName,number") or []
    for pr in prs:
        if INFLIGHT.match(pr.get("headRefName", "")):
            return f"migration in flight (PR #{pr.get('number')} {pr['headRefName']})"
    return None


def heartbeat(repo_path: Path, status: str, success: bool, now: Optional[dt.datetime] = None) -> None:
    """Rewrite the ONE open `manage-docs heartbeat` issue body (X3). Skips never count as success."""
    now = now or now_utc()
    issues = gh_json(repo_path, "issue", "list", "--state", "open", "--search", f"{HEARTBEAT_TITLE} in:title",
                     "--json", "number,title,body") or []
    issue = next((i for i in issues if i.get("title") == HEARTBEAT_TITLE), None)
    if issue is None:
        raise RoutineError(f"no open `{HEARTBEAT_TITLE}` issue (create it with `docs.py init --stage local`)")
    prev = re.search(r"^last_success:\s*(\S+)", issue.get("body") or "", re.M)
    last_success = iso(now) if success else (prev.group(1) if prev else "never")
    body = f"last_run: {iso(now)}\nstatus: {status}\nlast_success: {last_success}\n"
    gh(repo_path, "issue", "edit", str(issue["number"]), "--body", body)


# ------------------------------------------------------------------ fingerprints + rejections (ER18)

def fp_key(c: dict) -> Tuple[str, str, str]:
    return (c["op"], c["path"], c["input"])


def load_rejected(slug: str) -> List[dict]:
    p = state_dir(slug) / "rejected.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("fingerprints", [])


def save_rejected(slug: str, rows: List[dict]) -> None:
    p = state_dir(slug) / "rejected.json"
    p.write_text(json.dumps({"fingerprints": rows}, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def suppressed(c: dict, rejected: List[dict], now: dt.datetime) -> Optional[dict]:
    """The rejection that still suppresses change `c`, if any (same op+path+input, under 30 days)."""
    for r in rejected:
        if fp_key(r) != fp_key(c):
            continue
        at = dt.datetime.fromisoformat(r["rejected_at"].replace("Z", "+00:00"))
        if (now - at).days < SUPPRESS_DAYS:
            return r
    return None


def process_rejection(repo_root: Path, slug: str, now: dt.datetime) -> List[dict]:
    """Read the previously opened daily PR; if the owner rejected it, suppress its fingerprints."""
    sp = state_dir(slug) / "open-pr.json"
    if not sp.exists():
        return []
    st = json.loads(sp.read_text(encoding="utf-8"))
    view = gh_json(repo_root, "pr", "view", str(st["number"]), "--json", "state,comments") or {}
    if view.get("state") == "OPEN":
        return []
    new_rows = []
    if view.get("state") == "CLOSED":
        reason = None
        for c in view.get("comments") or []:
            m = re.match(r"^manage-docs:reject\s*(.*)", (c.get("body") or "").strip(), re.S)
            if m:
                reason = m.group(1).strip() or "(no reason given)"
        if reason is not None:
            for ch in st.get("changes", []):
                new_rows.append({"op": ch["op"], "path": ch["path"], "input": ch["input"], "reason": reason,
                                 "rejected_at": iso(now), "pr": st["number"]})
            keep = [r for r in load_rejected(slug) if (now - dt.datetime.fromisoformat(
                r["rejected_at"].replace("Z", "+00:00"))).days < SUPPRESS_DAYS]
            save_rejected(slug, keep + new_rows)
    sp.replace(state_dir(slug) / f"closed-pr-{st['number']}.json")
    return new_rows


# ------------------------------------------------------------------ approval section (8.3, 8.4)

def doc_title(root: Path, path: str) -> str:
    try:
        fm, body = frontmatter.parse((Path(root) / path).read_text(encoding="utf-8"))
    except (OSError, frontmatter.FrontmatterError):
        return path
    if fm and fm.get("title"):
        return str(fm["title"])
    for ln in body.splitlines():
        if ln.startswith("# "):
            return ln[2:].strip()
    return path


def docs_lines(changes: List[dict], notes: List[str]) -> List[str]:
    """Plain English, product level; deterministic (sorted) so the approval hash is stable."""
    out = []
    rec = sorted({c["title"] for c in changes if c["op"] == "receipt"})
    if rec:
        out.append(f"Re-checked {len(rec)} doc{'s' if len(rec) != 1 else ''} against the code and confirmed "
                   f"{'them' if len(rec) != 1 else 'it'}: " + "; ".join(rec) + ".")
    gens = sorted({c["label"] for c in changes if c["op"] == "generate"})
    for g in gens:
        out.append(f"Refreshed {g}.")
    sw = [c for c in changes if c["op"] == "sweep"]
    if sw:
        out.append(f"Archived {len(sw)} finished record{'s' if len(sw) != 1 else ''} older than the records window.")
    out.extend(sorted(set(notes)))
    return out


def approval_section(doc_lines: List[str], roadmap_lines: List[str]) -> str:
    parts = [APPROVAL_OPEN, "## Docs", ""]
    parts += [f"- {ln}" for ln in doc_lines] or ["- No docs changes."]
    if roadmap_lines:
        parts += ["", "## Roadmap", ""] + [f"- {ln}" for ln in roadmap_lines]
    parts.append(APPROVAL_CLOSE)
    return "\n".join(parts)


def pr_body(section: str, changes: List[dict]) -> str:
    fps = sorted(({"op": c["op"], "path": c["path"], "input": c["input"]} for c in changes),
                 key=lambda c: fp_key(c))
    return (f"Daily docs maintenance. Merges only on {site.get('owner')}'s Approve (manage-docs v2 section 8).\n\n"
            + section + "\n\n<!-- manage-docs:changes " + json.dumps(fps, sort_keys=True) + " -->\n")


def changes_from_body(body: str) -> List[dict]:
    m = CHANGES_RX.search(body or "")
    return json.loads(m.group(1)) if m else []


# ------------------------------------------------------------------ maintain (inside a worktree)

class Maintain:
    def __init__(self, root: Path, cfg: dict, slug: str, runtime: str, plugins=(), verify: bool = True,
                 push: bool = True, now: Optional[dt.datetime] = None):
        self.root = Path(root)
        self.cfg = cfg
        self.slug = slug
        self.runtime = runtime
        self.plugins = list(plugins)
        self.verify = verify
        self.push = push
        self.now = now or now_utc()
        self.today = self.now.date()
        self.changes: List[dict] = []
        self.notes: List[str] = []
        self.rejected = load_rejected(slug)
        self.index = receipts.Index(self.root)
        self.run_dir = records.fresh_run_dir(self.root, self.today)
        self.base = cfg.get("base_branch", "main")

    # -- commits
    def commit(self, msg: str, paths: Optional[List[str]] = None) -> bool:
        # scratch never lands in a commit, whatever the repo's .gitignore says
        git(self.root, "add", "-A", "--", *(paths or ["."]))
        git(self.root, "reset", "-q", "--", ".docs-cache", check=False)
        if git(self.root, "diff", "--cached", "--quiet", check=False).returncode == 0:
            return False
        git(self.root, "commit", "-q", "--no-verify", "-m", msg)
        self.index.refresh()
        return True

    def note_suppressed(self, c: dict) -> bool:
        r = suppressed(c, self.rejected, self.now)
        if r:
            self.notes.append(f"Held back a change {site.get('owner')} rejected on PR #{r['pr']} ({r['reason']}): {c['path']}.")
            return True
        return False

    # -- phase 1: sweeps
    def phase_sweep(self) -> None:
        cands = records.candidates(self.root, int(self.cfg.get("records_window_days", 30)), self.today)
        allowed = []
        for c in cands:
            h = records._tree_hash(self.root / c["src"])
            if not self.note_suppressed({"op": "sweep", "path": c["src"], "input": h}):
                allowed.append(c["src"])
        if not allowed:
            return
        rows = [r for r in records.sweep(self.root, self.cfg, self.today, self.run_dir, only=set(allowed))]
        for r in rows:
            self.changes.append({"op": "sweep", "path": r["path"], "input": r["input"]})
        if not config.is_public(self.cfg):
            self.commit(f"docs(maintain): archive {len(rows)} finished records")

    # -- phase 2: drift re-derive + claim checks within budget + receipt ages
    def worklist(self) -> List[Tuple[str, str]]:
        """[(doc, why)] oldest receipt first; unverified first."""
        items = []
        max_age = int(self.cfg.get("receipt_max_age_days", 30))
        for p in self.index.paths():
            if not config.is_doc(self.cfg, p) or not config.frontmatter_required(self.cfg, p):
                continue
            try:
                fm, _ = frontmatter.parse((self.root / p).read_text(encoding="utf-8"))
            except (OSError, frontmatter.FrontmatterError):
                continue
            if not fm or "id" not in fm or "covers" not in fm:
                continue
            st = receipts.receipt_state(self.root, self.index, p, self.cfg)
            if st["status"] == "verified" and (st["age_days"] or 0) <= max_age:
                continue
            at = (fm.get("verified") or {}).get("at", "0000-00-00")
            items.append((at, p, st["status"] if st["status"] != "verified" else f"older than {max_age} days"))
        items.sort()
        wl = [(p, why) for _, p, why in items]
        cache = self.root / ".docs-cache" / "maintain"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "worklist.json").write_text(json.dumps(wl, indent=1) + "\n", encoding="utf-8")
        return wl

    def phase_receipts(self) -> None:
        wl = self.worklist()
        if not wl:
            return
        if not self.verify:
            self.notes.append(f"{len(wl)} doc{'s' if len(wl) != 1 else ''} wait for a re-check; skipped today "
                              "(quota is high).")
            return
        budget = int(self.cfg.get("verify_budget_per_run", 10))
        cmd = receipts.reviewer_cmd(self.cfg, self.runtime)
        done = 0
        for p, why in wl:
            if done >= budget:
                self.notes.append(f"{len(wl) - done} more doc{'s' if len(wl) - done != 1 else ''} wait for a "
                                  "re-check (daily budget reached); the next run continues.")
                break
            text = (self.root / p).read_text(encoding="utf-8")
            fm, body = frontmatter.parse(text)
            ch = {"op": "receipt", "path": p,
                  "input": receipts.sha256_text(body) + ":" +
                  receipts.covers_hash(self.root, self.index, list(fm.get("covers") or []), self.cfg.get("doc_globs", []))}
            if self.note_suppressed(ch):
                continue
            done += 1
            try:
                packet = receipts.prepare(self.root, p, self.cfg)
                receipts.run_review(self.root, packet, cmd)
                receipts.finish(self.root, p, self.cfg, today=self.today.isoformat())
            except receipts.ReviewUnavailable:
                self.notes.append("Could not run the claim checks today (the second-model reviewer was "
                                  "unavailable); receipts are unchanged.")
                return
            except receipts.VerifyError:
                self.notes.append(f"{doc_title(self.root, p)} no longer matches the code ({why}); it needs a "
                                  "rewrite before it can be confirmed.")
                continue
            ch["title"] = doc_title(self.root, p)
            self.changes.append(ch)
            self.commit(f"docs(maintain): receipt for {p}", [p])

    # -- phase 3: roadmap plugin status + Beads export
    def phase_plugins(self) -> List[str]:
        lines: List[str] = []
        for plug in self.plugins:
            fn = getattr(plug, "maintain", None)
            if fn is None:
                continue
            for c in fn(self) or []:
                if self.note_suppressed(c):
                    continue
                self.changes.append(c)
        self.commit("docs(maintain): roadmap status and Beads export")
        return lines

    # -- phase 4: unclosed issue intents (contract section 2 step 4)
    def phase_intents(self) -> None:
        runs = sorted((self.root / "work" / "runs").glob("*-migration/ledger.jsonl")) if (self.root / "work" / "runs").is_dir() else []
        for led in runs:
            rows = [json.loads(x) for x in led.read_text(encoding="utf-8").splitlines() if x.strip()]
            if not any(r.get("disposition", "").startswith("issue:") for r in rows):
                continue
            date = led.parent.name[:10]
            engine = self.root / "tools" / "docs" / "engine" / "migrate.py"
            plug_args = []
            for name in ("roadmap.py", "docs.py"):
                f = self.root / "tools" / "docs" / name
                if f.exists():
                    plug_args += ["--plugin", str(f)]
            p = subprocess.run([sys.executable, str(engine), "close", "--run", date, "--repo", str(self.root),
                                *plug_args], capture_output=True, text=True)
            if p.returncode != 0:
                self.notes.append(f"Could not finish closing the issues from the {date} migration; the next run "
                                  "retries.")

    # -- phase 5: generators LAST
    def phase_generate(self) -> None:
        _, changed = generate.run(self.root, self.cfg, check=True)
        if not changed:
            return
        generate.run(self.root, self.cfg)
        for fp in generate.fingerprints(self.root, self.cfg, changed):
            if self.note_suppressed(fp):
                if git(self.root, "ls-files", "--error-unmatch", fp["path"], check=False).returncode == 0:
                    git(self.root, "checkout", "--", fp["path"])
                else:
                    (self.root / fp["path"]).unlink()
                continue
            fp["label"] = {"docs/README.md": "the docs map", "work/README.md": "the records index"}.get(
                fp["path"], f"the generated file {fp['path']}")
            self.changes.append(fp)
        self.commit("docs(maintain): regenerate generated docs")

    def write_rejection_record(self, rows: List[dict]) -> None:
        if not rows:
            return
        rd = self.root / self.run_dir
        rd.mkdir(parents=True, exist_ok=True)
        with (rd / "rejections.jsonl").open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, sort_keys=True) + "\n")
        rj = rd / "record.json"
        if not rj.exists():
            rj.write_text(json.dumps({"status": "done", "pr": None}) + "\n", encoding="utf-8")
        if not config.is_public(self.cfg):
            self.commit("docs(maintain): record the rejected daily PR")

    def run_phases(self, rejected_rows: List[dict]) -> None:
        self.write_rejection_record(rejected_rows)
        self.phase_sweep()
        self.phase_receipts()
        self.phase_plugins()
        self.phase_intents()
        self.phase_generate()


def roadmap_lines(plugins, root: Path, changes: List[dict]) -> List[str]:
    lines: List[str] = []
    for plug in plugins:
        fn = getattr(plug, "approval_lines", None)
        if fn is not None:
            lines.extend(fn(root, {"changes": changes}) or [])
    return lines


def open_daily_pr(root: Path) -> Optional[dict]:
    prs = gh_json(root, "pr", "list", "--state", "open", "--head", BRANCH, "--json", "number,headRefOid,body,url") or []
    return prs[0] if prs else None


def prepare_branch(root: Path, base: str) -> Optional[dict]:
    """Check out docs/maintain: the open PR's branch with origin/<base> merged in (a merge commit,
    never force-push), or a fresh branch from origin/<base>. A merge conflict closes the old PR."""
    pr = open_daily_pr(root)
    if pr is not None:
        git(root, "fetch", "-q", "origin", f"{BRANCH}:refs/remotes/origin/{BRANCH}")
        git(root, "checkout", "-q", "-B", BRANCH, f"origin/{BRANCH}")
        m = git(root, "merge", "--no-edit", "-q", f"origin/{base}", check=False)
        if m.returncode == 0:
            return pr
        git(root, "merge", "--abort", check=False)
        gh(root, "pr", "close", str(pr["number"]), "--comment", SUPERSEDED)
        git(root, "push", "-q", "origin", "--delete", BRANCH, check=False)
    git(root, "checkout", "-q", "-B", BRANCH, f"origin/{base}")
    return None


def maintain_here(root: Path, cfg: dict, slug: str, runtime: str, plugins=(), verify: bool = True,
                  push: bool = True, now: Optional[dt.datetime] = None) -> int:
    """`docs.py maintain --here`: phases, then open or update the ONE daily PR. 0 ok, 1 error."""
    root = Path(root)
    now = now or now_utc()
    base = cfg.get("base_branch", "main")
    rejected_rows = process_rejection(root, slug, now) if push else []
    pr = prepare_branch(root, base) if push else None
    m = Maintain(root, cfg, slug, runtime, plugins, verify=verify, push=push, now=now)
    m.run_phases(rejected_rows)
    ahead = int(git(root, "rev-list", "--count", f"origin/{base}..HEAD").stdout.strip() or 0)
    prior = changes_from_body(pr["body"]) if pr else []
    merged = {fp_key(c): c for c in prior}
    for c in m.changes:
        merged[fp_key(c)] = c
    all_changes = sorted(merged.values(), key=fp_key)
    for c in all_changes:  # titles/labels for approval text; prior changes carry only op/path/input
        if c["op"] == "receipt" and "title" not in c:
            c["title"] = doc_title(root, c["path"])
        if c["op"] == "generate" and "label" not in c:
            c["label"] = {"docs/README.md": "the docs map", "work/README.md": "the records index"}.get(
                c["path"], f"the generated file {c['path']}")
    if not m.changes and pr is None and ahead == 0:
        print(f"maintain {slug}: nothing to change" + ("".join(f"\n  note: {n}" for n in m.notes)))
        return 0
    section = approval_section(docs_lines(all_changes, m.notes), roadmap_lines(plugins, root, all_changes))
    body = pr_body(section, all_changes)
    if config.is_public(cfg):
        from docslib import namescan
        terms = namescan.load_list(namescan.default_list_path(cfg))
        if not terms:
            print("maintain: deny-term list missing; nothing published (fail closed)", file=sys.stderr)
            return 6
        hits = namescan.scan_items([("PR body", body), ("branch", BRANCH)], terms)
        if namescan.report(hits):
            print("maintain: the PR body would publish a protected name; nothing published", file=sys.stderr)
            return 6
    if not push:
        print(body)
        return 0
    if pr is not None:
        if ahead:
            git(root, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
        gh(root, "pr", "edit", str(pr["number"]), "--body", body, "--add-label", LABEL)
        number = pr["number"]
        print(f"maintain {slug}: updated daily PR #{number}")
    else:
        if ahead == 0:
            print(f"maintain {slug}: nothing to change" + ("".join(f"\n  note: {n}" for n in m.notes)))
            return 0
        if git(root, "ls-remote", "--exit-code", "--heads", "origin", BRANCH, check=False).returncode == 0:
            git(root, "push", "-q", "origin", "--delete", BRANCH)
        git(root, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
        out = gh(root, "pr", "create", "--base", base, "--head", BRANCH, "--title",
                 f"docs: daily maintenance {m.today.isoformat()}", "--body", body, "--label", LABEL).stdout
        mnum = re.search(r"/pull/(\d+)", out)
        number = int(mnum.group(1)) if mnum else None
        print(f"maintain {slug}: opened daily PR {out.strip()}")
    (state_dir(slug) / "open-pr.json").write_text(json.dumps(
        {"number": number, "changes": [{k: c[k] for k in ("op", "path", "input")} for c in all_changes]},
        indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


# ------------------------------------------------------------------ the routine (from the clone)

def worktree_for(entry: dict) -> Path:
    return data_home() / "worktrees" / entry["slug"]


def link_private_state(wt: Path, slug: str) -> None:
    """X4: a public repo's local-only state lives in ONE private dir, linked into every worktree."""
    priv = private_dir(slug)
    for rel in ("work", "docs/roadmap"):
        (priv / rel).mkdir(parents=True, exist_ok=True)
        dst = wt / rel
        if dst.is_symlink() or dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.symlink_to(priv / rel, target_is_directory=True)


def pin_behind(wt: Path, clone_root: Path) -> Optional[str]:
    """The clone's skill sha when the vendored PIN is older (an ancestor), else None."""
    pin = wt / "tools" / "docs" / "PIN"
    if not pin.exists():
        return None
    try:
        have = json.loads(pin.read_text(encoding="utf-8")).get("skill_sha")
    except ValueError:
        return None
    h = git(clone_root, "rev-parse", "HEAD", check=False)
    if h.returncode != 0:
        return None  # no dedicated clone to compare against
    head = h.stdout.strip()
    if not have or have == head:
        return None
    if git(clone_root, "merge-base", "--is-ancestor", have, head, check=False).returncode == 0:
        return head
    return None


def run_repo(entry: dict, runtime: str, clone_root: Path, band: str, docs_py: Path) -> Tuple[str, bool]:
    """One repo: (heartbeat status, success)."""
    path, slug, base = Path(entry["path"]), entry["slug"], entry.get("base_branch", "main")
    git(path, "fetch", "-q", "origin", base)
    reason = skip_reason(entry)
    if reason:
        return f"skipped {reason}", False
    if band == "small" and open_daily_pr(path) is None:
        return "skipped quota above 70% (no open daily PR to refresh)", False
    wt = worktree_for(entry)
    if wt.exists():
        git(path, "worktree", "remove", "--force", str(wt), check=False)
    git(path, "worktree", "prune")
    wt.parent.mkdir(parents=True, exist_ok=True)
    git(path, "worktree", "add", "-q", "--detach", str(wt), f"origin/{base}")
    try:
        cfg = config.load(wt) or {}
        if config.is_public(cfg):
            link_private_state(wt, slug)
        sha = pin_behind(wt, clone_root)
        if sha:
            up = subprocess.run([sys.executable, str(docs_py), "upgrade", "--repo", str(wt)], capture_output=True,
                                text=True)
            print(up.stdout + up.stderr)
        env = dict(os.environ, MANAGE_DOCS_ROUTINE="1", MANAGE_DOCS_RUNTIME=runtime)
        vend = wt / "tools" / "docs" / "docs.py"
        argv = [sys.executable, str(vend), "maintain", "--here", "--slug", slug]
        if band == "small":
            argv.append("--no-verify")
        p = subprocess.run(argv, cwd=wt, env=env, capture_output=True, text=True)
        print(p.stdout + p.stderr, end="")
        if p.returncode == 0:
            return "ran", True
        return f"error (maintain exit {p.returncode})", False
    finally:
        git(path, "worktree", "remove", "--force", str(wt), check=False)


def routine(runtime: str, clone_root: Path, docs_py: Path, only: Optional[str] = None) -> int:
    """`docs.py maintain` without --here: every enrolled repo (or one with --repo). 5 = lock busy or
    quota pause (reason printed)."""
    try:
        with RoutineLock():
            repos = [r for r in load_repos() if only in (None, r["slug"])]
            if not repos:
                print("maintain: no enrolled repos (~/.config/manage-docs/repos.json)" if only is None
                      else f"maintain: {only} is not in repos.json")
                return 1 if only else 0
            ran_unknown = False
            for entry in repos:
                q = quota(runtime, clone_root)
                if q["band"] == "pause":
                    print(f"maintain: quota {q['used']}% (>90%); stopping. Schedule the next run after "
                          f"resets_at={q['resets_at']}.")
                    return 5
                if q["band"] == "unknown":
                    if ran_unknown:
                        print(f"maintain: quota unknown ({q['reason']}); ran one repo, stopping to recheck next run.")
                        return 0
                    ran_unknown = True
                try:
                    status, ok = run_repo(entry, runtime, clone_root, q["band"], docs_py)
                except RoutineError as exc:
                    status, ok = f"error ({str(exc)[:120]})", False
                try:
                    heartbeat(Path(entry["path"]), status, ok)
                except RoutineError as exc:
                    print(f"maintain {entry['slug']}: heartbeat failed: {exc}", file=sys.stderr)
                print(f"maintain {entry['slug']}: {status}")
                if q["band"] == "checkpoint":
                    print("maintain: quota above 80%; finished this repo, stopping. HANDOFF: next run resumes "
                          "with the remaining repos.")
                    return 0
            return 0
    except Busy as exc:
        print(f"maintain: {exc}; exiting (5)")
        return 5


ROUTINE_PROMPT = """Run the manage-docs daily routine on this machine (manage-docs v2 section 8).

1. cd {clone} && git fetch -q origin {base} && git checkout -q --detach origin/{base}
2. MANAGE_DOCS_RUNTIME={runtime} python3 {skill_dir}/docs.py maintain
3. Report the output in one short message. Exit 5 means another run holds the lock or quota is above
   90%: if quota, schedule the next run after the printed resets_at. Never approve a migration map,
   never merge a daily PR, never post anywhere; {owner} approves daily PRs.
"""


SKILL_DIR = "/".join((".claude", "skills", "manage-docs"))  # the canonical skill dir in the home clone


def routine_prompt(runtime: str) -> str:
    return ROUTINE_PROMPT.format(runtime=runtime, skill_dir=SKILL_DIR, owner=site.get("owner"),
                                 base=site.get("home_base_branch"),
                                 clone="~/.local/share/manage-docs/" + site.get("home_clone_dir"))
