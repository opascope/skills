"""init, preflight, vendoring and upgrade (manage-docs v2 sections 4, 5, 12; contract sections 2, 9).

- `vendor(skill_root, repo)` copies the vendored set into `tools/docs/` and writes `tools/docs/PIN`
  = {"skill_sha", "files": {relpath: sha256}}; CI checks the files against it (contract section 2).
- `pin_proof(skill_root, pin)` is the LOCAL trust-root proof pasted into the PR body: the skill sha is
  an ancestor of gustav origin/working and every vendored file matches its recorded hash.
- `preflight(repo)` is contract section 2 step 0 / section 9; it prints the exact commands for Max.
- `init(stage)`: `pr1` (tools/docs, workflow, docs.json, .gitignore, optional DECISIONS append),
  `migration` (router, generated maps, committed hooks, placement hook: NEW files only), `wire` (after
  the migration PR merges: the same router, hook and placement edits to files the repo already
  tracked), `local` (hooksPath, ruleset, label, heartbeat issue, machine config). Untracked machine
  setup never lands in a PR.
- Why `wire` is separate: the engine proves every code path and every kept doc byte-identical to the
  base inside the migration PR (engine/INTERFACE.md section 5), so an edit to an existing tracked
  AGENTS.md, .claude/settings.json or husky hook there fails verify.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from docslib import config, generate, receipts, routine

VENDOR_FILES = ["docs.py", "roadmap.py", "engine/migrate.py", "engine/INTERFACE.md", "hooks/where_hook.py"]
VENDOR_GLOBS = ["docslib/*.py", "templates/**/*"]
WORKFLOW_SRC = "templates/workflows/docs-verify.yml"
WORKFLOW_DST = ".github/workflows/docs-verify.yml"
GITHOOKS = ["pre-commit", "commit-msg", "pre-push"]
ROUTER_OPEN, ROUTER_CLOSE = "<!-- manage-docs:router -->", "<!-- /manage-docs:router -->"
ROUTER_FRAGMENT = "docs/agent/_docs-router.md"
RULESET_NAME = "manage-docs verify"
ACTIONS_APP_ID = 15368  # GitHub Actions integration (S1 probe): pins the required check to Actions
REQUIRED_CHECK = "docs-verify"
GITIGNORE_ALL = [".docs-cache/"]
GITIGNORE_PUBLIC = ["work/", "docs/roadmap/", ".beads/"]
HEARTBEAT_BODY = "last_run: never\nstatus: none\nlast_success: never\n"

# The workflow's python-version per repo: a self-hosted runner pool only reuses a preinstalled series, and
# gustav's CI test requires every workflow to request PYTHON_SERIES from scripts/gustav_ci_runners.sh.
# Seed-only (never written to docs.json); GitHub-hosted repos take the default.
DEFAULT_PYTHON = "3.12"
PYTHON_PLACEHOLDER = "__PYTHON_VERSION__"

SEEDS = {
    "gustav": {"base_branch": "working", "visibility": "private", "tier": "full", "python_version": "3.13",
               "root_allow+": ["GEMINI.md", "IDENTITY.md", "SOUL.md", "TOOLS.md"],
               "md_allow+": ["context/**", "constitution/**", "brands/**", "scripts/**/README.md", "config/**"],
               "caps": {"docs": 60}},
    "uplink-v3": {"base_branch": "working", "visibility": "private", "tier": "full", "python_version": "3.12",
                  "root_allow+": ["DESIGN.md", "quickstart.md"],
                  "md_allow+": ["apps/**/README.md", "packages/**/README.md", "docs/agent/**", "supabase/**/README.md"],
                  "router": {"mode": "generator"}, "caps": {"docs": 60},
                  "inventory_exclude": ["reference/**"],
                  "generators": [{"id": "agent-docs", "cmd": ["node", "scripts/gen-agent-docs.mjs"],
                                  "check": ["node", "scripts/gen-agent-docs.mjs", "--check"]}]},
    "magic": {"visibility": "private", "tier": "small", "md_allow+": ["services/**/README.md"]},
    "opascope-skills": {"base_branch": "main", "visibility": "public", "tier": "small",
                        "root_allow+": ["ARCHITECTURE.md", "shared.md", "VERSION"],
                        # skills live in top-level folders since opascope/skills #16 (was skills/)
                        "md_allow+": ["*/SKILL.md", "*/shared.md", "*/references/**"]},
}


class InitError(Exception):
    pass


def sh(cmd: List[str], cwd: Optional[Path] = None, check: bool = True, input: Optional[str] = None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, input=input)
    if check and p.returncode != 0:
        raise InitError(f"{' '.join(cmd[:4])}: {(p.stderr or p.stdout).strip()[:400]}")
    return p


def gh_bin() -> str:
    return os.environ.get("MANAGE_DOCS_GH", "gh")


# ------------------------------------------------------------------ vendoring + PIN

def vendor_sources(skill_root: Path) -> Dict[str, Path]:
    """{path under tools/docs/: source file}. roadmap.py and its templates come from beside docs.py
    when this is a vendored copy, else from the sibling manage-roadmap skill (its canonical home);
    they vendor as `roadmap.py` and `templates/roadmap/*.md`."""
    skill_root = Path(skill_root)
    out: Dict[str, Path] = {}
    for f in VENDOR_FILES:
        if (skill_root / f).is_file():
            out[f] = skill_root / f
    for pat in VENDOR_GLOBS:
        for fp in sorted(skill_root.glob(pat)):
            if fp.is_file() and "__pycache__" not in fp.parts:
                out[fp.relative_to(skill_root).as_posix()] = fp
    rm = skill_root.parent / "manage-roadmap"
    if "roadmap.py" not in out and (rm / "roadmap.py").is_file():
        out["roadmap.py"] = rm / "roadmap.py"
        for fp in sorted((rm / "templates").glob("*.md")):
            out[f"templates/roadmap/{fp.name}"] = fp
    return dict(sorted(out.items()))


def vendored_set(skill_root: Path) -> List[str]:
    return list(vendor_sources(skill_root))


def sha256_file(fp: Path) -> str:
    return hashlib.sha256(Path(fp).read_bytes()).hexdigest()


def skill_sha(skill_root: Path) -> str:
    return sh(["git", "rev-parse", "HEAD"], cwd=skill_root).stdout.strip()


def vendor(skill_root: Path, repo: Path, sha: Optional[str] = None) -> dict:
    skill_root, repo = Path(skill_root), Path(repo)
    dst = repo / "tools" / "docs"
    if dst.exists():
        for fp in sorted(dst.rglob("*"), reverse=True):
            if fp.is_file() or fp.is_symlink():
                fp.unlink()
    files = {}
    for rel, src in vendor_sources(skill_root).items():
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        files[rel] = sha256_file(target)
    pin = {"skill_sha": sha or skill_sha(skill_root), "files": files}
    (dst / "PIN").write_text(json.dumps(pin, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return pin


def pin_proof(skill_root: Path, repo: Path) -> Tuple[bool, str]:
    """LOCAL trust-root proof for the PR body (contract section 2): ancestor + per-file hashes."""
    pin = json.loads((Path(repo) / "tools" / "docs" / "PIN").read_text(encoding="utf-8"))
    lines = [f"skill_sha {pin['skill_sha']}"]
    ok = True
    # MANAGE_DOCS_TRUST_REF is a test seam only (fixture suites run on unmerged commits)
    ref = os.environ.get("MANAGE_DOCS_TRUST_REF", "origin/working")
    anc = sh(["git", "merge-base", "--is-ancestor", pin["skill_sha"], ref], cwd=skill_root, check=False)
    lines.append(f"ancestor of gustav {ref}: {'yes' if anc.returncode == 0 else 'NO'}")
    ok &= anc.returncode == 0
    sources = vendor_sources(skill_root)
    for rel, want in sorted(pin["files"].items()):
        src = sources.get(rel, Path(skill_root) / rel)
        have = sha256_file(src) if src.is_file() else "missing"
        vend = Path(repo) / "tools" / "docs" / rel
        vhave = sha256_file(vend) if vend.is_file() else "missing"
        good = have == want == vhave
        ok &= good
        lines.append(f"{'ok ' if good else 'BAD'} {want[:16]} tools/docs/{rel}")
    return ok, "\n".join(lines)


# ------------------------------------------------------------------ repo identity + seed config

def nwo(repo: Path) -> str:
    url = sh(["git", "remote", "get-url", "origin"], cwd=repo, check=False).stdout.strip()
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
    return m.group(1) if m else ""


# Repos whose merger updates each PR to its base right before merging (gh pr update-branch, wait for
# green, merge with --match-head-commit) instead of a merge queue or a strict up-to-date rule. uplink-v3
# by RE11; magic and gustav by Max's ruling of 2026-10-02 ("waive"): strict would serialize every
# gustav agent lane, and magic has no other required check before PR 1 adds docs-verify.
UPDATE_BEFORE_MERGE = {"uplink-v3", "magic", "gustav"}

# GitHub owner/name -> SEEDS key, where the repo name differs from the slug the loop uses. A miss here
# once seeded the PUBLIC skills package as private/full, which turns its public name-scan hooks off.
SEED_ALIASES = {"opascope/skills": "opascope-skills"}


def repo_slug(repo: Path) -> str:
    n = nwo(repo)
    if n in SEED_ALIASES:
        return SEED_ALIASES[n]
    return n.split("/")[-1] if n else Path(repo).name


def render_workflow(skill_root: Path, repo: Path) -> str:
    """The docs-verify workflow for this repo: the template with its python-version filled from the seed."""
    text = (Path(skill_root) / WORKFLOW_SRC).read_text(encoding="utf-8")
    ver = SEEDS.get(repo_slug(repo), {}).get("python_version", DEFAULT_PYTHON)
    out = text.replace(PYTHON_PLACEHOLDER, ver)
    if PYTHON_PLACEHOLDER in out or ver not in out:
        raise InitError(f"{WORKFLOW_SRC}: python-version placeholder not rendered")
    return out


def seed_cfg(repo: Path, tier: Optional[str] = None) -> dict:
    slug = repo_slug(repo)
    seed = SEEDS.get(slug, {})
    raw: dict = {"visibility": seed.get("visibility", "private"), "layout_version": 1,
                 "tier": tier or seed.get("tier", "full")}
    base = seed.get("base_branch")
    if base is None:
        p = sh([gh_bin(), "repo", "view", "--json", "defaultBranchRef"], cwd=repo, check=False)
        try:
            base = json.loads(p.stdout)["defaultBranchRef"]["name"]
        except (ValueError, KeyError, TypeError):
            base = "main"
    raw["base_branch"] = base
    d = config.DEFAULTS
    if "root_allow+" in seed:
        raw["root_allow"] = list(d["root_allow"]) + seed["root_allow+"]
    if "md_allow+" in seed:
        raw["md_allow"] = list(d["md_allow"]) + seed["md_allow+"]
    for k in ("router", "generators", "inventory_exclude"):
        if k in seed:
            raw[k] = seed[k]
    if "caps" in seed:
        raw["caps"] = dict(d["caps"], **seed["caps"])
    return raw


# ------------------------------------------------------------------ preflight

def preflight(repo: Path, cfg: dict, runtime: str, reviewer: Optional[List[str]] = None) -> Tuple[bool, List[str]]:
    """(go, lines). Every no-go line carries the exact command for Max."""
    repo = Path(repo)
    lines: List[str] = []
    go = True
    name = nwo(repo)
    slug = name.split("/")[-1] if name else repo.name
    base = cfg.get("base_branch", "main")

    def bad(msg: str) -> None:
        nonlocal go
        go = False
        lines.append("NO-GO " + msg)

    who = sh([gh_bin(), "api", "user", "--jq", ".login"], cwd=repo, check=False)
    if who.returncode != 0 or not who.stdout.strip():
        bad("gh is not authenticated. Run: gh auth login")
    else:
        lines.append(f"ok    gh identity: {who.stdout.strip()}")
    info = sh([gh_bin(), "api", f"repos/{name}"], cwd=repo, check=False)
    try:
        meta = json.loads(info.stdout) if info.returncode == 0 else {}
    except ValueError:
        meta = {}
    if not (meta.get("permissions") or {}).get("admin"):
        bad(f"this gh identity cannot create rulesets on {name} (needs admin). Ask an admin, or run as one: "
            f"gh api repos/{name} --jq .permissions.admin")
    else:
        lines.append("ok    ruleset admin")
    if not meta.get("allow_auto_merge"):
        bad(f"auto-merge is off. Run: gh api -X PATCH repos/{name} -F allow_auto_merge=true")
    else:
        lines.append("ok    auto-merge allowed")
    if slug in UPDATE_BEFORE_MERGE:
        lines.append("ok    merge gate: per-PR update before merge (update-branch, then --match-head-commit)")
    else:
        rules = sh([gh_bin(), "api", f"repos/{name}/rules/branches/{base}"], cwd=repo, check=False)
        try:
            rl = json.loads(rules.stdout) if rules.returncode == 0 else []
        except ValueError:
            rl = []
        gate = any(r.get("type") == "merge_queue" for r in rl) or any(
            r.get("type") == "required_status_checks"
            and (r.get("parameters") or {}).get("strict_required_status_checks_policy") for r in rl)
        if not gate:
            prot = sh([gh_bin(), "api", f"repos/{name}/branches/{base}/protection",
                       "--jq", ".required_status_checks.strict"], cwd=repo, check=False)
            gate = prot.returncode == 0 and prot.stdout.strip() == "true"
        if gate:
            lines.append("ok    merge queue or strict up-to-date")
        else:
            bad(f"no merge queue and no strict up-to-date rule on {base}. Turn on 'Require branches to be up "
                f"to date' for {base} in https://github.com/{name}/settings/rules")
    lab = sh([gh_bin(), "label", "list", "--search", routine.LABEL, "--json", "name"], cwd=repo, check=False)
    if routine.LABEL in lab.stdout or (meta.get("permissions") or {}).get("push"):
        lines.append(f"ok    label {routine.LABEL} exists or is creatable")
    else:
        bad(f"cannot create label. Run: gh label create {routine.LABEL} -R {name}")
    cmd = reviewer or receipts.reviewer_cmd(cfg, runtime)
    try:
        p = subprocess.run(cmd + ["Reply with exactly the word OK and nothing else."], capture_output=True,
                           text=True, timeout=300)
        if p.returncode == 0 and "OK" in p.stdout:
            lines.append(f"ok    reviewer `{' '.join(cmd)}` works")
        else:
            bad(f"reviewer `{' '.join(cmd)}` did not answer (exit {p.returncode}). Log in to it on this machine "
                "and retry: " + " ".join(cmd) + " 'Reply OK'")
    except (OSError, subprocess.TimeoutExpired) as exc:
        bad(f"reviewer `{' '.join(cmd)}` cannot run: {exc}")
    q = routine.quota(runtime)
    if q["band"] in ("checkpoint", "pause"):
        bad(f"quota {q['used']}% for {runtime}; wait until resets_at={q['resets_at']}")
    else:
        lines.append(f"ok    quota {q['band']}" + (f" ({q['reason']})" if q["reason"] else ""))
    bdw = sh([os.environ.get("MANAGE_DOCS_BD", "bd"), "where", "--json"], cwd=repo, check=False)
    lines.append("ok    beads: " + (bdw.stdout.strip()[:200] if bdw.returncode == 0 else "none"))
    if config.is_public(cfg):
        from docslib import namescan
        if not namescan.load_list(namescan.default_list_path(cfg)):
            bad("deny-term list missing for a public repo. Run: python3 tools/docs/docs.py scan-deny --rebuild")
        else:
            lines.append("ok    deny-term list present")
    return go, lines


# ------------------------------------------------------------------ init stages

def append_gitignore(repo: Path, entries: List[str]) -> None:
    gi = Path(repo) / ".gitignore"
    have = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    add = [e for e in entries if e not in have]
    if add:
        text = "\n".join(have).rstrip("\n")
        block = "# manage-docs\n" + "\n".join(add)
        gi.write_text((text + "\n\n" if text else "") + block + "\n", encoding="utf-8")


def stage_pr1(skill_root: Path, repo: Path, tier: Optional[str], decision: Optional[str]) -> List[str]:
    repo = Path(repo)
    done = []
    pin = vendor(skill_root, repo)
    done.append(f"tools/docs/ vendored ({len(pin['files'])} files, PIN {pin['skill_sha'][:12]})")
    wf = repo / WORKFLOW_DST
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_text(render_workflow(skill_root, repo), encoding="utf-8")
    done.append(WORKFLOW_DST)
    dj = repo / "docs.json"
    if not dj.exists():
        dj.write_text(json.dumps(seed_cfg(repo, tier), indent=2) + "\n", encoding="utf-8")
        done.append("docs.json (seeded)")
    cfg = config.load(repo)
    append_gitignore(repo, GITIGNORE_ALL + (GITIGNORE_PUBLIC if config.is_public(cfg) else []))
    done.append(".gitignore")
    if decision:
        if config.is_public(cfg):
            raise InitError("public repos never append to DECISIONS.md (it is gitignored)")
        dec = repo / config.DECISIONS
        dec.parent.mkdir(parents=True, exist_ok=True)
        old = dec.read_text(encoding="utf-8") if dec.exists() else "# Decisions\n"
        dec.write_text(old.rstrip("\n") + "\n\n" + Path(decision).read_text(encoding="utf-8").strip() + "\n",
                       encoding="utf-8")
        done.append(config.DECISIONS + " (appended)")
    return done


def router_text(cfg: dict, repo: Optional[Path] = None) -> str:
    lines = ["## Docs and records", "",
             "- Docs map: [docs/README.md](docs/README.md), one line per doc with its receipt status."]
    if not config.is_public(cfg):
        if repo is None or (Path(repo) / "docs" / "roadmap").is_dir():
            lines.append("- Roadmap and decisions: [docs/roadmap/](docs/roadmap/).")
        lines.append("- Records (handoffs, runs, evidence, audits): [work/](work/README.md).")
    lines += ["- Where does a new file go? `python3 tools/docs/docs.py where \"<what it is>\"`.",
              "- Before a PR: `python3 tools/docs/docs.py check`."]
    return "\n".join(lines)


def is_tracked(repo: Path, rel: str) -> bool:
    return sh(["git", "ls-files", "--error-unmatch", "--", rel], cwd=repo, check=False).returncode == 0


def install_router(repo: Path, cfg: dict, defer: Optional[list] = None) -> Optional[str]:
    """`defer` (stage migration): an existing tracked target is listed there, not edited."""
    repo = Path(repo)
    text = router_text(cfg, repo)
    if (cfg.get("router") or {}).get("mode") == "generator":
        frag = repo / ROUTER_FRAGMENT
        frag.parent.mkdir(parents=True, exist_ok=True)
        frag.write_text(text + "\n", encoding="utf-8")
        return (f"{ROUTER_FRAGMENT} written; in the wire PR after the migration merges, wire it after `shared` "
                "in both compose calls of scripts/gen-agent-docs.mjs (CLAUDE.md and AGENTS.md), then run the "
                "generator and --check")
    target = None
    for name in ("AGENTS.md", "CLAUDE.md"):
        fp = repo / name
        if fp.exists() and not fp.is_symlink():
            target = fp
            break
    if target is not None and defer is not None and is_tracked(repo, target.name):
        defer.append(f"router block in {target.name}")
        return None
    if target is None:
        target = repo / "AGENTS.md"
        target.write_text("# Agents\n", encoding="utf-8")
    cur = target.read_text(encoding="utf-8")
    block = f"{ROUTER_OPEN}\n{text}\n{ROUTER_CLOSE}"
    rx = re.compile(re.escape(ROUTER_OPEN) + r".*?" + re.escape(ROUTER_CLOSE), re.S)
    new = rx.sub(lambda _m: block, cur) if rx.search(cur) else cur.rstrip("\n") + "\n\n" + block + "\n"
    target.write_text(new, encoding="utf-8")
    return f"router block in {target.name}"


def install_githooks(skill_root: Path, repo: Path, cfg: dict, defer: Optional[list] = None) -> List[str]:
    repo = Path(repo)
    names = ["pre-commit"] + (["commit-msg", "pre-push"] if config.is_public(cfg) else [])
    out = []
    husky = repo / ".husky"
    for name in names:
        line = f'python3 tools/docs/docs.py hook {name} "$@" || exit $?'
        if name == "pre-commit" and husky.is_dir():
            fp = husky / "pre-commit"
        else:
            fp = repo / ".githooks" / name
        rel = fp.relative_to(repo).as_posix()
        if fp.exists() and defer is not None and is_tracked(repo, rel):
            if "tools/docs/docs.py hook" not in fp.read_text(encoding="utf-8"):
                defer.append(f"docs hook line in {rel}")
            continue
        if fp.exists():
            cur = fp.read_text(encoding="utf-8")
            if "tools/docs/docs.py hook" not in cur:
                fp.write_text(cur.rstrip("\n") + "\n" + line + "\n", encoding="utf-8")
        else:
            fp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(skill_root) / "templates" / "githooks" / name, fp)
        fp.chmod(0o755)
        out.append(fp.relative_to(repo).as_posix())
    return out


def install_placement_hook(repo: Path, defer: Optional[list] = None) -> Optional[str]:
    fp = Path(repo) / ".claude" / "settings.json"
    if fp.exists() and defer is not None and is_tracked(repo, ".claude/settings.json"):
        defer.append(".claude/settings.json placement warn hook")
        return None
    data = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {}
    cmd = "python3 tools/docs/hooks/where_hook.py"
    pre = data.setdefault("hooks", {}).setdefault("PreToolUse", [])
    if not any(cmd == h.get("command") for e in pre for h in e.get("hooks", [])):
        pre.append({"matcher": "Write|Edit", "hooks": [{"type": "command", "command": cmd}]})
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return ".claude/settings.json (placement warn hook)"


def stage_migration(skill_root: Path, repo: Path, plugins=()) -> List[str]:
    """RE10: every plugin's own init for this stage runs first (the roadmap plugin creates
    docs/roadmap/), then the router, hooks and the built-in generated maps. A repo's own external
    generators (uplink's agent-docs) run once the execution session has wired the fragment."""
    repo = Path(repo)
    cfg = config.load(repo)
    if cfg is None:
        raise InitError("no docs.json; run `docs.py init --stage pr1` first")
    done = []
    for plug in plugins:
        fn = getattr(plug, "init", None)
        if fn is not None:
            done += list(fn(repo, "migration") or [])
    # what a plugin laid down joins the index (intent-to-add) so the generated maps list it;
    # ignored paths (a public repo's local-only roadmap) stay out
    new = sh(["git", "ls-files", "-z", "--others", "--exclude-standard", "--", "docs/roadmap", ".beads/issues.jsonl"],
             cwd=repo, check=False).stdout.split("\0")
    new = [p for p in new if p]
    if new:
        sh(["git", "add", "-N", "--", *new], cwd=repo)
    deferred: list = []
    done += [d for d in [install_router(repo, cfg, deferred)] if d]
    done += install_githooks(skill_root, repo, cfg, deferred)
    done += [d for d in [install_placement_hook(repo, deferred)] if d]
    generate.run(repo, cfg, builtin_only=True)
    done.append("docs/README.md" + ("" if config.is_public(cfg) else " and work/README.md") + " generated")
    if deferred:
        done.append("deferred to `docs.py init --stage wire` in a small PR after the migration PR merges "
                    "(the migration PR must leave tracked files byte-identical): " + "; ".join(deferred))
    return done


def stage_wire(skill_root: Path, repo: Path) -> List[str]:
    """After the migration PR merges: the router, hook and placement edits stage migration deferred
    because they touch files the repo already tracked. Idempotent."""
    repo = Path(repo)
    cfg = config.load(repo)
    if cfg is None:
        raise InitError("no docs.json; run `docs.py init --stage pr1` first")
    done = [d for d in [install_router(repo, cfg)] if d]
    done += install_githooks(skill_root, repo, cfg)
    done.append(install_placement_hook(repo))
    return done


def ruleset_payload(base: str) -> dict:
    return {"name": RULESET_NAME, "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"include": [f"refs/heads/{base}"], "exclude": []}},
            "rules": [{"type": "required_status_checks",
                       "parameters": {"strict_required_status_checks_policy": False,
                                      "required_status_checks": [{"context": REQUIRED_CHECK,
                                                                  "integration_id": ACTIONS_APP_ID}]}}]}


def stage_local(repo: Path, clone: bool = False, gustav_url: Optional[str] = None) -> Tuple[bool, List[str]]:
    repo = Path(repo)
    cfg = config.load(repo)
    if cfg is None:
        raise InitError("no docs.json; run `docs.py init --stage pr1` first")
    name, base, ok, done = nwo(repo), cfg.get("base_branch", "main"), True, []
    if (repo / ".githooks").is_dir() and not (repo / ".husky").is_dir():
        sh(["git", "config", "core.hooksPath", ".githooks"], cwd=repo)
        done.append("core.hooksPath=.githooks")
    rs = sh([gh_bin(), "api", f"repos/{name}/rulesets"], cwd=repo, check=False)
    try:
        have = [r.get("name") for r in json.loads(rs.stdout)] if rs.returncode == 0 else []
    except ValueError:
        have = []
    if RULESET_NAME in have:
        done.append("ruleset present")
    else:
        payload = json.dumps(ruleset_payload(base))
        p = sh([gh_bin(), "api", "-X", "POST", f"repos/{name}/rulesets", "--input", "-"], cwd=repo, check=False,
               input=payload)
        if p.returncode == 0:
            done.append(f"ruleset `{RULESET_NAME}` added (docs-verify required on {base})")
        else:
            ok = False
            done.append(f"FAILED ruleset; run as an admin: echo '{payload}' | gh api -X POST repos/{name}/rulesets "
                        "--input -   (auto-merge stays off for this repo until the check is required)")
    lab = sh([gh_bin(), "label", "create", routine.LABEL, "--color", "D4C5F9", "--description",
              "manage-docs daily PR awaiting Max's approval"], cwd=repo, check=False)
    done.append(f"label {routine.LABEL} " + ("created" if lab.returncode == 0 else "exists or not creatable"))
    il = sh([gh_bin(), "issue", "list", "--state", "open", "--search", f"{routine.HEARTBEAT_TITLE} in:title",
             "--json", "number,title"], cwd=repo, check=False)
    try:
        found = any(i.get("title") == routine.HEARTBEAT_TITLE for i in json.loads(il.stdout or "[]"))
    except ValueError:
        found = False
    if not found:
        p = sh([gh_bin(), "issue", "create", "--title", routine.HEARTBEAT_TITLE, "--body", HEARTBEAT_BODY],
               cwd=repo, check=False)
        ok &= p.returncode == 0
        done.append("heartbeat issue " + ("created" if p.returncode == 0 else "FAILED: " + p.stderr.strip()[:200]))
    else:
        done.append("heartbeat issue present")
    reposj = routine.config_home() / "repos.json"
    reposj.parent.mkdir(parents=True, exist_ok=True)
    entries = json.loads(reposj.read_text(encoding="utf-8")) if reposj.exists() else []
    slug = name.split("/")[-1] if name else repo.name
    if not any(e.get("slug") == slug for e in entries):
        entries.append({"slug": slug, "path": str(repo.resolve()), "base_branch": base})
        reposj.write_text(json.dumps(entries, indent=1) + "\n", encoding="utf-8")
        done.append(f"repos.json += {slug} (enroll it in the routine only after magic completes one cycle)")
    if clone:
        dest = routine.data_home() / "gustav"
        if not dest.exists():
            sh(["git", "clone", "-q", gustav_url or "https://github.com/opascope/gustav.git", str(dest)])
            done.append(f"dedicated gustav clone at {dest}")
        else:
            done.append("dedicated gustav clone present")
    return ok, done


# ------------------------------------------------------------------ upgrade (RE10)

def upgrade(skill_root: Path, repo: Path, plugins=(), push: bool = True) -> Tuple[int, str]:
    """Open the vendored tools/docs upgrade PR (branch docs/upgrade-<sha>, auto-merge on green) and run
    every plugin's layout upgrade to the vendored layout_version in the same PR."""
    repo = Path(repo)
    cfg = config.load(repo) or {}
    base = cfg.get("base_branch", "main")
    sha = skill_sha(skill_root)
    branch = f"docs/upgrade-{sha[:12]}"
    sh(["git", "fetch", "-q", "origin", base], cwd=repo)
    sh(["git", "checkout", "-q", "-B", branch, f"origin/{base}"], cwd=repo)
    vendor(skill_root, repo, sha)
    for plug in plugins:
        fn = getattr(plug, "upgrade_layout", None)
        if fn is not None:
            fn(repo, int(cfg.get("layout_version", 1)))
    ok, proof = pin_proof(skill_root, repo)
    if not ok:
        return 1, "upgrade refused: the local trust-root proof failed\n" + proof
    sh(["git", "add", "-A", "tools/docs"], cwd=repo)
    if sh(["git", "diff", "--cached", "--quiet"], cwd=repo, check=False).returncode == 0:
        return 0, "upgrade: tools/docs already at " + sha[:12]
    sh(["git", "commit", "-q", "-m", f"chore(docs): upgrade tools/docs to {sha[:12]}"], cwd=repo)
    if not push:
        return 0, proof
    sh(["git", "push", "-q", "origin", f"HEAD:refs/heads/{branch}"], cwd=repo)
    body = ("Upgrade the vendored manage-docs tools.\n\nLocal trust-root proof (contract section 2):\n\n```\n"
            + proof + "\n```\n")
    out = sh([gh_bin(), "pr", "create", "--base", base, "--head", branch, "--title",
              f"chore(docs): upgrade tools/docs to {sha[:12]}", "--body", body], cwd=repo).stdout.strip()
    sh([gh_bin(), "pr", "merge", out, "--auto", "--squash"], cwd=repo, check=False)
    return 0, f"upgrade PR {out}"
