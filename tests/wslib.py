"""Shared helpers for the workspace test suites (tests/test_workspace_*.py).

Every test gets its own sandbox: a fresh temp dir (outside any git repo) holding
the workspace, holdout and archive roots, an empty HOME, throwaway source repos
and local bare repos standing in for remotes. Nothing touches the network or the
real home directory.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from collections import namedtuple
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "core" / "bin" / "team-guard"
ASK_JEV = ROOT / "core" / "skills" / "jev-decide" / "scripts" / "ask-jev"

Result = namedtuple("Result", "code out err")

TIMEOUT = 60
DAY = 86400


def realpath(p):
    return os.path.realpath(str(p))


def iso_days_ago(days):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days * DAY))


def backdate_tree(path, days):
    """Set atime/mtime of path and everything under it `days` days (plus an hour) back."""
    t = time.time() - days * DAY - 3600
    for dirpath, dirnames, filenames in os.walk(path):
        for name in filenames + dirnames:
            os.utime(os.path.join(dirpath, name), (t, t), follow_symlinks=False)
    os.utime(path, (t, t), follow_symlinks=False)


def tree_snapshot(path, skip=()):
    """Map of relative path -> ('d'|'l:<target>'|'f:<sha256>:<mode>') for everything under path.

    `skip` holds top-level-relative path prefixes to leave out. A missing path
    snapshots as None.
    """
    path = str(path)
    if not os.path.lexists(path):
        return None
    snap = {}
    for dirpath, dirnames, filenames in os.walk(path):
        rel_dir = os.path.relpath(dirpath, path)
        keep = []
        for d in sorted(dirnames):
            rel = os.path.normpath(os.path.join(rel_dir, d))
            if any(rel == s or rel.startswith(s + os.sep) for s in skip):
                continue
            full = os.path.join(dirpath, d)
            if os.path.islink(full):
                snap[rel] = "l:" + os.readlink(full)
                continue
            snap[rel] = "d"
            keep.append(d)
        dirnames[:] = keep
        for f in sorted(filenames):
            rel = os.path.normpath(os.path.join(rel_dir, f))
            if any(rel == s or rel.startswith(s + os.sep) for s in skip):
                continue
            full = os.path.join(dirpath, f)
            if os.path.islink(full):
                snap[rel] = "l:" + os.readlink(full)
            else:
                with open(full, "rb") as fh:
                    digest = hashlib.sha256(fh.read()).hexdigest()
                snap[rel] = "f:%s:%o" % (digest, stat.S_IMODE(os.lstat(full).st_mode))
    return snap


def read_bytes(p):
    try:
        with open(p, "rb") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


class Sandbox:
    """A throwaway environment with its own roots and repos."""

    def __init__(self):
        self.tmp = Path(realpath(tempfile.mkdtemp(prefix="tg-ws-")))
        self.ws_root = self.tmp / "ws"
        self.ho_root = self.tmp / "holdout"
        self.ar_root = self.tmp / "archive"
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.scratch = self.tmp / "scratch"  # a plain, non-git, non-workspace dir
        self.scratch.mkdir()
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("TEAM_", "GIT_")) and k not in ("OPENROUTER_API_KEY", "PWD", "OLDPWD")}
        env.update({
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
            "HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9",
            "https_proxy": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9",
            "TEAM_WORKSPACE_ROOT": str(self.ws_root),
            "TEAM_HOLDOUT_ROOT": str(self.ho_root),
            "TEAM_ARCHIVE_ROOT": str(self.ar_root),
        })
        self.env = env

    def cleanup(self):
        # Undo any chmod a test made, then remove everything.
        for dirpath, dirnames, _ in os.walk(self.tmp):
            for d in dirnames:
                full = os.path.join(dirpath, d)
                if not os.path.islink(full):
                    try:
                        os.chmod(full, 0o755)
                    except OSError:
                        pass
        try:
            os.chmod(self.tmp, 0o755)
        except OSError:
            pass
        subprocess.run(["chmod", "-R", "u+w", str(self.tmp)], capture_output=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- running things ------------------------------------------------------

    def run(self, argv, cwd=None, input=None, env=None, unset=()):
        e = dict(self.env)
        if env:
            e.update({k: str(v) for k, v in env.items()})
        for k in unset:
            e.pop(k, None)
        p = subprocess.run([str(a) for a in argv], cwd=str(cwd or self.scratch), input=input,
                           capture_output=True, text=True, env=e, timeout=TIMEOUT)
        return Result(p.returncode, p.stdout, p.stderr)

    def tg(self, *args, cwd=None, input=None, env=None, unset=()):
        return self.run([str(GUARD), *args], cwd=cwd, input=input, env=env, unset=unset)

    def jev(self, *args, cwd=None, input=None, env=None):
        return self.run([str(ASK_JEV), *args], cwd=cwd, input=input, env=env)

    def git(self, cwd, *args, check=True, env=None):
        r = self.run(["git", *args], cwd=cwd, env=env)
        if check and r.code != 0:
            raise AssertionError("harness: git %s failed in %s: %s" % (" ".join(args), cwd, r.err))
        return r.out.strip() if check else r

    # -- building fixtures ---------------------------------------------------

    def new_repo(self, name, parent=None, branch="main", commit_date=None):
        """A repo with pkg/a.go, pkg/a_test.go, pkg/b_test.go and README.md on `branch`."""
        parent = Path(parent) if parent else self.tmp / "src"
        parent.mkdir(parents=True, exist_ok=True)
        repo = parent / name
        repo.mkdir()
        self.git(repo, "init", "-q", "-b", branch)
        self.git(repo, "config", "user.email", "t@t")
        self.git(repo, "config", "user.name", "t")
        (repo / "pkg").mkdir()
        (repo / "pkg" / "a.go").write_text("package pkg\n")
        (repo / "pkg" / "a_test.go").write_text("func TestA(t *testing.T) {}\n")
        (repo / "pkg" / "b_test.go").write_text("func TestB(t *testing.T) {}\n")
        (repo / "README.md").write_text("# %s\n" % name)
        self.git(repo, "add", "-A")
        env = None
        if commit_date:
            env = {"GIT_COMMITTER_DATE": commit_date, "GIT_AUTHOR_DATE": commit_date}
        self.git(repo, "commit", "-qm", "init", env=env)
        return Path(realpath(repo))

    def add_remote(self, repo, push=("main",)):
        """Give repo a local bare `origin` and push the given branches (updates origin/*)."""
        bare = self.tmp / "remotes" / (Path(repo).name + ".git")
        bare.parent.mkdir(parents=True, exist_ok=True)
        self.git(self.tmp, "init", "-q", "--bare", str(bare))
        self.git(repo, "remote", "add", "origin", str(bare))
        for b in push:
            self.git(repo, "push", "-q", "origin", b)
        return Path(realpath(bare))

    def commit_file(self, cwd, rel, text, msg="change", no_verify=False, env=None):
        p = Path(cwd) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        self.git(cwd, "add", rel)
        args = ["commit", "-qm", msg] + (["--no-verify"] if no_verify else [])
        self.git(cwd, *args, env=env)
        return self.git(cwd, "rev-parse", "HEAD")

    def ws_dir(self, feature):
        return self.ws_root / feature

    def wt(self, feature, repo_name):
        return self.ws_root / feature / "repos" / repo_name

    def load_ws(self, feature):
        with open(self.ws_dir(feature) / "workspace.json") as fh:
            return json.load(fh)

    def source_state(self, repo):
        """Everything about a source repo that team-guard must never change."""
        repo = Path(repo)
        return {
            "status": self.git(repo, "status", "--porcelain", "--ignored"),
            "tree": tree_snapshot(repo, skip=(".git",)),
            "config": read_bytes(repo / ".git" / "config"),
            "head": self.git(repo, "rev-parse", "HEAD"),
            "symref": self.git(repo, "symbolic-ref", "-q", "HEAD", check=False).out.strip(),
            "index": self.git(repo, "ls-files", "-s"),
        }

    def refs(self, repo):
        return self.git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads")

    def worktrees(self, repo):
        out = self.git(repo, "worktree", "list", "--porcelain")
        return [realpath(line[len("worktree "):]) for line in out.splitlines() if line.startswith("worktree ")]

    def roots_snapshot(self):
        return {
            "ws": tree_snapshot(self.ws_root),
            "ho": tree_snapshot(self.ho_root),
            "ar": tree_snapshot(self.ar_root),
        }


class WSTestCase(unittest.TestCase):
    """Base class: a fresh Sandbox per test, plus assertion helpers."""

    maxDiff = 4000

    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.addCleanup(self._home_untouched)

    def _home_untouched(self):
        # AC-70: nothing is written outside the temp dir. HOME points at an empty
        # dir inside it; no command may have written there.
        left = sorted(os.listdir(self.sb.home)) if self.sb.home.exists() else []
        if left:
            raise AssertionError("AC-70: something wrote into HOME: %s" % left)

    # -- assertions ----------------------------------------------------------

    def show(self, r):
        return "exit=%s\n--stdout--\n%s--stderr--\n%s" % (r.code, r.out, r.err)

    def ok(self, r, msg=""):
        """Assert exit 0; on failure show both streams (including 'not implemented')."""
        if r.code != 0:
            self.fail("%sexpected exit 0\n%s" % (msg + ": " if msg else "", self.show(r)))
        return r

    def code(self, r, expected, msg=""):
        if r.code != expected:
            self.fail("%sexpected exit %s\n%s" % (msg + ": " if msg else "", expected, self.show(r)))
        return r

    def nonzero(self, r, msg=""):
        if r.code == 0:
            self.fail("%sexpected non-zero exit\n%s" % (msg + ": " if msg else "", self.show(r)))
        return r

    def has(self, text, needle, r=None, msg=""):
        if needle not in text:
            ctx = self.show(r) if r is not None else text
            self.fail("%sexpected %r in output\n%s" % (msg + ": " if msg else "", needle, ctx))

    def lacks(self, text, needle, r=None, msg=""):
        if needle in text:
            ctx = self.show(r) if r is not None else text
            self.fail("%sdid not expect %r in output\n%s" % (msg + ": " if msg else "", needle, ctx))

    def no_traceback(self, r):
        self.lacks(r.err + r.out, "Traceback", r)

    # -- common fixtures -----------------------------------------------------

    def new_ws(self, feature="f", *extra, cwd=None, env=None):
        r = self.sb.tg("workspace", "new", feature, *extra, cwd=cwd, env=env)
        self.ok(r, "workspace new %s" % feature)
        return self.sb.ws_dir(feature)

    def attach(self, repo, feature="f", *extra, cwd=None, env=None):
        cwd = cwd or self.sb.ws_dir(feature)
        r = self.sb.tg("workspace", "attach", str(repo), *extra, cwd=cwd, env=env)
        self.ok(r, "workspace attach %s" % repo)
        return self.sb.wt(feature, Path(realpath(repo)).name)

    def two_repo_ws(self, feature="f", remotes=False):
        """ws `feature` with repos A and B attached. Returns (ws, A, B, wtA, wtB)."""
        A = self.sb.new_repo("A")
        B = self.sb.new_repo("B")
        if remotes:
            self.sb.add_remote(A)
            self.sb.add_remote(B)
        ws = self.new_ws(feature)
        wtA = self.attach(A, feature)
        wtB = self.attach(B, feature)
        return ws, A, B, wtA, wtB

    def pretool(self, cwd, agent, tool, path=None, patch=None, proc_cwd=None):
        ev = {"cwd": str(cwd), "tool_name": tool}
        if agent is not None:
            ev["agent_type"] = agent
        if patch is not None:
            ev["tool_input"] = {"command": patch}
        else:
            ev["tool_input"] = {"file_path": str(path)}
        return self.sb.tg("hook-pretool", input=json.dumps(ev), cwd=proc_cwd or self.sb.scratch)

    def assert_deny(self, r, *needles):
        self.code(r, 0, "hook-pretool always exits 0")
        try:
            data = json.loads(r.out)
        except ValueError:
            self.fail("expected a JSON deny decision\n%s" % self.show(r))
        hso = data.get("hookSpecificOutput", {})
        self.assertEqual(hso.get("permissionDecision"), "deny", self.show(r))
        self.assertEqual(hso.get("hookEventName"), "PreToolUse", self.show(r))
        reason = hso.get("permissionDecisionReason", "")
        for n in needles:
            self.has(reason, n, r)
        return reason

    def assert_no_decision(self, r):
        self.code(r, 0, "hook-pretool always exits 0")
        self.assertEqual(r.out, "", "expected no decision\n%s" % self.show(r))
        self.no_traceback(r)


def patch_text(*ops):
    """Build apply_patch text. ops: ('update'|'add'|'delete', path)."""
    lines = ["*** Begin Patch"]
    for op, path in ops:
        if op == "update":
            lines += ["*** Update File: %s" % path, "@@", "+// y"]
        elif op == "add":
            lines += ["*** Add File: %s" % path, "+package x"]
        elif op == "delete":
            lines += ["*** Delete File: %s" % path]
    lines.append("*** End Patch")
    return "\n".join(lines)


LIST_LINE = re.compile(r"^(?P<feature>[^\t]+)\t(?P<status>active|merged|stale|invalid)\t(?P<age>[0-9]+d|-)\t(?P<size>[0-9]+(\.[0-9])?[BKMGT]|-)$")
