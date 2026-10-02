#!/usr/bin/env python3
"""Claude Code PreToolUse hook on Write|Edit (manage-docs v2 section 7.4, E2).

WARNS (systemMessage) when a doc is written outside the docs layout (`doc_globs` but not in
`md_allow` / `root_allow`). It never denies: CI check 1 is the enforcement, and Codex has no hook.
Silent in repos without docs.json. Vendored at tools/docs/hooks/where_hook.py.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    fp = (event.get("tool_input") or {}).get("file_path")
    if not fp:
        return 0
    path = Path(fp)
    start = path.parent if path.parent.exists() else Path(event.get("cwd") or ".")
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start, capture_output=True, text=True)
    if top.returncode != 0:
        return 0
    root = Path(top.stdout.strip())
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return 0
    try:
        from docslib import checks, config
        cfg = config.load(root)
    except Exception:  # never block a write because the hook itself broke
        return 0
    if cfg is None or not config.is_doc(cfg, rel) or config.placed(cfg, rel):
        return 0
    rc, hits = checks.where(Path(rel).stem.replace("-", " ").replace("_", " "))
    hint = f" Suggested: {hits[0][1]}." if rc == 0 else ""
    msg = (f"manage-docs: {rel} is outside the docs layout (docs.json md_allow/root_allow); CI check 1 will "
           f"fail it. Run `python3 tools/docs/docs.py where \"<what it is>\"` for the right place.{hint}")
    print(json.dumps({"systemMessage": msg}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
