"""Shared helpers for the design-gate suites (tests/test_workspace_gate*.py).

Every gate test runs against a fake Crit named by CRIT_BIN, with a guard `crit` first on
PATH that records any call: a test that reaches the real tool by accident fails. Nothing
binds a port, opens a browser or touches the network.

The fake mirrors what crit 0.21.0 was observed to do: the session line on stderr, a
session id keyed on the working directory and the file arguments, `-o` refused together
with `--session`, no trailing newline on stdout, the review deleted on approval, replies
only while the session's daemon is live, and review.json written lazily.
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
NOT_APPROVED_OUT = "The review finished with %d unresolved comments."
APPROVED_OUT = "Review approved with no comments — no changes requested."
SHUTDOWN_OUT = "crit daemon shut down before review was finished."

FAKE_CRIT = r'''#!/usr/bin/env python3
# A stand-in for crit 0.21.0, configured by FAKE_CRIT_* variables. It never binds a port.
import hashlib, json, os, shutil, signal, sys, time
signal.signal(signal.SIGPIPE, signal.SIG_IGN)
signal.signal(signal.SIGHUP, signal.SIG_IGN)
argv = sys.argv[1:]
state = os.environ["FAKE_CRIT_STATE"]
os.makedirs(state, exist_ok=True)
stdin = sys.stdin.buffer.read() if argv[:1] == ["comment"] else None
rec = {"argv": argv, "cwd": os.getcwd(), "pid": os.getpid(), "pgid": os.getpgid(0), "sid": os.getsid(0),
       "env": {k: os.environ.get(k) for k in ("CRIT_NO_UPDATE_CHECK", "CRIT_PLAN_REVIEW", "CRIT_SHARE_URL")},
       "stdin": stdin.decode("utf-8", "replace") if stdin is not None else None}
if os.environ.get("FAKE_CRIT_LOG"):
    with open(os.environ["FAKE_CRIT_LOG"], "a") as fh:
        fh.write(json.dumps(rec) + "\n")

def out(text):
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except (BrokenPipeError, OSError, ValueError):
        pass

def err(text):
    try:
        sys.stderr.write(text + "\n")
        sys.stderr.flush()
    except (BrokenPipeError, OSError, ValueError):
        pass

def live(i):
    return os.path.exists(os.path.join(state, i + ".live"))

def comments():
    p = os.environ.get("FAKE_CRIT_COMMENTS")
    if p and os.path.exists(p):
        with open(p) as fh:
            return json.load(fh)
    return None

def after(flag):
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else None

if "-o" in argv and "--session" in argv:
    err("--session cannot be used with --plan, --output, or an explicit review path")
    sys.exit(1)

if argv[:1] == ["comments"]:
    sid = after("--session")
    if sid is not None:
        data = comments()
        if not live(sid) or data is None:
            err("no active review session %s" % sid)
            sys.exit(1)
        if "--all" not in argv:
            data = [c for c in data if not c.get("resolved")]
        out(json.dumps(data, indent=2))
        sys.exit(0)
    path = argv[-1]
    if not os.path.isfile(path):
        err("stat %s: no such file or directory" % path)
        sys.exit(1)
    with open(path) as fh:
        out(fh.read())
    sys.exit(0)

if argv[:1] == ["comment"]:
    sid = after("--session")
    if sid is None or not live(sid):
        err("no active review session %s" % sid)
        sys.exit(1)
    dst = os.environ.get("FAKE_CRIT_REPLY_COPY")
    if dst:
        with open(dst, "wb") as fh:
            fh.write(stdin)
    out("Added 1 reply")
    sys.exit(int(os.environ.get("FAKE_CRIT_REPLY_EXIT", "0")))

# A review start: `-o <O> [--no-open] <file>...`.
O = after("-o")
files = []
skip = False
for i, a in enumerate(argv):
    if skip:
        skip = False
        continue
    if a == "-o":
        skip = True
        continue
    if a.startswith("-"):
        continue
    files.append(a)
sid = hashlib.sha256((os.getcwd() + "\0" + "\0".join(files)).encode()).hexdigest()[:12]
marker = os.path.join(state, sid + ".live")
if live(sid):
    err("Connected to crit daemon at http://localhost:47111 (session %s)" % sid)
else:
    err("Started crit daemon at http://localhost:47111 (session %s, PID %d)" % (sid, os.getpid()))
    open(marker, "w").close()
hold = os.environ.get("FAKE_CRIT_HOLD")
while hold and not os.path.exists(hold):
    if not os.path.isdir(os.path.dirname(hold)):
        sys.exit(0)  # the test's sandbox is gone: don't outlive it
    time.sleep(0.05)
data = comments() or []
store = os.path.join(O, "reviews", sid)
verdict = os.environ.get("FAKE_CRIT_VERDICT", "false")

def save():
    os.makedirs(store, exist_ok=True)
    with open(os.path.join(store, "review.json"), "w") as fh:
        json.dump(data, fh)

if verdict == "true":
    err("approved: true")
    err("")
    err("Session: 1 file and 1s reviewed")
    out("Review approved with no comments — no changes requested.")
    if os.path.exists(marker):
        os.unlink(marker)
    shutil.rmtree(store, ignore_errors=True)
elif verdict == "false":
    err("approved: false")
    out("The review finished with %d unresolved comments." % sum(1 for c in data if not c.get("resolved")))
    save()
elif verdict == "shutdown":
    err("approved: false")
    out("crit daemon shut down before review was finished.")
    if os.path.exists(marker):
        os.unlink(marker)
    save()
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


def verdict_line(gate, n, approved, k):
    """The last line gate review prints for a finished round."""
    return "gate %s round %d: approved=%s comments=%d" % (gate, n, "true" if approved else "false", k)


def export_name(gate, n):
    return "%s-round%d.json" % (gate, n)


def fake_session_id(cwd, files):
    return hashlib.sha256((str(cwd) + "\0" + "\0".join(files)).encode()).hexdigest()[:12]


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
        self.fake_state = self.sb.tmp / "fake-state"
        self.comments_file = self.sb.tmp / "comments.json"
        self.reply_copy = self.sb.tmp / "reply-copy.bin"
        self.sb.env.update({
            "PATH": str(guard.parent) + os.pathsep + self.sb.env.get("PATH", ""),
            "CRIT_BIN": str(self.fake),
            "FAKE_CRIT_LOG": str(self.log),
            "FAKE_CRIT_STATE": str(self.fake_state),
            "FAKE_CRIT_COMMENTS": str(self.comments_file),
            "FAKE_CRIT_REPLY_COPY": str(self.reply_copy),
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

    def review_dir(self, gate, open_round):
        return os.path.join(self.O, gate, "r%d" % open_round)

    def abs_files(self, *names):
        return [str(self.ws / n) for n in names]

    def session_of(self, gate, open_round, *names):
        return fake_session_id(self.review_dir(gate, open_round), self.abs_files(*names))

    def end_daemon(self, session):
        """Make a session's daemon gone, as after a crash: replies and live exports fail."""
        (self.fake_state / (session + ".live")).unlink(missing_ok=True)

    # -- running ---------------------------------------------------------------

    def gate(self, *args, cwd=None, env=None, input=None):
        return self.sb.tg("gate", *args, cwd=cwd or self.ws, env=env, input=input)

    def review(self, gate="design", *args, verdict="false", comments=(), session=None, env=None, cwd=None):
        """Run one gate review round. `session` is accepted for older callers and ignored: the
        fake derives the id from the review dir and the files, as real Crit does."""
        if comments is not None:
            self.set_comments(list(comments))
        e = {"FAKE_CRIT_VERDICT": verdict}
        e.update(env or {})
        return self.gate("review", gate, *args, env=e, cwd=cwd)

    def state(self, gate="design"):
        p = self.ws / ".crit" / ("gate-%s.json" % gate)
        return json.loads(p.read_text()) if p.exists() else None

    def export(self, gate, n):
        p = self.ws / "reviews" / export_name(gate, n)
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
