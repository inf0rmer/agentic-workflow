"""team-guard sign: re-sign every commit on the branch before anything is pushed.

Agents commit unsigned, so the branch is re-signed once at the human gate. Rewriting
history is only safe on a clean, ordinary checkout, and a failed rewrite must leave the
branch exactly where it was, so the command checks first and verifies afterwards.
"""

import random
import subprocess
import unittest
from pathlib import Path

from wstools import ToolCase

NOT_VERIFIED = "; signatures present, not verified (set gpg.ssh.allowedSignersFile to verify them)"


class SignCase(ToolCase):

    def setUp(self):
        super().setUp()
        self.key = self.sb.tmp / "signing-key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "t@t", "-f", str(self.key)],
                       check=True, capture_output=True)
        self.allowed = self.sb.tmp / "allowed-signers"
        self.allowed.write_text('t@t namespaces="git" %s' % Path(str(self.key) + ".pub").read_text())

    def signing(self, repo, verify=False, failing=False):
        self.sb.git(repo, "config", "gpg.format", "ssh")
        self.sb.git(repo, "config", "user.signingkey", str(self.key))
        if verify:
            self.sb.git(repo, "config", "gpg.ssh.allowedSignersFile", str(self.allowed))
        if failing:
            self.sb.git(repo, "config", "gpg.ssh.program", "false")

    def branch_repo(self, name="R", commits=3, parent=None):
        repo = self.sb.new_repo(name, parent=parent)
        self.sb.git(repo, "checkout", "-q", "-b", "feat/x")
        for i in range(commits):
            self.sb.commit_file(repo, "pkg/f%d.go" % i, "package pkg\nvar F%d = %d\n" % (i, i), "c%d" % i)
        return repo

    def shape(self, repo, base="main"):
        """(tip, [tree of each commit in base..HEAD, oldest first])."""
        trees = self.sb.git(repo, "log", "--reverse", "--topo-order", "--format=%T", "%s..HEAD" % base)
        return self.sb.git(repo, "rev-parse", "HEAD"), trees.splitlines()

    def commits(self, repo, base="main"):
        return self.sb.git(repo, "rev-list", "%s..HEAD" % base).splitlines()

    def all_signed(self, repo, base="main"):
        return all("\ngpgsig " in self.sb.git(repo, "cat-file", "commit", c) for c in self.commits(repo, base))

    def sign(self, cwd, *args):
        return self.sb.tg("sign", *args, cwd=cwd)


class TestSignPreflight(SignCase):

    def test_sign_refuses_an_unsafe_checkout_and_rewrites_nothing(self):
        def staged(repo):
            (repo / "pkg" / "a.go").write_text("package pkg // staged\n")
            self.sb.git(repo, "add", "pkg/a.go")

        def unstaged(repo):
            (repo / "README.md").write_text("changed\n")

        def rebasing(repo):
            self.sb.git(repo, "rebase", "--exec", "false", "main", check=False)
            self.assertTrue((self.git_dir(repo) / "rebase-merge").exists(), "harness: rebase stopped")

        def merging(repo):
            (self.git_dir(repo) / "MERGE_HEAD").write_text(self.sb.git(repo, "rev-parse", "main") + "\n")

        def detached(repo):
            self.sb.git(repo, "checkout", "-q", "--detach")

        cases = [("staged", staged, "has uncommitted changes to tracked files"),
                 ("unstaged", unstaged, "has uncommitted changes to tracked files"),
                 ("rebase", rebasing, "a rebase is in progress"),
                 ("merge", merging, "a merge is in progress"),
                 ("detached", detached, "HEAD is detached")]
        for n, (label, make, reason) in enumerate(cases):
            with self.subTest(state=label):
                repo = self.branch_repo("P%d" % n)
                self.signing(repo)
                heads = self.sb.git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
                make(repo)
                head = self.sb.git(repo, "rev-parse", "HEAD")
                cfg = self.config_list(repo)
                r = self.code(self.sign(repo, "--base", "main"), 1)
                self.has(r.err, "team-guard: sign: P%d: %s" % (n, reason), r)
                self.assertEqual(self.sb.git(repo, "rev-parse", "HEAD"), head)
                self.assertEqual(self.sb.git(repo, "for-each-ref", "--format=%(refname) %(objectname)"), heads)
                self.assertEqual(self.config_list(repo), cfg, "no git config is changed")

        A = self.sb.new_repo("A")
        B = self.sb.new_repo("B")
        ws = self.new_ws("f")
        wtA = self.attach(A)
        wtB = self.attach(B)
        for wt in (wtA, wtB):
            self.signing(wt)
            self.sb.commit_file(wt, "pkg/n.go", "package pkg\n", "work")
        tipA = self.sb.git(wtA, "rev-parse", "HEAD")
        (wtB / "README.md").write_text("dirty\n")
        r = self.code(self.sign(ws), 1)
        self.has(r.err, "team-guard: sign: B: has uncommitted changes to tracked files", r)
        self.assertEqual(self.sb.git(wtA, "rev-parse", "HEAD"), tipA, "no repo is rewritten when any fails the check")

        U = self.branch_repo("U")
        self.signing(U)
        (U / "scratch.txt").write_text("untracked\n")
        cfg = self.config_list(U)
        r = self.ok(self.sign(U, "--base", "main"))
        self.assertTrue(self.all_signed(U), "an untracked file doesn't block signing")
        self.assertEqual(self.config_list(U), cfg)


class TestSignRewrite(SignCase):

    def test_sign_signs_every_branch_commit_and_keeps_every_tree(self):
        repo = self.branch_repo("R", commits=2)
        self.sb.git(repo, "commit", "-q", "--allow-empty", "-m", "empty")
        self.sb.git(repo, "checkout", "-q", "-b", "side")
        self.sb.commit_file(repo, "pkg/side.go", "package pkg\n", "side")
        self.sb.git(repo, "checkout", "-q", "feat/x")
        self.sb.git(repo, "merge", "-q", "--no-ff", "-m", "merge side", "side")
        self.signing(repo)
        main_before = self.sb.git(repo, "rev-parse", "main")
        tip0, trees0 = self.shape(repo)
        n = len(self.commits(repo))
        cfg = self.config_list(repo)
        r = self.ok(self.sign(repo, "--base", "main"))
        self.assertEqual(r.out, "team-guard: sign: R: signed %d commit(s)%s\n" % (n, NOT_VERIFIED))
        tip1, trees1 = self.shape(repo)
        self.assertNotEqual(tip1, tip0)
        self.assertEqual(trees1, trees0, "same number of commits and the same tree at every position")
        self.assertTrue(self.all_signed(repo))
        self.assertEqual(self.sb.git(repo, "rev-parse", "main"), main_before)
        self.assertEqual(self.sb.git(repo, "rev-parse", "--abbrev-ref", "HEAD"), "feat/x")
        self.assertEqual(self.config_list(repo), cfg)

        V = self.branch_repo("V", commits=2)
        self.signing(V, verify=True)
        r = self.ok(self.sign(V, "--base", "main"))
        self.assertEqual(r.out, "team-guard: sign: V: signed 2 commit(s)\n")
        self.assertEqual(self.sb.git(V, "log", "--format=%G?", "main..HEAD").splitlines(), ["G", "G"])

        F = self.branch_repo("F", commits=2)
        self.signing(F, failing=True)
        tip = self.sb.git(F, "rev-parse", "HEAD")
        r = self.code(self.sign(F, "--base", "main"), 1)
        self.has(r.err, "team-guard: sign: F: ", r)
        self.assertEqual(self.sb.git(F, "rev-parse", "HEAD"), tip)
        self.assertEqual(self.sb.git(F, "rev-parse", "--abbrev-ref", "HEAD"), "feat/x")
        self.assertFalse((self.git_dir(F) / "rebase-merge").exists(), "no rebase is left in progress")
        self.assertEqual(self.sb.git(F, "status", "--porcelain"), "")

        E = self.branch_repo("E", commits=0)
        self.signing(E)
        r = self.ok(self.sign(E, "--base", "main"))
        self.assertIn("team-guard: sign: E: nothing to sign", r.out + r.err)

        A = self.sb.new_repo("A")
        B = self.sb.new_repo("B")
        ws = self.new_ws("f")
        wtA = self.attach(A)
        wtB = self.attach(B)
        for wt in (wtA, wtB):
            self.signing(wt)
            self.sb.commit_file(wt, "pkg/n.go", "package pkg\n", "work")
        r = self.ok(self.sign(ws))
        self.assertEqual(r.out.splitlines(), ["team-guard: sign: A: signed 1 commit(s)" + NOT_VERIFIED,
                                              "team-guard: sign: B: signed 1 commit(s)" + NOT_VERIFIED])
        self.sb.commit_file(wtA, "pkg/m.go", "package pkg\n", "more")
        self.sb.commit_file(wtB, "pkg/m.go", "package pkg\n", "more")
        self.sb.git(wtB, "config", "gpg.ssh.program", "false")
        tipB = self.sb.git(wtB, "rev-parse", "HEAD")
        r = self.code(self.sign(ws), 1)
        self.has(r.err, "team-guard: sign: B: ", r)
        self.assertEqual(self.sb.git(wtB, "rev-parse", "HEAD"), tipB)
        self.assertTrue(self.all_signed(wtA), "a repo signed before the failure stays signed")

        H = self.branch_repo("H", commits=1)
        self.signing(H)
        hook = self.git_dir(H) / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 0\n")
        hook.chmod(0o755)
        r = self.ok(self.sign(H, "--base", "main"))
        self.has(r.err, "team-guard: warning: H: another pre-commit hook runs on every amend (", r)
        self.has(r.err, "pre-commit)", r)
        C = self.branch_repo("C", commits=1)
        self.signing(C)
        chook = self.git_dir(C) / "hooks" / "pre-commit"
        chook.write_text("#!/bin/sh\nexit 0\n")
        chook.chmod(0o755)
        self.ok(self.sb.tg("seal", "pkg/a_test.go", cwd=C))
        self.assertTrue((self.git_dir(C) / "hooks" / "pre-commit.team-guard-chained").exists(), "harness: chained")
        r = self.ok(self.sign(C, "--base", "main"))
        self.has(r.err, "team-guard: warning: C: another pre-commit hook runs on every amend (", r)
        N = self.branch_repo("N", commits=1)
        self.signing(N)
        self.ok(self.sb.tg("seal", "pkg/a_test.go", cwd=N))
        r = self.ok(self.sign(N, "--base", "main"))
        self.lacks(r.err, "another pre-commit hook", r)

    def test_sign_keeps_trees_on_success_and_the_tip_on_failure_property(self):
        rng = random.Random(9)
        for it in range(3):
            with self.subTest(iteration=it):
                repo = self.branch_repo("Q%d" % it, commits=0)
                for c in range(rng.randint(1, 4)):
                    for _ in range(rng.randint(1, 3)):
                        f = "pkg/q%d.go" % rng.randint(0, 3)
                        (repo / f).write_text("package pkg\nvar Q = %d\n" % rng.randint(0, 999))
                    self.sb.git(repo, "add", "-A")
                    self.sb.git(repo, "commit", "-q", "--allow-empty", "-m", "q%d" % c)
                fail = rng.random() < 0.5
                self.signing(repo, failing=fail)
                tip0, trees0 = self.shape(repo)
                r = self.sign(repo, "--base", "main")
                tip1, trees1 = self.shape(repo)
                if fail:
                    self.code(r, 1)
                    self.assertEqual(tip1, tip0)
                else:
                    self.ok(r)
                    self.assertEqual(trees1, trees0)
                    self.assertTrue(self.all_signed(repo))


if __name__ == "__main__":
    unittest.main()
