"""Small tooling bugs: installer backups and replacement, and an upstream license that goes away.

Every case runs offline in its own sandbox from wslib.
"""


import os
import re
import shutil
import sys
import unittest

from wslib import ROOT
from test_workspace_installers import InstallCase
from test_workspace_vendor_check import VendorCase

HARNESSES = ("claude-code", "codex")
FROZEN_EPOCH = 1767225600  # every `date` call in the installer sees this one second


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
                    store = self.ext / harness / ("store%d" % i) / "diagnosing-bugs"
                    store.mkdir(parents=True)
                    (store / "marker.txt").write_text("store %d\n" % i)
                    stores.append(store)

                os.symlink(stores[0], skills / "diagnosing-bugs")
                self.ok(self.run_installer(harness), "first install")
                shutil.rmtree(skills / "diagnosing-bugs")
                os.symlink(stores[1], skills / "diagnosing-bugs")
                r = self.ok(self.run_installer(harness), "second install in the same second")

                backups = self.backups(harness)
                self.assertEqual(len(backups), 2, "each run has its own backup dir: %s\n%s" % (backups, self.show(r)))
                links = sorted(os.readlink(b / "skills" / "diagnosing-bugs") for b in backups
                               if os.path.islink(b / "skills" / "diagnosing-bugs"))
                self.assertEqual(links, sorted(str(s) for s in stores), "each backup keeps the link it replaced")
                for i, store in enumerate(stores, 1):
                    self.assertEqual(sorted(p.name for p in store.iterdir()), ["marker.txt"],
                                     "nothing was written through the link")
                    self.assertEqual((store / "marker.txt").read_text(), "store %d\n" % i)
                self.assertFalse(os.path.islink(skills / "diagnosing-bugs"))
                self.assertTrue((skills / "diagnosing-bugs" / "SKILL.md").is_file())

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
