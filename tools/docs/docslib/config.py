"""docs.json: defaults, loading, the docs universe, and the closed "loosens a rule" list.

manage-docs v2 section 4 (keys and defaults) and section 6 check 7 (with ER14's five additions and
contract RE1's inventory_exclude). Config is JSON because python3 stdlib has no YAML parser (C-a).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import List, Optional

from docslib import globs

DEFAULTS = {
    "visibility": "private",
    "layout_version": 1,
    "base_branch": "main",
    "tier": "full",
    "caps": {"docs": 40, "lines": 300, "overage": "warn"},
    "root_allow": ["README.md", "AGENTS.md", "CLAUDE.md", "CHANGELOG.md", "LICENSE", "CONTRIBUTING.md",
                   "docs.json"],
    "md_allow": ["docs/**", "work/**", "packages/*/README.md", "apps/*/README.md", ".claude/**",
                 ".agents/**", ".github/**"],
    "doc_globs": ["**/*.md", "**/*.mdx", "**/*.rst", "docs/**/*.txt", "docs/**/*.html"],
    "doc_exclude": ["**/node_modules/**", "**/fixtures/**", "**/__snapshots__/**", "**/test*/**",
                    "vendor/**", "tools/docs/**", ".docs-cache/**"],
    "frontmatter_exempt": [],
    "router": {"mode": "block"},
    "generators": [],
    "docs_map": "docs/README.md",
    "records_window_days": 30,
    "receipt_max_age_days": 30,
    "verify_budget_per_run": 10,
    "reviewer_cmd": {"claude": "codex exec", "codex": "claude -p"},
    "facts": {},
    "read_test": [],
    "deny_terms_file": None,
    "inventory_exclude": [],
    "roadmap": {},
    # X12: entry-point docs (no frontmatter, so receipt staleness never covers them). Repo-wide,
    # WARN-only signals flag one unchanged longer than entrypoint_max_age_days, or naming a dead path.
    "entrypoints": ["*.md", "docs/README.md", "apps/*/README.md", "packages/*/README.md"],
    "entrypoint_max_age_days": 180,
}

# Paths that never leave the machine in a public repo (contract section 7).
LOCAL_ONLY = ["work/", "docs/roadmap/", ".beads/"]
DECISIONS = "docs/roadmap/DECISIONS.md"


class ConfigError(ValueError):
    pass


def merged(raw: Optional[dict]) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    for k, v in (raw or {}).items():
        if k == "caps" and isinstance(v, dict):
            cfg["caps"].update(v)
        else:
            cfg[k] = v
    return cfg


def load(root: Path) -> Optional[dict]:
    """Merged config, or None when the repo has no docs.json (not enrolled)."""
    p = Path(root) / "docs.json"
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"docs.json is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("docs.json must be a JSON object")
    return merged(raw)


def excludes(cfg: dict) -> List[str]:
    """doc_exclude unioned with inventory_exclude (contract RE1)."""
    return list(cfg.get("doc_exclude", [])) + list(cfg.get("inventory_exclude", []))


def is_doc(cfg: dict, path: str) -> bool:
    return globs.any_match(cfg.get("doc_globs", []), path) and not globs.any_match(excludes(cfg), path)


def placed(cfg: dict, path: str) -> bool:
    """True when a doc path is allowed where it is (root_allow or md_allow)."""
    return globs.any_match(cfg.get("root_allow", []), path) or globs.any_match(cfg.get("md_allow", []), path)


def is_entrypoint(cfg: dict, path: str) -> bool:
    """A doc the entrypoint check watches for staleness and dead path references (X12)."""
    return globs.any_match(cfg.get("entrypoints") or [], path)


def is_public(cfg: dict) -> bool:
    return cfg.get("visibility") == "public"


def local_only(path: str) -> bool:
    return any(globs.match(p, path) for p in LOCAL_ONLY)


def is_record(path: str) -> bool:
    return path.startswith("work/")


# The repo-relative path the built-in `docs-map` generator writes. docs/README.md by default, so a
# repo that does not set `docs_map` keeps its generated map there; a repo with a hand-written
# docs/README.md points `docs_map` at another path (e.g. docs/DOCS-MAP.md) to protect the README.
DEFAULT_DOCS_MAP = "docs/README.md"


def docs_map_path(cfg: dict) -> str:
    return cfg.get("docs_map") or DEFAULT_DOCS_MAP


# The docs router fragment init writes for repos that compose AGENTS.md / CLAUDE.md from docs/agent/
# fragments: its text lands in the ROOT files, so its links are root-relative and it carries no frontmatter.
ROUTER_FRAGMENT = "docs/agent/_docs-router.md"


def link_base(path: str) -> str:
    """The path a doc's relative links resolve from: the doc itself, or the repo root for the router fragment."""
    return "AGENTS.md" if path == ROUTER_FRAGMENT else path


def frontmatter_required(cfg: dict, path: str) -> bool:
    """Hand-written docs in docs/ outside roadmap/ and generated/ carry frontmatter (7.1)."""
    if not path.startswith("docs/") or path.startswith(("docs/roadmap/", "docs/generated/")):
        return False
    # The README and the router fragment are READMEs, not ours to parse; the generated docs map
    # (wherever docs_map points it) carries a generated header, not frontmatter.
    if path in ("docs/README.md", ROUTER_FRAGMENT, docs_map_path(cfg)):
        return False
    if not path.endswith((".md", ".mdx")):
        return False
    return not globs.any_match(cfg.get("frontmatter_exempt", []), path)


# ------------------------------------------------------------------ check 7: loosening

def _added(old: list, new: list) -> list:
    return [x for x in (new or []) if x not in (old or [])]


def _removed(old: list, new: list) -> list:
    return [x for x in (old or []) if x not in (new or [])]


def loosenings(old_raw: Optional[dict], new_raw: Optional[dict]) -> List[str]:
    """The closed list of v2 section 6 check 7 (incl. ER14), comparing two docs.json objects.

    Compares RAW files (what the commit changed), with defaults filled in, so removing a key that
    restates a default still counts as a key removed.
    """
    if old_raw is None or new_raw is None:
        return []
    out: List[str] = []
    for k in old_raw:
        if k not in new_raw:
            out.append(f"key removed: {k}")
    old, new = merged(old_raw), merged(new_raw)
    for cap in ("docs", "lines"):
        if (new["caps"].get(cap) or 0) > (old["caps"].get(cap) or 0):
            out.append(f"caps.{cap} raised {old['caps'].get(cap)} -> {new['caps'].get(cap)}")
    if new["caps"].get("overage") == "warn" and old["caps"].get("overage") != "warn":
        out.append("caps.overage set to warn")
    for key in ("root_allow", "md_allow", "doc_exclude", "frontmatter_exempt", "inventory_exclude"):
        for x in _added(old.get(key), new.get(key)):
            out.append(f"{key} entry added: {x}")
    for key in ("doc_globs", "read_test"):
        for x in _removed(old.get(key), new.get(key)):
            out.append(f"{key} entry removed: {json.dumps(x, sort_keys=True)}")
    for key in ("receipt_max_age_days", "records_window_days"):
        if (new.get(key) or 0) > (old.get(key) or 0):
            out.append(f"{key} raised {old.get(key)} -> {new.get(key)}")
    if (new.get("verify_budget_per_run") or 0) < (old.get("verify_budget_per_run") or 0):
        out.append(f"verify_budget_per_run lowered {old.get('verify_budget_per_run')} -> "
                   f"{new.get('verify_budget_per_run')}")
    if new.get("visibility") == "private" and old.get("visibility") != "private":
        out.append("visibility changed to private")
    if old.get("deny_terms_file") and not new.get("deny_terms_file"):
        out.append("deny_terms_file cleared")
    # ER14 (D5)
    for x in _removed(old.get("generators"), new.get("generators")):
        out.append(f"generators entry removed: {json.dumps(x, sort_keys=True)}")
    if old.get("reviewer_cmd") != new.get("reviewer_cmd"):
        out.append("reviewer_cmd changed")
    if (old.get("router") or {}).get("mode") != (new.get("router") or {}).get("mode"):
        out.append("router.mode changed")
    if old.get("tier") == "full" and new.get("tier") == "small":
        out.append("tier lowered full -> small")
    of, nf = old.get("facts") or {}, new.get("facts") or {}
    for k in sorted(set(of) | set(nf)):
        if k in of and of.get(k) != nf.get(k):
            out.append(f"facts.{k} changed")
    return out
