"""The harness installers never lose what a person already installed.

Each installer is run from a copy of the repo (with a stand-in for the self-test it runs
first) into a sandboxed home. An existing skill is backed up before it is replaced, a
symlinked skill is never written through, and an upstream `tdd` skill is backed up and
replaced.
"""


import os
import shutil
import unittest

from wslib import ROOT, WSTestCase


class InstallCase(WSTestCase):

    def setUp(self):
        super().setUp()
        self.repo = self.sb.tmp / "repo"
        for part in ("adapters", "dist", "scripts"):
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
                store = self.ext / harness / "handoff"
                store.mkdir(parents=True)
                (store / "marker.txt").write_text("store copy\n")
                os.symlink(store, skills / "handoff")
                (skills / "tdd").mkdir()
                (skills / "tdd" / "SKILL.md").write_text("---\nname: tdd\n---\n")

                r = self.install(harness)
                b = self.backup(harness)
                self.assertEqual((b / "skills" / "grilling" / "mine.md").read_text(), "my own notes\n",
                                 "the replaced skill was backed up first")
                self.assertTrue((skills / "grilling" / "SKILL.md").is_file())
                self.assertEqual((skills / "grilling" / "SKILL.md").read_bytes(),
                                 (ROOT / "dist" / harness / "skills" / "grilling" / "SKILL.md").read_bytes())

                self.assertTrue(os.path.islink(b / "skills" / "handoff"), "the link itself is backed up")
                self.assertEqual(os.readlink(b / "skills" / "handoff"), str(store))
                self.assertFalse(os.path.islink(skills / "handoff"), "the link is replaced by a real dir")
                self.assertTrue((skills / "handoff" / "SKILL.md").is_file())
                self.assertEqual(sorted(p.name for p in store.iterdir()), ["marker.txt"],
                                 "nothing was written through the link")
                self.assertEqual((store / "marker.txt").read_text(), "store copy\n")

                self.assertEqual((b / "skills" / "tdd" / "SKILL.md").read_text(), "---\nname: tdd\n---\n",
                                 "the upstream tdd skill was backed up before it was replaced")
                self.assertEqual((skills / "tdd" / "SKILL.md").read_bytes(),
                                 (ROOT / "dist" / harness / "skills" / "tdd" / "SKILL.md").read_bytes())

    def backup(self, harness):
        backups = sorted((self.dest(harness) / "backups").glob("agentic-workflow-*"))
        self.assertTrue(backups, "a backup was made")
        return backups[-1]


if __name__ == "__main__":
    unittest.main()
