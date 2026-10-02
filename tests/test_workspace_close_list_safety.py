"""Behaviour of close and list that must stay safe: what counts as unpushed, no network, no leftover processes."""

import errno
import os
import select
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


if __name__ == "__main__":
    unittest.main()
