"""Harness text, adapters, build output and suite-level checks, including the
guard source having no background scheduling and where the Jev log is described."""

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

    def test_instructions_name_workspace_commands_and_plain_language_rule(self):
        self.must_contain(CORE / "instructions.md",
                          "team-guard workspace new", "team-guard workspace attach", "team-guard workspace-path",
                          "never in a project repo", "workspace dir", "attached worktree", "ship order",
                          "team-guard workspace close", *JARGON)

    def test_rendered_worktree_section_uses_attached_worktrees(self):
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

    def test_close_out_step_closes_the_workspace(self):
        for path in (CORE / "instructions.md", CORE / "roles" / "product-owner.md"):
            self.must_contain(path, "team-guard workspace close", "outside the workspace")


class TestRoles(ContentCase):

    def test_architect_covers_cross_repo_boundaries(self):
        self.must_contain(CORE / "roles" / "architect.md",
                          "Cross-repo boundaries", "owner repo", "consumer repo", "single-repo", "ship order")

    def test_engineer_and_doc_writer_avoid_workflow_jargon(self):
        for role in ("software-engineer.md", "doc-writer.md"):
            self.must_contain(CORE / "roles" / role, *JARGON)

    def test_tester_and_reviewer_read_workspace_inputs(self):
        for role in ("tester.md", "reviewer.md"):
            self.must_contain(CORE / "roles" / role, "team-guard holdout-path", "workspace dir")

    def test_no_role_puts_artifacts_in_a_repo(self):
        pat = re.compile(r"(commit|save|write|put)[^.\n]*(spec|contract\.md|design doc|architecture doc|review report)"
                         r"[^.\n]*(in|into|to) the (project )?repo", re.IGNORECASE)
        roles = sorted((CORE / "roles").glob("*.md"))
        self.assertTrue(roles)
        for role in roles:
            with self.subTest(role=role.name):
                m = pat.search(text(role))
                self.assertIsNone(m, "%s: %r" % (role.name, m.group(0) if m else ""))

    def test_product_owner_closes_the_workspace(self):
        self.must_contain(CORE / "roles" / "product-owner.md", "team-guard workspace close")

    def test_jev_log_location_is_stated_with_workspace_dir(self):
        for path in (CORE / "roles" / "reviewer.md", CORE / "instructions.md",
                     CORE / "skills" / "jev-decide" / "SKILL.md"):
            with self.subTest(file=str(path.relative_to(ROOT))):
                hits = [p for p in paragraphs(text(path)) if "jev-decisions.md" in p and "workspace dir" in p]
                self.assertTrue(hits, "%s: no paragraph mentions both jev-decisions.md and workspace dir" % path)


class TestAdapters(ContentCase):

    def test_claude_code_adapter_allows_and_hooks_team_guard(self):
        data = json.loads(text(ROOT / "adapters" / "claude-code" / "settings.hooks.json"))
        allow = data.get("permissions", {}).get("allow", [])
        self.assertTrue(any("team-guard:*" in a for a in allow), allow)
        hooks = json.dumps(data.get("hooks", {}))
        self.assertIn("team-guard hook-pretool", hooks)
        self.assertIn("team-guard hook-task", hooks)

    def test_codex_adapter_hooks_and_allows_team_guard(self):
        hooks = text(ROOT / "adapters" / "codex" / "hooks.json")
        rules = text(ROOT / "adapters" / "codex" / "team.rules.template")
        self.assertIn("team-guard hook-pretool", hooks)
        self.assertIn("team-guard hook-stop", hooks)
        self.assertRegex(rules, r'prefix_rule\(pattern=\["[^"]*/team-guard"\]',
                         "a prefix_rule whose pattern is exactly the team-guard path")


class TestBuildAndReadme(ContentCase):

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

    def test_dist_carries_the_workspace_text(self):
        for name, path in DIST_DOCS.items():
            with self.subTest(dist=name):
                body = text(path)
                for n in ("team-guard workspace new", "team-guard workspace close", "never in a project repo"):
                    self.assertIn(n, body)

    def test_readme_documents_workspaces_and_known_limits(self):
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

    def test_run_sh_only_gains_lines(self):
        def git(*args):
            return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=60)
        if git("rev-parse", "--verify", "-q", "main").returncode != 0:
            self.skipTest("SKIPPED: no local main branch to diff tests/run.sh against")
        base = git("merge-base", "HEAD", "main").stdout.strip()
        diff = git("diff", base, "--", "tests/run.sh")
        self.assertEqual(diff.returncode, 0, diff.stderr)
        removed = [l for l in diff.stdout.splitlines() if l.startswith("-") and not l.startswith("---")]
        self.assertEqual(removed, [], "tests/run.sh may only gain lines")

    def test_run_sh_runs_the_workspace_suites(self):
        self.assertIn("test_workspace_", text(ROOT / "tests" / "run.sh"))

    def test_sandbox_is_offline_and_temp_only(self):
        sb = Sandbox()
        try:
            for var in ("TEAM_WORKSPACE_ROOT", "TEAM_HOLDOUT_ROOT", "TEAM_ARCHIVE_ROOT", "HOME"):
                self.assertTrue(sb.env[var].startswith(str(sb.tmp) + "/"), var)
            self.assertNotIn("OPENROUTER_API_KEY", sb.env)
            self.assertNotEqual(subprocess.run(["git", "-C", str(sb.tmp), "rev-parse"], capture_output=True).returncode, 0,
                                "the sandbox must not sit inside a git repo")
        finally:
            sb.cleanup()

    def test_guard_suite_has_the_required_scenarios(self):
        guard = text(ROOT / "tests" / "test_workspace_guard.py")
        for n in ("test_pretool_with_workspace_dir_cwd", "test_pretool_multi_repo_patch",
                  "test_verify_passes_iff_all_sealed_files_match_property", "test_verify_reports_violations_per_repo",
                  "sealed_ws"):
            self.assertIn(n, guard)
        self.assertIn('"history": []', guard, "the guard suite covers a legacy lock without blob hashes")
        self.assertIn("reflog", guard, "the guard suite covers verify after a rebase or squash")
        self.assertIn("rebase", guard)


class TestNoBackgroundWork(ContentCase):

    def test_guard_source_has_no_scheduling(self):
        # The one exception is the design-gate review, which starts Crit in its own session so a
        # killed wait doesn't kill the review (approved answer 13): exactly one
        # start_new_session=True. The word "daemon" itself is allowed, because the gate reads
        # Crit's own "crit daemon ..." status lines; turning a process into a daemon is not.
        body = text(GUARD)
        for word in ("crontab", "launchctl", "systemctl", "nohup", "preexec_fn", "os.setsid", "os.fork(",
                     "daemonize", "daemonise", "DaemonContext", "double fork"):
            with self.subTest(word=word):
                self.assertNotIn(word, body)
        self.assertEqual(re.findall(r"start_new_session\s*=\s*\w+", body), ["start_new_session=True"],
                         "only the Crit review launch starts a new session")


if __name__ == "__main__":
    unittest.main()
