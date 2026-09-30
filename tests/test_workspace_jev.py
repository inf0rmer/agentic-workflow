"""ask-jev log placement inside and outside workspaces (JEV-1..JEV-3). Offline only:
ask-jev is run with --print-log-path or with input that fails validation."""

import json
import os
import time
import unittest
from pathlib import Path

from wslib import WSTestCase, realpath

LIMIT = 5.0  # JEV-3: each call returns quickly because nothing touches the network


class JevCase(WSTestCase):

    def timed_jev(self, *args, cwd=None, input=None):
        start = time.time()
        r = self.sb.jev(*args, cwd=cwd, input=input)
        elapsed = time.time() - start
        self.assertLess(elapsed, LIMIT, "ask-jev %s took %.1fs" % (" ".join(args), elapsed))
        return r

    def log_path(self, cwd, *extra):
        r = self.timed_jev("--print-log-path", *extra, cwd=cwd)
        self.ok(r, "ask-jev --print-log-path")
        self.assertTrue(r.out.endswith("\n"), self.show(r))
        return r.out[:-1]

    def git_dir(self, wt):
        return self.sb.git(wt, "rev-parse", "--absolute-git-dir")


class TestJevWorkspaceLog(JevCase):

    def test_jev1_log_path_matches_workspace_path(self):
        A = self.sb.new_repo("A")
        ws = self.new_ws("f")
        wt = self.attach(A)
        (ws / "notes").mkdir()
        for cwd in (ws, ws / "notes", wt, wt / "pkg"):
            with self.subTest(cwd=str(cwd)):
                w = self.ok(self.sb.tg("workspace-path", cwd=cwd)).out.strip()
                self.assertEqual(self.log_path(cwd), w + "/jev-decisions.md")
                self.assertEqual(w, realpath(ws))

    def test_jev1_failed_validation_logs_into_workspace(self):
        A = self.sb.new_repo("A")
        ws = self.new_ws("f")
        wt = self.attach(A)
        expected = realpath(ws) + "/jev-decisions.md"
        for n, cwd in enumerate((wt, wt / "pkg", ws), start=1):
            with self.subTest(cwd=str(cwd)):
                r = self.timed_jev(cwd=cwd, input="{}")
                data = json.loads(r.out)
                self.assertEqual(data.get("verdict"), "ESCALATE", self.show(r))
                self.assertIn("error", data)
                self.assertEqual(data.get("log"), expected)
                text = Path(expected).read_text()
                records = [l for l in text.splitlines() if l.startswith("## ")]
                self.assertEqual(len(records), n, text)
                self.assertEqual(text.count("verdict: **ESCALATE**"), n, text)
        self.assertFalse(Path(self.git_dir(wt), "jev-decisions.md").exists(), "nothing in the worktree's git dir")
        self.assertFalse((A / ".git" / "jev-decisions.md").exists())
        self.assertEqual(self.sb.git(wt, "status", "--porcelain", "--ignored"), "")


class TestJevOutsideWorkspace(JevCase):

    def test_jev2_unattached_repo_uses_git_dir(self):
        R = self.sb.new_repo("R")
        self.assertEqual(self.log_path(R), self.git_dir(R) + "/jev-decisions.md")
        self.assertEqual(self.log_path(R / "pkg"), self.git_dir(R) + "/jev-decisions.md")

    def test_jev2_source_checkout_of_attached_repo_uses_git_dir(self):
        A = self.sb.new_repo("A")
        self.new_ws("f")
        self.attach(A)
        self.assertEqual(self.log_path(A), self.git_dir(A) + "/jev-decisions.md")

    def test_jev2_plain_dir_has_no_log(self):
        self.assertEqual(self.log_path(self.sb.scratch), "")

    def test_jev2_dangling_pointer_falls_back_to_git_dir(self):
        A = self.sb.new_repo("A")
        self.new_ws("f")
        wt = self.attach(A)
        Path(self.git_dir(wt), "team-workspace").write_text("/nonexistent\n")
        self.assertEqual(self.sb.tg("workspace-path", cwd=wt).code, 1)
        self.assertEqual(self.log_path(wt), self.git_dir(wt) + "/jev-decisions.md")

    def test_jev2_workspace_dir_without_json_has_no_log(self):
        ws = self.new_ws("f")
        (ws / "workspace.json").unlink()
        self.assertEqual(self.log_path(ws), "")

    def test_jev2_explicit_log_options_win(self):
        R = self.sb.new_repo("R")
        A = self.sb.new_repo("A")
        ws = self.new_ws("f")
        wt = self.attach(A)
        custom = str(self.sb.tmp / "custom-log.md")
        for cwd in (R, self.sb.scratch, ws, wt):
            with self.subTest(cwd=str(cwd)):
                self.assertEqual(self.log_path(cwd, "--log", custom), custom)
                self.assertEqual(self.log_path(cwd, "--no-log"), "")
        self.assertFalse(Path(custom).exists(), "--print-log-path writes nothing")
        self.assertFalse((ws / "jev-decisions.md").exists())

    def test_jev3_no_api_key_needed(self):
        self.assertNotIn("OPENROUTER_API_KEY", self.sb.env)
        self.assertEqual(self.sb.env.get("HTTPS_PROXY"), "http://127.0.0.1:9")
        r = self.timed_jev(cwd=self.sb.scratch, input="{}")
        self.assertEqual(json.loads(r.out).get("verdict"), "ESCALATE", self.show(r))


if __name__ == "__main__":
    unittest.main()
