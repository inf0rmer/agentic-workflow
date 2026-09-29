#!/usr/bin/env python3
"""Render the harness-neutral core into one harness's file layout.

  ./build.py claude-code          write dist/claude-code/
  ./build.py --check claude-code  exit 1 if dist/claude-code/ is stale

core/instructions.md and core/roles/*.md use {{placeholders}} for anything that
differs between harnesses; core/team.json gives each role a description and a
model tier. adapters/<harness>/adapter.json maps tiers to models and fills the
placeholders. Output formats are chosen by the adapter's "harness" field.
"""
import argparse
import filecmp
import json
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))


def load(path):
    with open(os.path.join(ROOT, path)) as f:
        return json.load(f) if path.endswith(".json") else f.read()


def render(text, vars_, where):
    def sub(m):
        if m.group(1) not in vars_:
            sys.exit(f"build: {where} uses {{{{{m.group(1)}}}}}, which the adapter doesn't define")
        return vars_[m.group(1)]
    return re.sub(r"\{\{([a-z_]+)\}\}", sub, text)


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


def build_claude_code(adapter, team, out):
    vars_ = adapter["vars"]
    write(os.path.join(out, "CLAUDE.md"),
          render(load("core/instructions.md"), vars_, "instructions") + adapter.get("instructions_footer", ""))
    for name, role in team["roles"].items():
        frontmatter = f"---\nname: {name}\nmodel: {adapter['models'][role['tier']]}\ndescription: {role['description']}\n---\n"
        write(os.path.join(out, "agents", f"{name}.md"), frontmatter + render(load(f"core/roles/{name}.md"), vars_, name))


BUILDERS = {"claude-code": build_claude_code}


def build(harness, out):
    adapter = load(f"adapters/{harness}/adapter.json")
    team = load("core/team.json")
    if os.path.exists(out):
        shutil.rmtree(out)
    BUILDERS[adapter["harness"]](adapter, team, out)
    for name in sorted(os.listdir(os.path.join(ROOT, "core", "bin"))):
        src = os.path.join(ROOT, "core", "bin", name)
        dst = os.path.join(out, "bin", name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)


def same_tree(a, b):
    cmp = filecmp.dircmp(a, b)
    if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
        return False
    # dircmp's shallow compare trusts size+mtime; compare contents for real.
    for name in cmp.common_files:
        if not filecmp.cmp(os.path.join(a, name), os.path.join(b, name), shallow=False):
            return False
    return all(same_tree(os.path.join(a, d), os.path.join(b, d)) for d in cmp.common_dirs)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("harness", choices=sorted(os.listdir(os.path.join(ROOT, "adapters"))))
    p.add_argument("--check", action="store_true", help="verify dist/ is up to date instead of writing it")
    a = p.parse_args()
    dist = os.path.join(ROOT, "dist", a.harness)
    if a.check:
        with tempfile.TemporaryDirectory() as tmp:
            build(a.harness, tmp)
            if not os.path.isdir(dist) or not same_tree(tmp, dist):
                sys.exit(f"build: dist/{a.harness} is stale; run ./build.py {a.harness}")
        print(f"dist/{a.harness} is up to date")
        return
    build(a.harness, dist)
    print(f"wrote dist/{a.harness}")


if __name__ == "__main__":
    main()
