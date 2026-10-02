#!/usr/bin/env python3
"""manage-docs: the single entry point (manage-docs v2 section 12) AND the engine's docs plugin.

As a CLI (`python3 tools/docs/docs.py <command>`): init, preflight, check, verify, generate, sweep,
where, approval-request, routine-prompt, maintain, migrate, upgrade, scan-deny, hook.

As a plugin (`migrate.py ... --plugin tools/docs/docs.py`, engine/INTERFACE.md section 3): `name`,
`scan` (the complement of the roadmap plugin's claimed set), `dispositions`, `triage`, `consumers`,
`verify`, plus the DE5 extras `run_checks` (none beyond docs.py's own) and `read_test_questions`.

Python 3.9+ standard library only. Logic lives in docslib/ (ER15 / D6).
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

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from docslib import checks, coldread, config, frontmatter, generate, globs, receipts, records, routine, site  # noqa: E402
from docslib._engine import engine  # noqa: E402

migrate = engine()

# ======================================================================== the docs plugin

name = "docs"
dispositions = dict(migrate.STANDARD)

RECORD_PATTERNS = [
    (re.compile(r"(^|/)handoffs?/"), "handoff"),
    (re.compile(r"(^|/)runs?/"), "run"),
    (re.compile(r"(^|/)status/"), "status"),
    (re.compile(r"(^|/)codex-autopilot/"), "run"),
    (re.compile(r"(^|/)Agent_Feedback/"), "feedback"),
    (re.compile(r"(^|/)evidence/"), "evidence"),
    (re.compile(r"(^|/)\d{4}-\d{2}-\d{2}[^/]*audit[^/]*$", re.I), "audit"),
]
STATE_DOCS = re.compile(r"(^|/)(CURRENT-CONTEXT|CURRENT_CONTEXT|STATUS|CURRENT-STATE)\.md$")
LAYOUT_DIRS = ("docs/roadmap/", "docs/architecture/", "docs/guides/", "docs/runbooks/", "docs/rules/",
               "docs/generated/")
CITING = ["AGENTS.md", "CLAUDE.md", "GEMINI.md"]
PLACEMENT_TOOLS = list(site.get("placement_tools"))  # the home repo's placement enforcers (site.json)


def record_kind(path: str):
    for rx, kind in RECORD_PATTERNS:
        if rx.search(path):
            return kind
    return None


PATH_TOKEN = re.compile(r"[\w./-]+\.(?:md|mdx|rst|txt|html)\b")


def is_link(repo, path) -> bool:
    """A tracked symlink is code: its link text is its content; never read through it (it may
    dangle or point at a directory)."""
    return (repo.root / path).is_symlink()


def references(repo, cfg) -> dict:
    """{doc path: sorted referrers}: every live doc or constitution/rule file that names it, by
    relative link or repo-relative path. One pass over the docs (not per pair)."""
    cache = getattr(repo, "_docs_refs", None)
    if cache is not None:
        return cache
    docs = [p for p in repo.tracked if config.is_doc(cfg, p) and not is_link(repo, p)]
    known = set(docs)
    refs = {}
    for src in docs:
        if src.startswith("work/"):
            continue  # records are history; their links do not keep a doc alive
        text = repo.read_text(src)
        d = src.rsplit("/", 1)[0] if "/" in src else ""
        cands = set(PATH_TOKEN.findall(text)) | set(checks.link_targets(text))
        for tok in cands:
            tok = tok.split("#", 1)[0].lstrip("./") if tok.startswith("./") else tok.split("#", 1)[0]
            for res in {tok.lstrip("/"), checks.resolve_link(src, tok) or ""}:
                if res in known and res != src:
                    refs.setdefault(res, set()).add(src)
    out = {k: sorted(v) for k, v in refs.items()}
    repo._docs_refs = out
    return out


def constitution_cited(live_refs) -> bool:
    return any(r in CITING or globs.match(".claude/rules/*.md", r) for r in live_refs)


def open_pr_files(root) -> "set | None":
    """Paths touched by open PRs; None when gh cannot say (then no doc is auto-deleted as stale).
    The same rule as the roadmap plugin's: a file an open PR edits is live work, never stale."""
    try:
        data = migrate.gh_json(["pr", "list", "--state", "open", "--limit", "500", "--json", "number,files"], root)
    except Exception:  # noqa: BLE001 (any gh failure means "unknown", never "none")
        return None
    out = set()
    for pr in data or []:
        for f in pr.get("files") or []:
            out.add(f.get("path") if isinstance(f, dict) else str(f))
    return out


def scan(repo):
    cfg = config.merged(repo.cfg)
    refs = references(repo, cfg)
    pr_files = open_pr_files(repo.root)
    bead_text = "\n".join(migrate.bead_text(r) for r in repo.beads)
    for path in repo.tracked:
        if path in repo.claimed:
            continue
        date = repo.last_commit_date(path)
        if is_link(repo, path):
            yield migrate.make_item(name, path, "", "code", os.readlink(repo.root / path), last_commit_date=date)
            continue
        text = repo.read_text(path)
        if not config.is_doc(cfg, path):
            yield migrate.make_item(name, path, "", "code", text, last_commit_date=date)
            continue
        head = "\n".join(text.splitlines()[:12])
        terminal = bool(record_kind(path)) or bool(re.search(r"^status:\s*done\s*$", head, re.M))
        live = list(refs.get(path, []))
        if pr_files is None:
            live.append("pr:unknown")
        elif path in pr_files:
            live.append("pr:open")
        if bead_text and path in bead_text:
            live.append("beads")
        yield migrate.make_item(name, path, "", "file", text, terminal=terminal, last_commit_date=date,
                                live_refs=live)


def triage(item, cfg):
    cfg = config.merged(cfg)
    path = item["source_path"]
    if item["kind"] != "file":
        return None
    kind = record_kind(path)
    if kind and not path.startswith("work/"):
        return f"record:{kind}"
    if path.startswith("work/"):
        return "keep"
    if STATE_DOCS.search(path):
        return "delete"  # hand-written state doc: delete + a generator entry (the agent adds it)
    if item["signals"]["terminal"]:
        return "record:work-item"
    if constitution_cited(item["signals"].get("live_refs") or []):
        return "keep"  # constitution- or rule-cited: byte-identical, frontmatter-exempt
    if path.startswith(LAYOUT_DIRS) or path == "docs/README.md":
        return "keep"
    if not path.startswith("docs/") and config.placed(cfg, path):
        return "keep"  # already where the standard puts it (root files, .claude/**, package READMEs)
    stale_days = int((cfg.get("roadmap") or {}).get("stale_days", 60))
    last = item["signals"].get("last_commit_date")
    if last and not item["signals"].get("live_refs"):
        try:
            age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(last)).days
        except ValueError:
            age = 0
        if age > stale_days and path.startswith("docs/"):
            return "delete"  # stale, unreferenced non-record (listed at approve)
    return None


def consumers(repo, paths):
    """Placement tooling must accept tracked top-level work/ (v2 section 10; ER16)."""
    out = []
    for tool in PLACEMENT_TOOLS:
        if tool in repo.tracked:
            out.append({"path": tool, "line": 1, "text": "placement tooling: must accept tracked work/",
                        "target": "work/", "role": "reader"})
    return out


def verify(repo, ledger):
    """Section 6 checks over the migrated tree: debt warns, paths the run touched fail; rewrite
    receipts' covers_hash recomputed (the engine checks doc_hash)."""
    cfg = config.merged(repo.cfg)
    ctx = checks.Ctx(repo.root, cfg, scope="all")
    touched = set()
    for row in ledger:
        touched.update(row.get("dest") or [])
        if row.get("disposition") in ("keep", "rewrite"):
            touched.add(row["source_path"])
    out = []
    for r in checks.run(ctx):
        blocking = "fail" if (r["level"] == "fail" and r["path"] in touched) else "warn"
        out.append({"item_id": None, "message": f"[{r['check']}] {r['path']}: {r['message']}", "blocking": blocking})
    for row in ledger:
        if row.get("disposition") != "rewrite" or not row.get("receipt"):
            continue
        p = row["source_path"]
        if not (repo.root / p).is_file():
            continue
        fm, _ = frontmatter.parse(repo.read_text(p))
        want = receipts.covers_hash(repo.root, ctx.index, list((fm or {}).get("covers") or []), cfg["doc_globs"])
        if want != row["receipt"].get("covers_hash"):
            out.append({"item_id": row["item_id"], "message": f"{p}: receipt covers_hash does not match the tree",
                        "blocking": "fail"})
    return out


def run_checks(ctx):
    return []


def read_test_questions(ctx):
    return []


# ======================================================================== CLI helpers

def repo_root(arg) -> Path:
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=arg or ".", capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"docs.py: not a git repository: {arg or '.'}")
    return Path(p.stdout.strip())


def load_cfg(root: Path, required: bool = True) -> dict:
    try:
        cfg = config.load(root)
    except config.ConfigError as exc:
        sys.exit(f"docs.py: {exc}")
    if cfg is None:
        if required:
            sys.exit("docs.py: no docs.json here; run `docs.py init --stage pr1` first")
        return config.merged({})
    return cfg


def runtime() -> str:
    return os.environ.get("MANAGE_DOCS_RUNTIME", "claude")


def roadmap_file():
    """roadmap.py: beside docs.py when vendored (tools/docs/), else in the sibling manage-roadmap skill."""
    for rp in (HERE / "roadmap.py", HERE.parent / "manage-roadmap" / "roadmap.py"):
        if rp.is_file():
            return rp
    return None


def plugin_files():
    """Plugin files for the engine, roadmap first (RE4: it claims first, docs takes the complement)."""
    rp = roadmap_file()
    return ([rp] if rp else []) + [HERE / "docs.py"]


def other_plugins():
    """Plugins beside docs.py that join the one-pass check / migrate (contract DE5, RE10)."""
    rp = roadmap_file()
    return [migrate.load_plugin(str(rp))] if rp else []


def skill_root() -> Path:
    return HERE


# ======================================================================== commands

def predates_bootstrap(root: Path, base: "str | None") -> bool:
    """A PR branch cut before the docs bootstrap: no docs.json on the branch, but its base has one. In CI the
    base's vendored engine (HERE, under the base checkout) checks the head checkout, so either the base
    commit's tree or the engine's own checkout proves the base is bootstrapped."""
    if not base or (root / "docs.json").is_file():
        return False
    p = subprocess.run(["git", "cat-file", "-e", f"{base}:docs.json"], cwd=root, capture_output=True)
    if p.returncode == 0:
        return True
    home = HERE.parent.parent  # tools/docs/ -> the checkout that vendored this engine
    return HERE.name == "docs" and HERE.parent.name == "tools" and (home / "docs.json").is_file()


def cmd_check(a) -> int:
    root = repo_root(a.repo)
    if a.pr and predates_bootstrap(root, a.pr):
        print(f"docs.py: this branch{' (' + a.head_ref + ')' if a.head_ref else ''} predates the docs bootstrap: "
              f"it has no docs.json, but its base {a.pr[:10]} does. Update the branch from its base "
              "(`gh pr update-branch <pr>`, or merge the base branch into it) and the check re-runs.")
        return 1
    cfg = load_cfg(root)
    scope = "pr" if a.pr else "all"
    plugins = other_plugins()
    if a.read_test:
        qs = []
        for plug in plugins:
            fn = getattr(plug, "read_test_questions", None)
            if fn is not None:
                qs.extend(fn(checks.Ctx(root, cfg)) or [])
        try:
            return coldread.run(root, cfg, receipts.reviewer_cmd(cfg, runtime()), qs)
        except coldread.ColdReadError as exc:
            print(f"read-test: {exc}")
            return 1
    ctx = checks.Ctx(root, cfg, scope=scope, base=a.pr, head_ref=a.head_ref, local=a.local)
    results = checks.run(ctx, plugins)
    if a.local and config.is_public(cfg):
        results += checks.check7_loosening(ctx, force_local=True) if scope == "pr" else []
    rc = checks.exit_code(results, scope)
    if results:
        print(checks.render(results, annotate=a.annotate or bool(os.environ.get("GITHUB_ACTIONS"))))
    if scope == "pr":
        mig = sorted({p.split("/")[2] for p in ctx.changed("ACMR") if re.match(r"^work/runs/\d{4}-\d{2}-\d{2}-migration/", p)})
        for run_dir in mig:
            argv = [sys.executable, str(HERE / "engine" / "migrate.py"), "verify", "--ci", "--base", ctx.base,
                    "--run", run_dir[:10], "--repo", str(root)]
            for plug in plugin_files():
                argv += ["--plugin", str(plug)]
            p = subprocess.run(argv, capture_output=True, text=True)
            print(p.stdout + p.stderr, end="")
            if p.returncode != 0:
                rc = 1
    if a.local and config.is_public(cfg):
        from docslib import namescan
        rcs = namescan.scan_deny_main(root, cfg, history=False)
        if rcs:
            rc = 1
    print(f"check: {'FAIL' if rc == 1 else 'PASS'}" + (" (warnings only)" if rc == 4 or (rc == 0 and results) else ""))
    return rc


def cmd_verify(a) -> int:
    root = repo_root(a.repo)
    cfg = load_cfg(root)
    doc = a.doc
    try:
        if a.prepare or a.run:
            pk = receipts.prepare(root, doc, cfg)
            print(f"prepared {receipts.packet_dir(root, frontmatter.parse((root / doc).read_text())[0]['id'])}/packet.json")
            if a.prepare:
                return 0
            receipts.run_review(root, pk, receipts.reviewer_cmd(cfg, runtime()))
        rec = receipts.finish(root, doc, cfg)
        print(f"receipt written: {doc} verified {rec['at']} by {rec['by']}")
        return 0
    except receipts.VerifyError as exc:
        print(f"verify: {exc}")
        return 3
    except receipts.ReviewUnavailable as exc:
        print(f"ReviewUnavailable: {exc}")
        return 12


def cmd_generate(a) -> int:
    root = repo_root(a.repo)
    cfg = load_cfg(root)
    try:
        rc, changed = generate.run(root, cfg, check=a.check)
    except generate.GenerateError as exc:
        print(f"generate: {exc}")
        return 1
    for c in changed:
        print(("stale: " if a.check else "wrote: ") + c)
    return rc


def cmd_sweep(a) -> int:
    root = repo_root(a.repo)
    cfg = load_cfg(root)
    rows = records.sweep(root, cfg)
    for r in rows:
        print(f"archived {r['path']} -> {r['dest']}")
    print(f"sweep: {len(rows)} record(s) archived")
    return 0


def cmd_where(a) -> int:
    rc, hits = checks.where(" ".join(a.what))
    if rc == 1:
        print("where: no match; describe it with a word from the table (handoff, run, evidence, audit, guide, "
              "runbook, architecture, rule, decision, plan, generated, cache)")
    elif rc == 0:
        print(hits[0][1])
    else:
        print("where: several kinds match; pick one:")
        for kind, path in hits:
            print(f"  {kind}: {path}")
    return rc


def cmd_approval_request(a) -> int:
    root = repo_root(a.repo)
    view = routine.gh_json(root, "pr", "view", str(a.pr), "--json", "body")
    changes = routine.changes_from_body((view or {}).get("body", ""))
    for c in changes:
        if c["op"] == "receipt":
            c["title"] = routine.doc_title(root, c["path"])
        if c["op"] == "generate":
            c["label"] = {"docs/README.md": "the docs map", "work/README.md": "the records index"}.get(
                c["path"], f"the generated file {c['path']}")
    print(routine.approval_section(routine.docs_lines(changes, []), routine.roadmap_lines(other_plugins(), root, changes)))
    return 0


def cmd_routine_prompt(a) -> int:
    print(routine.routine_prompt(runtime()))
    return 0


def cmd_maintain(a) -> int:
    if a.here:
        root = repo_root(a.repo)
        cfg = load_cfg(root)
        slug = a.slug or root.name
        try:
            return routine.maintain_here(root, cfg, slug, runtime(), other_plugins(), verify=not a.no_verify,
                                         push=not a.no_push)
        except routine.RoutineError as exc:
            print(f"maintain: {exc}")
            return 1
    clone = Path(os.environ.get("MANAGE_DOCS_CLONE", routine.data_home() / site.get("home_clone_dir")))
    return routine.routine(runtime(), clone, HERE / "docs.py", only=a.repo_slug)


def cmd_preflight(a) -> int:
    from docslib import initrepo
    root = repo_root(a.repo)
    cfg = load_cfg(root, required=False)
    go, lines = initrepo.preflight(root, cfg, runtime())
    print("\n".join(lines))
    print("preflight: " + ("GO" if go else "NO-GO"))
    return 0 if go else 1


def cmd_init(a) -> int:
    from docslib import initrepo
    root = repo_root(a.repo)
    try:
        if a.stage == "pr1":
            cfg = config.load(root) or initrepo_seed(root, a.tier)
            if not a.skip_preflight:
                go, lines = initrepo.preflight(root, cfg, runtime())
                print("\n".join(lines))
                if not go:
                    print("init: preflight NO-GO; nothing written")
                    return 1
            done = initrepo.stage_pr1(skill_root(), root, a.tier, a.decision)
            ok, proof = initrepo.pin_proof(skill_root(), root)
            print("\n".join(f"init: {d}" for d in done))
            print("local trust-root proof (paste into the PR body):\n" + proof)
            return 0 if ok else 1
        if a.stage == "migration":
            done = initrepo.stage_migration(skill_root(), root, other_plugins())
            print("\n".join(f"init: {d}" for d in done))
            return 0
        if a.stage == "wire":
            done = initrepo.stage_wire(skill_root(), root)
            print("\n".join(f"init: {d}" for d in done))
            return 0
        ok, done = initrepo.stage_local(root, clone=a.clone)
        print("\n".join(f"init: {d}" for d in done))
        return 0 if ok else 1
    except initrepo.InitError as exc:
        print(f"init: {exc}")
        return 1


def initrepo_seed(root: Path, tier):
    from docslib import initrepo
    return config.merged(initrepo.seed_cfg(root, tier))


def cmd_upgrade(a) -> int:
    from docslib import initrepo
    root = repo_root(a.repo)
    try:
        rc, msg = initrepo.upgrade(skill_root(), root, other_plugins(), push=not a.no_push)
    except initrepo.InitError as exc:
        print(f"upgrade: {exc}")
        return 1
    print(msg)
    return rc


def cmd_migrate(a, extra) -> int:
    argv = [sys.executable, str(HERE / "engine" / "migrate.py"), a.step]
    for plug in plugin_files():
        argv += ["--plugin", str(plug)]
    if a.repo:
        argv += ["--repo", a.repo]
    return subprocess.call(argv + extra)


def cmd_scan_deny(a) -> int:
    from docslib import namescan
    root = repo_root(a.repo)
    cfg = load_cfg(root)
    if a.rebuild:
        home = Path(a.home) if a.home else Path(os.environ.get("MANAGE_DOCS_CLONE",
                                                               routine.data_home() / site.get("home_clone_dir")))
        try:
            data = namescan.build_list(namescan.default_list_path(cfg), home if home.is_dir() else None)
        except namescan.ListMissing as exc:
            print(f"scan-deny: {exc}")
            return 6
        print(f"scan-deny: list rebuilt ({len(data.get('terms', []))} terms)" + (f"; {data['note']}" if data.get("note") else ""))
    return namescan.scan_deny_main(root, cfg, history=a.history)


def cmd_hook(a) -> int:
    from docslib import namescan
    root = repo_root(None)
    cfg = config.load(root)
    if cfg is None:
        return 0
    rc = 0
    if a.name == "pre-commit":
        ctx = checks.Ctx(root, cfg, scope="staged", local=True)
        results = [r for r in checks.run(ctx) if r["check"] not in ("drift", "receipt")]
        if results:
            print(checks.render(results))
        rc = checks.exit_code(results, "pr")
    stdin_text = sys.stdin.read() if a.name == "pre-push" else ""
    nrc = namescan.hook_main(a.name, a.args, stdin_text, root, cfg)
    return rc or nrc


# ======================================================================== argparse

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="docs.py", description="manage-docs: one docs standard per repo")
    ap.add_argument("--repo", default=None, help="repo path (default: the current git repo)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="set up a repo in stages: pr1 | migration | local")
    s.add_argument("--stage", choices=["pr1", "migration", "wire", "local"], default="pr1")
    s.add_argument("--tier", choices=["small", "full"])
    s.add_argument("--decision", help="file whose text is appended to docs/roadmap/DECISIONS.md (pr1)")
    s.add_argument("--clone", action="store_true", help="local: create the routine's dedicated clone of the skill's home repo")
    s.add_argument("--skip-preflight", action="store_true", help=argparse.SUPPRESS)

    sub.add_parser("preflight", help="contract step 0 checks; prints exact commands on no-go")

    s = sub.add_parser("check", help="section 6 checks plus every plugin's checks")
    s.add_argument("--pr", metavar="BASE", help="diff-scoped against BASE (CI)")
    s.add_argument("--read-test", action="store_true", help="cold-read test (local only)")
    s.add_argument("--local", action="store_true", help="add local-only checks")
    s.add_argument("--head-ref", help="PR head branch (path guard); default $GITHUB_HEAD_REF")
    s.add_argument("--annotate", action="store_true", help="GitHub annotation output")

    s = sub.add_parser("verify", help="claim check: --prepare, --finish, or --run (both + reviewer)")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--prepare", action="store_true")
    g.add_argument("--finish", action="store_true")
    g.add_argument("--run", action="store_true")
    s.add_argument("doc")

    s = sub.add_parser("generate", help="run generators")
    s.add_argument("--check", action="store_true")

    sub.add_parser("sweep", help="archive done records past the window (ledgered)")

    s = sub.add_parser("where", help="where does a new file go")
    s.add_argument("what", nargs="+")

    s = sub.add_parser("approval-request", help="render a daily PR's approval section")
    s.add_argument("pr")

    sub.add_parser("routine-prompt", help="print the scheduled-task prompt")

    s = sub.add_parser("maintain", help="daily maintenance: every enrolled repo, or --repo-slug one")
    s.add_argument("--repo-slug", "--for", dest="repo_slug", help="only this enrolled repo")
    s.add_argument("--here", action="store_true", help="run the phases in this worktree (routine internal)")
    s.add_argument("--slug", help=argparse.SUPPRESS)
    s.add_argument("--no-verify", action="store_true", help="skip claim checks (quota above 70%)")
    s.add_argument("--no-push", action="store_true", help="local dry run: commit, print the PR body")

    s = sub.add_parser("migrate", help="run the vendored engine with both plugins")
    s.add_argument("step")

    s = sub.add_parser("upgrade", help="open the tools/docs upgrade PR")
    s.add_argument("--no-push", action="store_true")

    s = sub.add_parser("scan-deny", help="client-name scan (public repos)")
    s.add_argument("--history", action="store_true")
    s.add_argument("--rebuild", action="store_true", help="rebuild the deny list from opx first")
    s.add_argument("--home", help="the home repo checkout that lists prospects (default: the dedicated clone)")

    s = sub.add_parser("hook", help="git hook entry: pre-commit | commit-msg | pre-push")
    s.add_argument("name", choices=["pre-commit", "commit-msg", "pre-push"])
    s.add_argument("args", nargs="*")
    return ap


HANDLERS = {"check": cmd_check, "verify": cmd_verify, "generate": cmd_generate, "sweep": cmd_sweep,
            "where": cmd_where, "approval-request": cmd_approval_request, "routine-prompt": cmd_routine_prompt,
            "maintain": cmd_maintain, "preflight": cmd_preflight, "init": cmd_init, "upgrade": cmd_upgrade,
            "scan-deny": cmd_scan_deny, "hook": cmd_hook}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = build_parser()
    if "migrate" in argv:
        i = argv.index("migrate")
        if i + 1 < len(argv):
            a = ap.parse_args(argv[:i + 2])
            return cmd_migrate(a, argv[i + 2:])
    a = ap.parse_args(argv)
    return HANDLERS[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
