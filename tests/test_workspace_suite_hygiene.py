"""This repo's own tests describe behavior, not the documents that planned it.

A test named after a criterion number or a review round tells a later reader nothing, so
test names, file names, docstrings and comments under tests/ state the behavior, and the
reference check finds nothing there.
"""

import ast
import os
import re
import subprocess
import unittest

from wstools import ROOT, GUARD

TESTS = ROOT / "tests"


class TestSuiteHygiene(unittest.TestCase):

    def test_tests_are_named_by_behavior_and_still_discovered(self):
        numbered = []
        for f in sorted(TESTS.glob("*.py")):
            for node in ast.walk(ast.parse(f.read_text())):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and re.match(r"test_ac\d", node.name):
                    numbered.append("%s:%s" % (f.name, node.name))
        self.assertEqual(numbered, [])
        self.assertEqual([p.name for p in TESTS.iterdir() if re.search(r"round\d", p.name)], [])
        run_sh = (TESTS / "run.sh").read_text()
        for pattern, glob in (("test_workspace_*.py", "test_workspace_*.py"), ("test_workflow_*.py", "test_workflow_*.py")):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, run_sh)
                self.assertTrue(list(TESTS.glob(glob)))

    def test_reference_check_finds_nothing_under_tests(self):
        root = subprocess.run(["git", "-C", str(ROOT), "rev-list", "--max-parents=0", "HEAD"],
                              capture_output=True, text=True).stdout.split()
        self.assertTrue(root, "harness: the repo has a first commit")
        env = {k: v for k, v in os.environ.items() if k != "TEAM_REFS_CONFIG"}
        p = subprocess.run([str(GUARD), "refs", "--base", root[-1]], cwd=str(ROOT), capture_output=True,
                           text=True, env=env, timeout=120)
        self.assertIn(p.returncode, (0, 2), "refs ran\n%s%s" % (p.stdout, p.stderr))
        under_tests = [l for l in p.stderr.splitlines() if re.match(r"^(?:[^\s:]+: )?tests/", l)]
        self.assertEqual(under_tests, [])


if __name__ == "__main__":
    unittest.main()
