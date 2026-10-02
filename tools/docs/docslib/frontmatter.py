"""Doc frontmatter: a restricted YAML grammar our stdlib parser handles (contract C-a, v2 7.1).

Grammar:
- `key: value` where value is a bare token containing none of `:` `#` `[` `,` `"`, or a
  double-quoted string whose only escapes are `\\"` and `\\\\`;
- inline lists `["a", "b"]` of double-quoted strings (globs must be quoted);
- one nested map, `verified:`, with indented `at`, `doc_hash`, `covers_hash`, `by`.
Anything else raises FrontmatterError("unsupported frontmatter ...").
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

VERIFIED_KEYS = ("at", "doc_hash", "covers_hash", "by")
_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(?:[ \t]+(.*))?$")
_BARE_BAD = set(':#[,"')


class FrontmatterError(ValueError):
    pass


def split(text: str) -> Tuple[Optional[str], str]:
    """Return (frontmatter block text or None, body). The block excludes the --- fences."""
    if not (text.startswith("---\n") or text.startswith("---\r\n")):
        return None, text
    lines = text.splitlines(keepends=True)
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            return "".join(lines[1:i]), "".join(lines[i + 1:])
    raise FrontmatterError("unsupported frontmatter: opening --- without a closing ---")


def body(text: str) -> str:
    return split(text)[1]


def _scalar(raw: str, lineno: int):
    raw = raw.strip()
    if raw.startswith('"'):
        if len(raw) < 2 or not raw.endswith('"'):
            raise FrontmatterError(f"unsupported frontmatter line {lineno}: unterminated string")
        inner, out, i = raw[1:-1], [], 0
        while i < len(inner):
            c = inner[i]
            if c == "\\":
                if i + 1 >= len(inner) or inner[i + 1] not in '"\\':
                    raise FrontmatterError(f"unsupported frontmatter line {lineno}: only \\\" and \\\\ escapes")
                out.append(inner[i + 1])
                i += 2
                continue
            if c == '"':
                raise FrontmatterError(f"unsupported frontmatter line {lineno}: unescaped quote")
            out.append(c)
            i += 1
        return "".join(out)
    if raw.startswith("["):
        if not raw.endswith("]"):
            raise FrontmatterError(f"unsupported frontmatter line {lineno}: unterminated list")
        inner = raw[1:-1].strip()
        if not inner:
            return []
        items, rest = [], inner
        while rest:
            m = re.match(r'^"((?:[^"\\]|\\["\\])*)"\s*(,\s*|$)', rest)
            if not m:
                raise FrontmatterError(f"unsupported frontmatter line {lineno}: list items must be "
                                       "double-quoted strings")
            items.append(_scalar('"' + m.group(1) + '"', lineno))
            rest = rest[m.end():]
            if (m.group(2) == "" and rest) or (m.group(2).strip() == "," and not rest):
                raise FrontmatterError(f"unsupported frontmatter line {lineno}: bad list")
        return items
    if not raw or any(c in _BARE_BAD for c in raw):
        raise FrontmatterError(f"unsupported frontmatter line {lineno}: bare value must not contain "
                               ': # [ , " (quote it)')
    return raw


def parse_block(block: str) -> dict:
    data: dict = {}
    lines = block.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        n = i + 1
        if not ln.strip():
            i += 1
            continue
        if ln[0] in " \t":
            raise FrontmatterError(f"unsupported frontmatter line {n}: unexpected indentation")
        m = _KEY.match(ln.rstrip())
        if not m:
            raise FrontmatterError(f"unsupported frontmatter line {n}: expected `key: value`")
        key, val = m.group(1), m.group(2)
        if key in data:
            raise FrontmatterError(f"unsupported frontmatter line {n}: duplicate key {key}")
        if val is None or not val.strip():
            if key != "verified":
                raise FrontmatterError(f"unsupported frontmatter line {n}: only `verified:` may be a map")
            sub: dict = {}
            i += 1
            while i < len(lines) and lines[i].startswith(("  ", "\t")):
                sm = _KEY.match(lines[i].strip())
                if not sm or sm.group(1) not in VERIFIED_KEYS or sm.group(2) is None:
                    raise FrontmatterError(f"unsupported frontmatter line {i + 1}: verified keys are "
                                           + ", ".join(VERIFIED_KEYS))
                if sm.group(1) in sub:
                    raise FrontmatterError(f"unsupported frontmatter line {i + 1}: duplicate key")
                sub[sm.group(1)] = _scalar(sm.group(2), i + 1)
                if isinstance(sub[sm.group(1)], list):
                    raise FrontmatterError(f"unsupported frontmatter line {i + 1}: verified values are scalars")
                i += 1
            if set(sub) != set(VERIFIED_KEYS):
                raise FrontmatterError(f"unsupported frontmatter line {n}: verified needs "
                                       + ", ".join(VERIFIED_KEYS))
            data[key] = sub
            continue
        data[key] = _scalar(val, n)
        i += 1
    return data


def parse(text: str) -> Tuple[Optional[dict], str]:
    """Return (frontmatter dict or None, body). Raises FrontmatterError on unsupported syntax."""
    block, rest = split(text)
    if block is None:
        return None, rest
    return parse_block(block), rest


def _fmt(v) -> str:
    if isinstance(v, list):
        return "[" + ", ".join(_quote(x) for x in v) + "]"
    s = str(v)
    if not s or any(c in _BARE_BAD for c in s) or s != s.strip():
        return _quote(s)
    return s


def _quote(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(data: dict) -> str:
    """Serialize a frontmatter dict (keys in insertion order) to a `---` block with a newline."""
    out = ["---"]
    for k, v in data.items():
        if k == "verified":
            out.append("verified:")
            for vk in VERIFIED_KEYS:
                out.append(f"  {vk}: {_fmt(v[vk])}")
        else:
            out.append(f"{k}: {_fmt(v)}")
    out.append("---")
    return "\n".join(out) + "\n"


def with_frontmatter(text: str, data: dict) -> str:
    """Replace (or add) the frontmatter block; the body is kept byte-identical."""
    return render(data) + body(text)
