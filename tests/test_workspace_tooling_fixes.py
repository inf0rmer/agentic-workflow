"""Regression tests for edge cases in the workflow tooling.

Covers answers of the wrong type in the routing log, a base missing from one repo of a
workspace, a signature that is present but invalid, lockfile hashes that look like
citations, and a hang-up signal during a mutation run.
"""

import signal
import subprocess
import unittest

from test_workspace_mutation_limits import MCCase
from test_workspace_risk import RiskCase, RANK, parse
from test_workspace_sign import SignCase
from wstools import MC, FifoWaiter, ToolCase, safe_answers, tok, ANSWER_KEYS

FAKE_SIGNER = """#!/bin/sh
# Signs with a well-formed but invalid signature, and fails every other request.
for a in "$@"; do last="$a"; done
if [ "$1" = "-Y" ] && [ "$2" = "sign" ]; then
  printf -- '-----BEGIN SSH SIGNATURE-----\\nAAAAbm90IGEgc2lnbmF0dXJl\\n-----END SSH SIGNATURE-----\\n' > "$last.sig"
  exit 0
fi
exit 1
"""


class TestRiskAnswerTypes(RiskCase):

    def test_answers_of_the_wrong_type_count_as_unknown(self):
        _, _, r = self.scenario(None, safe_answers(), packet=False)
        low = RANK[parse(self.ok(r).out)[0].split()[2]]
        bad = safe_answers(persisted=True, security=1, contract="<=3", revertible=False, repos="<=3", files=True)
        repo, feature, r = self.scenario(None, bad, packet=False, log=True)
        first = parse(self.ok(r).out)[0]
        self.assertEqual(first.split()[2], "high", self.show(r))
        self.assertGreaterEqual(RANK[first.split()[2]], low)
        risk = [e for e in self.log_lines() if e.get("event") == "risk"]
        self.assertEqual(len(risk), 1)
        answers = risk[0]["inputs"]["answers"]
        self.assertEqual(sorted(answers), sorted(ANSWER_KEYS))
        for k in ("persisted", "security", "contract", "revertible", "repos", "files"):
            with self.subTest(answer=k):
                self.assertEqual(answers[k], "unknown")
        for k, v in answers.items():
            with self.subTest(key=k, value=v):
                if k == "repos":
                    self.assertTrue(v == "unknown" or (type(v) is int and v >= 1), v)
                elif k == "files":
                    self.assertIn(v, ("<=3", "4-15", ">15", "unknown"))
                else:
                    self.assertIn(v, ("yes", "no", "unknown"))


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


class TestRefsLockfiles(ToolCase):

    def test_refs_skips_lockfiles_and_dependency_sums(self):
        repo = self.feature_repo("L")
        # test input for the reference scanner: hash-like runs that look like citations
        h1, h2 = tok("ac", "1f9e2d7c4b"), tok("adr", "9c0ffee12")
        files = {"go.sum": "example.com/m v1.0.0 h1:%s=\n" % h1,
                 "web/package-lock.json": '{"integrity": "sha512-%s"}\n' % h2,
                 "pnpm-lock.yaml": "integrity: sha512-%s\n" % h1,
                 "yarn.lock": "  integrity sha1-%s\n" % h2,
                 "rust/Cargo.lock": 'checksum = "%s"\n' % h1,
                 # test input for the reference scanner: a real citation outside a lockfile
                 "src/x.py": "# %s\n" % tok("AC", "-12")}
        for rel, text in files.items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        self.sb.git(repo, "add", "-A")
        self.sb.git(repo, "commit", "-qm", "deps")
        r = self.code(self.sb.tg("refs", "--base", "main", cwd=repo), 2)
        # test input for the reference scanner
        self.assertEqual(r.err.splitlines(), ["src/x.py:1: criterion-id: # %s" % tok("AC", "-12"),
                                              "team-guard: refs: 1 finding(s)"], self.show(r))


class TestMutationHangup(MCCase):

    def test_hangup_cleans_up_and_exits_129(self):
        repo = self.repo()
        gc = FifoWaiter(self.sb.tmp / "gc.fifo")
        ready = FifoWaiter(self.sb.tmp / "ready.fifo")
        self.addCleanup(gc.release)
        self.addCleanup(ready.release)
        self.fake.set(grandchild_fifo=gc.path, grandchild_pid=str(self.sb.tmp / "gc.pid"),
                      ready_fifo=ready.path, sleep=30)
        env = dict(self.sb.env, **self.mc_env())
        p = subprocess.Popen([str(MC), "--base", "main", "--go-only"], cwd=str(repo), env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertTrue(ready.ready(30), "the fake tool started")
            p.send_signal(signal.SIGHUP)
            p.communicate(timeout=30)
        finally:
            self.fake.set(ready_fifo=None)
            if p.poll() is None:
                p.kill()
                p.communicate()
        self.assertEqual(p.returncode, 129)
        calls = self.fake.calls()
        self.assertTrue(calls)
        td = calls[0]["env"]["TMPDIR"]
        self.assertEqual(td, "%s/mutation-check-%d" % (self.tmp_root, p.pid))
        self.assertEqual(self.run_dirs(), [], "the run's temp dir is removed")
        self.assertTrue(gc.closed(10), "the tool's own child process is gone")


if __name__ == "__main__":
    unittest.main()
