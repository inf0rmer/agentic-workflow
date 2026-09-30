"""An opt-in smoke test of the design gate against the real crit binary.

The rest of the gate suites use a fake that mirrors observed Crit behavior; this test is
the check that the mirror still matches. It runs one review with a comment, a reply, a
second round that approves, and the record and check, driving Crit through its local API
(Crit's own interface, used only here). It never shares, pushes or pulls, and it kills every
process it started.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import threading
import unittest
import urllib.error
import urllib.request

from wslib import GUARD, WSTestCase, realpath
from wsgate import LineReader, approval_sidecar_name, verdict_line, wait_until

REASON = "set CRIT_REAL=1 with crit 0.21+ on PATH to run the real-Crit smoke test"
SESSION = re.compile(r"localhost:(\d+) \(session ([0-9A-Za-z]+)(?:, PID (\d+))?\)")


def crit_ok():
    if os.environ.get("CRIT_REAL") != "1" or not shutil.which("crit"):
        return False
    p = subprocess.run(["crit", "--version"], capture_output=True, text=True, timeout=30,
                       env=dict(os.environ, CRIT_NO_UPDATE_CHECK="1"))
    m = re.search(r"(\d+)\.(\d+)", p.stdout + p.stderr)
    return bool(m) and (int(m.group(1)), int(m.group(2))) >= (0, 21)


@unittest.skipUnless(crit_ok(), REASON)
class TestRealCrit(WSTestCase):

    def setUp(self):
        super().setUp()
        self.ws = realpath(self.new_ws("f"))
        self.art = "plan.md"
        with open(os.path.join(self.ws, self.art), "w") as fh:
            fh.write("# plan\n\nline two\n")
        home = self.sb.tmp / "crit-home"
        home.mkdir()
        self.env = dict(self.sb.env, HOME=str(home), CRIT_NO_UPDATE_CHECK="1", CRIT_SHARE_URL="")
        self.env.pop("CRIT_BIN", None)
        self.pids = set()
        self.addCleanup(self.kill_all)

    def kill_all(self):
        mark = str(self.sb.tmp)
        ps = subprocess.run(["ps", "-ax", "-o", "pid=,command="], capture_output=True, text=True).stdout
        for line in ps.splitlines():
            pid, _, cmd = line.strip().partition(" ")
            if mark in cmd and "crit" in cmd:
                self.pids.add(int(pid))
        for pid in self.pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def start_review(self, *args):
        p = subprocess.Popen([str(GUARD), "gate", "review", "design", *args, "--no-open"], cwd=self.ws, env=self.env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        self.pids.add(p.pid)
        out, err = LineReader(p.stdout), LineReader(p.stderr)
        self.assertTrue(err.wait_for("(session ", timeout=60), "Crit printed its session line")
        m = SESSION.search(err.lines[-1])
        self.assertIsNotNone(m, err.lines)
        if m.group(3):
            self.pids.add(int(m.group(3)))
        return p, out, err, "http://127.0.0.1:%s" % m.group(1), m.group(2)

    def api(self, base, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else (b"" if method != "GET" else None)
        req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()
        except urllib.error.URLError as e:
            return 0, str(e)

    def ready(self, base):
        return wait_until(lambda: self.api(base, "GET", "/api/health")[0] == 200, timeout=30)

    def finish(self, p, base):
        """Finish the round; Crit only acts on it once its client is listening, so retry briefly."""
        for _ in range(20):
            self.api(base, "POST", "/api/finish")
            try:
                p.wait(timeout=2)
                return p.returncode
            except subprocess.TimeoutExpired:
                continue
        self.fail("the review never finished")

    def export(self, n):
        path = os.path.join(self.ws, "reviews", "design-round%d.json" % n)
        return json.load(open(path)) if os.path.exists(path) else None

    def test_a_real_crit_round_is_exported_replied_to_and_approved(self):
        p, out, err, base, sid = self.start_review(self.art)
        self.assertTrue(self.ready(base))
        state = os.path.join(self.ws, ".crit", "gate-design.json")
        self.assertTrue(wait_until(lambda: os.path.exists(state) and json.load(open(state)).get("pending")))
        self.assertTrue(wait_until(lambda: self.export(1) is not None), "the round is exported at its start")
        code, body = self.api(base, "POST", "/api/file/comments?path=%s" % self.art,
                              {"start_line": 1, "end_line": 1, "body": "why this heading?"})
        self.assertIn(code, (200, 201), body)
        cid = json.loads(body)["id"]
        self.assertEqual(self.finish(p, base), 0, "\n".join(err.lines))
        self.assertTrue(out.wait_for(verdict_line("design", 1, False, 1), timeout=10), out.lines)
        self.assertEqual(out.lines[-1], verdict_line("design", 1, False, 1))

        reply = self.sb.tmp / "reply.json"
        reply.write_text(json.dumps([{"reply_to": cid, "body": "kept, it names the plan"}]))
        r = self.sb.run([str(GUARD), "gate", "reply", "design", "--file", str(reply), "--author", "architect"],
                        cwd=self.ws, env=self.env)
        self.assertEqual(r.code, 0, self.show(r))
        self.assertIn("kept, it names the plan", json.dumps(self.export(1)), "the export holds the reply")

        p2, out2, err2, base2, sid2 = self.start_review()
        self.assertEqual(sid2, sid, "re-running continues the same session")
        self.assertTrue(self.ready(base2))
        code, body = self.api(base2, "DELETE", "/api/comment/%s?path=%s" % (cid, self.art))
        self.assertEqual(code, 200, body)
        self.assertEqual(self.finish(p2, base2), 0, "\n".join(err2.lines))
        self.assertTrue(out2.wait_for(verdict_line("design", 2, True, 0), timeout=10), out2.lines)
        self.assertEqual(out2.lines[-1], verdict_line("design", 2, True, 0))
        self.assertIsNotNone(self.export(2), "the approved round still has an export")
        if self.export(2):
            self.assertTrue(os.path.exists(os.path.join(self.ws, "reviews", approval_sidecar_name("design", 2))),
                            "a kept, non-empty export of an approved round has its approval sidecar")
        self.assertIsNotNone(self.export(1))
        for args in (("record", "design", "--crit"), ("check", "design")):
            r = self.sb.run([str(GUARD), "gate", *args], cwd=self.ws, env=self.env)
            self.assertEqual(r.code, 0, self.show(r))


if __name__ == "__main__":
    unittest.main()
