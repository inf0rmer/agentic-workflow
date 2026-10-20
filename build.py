#!/usr/bin/env python3
"""Render the harness-neutral core into one harness's file layout.

  ./build.py claude-code          write dist/claude-code/
  ./build.py --check claude-code  exit 1 if dist/claude-code/ is stale

core/instructions.md and core/roles/*.md use {{placeholders}} for anything that
differs between harnesses; core/team.json gives each role a description and a
model tier, and marks a read-only role, which Claude Code limits to the adapter's
read_only_tools (Codex has no tool list, so the role's own text is its only guard). adapters/<harness>/adapter.json maps tiers to models and fills the
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
import tomllib

ROOT = os.path.dirname(os.path.abspath(__file__))
# The rendered instructions stay under half of Codex's 32 KiB limit.
INSTRUCTIONS_LIMIT = 16 * 1024


def load(path):
    with open(os.path.join(ROOT, path)) as f:
        return json.load(f) if path.endswith(".json") else f.read()


def render(text, vars_, where):
    def sub(m):
        if m.group(1) not in vars_:
            sys.exit(f"build: {where} uses {{{{{m.group(1)}}}}}, which the adapter doesn't define")
        return vars_[m.group(1)]
    return re.sub(r"\{\{([a-z_]+)\}\}", sub, text)


def instructions(adapter):
    text = render(load("core/instructions.md"), adapter["vars"], "instructions") + adapter.get("instructions_footer", "")
    size = len(text.encode())
    if size > INSTRUCTIONS_LIMIT:
        sys.exit(f"build: the rendered instructions are {size} bytes; the limit is {INSTRUCTIONS_LIMIT}")
    return text


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


def build_claude_code(adapter, team, out):
    vars_ = adapter["vars"]
    write(os.path.join(out, "CLAUDE.md"), instructions(adapter))
    for name, role in team["roles"].items():
        tools = f"tools: {adapter['read_only_tools']}\n" if role.get("access") == "read-only" else ""
        frontmatter = (f"---\nname: {name}\nmodel: {adapter['models'][role['tier']]}\n{tools}"
                       f"description: {role['description']}\n---\n")
        write(os.path.join(out, "agents", f"{name}.md"), frontmatter + render(load(f"core/roles/{name}.md"), vars_, name))


def toml_str(value):
    return json.dumps(value)  # a JSON string is a valid TOML basic string


def build_codex(adapter, team, out):
    vars_ = adapter["vars"]
    write(os.path.join(out, "AGENTS.md"), instructions(adapter))
    for name, role in team["roles"].items():
        body = render(load(f"core/roles/{name}.md"), vars_, name).strip("\n")
        if "'''" in body:
            sys.exit(f"build: {name} contains ''', which can't go in a TOML literal string")
        tier = role["tier"]
        text = (f"name = {toml_str(name)}\n"
                f"description = {toml_str(role['description'])}\n"
                f"model = {toml_str(adapter['models'][tier])}\n"
                f"model_reasoning_effort = {toml_str(adapter['efforts'][tier])}\n"
                f"developer_instructions = '''\n{body}\n'''\n")
        tomllib.loads(text)  # fail the build rather than ship a file Codex can't parse
        write(os.path.join(out, "agents", f"{name}.toml"), text)


BUILDERS = {"claude-code": build_claude_code, "codex": build_codex}


def copy_skills(vars_, out):
    skills = os.path.join(ROOT, "core", "skills")
    # Only a directory with a SKILL.md is a skill; UPSTREAM.json and patches/ are bookkeeping and never ship.
    shipped = sorted(d for d in os.listdir(skills) if os.path.isfile(os.path.join(skills, d, "SKILL.md")))
    for skill in shipped:
        for dirpath, dirs, files in os.walk(os.path.join(skills, skill)):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in files:
                if name.endswith(".pyc"):
                    continue
                src = os.path.join(dirpath, name)
                dst = os.path.join(out, "skills", os.path.relpath(src, skills))
                if name == "SKILL.md":
                    write(dst, render(load(os.path.relpath(src, ROOT)), vars_, os.path.relpath(src, ROOT)))
                else:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)


def build(harness, out):
    adapter = load(f"adapters/{harness}/adapter.json")
    team = load("core/team.json")
    if os.path.exists(out):
        shutil.rmtree(out)
    BUILDERS[adapter["harness"]](adapter, team, out)
    copy_skills(adapter["vars"], out)
    for name in sorted(os.listdir(os.path.join(ROOT, "core", "bin"))):
        src = os.path.join(ROOT, "core", "bin", name)
        if not os.path.isfile(src) or name == "__pycache__":
            continue
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
