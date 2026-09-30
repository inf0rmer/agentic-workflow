"""The harness installers never lose what a person already installed.

Each installer is run from a copy of the repo (with a stand-in for the self-test it runs
first) into a sandboxed home. An existing skill is backed up before it is replaced, a
symlinked skill is never written through, an upstream `tdd` skill is warned about and left
in place, and the Jev denylist a person keeps survives a reinstall.
"""

import os
import shutil
import subprocess
import unittest
from pathlib import Path

from wslib import ROOT, WSTestCase

DEFAULT_DENYLIST = "# Names that must never be sent to Jev"


class InstallCase(WSTestCase):

    def setUp(self):
        super().setUp()
        self.repo = self.sb.tmp / "repo"
        for part in ("adapters", "dist"):
            shutil.copytree(ROOT / part, self.repo / part, symlinks=True)
        (self.repo / "tests").mkdir()
        stub = self.repo / "tests" / "run.sh"
        stub.write_text("#!/usr/bin/env bash\n# stands in for the self-test the installer runs first\nexit 0\n")
        stub.chmod(0o755)
        self.home = self.sb.tmp / "h"
        self.home.mkdir()
        self.ext = self.sb.tmp / "elsewhere"
        self.ext.mkdir()

    def skills_dir(self, harness):
        return self.home / ".claude" / "skills" if harness == "claude-code" else self.home / ".agents" / "skills"

    def dest(self, harness):
        return self.home / ".claude" if harness == "claude-code" else self.home / ".codex"

    def install(self, harness):
        # Backups are named to the second; clear earlier ones so quick reinstalls in one test
        # don't share a backup dir.
        shutil.rmtree(self.dest(harness) / "backups", ignore_errors=True)
        env = {"HOME": str(self.home), "CLAUDE_HOME": str(self.home / ".claude"), "CODEX_HOME": str(self.home / ".codex")}
        r = self.sb.run(["bash", str(self.repo / "adapters" / harness / "install.sh")], cwd=self.sb.scratch, env=env)
        self.ok(r, "install %s" % harness)
        return r

class TestInstallers(InstallCase):

    def test_existing_and_symlinked_skills_are_backed_up_and_never_written_through(self):
        for harness in ("claude-code", "codex"):
            with self.subTest(harness=harness):
                skills = self.skills_dir(harness)
                (skills / "grilling").mkdir(parents=True)
                (skills / "grilling" / "mine.md").write_text("my own notes\n")
                store = self.ext / harness / "codebase-design"
                store.mkdir(parents=True)
                (store / "marker.txt").write_text("store copy\n")
                os.symlink(store, skills / "codebase-design")
                (skills / "tdd").mkdir()
                (skills / "tdd" / "SKILL.md").write_text("---\nname: tdd\n---\n")

                r = self.install(harness)
                b = self.backup(harness)
                self.assertEqual((b / "skills" / "grilling" / "mine.md").read_text(), "my own notes\n",
                                 "the replaced skill was backed up first")
                self.assertTrue((skills / "grilling" / "SKILL.md").is_file())
                self.assertEqual((skills / "grilling" / "SKILL.md").read_bytes(),
                                 (ROOT / "dist" / harness / "skills" / "grilling" / "SKILL.md").read_bytes())

                self.assertTrue(os.path.islink(b / "skills" / "codebase-design"), "the link itself is backed up")
                self.assertEqual(os.readlink(b / "skills" / "codebase-design"), str(store))
                self.assertFalse(os.path.islink(skills / "codebase-design"), "the link is replaced by a real dir")
                self.assertTrue((skills / "codebase-design" / "SKILL.md").is_file())
                self.assertEqual(sorted(p.name for p in store.iterdir()), ["marker.txt"],
                                 "nothing was written through the link")
                self.assertEqual((store / "marker.txt").read_text(), "store copy\n")

                self.assertIn("warning: an upstream 'tdd' skill is installed", r.err)
                self.assertEqual((skills / "tdd" / "SKILL.md").read_text(), "---\nname: tdd\n---\n",
                                 "the upstream tdd skill is not deleted")

    def test_the_jev_denylist_survives_a_reinstall(self):
        for harness in ("claude-code", "codex"):
            jev = self.skills_dir(harness) / "jev-decide"
            with self.subTest(harness=harness, case="none existed"):
                self.install(harness)
                self.assertTrue((jev / "denylist.txt").read_text().startswith(DEFAULT_DENYLIST))
            with self.subTest(harness=harness, case="a plain file"):
                (jev / "denylist.txt").write_text("acme corp\n")
                self.install(harness)
                self.assertEqual((jev / "denylist.txt").read_text(), "acme corp\n")
            with self.subTest(harness=harness, case="behind a symlink"):
                real = self.ext / ("%s-deny.txt" % harness)
                real.write_text("secret partner\n")
                (jev / "denylist.txt").unlink()
                os.symlink(real, jev / "denylist.txt")
                self.install(harness)
                self.assertEqual((jev / "denylist.txt").read_text(), "secret partner\n")
                self.assertEqual(real.read_text(), "secret partner\n", "the linked file keeps its contents")
            with self.subTest(harness=harness, case="the skill dir behind a symlink"):
                store = self.ext / ("%s-store" % harness) / "jev-decide"
                shutil.copytree(jev, store)
                (store / "denylist.txt").unlink()
                (store / "denylist.txt").write_text("linked store name\n")
                shutil.rmtree(jev)
                os.symlink(store, jev)
                self.install(harness)
                self.assertFalse(os.path.islink(jev), "the link is replaced by a real dir")
                self.assertEqual((jev / "denylist.txt").read_text(), "linked store name\n")
                self.assertEqual((store / "denylist.txt").read_text(), "linked store name\n")
            shutil.rmtree(self.dest(harness) / "backups", ignore_errors=True)

    def backup(self, harness):
        backups = sorted((self.dest(harness) / "backups").glob("agentic-workflow-*"))
        self.assertTrue(backups, "a backup was made")
        return backups[-1]


if __name__ == "__main__":
    unittest.main()
