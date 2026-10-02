"""Cold-read test (manage-docs v2 section 7.3, E1, OV6; T13). LOCAL ONLY.

A second model reads ONLY the docs (README, router, docs/, work/README.md), copied into a sandbox,
and answers fixed questions. Answers are JSON `{"id", "answer", "source"}` with the exact value only:
single values compare by exact string after trimming whitespace and surrounding backticks, lists by
set equality, prose is never parsed. Every answer must cite a file inside the sandbox.

Questions come from docs.json `read_test` and from every loaded plugin's `read_test_questions(ctx)`
(contract DE5: one sandbox, one reviewer run, every plugin's comparator).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

SANDBOX_FILES = ["README.md", "AGENTS.md", "CLAUDE.md", "work/README.md"]
SANDBOX_DIRS = ["docs"]


class ColdReadError(Exception):
    pass


def norm(value) -> str:
    s = str(value).strip()
    while len(s) >= 2 and s[0] == s[-1] == "`":
        s = s[1:-1].strip()
    return s


def expected_value(root: Path, cfg: dict, expect: dict):
    """Resolve an `expect` spec: {"fact": key} | {"literal": "..."} | {"file": "path#heading"}."""
    if "fact" in expect:
        facts = cfg.get("facts") or {}
        if expect["fact"] not in facts:
            raise ColdReadError(f"read_test expects fact `{expect['fact']}`, not set in docs.json facts")
        return facts[expect["fact"]]
    if "literal" in expect:
        return expect["literal"]
    if "file" in expect:
        path, _, heading = expect["file"].partition("#")
        text = (Path(root) / path).read_text(encoding="utf-8")
        block = first_code_block_under(text, heading)
        if block is None:
            raise ColdReadError(f"no fenced code block under `{heading}` in {path}")
        return block
    raise ColdReadError(f"unknown expect spec {expect}")


def first_code_block_under(text: str, heading: str) -> Optional[str]:
    lines = text.splitlines()
    want = heading.strip().lower()
    i, n = 0, len(lines)
    while i < n:
        m = re.match(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", lines[i])
        if m and (m.group(1).strip().lower() == want or _slug(m.group(1)) == want):
            j = i + 1
            while j < n:
                if re.match(r"^\s{0,3}#{1,6}\s", lines[j]):
                    return None
                f = re.match(r"^\s{0,3}(```|~~~)", lines[j])
                if f:
                    fence, body = f.group(1), []
                    j += 1
                    while j < n and not lines[j].lstrip().startswith(fence):
                        body.append(lines[j])
                        j += 1
                    return "\n".join(body)
                j += 1
            return None
        i += 1
    return None


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def compare(expected, answer) -> bool:
    """Exact compare (OV6): lists by set equality, everything else as one exact string."""
    if isinstance(expected, list):
        if not isinstance(answer, list):
            return False
        return {norm(x) for x in expected} == {norm(x) for x in answer}
    if isinstance(answer, (list, dict)):
        return False
    return norm(expected) == norm(answer)


def build_sandbox(root: Path, run_id: Optional[str] = None) -> Path:
    root = Path(root)
    run_id = run_id or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    box = root / ".docs-cache" / "coldread" / run_id
    box.mkdir(parents=True, exist_ok=True)
    for rel in SANDBOX_FILES:
        src = root / rel
        if src.is_file():
            (box / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, box / rel)
    for rel in SANDBOX_DIRS:
        src = root / rel
        if src.is_dir():
            shutil.copytree(src, box / rel, dirs_exist_ok=True)
    return box


PROMPT = """Answer each question using ONLY the files in your current working directory. Do not read
anything outside it. For each question give the exact value only (a command, a path, a list of
names), never a sentence. Cite the file (a path relative to the current directory) the answer came
from. Answer with exactly one JSON array and nothing else:
[{{"id": "<question id>", "answer": "<exact value, or a JSON list for list questions>",
  "source": "<relative path>"}}, ...]

Questions:
{questions}
"""


def parse_answers(out: str) -> Optional[List[dict]]:
    dec = json.JSONDecoder()
    found = None
    for i, ch in enumerate(out):
        if ch == "[":
            try:
                obj, _ = dec.raw_decode(out[i:])
            except json.JSONDecodeError:
                continue
            if isinstance(obj, list) and all(isinstance(x, dict) and "id" in x for x in obj):
                found = obj
    return found


def run_reviewer(box: Path, questions: List[dict], reviewer_cmd: List[str], timeout: int = 1800) -> List[dict]:
    qtext = "\n".join(f"- {q['id']}: {q['question']}" for q in questions)
    prompt = PROMPT.format(questions=qtext)
    for _ in range(2):
        p = subprocess.run(reviewer_cmd, input=prompt, cwd=box, capture_output=True, text=True,
                           timeout=timeout)  # stdin, never argv (E2BIG on long prompts)
        ans = parse_answers(p.stdout) if p.returncode == 0 else None
        if ans is not None:
            (box / "answers.json").write_text(json.dumps(ans, indent=1) + "\n", encoding="utf-8")
            return ans
    raise ColdReadError(f"ReviewUnavailable: `{' '.join(reviewer_cmd)}` gave no answer JSON after one retry")


def source_inside(box: Path, source) -> bool:
    if not isinstance(source, str) or not source.strip():
        return False
    s = source.strip().split("#", 1)[0]
    p = Path(s)
    full = (p if p.is_absolute() else box / p).resolve()
    try:
        full.relative_to(box.resolve())
    except ValueError:
        return False
    return full.exists()


def grade(box: Path, questions: List[dict], answers: List[dict]) -> List[Tuple[str, bool, str]]:
    """[(id, passed, why)] for every question; an answer citing outside the sandbox fails."""
    by_id: Dict[str, dict] = {}
    for a in answers:
        by_id.setdefault(str(a.get("id")), a)
    out = []
    for q in questions:
        a = by_id.get(q["id"])
        if a is None:
            out.append((q["id"], False, "no answer"))
            continue
        if not source_inside(box, a.get("source")):
            out.append((q["id"], False, f"source outside the sandbox or missing: {a.get('source')!r}"))
            continue
        cmp: Callable = q.get("compare") or compare
        if cmp(q["expected"], a.get("answer")):
            out.append((q["id"], True, ""))
        else:
            out.append((q["id"], False, f"expected {q['expected']!r}, got {a.get('answer')!r}"))
    return out


def questions_from_cfg(root: Path, cfg: dict) -> List[dict]:
    qs = []
    for entry in cfg.get("read_test") or []:
        qs.append({"id": entry["id"], "question": entry["question"],
                   "expected": expected_value(root, cfg, entry["expect"])})
    return qs


def run(root: Path, cfg: dict, reviewer_cmd: List[str], plugin_questions: List[dict] = ()) -> int:
    """`docs.py check --read-test`: 0 every question passed, 1 any failed or no questions."""
    questions = questions_from_cfg(root, cfg) + list(plugin_questions)
    if not questions:
        print("read-test: no questions (docs.json read_test is empty and no plugin added any)")
        return 1
    ids = [q["id"] for q in questions]
    if len(ids) != len(set(ids)):
        raise ColdReadError("duplicate read_test question ids")
    box = build_sandbox(root)
    answers = run_reviewer(box, questions, reviewer_cmd)
    results = grade(box, questions, answers)
    for qid, ok, why in results:
        print(f"{'PASS' if ok else 'FAIL'} {qid}" + (f": {why}" if why else ""))
    return 0 if all(ok for _, ok, _ in results) else 1
