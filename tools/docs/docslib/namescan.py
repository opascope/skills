"""Deny-term scan for public repos (manage-docs v2 section 9, ER12, OV1; contract section 7, X5, DE2).

The deny-term list is a local file outside every repo (never in git). It is rebuilt from opx
(clients: names, domain stems and aliases, active and churned, internal rows excluded) plus the home
repo's data store prospects (de-slugged); vendors are excluded. A term that is a dictionary word or has
under 4 letters WARNs with the line shown; every other term BLOCKs. Matching is case-insensitive,
whole words and phrases.

A BLOCK hit never prints the term, so the scan output itself cannot leak a protected name.

Scanned surfaces:
- pre-commit: staged added lines and staged paths.
- commit-msg: the message (comment lines skipped, as git strips them).
- pre-push: every ref update on stdin; every added line of every pushed commit with merge diffs
  (`git log -p -m`), every commit message, every touched path, ref names, tag names and annotations.
  A term added then removed inside the pushed commits still hits.
- scan-deny: tracked files and paths in the working tree, or with --history the full history.

Exit codes: 0 clean (warnings allowed), 1 a BLOCK hit, 6 the list is missing or empty where the scan
must fail closed (or git could not produce the data to scan).
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

DICT_PATH = "/usr/share/dict/words"
MAX_LIST_AGE = dt.timedelta(days=7)
ZERO_SHA_RX = re.compile(r"^0+$")
# Trailing slug tokens that are a web TLD, dropped when de-slugging a prospect ("acme-com" -> "acme").
SLUG_TLDS = frozenset(("com", "io", "co", "net", "org", "app", "dev", "us", "uk", "ca", "xyz", "inc"))
# Second-level labels under a country code ("acme.co.uk" -> "acme").
SECOND_LEVEL = frozenset(("co", "com", "org", "net", "ac", "gov", "edu"))
_WORD = r"[A-Za-z0-9_]"
_COMMENT_SAFE = "protected name"


class ListMissing(Exception):
    """The deny-term list is missing, empty, or too old to use, and could not be rebuilt."""


class ScanError(Exception):
    """git could not produce the data a scan needs; the scan fails closed."""


# ---------------------------------------------------------------------------------------------
# list location, build and load
# ---------------------------------------------------------------------------------------------

def _config_home() -> Path:
    env = os.environ.get("MANAGE_DOCS_CONFIG_HOME")
    return Path(env).expanduser() if env else Path.home() / ".config" / "manage-docs"


def _data_home() -> Path:
    env = os.environ.get("MANAGE_DOCS_DATA_HOME")
    return Path(env).expanduser() if env else Path.home() / ".local" / "share" / "manage-docs"


def default_list_path(cfg: dict) -> Path:
    explicit = (cfg or {}).get("deny_terms_file")
    if explicit:
        return Path(str(explicit)).expanduser()
    pointer = _config_home() / "deny_terms_path"
    if pointer.is_file():
        text = pointer.read_text(encoding="utf-8").strip()
        if text:
            return Path(text).expanduser()
    return _data_home() / "deny-terms.json"


def _now(now=None) -> dt.datetime:
    if now is None:
        return dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=dt.timezone.utc)
    return now


def _parse_time(s: str) -> Optional[dt.datetime]:
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t


def _parse_json_output(text: str):
    """Parse CLI JSON output, skipping any non-JSON banner lines before it."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.lstrip().startswith(("{", "[")):
            try:
                return json.loads("\n".join(lines[i:]))
            except ValueError:
                continue
    raise ValueError("no JSON document in output")


def _run_json(cmd: Sequence[str], cwd: Optional[Path] = None):
    try:
        proc = subprocess.run(list(cmd), cwd=str(cwd) if cwd else None, capture_output=True,
                              text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("could not run %s: %s" % (cmd[0], exc.__class__.__name__))
    if proc.returncode != 0:
        raise RuntimeError("%s exited %d" % (" ".join(cmd[:3]), proc.returncode))
    try:
        return _parse_json_output(proc.stdout)
    except ValueError:
        raise RuntimeError("%s output was not JSON" % " ".join(cmd[:3]))


def domain_stem(domain: str) -> str:
    d = str(domain or "").strip().lower()
    d = re.sub(r"^[a-z]+://", "", d).split("/")[0].split(":")[0]
    if d.startswith("www."):
        d = d[4:]
    parts = [p for p in d.split(".") if p]
    if len(parts) < 2:
        return parts[0] if parts else ""
    if len(parts) >= 3 and parts[-2] in SECOND_LEVEL and len(parts[-1]) == 2:
        return parts[-3]
    return parts[-2]


def deslug(slug: str) -> str:
    tokens = [t for t in str(slug or "").strip().lower().split("-") if t]
    if len(tokens) > 1 and tokens[-1] in SLUG_TLDS:
        tokens = tokens[:-1]
    return " ".join(tokens)


def _alias_value(item) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in ("entity_name", "name", "alias"):
            v = item.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _dictionary() -> set:
    try:
        with open(DICT_PATH, encoding="utf-8", errors="replace") as fh:
            return {w.strip().lower() for w in fh if w.strip()}
    except OSError:
        return set()


def _tier(term: str, words: set) -> str:
    letters = sum(1 for c in term if c.isalpha())
    if letters < 4 or term.lower() in words:
        return "warn"
    return "block"


def _norm(term: str) -> str:
    return " ".join(str(term).split()).lower()


def _collect_raw(home_root: Optional[Path], opx_cmd: Sequence[str]) -> List[str]:
    raw: List[str] = []
    data = _run_json(list(opx_cmd) + ["context", "clients"])
    clients = data.get("clients") if isinstance(data, dict) else None
    if not isinstance(clients, list):
        raise RuntimeError("opx clients output had no clients list")
    for c in clients:
        if not isinstance(c, dict) or str(c.get("type", "")).lower() == "internal":
            continue
        if isinstance(c.get("name"), str):
            raw.append(c["name"])
        domains = list(c.get("domains") or [])
        if c.get("primary_domain"):
            domains.append(c["primary_domain"])
        for d in domains:
            if isinstance(d, str):
                raw.append(domain_stem(d))
        slug = c.get("slug")
        if isinstance(slug, str) and slug:
            al = _run_json(list(opx_cmd) + ["context", "aliases", slug])
            items = al.get("aliases") if isinstance(al, dict) else None
            for item in items or []:
                raw.append(_alias_value(item))
    if home_root is not None:
        rows = _run_json([sys.executable, "scripts/data_store.py", "list", "--relationship",
                          "prospect", "--json"], cwd=home_root)
        if not isinstance(rows, list):
            raise RuntimeError("data_store list output was not a list")
        for r in rows:
            if isinstance(r, dict) and r.get("relationship") == "prospect":
                raw.append(deslug(r.get("slug", "")))
    return raw


def build_list(out_path: Path, home_root: Optional[Path], opx_cmd: Sequence[str] = ("opx",),
               now=None) -> dict:
    out_path = Path(out_path)
    when = _now(now)
    try:
        raw = _collect_raw(home_root, opx_cmd)
    except RuntimeError as exc:
        prior = _read_json(out_path)
        built = _parse_time(prior.get("built_at")) if prior else None
        if prior and prior.get("terms") and built is not None and when - built < MAX_LIST_AGE:
            prior["note"] = "opx unavailable; using last good list"
            return prior
        raise ListMissing("deny-term list could not be rebuilt (%s) and no list under 7 days old "
                          "exists" % exc)
    words = _dictionary()
    seen = set()
    terms = []
    for t in raw:
        t = " ".join(str(t).split())
        key = t.lower()
        if not t or key in seen:
            continue
        seen.add(key)
        terms.append({"term": t, "tier": _tier(t, words)})
    doc = {"built_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "terms": terms}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
        fh.write("\n")
    os.replace(str(tmp), str(out_path))
    return doc


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_list(path: Path) -> Optional[list]:
    data = _read_json(path)
    if not data:
        return None
    terms = [t for t in data.get("terms") or []
             if isinstance(t, dict) and isinstance(t.get("term"), str) and t["term"].strip()]
    return terms or None


# ---------------------------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------------------------

_MATCHER_CACHE: Dict[Tuple[Tuple[str, str], ...], Tuple[re.Pattern, Dict[str, dict]]] = {}


def _matcher(terms: list):
    key = tuple((str(t.get("term")), str(t.get("tier", "block"))) for t in terms)
    hit = _MATCHER_CACHE.get(key)
    if hit is not None:
        return hit
    by_norm: Dict[str, dict] = {}
    for t in terms:
        n = _norm(t.get("term", ""))
        if not n:
            continue
        tier = "warn" if t.get("tier") == "warn" else "block"
        prev = by_norm.get(n)
        if prev is None or (prev["tier"] == "warn" and tier == "block"):
            by_norm[n] = {"term": " ".join(str(t["term"]).split()), "tier": tier}
    if not by_norm:
        rx = None
    else:
        alts = sorted(by_norm, key=len, reverse=True)
        body = "|".join(r"\s+".join(re.escape(w) for w in n.split(" ")) for n in alts)
        rx = re.compile(r"(?<!%s)(?:%s)(?!%s)" % (_WORD, body, _WORD), re.IGNORECASE)
    _MATCHER_CACHE[key] = (rx, by_norm)
    return rx, by_norm


def _scan_lines(lines: Iterable[Tuple[int, str]], terms: list, label: str) -> List[dict]:
    rx, by_norm = _matcher(terms)
    hits: List[dict] = []
    if rx is None:
        return hits
    for line_no, line in lines:
        for m in rx.finditer(line):
            entry = by_norm.get(_norm(m.group(0)))
            if entry is None:
                continue
            hits.append({"term": entry["term"], "tier": entry["tier"], "label": label,
                         "line_no": line_no, "line": line})
    return hits


def scan_text(text: str, terms: list, label: str = "") -> List[dict]:
    """Whole text, so a phrase may span a line break; line_no is where the match starts."""
    rx, by_norm = _matcher(terms)
    hits: List[dict] = []
    if rx is None or not text:
        return hits
    lines = text.split("\n")
    starts = []
    pos = 0
    for ln in lines:
        starts.append(pos)
        pos += len(ln) + 1
    for m in rx.finditer(text):
        entry = by_norm.get(_norm(m.group(0)))
        if entry is None:
            continue
        idx = bisect.bisect_right(starts, m.start()) - 1
        hits.append({"term": entry["term"], "tier": entry["tier"], "label": label,
                     "line_no": idx + 1, "line": lines[idx]})
    return hits


def scan_items(items: list, terms: list) -> List[dict]:
    hits: List[dict] = []
    for label, text in items:
        hits.extend(scan_text(text or "", terms, label))
    return hits


def scan_commit_msg(msg_path, terms: list) -> List[dict]:
    text = Path(msg_path).read_text(encoding="utf-8", errors="replace")
    lines = [(i + 1, ln) for i, ln in enumerate(text.split("\n")) if not ln.startswith("#")]
    return _scan_lines(lines, terms, "commit-msg")


# ---------------------------------------------------------------------------------------------
# git plumbing
# ---------------------------------------------------------------------------------------------

def _git(repo: Path, *args: str, stdin: Optional[str] = None) -> str:
    try:
        proc = subprocess.run(["git", "-c", "core.quotepath=off", "-C", str(repo)] + list(args),
                              capture_output=True, input=stdin)
    except OSError as exc:
        raise ScanError("git could not run: %s" % exc.__class__.__name__)
    if proc.returncode != 0:
        raise ScanError("git %s exited %d" % (args[0], proc.returncode))
    return proc.stdout.decode("utf-8", errors="replace")


_HUNK_RX = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _added_lines(diff: str) -> Dict[str, List[Tuple[int, str]]]:
    """Map path -> [(new line number, text)] for every added line in a unified diff."""
    out: Dict[str, List[Tuple[int, str]]] = {}
    path = ""
    old_left = new_left = 0
    new_no = 0
    for line in diff.split("\n"):
        if old_left > 0 or new_left > 0:
            if line.startswith("+"):
                out.setdefault(path, []).append((new_no, line[1:]))
                new_no += 1
                new_left -= 1
                continue
            if line.startswith("-"):
                old_left -= 1
                continue
            if line.startswith(" ") or line == "":
                new_no += 1
                new_left -= 1
                old_left -= 1
                continue
            if line.startswith("\\"):
                continue
            old_left = new_left = 0
        if line.startswith("diff --git ") or line.startswith("diff --cc ") \
                or line.startswith("diff --combined "):
            path = ""
            continue
        if line.startswith("+++ "):
            p = line[4:]
            if p.startswith("b/"):
                p = p[2:]
            path = p if p != "/dev/null" else path
            continue
        if line.startswith("--- "):
            p = line[4:]
            if p != "/dev/null" and not path:
                path = p[2:] if p.startswith("a/") else p
            continue
        m = _HUNK_RX.match(line)
        if m:
            old_left = int(m.group(1)) if m.group(1) is not None else 1
            new_no = int(m.group(2))
            new_left = int(m.group(3)) if m.group(3) is not None else 1
    return out


def _scan_log(repo: Path, rev_args: List[str], terms: list) -> List[dict]:
    """Scan every commit selected by rev_args: messages, added lines (merge diffs too), paths."""
    hits: List[dict] = []
    log = _git(repo, "log", "-p", "-m", "--no-color", "--no-ext-diff", "--no-renames",
               "--no-textconv", "--format=%x1e%H%x1f%B%x1f", *rev_args)
    for chunk in log.split("\x1e"):
        if not chunk.strip():
            continue
        parts = chunk.split("\x1f", 2)
        if len(parts) < 3:
            continue
        sha, msg, diff = parts[0].strip(), parts[1], parts[2]
        short = sha[:12]
        hits.extend(scan_text(msg, terms, "commit %s message" % short))
        for p, lines in _added_lines(diff).items():
            hits.extend(_scan_lines(lines, terms, "commit %s %s" % (short, p)))
    names = _git(repo, "log", "-m", "--no-renames", "--name-only", "--format=%x1e%H", *rev_args)
    for chunk in names.split("\x1e"):
        rows = [r for r in chunk.split("\n") if r.strip()]
        if not rows:
            continue
        short = rows[0].strip()[:12]
        for p in rows[1:]:
            hits.extend(_scan_lines([(0, p)], terms, "commit %s path" % short))
    return hits


def _is_zero(sha: str) -> bool:
    return bool(ZERO_SHA_RX.match(sha or ""))


def _object_exists(repo: Path, sha: str) -> bool:
    try:
        _git(repo, "cat-file", "-e", sha + "^{commit}")
        return True
    except ScanError:
        return False


def _tag_annotation(repo: Path, sha: str) -> Optional[str]:
    try:
        kind = _git(repo, "cat-file", "-t", sha).strip()
    except ScanError:
        return None
    if kind != "tag":
        return None
    body = _git(repo, "cat-file", "-p", sha)
    _, _, message = body.partition("\n\n")
    return message


def _dedupe(hits: List[dict]) -> List[dict]:
    seen = set()
    out = []
    for h in hits:
        key = (h["term"].lower(), h["label"], h["line_no"], h["line"])
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


def scan_push(repo: Path, stdin_text: str, terms: list) -> List[dict]:
    hits: List[dict] = []
    for raw in (stdin_text or "").splitlines():
        fields = raw.split()
        if len(fields) < 4:
            continue
        local_ref, local_sha, remote_ref, remote_sha = fields[:4]
        if _is_zero(local_sha):
            continue
        hits.extend(_scan_lines([(0, local_ref)], terms, "local ref"))
        hits.extend(_scan_lines([(0, remote_ref)], terms, "remote ref"))
        if local_ref.startswith("refs/tags/") or remote_ref.startswith("refs/tags/"):
            note = _tag_annotation(repo, local_sha)
            if note:
                hits.extend(scan_text(note, terms, "tag annotation"))
        if not _is_zero(remote_sha) and _object_exists(repo, remote_sha):
            rev_args = ["%s..%s" % (remote_sha, local_sha)]
        else:
            rev_args = [local_sha, "--not", "--remotes"]
        hits.extend(_scan_log(repo, rev_args, terms))
    return _dedupe(hits)


def scan_history(repo: Path, terms: list) -> List[dict]:
    hits: List[dict] = []
    refs = _git(repo, "for-each-ref", "--format=%(refname)%1f%(objecttype)%1f%(objectname)")
    for row in refs.splitlines():
        parts = row.split("\x1f")
        if len(parts) < 3:
            continue
        name, kind, sha = parts
        hits.extend(_scan_lines([(0, name)], terms, "ref"))
        if kind == "tag":
            note = _tag_annotation(repo, sha)
            if note:
                hits.extend(scan_text(note, terms, "tag annotation"))
    if refs.strip():
        hits.extend(_scan_log(repo, ["--all"], terms))
    return _dedupe(hits)


def scan_staged(repo: Path, terms: list) -> List[dict]:
    hits: List[dict] = []
    diff = _git(repo, "diff", "--cached", "--no-color", "--no-ext-diff", "--no-renames",
                "--no-textconv", "-U0")
    for p, lines in _added_lines(diff).items():
        hits.extend(_scan_lines(lines, terms, "staged %s" % p))
    names = _git(repo, "diff", "--cached", "--name-only", "--no-renames")
    for p in names.splitlines():
        if p.strip():
            hits.extend(_scan_lines([(0, p)], terms, "staged path"))
    return _dedupe(hits)


def scan_tree(repo: Path, terms: list) -> List[dict]:
    hits: List[dict] = []
    listing = _git(repo, "ls-files", "-z")
    for p in listing.split("\0"):
        if not p:
            continue
        hits.extend(_scan_lines([(0, p)], terms, "path"))
        fp = Path(repo) / p
        try:
            data = fp.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue
        hits.extend(_scan_lines(
            [(i + 1, ln) for i, ln in enumerate(data.decode("utf-8", errors="replace").split("\n"))],
            terms, p))
    return _dedupe(hits)


# ---------------------------------------------------------------------------------------------
# reporting and entry points
# ---------------------------------------------------------------------------------------------

def _redactor(hits: List[dict]):
    block = sorted({_norm(h["term"]) for h in hits if h["tier"] == "block"}, key=len, reverse=True)
    if not block:
        return lambda s: s
    body = "|".join(r"\s+".join(re.escape(w) for w in n.split(" ")) for n in block)
    rx = re.compile(r"(?<!%s)(?:%s)(?!%s)" % (_WORD, body, _WORD), re.IGNORECASE)
    return lambda s: rx.sub("<%s>" % _COMMENT_SAFE, s)


def report(hits: List[dict]) -> int:
    red = _redactor(hits)
    blocked = False
    for h in hits:
        where = "%s:%s" % (red(h.get("label", "")), h.get("line_no", 0))
        if h["tier"] == "block":
            blocked = True
            print("BLOCK %s: <%s>" % (where, _COMMENT_SAFE))
        else:
            print("WARN %s: %s: %s" % (where, h["term"], red(h.get("line", "").strip())))
    return 1 if blocked else 0


def _visibility(cfg: dict) -> str:
    return str((cfg or {}).get("visibility", "private")).lower()


def hook_main(name: str, argv: list, stdin_text: str, repo: Path, cfg: dict) -> int:
    if _visibility(cfg) != "public":
        return 0  # the name scan protects public repos only (R3); private hooks stay silent
    path = default_list_path(cfg)
    terms = load_list(path)
    if not terms:
        print("BLOCK deny-term list missing or empty (%s); public repo, failing closed. "
              "Rebuild it, then retry." % path)
        return 6
    try:
        if name == "pre-commit":
            hits = scan_staged(repo, terms)
        elif name == "commit-msg":
            if not argv:
                print("BLOCK commit-msg hook called without a message file")
                return 6
            hits = scan_commit_msg(argv[0], terms)
        elif name == "pre-push":
            hits = scan_push(repo, stdin_text, terms)
        else:
            print("BLOCK unknown hook %s" % name)
            return 6
    except (ScanError, OSError) as exc:
        print("BLOCK deny-term scan could not run (%s); failing closed" % exc)
        return 6
    return report(hits)


def scan_deny_main(repo: Path, cfg: dict, history: bool) -> int:
    path = default_list_path(cfg)
    terms = load_list(path)
    if not terms:
        print("BLOCK deny-term list missing or empty (%s)" % path)
        return 6
    try:
        hits = scan_history(repo, terms) if history else scan_tree(repo, terms)
    except ScanError as exc:
        print("BLOCK deny-term scan could not run (%s); failing closed" % exc)
        return 6
    rc = report(hits)
    if not hits:
        print("scan-deny: clean (%d terms)" % len(terms))
    return rc
