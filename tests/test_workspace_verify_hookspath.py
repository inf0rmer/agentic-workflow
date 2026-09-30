"""team-guard verify warns when core.hooksPath hides team-guard's git hooks.

Tools such as Husky point core.hooksPath at their own directory, and git then never runs
the hooks team-guard installed in .git/hooks. Nothing refuses a commit to a sealed test
in that case, so verify says so; it changes nothing and keeps its exit code.
"""

import shutil
import subprocess
import unittest

from wstools import ToolCase, ROOT

SEALED = "pkg/a_test.go"


def warning(value, who, repo=None):
    return ("team-guard: warning: %score.hooksPath is %s; %s sets this, and git then ignores team-guard's hooks in "
            ".git/hooks, so verify and the reviewer are the remaining checks (team-guard git-hook install would "
            "write into that directory)" % ("%s: " % repo if repo else "", value, who))


class TestHooksPathWarning(ToolCase):

    def previous_guard(self):
        """team-guard as it was on main, to show which outputs must not change."""
        r = subprocess.run(["git", "-C", str(ROOT), "show", "main:core/bin/team-guard"], capture_output=True)
        if r.returncode != 0:
            self.skipTest("SKIPPED: no local main branch to compare verify output with")
        p = self.sb.tmp / "team-guard-main"
        p.write_bytes(r.stdout)
        p.chmod(0o755)
        return p

    def verify(self, cwd, guard=None):
        if guard is None:
            return self.sb.tg("verify", cwd=cwd)
        return self.sb.run([str(guard), "verify"], cwd=cwd)

    def sealed_repo(self, name="R"):
        repo = self.feature_repo(name)
        self.ok(self.sb.tg("seal", SEALED, cwd=repo))
        return repo

    def test_verify_warns_when_core_hookspath_bypasses_the_hooks(self):
        old = self.previous_guard()
        for value, who in ((".husky/_", "Husky"), ("tools/githooks", "a tool such as Husky")):
            with self.subTest(value=value):
                repo = self.sealed_repo("R%d" % len(who))
                self.sb.git(repo, "config", "core.hooksPath", value)
                cfg = self.config_list(repo)
                hooks = sorted(p.name for p in (self.git_dir(repo) / "hooks").iterdir())
                r = self.code(self.verify(repo), 0)
                self.assertIn(warning(value, who), r.err.splitlines(), self.show(r))
                self.assertEqual(r.out, self.verify(repo, old).out, "stdout is unchanged")
                (repo / SEALED).write_text("// weakened\n")
                r = self.code(self.verify(repo), 2)
                self.assertIn(warning(value, who), r.err.splitlines(), self.show(r))
                self.sb.git(repo, "checkout", "-q", "--", SEALED)
                self.assertEqual(self.config_list(repo), cfg, "no config is changed")
                self.assertEqual(sorted(p.name for p in (self.git_dir(repo) / "hooks").iterdir()), hooks)
                self.assertFalse((repo / value).exists(), "nothing is written into the hooks path")

        A = self.sb.new_repo("A")
        B = self.sb.new_repo("B")
        ws = self.new_ws("f")
        self.attach(A)
        wtB = self.attach(B)
        self.ok(self.sb.tg("seal", SEALED, cwd=wtB))
        self.sb.git(wtB, "config", "core.hooksPath", ".husky")
        r = self.ok(self.verify(ws))
        self.assertIn(warning(".husky", "Husky", repo="B"), r.err.splitlines(), self.show(r))

        # Negative cases: output is byte-identical to verify before this warning existed.
        unset = self.sealed_repo("N1")
        own = self.sealed_repo("N2")
        (own / "myhooks").mkdir()
        shutil.copy2(self.git_dir(own) / "hooks" / "pre-commit", own / "myhooks" / "pre-commit")
        self.sb.git(own, "config", "core.hooksPath", "myhooks")
        nothing = self.feature_repo("N3")
        self.sb.git(nothing, "config", "core.hooksPath", ".husky/_")
        for label, repo in (("hooksPath unset", unset), ("team-guard's hook in the hooks path", own),
                            ("nothing sealed", nothing)):
            with self.subTest(case=label):
                new, prev = self.verify(repo), self.verify(repo, old)
                self.assertEqual((new.code, new.out, new.err), (prev.code, prev.out, prev.err))


if __name__ == "__main__":
    unittest.main()
