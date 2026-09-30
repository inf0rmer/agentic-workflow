"""team-guard workspace close stops the workspace's own Crit daemons first.

A review daemon outlives the gate command by design. Closing a workspace must not leave one
running against an archived directory, must stop it before the exports are archived so they
are final, and must never touch a review that belongs to another workspace.
"""

import json
import os
import signal
import subprocess
import unittest
from pathlib import Path

from wslib import GUARD, realpath
from wsgate import GateCase, LineReader, wait_until
from wstools import pid_alive


class TestCloseStopsDaemons(GateCase):

    def held_review(self, ws, gate="design"):
        """Start a review held open in `ws`, then kill the waiting command so only Crit is left."""
        hold = self.sb.tmp / ("hold-%s-%s" % (Path(ws).name, gate))
        self.addCleanup(lambda: hold.exists() or hold.write_text("go"))
        env = dict(self.sb.env, FAKE_CRIT_HOLD=str(hold), FAKE_CRIT_ON_TERM="1")
        n_before = len(self.calls())
        p = subprocess.Popen([str(GUARD), "gate", "review", gate, "plan.md"], cwd=str(ws), env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        self.assertTrue(LineReader(p.stderr).wait_for("(session "), "the review started")
        state = Path(ws) / ".crit" / ("gate-%s.json" % gate)
        self.assertTrue(wait_until(lambda: state.exists() and json.loads(state.read_text()).get("pending")),
                        "the round is pending")
        os.killpg(p.pid, signal.SIGKILL)
        p.wait(timeout=30)
        pid = [c for c in self.calls()[n_before:] if c["argv"][:1] == ["-o"]][0]["pid"]
        self.addCleanup(self._kill, pid)
        self.assertTrue(pid_alive(pid), "the review daemon runs on after the command")
        return pid

    def _kill(self, pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    def close(self, feature):
        return self.sb.tg("workspace", "close", feature, cwd=self.sb.scratch,
                          env={"CRIT_BIN": str(self.sb.tmp / "no-crit-installed")})

    def test_close_stops_only_this_workspaces_daemons_before_archiving(self):
        other = Path(realpath(self.new_ws("g")))
        (other / "plan.md").write_text("# other plan\n")
        mine = self.held_review(self.ws)
        theirs = self.held_review(other)

        r = self.ok(self.close("f"))
        self.assertIn("team-guard: stopped crit daemon %d (gate design)" % mine, r.out.splitlines(), self.show(r))
        self.assertTrue(wait_until(lambda: not pid_alive(mine), timeout=10), "this workspace's daemon is stopped")
        self.assertTrue(pid_alive(theirs), "another workspace's daemon is left alone")
        self.assertNotIn(str(theirs), r.out + r.err)
        archived = Path(realpath(self.sb.ar_root / "f"))
        self.assertTrue((archived / "reviews" / ("stopped-%d.json" % mine)).exists(),
                        "the daemon was stopped before the exports were archived")

    def test_close_without_running_daemons_prints_nothing_extra(self):
        pid = self.held_review(self.ws)
        self._kill(pid)
        self.assertTrue(wait_until(lambda: not pid_alive(pid), timeout=10))
        r = self.ok(self.close("f"))
        self.assertNotIn("stopped crit daemon", r.out + r.err)


if __name__ == "__main__":
    unittest.main()
