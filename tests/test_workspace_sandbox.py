"""TEAM_SANDBOX=1 makes `team-guard sign` stand aside, and the shared instructions say so.

Inside a sandboxed workspace no human signing key exists, so commits stay unsigned. Any other
value (unset, 0, empty) must leave signing exactly as it was.
"""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

from test_workspace_sign import SignCase
from wslib import ROOT

sys.path.insert(0, str(ROOT))
import build  # noqa: E402

SKIP_MESSAGE = "team-guard: sign: skipped (TEAM_SANDBOX=1, commits stay unsigned)"


class TestSignInSandbox(SignCase):

    def test_sandbox_skips_signing_and_leaves_every_commit_unchanged(self):
        repo = self.branch_repo()
        self.signing(repo)
        before = self.shape(repo)
        heads = self.sb.git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
        r = self.sb.tg("sign", "--base", "main", cwd=repo, env={"TEAM_SANDBOX": "1"})
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.out.strip(), SKIP_MESSAGE)
        self.assertEqual(self.shape(repo), before)
        self.assertEqual(self.sb.git(repo, "for-each-ref", "--format=%(refname) %(objectname)"), heads)
        self.assertFalse(self.all_signed(repo))

    def test_sandbox_skips_even_without_a_signing_setup_or_a_repo(self):
        r = self.sb.tg("sign", env={"TEAM_SANDBOX": "1"})
        self.assertEqual((r.code, r.out.strip()), (0, SKIP_MESSAGE), r)

    def test_other_values_still_sign(self):
        for label, env, unset in [("unset", None, ()), ("zero", {"TEAM_SANDBOX": "0"}, ()),
                                  ("empty", {"TEAM_SANDBOX": ""}, ())]:
            with self.subTest(value=label):
                repo = self.branch_repo("S" + label)
                self.signing(repo)
                r = self.sb.tg("sign", "--base", "main", cwd=repo, env=env, unset=unset)
                self.assertEqual(r.code, 0, r)
                self.assertNotIn("skipped", r.out)
                self.assertTrue(self.all_signed(repo))


class TestSharedInstructions(unittest.TestCase):

    def render(self, harness):
        with tempfile.TemporaryDirectory() as out:
            build.build(harness, out)
            name = {"claude-code": "CLAUDE.md", "codex": "AGENTS.md"}[harness]
            return (Path(out) / name).read_text()

    def test_ship_and_pr_watch_say_when_sign_is_skipped(self):
        for harness in ("claude-code", "codex"):
            with self.subTest(harness=harness):
                text = self.render(harness)
                ship = next(l for l in text.splitlines() if l.startswith("8. **Ship.**"))
                self.assertIn("skip `sign` when `TEAM_SANDBOX=1`", ship)
                watch = next(l for l in text.splitlines() if l.startswith("The first push needs"))
                self.assertIn("skip `sign` when `TEAM_SANDBOX=1`", watch)

    def test_existing_harnesses_keep_their_role_files_sentence_and_have_no_child_guard(self):
        expected = {
            "claude-code": "Role definitions are in `~/.claude/agents/` (`<role>.md`). A project can override one with its own in `.claude/agents/`.",
            "codex": "Role definitions are in `~/.codex/agents/` (`<role>.toml`). A project can override one with its own in `.codex/agents/`.",
        }
        for harness, sentence in expected.items():
            with self.subTest(harness=harness):
                text = self.render(harness)
                self.assertIn(sentence + "\n", text)
                self.assertTrue(text.startswith("# Global Instructions\n\n## Codebase Memory MCP"))
                self.assertNotIn("{{", text)

    def test_the_adapter_vars_are_defined_for_every_harness(self):
        for adapter in sorted((ROOT / "adapters").glob("*/adapter.json")):
            with self.subTest(adapter=adapter.parent.name):
                vars_ = json.loads(adapter.read_text())["vars"]
                self.assertIn("role_files", vars_)
                self.assertIn("child_guard", vars_)


if __name__ == "__main__":
    unittest.main()
