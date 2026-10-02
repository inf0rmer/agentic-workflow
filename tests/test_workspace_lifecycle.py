"""Workspace lifecycle: list status and close."""

import json
import os
import shutil
import unittest
from pathlib import Path

from wslib import LIST_LINE, WSTestCase, read_bytes, realpath


class LifecycleCase(WSTestCase):

    def pushed_ws(self, feature, *names, remotes=True, commit_date=None):
        """ws `feature` with fresh repos attached, each with a local bare origin (main pushed)."""
        ws = self.new_ws(feature)
        repos, wts = {}, {}
        for name in names:
            repo = self.sb.new_repo(name, parent=self.sb.tmp / "src" / feature, commit_date=commit_date)
            if remotes:
                self.sb.add_remote(repo)
            repos[name] = repo
            wts[name] = self.attach(repo, feature)
        return ws, repos, wts

    def close(self, feature, *extra, cwd=None, env=None):
        return self.sb.tg("workspace", "close", feature, *extra, cwd=cwd or self.sb.scratch, env=env)

    def status_of(self, feature, env=None):
        r = self.sb.tg("workspace", "list", env=env)
        for line in r.out.splitlines():
            m = LIST_LINE.match(line)
            if m and m.group("feature") == feature:
                return m.group("status"), r
        self.fail("no list line for %s\n%s" % (feature, self.show(r)))

    def everything(self, *repos):
        """Snapshot of all three roots plus the given source repos' state, refs and worktree lists."""
        return (self.sb.roots_snapshot(),
                [self.sb.source_state(r) if Path(r).exists() else None for r in repos],
                [self.sb.refs(r) if Path(r).exists() else None for r in repos],
                [self.sb.worktrees(r) if Path(r).exists() else None for r in repos])

    def admin_entries(self, repo):
        d = Path(self.sb.git(repo, "rev-parse", "--git-common-dir"))
        d = d if d.is_absolute() else Path(repo) / d
        w = d / "worktrees"
        return sorted(os.listdir(w)) if w.exists() else []


class TestListStatus(LifecycleCase):

    def test_status_merged_single_repo(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        self.assertEqual(self.status_of("f")[0], "active", "a just-attached branch is not merged")
        self.sb.commit_file(wts["A"], "pkg/new.go", "package pkg\n", "work")
        self.assertEqual(self.status_of("f")[0], "active")
        self.sb.git(repos["A"], "merge", "-q", "feat/f")
        status, r = self.status_of("f")
        self.assertEqual(status, "merged", self.show(r))
        self.code(r, 0)

    def test_status_merged_via_remote_tracking_base(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        self.sb.commit_file(wts["A"], "pkg/new.go", "package pkg\n", "work")
        self.sb.git(wts["A"], "push", "-q", "origin", "feat/f:main")
        self.sb.git(repos["A"], "fetch", "-q", "origin")
        self.assertNotEqual(self.sb.git(repos["A"], "rev-parse", "main"), self.sb.git(wts["A"], "rev-parse", "HEAD"))
        self.assertEqual(self.status_of("f")[0], "merged")

    def test_status_deleted_branch_counts_as_merged(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        self.sb.git(repos["A"], "update-ref", "-d", "refs/heads/feat/f")
        self.assertEqual(self.status_of("f")[0], "merged")

    def test_status_partly_merged_is_not_merged(self):
        ws, repos, wts = self.pushed_ws("g", "A", "B")
        for n in ("A", "B"):
            self.sb.commit_file(wts[n], "pkg/new.go", "package pkg\n", "work")
        self.sb.git(repos["A"], "merge", "-q", "feat/g")
        self.assertEqual(self.status_of("g")[0], "active")

    def test_status_missing_source_is_stale(self):
        ws, repos, wts = self.pushed_ws("g", "A", "B")
        shutil.move(str(repos["B"]), str(self.sb.tmp / "B-moved"))
        status, r = self.status_of("g")
        self.assertEqual(status, "stale")
        self.code(r, 0)
        self.no_traceback(r)

    def test_workspace_without_repos_is_never_merged(self):
        self.new_ws("e")
        self.assertEqual(self.status_of("e")[0], "active")
        self.assertEqual(self.status_of("e", env={"TEAM_STALE_DAYS": "0"})[0], "stale")


class TestClose(LifecycleCase):

    def test_close_removes_worktrees_and_archives_artifacts(self):
        A = self.sb.new_repo("A")
        B = self.sb.new_repo("B")
        self.sb.add_remote(A)
        self.sb.add_remote(B)
        pre_attach = {n: self.sb.source_state(p) for n, p in (("A", A), ("B", B))}
        ws = self.new_ws("f")
        wtA = self.attach(A)
        wtB = self.attach(B)
        (ws / "plan.md").write_text("# plan\n")
        (ws / "adr").mkdir()
        (ws / "adr" / "1.md").write_text("# decision 1\n")
        wj = (ws / "workspace.json").read_bytes()
        refs_before = {n: self.sb.refs(p) for n, p in (("A", A), ("B", B))}
        cfg_before = {n: read_bytes(p / ".git" / "config") for n, p in (("A", A), ("B", B))}

        r = self.close("f")
        self.ok(r)
        self.no_traceback(r)
        archive = realpath(self.sb.ar_root / "f")
        self.has(r.out, "team-guard: removed worktree %s" % realpath(wtA), r)
        self.has(r.out, "team-guard: removed worktree %s" % realpath(wtB), r)
        self.has(r.out, "team-guard: closed f; artifacts archived at %s" % archive, r)
        for n, p in (("A", A), ("B", B)):
            # The worktree and its admin dir are gone.
            self.assertEqual(self.sb.worktrees(p), [realpath(p)])
            self.assertEqual(self.admin_entries(p), [], "no admin dir (and so no pointer) left for %s" % n)
            # Branches survive the close.
            self.assertEqual(self.sb.refs(p), refs_before[n])
            self.assertIn("refs/heads/feat/f", self.sb.refs(p))
            # The source checkout is as it was before attach.
            self.assertEqual(read_bytes(p / ".git" / "config"), cfg_before[n])
            self.assertEqual(self.sb.source_state(p), pre_attach[n])
        # The workspace dir is archived, artifacts included.
        self.assertFalse(ws.exists())
        self.assertEqual(Path(archive, "plan.md").read_text(), "# plan\n")
        self.assertEqual(Path(archive, "adr", "1.md").read_text(), "# decision 1\n")
        self.assertEqual(Path(archive, "workspace.json").read_bytes(), wj)
        meta = json.loads(Path(archive, "archive.json").read_text())
        self.assertEqual(meta["feature"], "f")
        self.assertEqual(meta["workspace"], realpath(ws))
        self.assertIs(meta["forced"], False)
        self.assertEqual(meta["discarded"], [])
        self.assertRegex(meta["closed"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertFalse(Path(archive, "repos").exists())

    def test_close_archive_name_collision_gets_suffix(self):
        self.new_ws("f")
        self.ok(self.close("f"))
        self.new_ws("f")
        (self.sb.ws_dir("f") / "notes.md").write_text("second\n")
        r = self.ok(self.close("f"))
        second = realpath(self.sb.ar_root / "f+2")
        self.has(r.out, "artifacts archived at %s" % second, r)
        self.assertEqual(Path(second, "notes.md").read_text(), "second\n")
        self.assertTrue((self.sb.ar_root / "f" / "archive.json").exists())

    def test_close_refuses_uncommitted_changes(self):
        ws, repos, wts = self.pushed_ws("f", "A", "B")
        (ws / "plan.md").write_text("# plan\n")
        for kind in ("untracked", "modified"):
            with self.subTest(kind=kind):
                if kind == "untracked":
                    (wts["A"] / "scratch.txt").write_text("wip\n")
                else:
                    (wts["A"] / "pkg" / "a.go").write_text("package pkg // wip\n")
                before = self.everything(repos["A"], repos["B"])
                r = self.close("f")
                self.code(r, 1)
                self.has(r.err, "team-guard: can't close f:", r)
                self.has(r.err, "  %s: uncommitted changes" % realpath(wts["A"]), r)
                self.has(r.err, "--force", r)
                self.lacks(r.err, realpath(wts["B"]) + ":", r)
                self.assertEqual(self.everything(repos["A"], repos["B"]), before)
                self.sb.git(wts["A"], "checkout", "-q", "--", ".")
                self.sb.git(wts["A"], "clean", "-qfd")

    def test_close_refuses_unpushed_commits(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        self.sb.commit_file(wts["A"], "pkg/new.go", "package pkg\n", "unpushed")
        before = self.everything(repos["A"])
        r = self.close("f")
        self.code(r, 1)
        self.has(r.err, "  %s: unpushed commits" % realpath(wts["A"]), r)
        self.assertEqual(self.everything(repos["A"]), before)
        # pushing makes it safe
        self.sb.git(wts["A"], "push", "-q", "origin", "feat/f")
        self.ok(self.close("f"))

    def test_close_treats_no_remotes_as_unpushed(self):
        ws, repos, wts = self.pushed_ws("f", "C", remotes=False)
        before = self.everything(repos["C"])
        r = self.close("f")
        self.code(r, 1)
        self.has(r.err, "  %s: unpushed commits" % realpath(wts["C"]), r)
        self.assertEqual(self.everything(repos["C"]), before)

    def test_close_lists_every_unsafe_worktree(self):
        ws, repos, wts = self.pushed_ws("f", "A", "B")
        (wts["A"] / "scratch.txt").write_text("wip\n")
        self.sb.commit_file(wts["B"], "pkg/new.go", "package pkg\n", "unpushed")
        before = self.everything(repos["A"], repos["B"])
        r = self.close("f")
        self.code(r, 1)
        self.assertEqual(r.err.count("can't close f:"), 1, self.show(r))
        self.has(r.err, "  %s: uncommitted changes" % realpath(wts["A"]), r)
        self.has(r.err, "  %s: unpushed commits" % realpath(wts["B"]), r)
        self.assertEqual(self.everything(repos["A"], repos["B"]), before)

    def test_close_force_discards_unsafe_work(self):
        ws, repos, wts = self.pushed_ws("f", "A", "B")
        (wts["A"] / "scratch.txt").write_text("wip\n")
        b_tip = self.sb.commit_file(wts["B"], "pkg/new.go", "package pkg\n", "unpushed")
        r = self.close("f", "--force")
        self.ok(r)
        self.has(r.out, "team-guard: discarded uncommitted changes in %s" % realpath(wts["A"]), r)
        self.has(r.out, "team-guard: discarded unpushed commits in %s (branch feat/f kept)" % realpath(wts["B"]), r)
        self.assertFalse(wts["A"].exists())
        self.assertFalse(wts["B"].exists())
        self.assertEqual(self.sb.git(repos["B"], "rev-parse", "refs/heads/feat/f"), b_tip)
        self.assertEqual(self.sb.worktrees(repos["A"]), [realpath(repos["A"])])
        meta = json.loads((self.sb.ar_root / "f" / "archive.json").read_text())
        self.assertIs(meta["forced"], True)
        reasons = sorted((d["worktree"], d["reason"]) for d in meta["discarded"])
        self.assertIn((realpath(wts["A"]), "uncommitted changes"), reasons)
        self.assertIn((realpath(wts["B"]), "unpushed commits"), reasons)

    def test_close_unknown_feature_fails(self):
        self.pushed_ws("f", "A")
        before = self.sb.roots_snapshot()
        r = self.close("nope")
        self.code(r, 1)
        self.has(r.err, "no workspace named nope", r)
        self.assertEqual(self.sb.roots_snapshot(), before)

    def test_close_refuses_from_inside_the_workspace(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        (ws / "notes").mkdir()
        before = self.everything(repos["A"])
        for cwd in (ws, ws / "notes", ws / "repos", wts["A"], wts["A"] / "pkg"):
            for extra in ((), ("--force",)):
                with self.subTest(cwd=str(cwd), extra=extra):
                    r = self.close("f", *extra, cwd=cwd)
                    self.code(r, 1)
                    self.has(r.err, "can't close f from inside it", r)
                    self.has(r.err, "run it from elsewhere", r)
                    self.assertEqual(self.everything(repos["A"]), before)

    def test_unrecorded_dir_in_repos_refused_even_with_force(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        (ws / "repos" / "X").mkdir()
        before = self.everything(repos["A"])
        for extra in ((), ("--force",)):
            with self.subTest(extra=extra):
                r = self.close("f", *extra)
                self.code(r, 1)
                # An unrecorded entry is named as such, not as "‹path›: not an attached worktree".
                self.has(r.err, "unrecorded entry in repos/: X", r)
                self.assertEqual(self.everything(repos["A"]), before)

    def test_close_prunes_worktree_already_gone(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        shutil.rmtree(wts["A"])
        r = self.close("f")
        self.ok(r)
        self.has(r.out, "team-guard: pruned missing worktree %s" % realpath(wts["A"]), r)
        self.assertEqual(self.sb.worktrees(repos["A"]), [realpath(repos["A"])])
        self.assertEqual(self.admin_entries(repos["A"]), [])
        self.assertFalse(ws.exists())

    def test_close_handles_worktree_and_source_gone(self):
        ws, repos, wts = self.pushed_ws("g", "A", "B")
        shutil.rmtree(wts["B"])
        shutil.rmtree(repos["B"])
        r = self.close("g")
        self.ok(r)
        self.has(r.err, "team-guard: warning: source repo %s is missing; skipped B" % realpath(repos["B"]), r)
        self.has(r.out, "team-guard: removed worktree %s" % realpath(wts["A"]), r)
        self.assertFalse(ws.exists())

    def test_source_missing_with_worktree_present_needs_force(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        shutil.move(str(repos["A"]), str(self.sb.tmp / "A-moved"))
        before = self.sb.roots_snapshot()
        r = self.close("f")
        self.code(r, 1)
        self.has(r.err, "  %s: source repository missing" % realpath(wts["A"]), r)
        self.assertEqual(self.sb.roots_snapshot(), before)
        r = self.close("f", "--force")
        self.ok(r)
        self.has(r.out, "discarded source repository missing in %s" % realpath(wts["A"]), r)
        self.assertFalse(ws.exists())

    def test_close_partial_failure_then_rerun_completes(self):
        ws, repos, wts = self.pushed_ws("f", "A", "B")
        (ws / "plan.md").write_text("# plan\n")
        self.sb.git(repos["B"], "worktree", "lock", str(wts["B"]))
        r = self.close("f")
        self.code(r, 1)
        self.has(r.err, "team-guard: git worktree remove failed for %s" % realpath(wts["B"]), r)
        self.assertFalse(wts["A"].exists(), "worktrees removed before the failure stay removed")
        self.assertTrue(wts["B"].exists())
        data = self.sb.load_ws("f")
        self.assertEqual([e["name"] for e in data["repos"]], ["B"])
        self.assertIn("closing", data)
        self.assertTrue((ws / "plan.md").exists(), "artifacts are untouched until every worktree is gone")
        C = self.sb.new_repo("C")
        a = self.sb.tg("workspace", "attach", str(C), cwd=ws)
        self.code(a, 1)
        self.has(a.err, "is being closed", a)
        self.sb.git(repos["B"], "worktree", "unlock", str(wts["B"]))
        r = self.close("f")
        self.ok(r)
        self.has(r.out, "artifacts archived at %s" % realpath(self.sb.ar_root / "f"), r)
        self.assertFalse((self.sb.ar_root / "f+2").exists())
        self.assertEqual((self.sb.ar_root / "f" / "plan.md").read_text(), "# plan\n")
        self.assertFalse(ws.exists())


if __name__ == "__main__":
    unittest.main()
