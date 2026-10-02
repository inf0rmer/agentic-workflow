"""Tests added after mutation testing: each one pins a documented behavior that a
surviving mutant showed the other suites didn't check."""

import json
import shutil
import unittest
from pathlib import Path

from wslib import WSTestCase, realpath, tree_snapshot


class TestDefaultRoots(WSTestCase):

    def test_roots_default_under_home(self):
        # Without the env vars the roots are ~/.agentic-workflow/{work,archive}.
        unset = ("TEAM_WORKSPACE_ROOT", "TEAM_ARCHIVE_ROOT")
        home = self.sb.home
        base = home / ".agentic-workflow"
        try:
            r = self.sb.tg("workspace", "new", "f", unset=unset)
            self.ok(r)
            ws = base / "work" / "f"
            self.assertTrue((ws / "workspace.json").is_file(), self.show(r))
            self.ok(self.sb.tg("workspace", "close", "f", unset=unset))
            self.assertTrue((base / "archive" / "f" / "archive.json").is_file())
            self.assertFalse(self.sb.ws_root.exists(), "the sandbox roots were not used")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_empty_env_var_means_default(self):
        home = self.sb.home
        base = home / ".agentic-workflow"
        try:
            self.ok(self.sb.tg("workspace", "new", "f", env={"TEAM_WORKSPACE_ROOT": ""}))
            self.assertTrue((base / "work" / "f" / "workspace.json").is_file())
        finally:
            shutil.rmtree(base, ignore_errors=True)


class TestWorkspaceLookupEdges(WSTestCase):

    def test_dot_dirs_under_the_root_are_not_workspaces(self):
        # A workspace W is a direct child of <WS> whose name doesn't start with ".".
        d = self.sb.ws_root / ".trash" / "x"
        d.mkdir(parents=True)
        (self.sb.ws_root / ".trash" / "workspace.json").write_text(json.dumps({"feature": ".trash", "repos": []}))
        r = self.sb.tg("workspace-path", cwd=d)
        self.code(r, 1)
        self.assertEqual(r.out, "")
        r = self.sb.tg("workspace", "attach", str(self.sb.new_repo("A")), cwd=d)
        self.code(r, 1)
        self.has(r.err, "no workspace found", r)

    def test_workspace_json_of_the_wrong_shape(self):
        # A workspace.json that parses but isn't a workspace object is reported, never used.
        for bad in ("[]", '{"feature": "f"}', '{"feature": "f", "repos": 5}', '"text"'):
            with self.subTest(content=bad):
                ws = self.sb.ws_dir("f")
                if ws.exists():
                    shutil.rmtree(ws)
                self.new_ws("f")
                (ws / "workspace.json").write_text(bad)
                name = realpath(ws) + "/workspace.json"
                for args, cwd in ((("workspace", "show", "f"), self.sb.scratch), (("sign",), ws),
                                  (("workspace", "close", "f"), self.sb.scratch)):
                    r = self.sb.tg(*args, cwd=cwd)
                    self.nonzero(r, " ".join(args))
                    self.has(r.err, name, r)
                    self.no_traceback(r)
                self.assertEqual((ws / "workspace.json").read_text(), bad)
                r = self.sb.tg("workspace", "list")
                self.code(r, 1)
                self.assertIn("f\tinvalid\t-\t-", r.out.splitlines())

    def test_feature_names_cannot_escape_the_root(self):
        # `show`/`close`/`attach --feature` take a feature name, never a path.
        self.new_ws("f")
        (self.sb.tmp / "workspace.json").write_text(json.dumps({"feature": "x", "branch": "b", "repos": []}))
        before = tree_snapshot(self.sb.tmp, skip=("home",))
        for name in ("..", ".", "../ws/f", "f/..", "f/../f"):
            for args in (("workspace", "show", name), ("workspace", "close", name), ("workspace", "close", name, "--force")):
                with self.subTest(args=args):
                    r = self.sb.tg(*args)
                    self.code(r, 1)
                    self.has(r.err, "no workspace named", r)
            r = self.sb.tg("workspace", "attach", str(self.sb.scratch), "--feature", name)
            self.code(r, 1)
            self.has(r.err, "no workspace named", r)
        self.assertEqual(tree_snapshot(self.sb.tmp, skip=("home",)), before)

    def test_dangling_pointer_refuses_attach_and_show(self):
        A = self.sb.new_repo("A")
        B = self.sb.new_repo("B")
        self.new_ws("f")
        wt = self.attach(A)
        gd = Path(self.sb.git(wt, "rev-parse", "--absolute-git-dir"))
        (gd / "team-workspace").write_text("/nonexistent\n")
        before = tree_snapshot(self.sb.ws_root)
        for args in (("workspace", "attach", str(B)), ("workspace", "show")):
            with self.subTest(args=args):
                r = self.sb.tg(*args, cwd=wt)
                self.code(r, 1)
                self.has(r.err, "dangling", r)
                self.no_traceback(r)
        self.assertEqual(tree_snapshot(self.sb.ws_root), before)
        self.assertEqual(len(self.sb.worktrees(B)), 1)


if __name__ == "__main__":
    unittest.main()
