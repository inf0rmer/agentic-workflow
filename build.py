#!/usr/bin/env python3
"""Render the harness-neutral core into one harness's file layout.

  ./build.py claude-code          write dist/claude-code/
  ./build.py --check claude-code  exit 1 if dist/claude-code/ is stale

core/instructions.md and core/roles/*.md use {{placeholders}} for anything that
differs between harnesses; core/team.json gives each role a description and a
model tier, and marks a read-only role, which Claude Code limits to the adapter's
read_only_tools (Codex has no tool list, so the role's own text is its only guard). core/vars.json holds the default text for the placeholders about workspaces and signing
(the team-guard flow); adapters/<harness>/adapter.json maps tiers to models, fills the
other placeholders and overrides any default. Output formats are chosen by the adapter's "harness" field.
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
# Coder ignores a SKILL.md or an AGENTS.md larger than 64 KiB.
SKILL_LIMIT = 64 * 1024
AGENTS_LIMIT = 64 * 1024


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


def build_coder_agents(adapter, team, out):
    """Coder Agents has no agent-type files: each role becomes a skill that a spawned child reads first,
    and the lead learns each role's spawn type and model from a roster appended to AGENTS.md."""
    vars_ = adapter["vars"]
    rows = []
    for name, role in team["roles"].items():
        spawn_type = adapter["read_only_spawn_type"] if role.get("access") == "read-only" else adapter["spawn_type"]
        tier = role["tier"]
        rows.append(f"| {name} | `role-{name}` | `{spawn_type}` | `{adapter['models'][tier]}` | `{adapter['efforts'][tier]}` |")
        text = (f"---\nname: role-{name}\ndescription: Role instructions for a spawned sub-agent, not for the lead. {role['description']}\n---\n"
                "You were spawned as the role below; you are not the lead.\n\n" + render(load(f"core/roles/{name}.md"), vars_, name))
        check_size(f"skills/role-{name}/SKILL.md", text, SKILL_LIMIT)
        write(os.path.join(out, "skills", f"role-{name}", "SKILL.md"), text)
    roster = ("\n### Role roster\n\n"
              "Spawn a role with `spawn_agent` using the row's type, `model_config_id` and `reasoning_effort`. "
              "The `explore` type is the read-only type: it has no file-editing tools.\n\n"
              "| Role | Skill the child reads first | `type` | `model_config_id` | `reasoning_effort` |\n|---|---|---|---|---|\n"
              + "\n".join(rows) + "\n\n"
              "Model tiers, for a role that isn't listed above:\n\n"
              "| Tier | `model_config_id` | `reasoning_effort` |\n|---|---|---|\n"
              + "\n".join(f"| {t} | `{adapter['models'][t]}` | `{adapter['efforts'][t]}` |" for t in ("strong", "standard", "fast")) + "\n")
    text = instructions(adapter) + roster
    check_size("AGENTS.md", text, AGENTS_LIMIT)
    write(os.path.join(out, "AGENTS.md"), text)


def check_size(name, text, limit):
    size = len(text.encode())
    if size > limit:
        sys.exit(f"build: {name} is {size} bytes; Coder ignores a file over {limit} (a {name.rsplit('/', 1)[-1]} limit)")


FRONTMATTER_LINE = re.compile(r"([A-Za-z][\w-]*): (.*)")
# A plain YAML scalar can't start with these indicators, and a strict parser rejects ': ' or a trailing ':' inside one.
PLAIN_BAD_START = ("`", "@", "%", "*", "&", "!", "- ", "? ", ",", "#")


def split_frontmatter(text):
    head, sep, rest = text.partition("\n---\n")
    if not text.startswith("---\n") or not sep:
        return None
    return head.split("\n")[1:], rest


def value_problem(value):
    """Why a strict YAML parser would reject this single-line value, or None."""
    if value.startswith('"'):
        try:
            json.loads(value)
        except ValueError:
            return "a double-quoted value that doesn't parse"
        return None
    if value.startswith("'"):
        if not re.fullmatch(r"'(?:[^']|'')*'", value):
            return "a single-quoted value that doesn't parse"
        return None
    if value[:1] in "|>[{":
        return "a block or flow value, which this build can't check"
    if value.startswith(PLAIN_BAD_START) or ": " in value or value.endswith(":"):
        return "a plain value a strict parser rejects; quote it"
    return None


def frontmatter_problems(text):
    """Strict-YAML problems in a SKILL.md's frontmatter, as a list of messages. Coder parses
    frontmatter strictly and silently drops a skill it can't parse."""
    parts = split_frontmatter(text)
    if parts is None:
        return ["no frontmatter"]
    problems = []
    for line in parts[0]:
        m = FRONTMATTER_LINE.fullmatch(line)
        if not m:
            problems.append(f"line {line!r} is not a single-line 'key: value'")
            continue
        why = value_problem(m.group(2)) if m.group(2) else "an empty value"
        if why:
            problems.append(f"{m.group(1)}: {why}")
    return problems


def quote_frontmatter(text):
    """Write every plain single-line frontmatter value as a double-quoted string, so strict YAML reads it
    the way the lenient parsers of other harnesses do. Already-quoted values are kept."""
    parts = split_frontmatter(text)
    if parts is None:
        return text
    lines = []
    for line in parts[0]:
        m = FRONTMATTER_LINE.fullmatch(line)
        if m and m.group(2) and m.group(2)[0] not in "\"'|>[{":
            line = f"{m.group(1)}: {json.dumps(m.group(2), ensure_ascii=False)}"
        lines.append(line)
    return "\n".join(["---"] + lines) + "\n---\n" + parts[1]


def finish_coder_agents(out):
    for dirpath, _, files in os.walk(os.path.join(out, "skills")):
        if "SKILL.md" not in files:
            continue
        path = os.path.join(dirpath, "SKILL.md")
        with open(path) as f:
            text = quote_frontmatter(f.read())
        check_size(os.path.relpath(path, out), text, SKILL_LIMIT)
        problems = frontmatter_problems(text)
        if problems:
            sys.exit(f"build: {os.path.relpath(path, out)} frontmatter: " + "; ".join(problems))
        write(path, text)


BUILDERS = {"claude-code": build_claude_code, "codex": build_codex, "coder-agents": build_coder_agents}


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
    adapter["vars"] = {**load("core/vars.json"), **adapter["vars"]}
    team = load("core/team.json")
    if os.path.exists(out):
        shutil.rmtree(out)
    BUILDERS[adapter["harness"]](adapter, team, out)
    copy_skills(adapter["vars"], out)
    if adapter["harness"] == "coder-agents":
        finish_coder_agents(out)
    # The Coder build keeps no workspaces or signing keys, so it ships no team-guard.
    for name in [] if adapter["harness"] == "coder-agents" else sorted(os.listdir(os.path.join(ROOT, "core", "bin"))):
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
