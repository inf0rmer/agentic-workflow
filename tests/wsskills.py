"""Shared helpers for the vendored-skills suites.

The copies under core/skills are compared with a local clone of the upstream repo at the
pinned commit, named by VENDOR_UPSTREAM_CLONE. Tests that need it skip, with the reason
below, when it is unset: nothing here reaches the network.
"""

import os
import re
import subprocess
import unittest
from pathlib import Path

from wslib import ROOT

SK = ROOT / "core" / "skills"
PIN = "d81f3a183412e71a5b1e84ca21bc1a35eea03a60"
SOURCE = "https://github.com/mattpocock/skills"
VENDOR_CHECK = ROOT / "scripts" / "vendor-check"
CLONE_REASON = "set VENDOR_UPSTREAM_CLONE to a local clone of mattpocock/skills at the pin"

SKILLS = {
    "grilling": ("skills/productivity/grilling", "as-is"),
    "writing-for-agents": ("skills/productivity/writing-for-agents", "as-is"),
    "handoff": ("skills/productivity/handoff", "patched"),
    "diagnosing-bugs": ("skills/engineering/diagnosing-bugs", "patched"),
    "tdd": ("skills/engineering/tdd", "patched"),
}
ALL_DIRS = sorted(SKILLS)
FILES = {
    "grilling": {"SKILL.md", "LICENSE", "agents/openai.yaml"},
    "writing-for-agents": {"SKILL.md", "LICENSE", "agents/openai.yaml", "SKILL-MECHANICS.md"},
    "handoff": {"SKILL.md", "LICENSE", "agents/openai.yaml"},
    "diagnosing-bugs": {"SKILL.md", "LICENSE", "agents/openai.yaml", "scripts/hitl-loop.template.sh"},
    "tdd": {"SKILL.md", "LICENSE", "agents/openai.yaml", "tests.md", "mocking.md"},
}


def note_for(d, sha=PIN):
    path, status = SKILLS[d]
    tail = "copied as-is" if status == "as-is" else \
        "modified by agentic-workflow (core/skills/patches/%s.patch)" % d
    return "<!-- vendored from %s %s at %s; license: MIT (see LICENSE); %s -->" % (SOURCE, path, sha, tail)


def split_frontmatter(text):
    """(frontmatter lines including both --- lines, the rest as lines), or (None, lines)."""
    lines = text.split("\n")
    if lines and lines[0] == "---":
        for i in range(1, len(lines)):
            if lines[i] == "---":
                return lines[:i + 1], lines[i + 1:]
    return None, lines


def frontmatter_value(text, key):
    fm, _ = split_frontmatter(text)
    for line in fm or []:
        m = re.match(r"^%s:\s*(.*)$" % re.escape(key), line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return None


def strip_note(text):
    """SKILL.md text with the one provenance line after the frontmatter removed."""
    fm, rest = split_frontmatter(text)
    if fm is not None and rest and rest[0].startswith("<!-- vendored from "):
        return "\n".join(fm + rest[1:])
    return text


def insert_note(text, note):
    fm, rest = split_frontmatter(text)
    return "\n".join(fm + [note] + rest)


def files_under(d):
    base = Path(d)
    return {str(p.relative_to(base)) for p in base.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}


def clone_or_skip(case):
    c = os.environ.get("VENDOR_UPSTREAM_CLONE")
    if not c:
        case.skipTest(CLONE_REASON)
    return Path(c)


def git_bytes(repo, *args):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=60)
    if p.returncode != 0:
        raise AssertionError("harness: git %s failed: %s" % (" ".join(args), p.stderr.decode()))
    return p.stdout


def upstream_files(clone, upstream_path, sha=PIN):
    out = git_bytes(clone, "ls-tree", "-r", "--name-only", sha, "--", upstream_path).decode()
    return sorted(l[len(upstream_path) + 1:] for l in out.splitlines() if l)


def upstream_file(clone, upstream_path, f, sha=PIN):
    return git_bytes(clone, "show", "%s:%s/%s" % (sha, upstream_path, f))
