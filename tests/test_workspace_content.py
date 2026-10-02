"""Harness text, adapters and build output: what agents read is short, plain, and free of
the removed machinery, and the adapters wire in only what still exists."""

import re
import subprocess
import unittest
from pathlib import Path

from wslib import ROOT, GUARD, Sandbox

CORE = ROOT / "core"
DIST_DOCS = {"claude-code": ROOT / "dist" / "claude-code" / "CLAUDE.md",
             "codex": ROOT / "dist" / "codex" / "AGENTS.md"}
LIMIT = 16 * 1024
# Words from the removed machinery. None may appear in what agents read.
REMOVED = ["sealed", "holdout", "contract.md", "mutation-check", "hook-pretool", "jev", "Jev",
           "team-guard verify", "team-guard gate", "team-guard routing", "team-guard risk", "team-guard refs",
           "team-guard gc", "tester"]


def text(path):
    return Path(path).read_text()


def agent_text():
    """Everything an agent reads: instructions, roles, and the docs installed beside the tools."""
    # harnesses.md names the removed files it cleans up, on purpose.
    docs = [d for d in sorted((ROOT / "docs").glob("*.md")) if d.name != "harnesses.md"]
    files = [CORE / "instructions.md", *sorted((CORE / "roles").glob("*.md")), *docs]
    return [(f, text(f)) for f in files]


class ContentCase(unittest.TestCase):

    def must_contain(self, path, *needles):
        body = text(path)
        for n in needles:
            with self.subTest(file=str(Path(path).relative_to(ROOT)), needle=n):
                self.assertIn(n, body, "%s must contain %r" % (path, n))


class TestInstructions(ContentCase):

    def test_instructions_name_the_workspace_commands_and_the_repo_rule(self):
        self.must_contain(CORE / "instructions.md",
                          "team-guard workspace new", "team-guard workspace attach", "team-guard workspace-path",
                          "team-guard workspace close", "team-guard sign", "never go in a project repo")

    def test_instructions_describe_three_variants_and_the_four_always_feature_cases(self):
        body = text(CORE / "instructions.md")
        for n in ("**Quick**", "**Feature**", "**Spike**", "security", "stored data", "public interface",
                  "more than one repo"):
            self.assertIn(n, body)

    def test_rendered_instructions_fit_the_size_limit_for_both_harnesses(self):
        for name, path in DIST_DOCS.items():
            with self.subTest(dist=name):
                self.assertLessEqual(len(path.read_bytes()), LIMIT)

    def test_nothing_an_agent_reads_mentions_the_removed_machinery(self):
        for f, body in agent_text():
            for word in REMOVED:
                with self.subTest(file=str(f.relative_to(ROOT)), word=word):
                    self.assertNotIn(word, body)

    def test_close_out_step_closes_the_workspace(self):
        for path in (CORE / "instructions.md", CORE / "roles" / "product-owner.md"):
            self.must_contain(path, "team-guard workspace close", "outside the workspace")


class TestRoles(ContentCase):

    def test_there_is_no_tester_role(self):
        self.assertFalse((CORE / "roles" / "tester.md").exists())
        for harness, ext in (("claude-code", ".md"), ("codex", ".toml")):
            self.assertFalse((ROOT / "dist" / harness / "agents" / ("tester" + ext)).exists())

    def test_the_design_doc_template_is_in_the_architect_role(self):
        self.must_contain(CORE / "roles" / "architect.md",
                          "design.md", "Mermaid", "How we'll know it works", "Rollout and PR plan", "Open questions")

    def test_engineer_writes_tests_with_the_code_and_never_weakens_them(self):
        self.must_contain(CORE / "roles" / "software-engineer.md",
                          "tdd", "tests.md", "Never weaken a test", "decisions made")

    def test_each_harness_tells_the_engineer_to_load_tdd_in_its_own_way(self):
        # Implicit invocation is off for tdd, so Codex only loads it when the role mentions $tdd.
        codex = text(ROOT / "dist" / "codex" / "agents" / "software-engineer.toml")
        self.assertIn("mention `$tdd`", codex)
        claude = text(ROOT / "dist" / "claude-code" / "agents" / "software-engineer.md")
        self.assertIn("call the Skill tool with `tdd`", claude)

    def test_reviewer_runs_the_real_tools_checks_test_changes_and_writes_the_risk_line(self):
        self.must_contain(CORE / "roles" / "reviewer.md",
                          "real", "base branch", "weakened", "review-guide.md", "Merge risk:")

    def test_no_role_puts_working_files_in_a_repo(self):
        pat = re.compile(r"(commit|save|write|put)[^.\n]*(design doc|review report|notes)"
                         r"[^.\n]*(in|into|to) the (project )?repo", re.IGNORECASE)
        for role in sorted((CORE / "roles").glob("*.md")):
            with self.subTest(role=role.name):
                m = pat.search(text(role))
                self.assertIsNone(m, "%s: %r" % (role.name, m.group(0) if m else ""))

    def test_product_owner_closes_the_workspace(self):
        self.must_contain(CORE / "roles" / "product-owner.md", "team-guard workspace close")


class TestAdapters(ContentCase):

    def test_neither_adapter_ships_hook_config(self):
        self.assertFalse((ROOT / "adapters" / "claude-code" / "settings.hooks.json").exists())
        self.assertFalse((ROOT / "adapters" / "codex" / "hooks.json").exists())

    def test_codex_rules_allow_only_team_guard(self):
        rules = text(ROOT / "adapters" / "codex" / "team.rules.template")
        self.assertRegex(rules, r'prefix_rule\(pattern=\["[^"]*/team-guard"\]',
                         "a prefix_rule whose pattern is exactly the team-guard path")
        self.assertEqual(len(re.findall(r"prefix_rule\(", rules)), 2, "the plain and the home-expanded team-guard lines")
        for word in ("mutation-check", "ask-jev"):
            self.assertNotIn(word, rules)

    def test_adapters_define_only_placeholders_the_core_uses(self):
        import json
        used = set()
        for f in [CORE / "instructions.md", *(CORE / "roles").glob("*.md"), *(CORE / "skills").rglob("SKILL.md")]:
            used |= set(re.findall(r"\{\{([a-z_]+)\}\}", text(f)))
        for harness in ("claude-code", "codex"):
            with self.subTest(harness=harness):
                data = json.loads(text(ROOT / "adapters" / harness / "adapter.json"))
                self.assertEqual(set(data["vars"]) - used, set())


class TestBuild(ContentCase):

    def test_build_is_up_to_date(self):
        for adapter in ("claude-code", "codex"):
            with self.subTest(adapter=adapter):
                p = subprocess.run([str(ROOT / "build.py"), "--check", adapter], cwd=str(ROOT),
                                   capture_output=True, text=True, timeout=60)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn("up to date", p.stdout + p.stderr)

    def test_dist_has_no_unresolved_placeholders(self):
        files = [f for f in (ROOT / "dist").rglob("*") if f.is_file()]
        self.assertTrue(files)
        for f in files:
            try:
                body = f.read_text()
            except UnicodeDecodeError:
                continue
            with self.subTest(file=str(f.relative_to(ROOT))):
                self.assertIsNone(re.search(r"\{\{[a-z_]+\}\}", body))

    def test_the_build_refuses_instructions_over_the_size_limit(self):
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="tg-build-"))
        try:
            for part in ("core", "adapters"):
                shutil.copytree(ROOT / part, tmp / part)
            shutil.copy(ROOT / "build.py", tmp / "build.py")
            with open(tmp / "core" / "instructions.md", "a") as f:
                f.write("\nfiller line\n" * 2000)
            p = subprocess.run([str(tmp / "build.py"), "--check", "codex"], cwd=str(tmp),
                               capture_output=True, text=True, timeout=60)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("limit", p.stdout + p.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_codex_agent_files_parse(self):
        import tomllib
        files = sorted((ROOT / "dist" / "codex" / "agents").glob("*.toml"))
        self.assertEqual(len(files), 6)
        for f in files:
            with self.subTest(file=f.name):
                data = tomllib.loads(text(f))
                self.assertTrue(data["developer_instructions"].strip())

    def test_the_docs_describe_the_workspace_commands(self):
        body = text(ROOT / "README.md") + text(ROOT / "docs" / "workspaces.md")
        for n in ("workspace new", "workspace attach", "workspace-path", "workspace close", "team-guard sign",
                  "TEAM_WORKSPACE_ROOT", "repos/"):
            with self.subTest(needle=n):
                self.assertIn(n, body)


class TestSuite(ContentCase):

    def test_run_sh_runs_the_workspace_suites(self):
        self.assertIn("test_workspace_", text(ROOT / "tests" / "run.sh"))

    def test_sandbox_is_offline_and_temp_only(self):
        sb = Sandbox()
        try:
            for var in ("TEAM_WORKSPACE_ROOT", "TEAM_ARCHIVE_ROOT", "HOME"):
                self.assertTrue(sb.env[var].startswith(str(sb.tmp) + "/"), var)
            self.assertNotEqual(subprocess.run(["git", "-C", str(sb.tmp), "rev-parse"], capture_output=True).returncode, 0,
                                "the sandbox must not sit inside a git repo")
        finally:
            sb.cleanup()


class TestNoBackgroundWork(ContentCase):

    def test_guard_source_has_no_scheduling(self):
        body = text(GUARD)
        for word in ("crontab", "launchctl", "systemctl", "nohup", "preexec_fn", "os.setsid", "os.fork(",
                     "daemonize", "daemonise", "DaemonContext", "double fork", "start_new_session"):
            with self.subTest(word=word):
                self.assertNotIn(word, body)


if __name__ == "__main__":
    unittest.main()
