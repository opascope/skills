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

from docslib import checks, config, frontmatter, generate, receipts, records, site

BRANCH = "docs/maintain"
LABEL = "docs-maintain"
HEARTBEAT_TITLE = "manage-docs heartbeat"
APPROVAL_OPEN, APPROVAL_CLOSE = "<!-- manage-docs:approval -->", "<!-- /manage-docs:approval -->"
CHANGES_RX = re.compile(r"<!-- manage-docs:changes (.*?) -->", re.S)
INFLIGHT = re.compile(r"^docs/(bootstrap|migrate-|port-)")
# An open docs/upgrade-<sha> PR (checks.UPGRADE_BRANCH) is not a skip: run_repo gates it like the daily
# PR (review + CI at head) and holds the repo's maintenance until it lands.
SUPPRESS_DAYS = 30
SUPERSEDED = "manage-docs: superseded by a fresh run (conflict)"
REVIEW_COMMENT = "@codex review"
# A daily PR closed unmerged is rejected: its change suppresses for 30 days. An
# explicit `manage-docs:reject <reason>` on that closed PR names the reason; a close is the only trusted
# rejection trigger (free text on an OPEN PR is never inferred as a verdict).
LEGACY_REJECT_RX = re.compile(r"^manage-docs:reject\s*(.*)", re.S)
CODEX_LOGINS = {"chatgpt-codex-connector[bot]", "chatgpt-codex-connector"}  # mirrors codex_gate.py
USAGE_LIMIT_RX = re.compile(r"usage limit", re.I)
NWO_RX = re.compile(r"github\.com/([^/]+/[^/]+)/(?:pull|issues)/")


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


def state_after_merge(root: Path, number: int) -> str:
    """The PR's state read right after `gh pr merge` succeeded, upper-cased. Any failure to read it is ""
    (not MERGED), so the caller holds: the merge may have only queued the PR, and maintenance must never
    push to a queued branch it could not confirm landed."""
    try:
        return ((gh_json(root, "pr", "view", str(number), "--json", "state") or {}).get("state") or "").upper()
    except Exception:  # noqa: BLE001 -- unreadable state is a hold, whatever the cause
        return ""


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
    """The enrolled repos, one per checkout. When two entries share a checkout (a legacy slug left beside its
    renamed one), the one whose slug is a site.json seed key wins, else the first; the other is skipped with
    a warning, so one repo never runs twice in a cycle (Codex P2s on #1118)."""
    p = config_home() / "repos.json"
    if not p.exists():
        return []
    canonical = set(site.get("seeds") or {})
    out: List[dict] = []
    at: Dict[str, int] = {}
    for e in json.loads(p.read_text(encoding="utf-8")):
        key = str(Path(e["path"]).expanduser().resolve())
        if key not in at:
            at[key] = len(out)
            out.append(e)
            continue
        kept = out[at[key]]
        if e.get("slug") in canonical and kept.get("slug") not in canonical:
            out[at[key]], kept, e = e, e, kept
        print(f"manage-docs: repos.json lists {key} twice ({kept.get('slug')!r} and {e.get('slug')!r}); "
              f"running {kept.get('slug')!r}, skipping {e.get('slug')!r}", file=sys.stderr)
    return out


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


def closed_reason(comments: List[dict]) -> str:
    """A human-readable reason for suppressing a CLOSED (unmerged) daily PR. Only a privileged actor can
    close a PR, so reading its comments here is safe (unlike scanning an OPEN PR, which any commenter can
    write to). An explicit `manage-docs:reject <reason>` is used verbatim; otherwise "closed unmerged"."""
    for c in comments:
        m = LEGACY_REJECT_RX.match((c.get("body") or "").strip())
        if m:
            return (m.group(1).strip() or "(no reason given)")[:200]
    return "closed unmerged"


def suppress_fingerprints(slug: str, changes: List[dict], now: dt.datetime, reason: str,
                          pr_number) -> List[dict]:
    """Record a 30-day suppression for each change fingerprint {op, path, input}, dropping expired rows.
    Shared by the two rejection triggers: a PR closed unmerged (process_rejection) and a trusted,
    head-anchored findings verdict consumed at the merge gate (try_merge_daily_pr)."""
    new_rows = [{"op": ch["op"], "path": ch["path"], "input": ch["input"], "reason": reason,
                 "rejected_at": iso(now), "pr": pr_number} for ch in changes]
    keep = [r for r in load_rejected(slug) if (now - dt.datetime.fromisoformat(
        r["rejected_at"].replace("Z", "+00:00"))).days < SUPPRESS_DAYS]
    save_rejected(slug, keep + new_rows)
    return new_rows


def process_rejection(repo_root: Path, slug: str, now: dt.datetime) -> List[dict]:
    """Read the previously opened daily PR. A CLOSE that is not a merge rejects it: its fingerprints
    suppress for 30 days (no owner-only trigger). A MERGE is acceptance. An OPEN PR is
    left untouched here: a reviewer's "changes needed" is NOT inferred from free-text on an open PR
    (any commenter could forge it, a stale verdict could trigger it). The trusted rejection signals are a
    privileged CLOSE (handled here) and the merge gate's head-anchored findings verdict, which
    try_merge_daily_pr consumes directly (closing + suppressing) before this runs."""
    sp = state_dir(slug) / "open-pr.json"
    if not sp.exists():
        return []
    st = json.loads(sp.read_text(encoding="utf-8"))
    view = gh_json(repo_root, "pr", "view", str(st["number"]), "--json", "state,comments") or {}
    state = view.get("state", "OPEN")
    comments = view.get("comments") or []
    if state == "OPEN":
        return []  # leave it for the merge gate, a reviewer, or the lead
    if state == "MERGED":  # merged = accepted; clear the pointer, suppress nothing
        sp.replace(state_dir(slug) / f"merged-pr-{st['number']}.json")
        return []
    # CLOSED but not merged: a privileged actor closed it -> a rejection.
    if any((c.get("body") or "").strip() == SUPERSEDED for c in comments):
        sp.replace(state_dir(slug) / f"closed-pr-{st['number']}.json")
        return []  # the routine's own conflict-supersede close, not a rejection
    new_rows = suppress_fingerprints(slug, st.get("changes", []), now, closed_reason(comments), st["number"])
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


def pr_body(section: str, changes: List[dict], cfg: Optional[dict] = None) -> str:
    fps = sorted(({"op": c["op"], "path": c["path"], "input": c["input"]} for c in changes),
                 key=lambda c: fp_key(c))
    return ("Daily docs maintenance. Reviewed like any agent PR (a review is requested when the head "
            "moves). A later routine run may merge this automatically once there is a clean independent "
            "review at the current head AND green required CI at that head, pinned to the reviewed head; "
            "a reviewer or a maintainer can also merge it. The "
            "routine never merges on its own judgement.\n\n"
            + section + "\n\n<!-- manage-docs:changes " + json.dumps(fps, sort_keys=True) + " -->\n")


def changes_from_body(body: str) -> List[dict]:
    m = CHANGES_RX.search(body or "")
    return json.loads(m.group(1)) if m else []


def ensure_label(root: Path) -> None:
    """Create the daily-PR marker label if it is missing, so --label/--add-label never fails on a repo
    that has not had `init --stage local` run since the label was renamed. Idempotent; never fatal."""
    gh(root, "label", "create", LABEL, "--color", "D4C5F9", "--description",
       "manage-docs daily docs maintenance PR", check=False)


def request_review(root: Path, number: int, env: Optional[Dict[str, str]] = None) -> bool:
    """Request an independent review on the daily PR. The comment is a human-visible emission, so it is
    posted ONLY through the ledgered command the routine injects (MANAGE_DOCS_REVIEW_CMD, resolved from
    site.json for EVERY enrolled repo so no repo takes an unledgered path). There is deliberately NO
    plain `gh pr comment` fallback: with no command (a direct/manual maintain_here call outside the
    routine) the request is skipped with a loud note rather than posted unledgered. The routine never
    merges; it only asks for review.

    Returns True only when a review was actually posted, False when it was skipped for lack of a command,
    and raises on a configured command that fails. The caller must advance its recorded requested-head
    ONLY on True, so a skipped (or failed) request leaves the head pending and a later run with the
    command retries, instead of a manual no-command run marking the head as reviewed forever."""
    src = os.environ if env is None else env  # the routine passes review_env() for an upgrade PR
    comment = src.get("MANAGE_DOCS_REVIEW_COMMENT") or REVIEW_COMMENT
    cmd_json = src.get("MANAGE_DOCS_REVIEW_CMD")
    if not cmd_json:
        print(f"maintain: review not requested on PR #{number} (no ledgered comment command configured); "
              "a reviewer or the lead requests it", file=sys.stderr)
        return False
    try:
        cmd = json.loads(cmd_json)
    except ValueError as exc:
        raise RoutineError(f"MANAGE_DOCS_REVIEW_CMD is not valid JSON: {exc}")
    p = subprocess.run([*cmd, str(number), comment], cwd=root, capture_output=True, text=True)
    if p.returncode != 0:
        # Fail-closed: a ledger refusal (or any failure) means the comment did not post. Surface it
        # loudly; the PR stays open and unreviewed until a human re-triggers review.
        raise RoutineError(f"review request not posted (ledgered command exit {p.returncode}): "
                           f"{(p.stderr or p.stdout).strip()[:300]}")
    return True


# ------------------------------------------------------------------ deterministic merge

def _nwo_from_url(url: Optional[str]) -> Optional[str]:
    m = NWO_RX.search(url or "")
    return m.group(1) if m else None


def merge_gate_cmd(slug: str, clone_root: Path) -> Optional[list]:
    """The command that reports a PR's independent-review verdict by exit code (0 = clean at head), or
    None when this repo has no gate configured and so never auto-merges. A home repo wires its reviewer
    gate (e.g. the Codex gate) in site.json `merge_gate.command`, per seed; `MANAGE_DOCS_MERGE_GATE_CMD`
    overrides it. The gate's script path is resolved absolute against the home clone (pinned to
    origin/<base>), NOT the enrolled checkout, so a stale or locally modified gate on a feature branch
    cannot return a false verdict. The verdict logic is NOT reimplemented here: this only runs the gate
    and reads its exit code (0 clean, 1 open findings, 2 not-reviewed-at-head/stale, else unknown)."""
    env = os.environ.get("MANAGE_DOCS_MERGE_GATE_CMD")
    if env:
        try:
            return json.loads(env)  # an explicit override (e.g. a test) is used verbatim
        except ValueError as exc:
            raise RoutineError(f"MANAGE_DOCS_MERGE_GATE_CMD is not valid JSON: {exc}")
    rr = dict(site.get("merge_gate") or {})
    seed = (site.get("seeds") or {}).get(slug) or {}
    rr.update(seed.get("merge_gate") or {})
    cmd = rr.get("command")
    return _abs_cmd(cmd, clone_root) if cmd else None


CI_PASS_STATES = {"SUCCESS", "NEUTRAL", "SKIPPED", "SKIPPING", "PASS"}


def ci_green(root: Path, number: int) -> bool:
    """Only the REQUIRED checks must be green. `gh pr checks --required` returns just the
    branch-required checks for the PR's current head, so a pending or failed OPTIONAL workflow (a repo
    may run several alongside its required check) never makes a clean PR look red. Green iff at least one
    required check is reported and every one is in a pass state."""
    p = gh(root, "pr", "checks", str(number), "--required", "--json", "state,bucket", check=False)
    out = (p.stdout or "").strip()
    if not out:
        return False  # gh prints nothing + exits non-zero when there are no required checks -> not provably green
    try:
        data = json.loads(out)
    except ValueError:
        return False
    if not data:
        return False
    return all((c.get("state") or c.get("bucket") or "").upper() in CI_PASS_STATES for c in data)


def codex_capped_at_head(root: Path, nwo: str, number: int, head: str) -> bool:
    """A reviewer usage-limit reply newer than the head commit means no review is coming for this head
    (`.claude/skills/codex-comments/SKILL.md`, "Fallback: Codex hit its usage limit"). Best-effort: any
    read failure returns False so it only ever refines the report, never blocks."""
    try:
        comments = gh_json(root, "api", f"repos/{nwo}/issues/{number}/comments", "--paginate") or []
        capped = [c.get("created_at") for c in comments
                  if (c.get("user") or {}).get("login") in CODEX_LOGINS
                  and USAGE_LIMIT_RX.search(c.get("body") or "")]
        if not capped:
            return False
        commit = gh_json(root, "api", f"repos/{nwo}/commits/{head}") or {}
        head_at = (((commit.get("commit") or {}).get("committer") or {}).get("date")) or ""
        return bool(head_at) and max(c for c in capped if c) > head_at
    except (RoutineError, ValueError, KeyError, TypeError):
        return False


def behind_base_note(root: Path, nwo: Optional[str], slug: str, number: int, label: str) -> Optional[str]:
    """update_before_merge (site.json): None when the PR may merge now, else why it waits. A BEHIND branch
    is updated (merge commit, never force) and left for the next run's review + CI at the new head; an
    unknown merge state also defers, because it cannot confirm the branch is current."""
    if not (nwo and slug in (site.get("update_before_merge") or [])):
        return None
    mss = (gh_json(root, "pr", "view", str(number), "--json", "mergeStateStatus") or {}).get("mergeStateStatus")
    if mss == "BEHIND":
        upd = gh(root, "pr", "update-branch", str(number), "--repo", nwo, check=False)
        if upd.returncode != 0:
            return (f"{label}: left open (branch behind base; update failed: "
                    f"{(upd.stderr or upd.stdout).strip()[:100]})")
        return f"{label}: updated branch to base; a clean review + green CI at the new head lands it next run"
    if mss in (None, "", "UNKNOWN"):
        return f"{label}: left open (merge state {mss or 'unknown'}; cannot confirm the branch is current with base)"
    return None


def try_merge_daily_pr(root: Path, entry: dict, clone_root: Path,
                       now: Optional[dt.datetime] = None) -> Optional[str]:
    """Deterministic merge of an open docs-maintain PR at the START of a routine run:
    merge ONLY on a clean independent review at the CURRENT head AND green required CI, pinned to that
    head with `--match-head-commit`. Never on the routine's own judgement. Any miss leaves the PR for a
    reviewer or the lead. Returns a one-line outcome, or None when there is no open daily PR.

    `root` is the enrolled checkout the PR lives in; `clone_root` is the pinned home clone the merge gate
    resolves against, so a stale or modified gate on the feature branch cannot return a false verdict."""
    now = now or dt.datetime.now(dt.timezone.utc)
    slug = entry["slug"]
    pr = open_daily_pr(root)
    if pr is None:
        return None
    number, head = pr["number"], pr.get("headRefOid") or ""
    # open_daily_pr selects the PR by its docs/maintain HEAD only; the merge path must also confirm the
    # PR still targets the configured base. A PR retargeted to another branch (e.g. main) would otherwise
    # be reviewed, closed, or MERGED INTO THAT BRANCH here, and --match-head-commit pins only the head,
    # not the base. Refuse to run the gate or touch the PR when the base differs; leave it for a human.
    want_base = entry.get("base_branch", "main")
    base = pr.get("baseRefName")
    if base != want_base:
        return (f"daily PR #{number}: left untouched (PR base is {base!r}, not the configured "
                f"{want_base!r}; refusing to gate, close, or merge a retargeted PR)")
    cmd = merge_gate_cmd(slug, clone_root)
    if not cmd:
        return f"daily PR #{number}: left open (no merge gate; a reviewer or the lead merges)"
    if not head:
        return f"daily PR #{number}: left open (could not read the PR head)"
    nwo = _nwo_from_url(pr.get("url"))
    repo_args = ["--repo", nwo] if nwo else []
    # 1 + 3: an independent review is clean AND head-anchored (the gate exits 0 only at the reviewed head)
    try:
        gate = subprocess.run([*cmd, *repo_args, "--pr", str(number), "--head", head], cwd=clone_root,
                              capture_output=True, text=True)
    except OSError as exc:
        return f"daily PR #{number}: left open (merge gate failed to run: {exc})"
    if gate.returncode == 1:
        # OPEN findings head-anchored to the current head is a TRUSTED rejection (codex_gate reads the
        # review + review-thread surfaces that `gh pr view --json comments` cannot). try_merge CLOSES the
        # PR on this verdict and nothing more: the suppression is left to process_rejection, which runs
        # next (maintain_here), sees the now-CLOSED PR this branch set, and writes BOTH records -- the
        # 30-day suppression in rejected.json (suppress_fingerprints) AND the committed work/runs
        # rejection record (write_rejection_record) -- then rotates the pointer. Closing FIRST and
        # suppressing SECOND means a failed close suppresses nothing (no "held back but still open"
        # inconsistency), and process_rejection never has to read inline findings: it keys only off the
        # CLOSED state. Closing is a retraction, not new wire content, so it is not a ledgered emission.
        # The gate verdict covers the `head` captured at the top of this run. If another actor pushed a
        # fix in between, that new head was never rejected, and `gh pr close` has no --match-head-commit
        # guard; re-read the live head and abort the close unless it still equals the reviewed head
        # (`.claude/rules/pr-review.md`: a verdict binds only the head it named).
        live = (gh_json(root, "pr", "view", str(number), "--json", "headRefOid") or {}).get("headRefOid") or ""
        if live and live != head:
            return (f"daily PR #{number}: left open (head moved to {live[:10]} under the review; "
                    "not closing on a verdict that did not cover it)")
        try:
            gh(root, "pr", "close", str(number))
        except RoutineError as exc:
            return f"daily PR #{number}: left open (could not close after review findings: {str(exc)[:100]})"
        git(root, "push", "-q", "origin", "--delete", BRANCH, check=False)
        return (f"daily PR #{number}: closed (independent review found changes at head; "
                "process_rejection holds the fingerprints back 30 days)")
    if gate.returncode != 0:
        if gate.returncode == 2 and nwo and codex_capped_at_head(root, nwo, number, head):
            return f"daily PR #{number}: left open (review capped; a fallback reviewer is a lead action)"
        verdict = {2: "not reviewed at head", 3: "review state unknown"}.get(
            gate.returncode, f"gate exit {gate.returncode}")
        return f"daily PR #{number}: left open (review not clean at head: {verdict})"
    # 2: only the REQUIRED CI must be green
    if not ci_green(root, number):
        return f"daily PR #{number}: left open (required CI not green at head)"
    # update_before_merge (a repo opts into it in site.json; see initrepo.UPDATE_BEFORE_MERGE): these
    # repos land a PR only after its branch is current with the latest base, so a merge never ships a head
    # whose review + CI predated an advance of the base. The decision is POSITIVE: merge only when GitHub
    # reports the branch is NOT behind its base (mergeStateStatus != "BEHIND"), never merely because an
    # update call did not error. If it is BEHIND, update the branch (merge commit, never force) and leave
    # the PR whatever the update's outcome -- a BEHIND branch must never land on a stale review/CI, so a
    # failed update defers rather than merges; the next run re-reviews and re-checks CI at the updated
    # head. An unknown/uncomputed state also defers, because it cannot confirm the branch is current.
    behind = behind_base_note(root, nwo, slug, number, f"daily PR #{number}")
    if behind:
        return behind
    try:
        gh(root, "pr", "merge", str(number), "--squash", "--match-head-commit", head)
    except RoutineError as exc:
        return f"daily PR #{number}: left open (merge refused, head may have moved: {str(exc)[:120]})"
    # under a merge queue a successful `gh pr merge` only enqueues the PR; run_repo must not push to the
    # queued docs/maintain branch meanwhile (the upgrade PR's rule, applied to the daily PR)
    state = state_after_merge(root, number)
    if state != "MERGED":
        return f"daily PR #{number}: queued to merge at {head[:10]} (state {state or 'unknown'}); holding until it lands"
    return f"daily PR #{number}: merged (clean review + green CI at {head[:10]})"


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
        # .beads/ is never swept in by the catch-all: a live Beads db rewrites .beads/issues.jsonl on
        # any bd call, so a maintain run that touched Beads for its own reasons must not commit that
        # drift. The roadmap plugin's Beads export is the one maintain writer of .beads/, and it names
        # its path explicitly (phase_plugins), so the guard only drops .beads/ the caller did not ask for.
        if not any(p == ".beads" or p.startswith(".beads/") for p in (paths or [])):
            git(self.root, "reset", "-q", "--", ".beads", check=False)
        # a link to a path outside the repo (the private state dir) never lands in a commit
        for rel in escaping_symlinks(self.root):
            git(self.root, "reset", "-q", "--", rel, check=False)
            self.notes.append(f"Left out {rel}: it links to a path outside the repo.")
        if git(self.root, "diff", "--cached", "--quiet", check=False).returncode == 0:
            return False
        git(self.root, "commit", "-q", "--no-verify", "-m", msg)
        self.index.refresh()
        return True

    def note_suppressed(self, c: dict) -> bool:
        r = suppressed(c, self.rejected, self.now)
        if r:
            self.notes.append(f"Held back a change rejected on PR #{r['pr']} ({r['reason']}): {c['path']}.")
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
            # stage exactly what the sweep touched: each record's new path (its deletion at the old
            # path is already staged by `git mv`, or was untracked), plus this run's ledger dir. Never
            # the whole working tree. The old path is left out because a pathspec matching nothing on
            # disk errors ("did not match any files").
            moved = [r["dest"] for r in rows] + [self.run_dir]
            self.commit(f"docs(maintain): archive {len(rows)} finished records", moved)

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
        written: List[str] = []
        for plug in self.plugins:
            fn = getattr(plug, "maintain", None)
            if fn is None:
                continue
            for c in fn(self) or []:
                if self.note_suppressed(c):
                    continue
                self.changes.append(c)
                written.append(c["path"])
        # stage exactly the paths the plugins wrote (ROADMAP.md and, named explicitly so the commit
        # guard keeps it, .beads/issues.jsonl); nothing to commit when no plugin wrote anything
        if written:
            self.commit("docs(maintain): roadmap status and Beads export", written)
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
        # A generator pointed at a hand-written file (no manage-docs:generated marker) refuses rather
        # than clobber it. In the daily run that is a note for the operator to set docs.json, never a
        # crash and never a silent overwrite; the next run regenerates once the config is fixed.
        try:
            _, changed = generate.run(self.root, self.cfg, check=True)
            if not changed:
                return
            generate.run(self.root, self.cfg)
        except generate.GenerateError as exc:
            self.notes.append(str(exc))
            return
        for fp in generate.fingerprints(self.root, self.cfg, changed):
            if self.note_suppressed(fp):
                if git(self.root, "ls-files", "--error-unmatch", fp["path"], check=False).returncode == 0:
                    git(self.root, "checkout", "--", fp["path"])
                else:
                    (self.root / fp["path"]).unlink()
                continue
            fp["label"] = {config.docs_map_path(self.cfg): "the docs map",
                           "work/README.md": "the records index"}.get(
                fp["path"], f"the generated file {fp['path']}")
            self.changes.append(fp)
        self.commit("docs(maintain): regenerate generated docs")

    # -- phase 6: repo-wide warnings into the daily PR body (X12)
    def phase_warnings(self) -> None:
        """Surface the repo-wide signals (broken links anywhere, existing misplaced files, entry-point
        staleness and dead path references) as notes in the daily PR. WARN-only: they report, they
        never fail a PR and never block the run. No commit; notes ride along the daily PR body."""
        ctx = checks.Ctx(self.root, self.cfg, scope="all")
        for r in checks.daily_warnings(ctx):
            self.notes.append(f"{r['path']}: {r['message']}")

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
            self.commit("docs(maintain): record the rejected daily PR", [self.run_dir])

    def run_phases(self, rejected_rows: List[dict]) -> None:
        self.write_rejection_record(rejected_rows)
        self.phase_sweep()
        self.phase_receipts()
        self.phase_plugins()
        self.phase_intents()
        self.phase_generate()
        self.phase_warnings()


def roadmap_lines(plugins, root: Path, changes: List[dict]) -> List[str]:
    lines: List[str] = []
    for plug in plugins:
        fn = getattr(plug, "approval_lines", None)
        if fn is not None:
            lines.extend(fn(root, {"changes": changes}) or [])
    return lines


def open_upgrade_pr(root: Path) -> Optional[dict]:
    """The repo's own open docs/upgrade-<sha> PR (never a fork's), or None."""
    # the branch name carries a sha, so --head cannot select it; list past gh's default 30 so an upgrade PR
    # behind other open PRs is never missed (and never duplicated by the PIN-behind path; Codex P2, #1117)
    prs = gh_json(root, "pr", "list", "--state", "open", "--limit", "1000", "--json",
                  "number,headRefName,headRefOid,baseRefName,isCrossRepository,url") or []
    own = [p for p in prs if not p.get("isCrossRepository")
           and checks.UPGRADE_BRANCH.match(p.get("headRefName") or "")]
    return own[0] if own else None


def try_merge_upgrade_pr(root: Path, entry: dict, clone_root: Path, pr: dict) -> str:
    """Merge an open upgrade PR ONLY on a clean independent review at its CURRENT head plus green required
    CI, pinned with --match-head-commit: the daily PR's gate. Never --auto, never on the
    routine's own judgement. Unlike the daily PR, findings never close it: an engine upgrade is left open
    for a human whatever the verdict. Returns a one-line outcome; "merged (" only when it landed."""
    number, head = pr["number"], pr.get("headRefOid") or ""
    label = f"upgrade PR #{number}"
    want_base = entry.get("base_branch", "main")
    if pr.get("baseRefName") != want_base:
        return f"{label}: left untouched (PR base is {pr.get('baseRefName')!r}, not {want_base!r})"
    cmd = merge_gate_cmd(entry["slug"], clone_root)
    if not cmd:
        return f"{label}: left open (no merge gate; a reviewer or the lead merges)"
    if not head:
        return f"{label}: left open (could not read the PR head)"
    nwo = _nwo_from_url(pr.get("url"))
    repo_args = ["--repo", nwo] if nwo else []
    try:
        gate = subprocess.run([*cmd, *repo_args, "--pr", str(number), "--head", head], cwd=clone_root,
                              capture_output=True, text=True)
    except OSError as exc:
        return f"{label}: left open (merge gate failed to run: {exc})"
    if gate.returncode != 0:
        if gate.returncode == 2 and nwo and codex_capped_at_head(root, nwo, number, head):
            return f"{label}: left open (review capped; a fallback reviewer is a lead action)"
        verdict = {1: "open findings", 2: "not reviewed at head", 3: "review state unknown"}.get(
            gate.returncode, f"gate exit {gate.returncode}")
        return f"{label}: left open (review not clean at head: {verdict})"
    if not ci_green(root, number):
        return f"{label}: left open (required CI not green at head)"
    behind = behind_base_note(root, nwo, entry["slug"], number, label)
    if behind:
        return behind
    try:
        gh(root, "pr", "merge", str(number), "--squash", "--match-head-commit", head)
    except RoutineError as exc:
        return f"{label}: left open (merge refused, head may have moved: {str(exc)[:120]})"
    # under a merge queue a successful `gh pr merge` only enqueues the PR; maintenance must keep waiting
    # until GitHub reports it MERGED, or it would run on the old vendored engine (Codex P2, #1117)
    state = state_after_merge(root, number)
    if state != "MERGED":
        return f"{label}: queued to merge at {head[:10]} (state {state or 'unknown'}); holding until it lands"
    return f"{label}: merged (clean review + green CI at {head[:10]})"


def upgrade_review_state(slug: str) -> Path:
    return state_dir(slug) / "upgrade-pr.json"


def ensure_upgrade_review(root: Path, slug: str, pr: dict, renv: Dict[str, str]) -> str:
    """Ask for the ledgered review whenever the upgrade PR's head is not the head a review was last
    successfully requested on: a new PR, pushed fixes, a behind_base_note update, or a failed earlier
    request. Without this a moved head stays unreviewed and the repo's maintenance waits forever (Codex
    P1, #1117). The requested head advances only when request_review actually posted."""
    number, head = pr["number"], pr.get("headRefOid") or ""
    sp = upgrade_review_state(slug)
    prior = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    if not head or (prior.get("number") == number and prior.get("review_requested_head") == head):
        return ""
    if not request_review(root, number, env=renv):
        return "; review not requested: no ledgered command"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps({"number": number, "review_requested_head": head}, sort_keys=True) + "\n",
                  encoding="utf-8")
    return f"; review requested at {head[:10]}"


def open_daily_pr(root: Path) -> Optional[dict]:
    prs = gh_json(root, "pr", "list", "--state", "open", "--head", BRANCH, "--json",
                  "number,headRefOid,baseRefName,isCrossRepository,body,url") or []
    # `gh pr list --head` filters by branch NAME only (it does not accept owner:branch), so on a public
    # repo a fork can open a PR whose head branch is also `docs/maintain`. Selecting the first match could
    # let the routine gate, close, or auto-merge an unrelated fork PR. Consider ONLY the managed repo's
    # own branch: a cross-repository (fork) head is never the daily PR.
    own = [p for p in prs if not p.get("isCrossRepository")]
    return own[0] if own else None


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
            c["label"] = {config.docs_map_path(cfg): "the docs map",
                          "work/README.md": "the records index"}.get(
                c["path"], f"the generated file {c['path']}")
    if not m.changes and pr is None and ahead == 0:
        print(f"maintain {slug}: nothing to change" + ("".join(f"\n  note: {n}" for n in m.notes)))
        return 0
    section = approval_section(docs_lines(all_changes, m.notes), roadmap_lines(plugins, root, all_changes))
    body = pr_body(section, all_changes, cfg)
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
    ensure_label(root)
    local_head = git(root, "rev-parse", "HEAD").stdout.strip()
    sp = state_dir(slug) / "open-pr.json"
    prior = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    prior_requested = prior.get("review_requested_head")  # the head a review was last requested on, if any
    if pr is not None:
        # The head MOVED this run only when local HEAD differs from the PR's remote head. `ahead`
        # (origin/base..HEAD) stays positive for an unchanged PR because it still holds its original
        # commits, so it must NOT drive the push decision: that would re-push an unchanged branch.
        remote_head = git(root, "rev-parse", f"origin/{BRANCH}", check=False).stdout.strip()
        if local_head != remote_head:
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

    def save_state(requested_head):
        sp.write_text(json.dumps(
            {"number": number, "changes": [{k: c[k] for k in ("op", "path", "input")} for c in all_changes],
             "review_requested_head": requested_head}, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    # Persist the PR + its changes FIRST, keeping the prior requested-head, so the state file always
    # reflects an existing PR even if the review request below fails. A review is (re)requested whenever
    # the reviewable head differs from the head a review was last SUCCESSFULLY requested on: a new PR
    # (prior None), a moved head, or a prior run whose request raised before it recorded success. Advance
    # the requested head ONLY when request_review actually posted (returns True); a skipped request (no
    # command, e.g. a manual run) or a raised one leaves the head pending so a later run with the ledgered
    # command retries, instead of marking the head reviewed when no review was ever asked for.
    save_state(prior_requested)
    if number is not None and prior_requested != local_head:
        if request_review(root, number):
            save_state(local_head)
    return 0


# ------------------------------------------------------------------ the routine (from the clone)

def worktree_for(entry: dict) -> Path:
    return data_home() / "worktrees" / entry["slug"]


def _abs_cmd(cmd: list, clone_root: Path) -> list:
    """Resolve a relative `.py` script element against the home clone, so a command vendored in the home
    repo (e.g. the ledgered comment helper) runs even with cwd set to another repo's worktree."""
    out = []
    for a in cmd:
        if isinstance(a, str) and a.endswith(".py") and not os.path.isabs(a):
            out.append(str((Path(clone_root) / a).resolve()))
        else:
            out.append(a)
    return out


def review_env(slug: str, clone_root: Path) -> Dict[str, str]:
    """The review-request env the routine hands the vendored maintain_here: the comment text and the
    LEDGERED comment command. The command is resolved from site.json for EVERY enrolled repo (top-level
    `review_request`, optionally overridden per seed), never only for one, so no repo takes an unledgered
    path; its script path is made absolute against the home clone. `MANAGE_DOCS_REVIEW_CMD` already in the
    environment wins (an override / test seam). This runs only from the home clone, where site.json
    exists. A repo with no command configured anywhere gets the comment text only, and maintain_here then
    skips the request rather than post it unledgered."""
    rr = dict(site.get("review_request") or {})
    seed = (site.get("seeds") or {}).get(slug) or {}
    rr.update(seed.get("review_request") or {})
    out = {"MANAGE_DOCS_REVIEW_COMMENT": str(rr.get("comment") or REVIEW_COMMENT)}
    env_cmd = os.environ.get("MANAGE_DOCS_REVIEW_CMD")
    if env_cmd:
        out["MANAGE_DOCS_REVIEW_CMD"] = env_cmd
    elif rr.get("command"):
        out["MANAGE_DOCS_REVIEW_CMD"] = json.dumps(_abs_cmd(rr["command"], clone_root))
    return out


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
    # A repo's `work/` and `docs/roadmap/` ignore lines end in a slash, which matches a
    # directory but never a symlink, so the links themselves must be excluded too.
    exclude_private_links(wt)


PRIVATE_LINKS = ("/work", "/docs/roadmap")


def exclude_private_links(wt: Path) -> None:
    """List the private-state links in the checkout's git exclude file (no trailing slash)."""
    r = git(wt, "rev-parse", "--git-path", "info/exclude", check=False)
    if r.returncode != 0:
        return  # not a git checkout
    fp = Path(r.stdout.strip())
    if not fp.is_absolute():
        fp = wt / fp
    have = fp.read_text(encoding="utf-8").splitlines() if fp.exists() else []
    missing = [p for p in PRIVATE_LINKS if p not in have]
    if missing:
        fp.parent.mkdir(parents=True, exist_ok=True)
        lead = "\n" if have and have[-1] != "" else ""
        with open(fp, "a", encoding="utf-8") as fh:
            fh.write(lead + "\n".join(missing) + "\n")


def escaping_symlinks(root: Path) -> List[str]:
    """Staged symlinks whose target is absolute or leaves the repo (they would publish a local path)."""
    out = git(root, "diff", "--cached", "--name-only", "--diff-filter=AMT", "-z", check=False).stdout
    bad = []
    for rel in filter(None, out.split("\0")):
        p = root / rel
        if not p.is_symlink():
            continue
        target = os.readlink(p)
        if os.path.isabs(target):
            bad.append(rel)
            continue
        base = Path(os.path.abspath(root))
        if not Path(os.path.abspath(p.parent / target)).is_relative_to(base):
            bad.append(rel)
    return bad


def pin_behind(wt: Path, clone_root: Path) -> Optional[str]:
    """The clone's skill sha when the vendored PIN is older (an ancestor), else None."""
    pin = wt / "tools" / "docs" / "PIN"
    if not pin.exists():
        return None
    try:
        have = json.loads(pin.read_text(encoding="utf-8")).get("skill_sha")
    except ValueError:
        return None
    if not Path(clone_root).is_dir():
        return None  # no dedicated clone to compare against (git cannot even start in a missing folder)
    h = git(clone_root, "rev-parse", "HEAD", check=False)
    if h.returncode != 0:
        return None  # not a git checkout
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
    # An open engine upgrade PR goes through the daily PR's gate (clean review + green CI at head, never
    # --auto). Until it lands, the repo's maintenance waits: the next maintenance should run the new engine.
    renv = review_env(slug, clone_root)
    upr = open_upgrade_pr(path)
    if upr is not None:
        try:
            unote = try_merge_upgrade_pr(path, entry, clone_root, upr)
        except RoutineError as exc:
            unote = f"upgrade PR #{upr['number']}: merge check errored ({str(exc)[:120]})"
        if "merged (" not in unote and upr.get("baseRefName") == base:
            # a PR retargeted off the configured base is left entirely alone: no review request either
            # (the daily-PR safeguard; Codex P2, #1117). Re-read the head: behind_base_note may have
            # just updated the branch
            live = (gh_json(path, "pr", "view", str(upr["number"]), "--json", "headRefOid") or {})
            try:
                unote += ensure_upgrade_review(path, slug, dict(upr, **{k: v for k, v in live.items() if v}),
                                               renv)
            except RoutineError as exc:
                unote += f"; review request failed ({str(exc)[:100]})"
        print(f"maintain {slug}: {unote}")
        if "merged (" not in unote:
            return f"skipped ({unote}; maintenance waits for it)", False
        git(path, "fetch", "-q", "origin", base)
    # At the start of the run, merge yesterday's daily PR if (and only if) its review is clean at head
    # and required CI is green; otherwise leave it. This is cheap (no model, no worktree), so it runs
    # before the quota band gate.
    try:
        note = try_merge_daily_pr(path, entry, clone_root)
    except RoutineError as exc:
        note = f"daily PR: merge check errored ({str(exc)[:120]})"
    if note:
        print(f"maintain {slug}: {note}")
        if "merged (" in note:
            git(path, "fetch", "-q", "origin", base)  # pick up the merge so the worktree is cut post-merge
        elif "holding until it lands" in note:
            return f"skipped ({note}; maintenance waits for it)", False
    # A daily PR retargeted away from the configured base must be left ENTIRELY alone, not just unmerged:
    # try_merge above refuses to gate/close/merge it, but maintain_here would still select it by its
    # docs/maintain head and push commits, edit its body, and re-request review on the wrong base. Skip
    # the whole repo for this run and leave the PR for a human (same class as the merge-path base guard).
    daily = open_daily_pr(path)
    if daily is not None and daily.get("baseRefName") not in (None, base):
        return (f"skipped (daily PR #{daily['number']} targets {daily.get('baseRefName')!r}, not "
                f"{base!r}; left untouched for a human)", False)
    if band == "small" and daily is None:
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
            # Open the upgrade PR (never --auto), ask for the ledgered independent review, and hold this
            # repo's maintenance until a later run lands it through try_merge_upgrade_pr.
            # --repo is a top-level option: it goes before the subcommand (`upgrade --repo` was a usage error,
            # so the old auto-upgrade never ran)
            up = subprocess.run([sys.executable, str(docs_py), "--repo", str(wt), "upgrade"], capture_output=True,
                                text=True)
            print(up.stdout + up.stderr)
            if up.returncode != 0:
                return f"error (upgrade exit {up.returncode})", False
            m = re.search(r"upgrade PR \S*/pull/(\d+)", up.stdout)
            if m:
                number = int(m.group(1))
                pr = {"number": number, "headRefOid": git(wt, "rev-parse", "HEAD").stdout.strip()}
                try:
                    asked = ensure_upgrade_review(path, slug, pr, renv)
                except RoutineError as exc:
                    # the requested head is not recorded, so the next run asks again
                    return f"upgrade PR #{number} opened; review request failed ({str(exc)[:120]})", False
                return f"upgrade PR #{number} opened{asked}; maintenance waits for it", True
        env = dict(os.environ, MANAGE_DOCS_ROUTINE="1", MANAGE_DOCS_RUNTIME=runtime, **renv)
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
   90%: if quota, schedule the next run after the printed resets_at.

This run: for each repo it first merges yesterday's daily docs PR ONLY when an
independent review is clean at the PR's current head AND required CI is green there, pinned to that
head (`--match-head-commit`); any miss leaves the PR for a reviewer or the lead. It then opens or
updates today's daily docs PR and requests a review on it (the ledgered `@codex review`). When a repo's
vendored tools are behind, it opens that repo's upgrade PR (docs/upgrade-<sha>, never auto-merged),
requests the same ledgered review on it, merges it only through the same gate, and holds that repo's
maintenance until it lands. It NEVER merges on its own judgement (only on a
clean review at head plus green CI), NEVER approves a migration map, and NEVER posts anywhere except
the ledgered review request on the repo's own daily PR or its own upgrade PR. A fallback reviewer,
when Codex is capped, is a lead action, not this routine's.
"""


SKILL_DIR = "/".join((".claude", "skills", "manage-docs"))  # the canonical skill dir in the home clone


def routine_prompt(runtime: str) -> str:
    return ROUTINE_PROMPT.format(runtime=runtime, skill_dir=SKILL_DIR,
                                 base=site.get("home_base_branch"),
                                 clone="~/.local/share/manage-docs/" + site.get("home_clone_dir"))
