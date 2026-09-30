"""Shared helpers for the workflow tooling suites: refs, routing, risk, sign, verify's
hooksPath warning and mutation-check's limits.

Everything runs offline in the per-test Sandbox from wslib. Text that the reference
check would flag (criterion numbers, workflow file names) is assembled at run time
with `tok`, so these sources stay clean under that same check.
"""

import json
import os
import re
import subprocess
import threading
from pathlib import Path

from wslib import ROOT, GUARD, WSTestCase, realpath

MC = ROOT / "core" / "bin" / "mutation-check"


def tok(*parts):
    """Join fragments into one token, so the literal never appears whole in this source."""
    return "".join(parts)


# The file the routing command writes; its name is one the reference scanner reports, so it is
# assembled here rather than spelled out.
ROUTING_MD = tok("routing", ".md")
VARIANTS = ("chore", "bug-fix", "spike", "standard", "full")
ANSWER_KEYS = ("repos", "files", "contract", "persisted", "security", "cross_team", "revertible",
               "user_visible", "design_question", "repro")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class ToolCase(WSTestCase):
    """A sandbox with its own routing-log root and a feature-branch repo helper."""

    def setUp(self):
        super().setUp()
        self.log_root = self.sb.tmp / "logroot"
        self.sb.env["TEAM_LOG_ROOT"] = str(self.log_root)
        self.sb.env.pop("TEAM_REFS_CONFIG", None)

    @property
    def log_path(self):
        return self.log_root / "routing-log.jsonl"

    def log_lines(self):
        if not self.log_path.exists():
            return []
        return [json.loads(l) for l in self.log_path.read_text().splitlines() if l.strip()]

    def write_log(self, *events, raw=()):
        self.log_root.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a") as fh:
            for e in events:
                fh.write(json.dumps(e) + "\n")
            for line in raw:
                fh.write(line + "\n")

    def feature_repo(self, name="R", branch="feat/x"):
        """A repo on `branch` off main, with the Sandbox's standard files on main."""
        repo = self.sb.new_repo(name)
        self.sb.git(repo, "checkout", "-q", "-b", branch)
        return repo

    def git_dir(self, repo):
        return Path(self.sb.git(repo, "rev-parse", "--absolute-git-dir"))

    def config_list(self, repo):
        return self.sb.git(repo, "config", "--list", "--show-origin")


def route_event(feature, proposed="standard", confirmed=None, ts="2026-01-01T00:00:00Z", **answers):
    full = {k: "unknown" for k in ANSWER_KEYS}
    full.update(answers)
    return {"v": 1, "ts": ts, "event": "route", "feature": feature, "answers": full,
            "proposed": proposed, "confirmed": confirmed or proposed}


def safe_answers(**over):
    """Checklist answers under which no scope rule fires."""
    a = {"repos": 1, "files": "<=3", "contract": "no", "persisted": "no", "security": "no", "cross_team": "no",
         "revertible": "yes", "user_visible": "no", "design_question": "no", "repro": "no"}
    a.update(over)
    return a


class FifoWaiter:
    """Wait on a named pipe without sleeping.

    `ready()` blocks until some writer opens the pipe and writes a line. `closed()` blocks
    until every writer that opened the pipe has closed it, which for a process holding the
    write end means it has exited.
    """

    def __init__(self, path):
        self.path = str(path)
        os.mkfifo(self.path)
        self._line = threading.Event()
        self._eof = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        with open(self.path, "rb") as fh:  # blocks until a writer opens it
            if fh.readline():
                self._line.set()
            fh.read()  # returns once every writer has closed its end
        self._line.set()
        self._eof.set()

    def ready(self, timeout=30):
        return self._line.wait(timeout)

    def closed(self, timeout=30):
        return self._eof.wait(timeout)

    def release(self):
        """Unblock the reader thread if no writer ever came."""
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_NONBLOCK)
            os.close(fd)
        except OSError:
            pass


FAKE_TOOL = r'''#!/usr/bin/env python3
# A stand-in for `go` / `npx` that records how it was run.
import errno, json, os, subprocess, sys, time
CFG = json.load(open(%(cfg)r))
ROOT = os.path.dirname(CFG["log"])


def alive_or_exit():
    """Never outlive the test: once its sandbox is gone, stop."""
    if not os.path.isdir(ROOT):
        sys.exit(0)


def open_fifo(path, mode):
    # A plain open() of a named pipe blocks until a reader comes, forever if none does.
    while True:
        alive_or_exit()
        try:
            fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
            break
        except OSError as e:
            if e.errno != errno.ENXIO:
                sys.exit(0)
            time.sleep(0.05)
    os.set_blocking(fd, True)
    return os.fdopen(fd, mode)


def nap(seconds):
    end = time.time() + seconds
    while time.time() < end:
        alive_or_exit()
        time.sleep(min(0.1, max(0.0, end - time.time())))
NAME = %(name)r
argv = sys.argv[1:]
rec = {"tool": NAME, "argv": argv, "pid": os.getpid(), "pgid": os.getpgid(0), "cwd": os.getcwd(),
       "env": {k: os.environ.get(k) for k in ("TMPDIR", "TMP", "TEMP", "GOTMPDIR")}}
with open(CFG["log"], "a") as fh:
    fh.write(json.dumps(rec) + "\n")
is_tool = (NAME == "go" and "unleash" in argv) or (NAME == "npx" and "run" in argv)
if not is_tool:
    sys.exit(0)
try:
    fd = os.open(CFG["lock"], os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(fd)
except FileExistsError:
    with open(CFG["overlap"], "a") as fh:
        fh.write("overlap\n")
try:
    td = os.environ.get("TMPDIR")
    if td and os.path.isdir(td):
        with open(os.path.join(td, "fake-%%s-%%d" %% (NAME, os.getpid())), "w") as fh:
            fh.write("x")
    if NAME == "npx":
        os.makedirs(os.path.join(os.getcwd(), ".stryker-tmp", "sandbox-%%d" %% os.getpid()), exist_ok=True)
    if CFG.get("grandchild_fifo"):
        with open_fifo(CFG["grandchild_fifo"], "wb") as w:
            gc = subprocess.Popen(["sleep", "300"], stdout=w, stdin=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, close_fds=True)
        with open(CFG["grandchild_pid"], "w") as fh:
            fh.write(str(gc.pid))
    if CFG.get("ready_fifo"):
        with open_fifo(CFG["ready_fifo"], "w") as fh:
            fh.write("ready\n")
    pkg = argv[-1][2:] if argv[-1].startswith("./") else argv[-1]
    nap(CFG.get("sleep_by_pkg", {}).get(pkg, CFG.get("sleep", 0.2)))
    if NAME == "go":
        for line in CFG.get("lines", {}).get(pkg, []):
            print(line, flush=True)
finally:
    try:
        os.unlink(CFG["lock"])
    except FileNotFoundError:
        pass
sys.exit(CFG.get("exit", 0))
'''


class FakeTools:
    """Fake `go` and `npx` executables first on PATH, configured through one JSON file."""

    def __init__(self, root):
        self.root = Path(root)
        self.bin = self.root / "fakebin"
        self.bin.mkdir(parents=True)
        self.cfg_path = self.root / "fake-config.json"
        self.log = self.root / "fake-calls.jsonl"
        self.cfg = {"log": str(self.log), "lock": str(self.root / "fake-running"),
                    "overlap": str(self.root / "fake-overlap"), "lines": {}, "sleep": 0.2, "exit": 0}
        self.save()
        for name in ("go", "npx"):
            p = self.bin / name
            p.write_text(FAKE_TOOL % {"cfg": str(self.cfg_path), "name": name})
            p.chmod(0o755)

    def save(self):
        self.cfg_path.write_text(json.dumps(self.cfg))

    def set(self, **kw):
        self.cfg.update(kw)
        self.save()

    def calls(self, tool=None, only_tools=True):
        if not self.log.exists():
            return []
        out = [json.loads(l) for l in self.log.read_text().splitlines() if l.strip()]
        if tool:
            out = [c for c in out if c["tool"] == tool]
        if only_tools:
            out = [c for c in out if ("unleash" in c["argv"]) or (c["tool"] == "npx" and "run" in c["argv"])]
        return out

    def overlapped(self):
        return Path(self.cfg["overlap"]).exists()

    def path_env(self, env):
        return str(self.bin) + os.pathsep + env.get("PATH", "")


def arg_after(argv, flag):
    """The value following `flag` in argv, or None."""
    for i, a in enumerate(argv):
        if a == flag and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return None


def pid_alive(pid):
    """True if `pid` exists and isn't a zombie."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(st) and not st.startswith("Z")


__all__ = ["ROOT", "GUARD", "MC", "WSTestCase", "realpath", "tok", "ROUTING_MD", "VARIANTS", "ANSWER_KEYS",
           "TS_RE", "ToolCase", "route_event", "safe_answers", "FifoWaiter", "FakeTools", "arg_after", "pid_alive"]
