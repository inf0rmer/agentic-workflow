"""Small tooling bugs: option handling in seal and unseal, lockfiles in the review count,
hooks dirs inside the work tree, installer backups and replacement, and an upstream
license that goes away.

Every case runs offline in its own sandbox from wslib.
"""

import os
import re
import shutil
import stat
import sys
import unittest

from wslib import ROOT, read_bytes, tree_snapshot
from wstools import ToolCase
from test_workspace_installers import InstallCase
from test_workspace_review_budget import BudgetCase, lines, parse, warnings
from test_workspace_vendor_check import VendorCase

SEALED = "pkg/a_test.go"
USAGE = re.compile(r"usage|team-guard (un)?seal <", re.I)
NOT_INSTALLED = re.compile(r"(not|n't) (been )?installed", re.I)
HARNESSES = ("claude-code", "codex")
FROZEN_EPOCH = 1767225600  # every `date` call in the installer sees this one second


# -- seal and unseal options -------------------------------------------------

class TestSealOptions(ToolCase):

    def sealed_repo(self):
        repo = self.feature_repo()
        self.ok(self.sb.tg("seal", SEALED, cwd=repo))
        return repo

    def lock_bytes(self, repo):
        return read_bytes(self.git_dir(repo) / "team-test-lock.json")

    def test_help_on_seal_or_unseal_prints_usage_and_writes_nothing_to_the_lock(self):
        repo = self.sealed_repo()
        for cmd in ("seal", "unseal"):
            for flag in ("--help", "-h"):
                with self.subTest(cmd=cmd, flag=flag):
                    before = self.lock_bytes(repo)
                    r = self.code(self.sb.tg(cmd, flag, cwd=repo), 0)
                    self.assertRegex(r.out + r.err, USAGE, self.show(r))
                    self.lacks(r.out + r.err, "sealed 1 file", r)
                    self.assertEqual(self.lock_bytes(repo), before, "the lock is unchanged\n" + self.show(r))

    def test_other_options_on_seal_or_unseal_are_refused_and_write_nothing(self):
        repo = self.sealed_repo()
        for cmd in ("seal", "unseal"):
            for argv in (["-x"], ["--force"], [SEALED, "--dry-run"]):
                with self.subTest(cmd=cmd, argv=argv):
                    before = self.lock_bytes(repo)
                    r = self.code(self.sb.tg(cmd, *argv, cwd=repo), 1)
                    self.assertEqual(self.lock_bytes(repo), before, "the lock is unchanged\n" + self.show(r))
                    self.no_traceback(r)


# -- lockfiles and the review count ------------------------------------------

class TestLockfilesInReviewCount(BudgetCase):

    def test_lockfiles_are_left_out_of_review_lines_but_count_as_non_test_lines_and_files(self):
        lockfiles = ("go.sum", "web/package-lock.json", "web/pnpm-lock.yaml", "web/yarn.lock", "rust/Cargo.lock")
        files = {"pkg/code.go": lines(10)}
        for rel in lockfiles:
            files[rel] = lines(300)
        repo, feature = self.change(files)
        r = self.ok(self.risk(repo, feature, budget=100))
        _, rules, inputs = parse(r.out)
        self.assertEqual(inputs.get("review_lines"), "10", self.show(r))
        self.assertEqual(inputs.get("non_test_lines"), str(10 + 300 * len(lockfiles)), self.show(r))
        self.assertEqual(inputs.get("files"), str(1 + len(lockfiles)), self.show(r))
        self.assertEqual(inputs.get("over_budget"), "no", self.show(r))
        self.assertEqual(warnings(r.out), [], self.show(r))


# -- hooks dir inside the work tree ------------------------------------------

class TestHooksPathInsideWorkTree(ToolCase):

    def husky_repo(self, name, absolute):
        repo = self.feature_repo(name)
        hooks = repo / (".husky/_" if not absolute else "tools/hooks")
        hooks.mkdir(parents=True)
        (hooks / "pre-commit").write_text("#!/bin/sh\necho project-hook\n")
        (hooks / "pre-commit").chmod(0o755)
        value = str(hooks) if absolute else ".husky/_"
        self.sb.git(repo, "config", "core.hooksPath", value)
        return repo, hooks, value

    def test_git_hook_install_and_seal_write_nothing_into_a_hooks_dir_inside_the_work_tree(self):
        for absolute in (False, True):
            with self.subTest(absolute=absolute):
                repo, hooks, value = self.husky_repo("H%d" % absolute, absolute)
                named = "tools/hooks" if absolute else ".husky/_"
                before = tree_snapshot(hooks)
                git_hooks = tree_snapshot(self.git_dir(repo) / "hooks")

                r = self.code(self.sb.tg("git-hook", "install", cwd=repo), 0)
                self.assertEqual(tree_snapshot(hooks), before,
                                 "nothing is written into the project's hooks dir\n" + self.show(r))
                self.assertEqual(tree_snapshot(self.git_dir(repo) / "hooks"), git_hooks)
                self.has(r.out + r.err, named, r, "the warning names the hooks path")
                self.assertRegex(r.out + r.err, NOT_INSTALLED, self.show(r))

                r = self.code(self.sb.tg("seal", SEALED, cwd=repo), 0)
                self.assertEqual(tree_snapshot(hooks), before,
                                 "seal writes nothing into the project's hooks dir\n" + self.show(r))
                self.has(r.out + r.err, named, r, "seal warns naming the hooks path")
                self.assertRegex(r.out + r.err, NOT_INSTALLED, self.show(r))
                self.has(self.ok(self.sb.tg("list", cwd=repo)).out, SEALED)


# -- installers ---------------------------------------------------------------

class InstallerBugCase(InstallCase):

    def setUp(self):
        super().setUp()
        self.fakebin = self.sb.tmp / "fakebin"
        self.fakebin.mkdir()
        real = shutil.which("date", path=self.sb.env.get("PATH")) or "/bin/date"
        fixed = ("-r %d" if sys.platform == "darwin" else "-d @%d") % FROZEN_EPOCH
        stub = self.fakebin / "date"
        stub.write_text("#!/bin/sh\n# the clock never moves: every run lands in the same second\n"
                        "exec %s %s \"$@\"\n" % (real, fixed))
        stub.chmod(0o755)

    def run_installer(self, harness, tmpdir=None):
        """Run the installer as is, keeping earlier backups, with the clock frozen."""
        env = {"HOME": str(self.home), "CLAUDE_HOME": str(self.home / ".claude"),
               "CODEX_HOME": str(self.home / ".codex"),
               "PATH": str(self.fakebin) + os.pathsep + self.sb.env.get("PATH", "")}
        if tmpdir is not None:
            env["TMPDIR"] = str(tmpdir)
        return self.sb.run(["bash", str(self.repo / "adapters" / harness / "install.sh")], cwd=self.sb.scratch, env=env)

    def backups(self, harness):
        root = self.dest(harness) / "backups"
        return sorted(root.glob("agentic-workflow-*")) if root.exists() else []


class TestInstallerBackups(InstallerBugCase):

    def test_two_installs_in_the_same_second_each_get_their_own_backup(self):
        for harness in HARNESSES:
            with self.subTest(harness=harness):
                skills = self.skills_dir(harness)
                skills.mkdir(parents=True)
                stores = []
                for i in (1, 2):
                    store = self.ext / harness / ("store%d" % i) / "codebase-design"
                    store.mkdir(parents=True)
                    (store / "marker.txt").write_text("store %d\n" % i)
                    stores.append(store)

                os.symlink(stores[0], skills / "codebase-design")
                self.ok(self.run_installer(harness), "first install")
                shutil.rmtree(skills / "codebase-design")
                os.symlink(stores[1], skills / "codebase-design")
                r = self.ok(self.run_installer(harness), "second install in the same second")

                backups = self.backups(harness)
                self.assertEqual(len(backups), 2, "each run has its own backup dir: %s\n%s" % (backups, self.show(r)))
                links = sorted(os.readlink(b / "skills" / "codebase-design") for b in backups
                               if os.path.islink(b / "skills" / "codebase-design"))
                self.assertEqual(links, sorted(str(s) for s in stores), "each backup keeps the link it replaced")
                for i, store in enumerate(stores, 1):
                    self.assertEqual(sorted(p.name for p in store.iterdir()), ["marker.txt"],
                                     "nothing was written through the link")
                    self.assertEqual((store / "marker.txt").read_text(), "store %d\n" % i)
                self.assertFalse(os.path.islink(skills / "codebase-design"))
                self.assertTrue((skills / "codebase-design" / "SKILL.md").is_file())

    def test_an_existing_skill_dir_with_a_symlinked_file_is_replaced_entirely_and_never_written_through(self):
        for harness in HARNESSES:
            with self.subTest(harness=harness):
                skills = self.skills_dir(harness)
                mine = skills / "grilling"
                mine.mkdir(parents=True)
                outside = self.ext / ("%s-my-skill.md" % harness)
                outside.write_text("my own skill\n")
                os.symlink(outside, mine / "SKILL.md")
                (mine / "mine.md").write_text("my own notes\n")

                r = self.ok(self.run_installer(harness))
                self.assertEqual(outside.read_text(), "my own skill\n", "nothing was written through the link")
                self.assertFalse(os.path.islink(mine / "SKILL.md"), "our copy is a real file")
                self.assertEqual((mine / "SKILL.md").read_bytes(),
                                 (ROOT / "dist" / harness / "skills" / "grilling" / "SKILL.md").read_bytes())
                self.assertFalse(os.path.lexists(mine / "mine.md"),
                                 "the old dir is replaced, not merged into\n" + self.show(r))
                backup = self.backups(harness)[-1] / "skills" / "grilling"
                self.assertEqual((backup / "mine.md").read_text(), "my own notes\n", "the old dir was backed up")
                self.assertEqual((backup / "SKILL.md").read_text(), "my own skill\n")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "SKIPPED: root can read an unreadable file, so the failure can't be forced")
    def test_an_install_that_fails_partway_names_the_skill_and_exits_non_zero(self):
        for harness in HARNESSES:
            with self.subTest(harness=harness):
                broken = self.repo / "dist" / harness / "skills" / "handoff" / "SKILL.md"
                os.chmod(broken, 0)
                try:
                    r = self.run_installer(harness)
                finally:
                    os.chmod(broken, 0o644)
                self.nonzero(r)
                said = [l for l in (r.out + r.err).splitlines()
                        if "handoff" in l and re.search(r"fail|abort|stop|incomplete|error", l, re.I)
                        and not re.match(r"^\s*(cp|rm|mv|mkdir|install|ln):", l)]
                self.assertTrue(said, "the installer itself says which skill failed\n" + self.show(r))
                self.lacks(r.out, "Installed ", r)


class TestInstallerDenylist(InstallerBugCase):
    """The Jev denylist belongs to the person: a reinstall keeps it exactly as it was."""

    def jev(self, harness):
        return self.skills_dir(harness) / "jev-decide"

    def first_install(self, harness):
        self.ok(self.run_installer(harness), "first install")
        shutil.rmtree(self.dest(harness) / "backups", ignore_errors=True)
        return self.jev(harness) / "denylist.txt"

    def fresh_tmpdir(self, harness, case):
        t = self.sb.tmp / ("tmp-%s-%s" % (harness, case))
        t.mkdir()
        return t

    def leftovers(self, tmpdir):
        return sorted(os.path.relpath(os.path.join(d, f), tmpdir)
                      for d, _, files in os.walk(tmpdir) for f in files)

    def test_a_symlinked_denylist_stays_the_same_link_and_its_target_is_untouched(self):
        for harness in HARNESSES:
            with self.subTest(harness=harness):
                deny = self.first_install(harness)
                real = self.ext / ("%s-deny.txt" % harness)
                real.write_text("secret partner\n")
                os.chmod(real, 0o640)
                before = os.stat(real)
                deny.unlink()
                os.symlink(real, deny)
                tmpdir = self.fresh_tmpdir(harness, "link")

                r = self.ok(self.run_installer(harness, tmpdir))
                self.assertTrue(os.path.islink(deny), "the denylist is still a link\n" + self.show(r))
                self.assertEqual(os.readlink(deny), str(real), "the link points where it did")
                self.assertEqual(real.read_text(), "secret partner\n")
                after = os.stat(real)
                self.assertEqual((after.st_mode, after.st_mtime_ns, after.st_ino),
                                 (before.st_mode, before.st_mtime_ns, before.st_ino), "the target is untouched")
                self.assertEqual(self.leftovers(tmpdir), [], "no copy of the denylist is left in the temp dir")

    def test_a_regular_denylist_keeps_its_contents_and_its_mode(self):
        for harness in HARNESSES:
            for mode in (0o644, 0o640):
                with self.subTest(harness=harness, mode=oct(mode)):
                    deny = self.first_install(harness)
                    deny.write_text("acme corp\n")
                    os.chmod(deny, mode)
                    tmpdir = self.fresh_tmpdir(harness, "mode%o" % mode)

                    r = self.ok(self.run_installer(harness, tmpdir))
                    self.assertFalse(os.path.islink(deny))
                    self.assertEqual(deny.read_text(), "acme corp\n")
                    self.assertEqual(oct(stat.S_IMODE(os.lstat(deny).st_mode)), oct(mode),
                                     "the denylist keeps its mode\n" + self.show(r))
                    self.assertEqual(self.leftovers(tmpdir), [], "no copy of the denylist is left in the temp dir")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "SKIPPED: root can read an unreadable file, so the failure can't be forced")
    def test_a_denylist_that_cannot_be_saved_fails_the_install_naming_jev_decide(self):
        for harness in HARNESSES:
            with self.subTest(harness=harness):
                deny = self.first_install(harness)
                deny.write_text("acme corp\n")
                os.chmod(deny, 0)
                tmpdir = self.fresh_tmpdir(harness, "unreadable")
                try:
                    r = self.run_installer(harness, tmpdir)
                finally:
                    if os.path.lexists(deny):
                        os.chmod(deny, 0o644)
                self.nonzero(r)
                self.has(r.out + r.err, "agentic-workflow install: skill jev-decide failed", r)
                self.lacks(r.out, "Installed ", r)
                self.assertEqual(self.leftovers(tmpdir), [], "no copy of the denylist is left in the temp dir")


# -- vendor-check and a removed upstream license -----------------------------

class TestVendorCheckRemovedLicense(VendorCase):

    def test_an_upstream_that_removes_the_license_is_reported_as_drift_with_the_rest(self):
        self.branch("nolicense", lambda: self.sb.git(self.U, "rm", "-q", "LICENSE"))
        self.sb.git(self.U, "checkout", "-q", "main")
        r = self.code(self.check(ref="nolicense"), 1)
        out = r.out.splitlines()
        self.assertIn("alpha: ok", out, self.show(r))
        self.assertIn("beta-x: ok", out, self.show(r))
        self.assertRegex(r.out, r"(?m)^.*LICENSE.*(removed|deleted).*$", self.show(r))
        self.assertTrue(out and out[-1].startswith("drift in "), self.show(r))
        self.no_traceback(r)


if __name__ == "__main__":
    unittest.main()
