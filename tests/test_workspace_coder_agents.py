"""The coder-agents harness: roles become skills, the lead's roster says how to spawn them,
frontmatter survives Coder's strict YAML parser, and install.sh fills in the deployment's model IDs.

Builds go to a temp dir, so the build tests don't depend on dist/ being current.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_workspace_installers import InstallCase
from wslib import ROOT

sys.path.insert(0, str(ROOT))
import build  # noqa: E402

KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
TEAM = json.loads((ROOT / "core" / "team.json").read_text())["roles"]
ADAPTER = json.loads((ROOT / "adapters" / "coder-agents" / "adapter.json").read_text())
PLACEHOLDER = {"strong": "REPLACE-WITH-MODEL-CONFIG-ID-STRONG",
               "standard": "REPLACE-WITH-MODEL-CONFIG-ID-STANDARD",
               "fast": "REPLACE-WITH-MODEL-CONFIG-ID-FAST"}
NOT_LEAD = "You were spawned as the role below; you are not the lead."


def frontmatter_of(text):
    head = text.split("\n---\n", 1)[0]
    assert head.startswith("---\n"), "no frontmatter"
    return dict(line.split(": ", 1) for line in head.splitlines()[1:])


class BuildCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        build.build("coder-agents", cls.tmp.name)
        cls.out = Path(cls.tmp.name)
        cls.agents_md = (cls.out / "AGENTS.md").read_text()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class TestRoleSkills(BuildCase):

    def test_every_role_renders_to_a_skill_named_for_its_directory(self):
        for role in TEAM:
            with self.subTest(role=role):
                path = self.out / "skills" / f"role-{role}" / "SKILL.md"
                text = path.read_text()
                fm = frontmatter_of(text)
                self.assertEqual(json.loads(fm["name"]), f"role-{role}")
                self.assertRegex(json.loads(fm["name"]), KEBAB)
                body = text.split("\n---\n", 1)[1].lstrip("\n")
                self.assertEqual(body.splitlines()[0], NOT_LEAD)

    def test_the_instructions_open_with_the_child_guard(self):
        first = self.agents_md.split("\n\n", 1)[0]
        self.assertIn("role-", first)
        self.assertIn("you are that role", first)
        self.assertIn("not the lead", first)

    def test_the_instructions_say_a_project_cannot_override_a_role(self):
        self.assertIn("silently dropped", self.agents_md)
        self.assertNotIn("A project can override one", self.agents_md)

    def test_the_design_step_posts_the_doc_in_chat_and_never_mentions_crit(self):
        step = next(l for l in self.agents_md.splitlines() if l.startswith("3. **Approval.**"))
        self.assertIn("Post the doc in the chat", step)
        self.assertIn("`approve design`", step)
        texts = {"AGENTS.md": self.agents_md}
        texts.update({str(p.relative_to(self.out)): p.read_text() for p in self.out.glob("skills/role-*/SKILL.md")})
        for name, text in texts.items():
            with self.subTest(file=name):
                self.assertIsNone(re.search(r"\bcrit\b", text, re.IGNORECASE))

    def test_the_ship_step_says_sign_skips_in_a_sandbox(self):
        self.assertIn("with `TEAM_SANDBOX=1` it skips", self.agents_md)


class TestPersistence(BuildCase):
    """A Coder workspace is erased on stop, so the Coder build keeps work under ~/work and pushes early."""

    def lead_text(self):
        return " ".join(self.agents_md.split())

    def test_work_lives_under_the_persistent_directory(self):
        text = self.lead_text()
        self.assertIn("Only `~/work` (`/home/coder/work`) survives a workspace stop", text)
        self.assertIn("Clone every repo into `~/work`", text)
        self.assertIn("`~/work/.agentic-workflow`", text)

    def test_a_draft_pr_opens_after_the_first_commit_and_every_commit_is_pushed(self):
        text = self.lead_text()
        self.assertIn("`gh pr create --draft`", text)
        self.assertIn("Push after every commit (a security fix stays unpushed", text)

    def test_the_human_gate_is_marking_the_draft_ready(self):
        gate = next(l for l in self.agents_md.splitlines() if l.startswith("7. **Human gate.**"))
        self.assertIn("review the draft PR", gate)
        self.assertIn("Mark it ready only after they approve", gate)
        ship = next(l for l in self.agents_md.splitlines() if l.startswith("8. **Ship.**"))
        self.assertIn("`gh pr ready`", ship)
        self.assertNotIn("Nothing is pushed without approval", self.agents_md)
        self.assertNotIn("the code review before any push", self.agents_md)
        self.assertNotIn("The first push needs the human's approval", self.agents_md)

    def test_the_other_gates_remain(self):
        text = self.lead_text()
        for gate in ("the variant, the design doc", "any problem you cannot settle alone"):
            self.assertIn(gate, text)

    def test_the_other_harnesses_keep_the_push_gate(self):
        for harness in ("claude-code", "codex"):
            with self.subTest(harness=harness), tempfile.TemporaryDirectory() as tmp:
                build.build(harness, tmp)
                text = next(p for p in (Path(tmp) / "CLAUDE.md", Path(tmp) / "AGENTS.md") if p.exists()).read_text()
                self.assertIn("Nothing is pushed without approval", text)
                self.assertNotIn("draft PR", text)
                self.assertNotIn("~/work", text)

    def test_team_guard_creates_workspaces_under_the_configured_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "TEAM_WORKSPACE_ROOT": tmp, "HOME": tmp}
            out = subprocess.run([str(ROOT / "core" / "bin" / "team-guard"), "workspace", "new", "persist-check"],
                                 env=env, capture_output=True, text=True, cwd=tmp)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertTrue((Path(tmp) / "persist-check" / "workspace.json").exists())


class TestRoster(BuildCase):

    def rows(self):
        found = {}
        for line in self.agents_md.splitlines():
            m = re.match(r"\| (\S+) \| `(role-[^`]+)` \| `(\w+)` \| `([^`]+)` \| `(\w+)` \|$", line)
            if m:
                found[m.group(1)] = m.groups()[1:]
        return found

    def test_the_roster_lists_every_role_with_type_model_and_effort(self):
        rows = self.rows()
        self.assertEqual(set(rows), set(TEAM))
        for role, spec in TEAM.items():
            with self.subTest(role=role):
                skill, spawn_type, model, effort = rows[role]
                self.assertEqual(skill, f"role-{role}")
                self.assertEqual(spawn_type, "explore" if role == "security-reviewer" else "general")
                self.assertEqual(model, PLACEHOLDER[spec["tier"]])
                self.assertEqual(effort, {"strong": "medium", "standard": "medium", "fast": "low"}[spec["tier"]])

    def test_the_roster_lists_the_three_tiers_so_every_model_id_is_filled(self):
        for tier, placeholder in PLACEHOLDER.items():
            self.assertRegex(self.agents_md, rf"\| {tier} \| `{placeholder}` \| `\w+` \|")

    def test_only_the_read_only_role_is_explore(self):
        read_only = {r for r, s in TEAM.items() if s.get("access") == "read-only"}
        self.assertEqual(read_only, {"security-reviewer"})
        self.assertEqual({r for r, v in self.rows().items() if v[1] == "explore"}, read_only)


class TestStrictFrontmatter(BuildCase):

    def test_every_built_skill_passes_the_strict_check(self):
        skills = sorted(self.out.glob("skills/*/SKILL.md"))
        self.assertGreater(len(skills), 10)
        for path in skills:
            with self.subTest(skill=path.parent.name):
                self.assertEqual(build.frontmatter_problems(path.read_text()), [])

    def test_the_vendored_tdd_description_is_quoted_so_it_parses(self):
        fm = frontmatter_of((self.out / "skills" / "tdd" / "SKILL.md").read_text())
        self.assertTrue(fm["description"].startswith('"'))

    def test_the_check_rejects_values_a_strict_parser_rejects(self):
        for bad in ("a: b", "`code` first", "@mention", "- item", "ends with:", "%percent", "*alias", "&anchor", "!tag"):
            with self.subTest(value=bad):
                text = f"---\nname: x\ndescription: {bad}\n---\nbody\n"
                self.assertTrue(build.frontmatter_problems(text), bad)

    def test_the_check_accepts_quoted_and_plain_safe_values(self):
        text = '---\nname: "x"\ndescription: \'it\'\'s: fine\'\nargument-hint: plain words, with commas\n---\nbody\n'
        self.assertEqual(build.frontmatter_problems(text), [])

    def test_the_check_rejects_a_malformed_quoted_value_and_missing_frontmatter(self):
        self.assertTrue(build.frontmatter_problems('---\nname: "unterminated\n---\nbody\n'))
        self.assertTrue(build.frontmatter_problems("no frontmatter\n"))

    def test_quoting_makes_a_hostile_value_pass(self):
        text = "---\nname: x\ndescription: Loop: write `a` test\n---\nbody\n"
        self.assertTrue(build.frontmatter_problems(text))
        self.assertEqual(build.frontmatter_problems(build.quote_frontmatter(text)), [])

    def test_quoting_keeps_already_quoted_values(self):
        text = '---\nname: x\ndescription: "already: quoted"\n---\nbody\n'
        self.assertEqual(build.quote_frontmatter(text), text.replace("name: x", 'name: "x"'))


class TestSizeGuard(unittest.TestCase):

    def build_with(self, **limits):
        with tempfile.TemporaryDirectory() as out, mock.patch.multiple(build, **limits):
            build.build("coder-agents", out)

    def test_the_build_fails_on_an_oversized_skill(self):
        with self.assertRaises(SystemExit) as cm:
            self.build_with(SKILL_LIMIT=200)
        self.assertIn("SKILL.md", str(cm.exception))

    def test_the_build_fails_on_an_oversized_vendored_skill_and_names_it(self):
        # Above every role skill, below the largest vendored one.
        sizes = {p.parent.name: p.stat().st_size for p in (ROOT / "core" / "skills").glob("*/SKILL.md")}
        limit = max(sizes.values()) - 1
        roles = [len(build.render(build.load(f"core/roles/{r}.md"), ADAPTER["vars"], r).encode()) + 400 for r in TEAM]
        self.assertLess(max(roles), limit, "harness: the limit must sit between role and vendored skill sizes")
        with self.assertRaises(SystemExit) as cm:
            self.build_with(SKILL_LIMIT=limit)
        self.assertIn(max(sizes, key=sizes.get), str(cm.exception))

    def test_the_build_fails_on_oversized_instructions(self):
        with self.assertRaises(SystemExit) as cm:
            self.build_with(AGENTS_LIMIT=200)
        self.assertIn("AGENTS.md", str(cm.exception))


class TestInstall(InstallCase):

    def setUp(self):
        super().setUp()
        shutil.copytree(ROOT / "docs", self.repo / "docs", symlinks=True)
        # An image build or workspace may export these; each test sets exactly the ones it means to.
        for var in self.IDS:
            self.sb.env.pop(var, None)

    IDS = {"CODER_MODEL_STRONG": "11111111-aaaa", "CODER_MODEL_STANDARD": "22222222-bbbb",
           "CODER_MODEL_FAST": "33333333-cccc"}

    def run_install(self, **env):
        e = {"HOME": str(self.home), **env}
        return self.sb.run(["bash", str(self.repo / "adapters" / "coder-agents" / "install.sh")],
                           cwd=self.sb.scratch, env=e)

    def test_install_puts_everything_under_dot_coder_and_fills_the_model_ids(self):
        r = self.run_install(**self.IDS)
        self.ok(r, "install coder-agents")
        coder = self.home / ".coder"
        agents = (coder / "AGENTS.md").read_text()
        self.assertNotIn("REPLACE-WITH", agents)
        for tier, var in (("strong", "CODER_MODEL_STRONG"), ("standard", "CODER_MODEL_STANDARD"), ("fast", "CODER_MODEL_FAST")):
            self.assertIn(self.IDS[var], agents)
        for skill in ("role-architect", "role-security-reviewer", "tdd", "grilling"):
            self.assertTrue((coder / "skills" / skill / "SKILL.md").is_file(), skill)
        guard = coder / "team" / "bin" / "team-guard"
        self.assertTrue(guard.stat().st_mode & stat.S_IXUSR)
        self.assertTrue((coder / "team" / "docs" / "pr-loop.md").is_file())
        self.assertFalse((self.home / ".claude").exists())
        self.assertFalse((self.home / ".codex").exists())

    def test_install_backs_up_what_it_replaces(self):
        coder = self.home / ".coder"
        (coder / "skills" / "tdd").mkdir(parents=True)
        (coder / "skills" / "tdd" / "mine.md").write_text("my notes\n")
        (coder / "AGENTS.md").write_text("my instructions\n")
        self.ok(self.run_install(**self.IDS), "install coder-agents")
        backups = list((coder / "backups").glob("agentic-workflow-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "AGENTS.md").read_text(), "my instructions\n")
        self.assertEqual((backups[0] / "skills" / "tdd" / "mine.md").read_text(), "my notes\n")
        self.assertFalse((coder / "skills" / "tdd" / "mine.md").exists())

    def test_install_does_not_write_through_a_symlinked_agents_md(self):
        coder = self.home / ".coder"
        coder.mkdir()
        store = self.sb.tmp / "dotfiles-AGENTS.md"
        store.write_text("managed elsewhere\n")
        (coder / "AGENTS.md").symlink_to(store)
        self.ok(self.run_install(**self.IDS), "install coder-agents")
        self.assertEqual(store.read_text(), "managed elsewhere\n")
        self.assertFalse((coder / "AGENTS.md").is_symlink())
        self.assertIn(self.IDS["CODER_MODEL_FAST"], (coder / "AGENTS.md").read_text())
        backups = list((coder / "backups").glob("agentic-workflow-*"))
        self.assertEqual((backups[0] / "AGENTS.md").read_text(), "managed elsewhere\n")

    def test_install_refuses_when_a_model_id_is_unset_and_writes_nothing(self):
        for missing in self.IDS:
            with self.subTest(missing=missing):
                env = {k: v for k, v in self.IDS.items() if k != missing}
                r = self.run_install(**env)
                self.assertEqual(r.code, 1, r)
                self.assertIn(f"agentic-workflow install: {missing} is not set", r.err)
                self.assertFalse((self.home / ".coder").exists())

    def test_install_treats_an_empty_model_id_as_unset(self):
        r = self.run_install(**{**self.IDS, "CODER_MODEL_FAST": ""})
        self.assertEqual(r.code, 1, r)
        self.assertIn("CODER_MODEL_FAST is not set", r.err)

    def test_install_refuses_a_model_id_that_is_not_a_plain_identifier(self):
        r = self.run_install(**{**self.IDS, "CODER_MODEL_FAST": "a|b&c"})
        self.assertEqual(r.code, 1, r)
        self.assertIn("CODER_MODEL_FAST", r.err)
        self.assertFalse((self.home / ".coder").exists())


if __name__ == "__main__":
    unittest.main()
