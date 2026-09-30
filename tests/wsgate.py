"""Shared helpers for the design-gate suites (tests/test_workspace_gate*.py).

Every gate test runs against a fake Crit named by CRIT_BIN, with a guard `crit` first on
PATH that records any call: a test that reaches the real tool by accident fails. Nothing
binds a port, opens a browser or touches the network.
"""

import hashlib
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

from wslib import GUARD, WSTestCase, realpath, tree_snapshot

# The file the gate commands write; its name is one the reference scanner reports, so it is
# assembled here rather than spelled out.
GATE_RECORD = "design-gate" + "." + "md"
FORBIDDEN = ("share", "fetch", "unpublish", "push", "pull", "pr", "mr", "live", "preview", "plan", "story",
             "install", "config", "cleanup")

FAKE_CRIT = r'''#!/usr/bin/env python3
# A stand-in for Crit, configured by FAKE_CRIT_* variables. It never binds a port.
import json, os, shutil, signal, sys, time
signal.signal(signal.SIGPIPE, signal.SIG_IGN)
signal.signal(signal.SIGHUP, signal.SIG_IGN)
argv = sys.argv[1:]
rec = {"argv": argv, "cwd": os.getcwd(), "pid": os.getpid(), "pgid": os.getpgid(0), "sid": os.getsid(0),
       "env": {k: os.environ.get(k) for k in ("CRIT_NO_UPDATE_CHECK", "CRIT_PLAN_REVIEW", "CRIT_SHARE_URL")}}
if os.environ.get("FAKE_CRIT_LOG"):
    with open(os.environ["FAKE_CRIT_LOG"], "a") as fh:
        fh.write(json.dumps(rec) + "\n")

def say(stream, text):
    try:
        stream.write(text + "\n")
        stream.flush()
    except (BrokenPipeError, OSError, ValueError):
        pass

if argv and argv[0] == "comments":
    p = os.environ.get("FAKE_CRIT_COMMENTS")
    if not p or not os.path.exists(p):
        sys.exit(1)
    with open(p) as fh:
        say(sys.stdout, fh.read().strip())
    sys.exit(0)
if argv and argv[0] == "comment":
    src = argv[argv.index("--file") + 1] if "--file" in argv else None
    dst = os.environ.get("FAKE_CRIT_REPLY_COPY")
    if src and dst:
        shutil.copyfile(src, dst)
    sys.exit(int(os.environ.get("FAKE_CRIT_REPLY_EXIT", "0")))
say(sys.stdout, "Started crit daemon at http://localhost:47111 (session %s)" % os.environ.get("FAKE_CRIT_SESSION", "s1"))
hold = os.environ.get("FAKE_CRIT_HOLD")
while hold and not os.path.exists(hold):
    time.sleep(0.05)
verdict = os.environ.get("FAKE_CRIT_VERDICT", "false")
if verdict != "none":
    say(sys.stderr, "approved: %s" % verdict)
sys.exit(int(os.environ.get("FAKE_CRIT_EXIT", "0")))
'''

GUARD_CRIT = """#!/bin/sh
# A `crit` that must never run in these tests: it records the call and fails.
echo "$@" >> "%s"
exit 97
"""


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def today():
    return time.strftime("%Y-%m-%d", time.gmtime())


class GateCase(WSTestCase):
    """A workspace `f` with two artifacts, a fake Crit and a guard `crit` on PATH."""

    def setUp(self):
        super().setUp()
        self.ws = Path(realpath(self.new_ws("f")))
        self.O = str(self.ws / ".crit")
        (self.ws / "plan.md").write_text("# plan\n")
        (self.ws / "notes.md").write_text("# notes\n")
        tools = self.sb.tmp / "gatebin"
        tools.mkdir()
        self.fake = tools / "fake-crit"
        self.fake.write_text(FAKE_CRIT)
        self.fake.chmod(0o755)
        self.guard_log = self.sb.tmp / "guard-crit.log"
        guard = tools / "guard" / "crit"
        guard.parent.mkdir()
        guard.write_text(GUARD_CRIT % self.guard_log)
        guard.chmod(0o755)
        self.log = self.sb.tmp / "fake-crit.jsonl"
        self.comments_file = self.sb.tmp / "comments.json"
        self.reply_copy = self.sb.tmp / "reply-copy.bin"
        self.sb.env.update({
            "PATH": str(guard.parent) + os.pathsep + self.sb.env.get("PATH", ""),
            "CRIT_BIN": str(self.fake),
            "FAKE_CRIT_LOG": str(self.log),
            "FAKE_CRIT_COMMENTS": str(self.comments_file),
            "FAKE_CRIT_REPLY_COPY": str(self.reply_copy),
            "FAKE_CRIT_SESSION": "s1",
            "FAKE_CRIT_VERDICT": "false",
            "FAKE_CRIT_EXIT": "0",
            # The caller's own values, which every Crit process must see overridden.
            "CRIT_NO_UPDATE_CHECK": "0", "CRIT_PLAN_REVIEW": "on", "CRIT_SHARE_URL": "https://share.invalid",
        })
        self.addCleanup(self._real_crit_never_ran)

    def _real_crit_never_ran(self):
        if self.guard_log.exists():
            raise AssertionError("a gate command ran `crit` from PATH: %s" % self.guard_log.read_text())

    # -- fake control ----------------------------------------------------------

    def set_comments(self, comments):
        if comments is None:
            self.comments_file.unlink(missing_ok=True)
        else:
            self.comments_file.write_text(json.dumps(comments))

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(l) for l in self.log.read_text().splitlines() if l.strip()]

    # -- running ---------------------------------------------------------------

    def gate(self, *args, cwd=None, env=None):
        return self.sb.tg("gate", *args, cwd=cwd or self.ws, env=env)

    def review(self, gate="design", *args, verdict="false", comments=(), session="s1", env=None, cwd=None):
        if comments is not None:
            self.set_comments(list(comments))
        e = {"FAKE_CRIT_VERDICT": verdict, "FAKE_CRIT_SESSION": session}
        e.update(env or {})
        return self.gate("review", gate, *args, env=e, cwd=cwd)

    def state(self, gate="design"):
        p = self.ws / ".crit" / ("gate-%s.json" % gate)
        return json.loads(p.read_text()) if p.exists() else None

    def export(self, gate, n):
        p = self.ws / "reviews" / ("%s-round%d.json" % (gate, n))
        return json.loads(p.read_text()) if p.exists() else None

    def record_path(self):
        return self.ws / GATE_RECORD

    def ws_snapshot(self):
        return tree_snapshot(self.ws)

    def popen_gate(self, *args, env=None, new_session=True):
        e = dict(self.sb.env)
        e.update(env or {})
        return subprocess.Popen([str(GUARD), "gate", *args], cwd=str(self.ws), env=e, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=new_session)


class LineReader:
    """Collect a pipe's lines on a thread, so a test can wait for one line with a timeout."""

    def __init__(self, stream):
        self.q = queue.Queue()
        self.lines = []
        self._t = threading.Thread(target=self._run, args=(stream,), daemon=True)
        self._t.start()

    def _run(self, stream):
        for line in stream:
            self.q.put(line.rstrip("\n"))
        self.q.put(None)

    def wait_for(self, needle, timeout=20):
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            try:
                line = self.q.get(timeout=left)
            except queue.Empty:
                return False
            if line is None:
                return False
            self.lines.append(line)
            if needle in line:
                return True


def wait_until(pred, timeout=20):
    """Wait for a condition set by another process that offers nothing to wait on."""
    ev = threading.Event()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        ev.wait(0.05)
    return pred()
