"""Deployment settings that are never vendored: who approves, the skill's home repo and its routine
clone, the per-repo init seeds, and the merge-gate exceptions.

They live in `site.json` beside docs.py in the skill's home repo. A vendored copy (tools/docs/) has no
site.json and runs on the neutral defaults below, so the code it carries names no other repository and
no person. Only init, preflight, upgrade, consumers and the routine read the home values; all of those
run from the skill's home clone, never from a vendored copy."""

from __future__ import annotations

import json
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
SITE_FILE = "site.json"

DEFAULTS = {
    "owner": "the owner",            # who approves maps, roadmap changes and daily PRs (in messages)
    "home_clone_dir": "skill-home",  # the routine's dedicated clone, under data_home()
    "home_clone_url": None,          # required for `init --stage local --clone`
    "home_base_branch": "main",      # the routine checks the clone out at origin/<this>; the PIN trust ref
    "placement_tools": [],           # home-repo files that enforce placement (consumers report them)
    "seeds": {},                     # repo slug -> init seed (docs.json starting values)
    "seed_aliases": {},              # GitHub owner/name -> seed slug, where they differ
    "update_before_merge": [],       # repo slugs merged by update-branch instead of a strict gate
}


def load(root: Path = SKILL_ROOT) -> dict:
    out = dict(DEFAULTS)
    fp = Path(root) / SITE_FILE
    if fp.is_file():
        data = json.loads(fp.read_text(encoding="utf-8"))
        out.update({k: v for k, v in data.items() if not k.startswith("_")})
    return out


SITE = load()


def get(key: str):
    return SITE[key]
