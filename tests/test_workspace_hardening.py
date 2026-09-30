"""Tests added after mutation testing: each one pins a documented behavior that a
surviving mutant showed the other suites didn't check."""

import json
import os
import shutil
import unittest
from pathlib import Path

from wslib import WSTestCase, realpath, tree_snapshot


class TestDefaultRoots(WSTestCase):

    def test_roots_default_under_home(self):
        # Without the env vars the roots are ~/.agentic-workflow/{work,holdout,archive}.
        unset = ("TEAM_WORKSPACE_ROOT", "TEAM_HOLDOUT_ROOT", "TEAM_ARCHIVE_ROOT")
        home = self.sb.home
        base = home / ".agentic-workflow"
        try:
            r = self.sb.tg("workspace", "new", "f", unset=unset)
            self.ok(r)
            ws = base / "work" / "f"
            self.assertTrue((ws / "workspace.json").is_file(), self.show(r))
            r = self.ok(self.sb.tg("holdout-path", cwd=ws, unset=unset))
            self.assertEqual(r.out.strip(), realpath(base / "holdout" / "f"))
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


class TestHoldoutRootSymlink(WSTestCase):

    def test_symlinked_holdout_root(self):
        real = self.sb.tmp / "real-holdout"
        real.mkdir()
        link = self.sb.tmp / "holdout-link"
        os.symlink(real, link)
        env = {"TEAM_HOLDOUT_ROOT": str(link)}
        # Inside a workspace, printed paths are realpaths.
        ws = self.new_ws("f")
        r = self.ok(self.sb.tg("holdout-path", cwd=ws, env=env))
        self.assertEqual(r.out, realpath(real / "f") + "\n")
        # Outside one, the legacy output is unchanged: the root as given.
        repo = self.sb.new_repo("repo", branch="feat/x")
        r = self.ok(self.sb.tg("holdout-path", cwd=repo, env=env))
        self.assertEqual(r.out, str(link) + "/repo/feat-x\n")
        # The engineer is denied through either spelling.
        for p in (link / "f" / "s.md", real / "f" / "s.md", link / "repo" / "feat-x" / "s.md"):
            ev = {"cwd": str(ws), "agent_type": "software-engineer", "tool_name": "Read", "tool_input": {"file_path": str(p)}}
            r = self.sb.tg("hook-pretool", input=json.dumps(ev), env=env)
            self.assertIn('"deny"', r.out, "%s\n%s" % (p, self.show(r)))


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
                for args, cwd in ((("workspace", "show", "f"), self.sb.scratch), (("verify",), ws),
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


class TestVerifyEdges(WSTestCase):

    def hooks_dir(self, repo):
        p = Path(self.sb.git(repo, "rev-parse", "--git-path", "hooks"))
        return p if p.is_absolute() else Path(repo) / p

    def test_worktree_whose_repository_is_gone(self):
        # verify reports "  ‹repo-name›: repository unavailable: ‹worktree›".
        A = self.sb.new_repo("A")
        B = self.sb.new_repo("B")
        ws = self.new_ws("f")
        self.attach(A)
        wtB = self.attach(B)
        self.ok(self.sb.tg("seal", "pkg/a_test.go", cwd=self.sb.wt("f", "A")))
        shutil.move(str(B), str(self.sb.tmp / "B-moved"))
        r = self.sb.tg("verify", cwd=ws)
        self.code(r, 2)
        self.has(r.err, "  B: repository unavailable: %s" % realpath(wtB), r)
        self.lacks(r.err, "A:", r)
        t = self.sb.tg("hook-task", input=json.dumps({"cwd": str(ws)}))
        self.code(t, 2)
        self.has(t.err, "repository unavailable", t)

    def test_workspace_verify_warns_when_a_repos_hook_is_gone(self):
        # Same warning as single-repo verify (the same output either way), per attached repo that has seals.
        A = self.sb.new_repo("A")
        B = self.sb.new_repo("B")
        ws = self.new_ws("f")
        wtA = self.attach(A)
        self.attach(B)
        self.ok(self.sb.tg("seal", "pkg/a_test.go", cwd=wtA))
        r = self.ok(self.sb.tg("verify", cwd=ws))
        self.assertEqual(r.err, "")
        (self.hooks_dir(wtA) / "pre-commit").unlink()
        r = self.ok(self.sb.tg("verify", cwd=ws))
        self.has(r.err, "not installed", r)
        self.has(r.err, "A", r)
        self.lacks(r.err, " B", r)

    def test_no_hook_warning_without_seals(self):
        # An unsealed repo without hooks verifies silently, as today.
        R = self.sb.new_repo("R")
        r = self.ok(self.sb.tg("verify", cwd=R))
        self.assertEqual((r.out, r.err), ("team-guard: all sealed tests unchanged\n", ""))
        ws = self.new_ws("f")
        self.attach(self.sb.new_repo("A"))
        r = self.ok(self.sb.tg("verify", cwd=ws))
        self.assertEqual(r.err, "")

    def test_verify_from_a_subdirectory_finds_the_hook(self):
        R = self.sb.new_repo("R")
        self.ok(self.sb.tg("seal", "pkg/a_test.go", cwd=R))
        r = self.ok(self.sb.tg("verify", cwd=R / "pkg"))
        self.assertEqual((r.out, r.err), ("team-guard: all sealed tests unchanged\n", ""))

    def test_hook_events_without_cwd_use_the_process_cwd(self):
        # As today: an event with no "cwd" is checked where the hook runs.
        R = self.sb.new_repo("R")
        self.ok(self.sb.tg("seal", "pkg/a_test.go", cwd=R))
        (R / "pkg" / "a_test.go").write_text("// weakened\n")
        t = self.sb.tg("hook-task", input="{}", cwd=R)
        self.code(t, 2)
        self.has(t.err, "pkg/a_test.go", t)
        s = self.sb.tg("hook-stop", input="{}", cwd=R)
        self.assertEqual(json.loads(s.out)["decision"], "block", self.show(s))


class TestGitHookArguments(WSTestCase):

    def test_unknown_action_or_hook_is_refused(self):
        R = self.sb.new_repo("R")
        hooks = R / ".git" / "hooks"
        before = tree_snapshot(hooks)
        for args in (("git-hook", "frobnicate"), ("git-hook", "status", "pre-rebase"),
                     ("git-hook", "install", "post-merge"), ("git-hook", "uninstall", "bogus")):
            with self.subTest(args=args):
                r = self.sb.tg(*args, cwd=R)
                self.nonzero(r)
                self.assertEqual(r.out, "")
                self.assertEqual(tree_snapshot(hooks), before)
        self.assertEqual(self.ok(self.sb.tg("git-hook", "status", "pre-push", cwd=R)).out, "missing\n")


class TestHoldoutPathEdges(WSTestCase):

    def test_empty_unmarked_dir_is_adopted(self):
        # Contract: only a *non-empty* unmarked <HO>/<feature>/ collides.
        ws = self.new_ws("f")
        (self.sb.ho_root / "f").mkdir(parents=True)
        r = self.ok(self.sb.tg("holdout-path", cwd=ws))
        self.assertEqual(r.out.strip(), realpath(self.sb.ho_root / "f"))
        self.assertEqual(json.loads((self.sb.ho_root / "f" / ".workspace").read_text())["feature"], "f")

    def test_outside_any_repo_or_workspace(self):
        r = self.sb.tg("holdout-path", cwd=self.sb.scratch)
        self.code(r, 1)
        self.has(r.err, "not a git repository", r)
        self.no_traceback(r)
        self.assertFalse(self.sb.ho_root.exists())


if __name__ == "__main__":
    unittest.main()
