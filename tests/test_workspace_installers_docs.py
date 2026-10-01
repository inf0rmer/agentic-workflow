"""The harness installers put the workflow docs next to the tools.

The rendered instructions point at the installed docs, so each installer copies every guide
from the repo's docs/ into the harness's team dir, beside bin/.
"""

import shutil
import unittest

from test_workspace_installers import InstallCase
from wslib import ROOT


class TestInstalledDocs(InstallCase):

    def setUp(self):
        super().setUp()
        shutil.copytree(ROOT / "docs", self.repo / "docs", symlinks=True)

    def test_each_installer_copies_the_docs_into_its_team_dir(self):
        guides = sorted(p.name for p in (ROOT / "docs").glob("*.md"))
        self.assertIn("pr-loop.md", guides, "the repo has the PR loop guide")
        for harness in ("claude-code", "codex"):
            with self.subTest(harness=harness):
                self.install(harness)
                team = self.dest(harness) / "team"
                self.assertTrue((team / "bin" / "team-guard").is_file())
                docs = team / "docs"
                self.assertTrue(docs.is_dir(), "%s: no %s" % (harness, docs))
                installed = sorted(p.name for p in docs.glob("*.md")) if docs.is_dir() else []
                self.assertEqual(installed, guides)


if __name__ == "__main__":
    unittest.main()
