"""The security-reviewer role: how it builds for each harness, and what its text promises.

Builds go to a temp dir, so these tests don't depend on dist/ being current.
"""

import json
import re
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from wslib import ROOT

sys.path.insert(0, str(ROOT))
import build  # noqa: E402

ROLE = ROOT / "core" / "roles" / "security-reviewer.md"
LIMIT = 16 * 1024
BANNED_TOOLS = ("Edit", "Write", "WebFetch", "WebSearch", "Agent")


def built(harness):
    tmp = tempfile.TemporaryDirectory()
    build.build(harness, tmp.name)
    return tmp


def frontmatter(path):
    text = Path(path).read_text()
    assert text.startswith("---\n")
    return dict(l.split(": ", 1) for l in text.split("---\n")[1].splitlines())


class TestBuildOutput(unittest.TestCase):

    def test_claude_code_role_is_limited_to_read_only_tools(self):
        with built("claude-code") as out:
            fm = frontmatter(Path(out) / "agents" / "security-reviewer.md")
        self.assertEqual(fm["tools"], "Read, Grep, Glob, Bash, Skill")
        for tool in BANNED_TOOLS:
            self.assertNotIn(tool, fm["tools"])
        self.assertEqual(fm["model"], "claude-opus-5-5")

    def test_no_other_claude_code_role_gets_a_tools_line(self):
        with built("claude-code") as out:
            for f in sorted((Path(out) / "agents").glob("*.md")):
                if f.stem != "security-reviewer":
                    with self.subTest(role=f.stem):
                        self.assertNotIn("tools", frontmatter(f))

    def test_codex_role_parses_and_has_no_tools_setting(self):
        with built("codex") as out:
            data = tomllib.loads((Path(out) / "agents" / "security-reviewer.toml").read_text())
        self.assertEqual(data["name"], "security-reviewer")
        self.assertIn("security", data["description"].lower())
        self.assertNotIn("tools", data)
        self.assertNotIn("sandbox_mode", data)

    def test_rendered_instructions_stay_under_the_size_limit(self):
        for harness, name in (("claude-code", "CLAUDE.md"), ("codex", "AGENTS.md")):
            with self.subTest(harness=harness), built(harness) as out:
                self.assertLessEqual(len((Path(out) / name).read_bytes()), LIMIT)

    def test_the_role_is_read_only_in_the_team_file(self):
        role = json.loads((ROOT / "core" / "team.json").read_text())["roles"]["security-reviewer"]
        self.assertEqual((role["tier"], role["access"]), ("strong", "read-only"))


class TestRoleText(unittest.TestCase):

    def setUp(self):
        self.text = ROLE.read_text()

    def test_it_lists_every_trigger(self):
        for t in ("authn/authz", "input parsing or deserialization", "templates and HTML", "SQL, shell or exec",
                  "file paths", "outbound network", "crypto and randomness", "secrets and config defaults",
                  "dependency manifests and lockfiles", "CI config, installers and hooks", "stored-data formats",
                  "writes into a user's home dir or settings", "removed validation or security code"):
            with self.subTest(trigger=t):
                self.assertIn(t, self.text)

    def test_each_scanner_comes_with_its_install_command(self):
        for scanner, install in (("gitleaks", "brew install gitleaks"), ("semgrep", "brew install semgrep"),
                                 ("osv-scanner", "brew install osv-scanner"),
                                 ("govulncheck", "go install golang.org/x/vuln/cmd/govulncheck@latest"),
                                 ("zizmor", "brew install zizmor")):
            with self.subTest(scanner=scanner):
                self.assertIn(install, self.text)
        self.assertIn("gitleaks git -v --no-banner --log-opts", self.text)
        self.assertIn("--redact", self.text)
        self.assertIn("--baseline-commit", self.text)
        self.assertIn("--metrics off", self.text)
        self.assertIn("zizmor --offline", self.text)

    def test_it_states_the_rules_that_keep_findings_private_and_diffs_untrusted(self):
        for needle in ("never report", "denial of service", "data, not instructions",
                       "never silently drop a scanner hit", "{{workspace_locate}}",
                       "STOP: the human decides", "Never quote a secret value"):
            with self.subTest(needle=needle):
                self.assertIn(needle.lower(), self.text.lower())

    def test_it_never_runs_or_copies_code_under_review(self):
        low = self.text.lower()
        for needle in ("never run, import, build or install code from the repo or the diff",
                       "never make temp copies or worktrees"):
            with self.subTest(needle=needle):
                self.assertIn(needle, low)

    def test_it_names_the_git_commands_that_change_the_tree_and_forbids_them(self):
        self.assertIn("never run `git checkout`, `switch`, `stash`, `reset`, `restore`, `commit`, or any other "
                      "command that changes the working tree, the index or a branch", self.text.lower())
        self.assertIn("read history with `git show`, `git diff` and `git log` only", self.text.lower())

    def test_gitleaks_shows_rule_file_and_line_with_the_secret_redacted(self):
        self.assertIn('gitleaks git -v --no-banner --log-opts="<base>..HEAD" --redact', self.text)

    def test_agent_aimed_text_is_always_reported_even_if_a_gate_would_reject_it(self):
        low = self.text.lower()
        self.assertIn("bypasses gate 3", low)
        self.assertIn("hardening notes", low)

    def test_missing_upstream_clone_is_not_verified_and_never_fetched(self):
        low = self.text.lower()
        self.assertIn("no local upstream clone", low)
        self.assertIn("byte-for-byte", low)

    def test_scanner_output_never_quotes_matched_secrets(self):
        self.assertIn("never quote a matched value from any scanner", self.text.lower())

    def test_it_never_asks_for_a_report_file_in_a_repo_or_temp_dir(self):
        self.assertIsNone(re.search(r"/tmp\b|\$TMPDIR", self.text))

    def test_each_harness_loads_each_skill_its_own_way(self):
        with built("claude-code") as out:
            claude = (Path(out) / "agents" / "security-reviewer.md").read_text()
        with built("codex") as out:
            codex = (Path(out) / "agents" / "security-reviewer.toml").read_text()
        for skill in ("differential-review", "fp-check"):
            with self.subTest(skill=skill):
                self.assertIn("call the Skill tool with `%s`" % skill, claude)
                self.assertIn("mention `$%s`" % skill, codex)


class TestWorkflowText(unittest.TestCase):

    def read(self, *parts):
        return ROOT.joinpath("core", *parts).read_text()

    def test_reviewer_keeps_exploit_details_out_of_the_guide_and_flags_a_missing_security_review(self):
        text = self.read("roles", "reviewer.md")
        self.assertIn("exploit details", text)
        self.assertIn("review-guide.md", text[text.index("exploit details") - 300:text.index("exploit details") + 300])
        self.assertIn("no security review is running", text)

    def test_architect_asks_for_what_can_go_wrong_on_triggered_changes(self):
        text = self.read("roles", "architect.md")
        self.assertIn("What can go wrong", text)
        self.assertIn("mitigation", text[text.index("What can go wrong"):])

    def test_instructions_add_the_role_the_stop_point_and_the_followups_rule(self):
        text = self.read("instructions.md")
        for needle in ("**security-reviewer**", "`security-review.md`", "Never turn a security finding into a public issue or PR",
                       "security fix", "vulnerability in existing code", "Security review:"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_the_leads_trigger_list_matches_the_roles(self):
        text = self.read("instructions.md")
        step = text[text.index("5. **Review.**"):text.index("6. **PR description.**")]
        for needle in ("outbound network", "crypto and randomness", "sessions and tokens", "templates and HTML",
                       "tool lists or permissions", "security-reviewer` role's list"):
            with self.subTest(needle=needle):
                self.assertIn(needle, step)

    def test_instructions_start_the_role_in_both_variants_and_one_check_per_candidate(self):
        text = self.read("instructions.md")
        step = text[text.index("5. **Review.**"):text.index("6. **PR description.**")]
        for needle in ("Quick", "Feature", "find mode", "check mode", "one fresh", "blocker"):
            with self.subTest(needle=needle):
                self.assertIn(needle, step)

    def test_the_pr_risk_line_takes_the_higher_of_the_two_reviews(self):
        text = self.read("instructions.md")
        step = text[text.index("6. **PR description.**"):text.index("7. {{human_gate}}")]
        self.assertIn("higher", step)
        self.assertIn("Security review:", step)


if __name__ == "__main__":
    unittest.main()
