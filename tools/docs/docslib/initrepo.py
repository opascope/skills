"""init, preflight, vendoring and upgrade (manage-docs v2 sections 4, 5, 12; contract sections 2, 9).

- `vendor(skill_root, repo)` copies the vendored set into `tools/docs/` and writes `tools/docs/PIN`
  = {"skill_sha", "files": {relpath: sha256}}; CI checks the files against it (contract section 2).
- `pin_proof(skill_root, pin)` is the LOCAL trust-root proof pasted into the PR body: the skill sha is
  an ancestor of the home repo's base branch and every vendored file matches its recorded hash.
- `preflight(repo)` is contract section 2 step 0 / section 9; it prints the exact commands for the owner.
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

from docslib import checks, config, generate, receipts, routine, site

VENDOR_FILES = ["docs.py", "roadmap.py", "engine/migrate.py", "engine/INTERFACE.md", "hooks/where_hook.py"]
VENDOR_GLOBS = ["docslib/*.py", "templates/**/*"]
WORKFLOW_SRC = "templates/workflows/docs-verify.yml"
WORKFLOW_DST = ".github/workflows/docs-verify.yml"
GITHOOKS = ["pre-commit", "commit-msg", "pre-push"]
ROUTER_OPEN, ROUTER_CLOSE = "<!-- manage-docs:router -->", "<!-- /manage-docs:router -->"
ROUTER_FRAGMENT = config.ROUTER_FRAGMENT
RULESET_NAME = "manage-docs verify"
ACTIONS_APP_ID = 15368  # GitHub Actions integration (S1 probe): pins the required check to Actions
REQUIRED_CHECK = "docs-verify"
GITIGNORE_ALL = [".docs-cache/"]
GITIGNORE_PUBLIC = ["work/", "docs/roadmap/", ".beads/"]
HEARTBEAT_BODY = "last_run: never\nstatus: none\nlast_success: never\n"

# The workflow's python-version per repo: a self-hosted runner pool only reuses a preinstalled series.
# Seed-only (never written to docs.json); a repo with no seed value takes the default.
DEFAULT_PYTHON = "3.12"
PYTHON_PLACEHOLDER = "__PYTHON_VERSION__"
# The jobs' runs-on per repo, written verbatim after `runs-on: ` (a label, a flow list, or a
# ${{ }} expression). Seed-only like python_version. A repo with no seed runs on the org's
# self-hosted pool, never a GitHub-hosted runner; a repo allowed on hosted runners says so in
# its own seed.
DEFAULT_RUNS_ON = "[self-hosted, ci-group]"
RUNS_ON_PLACEHOLDER = "__RUNS_ON__"
VENDOR_PLACEHOLDER = "__VENDOR_DIR__"
# Folders whose Markdown does not make docs-verify run its full check (site.json seed
# `docs_verify_skip`, each one "<folder>/**"). Seed-only like runs_on. Each must already be in the
# repo's docs.json inventory_exclude, so no check reads a file there; a repo with no seed value skips
# nothing beyond non-docs files.
SKIP_PLACEHOLDER = "__DOCS_VERIFY_SKIP__"
SKIP_GLOB = re.compile(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*/\*\*")

SEEDS = site.get("seeds")  # repo slug -> init seed; the home repo's site.json (never vendored)


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
    """The sha a PIN records: the last commit that changed the vendored skill (routine.skill_tree_sha),
    the same value the daily run compares against, so an unrelated commit never reads as behind."""
    return routine.skill_tree_sha(skill_root) or sh(["git", "rev-parse", "HEAD"], cwd=skill_root).stdout.strip()


def repo_vendor_dir(repo: Path) -> str:
    """Where this repo vendors the engine: docs.json's vendor_dir once it exists, before that the
    repo's site.json seed (`vendor_dir`), else tools/docs."""
    repo = Path(repo)
    if (repo / "docs.json").exists():
        return config.vendor_dir_of(repo)
    return config.vendor_dir({"vendor_dir": SEEDS.get(repo_slug(repo), {}).get("vendor_dir")})


def unvendored_files(dst: Path) -> Optional[List[str]]:
    """Files under dst that its PIN does not list (bytecode caches aside), or None when dst does not hold an
    engine this skill vendored (checks.vendored_engine: a valid PIN whose every file is present and matches
    its hash, no symlinks). A PIN-shaped file with wrong hashes is never proof enough to clear a folder."""
    pin = checks.vendored_engine(dst)
    if pin is None:
        return None
    listed = set(pin["files"]) | {"PIN"}
    return sorted(rel for rel in (fp.relative_to(dst).as_posix() for fp in dst.rglob("*")
                                  if fp.is_file() or fp.is_symlink())
                  if rel not in listed and "__pycache__/" not in rel and not rel.endswith(".pyc"))


def vendor(skill_root: Path, repo: Path, sha: Optional[str] = None) -> dict:
    skill_root, repo = Path(skill_root), Path(repo)
    vd = repo_vendor_dir(repo)
    dst = repo / vd
    # The folder must stay inside the checkout: no part of the path may be a symlink.
    for depth in range(1, len(vd.split("/")) + 1):
        part = repo.joinpath(*vd.split("/")[:depth])
        if part.is_symlink():
            raise InitError(f"{part.relative_to(repo)} is a symlink; vendor_dir must be a real folder in the repo")
    if dst.exists() and any(dst.iterdir()):
        stray = unvendored_files(dst)
        if stray is None or stray:
            # Only a folder this skill vendored before is ever cleared: a valid PIN lists every file.
            what = "no valid PIN" if stray is None else "files its PIN does not list: " + ", ".join(stray[:5])
            raise InitError(f"{dst.relative_to(repo)} has {what}; it is not a vendored engine, so it is "
                            "never cleared. Pick an empty vendor_dir.")
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
    vd = repo_vendor_dir(repo)
    pin = json.loads((Path(repo) / vd / "PIN").read_text(encoding="utf-8"))
    lines = [f"skill_sha {pin['skill_sha']}"]
    ok = True
    # MANAGE_DOCS_TRUST_REF is a test seam only (fixture suites run on unmerged commits)
    ref = os.environ.get("MANAGE_DOCS_TRUST_REF", "origin/" + site.get("home_base_branch"))
    anc = sh(["git", "merge-base", "--is-ancestor", pin["skill_sha"], ref], cwd=skill_root, check=False)
    lines.append(f"ancestor of {site.get('home_clone_dir')} {ref}: {'yes' if anc.returncode == 0 else 'NO'}")
    ok &= anc.returncode == 0
    sources = vendor_sources(skill_root)
    for rel, want in sorted(pin["files"].items()):
        src = sources.get(rel, Path(skill_root) / rel)
        have = sha256_file(src) if src.is_file() else "missing"
        vend = Path(repo) / vd / rel
        vhave = sha256_file(vend) if vend.is_file() else "missing"
        good = have == want == vhave
        ok &= good
        lines.append(f"{'ok ' if good else 'BAD'} {want[:16]} {vd}/{rel}")
    return ok, "\n".join(lines)


# ------------------------------------------------------------------ repo identity + seed config

def nwo(repo: Path) -> str:
    try:
        url = sh(["git", "remote", "get-url", "origin"], cwd=repo, check=False).stdout.strip()
    except OSError:  # git is not installed: no remote to read
        return ""
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
    return m.group(1) if m else ""


# Repos whose merger updates each PR to its base right before merging (gh pr update-branch, wait for
# green, merge with --match-head-commit) instead of a merge queue or a strict up-to-date rule; each is
# an explicit ruling recorded in the home repo's site.json.
UPDATE_BEFORE_MERGE = set(site.get("update_before_merge"))

# GitHub owner/name -> SEEDS key, where the repo name differs from the slug the loop uses. A miss here
# once seeded a PUBLIC repo as private/full, which turns its public name-scan hooks off.
SEED_ALIASES = dict(site.get("seed_aliases"))


def slug_paths(slug: str) -> Dict[str, Path]:
    """Every machine-local path the routine keys by a repo's slug (routine.py: state_dir, private_dir,
    worktree_for, and the active-migration marker skip_reason reads), plus the engine's publish, close and
    rollback journals (migrate.journal; Codex P2 on #1124). The worktree comes first, so a refused
    `git worktree move` leaves every other path where it was."""
    return {"worktree": routine.data_home() / "worktrees" / slug,
            "state": routine.data_home() / "state" / slug,
            "private": routine.data_home() / "private" / slug,
            "active marker": routine.config_home() / "active" / slug,
            "journals": routine.config_home() / slug}


def move_slug_paths(olds: List[str], new: str, checkout: str, done: List[str]) -> None:
    """Carry a renamed enrollment's local paths to its new slug: moves only, never a merge or a delete.
    Every path is checked first: when a legacy slug's path and the new slug's path both exist, or two legacy
    slugs would land on one path, it refuses before anything moves or repos.json changes (Codex P2s on
    #1118)."""
    dst, moves, clash = slug_paths(new), [], []
    for kind, target in dst.items():
        srcs = [slug_paths(o)[kind] for o in olds if os.path.lexists(slug_paths(o)[kind])]
        if srcs and os.path.lexists(target):
            clash.append(f"{kind}: {', '.join(map(str, srcs))} and {target}")
        elif len(srcs) > 1:
            clash.append(f"{kind}: {', '.join(map(str, srcs))} would all move to {target}")
        elif srcs:
            moves.append((kind, srcs[0], target))
    if clash:
        raise InitError("both the legacy and the new slug's local paths exist; reconcile them by hand (nothing "
                        "was moved or merged, repos.json is unchanged):\n  " + "\n  ".join(clash))
    for kind, src, target in moves:
        target.parent.mkdir(parents=True, exist_ok=True)
        if kind == "worktree":
            p = sh(["git", "worktree", "move", str(src), str(target)], cwd=checkout, check=False)
            if p.returncode != 0:
                raise InitError(f"git worktree move {src} {target} failed ({p.stderr.strip()[:200]}); "
                                "nothing else was moved and repos.json is unchanged")
        else:
            src.rename(target)
        done.append(f"{kind} moved: {src} -> {target}")


def repo_slug(repo: Path) -> str:
    n = nwo(repo)
    if n in SEED_ALIASES:
        return SEED_ALIASES[n]
    return n.split("/")[-1] if n else Path(repo).name


def render_workflow(skill_root: Path, repo: Path) -> str:
    """The docs-verify workflow for this repo: the template with its python-version, runs-on and
    docs_verify_skip filled from the seed, and its vendor folder from docs.json."""
    text = (Path(skill_root) / WORKFLOW_SRC).read_text(encoding="utf-8")
    seed = SEEDS.get(repo_slug(repo), {})
    ver = seed.get("python_version", DEFAULT_PYTHON)
    runs_on = seed.get("runs_on", DEFAULT_RUNS_ON)
    if not isinstance(runs_on, str) or not runs_on.strip() or "\n" in runs_on:
        raise InitError(f"site.json seed runs_on must be one non-empty line: {runs_on!r}")
    out = (text.replace(PYTHON_PLACEHOLDER, ver).replace(RUNS_ON_PLACEHOLDER, runs_on)
           .replace(VENDOR_PLACEHOLDER, repo_vendor_dir(repo))
           .replace(SKIP_PLACEHOLDER, " ".join(docs_verify_skip(repo, seed))))
    if PYTHON_PLACEHOLDER in out or ver not in out:
        raise InitError(f"{WORKFLOW_SRC}: python-version placeholder not rendered")
    if RUNS_ON_PLACEHOLDER in out or f"runs-on: {runs_on}" not in out:
        raise InitError(f"{WORKFLOW_SRC}: runs-on placeholder not rendered")
    if VENDOR_PLACEHOLDER in out:
        raise InitError(f"{WORKFLOW_SRC}: vendor-dir placeholder not rendered")
    if SKIP_PLACEHOLDER in out:
        raise InitError(f"{WORKFLOW_SRC}: docs_verify_skip placeholder not rendered")
    return out


def docs_verify_skip(repo: Path, seed: dict) -> List[str]:
    """The seed's `docs_verify_skip` folders (no trailing slash) for the workflow's scope step. Each
    entry is "<folder>/**" and must already be in the repo's inventory_exclude (docs.json, or the seed's
    before PR 1 writes docs.json): the scope step may only skip what no docs check reads."""
    raw = seed.get("docs_verify_skip", [])
    if not isinstance(raw, list) or not all(isinstance(g, str) for g in raw):
        raise InitError(f"site.json seed docs_verify_skip must be a list of \"<folder>/**\" globs: {raw!r}")
    cfg = config.load(Path(repo)) if (Path(repo) / "docs.json").is_file() else seed
    excluded = set((cfg or {}).get("inventory_exclude") or [])
    out = []
    for glob in raw:
        if not SKIP_GLOB.fullmatch(glob) or any(p in (".", "..") for p in glob.split("/")):
            raise InitError(f"site.json seed docs_verify_skip entries are \"<folder>/**\" globs, not {glob!r}")
        if glob not in excluded:
            raise InitError(f"site.json seed docs_verify_skip {glob!r} is not in the repo's inventory_exclude, "
                            "so the docs checks still read it")
        out.append(glob[:-3])
    return out


def unseeded_base(repo: Path, seed: Optional[dict] = None) -> str:
    """The base branch before docs.json exists: the seed's, else GitHub's default branch, else main."""
    seed = SEEDS.get(repo_slug(repo), {}) if seed is None else seed
    base = seed.get("base_branch")
    if base is None:
        try:
            p = sh([gh_bin(), "repo", "view", "--json", "defaultBranchRef"], cwd=repo, check=False)
            base = json.loads(p.stdout)["defaultBranchRef"]["name"]
        except (OSError, ValueError, KeyError, TypeError):  # OSError: gh is not installed
            base = "main"
    return base


def seed_cfg(repo: Path, tier: Optional[str] = None) -> dict:
    slug = repo_slug(repo)
    seed = SEEDS.get(slug, {})
    raw: dict = {"visibility": seed.get("visibility", "private"), "layout_version": config.LAYOUT_VERSION,
                 "tier": tier or seed.get("tier", "full")}
    raw["base_branch"] = unseeded_base(repo, seed)
    d = config.DEFAULTS
    if "root_allow+" in seed:
        raw["root_allow"] = list(d["root_allow"]) + seed["root_allow+"]
    if "md_allow+" in seed:
        raw["md_allow"] = list(d["md_allow"]) + seed["md_allow+"]
    for k in ("router", "generators", "inventory_exclude", "doc_globs", "doc_exclude", "entrypoints"):
        if k in seed:
            raw[k] = seed[k]
    if "vendor_dir" in seed:
        raw["vendor_dir"] = config.vendor_dir({"vendor_dir": seed["vendor_dir"]})
    if "caps" in seed:
        raw["caps"] = dict(d["caps"], **seed["caps"])
    return raw


# ------------------------------------------------------------------ preflight

def preflight(repo: Path, cfg: dict, runtime: str, reviewer: Optional[List[str]] = None) -> Tuple[bool, List[str]]:
    """(go, lines). Every no-go line carries the exact command for the owner."""
    repo = Path(repo)
    lines: List[str] = []
    go = True
    name = nwo(repo)
    slug = repo_slug(repo)  # the seed slug, so an aliased repo reads its own site.json settings
    # before PR 1 there is no docs.json and cfg holds defaults; the base comes from the seed or GitHub
    base = cfg.get("base_branch", "main") if (repo / "docs.json").is_file() else unseeded_base(repo)

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
        # stdin is /dev/null: `codex exec` reads extra input from an open stdin and, run from a background
        # job whose stdin is a pipe, waited there until the timeout (the overnight "reviewer timed out")
        p = subprocess.run(cmd + ["Reply with exactly the word OK and nothing else."], stdin=subprocess.DEVNULL,
                           capture_output=True,
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
            bad("deny-term list missing for a public repo. Run: "
                f"python3 {config.vendor_dir(cfg)}/docs.py scan-deny --rebuild")
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
    done.append(f"{repo_vendor_dir(repo)}/ vendored ({len(pin['files'])} files, PIN {pin['skill_sha'][:12]})")
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
    docs_map = config.docs_map_path(cfg)
    lines = ["## Docs and records", "",
             f"- Docs map: [{docs_map}]({docs_map}), one line per doc with its receipt status."]
    if not config.is_public(cfg):
        if repo is None or (Path(repo) / "docs" / "roadmap").is_dir():
            lines.append("- Roadmap and decisions: [docs/roadmap/](docs/roadmap/).")
        lines.append("- Records (handoffs, runs, evidence, audits): [work/](work/README.md).")
    vd = config.vendor_dir(cfg)
    lines += [f"- Where does a new file go? `python3 {vd}/docs.py where \"<what it is>\"`.",
              f"- Before a PR: `python3 {vd}/docs.py check`."]
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
    vd = config.vendor_dir(cfg)
    marker = f"{vd}/docs.py hook"
    for name in names:
        line = f'python3 {vd}/docs.py hook {name} "$@" || exit $?'
        if name == "pre-commit" and husky.is_dir():
            fp = husky / "pre-commit"
        else:
            fp = repo / ".githooks" / name
        rel = fp.relative_to(repo).as_posix()
        if fp.exists() and defer is not None and is_tracked(repo, rel):
            if marker not in fp.read_text(encoding="utf-8"):
                defer.append(f"docs hook line in {rel}")
            continue
        if fp.exists():
            cur = fp.read_text(encoding="utf-8")
            if marker not in cur:
                # A hook from an earlier vendor_dir: repoint its managed command, never add a second.
                # Only a command proven to be an earlier vendored engine's counts: a manage-docs PIN beside
                # it vouches for docs.py, or it names the default tools/docs and that script is gone. A
                # repo's own docs.py hook (scripts/docs.py, or tools/docs/docs.py in a repo that owns
                # tools/) is left as it is.
                old = re.compile(rf"(python3 )(\S+)(/docs\.py hook {re.escape(name)})(?=\s|$)")
                new = old.sub(lambda m: m.group(1) + vd + m.group(3)
                              if (checks.pin_vouches_for(Path(repo) / m.group(2), "docs.py")
                                  or (m.group(2) == config.DEFAULT_VENDOR_DIR
                                      and not (Path(repo) / m.group(2) / "docs.py").exists()))
                              else m.group(0), cur)
                fp.write_text(new if new != cur else cur.rstrip("\n") + "\n" + line + "\n", encoding="utf-8")
        else:
            fp.parent.mkdir(parents=True, exist_ok=True)
            template = (Path(skill_root) / "templates" / "githooks" / name).read_text(encoding="utf-8")
            fp.write_text(template.replace("tools/docs/", vd + "/"), encoding="utf-8")
        fp.chmod(0o755)
        out.append(fp.relative_to(repo).as_posix())
    return out


def install_placement_hook(repo: Path, defer: Optional[list] = None) -> Optional[str]:
    vd = repo_vendor_dir(repo)
    fp = Path(repo) / ".claude" / "settings.json"
    if fp.exists() and defer is not None and is_tracked(repo, ".claude/settings.json"):
        defer.append(".claude/settings.json placement warn hook")
        return None
    data = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {}
    cmd = f"python3 {vd}/hooks/where_hook.py"
    pre = data.setdefault("hooks", {}).setdefault("PreToolUse", [])
    stale = re.compile(r"python3 (\S+)/hooks/where_hook\.py")
    for e in pre:  # a placement hook from an earlier vendor_dir is repointed, never doubled
        for h in e.get("hooks", []):
            m = stale.fullmatch(h["command"]) if isinstance(h.get("command"), str) else None
            # only a command proven to be an earlier vendored engine's is repointed: a manage-docs PIN in
            # its folder lists the hook and its hash matches, or its script is gone. A repo's own
            # where_hook (scripts/hooks/where_hook.py, or tools/docs/... in a repo that owns tools/) is
            # left as it is, whatever its path.
            if m and (checks.pin_vouches_for(Path(repo) / m.group(1), "hooks/where_hook.py")
                      or not (Path(repo) / m.group(1) / "hooks" / "where_hook.py").exists()):
                h["command"] = cmd
    if not any(cmd == h.get("command") for e in pre for h in e.get("hooks", [])):
        pre.append({"matcher": "Write|Edit", "hooks": [{"type": "command", "command": cmd}]})
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return ".claude/settings.json (placement warn hook)"


def stage_migration(skill_root: Path, repo: Path, plugins=()) -> List[str]:
    """RE10: every plugin's own init for this stage runs first (the roadmap plugin creates
    docs/roadmap/), then the router, hooks and the built-in generated maps. A repo's own external
    generators (a repo's own doc generator) run once the execution session has wired the fragment."""
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
    try:
        generate.run(repo, cfg, builtin_only=True)
    except generate.GenerateError as exc:
        raise InitError(str(exc))
    done.append(config.docs_map_path(cfg) + ("" if config.is_public(cfg) else " and work/README.md") + " generated")
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


def main_checkout(repo: Path) -> Path:
    """The clone's main working tree, also when init runs inside a linked worktree (the routine needs a
    path that outlives the session's worktree)."""
    p = sh(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=repo, check=False)
    common = Path(p.stdout.strip()) if p.returncode == 0 and p.stdout.strip() else None
    if common is not None and common.name == ".git":
        return common.parent.resolve()
    if common is not None:
        # a submodule (or a linked worktree of one): the shared .git/modules/<name> names its main checkout in
        # core.worktree; the same rule as the engine's main_checkout (Codex P2 PRRT_kwDOSy0wdc6pEMov)
        w = sh(["git", "config", "--file", str(common / "config"), "core.worktree"], cwd=repo, check=False)
        if w.returncode == 0 and w.stdout.strip():
            return (common / w.stdout.strip()).resolve()
    return Path(repo).resolve()


def active_default_hooks(repo: Path) -> List[str]:
    """Hooks the clone runs today from its default hooks folder (<git-common-dir>/hooks), samples excluded."""
    p = sh(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=repo, check=False)
    hooks = Path(p.stdout.strip()) / "hooks" if p.returncode == 0 and p.stdout.strip() else None
    if hooks is None or not hooks.is_dir():
        return []
    return sorted(f.name for f in hooks.iterdir()
                  if f.is_file() and not f.name.endswith(".sample") and os.access(f, os.X_OK))


def label_description(cfg: dict) -> str:
    """The marker on the one daily docs PR. It is review-gated like any agent PR, not
    owner-gated, so the description names no person and makes no approval claim."""
    return "manage-docs daily docs maintenance PR"


def stage_local(repo: Path, clone: bool = False, home_url: Optional[str] = None) -> Tuple[bool, List[str]]:
    repo = Path(repo)
    cfg = config.load(repo)
    if cfg is None:
        raise InitError("no docs.json; run `docs.py init --stage pr1` first")
    name, base, ok, done = nwo(repo), cfg.get("base_branch", "main"), True, []
    if (repo / ".githooks").is_dir() and not (repo / ".husky").is_dir():
        # core.hooksPath is SHARED by every worktree of the clone, so an existing value is the owner's choice
        # (an absolute path pins every worktree to one checkout's hooks); only an unset one is filled in
        cur = sh(["git", "config", "--get", "core.hooksPath"], cwd=repo, check=False).stdout.strip()
        live = active_default_hooks(repo) if not cur else []
        if live:
            # setting hooksPath would silently switch these off in every worktree of the clone
            done.append(f"core.hooksPath left unset: .git/hooks has active hooks ({', '.join(live)}); the docs "
                        "hooks are not active (the owner moves those hooks into .githooks/, or sets hooksPath)")
        elif not cur:
            sh(["git", "config", "core.hooksPath", ".githooks"], cwd=repo)
            done.append("core.hooksPath=.githooks")
        elif cur.rstrip("/").endswith(".githooks"):
            done.append(f"core.hooksPath already {cur} (left as is)")
        else:
            done.append(f"core.hooksPath is {cur}, not .githooks: left as is; the docs hooks are not active "
                        f"(the owner sets it, or merges them into {cur})")
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
              label_description(cfg)], cwd=repo, check=False)
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
    slug = repo_slug(repo)  # the seed slug (opascope/skills -> opascope-skills), the key site.json uses
    path = str(main_checkout(repo))
    # an entry enrolled before the alias fix carries the GitHub name ("skills"): match it by its checkout
    # or by that legacy name and rename it in place, never append a second entry for the same repo
    legacy = {n.split("/")[-1] for n, s in SEED_ALIASES.items() if s == slug}
    same = [e for e in entries if e.get("slug") == slug
            or str(Path(e.get("path", "")).expanduser().resolve()) == path or e.get("slug") in legacy]
    # the legacy slugs' local paths are always checked, even when repos.json already holds the new slug: a
    # partial or hand migration can leave state under the old name, which the routine would then never read
    # (Codex P2 on #1118). Each path moves, or the whole step refuses before repos.json changes.
    move_slug_paths(sorted((legacy | {x.get("slug") for x in same}) - {slug, None}), slug, path, done)
    if same:
        e = same[0]
        if e.get("slug") != slug or e.get("path") != path or len(same) > 1:
            old = e.get("slug")
            e.update(slug=slug, path=path)
            entries = [x for x in entries if x is e or not any(x is s for s in same)]
            reposj.write_text(json.dumps(entries, indent=1) + "\n", encoding="utf-8")
            done.append(f"repos.json: {old} -> {slug} at {path} (updated in place)")
        else:
            done.append(f"repos.json has {slug}")
    else:
        entries.append({"slug": slug, "path": path, "base_branch": base})
        reposj.write_text(json.dumps(entries, indent=1) + "\n", encoding="utf-8")
        done.append(f"repos.json += {slug} (enroll it in the routine only after the first enrolled repo completes one cycle)")
    if clone:
        dest = routine.data_home() / site.get("home_clone_dir")
        if not dest.exists():
            url = home_url or site.get("home_clone_url")
            if not url:
                raise InitError("--clone needs home_clone_url in the skill's site.json")
            sh(["git", "clone", "-q", url, str(dest)])
            done.append(f"dedicated home clone at {dest}")
        else:
            done.append("dedicated home clone present")
    return ok, done


# ------------------------------------------------------------------ upgrade (RE10)

VENDORED_PREFIX = "tools/docs/"
# Matches the repos' changelog guard escape (scripts/changelog_guard.py ESCAPES), case-insensitively.
NO_USER_VISIBLE_LINE = ("No user-visible change: this upgrade changes only the vendored docs tooling "
                        "(tools/docs and its PIN), which is internal.")


def upgrade_body(proof: str, changed: List[str], vd: str = config.DEFAULT_VENDOR_DIR) -> str:
    """The upgrade PR body. Vendored docs tooling is internal (ruling of 2026-10-12, recorded in the
    home repo's DECISIONS), so when every changed path is under tools/docs/ the body declares no
    user-visible change and the repo's changelog check passes. Any other path (the live workflow, docs.json) gets no
    line, so the changelog check still asks for an entry or a human's declaration."""
    body = ("Upgrade the vendored manage-docs tools.\n\nLocal trust-root proof (contract section 2):\n\n```\n"
            + proof + "\n```\n")
    if changed and all(p.startswith(vd + "/") for p in changed):
        body += "\n" + NO_USER_VISIBLE_LINE + "\n"
    return body


def upgrade(skill_root: Path, repo: Path, plugins=(), push: bool = True) -> Tuple[int, str]:
    """Open the vendored tools/docs upgrade PR (branch docs/upgrade-<sha>, never auto-merged) and run
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
    # The live workflow is rendered from the vendored template (and the repo's seeds), so a template or
    # seed change reaches the repo here; the upgrade branch is the one PR the X6 guard lets change it.
    wf = repo / WORKFLOW_DST
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_text(render_workflow(skill_root, repo), encoding="utf-8")
    # the vendored tools, the live workflow, and what a layout step changed (docs.json; its `git mv`s
    # are already staged)
    vd = repo_vendor_dir(repo)
    sh(["git", "add", "-A", vd, WORKFLOW_DST]
       + (["docs.json"] if (repo / "docs.json").exists() else []), cwd=repo)
    if sh(["git", "diff", "--cached", "--quiet"], cwd=repo, check=False).returncode == 0:
        return 0, f"upgrade: {vd} already at " + sha[:12]
    sh(["git", "commit", "-q", "-m", f"chore(docs): upgrade {vd} to {sha[:12]}"], cwd=repo)
    if not push:
        return 0, proof
    sh(["git", "push", "-q", "origin", f"HEAD:refs/heads/{branch}"], cwd=repo)
    changed = sh(["git", "show", "--name-only", "--no-renames", "--format=", "HEAD"], cwd=repo).stdout.split()
    body = upgrade_body(proof, changed, vd)
    out = sh([gh_bin(), "pr", "create", "--base", base, "--head", branch, "--title",
              f"chore(docs): upgrade {vd} to {sha[:12]}", "--body", body], cwd=repo).stdout.strip()
    # Never `--auto`: an upgrade PR changes the vendored engine, so it needs an independent review at head
    # like any other PR (.claude/rules/pr-review.md). The daily routine requests that review and merges only
    # on a clean verdict at head plus green required CI (routine.try_merge_upgrade_pr).
    return 0, f"upgrade PR {out}"
