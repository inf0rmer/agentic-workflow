"""Behaviour of close and list that must stay safe: what counts as unpushed, no network, no leftover processes."""

import errno
import json
import os
import select
import shutil
import subprocess
import tempfile
import time
import unittest

from test_workspace_lifecycle import LifecycleCase
from wslib import GUARD
from wsnet import Tripwire


def wait_exit_unreaped(pid):
    """Block until child `pid` exits, leaving it unreaped (a zombie).

    While the zombie exists its pid, and so its process group id, can't be reused by an
    unrelated process, so a `ps` taken now can't mistake a newcomer for a leftover.
    os.waitid(WNOWAIT) does this directly, but some builds (python.org's macOS Python, as
    used by actions/setup-python) lack os.waitid. There, a kqueue NOTE_EXIT watch gives the
    same guarantee: it reports the exit without reaping. Returns False when neither exists;
    reaping first and checking afterwards would reopen the pid-reuse race, so callers skip.
    """
    if hasattr(os, "waitid"):
        os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
        return True
    if hasattr(select, "kqueue"):
        kq = select.kqueue()
        try:
            ev = select.kevent(pid, filter=select.KQ_FILTER_PROC,
                               flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                               fflags=select.KQ_NOTE_EXIT)
            # With room for one event, kqueue reports a failed registration as an event with
            # EV_ERROR set rather than raising. macOS refuses to watch a child that is already a
            # zombie with ESRCH; that still means "exited and unreaped", which is what we want.
            # Anything else is unexpected and must not pass for an exit.
            (got,) = kq.control([ev], 1, None)
            if got.flags & select.KQ_EV_ERROR and got.data != errno.ESRCH:
                raise OSError(got.data, "kqueue watch on pid %d failed: %s"
                              % (pid, os.strerror(got.data)))
        finally:
            kq.close()
        return True
    return False


class TestCloseAndListSafety(LifecycleCase):

    def test_a_push_nobody_has_fetched_is_unpushed(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        self.sb.commit_file(wts["A"], "pkg/new.go", "package pkg\n", "work")
        # the commit is on the bare remote, but no remote-tracking ref knows yet
        bare = self.sb.git(repos["A"], "remote", "get-url", "origin")
        self.sb.git(wts["A"], "push", "-q", bare, "feat/f")
        # newer git (2.56, not 2.54) records a push to a URL that matches a configured remote under refs/remotes; drop it so no git version knows
        self.sb.git(wts["A"], "update-ref", "-d", "refs/remotes/origin/feat/f")
        r = self.close("f")
        self.code(r, 1)
        self.has(r.err, "unpushed commits", r)
        self.sb.git(repos["A"], "fetch", "-q", "origin")
        self.ok(self.close("f"))

    def test_invalid_stale_days_is_rejected_on_list(self):
        for env, name in (({"TEAM_STALE_DAYS": "-1"}, "TEAM_STALE_DAYS"), ({"TEAM_STALE_DAYS": "1.5"}, "TEAM_STALE_DAYS")):
            with self.subTest(env=env):
                r = self.sb.tg("workspace", "list", env=env)
                self.code(r, 1)
                self.has(r.err, "invalid", r)
                self.has(r.err, name, r)
        r = self.sb.tg("workspace", "list", "--stale-days", "abc")
        self.code(r, 1)
        self.has(r.err, "--stale-days", r)

    def test_new_gives_no_hint_when_nothing_is_stale(self):
        r = self.sb.tg("workspace", "new", "first", env={"TEAM_STALE_DAYS": "0"})
        self.ok(r)
        self.assertEqual(r.err, "", "the new workspace itself doesn't count")
        r = self.sb.tg("workspace", "new", "second")
        self.ok(r)
        self.assertEqual(r.err, "")

    def test_close_and_list_leave_no_background_processes(self):
        env = dict(self.sb.env, TEAM_STALE_DAYS="0")
        self.pushed_ws("f", "A")
        self.pushed_ws("g", "B")
        for args in (("workspace", "close", "f"), ("workspace", "list")):
            with self.subTest(args=args):
                with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
                    p = subprocess.Popen([str(GUARD), *args], cwd=str(self.sb.scratch), env=env,
                                         stdout=out, stderr=err, start_new_session=True)
                    # Wait for the exit but leave the child unreaped: its pid (and so its process
                    # group id) can't be reused by an unrelated process while we look.
                    if not wait_exit_unreaped(p.pid):
                        p.wait(timeout=60)
                        self.skipTest("no way to wait for a child without reaping it here")
                    ps = subprocess.run(["ps", "-A", "-o", "pgid=,pid=,command="], capture_output=True, text=True)
                    code = p.wait(timeout=60)
                    err.seek(0)
                    self.assertNotEqual(code, 70, "not implemented: %s: %s" % (args, err.read().decode()))
                left = [l for l in ps.stdout.splitlines()
                        if len(l.split()) > 1 and l.split()[0] == str(p.pid) and l.split()[1] != str(p.pid)]
                self.assertEqual(left, [], "processes left behind by %s" % (args,))

    def test_close_and_list_make_no_connection_to_the_remote(self):
        # The remote and every proxy point at a local tripwire that counts connections.
        net = Tripwire()
        self.addCleanup(net.close)
        env = dict(net.proxy_env(), TEAM_STALE_DAYS="0")
        ws, repos, wts = self.pushed_ws("f", "A")
        _, repos_g, _ = self.pushed_ws("g", "B")
        self.sb.git(repos["A"], "remote", "set-url", "origin", net.url + "/A.git")
        self.sb.git(repos["A"], "remote", "set-url", "--push", "origin", net.url + "/A.git")
        self.assertEqual(self.status_of("f", env=env)[0], "stale")
        self.ok(self.close("f", env=env))
        self.assertEqual(net.connections, 0, "a lifecycle command contacted the remote")
        # the tripwire itself works: a real fetch is counted
        self.sb.git(repos_g["B"], "remote", "set-url", "origin", net.url + "/B.git")
        self.sb.git(repos_g["B"], "fetch", "-q", "origin", check=False, env=net.proxy_env())
        self.assertGreaterEqual(net.connections, 1)


class TestDetachedHeadClose(LifecycleCase):
    """A detached HEAD on a commit no local branch holds is lost once the worktree goes."""

    def orphan(self, feature, *names, orphaned=None):
        """Attach `names`, then detach one of them (default: the first) on a new commit no local branch holds.

        The commit is also pushed to a remote branch that is then deleted on the remote without a
        fetch, so only a stale remote-tracking ref still contains it and the unpushed check stays quiet.
        """
        name = orphaned or names[0]
        ws, repos, wts = self.pushed_ws(feature, *names)
        wt = wts[name]
        self.sb.git(wt, "checkout", "-q", "--detach")
        sha = self.sb.commit_file(wt, "pkg/d.go", "package pkg\n", "detached work")
        self.sb.git(wt, "push", "-q", "origin", "HEAD:refs/heads/tmp")
        bare = self.sb.git(repos[name], "remote", "get-url", "origin")
        self.sb.git(bare, "update-ref", "-d", "refs/heads/tmp")
        # newer git records a push to a matching remote under refs/remotes; make sure the stale ref is there
        self.sb.git(wt, "update-ref", "refs/remotes/origin/tmp", sha)
        return repos, wts, sha

    def test_detached_commit_on_no_local_branch_is_refused(self):
        repos, wts, sha = self.orphan("f", "A")
        before = self.everything(repos["A"])
        r = self.close("f")
        self.code(r, 1)
        self.has(r.err, str(wts["A"]), r)
        self.has(r.err, "detached HEAD " + sha[:7], r)
        self.has(r.err, "branch keep-%s %s" % (sha[:7], sha[:7]), r)
        self.assertEqual(self.everything(repos["A"]), before)
        self.assertTrue(os.path.isdir(wts["A"]))

    def test_force_discards_it_and_records_the_full_sha(self):
        repos, wts, sha = self.orphan("f", "A")
        r = self.close("f", "--force")
        self.ok(r)
        self.has(r.out, "discarded detached HEAD %s on no local branch" % sha[:7], r)
        meta = json.loads((self.sb.ar_root / "f" / "archive.json").read_text())
        self.assertTrue(any(sha in d["reason"] for d in meta["discarded"]), meta)

    def test_the_printed_keep_command_clears_the_problem(self):
        repos, wts, sha = self.orphan("f", "A")
        r = self.close("f")
        self.code(r, 1)
        (command,) = [l.split("keep it with: ", 1)[1] for l in r.err.splitlines() if "keep it with: " in l]
        self.ok(self.sb.run(["sh", "-c", command]))
        self.ok(self.close("f"))

    def test_detached_commit_on_a_local_branch_is_fine(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        self.sb.git(wts["A"], "checkout", "-q", "--detach")
        self.ok(self.close("f"))

    def test_one_orphaned_repo_blocks_the_whole_close(self):
        repos, wts, sha = self.orphan("f", "A", "B", orphaned="B")
        before = self.everything(repos["A"], repos["B"])
        r = self.close("f")
        self.code(r, 1)
        self.has(r.err, str(wts["B"]), r)
        self.has(r.err, "is on no local branch", r)
        self.assertNotIn(str(wts["A"]), r.err)
        self.assertEqual(self.everything(repos["A"], repos["B"]), before)
        self.assertTrue(os.path.isdir(wts["A"]) and os.path.isdir(wts["B"]))

    def test_a_git_error_in_the_check_refuses_the_close(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        self.sb.git(wts["A"], "checkout", "-q", "--detach")
        shim = self.sb.tmp / "shim"
        shim.mkdir()
        (shim / "git").write_text(
            '#!/bin/sh\ncase " $* " in *" --branches "*|*" rev-parse HEAD "*) exit 128;; esac\n'
            'exec %s "$@"\n' % shutil.which("git"))
        (shim / "git").chmod(0o755)
        r = self.close("f", env={"PATH": "%s:%s" % (shim, self.sb.env["PATH"])})
        self.code(r, 1)
        self.has(r.err, "detached HEAD (unknown commit) on no local branch", r)
        self.assertNotIn("keep it with", r.err)
        self.assertTrue(os.path.isdir(wts["A"]))

    def test_the_detached_hint_appears_only_with_a_detached_problem(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        self.sb.commit_file(wts["A"], "pkg/n.go", "package pkg\n", "work")
        r = self.close("f")
        self.code(r, 1)
        self.has(r.err, "use --force to remove them anyway (branches are kept)", r)
        self.assertNotIn("detached", r.err)

    def test_detached_orphaned_and_unpushed_lists_both_problems(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        self.sb.git(wts["A"], "checkout", "-q", "--detach")
        self.sb.commit_file(wts["A"], "pkg/d.go", "package pkg\n", "detached work")
        r = self.close("f")
        self.code(r, 1)
        self.has(r.err, "unpushed commits", r)
        self.has(r.err, "is on no local branch", r)


if __name__ == "__main__":
    unittest.main()
