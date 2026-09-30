"""Harness text, adapters, build output and suite-level checks (sections G and H,
AC-59, AC-99's source check, JEV-4)."""

import json
import re
import subprocess
import unittest
from pathlib import Path

from wslib import ROOT, GUARD, Sandbox

CORE = ROOT / "core"
DIST_DOCS = {"claude-code": ROOT / "dist" / "claude-code" / "CLAUDE.md",
             "codex": ROOT / "dist" / "codex" / "AGENTS.md"}
JARGON = ['"sealed"', '"holdout"', '"Jev"', '"mutation survivors"']


def text(path):
    return Path(path).read_text()


def section(doc, title):
    """The markdown section whose heading contains `title`, up to the next heading of the same or higher level."""
    lines = doc.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(#+)\s+(.*)$", line)
        if m and title in m.group(2):
            level = len(m.group(1))
            out = [line]
            for rest in lines[i + 1:]:
                n = re.match(r"^(#+)\s", rest)
                if n and len(n.group(1)) <= level:
                    break
                out.append(rest)
            return "\n".join(out)
    return None


def paragraphs(doc):
    return [p for p in re.split(r"\n\s*\n", doc) if p.strip()]


class ContentCase(unittest.TestCase):

    def must_contain(self, path, *needles):
        body = text(path)
        for n in needles:
            with self.subTest(file=str(Path(path).relative_to(ROOT)), needle=n):
                self.assertIn(n, body, "%s must contain %r" % (path, n))


class TestInstructions(ContentCase):

    def test_ac61_ac62_ac64_ac65_ac100_instructions(self):
        self.must_contain(CORE / "instructions.md",
                          "team-guard workspace new", "team-guard workspace attach", "team-guard workspace-path",
                          "never in a project repo", "workspace dir", "attached worktree", "ship order",
                          "team-guard workspace close", *JARGON)

    def test_ac62_worktree_first_section(self):
        # core/instructions.md carries this section as an adapter placeholder, so the
        # rendered text in each dist tree is what agents read and what is checked.
        docs = {"dist/%s" % k: text(v) for k, v in DIST_DOCS.items()}
        for name, doc in docs.items():
            with self.subTest(doc=name):
                sec = section(doc, "Worktree-first workflow")
                self.assertIsNotNone(sec, "%s has a 'Worktree-first workflow' section" % name)
                self.assertIn("attached worktree", sec)
                self.assertIn("workspace dir", sec)
                self.assertNotIn("single worktree", sec)

    def test_ac100_close_out_step(self):
        for path in (CORE / "instructions.md", CORE / "roles" / "product-owner.md"):
            self.must_contain(path, "team-guard workspace close", "outside the workspace")


class TestRoles(ContentCase):

    def test_ac63_ac64_architect(self):
        self.must_contain(CORE / "roles" / "architect.md",
                          "Cross-repo boundaries", "owner repo", "consumer repo", "single-repo", "ship order")

    def test_ac65_no_jargon_instruction(self):
        for role in ("software-engineer.md", "doc-writer.md"):
            self.must_contain(CORE / "roles" / role, *JARGON)

    def test_ac66_tester_reviewer_inputs(self):
        for role in ("tester.md", "reviewer.md"):
            self.must_contain(CORE / "roles" / role, "team-guard holdout-path", "workspace dir")

    def test_ac66_no_role_puts_artifacts_in_a_repo(self):
        pat = re.compile(r"(commit|save|write|put)[^.\n]*(spec|contract\.md|design doc|architecture doc|review report)"
                         r"[^.\n]*(in|into|to) the (project )?repo", re.IGNORECASE)
        roles = sorted((CORE / "roles").glob("*.md"))
        self.assertTrue(roles)
        for role in roles:
            with self.subTest(role=role.name):
                m = pat.search(text(role))
                self.assertIsNone(m, "%s: %r" % (role.name, m.group(0) if m else ""))

    def test_ac100_product_owner(self):
        self.must_contain(CORE / "roles" / "product-owner.md", "team-guard workspace close")

    def test_jev4_log_location_in_the_same_paragraph(self):
        for path in (CORE / "roles" / "reviewer.md", CORE / "instructions.md",
                     CORE / "skills" / "jev-decide" / "SKILL.md"):
            with self.subTest(file=str(path.relative_to(ROOT))):
                hits = [p for p in paragraphs(text(path)) if "jev-decisions.md" in p and "workspace dir" in p]
                self.assertTrue(hits, "%s: no paragraph mentions both jev-decisions.md and workspace dir" % path)


class TestAdapters(ContentCase):

    def test_ac67_claude_code(self):
        data = json.loads(text(ROOT / "adapters" / "claude-code" / "settings.hooks.json"))
        allow = data.get("permissions", {}).get("allow", [])
        self.assertTrue(any("team-guard:*" in a for a in allow), allow)
        hooks = json.dumps(data.get("hooks", {}))
        self.assertIn("team-guard hook-pretool", hooks)
        self.assertIn("team-guard hook-task", hooks)

    def test_ac67_codex(self):
        hooks = text(ROOT / "adapters" / "codex" / "hooks.json")
        rules = text(ROOT / "adapters" / "codex" / "team.rules.template")
        self.assertIn("team-guard hook-pretool", hooks)
        self.assertIn("team-guard hook-stop", hooks)
        self.assertRegex(rules, r'prefix_rule\(pattern=\["[^"]*/team-guard"\]',
                         "a prefix_rule whose pattern is exactly the team-guard path")


class TestBuildAndReadme(ContentCase):

    def test_ac68_build_up_to_date(self):
        for adapter in ("claude-code", "codex"):
            with self.subTest(adapter=adapter):
                p = subprocess.run([str(ROOT / "build.py"), "--check", adapter], cwd=str(ROOT),
                                   capture_output=True, text=True, timeout=60)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn("up to date", p.stdout + p.stderr)

    def test_ac68_no_unresolved_placeholders(self):
        files = [f for f in (ROOT / "dist").rglob("*") if f.is_file()]
        self.assertTrue(files)
        for f in files:
            try:
                body = f.read_text()
            except UnicodeDecodeError:
                continue
            with self.subTest(file=str(f.relative_to(ROOT))):
                self.assertIsNone(re.search(r"\{\{[a-z_]+\}\}", body))

    def test_ac68_dist_carries_the_new_text(self):
        for name, path in DIST_DOCS.items():
            with self.subTest(dist=name):
                body = text(path)
                for n in ("team-guard workspace new", "team-guard workspace close", "never in a project repo"):
                    self.assertIn(n, body)

    def test_ac69_readme(self):
        body = text(ROOT / "README.md")
        for n in ("workspace new", "workspace attach", "workspace-path", "workspace close", "team-guard gc",
                  "TEAM_WORKSPACE_ROOT", "repos/"):
            with self.subTest(needle=n):
                self.assertIn(n, body)
        limits = section(body, "Known limits")
        self.assertIsNotNone(limits, "README has a '## Known limits' section")
        self.assertTrue(limits.startswith("## "), limits.splitlines()[0])
        bullets = [l for l in limits.splitlines() if re.match(r"^\s*[-*]\s", l)]
        self.assertTrue(any("workspace" in b for b in bullets), bullets)


class TestSuite(ContentCase):

    def test_ac59_run_sh_only_gains_lines(self):
        def git(*args):
            return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=60)
        if git("rev-parse", "--verify", "-q", "main").returncode != 0:
            self.skipTest("SKIPPED AC-59: no local main branch to diff tests/run.sh against")
        base = git("merge-base", "HEAD", "main").stdout.strip()
        diff = git("diff", base, "--", "tests/run.sh")
        self.assertEqual(diff.returncode, 0, diff.stderr)
        removed = [l for l in diff.stdout.splitlines() if l.startswith("-") and not l.startswith("---")]
        self.assertEqual(removed, [], "tests/run.sh may only gain lines")

    def test_ac59_run_sh_runs_these_tests(self):
        self.assertIn("test_workspace_", text(ROOT / "tests" / "run.sh"))

    def test_ac70_sandbox_is_offline_and_temp_only(self):
        sb = Sandbox()
        try:
            for var in ("TEAM_WORKSPACE_ROOT", "TEAM_HOLDOUT_ROOT", "TEAM_ARCHIVE_ROOT", "HOME"):
                self.assertTrue(sb.env[var].startswith(str(sb.tmp) + "/"), var)
            self.assertNotIn("OPENROUTER_API_KEY", sb.env)
            self.assertNotEqual(subprocess.run(["git", "-C", str(sb.tmp), "rev-parse"], capture_output=True).returncode, 0,
                                "the sandbox must not sit inside a git repo")
        finally:
            sb.cleanup()

    def test_ac71_ac72_suite_has_the_required_scenarios(self):
        guard = text(ROOT / "tests" / "test_workspace_guard.py")
        for n in ("test_ac42_", "test_ac43_", "test_ac48_", "test_ac49_", "sealed_ws"):
            self.assertIn(n, guard)
        self.assertIn('"history": []', guard, "legacy lock fixture (AC-36)")
        self.assertIn("reflog", guard, "rebase/squash then verify (AC-33)")
        self.assertIn("rebase", guard)


class TestNoBackgroundWork(ContentCase):

    def test_ac99_source_has_no_scheduling(self):
        body = text(GUARD)
        for word in ("crontab", "launchctl", "systemctl", "nohup", "start_new_session", "daemon"):
            with self.subTest(word=word):
                self.assertNotIn(word, body)


if __name__ == "__main__":
    unittest.main()
