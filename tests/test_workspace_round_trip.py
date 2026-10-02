"""One real-tool pass over the workspace life cycle with real git: create, attach, commit,
sign with a real key, push to a bare remote, and close."""

import subprocess
import unittest

from test_workspace_sign import SignCase
from wslib import realpath


class TestRoundTrip(SignCase):

    def test_new_attach_commit_sign_push_and_close(self):
        src = self.sb.new_repo("api")
        bare = self.sb.add_remote(src)
        ws = self.new_ws("pay")
        wt = self.attach(src, "pay")
        self.signing(wt, verify=True)

        self.sb.commit_file(wt, "pkg/pay.go", "package pkg\n", "add pay")
        self.sb.commit_file(wt, "pkg/pay_test.go", "func TestPay(t *testing.T) {}\n", "test pay")
        self.assertFalse(self.all_signed(wt, "main"), "agents commit unsigned")

        r = self.ok(self.sign(ws, "--base", "main"))
        self.has(r.out, "team-guard: sign: api: signed 2 commit(s)", r)
        self.assertTrue(self.all_signed(wt, "main"))
        marks = self.sb.git(wt, "log", "--format=%G?", "main..HEAD").split()
        self.assertEqual(marks, ["G", "G"], "the signatures verify against the allowed signers")

        self.sb.git(wt, "push", "-q", "origin", "feat/pay")
        pushed = subprocess.run(["git", "-C", str(bare), "rev-parse", "feat/pay"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(pushed, self.sb.git(wt, "rev-parse", "HEAD"))

        (ws / "design.md").write_text("# pay\n")
        listing = self.ok(self.sb.tg("workspace", "list"))
        self.assertRegex(listing.out, r"(?m)^pay\t(active|merged)\t")
        self.assertEqual(self.ok(self.sb.tg("workspace-path", cwd=wt)).out.strip(), realpath(ws))

        r = self.ok(self.sb.tg("workspace", "close", "pay"))
        self.has(r.out, "closed pay; artifacts archived at", r)
        self.assertFalse(ws.exists())
        self.assertEqual((self.sb.ar_root / "pay" / "design.md").read_text(), "# pay\n")
        self.assertEqual(self.sb.worktrees(src), [realpath(src)])
        self.assertIn("refs/heads/feat/pay", self.sb.refs(src), "the branch survives the close")

    def test_creating_a_workspace_hints_at_stale_ones(self):
        src = self.sb.new_repo("api")
        self.new_ws("old")
        self.attach(src, "old")
        r = self.ok(self.sb.tg("workspace", "new", "next", env={"TEAM_STALE_DAYS": "0"}))
        self.has(r.err, "1 stale workspace(s); run `team-guard workspace list` to review them", r)


if __name__ == "__main__":
    unittest.main()
