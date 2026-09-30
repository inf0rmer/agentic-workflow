"""Workspace creation, inspection, attach and discovery (spec sections A, B, C)."""

import json
import os
import random
import re
import unittest
from pathlib import Path

from wslib import (LIST_LINE, WSTestCase, iso_days_ago, read_bytes, realpath,
                   tree_snapshot)

TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class TestWorkspaceNew(WSTestCase):

    def test_ac1_new_creates_workspace_json(self):
        r = self.sb.tg("workspace", "new", "f")
        self.ok(r)
        ws = realpath(self.sb.ws_dir("f"))
        self.has(r.out, "team-guard: created workspace f at %s" % ws, r)
        data = self.sb.load_ws("f")
        self.assertEqual(data["feature"], "f")
        self.assertEqual(data["repos"], [])
        self.assertEqual(data["branch"], "feat/f")
        self.assertRegex(data["created"], TS)

    def test_ac1_branch_option(self):
        self.ok(self.sb.tg("workspace", "new", "g", "--branch", "team/custom"))
        self.assertEqual(self.sb.load_ws("g")["branch"], "team/custom")

    def test_ac1_invalid_branch_option_creates_nothing(self):
        r = self.sb.tg("workspace", "new", "g", "--branch", "bad..name")
        self.code(r, 1)
        self.has(r.err, "invalid branch name 'bad..name'", r)
        self.assertFalse(self.sb.ws_dir("g").exists())

    def test_ac2_new_creates_only_repos_dir(self):
        ws = self.new_ws("f")
        self.assertTrue((ws / "repos").is_dir())
        self.assertEqual(os.listdir(ws / "repos"), [])
        for dirpath, dirnames, filenames in os.walk(ws):
            self.assertNotIn(".git", dirnames + filenames, "no git repo or worktree under the workspace")
        self.assertFalse((self.sb.ho_root / "f").exists(), "no holdout dir is created by new")

    def test_ac3_existing_dir_is_left_alone(self):
        ws = self.sb.ws_dir("f")
        (ws / "x").mkdir(parents=True)
        (ws / "x" / "notes.md").write_text("keep me\n")
        before = tree_snapshot(self.sb.ws_root)
        r = self.sb.tg("workspace", "new", "f")
        self.code(r, 1)
        self.has(r.err, "workspace f already exists at %s" % realpath(ws), r)
        self.assertEqual(tree_snapshot(self.sb.ws_root), before)

    def test_ac3_existing_workspace_is_left_alone(self):
        self.new_ws("f")
        before = tree_snapshot(self.sb.ws_root)
        json_before = read_bytes(self.sb.ws_dir("f") / "workspace.json")
        r = self.sb.tg("workspace", "new", "f")
        self.code(r, 1)
        self.has(r.err, "already exists", r)
        self.assertEqual(tree_snapshot(self.sb.ws_root), before)
        self.assertEqual(read_bytes(self.sb.ws_dir("f") / "workspace.json"), json_before)

    def test_ac4_invalid_names_property(self):
        fixed = ["", ".", "..", "a/b", "a b", "\t", "a\\b", "a..b", "a.", "x.lock", " a", "a\nb", "/abs", "_a", ".hidden"]
        rng = random.Random(4)
        alphabet = "abcXYZ019._-"
        bad_chars = ["/", "\\", " ", "\t", "\n"]
        generated = []
        for _ in range(12):
            base = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 6)))
            base = "a" + base  # a valid start, so only the injected char makes it invalid
            pos = rng.randint(0, len(base))
            generated.append(base[:pos] + rng.choice(bad_chars) + base[pos:])
        for name in fixed + generated:
            with self.subTest(name=name):
                r = self.sb.tg("workspace", "new", "--", name) if name.startswith("-") else self.sb.tg("workspace", "new", name)
                self.code(r, 1, "name %r" % name)
                self.has(r.err, "invalid feature name", r)
                entries = os.listdir(self.sb.ws_root) if self.sb.ws_root.exists() else []
                self.assertEqual(entries, [], "name %r must create nothing" % name)

    def test_ac4_leading_dash_is_invalid(self):
        r = self.sb.tg("workspace", "new", "--", "-a")
        self.code(r, 1)
        self.has(r.err, "invalid feature name", r)
        r = self.sb.tg("workspace", "new", "-a")
        self.nonzero(r)
        entries = os.listdir(self.sb.ws_root) if self.sb.ws_root.exists() else []
        self.assertEqual(entries, [])

    def test_ac4_valid_names_property(self):
        rng = random.Random(44)
        alphabet = "abcdefXYZ0123456789_-"
        names = {"a", "A1", "feat-1", "x_y", "v1.2", "0"}
        while len(names) < 16:
            names.add(rng.choice("abcXYZ019") + "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 10))))
        for name in sorted(names):
            with self.subTest(name=name):
                r = self.sb.tg("workspace", "new", name)
                self.ok(r, "valid name %r" % name)
                self.assertEqual(self.sb.load_ws(name)["feature"], name)

    def test_ac5_cwd_independence(self):
        repo = self.sb.new_repo("R")
        before = self.sb.source_state(repo)
        r1 = self.sb.tg("workspace", "new", "one", cwd=self.sb.scratch)
        r2 = self.sb.tg("workspace", "new", "two", cwd=repo / "pkg")
        self.ok(r1)
        self.ok(r2)
        self.assertEqual(r1.out.replace("one", "X"), r2.out.replace("two", "X"))
        self.assertEqual(sorted(self.sb.load_ws("one")), sorted(self.sb.load_ws("two")))
        self.assertEqual(self.sb.load_ws("two")["repos"], [])
        self.assertEqual(self.sb.source_state(repo), before, "new must not touch the repo it runs in")
        self.assertEqual(os.listdir(self.sb.scratch), [])


class TestWorkspaceListShow(WSTestCase):

    def test_ac6_list_empty(self):
        r = self.sb.tg("workspace", "list")
        self.ok(r)
        self.assertEqual(r.out, "", "no workspace root yet")
        self.sb.ws_root.mkdir()
        (self.sb.ws_root / ".hidden").mkdir()
        r = self.sb.tg("workspace", "list")
        self.ok(r)
        self.assertEqual(r.out, "", "dot dirs are ignored")

    def test_ac6_ac73_list_lines(self):
        self.new_ws("b")
        self.new_ws("a")
        r = self.ok(self.sb.tg("workspace", "list"))
        lines = r.out.splitlines()
        self.assertEqual(len(lines), 2, self.show(r))
        self.assertEqual([l.split("\t")[0] for l in lines], ["a", "b"])
        for line in lines:
            m = LIST_LINE.match(line)
            self.assertIsNotNone(m, "line format: %r" % line)
            self.assertIn(m.group("status"), ("active", "merged", "stale"))
            self.assertEqual(m.group("age"), "0d")
            self.assertRegex(m.group("size"), r"^[0-9]+(\.[0-9])?[BKMGT]$")
        self.assertEqual(LIST_LINE.match(lines[0]).group("status"), "active")

    def test_ac73_age_from_created(self):
        self.new_ws("f")
        p = self.sb.ws_dir("f") / "workspace.json"
        data = json.loads(p.read_text())
        data["created"] = iso_days_ago(10)
        p.write_text(json.dumps(data))
        r = self.ok(self.sb.tg("workspace", "list"))
        self.assertRegex(r.out, r"^f\t(active|merged|stale)\t10d\t[0-9]+(\.[0-9])?[BKMGT]\n$")

    def test_ac7_show(self):
        A = self.sb.new_repo("A")
        B = self.sb.new_repo("B")
        self.sb.git(B, "branch", "dev")
        ws = self.new_ws("f")
        wtA = self.attach(A)
        wtB = self.attach(B, "f", "--base", "dev")
        r = self.ok(self.sb.tg("workspace", "show", "f"))
        expected = [
            "workspace: %s" % realpath(ws),
            "feature: f",
            "branch: feat/f",
            "repo A source=%s worktree=%s base=main" % (A, realpath(wtA)),
            "repo B source=%s worktree=%s base=dev" % (B, realpath(wtB)),
        ]
        lines = r.out.splitlines()
        idx = []
        for e in expected:
            self.assertIn(e, lines, self.show(r))
            idx.append(lines.index(e))
        self.assertEqual(idx, sorted(idx), "show lines are in the documented order")

    def test_ac8_show_unknown(self):
        r = self.sb.tg("workspace", "show", "nope")
        self.code(r, 1)
        self.has(r.err, "team-guard: no workspace named nope", r)
        self.assertFalse(self.sb.ws_dir("nope").exists())

    def test_ac9_show_from_inside(self):
        A = self.sb.new_repo("A")
        ws = self.new_ws("f")
        wtA = self.attach(A)
        (wtA / "pkg" / "sub").mkdir()
        ref = self.ok(self.sb.tg("workspace", "show", "f"))
        for cwd in (ws, wtA / "pkg" / "sub", wtA):
            with self.subTest(cwd=str(cwd)):
                r = self.ok(self.sb.tg("workspace", "show", cwd=cwd))
                self.assertEqual(r.out, ref.out)

    def test_ac9_show_outside_without_feature(self):
        r = self.sb.tg("workspace", "show", cwd=self.sb.scratch)
        self.code(r, 1)
        self.has(r.err, "no workspace found", r)


class TestBadWorkspaceJson(WSTestCase):
    """AC-10: missing or invalid workspace.json is reported, never rewritten."""

    def _check_all(self, corrupt):
        A = self.sb.new_repo("A")
        ws = self.new_ws("f")
        wj = ws / "workspace.json"
        if corrupt:
            wj.write_text("{")
        else:
            wj.unlink()
        before = read_bytes(wj)
        name = realpath(ws) + "/workspace.json"
        cases = {
            "show": dict(args=("workspace", "show", "f"), cwd=self.sb.scratch),
            "attach": dict(args=("workspace", "attach", str(A)), cwd=ws),
            "attach --feature": dict(args=("workspace", "attach", str(A), "--feature", "f"), cwd=self.sb.scratch),
            "verify": dict(args=("verify",), cwd=ws),
            "close": dict(args=("workspace", "close", "f"), cwd=self.sb.scratch),
            "list": dict(args=("workspace", "list"), cwd=self.sb.scratch),
            "gc": dict(args=("gc",), cwd=self.sb.scratch),
        }
        if not corrupt:
            cases["workspace-path"] = dict(args=("workspace-path",), cwd=ws)
        for label, c in cases.items():
            with self.subTest(cmd=label, corrupt=corrupt):
                r = self.sb.tg(*c["args"], cwd=c["cwd"])
                self.nonzero(r, label)
                self.has(r.err, name, r, label)
                self.no_traceback(r)
                self.assertEqual(read_bytes(wj), before, "%s must not modify workspace.json" % label)
        self.assertEqual(os.listdir(ws / "repos"), [], "attach must not have created a worktree")
        self.assertEqual(len(self.sb.worktrees(A)), 1)
        # Specific exit codes from the contract.
        self.code(self.sb.tg("verify", cwd=ws), 2, "verify fails closed on a bad workspace.json")
        r = self.sb.tg("workspace", "list")
        self.code(r, 1)
        self.assertIn("f\tinvalid\t-\t-", r.out.splitlines(), self.show(r))

    def test_ac10_missing_workspace_json(self):
        self._check_all(corrupt=False)

    def test_ac10_invalid_workspace_json(self):
        self._check_all(corrupt=True)

    def test_ac10_list_reports_others_first(self):
        self.new_ws("a")
        self.new_ws("b")
        (self.sb.ws_dir("a") / "workspace.json").write_text("not json")
        r = self.sb.tg("workspace", "list")
        self.code(r, 1)
        lines = r.out.splitlines()
        self.assertEqual([l.split("\t")[0] for l in lines], ["a", "b"], self.show(r))
        self.assertEqual(lines[0], "a\tinvalid\t-\t-")
        self.assertRegex(lines[1], r"^b\tactive\t0d\t")


class TestAttach(WSTestCase):

    def test_ac11_attach_creates_worktree(self):
        A = self.sb.new_repo("A")
        ws = self.new_ws("f")
        r = self.sb.tg("workspace", "attach", str(A), cwd=ws)
        self.ok(r)
        wt = realpath(ws / "repos" / "A")
        self.has(r.out, "team-guard: attached A at %s on feat/f from main" % wt, r)
        self.assertTrue(os.path.isdir(wt))
        self.assertEqual(self.sb.git(wt, "rev-parse", "--abbrev-ref", "HEAD"), "feat/f")
        self.assertIn(wt, self.sb.worktrees(A))
        self.assertEqual(self.sb.git(wt, "rev-parse", "HEAD"), self.sb.git(A, "rev-parse", "main"))

    def test_ac12_json_entry(self):
        A = self.sb.new_repo("A")
        ws = self.new_ws("f")
        wt = self.attach(A)
        data = self.sb.load_ws("f")
        self.assertEqual(len(data["repos"]), 1)
        e = data["repos"][0]
        self.assertEqual(e["name"], "A")
        self.assertEqual(e["source"], realpath(A))
        self.assertEqual(e["worktree"], realpath(wt))
        self.assertEqual(e["branch"], "feat/f")
        self.assertEqual(e["base"], "main")
        self.assertEqual(e["start"], self.sb.git(A, "rev-parse", "main"))
        self.assertIs(e["reused"], False)
        self.assertRegex(e["attached"], TS)

    def test_ac12_source_resolves_symlinks_subdirs_and_linked_worktrees(self):
        A = self.sb.new_repo("A")
        link = self.sb.tmp / "link-to-A"
        os.symlink(A, link)
        other_wt = self.sb.tmp / "A-linked"
        self.sb.git(A, "worktree", "add", "-q", str(other_wt), "-b", "side")
        for feature, path in (("g", link), ("h", A / "pkg"), ("i", other_wt / "pkg")):
            with self.subTest(via=str(path)):
                ws = self.new_ws(feature)
                r = self.sb.tg("workspace", "attach", str(path), cwd=ws)
                self.ok(r)
                e = self.sb.load_ws(feature)["repos"][0]
                self.assertEqual(e["source"], realpath(A))
                self.assertEqual(e["name"], "A")
                self.assertTrue((ws / "repos" / "A").is_dir())

    def test_ac13_explicit_base(self):
        A = self.sb.new_repo("A")
        self.sb.git(A, "checkout", "-q", "-b", "dev")
        dev_tip = self.sb.commit_file(A, "pkg/dev.go", "package pkg\n")
        self.sb.git(A, "checkout", "-q", "main")
        ws = self.new_ws("f")
        r = self.sb.tg("workspace", "attach", str(A), "--base", "dev", cwd=ws)
        self.ok(r)
        self.has(r.out, "from dev", r)
        wt = ws / "repos" / "A"
        self.assertEqual(self.sb.git(wt, "rev-parse", "HEAD"), dev_tip)
        self.assertEqual(self.sb.load_ws("f")["repos"][0]["base"], "dev")

    def test_ac13_default_base_from_origin_head(self):
        C = self.sb.new_repo("C")
        self.sb.git(C, "checkout", "-q", "-b", "trunk")
        trunk_tip = self.sb.commit_file(C, "pkg/t.go", "package pkg\n")
        self.sb.git(C, "checkout", "-q", "main")
        self.sb.add_remote(C, push=("main", "trunk"))
        self.sb.git(C, "branch", "-q", "-D", "trunk")  # only refs/remotes/origin/trunk remains
        self.sb.git(C, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
        ws = self.new_ws("f")
        r = self.sb.tg("workspace", "attach", str(C), cwd=ws)
        self.ok(r)
        self.has(r.out, "from trunk", r)
        self.assertEqual(self.sb.load_ws("f")["repos"][0]["base"], "trunk")
        self.assertEqual(self.sb.git(ws / "repos" / "C", "rev-parse", "HEAD"), trunk_tip)

    def test_ac13_default_base_without_origin_is_main(self):
        A = self.sb.new_repo("A")
        self.sb.git(A, "checkout", "-q", "-b", "elsewhere")
        self.sb.commit_file(A, "pkg/e.go", "package pkg\n")
        ws = self.new_ws("f")
        self.attach(A)
        self.assertEqual(self.sb.load_ws("f")["repos"][0]["base"], "main")
        self.assertEqual(self.sb.git(ws / "repos" / "A", "rev-parse", "HEAD"), self.sb.git(A, "rev-parse", "main"))

    def test_ac13_base_is_a_branch_name(self):
        C = self.sb.new_repo("C")
        self.sb.add_remote(C)
        self.sb.git(C, "branch", "-q", "-m", "main", "other")  # local main gone; origin/main remains
        ws = self.new_ws("f")
        r = self.sb.tg("workspace", "attach", str(C), "--base", "origin/main", cwd=ws)
        self.code(r, 1, "origin/main names a branch called origin/main, which doesn't exist")
        self.has(r.err, "base branch origin/main not found", r)

    def test_ac14_same_branch_name(self):
        ws, A, B, wtA, wtB = self.two_repo_ws()
        for wt in (wtA, wtB):
            self.assertEqual(self.sb.git(wt, "rev-parse", "--abbrev-ref", "HEAD"), "feat/f")
        self.assertEqual([e["branch"] for e in self.sb.load_ws("f")["repos"]], ["feat/f", "feat/f"])

    def test_ac14_custom_branch_everywhere(self):
        A = self.sb.new_repo("A")
        B = self.sb.new_repo("B")
        self.new_ws("g", "--branch", "team/x")
        wtA = self.attach(A, "g")
        wtB = self.attach(B, "g")
        for wt in (wtA, wtB):
            self.assertEqual(self.sb.git(wt, "rev-parse", "--abbrev-ref", "HEAD"), "team/x")

    def test_ac15_source_untouched(self):
        A = self.sb.new_repo("A")
        # A dirty, partly staged source checkout must survive attach byte for byte.
        (A / "pkg" / "a.go").write_text("package pkg // staged\n")
        self.sb.git(A, "add", "pkg/a.go")
        (A / "README.md").write_text("dirty\n")
        (A / "untracked.txt").write_text("u\n")
        before = self.sb.source_state(A)
        cfg_list = self.sb.git(A, "config", "--list", "--show-origin")
        self.new_ws("f")
        self.attach(A)
        self.assertEqual(self.sb.source_state(A), before)
        self.assertEqual(self.sb.git(A, "config", "--list", "--show-origin"), cfg_list)
        for dirpath, _, filenames in os.walk(A / ".git"):
            self.assertNotIn("config.worktree", filenames, "no worktree-scoped config is created")
        self.assertEqual(self.sb.git(A, "config", "--get", "branch.feat/f.remote", check=False).code, 1,
                         "the branch gets no upstream")

    def test_ac16_clean_worktree_and_no_hooks_run(self):
        A = self.sb.new_repo("A")
        marker = self.sb.tmp / "post-checkout-ran"
        hook = A / ".git" / "hooks" / "post-checkout"
        hook.write_text("#!/bin/sh\ntouch '%s'\ntouch hook-was-here\n" % marker)
        hook.chmod(0o755)
        before = self.sb.source_state(A)
        self.new_ws("f")
        wt = self.attach(A)
        self.assertEqual(self.sb.git(wt, "status", "--porcelain"), "")
        self.assertEqual(self.sb.git(wt, "status", "--porcelain", "--ignored"), "")
        self.assertEqual(self.sb.git(wt, "ls-files"), self.sb.git(A, "ls-tree", "-r", "--name-only", "main"))
        self.assertFalse(marker.exists(), "the source repo's hooks must not run during attach")
        self.assertFalse((wt / "hook-was-here").exists())
        self.assertEqual(self.sb.source_state(A), before)

    def test_ac17_pointer_scope(self):
        A = self.sb.new_repo("A")
        cfg_before = read_bytes(A / ".git" / "config")
        cfg_list = self.sb.git(A, "config", "--list", "--show-origin")
        ws = self.new_ws("f")
        wt = self.attach(A)
        r = self.ok(self.sb.tg("workspace-path", cwd=wt))
        self.assertEqual(r.out, realpath(ws) + "\n")
        r = self.sb.tg("workspace-path", cwd=A)
        self.code(r, 1)
        self.assertEqual(r.out, "")
        gd = self.sb.git(wt, "rev-parse", "--absolute-git-dir")
        self.assertEqual(Path(gd, "team-workspace").read_text(), realpath(ws) + "\n")
        self.assertFalse((A / ".git" / "team-workspace").exists())
        self.assertEqual(read_bytes(A / ".git" / "config"), cfg_before)
        self.assertEqual(self.sb.git(A, "config", "--list", "--show-origin"), cfg_list)


class TestAttachRefusals(WSTestCase):
    """AC-18..AC-25: every refused attach leaves the workspace and the sources as they were."""

    def snap(self, *repos):
        return (tree_snapshot(self.sb.ws_root),
                [self.sb.source_state(r) for r in repos],
                [self.sb.refs(r) for r in repos],
                [self.sb.worktrees(r) for r in repos])

    def test_ac18_not_a_repo(self):
        ws = self.new_ws("f")
        plain = self.sb.tmp / "plain"
        plain.mkdir()
        before = tree_snapshot(self.sb.ws_root)
        for path in (plain, self.sb.tmp / "does-not-exist"):
            with self.subTest(path=str(path)):
                r = self.sb.tg("workspace", "attach", str(path), cwd=ws)
                self.code(r, 1)
                self.has(r.err, "is not a git repository", r)
                self.no_traceback(r)
                self.assertEqual(tree_snapshot(self.sb.ws_root), before)

    def test_bare_repo_refused(self):
        ws = self.new_ws("f")
        bare = self.sb.tmp / "bare.git"
        self.sb.git(self.sb.tmp, "init", "-q", "--bare", str(bare))
        before = tree_snapshot(self.sb.ws_root)
        r = self.sb.tg("workspace", "attach", str(bare), cwd=ws)
        self.code(r, 1)
        self.has(r.err, "bare repositories are not supported", r)
        self.assertEqual(tree_snapshot(self.sb.ws_root), before)

    def test_ac19_already_attached(self):
        A = self.sb.new_repo("A")
        link = self.sb.tmp / "A-link"
        os.symlink(A, link)
        ws = self.new_ws("f")
        wt = self.attach(A)
        head = self.sb.git(wt, "rev-parse", "HEAD")
        before = self.snap(A)
        for path in (A, A / "pkg", link):
            with self.subTest(path=str(path)):
                r = self.sb.tg("workspace", "attach", str(path), cwd=ws)
                self.code(r, 1)
                self.has(r.err, "%s is already attached to f at %s" % (realpath(A), realpath(wt)), r)
                self.assertEqual(self.snap(A), before)
                self.assertEqual(self.sb.git(wt, "rev-parse", "HEAD"), head)

    def test_ac20_same_name_different_repo(self):
        x = self.sb.new_repo("api", parent=self.sb.tmp / "x")
        y = self.sb.new_repo("api", parent=self.sb.tmp / "y")
        ws = self.new_ws("f")
        self.attach(x)
        before = self.snap(x, y)
        r = self.sb.tg("workspace", "attach", str(y), cwd=ws)
        self.code(r, 1)
        self.has(r.err, "can't attach %s as api: %s is already attached as api" % (realpath(y), realpath(x)), r)
        self.assertEqual(self.snap(x, y), before)

    def test_ac20_repos_dir_exists_on_disk(self):
        B = self.sb.new_repo("B")
        ws = self.new_ws("f")
        (ws / "repos" / "B").mkdir()
        before = self.snap(B)
        r = self.sb.tg("workspace", "attach", str(B), cwd=ws)
        self.code(r, 1)
        self.has(r.err, "can't attach %s as B: %s already exists" % (realpath(B), realpath(ws / "repos" / "B")), r)
        self.assertEqual(self.snap(B), before)

    def test_ac21_branch_checked_out_in_source(self):
        A = self.sb.new_repo("A")
        self.sb.git(A, "checkout", "-q", "-b", "feat/f")
        ws = self.new_ws("f")
        before = self.snap(A)
        r = self.sb.tg("workspace", "attach", str(A), cwd=ws)
        self.code(r, 1)
        self.has(r.err, "branch feat/f is already checked out in %s" % realpath(A), r)
        self.assertEqual(self.snap(A), before)

    def test_ac21_branch_checked_out_in_other_worktree(self):
        B = self.sb.new_repo("B")
        other = self.sb.tmp / "B-other"
        self.sb.git(B, "worktree", "add", "-q", str(other), "-b", "feat/f")
        ws = self.new_ws("f")
        before = self.snap(B)
        r = self.sb.tg("workspace", "attach", str(B), cwd=ws)
        self.code(r, 1)
        self.has(r.err, "branch feat/f is already checked out in %s" % realpath(other), r)
        self.assertEqual(self.snap(B), before)

    def test_ac22_missing_base(self):
        A = self.sb.new_repo("A")
        ws = self.new_ws("f")
        before = self.snap(A)
        branches = self.sb.git(A, "branch")
        r = self.sb.tg("workspace", "attach", str(A), "--base", "nope", cwd=ws)
        self.code(r, 1)
        self.has(r.err, "base branch nope not found in %s" % realpath(A), r)
        self.assertEqual(self.snap(A), before)
        self.assertEqual(self.sb.git(A, "branch"), branches)

    def test_ac23_outside_workspace(self):
        A = self.sb.new_repo("A")
        self.new_ws("f")
        before = self.snap(A)
        for cwd in (self.sb.scratch, A):
            with self.subTest(cwd=str(cwd)):
                r = self.sb.tg("workspace", "attach", str(A), cwd=cwd)
                self.code(r, 1)
                self.has(r.err, "no workspace found", r)
                self.has(r.err, "team-guard workspace new", r)
                self.assertEqual(self.snap(A), before)

    def test_ac24_feature_option(self):
        A = self.sb.new_repo("A")
        self.new_ws("f")
        r = self.sb.tg("workspace", "attach", str(A), "--feature", "f", cwd=self.sb.scratch)
        self.ok(r)
        self.assertEqual([e["name"] for e in self.sb.load_ws("f")["repos"]], ["A"])
        self.assertTrue(self.sb.wt("f", "A").is_dir())

    def test_ac24_feature_option_beats_cwd(self):
        A = self.sb.new_repo("A")
        wsf = self.new_ws("f")
        self.new_ws("g")
        self.ok(self.sb.tg("workspace", "attach", str(A), "--feature", "g", cwd=wsf))
        self.assertEqual(self.sb.load_ws("f")["repos"], [])
        self.assertEqual([e["name"] for e in self.sb.load_ws("g")["repos"]], ["A"])

    def test_ac24_unknown_feature(self):
        A = self.sb.new_repo("A")
        before = self.snap(A)
        r = self.sb.tg("workspace", "attach", str(A), "--feature", "nope", cwd=self.sb.scratch)
        self.code(r, 1)
        self.has(r.err, "no workspace named nope", r)
        self.assertFalse(self.sb.ws_dir("nope").exists())
        self.assertEqual(self.snap(A), before)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "chmod can't make a dir read-only for root")
    def test_ac25_no_partial_attach(self):
        A = self.sb.new_repo("A")
        ws = self.new_ws("f")
        wj_before = read_bytes(ws / "workspace.json")
        refs_before = self.sb.refs(A)
        os.chmod(ws, 0o555)  # repos/ stays writable: the checkout works, the json update can't
        try:
            r = self.sb.tg("workspace", "attach", str(A), cwd=ws)
        finally:
            os.chmod(ws, 0o755)
        self.code(r, 1)
        self.no_traceback(r)
        self.assertFalse((ws / "repos" / "A").exists(), "no partial worktree")
        self.assertEqual(len(self.sb.worktrees(A)), 1)
        self.assertEqual(self.sb.git(A, "branch", "--list", "feat/f"), "", "no branch created by the failed attach")
        self.assertEqual(self.sb.refs(A), refs_before)
        self.assertEqual(read_bytes(ws / "workspace.json"), wj_before)

    def test_ac25_reused_branch_survives_failed_attach(self):
        A = self.sb.new_repo("A")
        self.sb.git(A, "branch", "feat/f")
        ws = self.new_ws("f")
        refs_before = self.sb.refs(A)
        os.chmod(ws, 0o555)
        try:
            r = self.sb.tg("workspace", "attach", str(A), cwd=ws)
        finally:
            os.chmod(ws, 0o755)
        self.code(r, 1)
        self.assertEqual(self.sb.refs(A), refs_before, "a pre-existing branch is not deleted on rollback")
        self.assertEqual(len(self.sb.worktrees(A)), 1)


class TestAttachReuse(WSTestCase):

    def test_q5_existing_branch_is_reused(self):
        A = self.sb.new_repo("A")
        self.sb.git(A, "checkout", "-q", "-b", "feat/f")
        tip = self.sb.commit_file(A, "pkg/w.go", "package pkg\n")
        self.sb.git(A, "checkout", "-q", "main")
        ws = self.new_ws("f")
        r = self.sb.tg("workspace", "attach", str(A), cwd=ws)
        self.ok(r)
        self.has(r.out, "team-guard: reused existing branch feat/f at %s" % tip[:12], r)
        self.assertEqual(self.sb.git(ws / "repos" / "A", "rev-parse", "HEAD"), tip)
        e = self.sb.load_ws("f")["repos"][0]
        self.assertIs(e["reused"], True)
        self.assertEqual(e["start"], tip)


class TestAttachProperty(WSTestCase):

    def test_ac26_random_attach_sequences_keep_json_valid_and_unique(self):
        for seed in (1, 2, 3):
            with self.subTest(seed=seed):
                self._run_sequence(seed)

    def _run_sequence(self, seed):
        rng = random.Random(seed)
        base = self.sb.tmp / ("seq%d" % seed)
        repos = [self.sb.new_repo("r%d" % i, parent=base / "p") for i in range(4)]
        dup = self.sb.new_repo("r2", parent=base / "q")  # same name as r2, different repo
        link = base / "link-r0"
        os.symlink(repos[0], link)
        plain = base / "plain"
        plain.mkdir()
        # target -> (source, name) or None for "not a repo"
        targets = [
            (repos[0], (repos[0], "r0")), (link, (repos[0], "r0")), (repos[1], (repos[1], "r1")),
            (repos[1] / "pkg", (repos[1], "r1")), (repos[2], (repos[2], "r2")), (dup, (dup, "r2")),
            (repos[3], (repos[3], "r3")), (plain, None), (base / "missing", None),
        ]
        feature = "s%d" % seed
        ws = self.new_ws(feature)
        attached = {}  # name -> source
        for step in range(10):
            target, ident = rng.choice(targets)
            wj_before = read_bytes(ws / "workspace.json")
            r = self.sb.tg("workspace", "attach", str(target), cwd=ws)
            expect_ok = ident is not None and ident[1] not in attached and str(ident[0]) not in attached.values()
            label = "seed %d step %d attach %s" % (seed, step, target)
            if expect_ok:
                self.ok(r, label)
                attached[ident[1]] = str(ident[0])
            else:
                self.code(r, 1, label)
                self.assertEqual(read_bytes(ws / "workspace.json"), wj_before, label + ": failed attach changed json")
            data = json.loads((ws / "workspace.json").read_text())
            names = [e["name"] for e in data["repos"]]
            sources = [e["source"] for e in data["repos"]]
            self.assertEqual(len(names), len(set(names)), label)
            self.assertEqual(len(sources), len(set(sources)), label)
            self.assertEqual(dict(zip(names, sources)), attached, label)
            self.assertEqual(sorted(os.listdir(ws / "repos")), sorted(attached), label)


class TestWorkspacePath(WSTestCase):

    def test_ac27_inside(self):
        A = self.sb.new_repo("A")
        ws = self.new_ws("f")
        wt = self.attach(A)
        (ws / "notes" / "deep").mkdir(parents=True)
        for cwd in (ws, ws / "notes" / "deep", ws / "repos", wt, wt / "pkg"):
            with self.subTest(cwd=str(cwd)):
                r = self.ok(self.sb.tg("workspace-path", cwd=cwd))
                self.assertEqual(r.out, realpath(ws) + "\n")

    def test_ac27_via_symlinked_cwd(self):
        ws = self.new_ws("f")
        link = self.sb.tmp / "ws-link"
        os.symlink(ws, link)
        r = self.ok(self.sb.tg("workspace-path", cwd=link))
        self.assertEqual(r.out, realpath(ws) + "\n")

    def test_ac28_outside(self):
        A = self.sb.new_repo("A")
        self.new_ws("f")
        for cwd in (self.sb.scratch, A, self.sb.ws_root):
            with self.subTest(cwd=str(cwd)):
                r = self.sb.tg("workspace-path", cwd=cwd)
                self.code(r, 1)
                self.assertEqual(r.out, "")
                self.has(r.err, "team-guard: not inside a workspace", r)

    def test_ac29_dangling_pointer(self):
        A = self.sb.new_repo("A")
        ws = self.new_ws("f")
        wt = self.attach(A)
        pointer = Path(self.sb.git(wt, "rev-parse", "--absolute-git-dir")) / "team-workspace"
        good = pointer.read_text()
        pointer.write_text("/nonexistent\n")
        r = self.sb.tg("workspace-path", cwd=wt)
        self.code(r, 1)
        self.assertEqual(r.out, "")
        self.has(r.err, "dangling", r)
        self.has(r.err, "/nonexistent", r)
        pointer.write_text(good)
        (ws / "workspace.json").unlink()
        r = self.sb.tg("workspace-path", cwd=wt)
        self.code(r, 1)
        self.has(r.err, "dangling", r)

    def test_workspace_dir_without_json(self):
        ws = self.new_ws("f")
        (ws / "workspace.json").unlink()
        r = self.sb.tg("workspace-path", cwd=ws)
        self.code(r, 1)
        self.has(r.err, realpath(ws) + "/workspace.json", r)

    def test_ac30_foreign_worktree_and_main_checkout_pointer(self):
        A = self.sb.new_repo("A")
        ws = self.new_ws("f")
        self.attach(A)
        other = self.sb.tmp / "other"
        self.sb.git(A, "worktree", "add", "-q", str(other), "-b", "other")
        r = self.sb.tg("workspace-path", cwd=other)
        self.code(r, 1)
        self.assertEqual(r.out, "")
        (A / ".git" / "team-workspace").write_text(realpath(ws) + "\n")
        r = self.sb.tg("workspace-path", cwd=A)
        self.code(r, 1, "a pointer in a main checkout's .git is ignored")
        self.assertEqual(r.out, "")


class TestHoldoutPath(WSTestCase):

    def test_ac31_holdout_in_workspace(self):
        A = self.sb.new_repo("A")
        ws = self.new_ws("f")
        wt = self.attach(A)
        for cwd in (ws, wt, wt / "pkg"):
            with self.subTest(cwd=str(cwd)):
                r = self.ok(self.sb.tg("holdout-path", cwd=cwd))
                path = r.out.strip()
                self.assertEqual(path, realpath(self.sb.ho_root / "f"))
                self.assertTrue(os.path.isdir(path))
                self.assertFalse(path.startswith(realpath(ws) + os.sep))
                self.assertFalse(path.startswith(realpath(wt) + os.sep))
                marker = json.loads(Path(path, ".workspace").read_text())
                self.assertEqual(marker["feature"], "f")
                self.assertEqual(marker["workspace"], realpath(ws))
        self.assertEqual(self.sb.git(wt, "status", "--porcelain", "--ignored"), "")

    def test_holdout_root_inside_workspace_refused(self):
        ws = self.new_ws("f")
        r = self.sb.tg("holdout-path", cwd=ws, env={"TEAM_HOLDOUT_ROOT": str(ws / "ho")})
        self.code(r, 1)
        self.has(r.err, "holdout root", r)
        self.assertFalse((ws / "ho" / "f").exists())

    def test_holdout_collision_refused(self):
        ws = self.new_ws("f")
        (self.sb.ho_root / "f" / "stuff").mkdir(parents=True)
        (self.sb.ho_root / "f" / "stuff" / "x.md").write_text("legacy\n")
        before = tree_snapshot(self.sb.ho_root)
        r = self.sb.tg("holdout-path", cwd=ws)
        self.code(r, 1)
        self.has(r.err, "collides", r)
        self.assertEqual(tree_snapshot(self.sb.ho_root), before)

    def test_holdout_dangling_pointer_refused(self):
        A = self.sb.new_repo("A")
        self.new_ws("f")
        wt = self.attach(A)
        pointer = Path(self.sb.git(wt, "rev-parse", "--absolute-git-dir")) / "team-workspace"
        pointer.write_text("/nonexistent\n")
        r = self.sb.tg("holdout-path", cwd=wt)
        self.code(r, 1)
        self.has(r.err, "dangling", r)

    def test_ac57_legacy_holdout_path(self):
        repo = self.sb.new_repo("repo", branch="feat/x")
        r = self.ok(self.sb.tg("holdout-path", cwd=repo))
        self.assertEqual(r.out.strip(), realpath(self.sb.ho_root) + "/repo/feat-x")
        self.assertFalse(Path(r.out.strip(), ".workspace").exists())


if __name__ == "__main__":
    unittest.main()
