"""Workspace lifecycle: list status, close, gc (spec section I)."""

import json
import os
import shutil
import subprocess
import time
import unittest
from pathlib import Path

from wslib import (GUARD, LIST_LINE, WSTestCase, backdate_tree, iso_days_ago,
                   read_bytes, realpath, tree_snapshot)


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

    def gc(self, *extra, cwd=None, env=None):
        return self.sb.tg("gc", *extra, cwd=cwd or self.sb.scratch, env=env)

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

    def test_ac74_merged_single_repo(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        self.assertEqual(self.status_of("f")[0], "active", "a just-attached branch is not merged")
        self.sb.commit_file(wts["A"], "pkg/new.go", "package pkg\n", "work")
        self.assertEqual(self.status_of("f")[0], "active")
        self.sb.git(repos["A"], "merge", "-q", "feat/f")
        status, r = self.status_of("f")
        self.assertEqual(status, "merged", self.show(r))
        self.code(r, 0)

    def test_ac74_merged_via_remote_tracking_base(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        self.sb.commit_file(wts["A"], "pkg/new.go", "package pkg\n", "work")
        self.sb.git(wts["A"], "push", "-q", "origin", "feat/f:main")
        self.sb.git(repos["A"], "fetch", "-q", "origin")
        self.assertNotEqual(self.sb.git(repos["A"], "rev-parse", "main"), self.sb.git(wts["A"], "rev-parse", "HEAD"))
        self.assertEqual(self.status_of("f")[0], "merged")

    def test_ac74_deleted_branch_counts_as_merged(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        self.sb.git(repos["A"], "update-ref", "-d", "refs/heads/feat/f")
        self.assertEqual(self.status_of("f")[0], "merged")

    def test_ac74_partly_merged_is_not_merged(self):
        ws, repos, wts = self.pushed_ws("g", "A", "B")
        for n in ("A", "B"):
            self.sb.commit_file(wts[n], "pkg/new.go", "package pkg\n", "work")
        self.sb.git(repos["A"], "merge", "-q", "feat/g")
        self.assertEqual(self.status_of("g")[0], "active")

    def test_ac74_missing_source_is_stale(self):
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

    def test_ac75_to_ac78_ac87_successful_close(self):
        A = self.sb.new_repo("A")
        B = self.sb.new_repo("B")
        self.sb.add_remote(A)
        self.sb.add_remote(B)
        pre_attach = {n: self.sb.source_state(p) for n, p in (("A", A), ("B", B))}
        ws = self.new_ws("f")
        wtA = self.attach(A)
        wtB = self.attach(B)
        ho = Path(self.ok(self.sb.tg("holdout-path", cwd=ws)).out.strip())
        (ho / "s.md").write_text("scenario\n")
        (ws / "spec.md").write_text("# spec\n")
        (ws / "adr").mkdir()
        (ws / "adr" / "1.md").write_text("# adr 1\n")
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
            # AC-75
            self.assertEqual(self.sb.worktrees(p), [realpath(p)])
            self.assertEqual(self.admin_entries(p), [], "no admin dir (and so no pointer) left for %s" % n)
            # AC-77
            self.assertEqual(self.sb.refs(p), refs_before[n])
            self.assertIn("refs/heads/feat/f", self.sb.refs(p))
            # AC-78
            self.assertEqual(read_bytes(p / ".git" / "config"), cfg_before[n])
            self.assertEqual(self.sb.source_state(p), pre_attach[n])
        # AC-76
        self.assertFalse(ho.exists())
        # AC-87
        self.assertFalse(ws.exists())
        self.assertEqual(Path(archive, "spec.md").read_text(), "# spec\n")
        self.assertEqual(Path(archive, "adr", "1.md").read_text(), "# adr 1\n")
        self.assertEqual(Path(archive, "workspace.json").read_bytes(), wj)
        meta = json.loads(Path(archive, "archive.json").read_text())
        self.assertEqual(meta["feature"], "f")
        self.assertEqual(meta["workspace"], realpath(ws))
        self.assertIs(meta["forced"], False)
        self.assertEqual(meta["discarded"], [])
        self.assertRegex(meta["closed"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertFalse(Path(archive, "repos").exists())

    def test_ac87_archive_name_collision(self):
        self.new_ws("f")
        self.ok(self.close("f"))
        self.new_ws("f")
        (self.sb.ws_dir("f") / "notes.md").write_text("second\n")
        r = self.ok(self.close("f"))
        second = realpath(self.sb.ar_root / "f+2")
        self.has(r.out, "artifacts archived at %s" % second, r)
        self.assertEqual(Path(second, "notes.md").read_text(), "second\n")
        self.assertTrue((self.sb.ar_root / "f" / "archive.json").exists())

    def test_ac79_uncommitted_changes_refused(self):
        ws, repos, wts = self.pushed_ws("f", "A", "B")
        self.ok(self.sb.tg("holdout-path", cwd=ws))
        (ws / "spec.md").write_text("# spec\n")
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

    def test_ac80_unpushed_commits_refused(self):
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

    def test_ac80_no_remotes_means_unpushed(self):
        ws, repos, wts = self.pushed_ws("f", "C", remotes=False)
        before = self.everything(repos["C"])
        r = self.close("f")
        self.code(r, 1)
        self.has(r.err, "  %s: unpushed commits" % realpath(wts["C"]), r)
        self.assertEqual(self.everything(repos["C"]), before)

    def test_ac81_every_unsafe_worktree_listed(self):
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

    def test_ac82_force(self):
        ws, repos, wts = self.pushed_ws("f", "A", "B")
        ho = Path(self.ok(self.sb.tg("holdout-path", cwd=ws)).out.strip())
        (wts["A"] / "scratch.txt").write_text("wip\n")
        b_tip = self.sb.commit_file(wts["B"], "pkg/new.go", "package pkg\n", "unpushed")
        r = self.close("f", "--force")
        self.ok(r)
        self.has(r.out, "team-guard: discarded uncommitted changes in %s" % realpath(wts["A"]), r)
        self.has(r.out, "team-guard: discarded unpushed commits in %s (branch feat/f kept)" % realpath(wts["B"]), r)
        self.assertFalse(wts["A"].exists())
        self.assertFalse(wts["B"].exists())
        self.assertFalse(ho.exists())
        self.assertEqual(self.sb.git(repos["B"], "rev-parse", "refs/heads/feat/f"), b_tip)
        self.assertEqual(self.sb.worktrees(repos["A"]), [realpath(repos["A"])])
        meta = json.loads((self.sb.ar_root / "f" / "archive.json").read_text())
        self.assertIs(meta["forced"], True)
        reasons = sorted((d["worktree"], d["reason"]) for d in meta["discarded"])
        self.assertIn((realpath(wts["A"]), "uncommitted changes"), reasons)
        self.assertIn((realpath(wts["B"]), "unpushed commits"), reasons)

    def test_ac83_unknown_feature(self):
        self.pushed_ws("f", "A")
        before = self.sb.roots_snapshot()
        r = self.close("nope")
        self.code(r, 1)
        self.has(r.err, "no workspace named nope", r)
        self.assertEqual(self.sb.roots_snapshot(), before)

    def test_ac84_refuses_from_inside(self):
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
                self.has(r.err, "  %s: not an attached worktree" % realpath(ws / "repos" / "X"), r)
                self.assertEqual(self.everything(repos["A"]), before)

    def test_ac85_worktree_already_gone(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        shutil.rmtree(wts["A"])
        r = self.close("f")
        self.ok(r)
        self.has(r.out, "team-guard: pruned missing worktree %s" % realpath(wts["A"]), r)
        self.assertEqual(self.sb.worktrees(repos["A"]), [realpath(repos["A"])])
        self.assertEqual(self.admin_entries(repos["A"]), [])
        self.assertFalse(ws.exists())

    def test_ac85_worktree_and_source_gone(self):
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

    def test_ac86_partial_failure_and_rerun(self):
        ws, repos, wts = self.pushed_ws("f", "A", "B")
        ho = Path(self.ok(self.sb.tg("holdout-path", cwd=ws)).out.strip())
        (ws / "spec.md").write_text("# spec\n")
        self.sb.git(repos["B"], "worktree", "lock", str(wts["B"]))
        r = self.close("f")
        self.code(r, 1)
        self.has(r.err, "team-guard: git worktree remove failed for %s" % realpath(wts["B"]), r)
        self.assertFalse(wts["A"].exists(), "worktrees removed before the failure stay removed")
        self.assertTrue(wts["B"].exists())
        data = self.sb.load_ws("f")
        self.assertEqual([e["name"] for e in data["repos"]], ["B"])
        self.assertIn("closing", data)
        self.assertTrue(ho.exists(), "holdouts are untouched until every worktree is gone")
        self.assertTrue((ws / "spec.md").exists(), "artifacts are untouched until every worktree is gone")
        g = self.gc()
        self.has(g.out, "would resume closing f", g)
        C = self.sb.new_repo("C")
        a = self.sb.tg("workspace", "attach", str(C), cwd=ws)
        self.code(a, 1)
        self.has(a.err, "is being closed", a)
        self.sb.git(repos["B"], "worktree", "unlock", str(wts["B"]))
        r = self.close("f")
        self.ok(r)
        self.has(r.out, "artifacts archived at %s" % realpath(self.sb.ar_root / "f"), r)
        self.assertFalse((self.sb.ar_root / "f+2").exists())
        self.assertEqual((self.sb.ar_root / "f" / "spec.md").read_text(), "# spec\n")
        self.assertFalse(ws.exists())
        self.assertFalse(ho.exists())

    def test_ac88_lock_and_jev_logs_archived(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        self.ok(self.sb.tg("seal", "pkg/a_test.go", cwd=wts["A"]))
        gd = Path(self.sb.git(wts["A"], "rev-parse", "--absolute-git-dir"))
        lock = (gd / "team-test-lock.json").read_bytes()
        (gd / "jev-decisions.md").write_text("## fake worktree log\n")
        wt_log = (gd / "jev-decisions.md").read_bytes()
        r = self.sb.jev(cwd=ws, input="{}")
        self.assertTrue((ws / "jev-decisions.md").exists(), "ask-jev logs into the workspace\n%s" % self.show(r))
        ws_log = (ws / "jev-decisions.md").read_bytes()
        self.ok(self.close("f"))
        ar = self.sb.ar_root / "f"
        self.assertEqual((ar / "jev-decisions.md").read_bytes(), ws_log)
        self.assertEqual((ar / "A" / "team-test-lock.json").read_bytes(), lock)
        self.assertEqual((ar / "A" / "jev-decisions.md").read_bytes(), wt_log)
        self.assertFalse(gd.exists(), "the worktree's git dir goes with the worktree")


class TestGc(LifecycleCase):

    def test_ac89_dry_run_changes_nothing(self):
        env = {"TEAM_STALE_DAYS": "0"}
        ws, repos, wts = self.pushed_ws("f", "A")
        wsg = self.new_ws("g")
        hog = Path(self.ok(self.sb.tg("holdout-path", cwd=wsg)).out.strip())
        shutil.rmtree(wsg)
        before = self.everything(repos["A"])
        r = self.gc(env=env)
        self.ok(r)
        self.has(r.out, "would close f (stale, 0d)", r)
        self.has(r.out, "would remove orphan holdout %s (no workspace)" % realpath(hog), r)
        self.assertEqual(self.everything(repos["A"]), before)

    def test_ac90_stale_is_merged_or_idle(self):
        old = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 40 * 86400)) + " +0000"
        wsm, rm, wm = self.pushed_ws("m", "M")
        self.sb.commit_file(wm["M"], "pkg/new.go", "package pkg\n", "work")
        self.sb.git(rm["M"], "merge", "-q", "feat/m")
        wsi, ri, wi = self.pushed_ws("i", "I", commit_date=old)
        (wsi / "spec.md").write_text("# spec\n")
        backdate_tree(wsi / "spec.md", 40)
        backdate_tree(wsi / "workspace.json", 40)
        wsn, rn, wn = self.pushed_ws("n", "N")
        r = self.ok(self.gc())
        self.has(r.out, "would close m (merged, 0d)", r)
        self.has(r.out, "would close i (stale, 0d)", r)
        self.lacks(r.out, "would close n", r)
        self.assertEqual(self.status_of("i")[0], "stale")
        self.assertEqual(self.status_of("n")[0], "active")
        r = self.ok(self.gc("--stale-days", "100"))
        self.lacks(r.out, "would close i", r)
        self.has(r.out, "would close m (merged", r)
        r = self.ok(self.gc("--stale-days", "0", env={"TEAM_STALE_DAYS": "100"}))
        self.has(r.out, "would close n (stale", r, "the option beats the env var")
        r = self.ok(self.gc(env={"TEAM_STALE_DAYS": "100"}))
        self.lacks(r.out, "would close i", r)
        self.assertEqual(self.status_of("i", env={"TEAM_STALE_DAYS": "100"})[0], "active")
        self.assertEqual(self.sb.tg("workspace", "list", "--stale-days", "100").code, 0)

    def test_ac90_recent_commit_is_activity(self):
        old = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 40 * 86400)) + " +0000"
        ws, repos, wts = self.pushed_ws("i", "I", commit_date=old)
        self.sb.commit_file(wts["I"], "pkg/new.go", "package pkg\n", "fresh work")
        backdate_tree(ws / "workspace.json", 40)
        self.assertEqual(self.status_of("i")[0], "active")

    def test_invalid_numbers_rejected(self):
        for args, env, name in ((("gc",), {"TEAM_STALE_DAYS": "-1"}, "TEAM_STALE_DAYS"),
                                (("gc", "--stale-days", "abc"), None, "--stale-days"),
                                (("gc",), {"TEAM_ARCHIVE_RETENTION_DAYS": "x"}, "TEAM_ARCHIVE_RETENTION_DAYS"),
                                (("gc", "--retention-days", "-3"), None, "--retention-days"),
                                (("workspace", "list"), {"TEAM_STALE_DAYS": "1.5"}, "TEAM_STALE_DAYS")):
            with self.subTest(args=args, env=env):
                r = self.sb.tg(*args, env=env)
                self.code(r, 1)
                self.has(r.err, "invalid", r)
                self.has(r.err, name, r)

    def test_ac91_orphan_workspace(self):
        env = {"TEAM_STALE_DAYS": "0"}
        ws, repos, wts = self.pushed_ws("h", "A", "B")
        shutil.rmtree(wts["A"])
        shutil.rmtree(wts["B"])
        r = self.ok(self.gc(env=env))
        self.has(r.out, "would close orphan workspace h (worktrees missing)", r)
        self.lacks(r.out, "would close h (", r)
        self.assertEqual(r.out.count(" h "), 1, self.show(r))

    def test_ac91_one_missing_worktree_is_not_an_orphan(self):
        ws, repos, wts = self.pushed_ws("h", "A", "B")
        shutil.rmtree(wts["A"])
        r = self.ok(self.gc())
        self.lacks(r.out, "orphan workspace h", r)

    def test_ac91_legacy_holdout_by_age(self):
        r_repo = self.sb.new_repo("r", branch="feat/x")
        legacy = Path(self.ok(self.sb.tg("holdout-path", cwd=r_repo)).out.strip())
        (legacy / "s.md").write_text("old scenario\n")
        backdate_tree(legacy, 181)
        r = self.ok(self.gc())
        self.has(r.out, "would delete legacy holdout %s (181d untouched)" % realpath(legacy), r)
        r = self.ok(self.gc("--retention-days", "400"))
        self.lacks(r.out, "legacy holdout", r)
        r = self.ok(self.gc(env={"TEAM_ARCHIVE_RETENTION_DAYS": "400"}))
        self.lacks(r.out, "legacy holdout", r)
        self.assertTrue(legacy.exists(), "dry runs delete nothing")
        r = self.ok(self.gc("--apply"))
        self.has(r.out, "deleted %s" % realpath(legacy), r)
        self.assertFalse(legacy.exists())
        self.assertFalse(legacy.parent.exists(), "the now-empty <HO>/<repo>/ goes too")

    def test_ac91_legacy_holdout_retention_zero(self):
        repo = self.sb.new_repo("r2")
        legacy = Path(self.ok(self.sb.tg("holdout-path", cwd=repo)).out.strip())
        (legacy / "s.md").write_text("fresh\n")
        r = self.ok(self.gc())
        self.lacks(r.out, "legacy holdout", r)
        r = self.ok(self.gc(env={"TEAM_ARCHIVE_RETENTION_DAYS": "0"}))
        self.has(r.out, "would delete legacy holdout %s" % realpath(legacy), r)

    def test_ac91_legacy_parent_with_other_branches_is_kept(self):
        repo = self.sb.new_repo("r3", branch="feat/old")
        old = Path(self.ok(self.sb.tg("holdout-path", cwd=repo)).out.strip())
        (old / "s.md").write_text("old\n")
        backdate_tree(old, 200)
        self.sb.git(repo, "checkout", "-q", "-b", "feat/new")
        new = Path(self.ok(self.sb.tg("holdout-path", cwd=repo)).out.strip())
        (new / "s.md").write_text("new\n")
        r = self.ok(self.gc("--apply"))
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())
        self.assertTrue(old.parent.exists())

    def test_expired_archives(self):
        self.new_ws("x")
        self.ok(self.close("x"))
        meta_path = self.sb.ar_root / "x" / "archive.json"
        meta = json.loads(meta_path.read_text())
        meta["closed"] = iso_days_ago(200)
        meta_path.write_text(json.dumps(meta))
        archive = realpath(self.sb.ar_root / "x")
        r = self.ok(self.gc())
        self.has(r.out, "would delete archive %s (200d old)" % archive, r)
        r = self.ok(self.gc("--retention-days", "400"))
        self.lacks(r.out, "would delete archive", r)
        r = self.ok(self.gc("--apply"))
        self.has(r.out, "deleted %s" % archive, r)
        self.assertFalse(Path(archive).exists())

    def test_apply_never_deletes_an_archive_it_created(self):
        env = {"TEAM_STALE_DAYS": "0", "TEAM_ARCHIVE_RETENTION_DAYS": "0"}
        ws, repos, wts = self.pushed_ws("f", "A")
        r = self.ok(self.gc("--apply", env=env))
        self.has(r.out, "closed f", r)
        self.assertTrue((self.sb.ar_root / "f" / "archive.json").exists())
        r = self.ok(self.gc("--apply", env=env))
        self.has(r.out, "deleted %s" % realpath(self.sb.ar_root / "f"), r)

    def test_ac92_non_orphans_left_alone(self):
        wsf, repos, wts = self.pushed_ws("f", "A")
        hof = Path(self.ok(self.sb.tg("holdout-path", cwd=wsf)).out.strip())
        (hof / "old").mkdir()
        (hof / "old" / "x.md").write_text("old\n")
        backdate_tree(hof / "old", 200)
        r_repo = self.sb.new_repo("r", branch="feat/x")
        legacy = Path(self.ok(self.sb.tg("holdout-path", cwd=r_repo)).out.strip())
        (legacy / "old.md").write_text("old\n")
        backdate_tree(legacy, 200)
        (legacy / "fresh.md").write_text("fresh\n")
        handmade = self.sb.ho_root / "x"
        handmade.mkdir()
        (handmade / "readme.txt").write_text("no subdirectory\n")
        backdate_tree(handmade, 200)
        (self.sb.ws_root / "stray.txt").write_text("a file, not a workspace\n")
        other = self.sb.tmp / "other"
        other.mkdir()
        (other / "old.md").write_text("old\n")
        backdate_tree(other, 400)
        before = (self.everything(repos["A"]), tree_snapshot(other))
        r = self.ok(self.gc())
        self.assertEqual(r.out, "team-guard: nothing to collect\n", self.show(r))
        r = self.ok(self.gc("--apply"))
        self.assertEqual(r.out, "team-guard: nothing to collect\n", self.show(r))
        self.assertEqual((self.everything(repos["A"]), tree_snapshot(other)), before)

    def test_ac93_apply_closes_without_force(self):
        env = {"TEAM_STALE_DAYS": "0"}
        ws1, r1, w1 = self.pushed_ws("s1", "A")
        ws2, r2, w2 = self.pushed_ws("s2", "B")
        (w2["B"] / "scratch.txt").write_text("wip\n")
        wj2 = (ws2 / "workspace.json").read_bytes()
        r = self.gc("--apply", env=env)
        self.code(r, 0, "a skip doesn't make gc fail")
        self.has(r.out, "closed s1", r)
        self.has(r.out, "skipped s2: %s: uncommitted changes" % realpath(w2["B"]), r)
        self.assertFalse(ws1.exists())
        self.assertTrue((self.sb.ar_root / "s1" / "archive.json").exists())
        self.assertTrue(w2["B"].exists())
        self.assertTrue((w2["B"] / "scratch.txt").exists())
        self.assertEqual((ws2 / "workspace.json").read_bytes(), wj2)

    def test_ac93_apply_skips_the_workspace_containing_cwd(self):
        env = {"TEAM_STALE_DAYS": "0"}
        ws1, r1, w1 = self.pushed_ws("s1", "A")
        ws3 = self.new_ws("s3")
        r = self.gc("--apply", cwd=ws3, env=env)
        self.code(r, 0)
        self.has(r.out, "closed s1", r)
        self.has(r.out, "skipped s3", r)
        self.assertTrue(ws3.exists())

    def test_ac94_hand_deleted_worktrees(self):
        ws, repos, wts = self.pushed_ws("h", "A", "B")
        shutil.rmtree(wts["A"])
        shutil.rmtree(wts["B"])
        r = self.gc("--apply")
        self.ok(r)
        self.has(r.out, "closed h", r)
        for n in ("A", "B"):
            self.assertEqual(self.sb.worktrees(repos[n]), [realpath(repos[n])])
            self.assertEqual(self.admin_entries(repos[n]), [])
        self.assertTrue((self.sb.ar_root / "h" / "archive.json").exists())
        self.assertFalse(ws.exists())

    def test_ac95_orphan_holdout_removal_only(self):
        wsf, repos, wts = self.pushed_ws("f", "A")
        hof = Path(self.ok(self.sb.tg("holdout-path", cwd=wsf)).out.strip())
        (hof / "s.md").write_text("live\n")
        wsg = self.new_ws("g")
        hog = Path(self.ok(self.sb.tg("holdout-path", cwd=wsg)).out.strip())
        (hog / "s.md").write_text("orphan\n")
        shutil.rmtree(wsg)
        r_repo = self.sb.new_repo("r", branch="feat/x")
        legacy = Path(self.ok(self.sb.tg("holdout-path", cwd=r_repo)).out.strip())
        (legacy / "s.md").write_text("legacy\n")
        keep = {p: tree_snapshot(p) for p in (hof, legacy)}
        r = self.ok(self.gc("--apply"))
        self.has(r.out, "removed %s" % realpath(hog), r)
        self.assertFalse(hog.exists())
        self.assertEqual({p: tree_snapshot(p) for p in keep}, keep)
        self.assertTrue(wsf.exists())

    def test_ac96_moved_source(self):
        wsf, rf, wf = self.pushed_ws("f", "A")
        wsg, rg, wg = self.pushed_ws("g", "B")
        moved = self.sb.tmp / "A-moved"
        shutil.move(str(rf["A"]), str(moved))
        r = self.gc()
        self.code(r, 0)
        self.no_traceback(r)
        self.has(r.out + r.err, realpath(rf["A"]), r)
        self.has(r.out, "would close f (stale, 0d)", r)
        r = self.gc("--apply", env={"TEAM_STALE_DAYS": "0"})
        self.code(r, 0)
        self.no_traceback(r)
        self.has(r.err, "source repo %s is missing" % realpath(rf["A"]), r)
        self.has(r.out, "closed g", r)
        self.assertFalse(wsg.exists())

    def test_ac97_second_run_has_nothing_to_do(self):
        env = {"TEAM_STALE_DAYS": "0"}
        self.pushed_ws("s1", "A")
        wsh, rh, wh = self.pushed_ws("h", "B")
        shutil.rmtree(wh["B"])
        wsg = self.new_ws("g")
        self.ok(self.sb.tg("holdout-path", cwd=wsg))
        shutil.rmtree(wsg)
        r_repo = self.sb.new_repo("r", branch="feat/x")
        legacy = Path(self.ok(self.sb.tg("holdout-path", cwd=r_repo)).out.strip())
        backdate_tree(legacy, 200)
        self.ok(self.gc("--apply", env=env))
        for _ in range(2):
            r = self.ok(self.gc(env=env))
            self.assertEqual(r.out, "team-guard: nothing to collect\n", self.show(r))
            r = self.ok(self.gc("--apply", env=env))
            self.assertEqual(r.out, "team-guard: nothing to collect\n", self.show(r))

    def test_ac97_only_skipped_items_remain(self):
        env = {"TEAM_STALE_DAYS": "0"}
        self.pushed_ws("s1", "A")
        ws2, r2, w2 = self.pushed_ws("s2", "B")
        (w2["B"] / "scratch.txt").write_text("wip\n")
        self.ok(self.gc("--apply", env=env))
        r = self.ok(self.gc(env=env))
        lines = [l for l in r.out.splitlines() if l.strip()]
        self.assertTrue(lines, self.show(r))
        for line in lines:
            self.has(line, "s2", r)
            self.lacks(line, "s1", r)
        r = self.ok(self.gc("--apply", env=env))
        for line in [l for l in r.out.splitlines() if l.strip()]:
            self.has(line, "skipped s2", r)

    def test_ac98_new_hint(self):
        self.new_ws("a")
        r = self.sb.tg("workspace", "new", "g", env={"TEAM_STALE_DAYS": "0"})
        self.ok(r)
        hint_lines = [l for l in r.err.splitlines() if "team-guard gc" in l]
        self.assertEqual(len(hint_lines), 1, self.show(r))
        self.has(hint_lines[0], "team-guard: hint: 1 stale workspace(s)", r)
        self.assertTrue(self.sb.ws_dir("g").exists())

    def test_ac98_no_hint_when_nothing_stale(self):
        r = self.sb.tg("workspace", "new", "first", env={"TEAM_STALE_DAYS": "0"})
        self.ok(r)
        self.assertEqual(r.err, "", "the new workspace itself doesn't count")
        r = self.sb.tg("workspace", "new", "second")
        self.ok(r)
        self.assertEqual(r.err, "")

    def test_ac99_no_background_processes(self):
        env = dict(self.sb.env, TEAM_STALE_DAYS="0")
        self.pushed_ws("f", "A")
        self.pushed_ws("g", "B")
        for args in (("workspace", "close", "f"), ("gc",), ("gc", "--apply"), ("workspace", "list")):
            with self.subTest(args=args):
                p = subprocess.Popen([str(GUARD), *args], cwd=str(self.sb.scratch), env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                out, err = p.communicate(timeout=60)
                self.assertNotEqual(p.returncode, 70, "not implemented: %s: %s" % (args, err.decode()))
                ps = subprocess.run(["ps", "-A", "-o", "pgid=,pid=,command="], capture_output=True, text=True)
                left = [l for l in ps.stdout.splitlines() if l.split() and l.split()[0] == str(p.pid)]
                self.assertEqual(left, [], "processes left behind by %s" % (args,))

    def test_ac101_no_network(self):
        env = {"TEAM_STALE_DAYS": "0"}
        ws, repos, wts = self.pushed_ws("f", "A")
        self.sb.git(repos["A"], "remote", "set-url", "origin", "https://invalid.invalid/x.git")
        start = time.time()
        status, r = self.status_of("f")
        self.assertEqual(status, "active")
        g = self.ok(self.gc(env=env))
        self.has(g.out, "would close f (stale", g)
        c = self.close("f")
        self.ok(c)
        self.assertLess(time.time() - start, 30, "lifecycle commands must not wait on the network")

    def test_ac101_remote_tracking_refs_decide_unpushed(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        self.sb.commit_file(wts["A"], "pkg/new.go", "package pkg\n", "work")
        # the commit is on the bare remote, but no remote-tracking ref knows yet
        bare = self.sb.git(repos["A"], "remote", "get-url", "origin")
        self.sb.git(wts["A"], "push", "-q", bare, "feat/f")
        r = self.close("f")
        self.code(r, 1)
        self.has(r.err, "unpushed commits", r)
        self.sb.git(repos["A"], "fetch", "-q", "origin")
        self.ok(self.close("f"))


if __name__ == "__main__":
    unittest.main()
