"""Regression tests for edge cases in signing: a base missing from one repo of a workspace,
and a signature that is present but invalid.
"""


import unittest

from test_workspace_sign import SignCase

FAKE_SIGNER = """#!/bin/sh
# Signs with a well-formed but invalid signature, and fails every other request.
for a in "$@"; do last="$a"; done
if [ "$1" = "-Y" ] && [ "$2" = "sign" ]; then
  printf -- '-----BEGIN SSH SIGNATURE-----\\nAAAAbm90IGEgc2lnbmF0dXJl\\n-----END SSH SIGNATURE-----\\n' > "$last.sig"
  exit 0
fi
exit 1
"""


class TestSignBaseAndVerification(SignCase):

    def test_sign_refuses_when_the_base_is_missing_in_one_repo(self):
        A = self.sb.new_repo("A")
        B = self.sb.new_repo("B")
        self.sb.git(A, "branch", "release")
        ws = self.new_ws("f")
        wtA = self.attach(A)
        wtB = self.attach(B)
        for wt in (wtA, wtB):
            self.signing(wt)
            self.sb.commit_file(wt, "pkg/n.go", "package pkg\n", "work")
        tips = [self.sb.git(wt, "rev-parse", "HEAD") for wt in (wtA, wtB)]
        r = self.code(self.sign(ws, "--base", "release"), 1)
        self.has(r.err, "team-guard: sign: B: ", r)
        self.assertEqual([self.sb.git(wt, "rev-parse", "HEAD") for wt in (wtA, wtB)], tips,
                         "no repo is signed when the base is missing in any of them")
        self.assertFalse(self.all_signed(wtA, base="main"))

    def test_sign_restores_the_tip_when_the_new_signatures_do_not_verify(self):
        repo = self.branch_repo("R", commits=2)
        self.signing(repo, verify=True)
        fake = self.sb.tmp / "fake-signer"
        fake.write_text(FAKE_SIGNER)
        fake.chmod(0o755)
        self.sb.git(repo, "config", "gpg.ssh.program", str(fake))
        tip = self.sb.git(repo, "rev-parse", "HEAD")
        r = self.sign(repo, "--base", "main")
        self.nonzero(r)
        self.has(r.err, "team-guard: sign: R: ", r)
        self.assertEqual(self.sb.git(repo, "rev-parse", "HEAD"), tip, "the original tip is restored")
        self.assertEqual(self.sb.git(repo, "rev-parse", "--abbrev-ref", "HEAD"), "feat/x")
        self.assertFalse((self.git_dir(repo) / "rebase-merge").exists())
        self.assertEqual(self.sb.git(repo, "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
