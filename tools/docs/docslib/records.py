"""work/ records: done-ness and the ledgered sweep (manage-docs v2 section 3; `docs.py sweep`).

Done = `status: done` (file frontmatter or folder record.json), or `pr` set and that PR merged.
Records that arrived by migration (no frontmatter, no record.json) are implicitly done. Done records
older than `records_window_days` move to `work/archive/<YYYY>/<kind>/<name>`, byte-identical, and each
move is a ledger row in `work/runs/<date>-maintain/sweep.jsonl`. Those moves are the ONLY permitted
change to a done record (check 6).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from docslib import frontmatter, receipts
from docslib.generate import KIND_DIRS, REC_NAME, _files


def _pr_merged(url: str) -> bool:
    gh = os.environ.get("MANAGE_DOCS_GH", "gh")
    p = subprocess.run([gh, "pr", "view", url, "--json", "state"], capture_output=True, text=True)
    if p.returncode != 0:
        return False
    try:
        return json.loads(p.stdout).get("state") == "MERGED"
    except json.JSONDecodeError:
        return False


def is_done(root: Path, rec: str) -> bool:
    """rec = `work/<kind dir>/<YYYY-MM-DD-slug>[.md]` (a file or a folder)."""
    fp = Path(root) / rec
    if fp.is_dir():
        rj = fp / "record.json"
        if not rj.is_file():
            return True
        try:
            data = json.loads(rj.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        return data.get("status") == "done" or bool(data.get("pr") and _pr_merged(data["pr"]))
    try:
        fm, _ = frontmatter.parse(fp.read_text(encoding="utf-8", errors="replace"))
    except (frontmatter.FrontmatterError, OSError):
        return False
    if fm is None:
        return True
    return fm.get("status") == "done" or bool(fm.get("pr") and _pr_merged(str(fm["pr"])))


def candidates(root: Path, window_days: int, today: dt.date) -> List[dict]:
    out, seen = [], set()
    for p in _files(Path(root), "work/"):
        parts = p.split("/")
        if len(parts) < 3 or parts[1] == "archive" or parts[1] not in KIND_DIRS:
            continue
        rec = "/".join(parts[:3])
        if rec in seen:
            continue
        seen.add(rec)
        m = REC_NAME.match(parts[2])
        if not m:
            continue
        date = dt.date.fromisoformat(m.group(1))
        if (today - date).days <= window_days or not is_done(root, rec):
            continue
        dest = f"work/archive/{date.year}/{KIND_DIRS[parts[1]]}/{parts[2]}"
        out.append({"src": rec, "dest": dest})
    return sorted(out, key=lambda r: r["src"])


def _tree_hash(fp: Path) -> str:
    if fp.is_file():
        return receipts.sha256_text(fp.read_bytes().decode("latin-1"))
    lines = []
    for f in sorted(fp.rglob("*")):
        if f.is_file():
            lines.append(f"{f.relative_to(fp).as_posix()} {receipts.sha256_text(f.read_bytes().decode('latin-1'))}")
    return receipts.sha256_text("\n".join(lines))


def _tracked(root: Path, rel: str) -> bool:
    p = subprocess.run(["git", "ls-files", "--error-unmatch", "--", rel], cwd=root, capture_output=True)
    return p.returncode == 0


def fresh_run_dir(root: Path, today: dt.date) -> str:
    """A maintain run record dir that does not exist yet (a done record is never appended to)."""
    base = f"work/runs/{today.isoformat()}-maintain"
    cand, n = base, 2
    while (Path(root) / cand).exists():
        cand, n = f"{base}-{n}", n + 1
    return cand


def sweep(root: Path, cfg: dict, today: Optional[dt.date] = None, run_dir: Optional[str] = None,
          only: Optional[set] = None) -> List[dict]:
    """Move done records past the window; return ledger rows (each with a fingerprint).

    `only` limits the sweep to these record paths (maintain passes the unsuppressed ones)."""
    root = Path(root)
    today = today or dt.date.today()
    rows = []
    for c in candidates(root, int(cfg.get("records_window_days", 30)), today):
        if only is not None and c["src"] not in only:
            continue
        src, dest = root / c["src"], root / c["dest"]
        if dest.exists():
            continue  # never overwrite an archived record
        h = _tree_hash(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if _tracked(root, c["src"]):
            subprocess.run(["git", "mv", c["src"], c["dest"]], cwd=root, check=True, capture_output=True)
        else:
            shutil.move(str(src), str(dest))
        rows.append({"op": "sweep", "path": c["src"], "dest": c["dest"], "input": h,
                     "at": today.isoformat()})
    if rows:
        rd = root / (run_dir or fresh_run_dir(root, today))
        rd.mkdir(parents=True, exist_ok=True)
        with (rd / "sweep.jsonl").open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, sort_keys=True) + "\n")
        rj = rd / "record.json"
        if not rj.exists():
            rj.write_text(json.dumps({"status": "done", "pr": None}) + "\n", encoding="utf-8")
    return rows
