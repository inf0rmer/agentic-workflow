"""TEAM_SANDBOX=1 makes `team-guard sign` stand aside, and the shared instructions say so.

Inside a sandboxed workspace no human signing key exists, so commits stay unsigned. Any other
value (unset, 0, empty) must leave signing exactly as it was.
"""

import hashlib
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

    def test_ship_and_pr_watch_say_sign_skips_in_a_sandbox_and_the_commits_are_unsigned(self):
        for harness in ("claude-code", "codex"):
            with self.subTest(harness=harness):
                text = self.render(harness)
                ship = next(l for l in text.splitlines() if l.startswith("8. **Ship.**"))
                watch = next(l for l in text.splitlines() if l.startswith("The first push needs"))
                for line in (ship, watch):
                    self.assertIn("`team-guard sign", line)
                    self.assertIn("`TEAM_SANDBOX=1`", line)
                    self.assertIn("unsigned", line)
                self.assertIn("in the PR draft", ship)

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
                self.assertIn("design_review", vars_)

    def test_existing_harnesses_keep_their_crit_design_review_step(self):
        step = ("3. **Approval.** Run `crit design.md` from the workspace so the human can comment (Crit renders the diagrams), "
                "or post the doc in chat. The architect revises from comments. The human approves in Crit, or replies "
                "`approve design` in chat. Then add")
        for harness in ("claude-code", "codex"):
            with self.subTest(harness=harness):
                self.assertIn(step, self.render(harness))



# What claude-code and codex rendered before TEAM_SANDBOX existed, as sha256 of the files in
# dist/ at that time. The test below rebuilds that text from today's output by undoing the
# sandbox wording, so any other drift in these shipped files fails. If a later change to the
# shared instructions or team-guard is intended, refresh these hashes in that change.
BASELINE = {
    "claude-code": {"CLAUDE.md": "9898077e993521d2efaa2e181200faec302c48e3c1396cc4e59a25ca5e78e900",
                    "bin/team-guard": "da3e2a68829d8d488e791f87eba7fd4032645b2f6e4e9c395990c468faeb7991"},
    "codex": {"AGENTS.md": "d7855dfc17d4c87477198a27cf2c1b5f2529f5ebfd44fbe160226fa0176dcf03",
              "bin/team-guard": "da3e2a68829d8d488e791f87eba7fd4032645b2f6e4e9c395990c468faeb7991"},
}
SANDBOX_EDITS = [  # (new text, text it replaced)
    (" (with `TEAM_SANDBOX=1` it skips signing and the commits stay unsigned: say so in the PR draft; only the workspace image sets it, never set it yourself)", ""),
    (" (with `TEAM_SANDBOX=1` it skips and the commits stay unsigned)", ""),
    ("                                   (does nothing when TEAM_SANDBOX=1, which a sandboxed workspace image sets)\n", ""),
    (", TEAM_SANDBOX.\n", ".\n"),
    ('    if os.environ.get("TEAM_SANDBOX") == "1":\n'
     '        # A sandboxed workspace has no human signing key; its commits stay unsigned by design.\n'
     '        print("team-guard: sign: skipped (TEAM_SANDBOX=1, commits stay unsigned)")\n'
     '        return\n', ""),
]


class TestExistingHarnessesDrift(unittest.TestCase):

    def test_shipped_claude_code_and_codex_files_differ_from_the_pre_sandbox_output_only_by_the_sandbox_wording(self):
        for harness, files in BASELINE.items():
            for rel, digest in files.items():
                with self.subTest(harness=harness, file=rel):
                    text = (ROOT / "dist" / harness / rel).read_text()
                    for new, old in SANDBOX_EDITS:
                        text = text.replace(new, old)
                    self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), digest)

    def test_every_sandbox_edit_is_present_in_the_shipped_files(self):
        shipped = {h: {rel: (ROOT / "dist" / h / rel).read_text() for rel in files} for h, files in BASELINE.items()}
        for new, _ in SANDBOX_EDITS:
            with self.subTest(edit=new[:40]):
                self.assertTrue(any(new in t for files in shipped.values() for t in files.values()))


if __name__ == "__main__":
    unittest.main()
