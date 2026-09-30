"""Review round 1 (contract-addendum-round1.md, R1-1..R1-8).

Where the addendum and contract.md disagree, the addendum wins.
"""

import json
import os
import re
import shutil
import unittest
from pathlib import Path

from test_workspace_lifecycle import LifecycleCase
from wslib import ROOT, read_bytes, realpath, tree_snapshot


def text_of(p):
    """File text, or None if it's gone (so a deletion shows up as an assertion, not an error)."""
    b = read_bytes(p)
    return None if b is None else b.decode()


def section(doc, title):
    lines = doc.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(#+)\s+(.*)$", line)
        if m and title in m.group(2):
            out = [line]
            for rest in lines[i + 1:]:
                n = re.match(r"^(#+)\s", rest)
                if n and len(n.group(1)) <= len(m.group(1)):
                    break
                out.append(rest)
            return "\n".join(out)
    return ""


class Round1Case(LifecycleCase):

    def edit_entry(self, feature, name, **fields):
        p = self.sb.ws_dir(feature) / "workspace.json"
        data = json.loads(p.read_text())
        for e in data["repos"]:
            if e["name"] == name:
                e.update(fields)
        p.write_text(json.dumps(data, indent=2))


class TestR1_1_NothingDeletedUnarchived(Round1Case):

    def test_tmp_named_artifacts_are_archived(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        (ws / "notes.tmp").write_text("my notes\n")
        (ws / "draft.tmp").mkdir()
        (ws / "draft.tmp" / "x.md").write_text("draft\n")
        (ws / "spec.md").write_text("# spec\n")
        r = self.close("f")
        self.ok(r)
        ar = self.sb.ar_root / "f"
        self.assertEqual(text_of(ar / "notes.tmp"), "my notes\n", "notes.tmp was deleted without being archived")
        self.assertEqual(text_of(ar / "draft.tmp" / "x.md"), "draft\n")
        self.assertEqual(text_of(ar / "spec.md"), "# spec\n")
        self.assertFalse(ws.exists())

    def test_file_in_repos_is_refused_even_with_force(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        (ws / "repos" / "README-local.md").write_text("keep me\n")
        before = self.everything(repos["A"])
        for extra in ((), ("--force",)):
            with self.subTest(extra=extra):
                r = self.close("f", *extra)
                self.code(r, 1)
                self.has(r.err, "unrecorded entry in repos/: README-local.md", r)
                self.no_traceback(r)
                self.assertEqual(self.everything(repos["A"]), before)
        self.assertEqual(text_of(ws / "repos" / "README-local.md"), "keep me\n")

    def test_directory_and_symlink_in_repos_are_refused(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        target = self.sb.tmp / "elsewhere"
        target.mkdir()
        (target / "data.txt").write_text("precious\n")
        for name, make in (("extra-dir", lambda p: p.mkdir()), ("link", lambda p: os.symlink(target, p)),
                           ("file-link", lambda p: os.symlink(target / "data.txt", p))):
            with self.subTest(entry=name):
                p = ws / "repos" / name
                make(p)
                before = self.everything(repos["A"])
                for extra in ((), ("--force",)):
                    r = self.close("f", *extra)
                    self.code(r, 1)
                    self.has(r.err, "unrecorded entry in repos/: %s" % name, r)
                    self.assertEqual(self.everything(repos["A"]), before)
                if p.is_symlink():
                    p.unlink()
                else:
                    p.rmdir()
        self.assertEqual(text_of(target / "data.txt"), "precious\n")

    def test_gc_apply_skips_a_workspace_with_unrecorded_entries(self):
        env = {"TEAM_STALE_DAYS": "0"}
        ws, repos, wts = self.pushed_ws("f", "A")
        (ws / "repos" / "README-local.md").write_text("keep me\n")
        self.pushed_ws("g", "B")
        r = self.gc("--apply", env=env)
        self.code(r, 0)
        self.has(r.out, "skipped f", r)
        self.has(r.out, "closed g", r)
        self.assertEqual(text_of(ws / "repos" / "README-local.md"), "keep me\n")
        self.assertTrue(wts["A"].exists())


class TestR1_2_ContainedAndOwnedWorktrees(Round1Case):

    def test_tampered_path_outside_with_missing_source(self):
        ws, repos, wts = self.pushed_ws("g", "A", "B")
        victim = self.sb.tmp / "victim"
        victim.mkdir()
        (victim / "important.txt").write_text("do not delete\n")
        self.edit_entry("g", "B", worktree=str(victim))
        shutil.rmtree(repos["B"])
        before = (self.sb.roots_snapshot(), tree_snapshot(victim), self.sb.worktrees(repos["A"]))
        for extra in ((), ("--force",)):
            with self.subTest(extra=extra):
                r = self.close("g", *extra)
                self.code(r, 1)
                self.has(r.err, "worktree outside workspace: %s" % realpath(victim), r)
                self.no_traceback(r)
                self.assertEqual((self.sb.roots_snapshot(), tree_snapshot(victim), self.sb.worktrees(repos["A"])), before)
        self.assertEqual(text_of(victim / "important.txt"), "do not delete\n")

    def test_path_naming_another_worktree_of_the_source(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        hotfix = self.sb.tmp / "hotfix"
        self.sb.git(repos["A"], "worktree", "add", "-q", str(hotfix), "-b", "hotfix")
        (hotfix / "wip.txt").write_text("user's own work\n")
        self.edit_entry("f", "A", worktree=str(hotfix))
        before = (self.sb.roots_snapshot(), tree_snapshot(hotfix), sorted(self.sb.worktrees(repos["A"])))
        for extra in ((), ("--force",)):
            with self.subTest(extra=extra):
                r = self.close("f", *extra)
                self.code(r, 1)
                self.has(r.err, "worktree outside workspace: %s" % realpath(hotfix), r)
                self.assertEqual((self.sb.roots_snapshot(), tree_snapshot(hotfix),
                                  sorted(self.sb.worktrees(repos["A"]))), before)
        self.assertIn(realpath(hotfix), self.sb.worktrees(repos["A"]))

    def test_path_of_a_sibling_entry(self):
        ws, repos, wts = self.pushed_ws("f", "A", "B")
        self.edit_entry("f", "A", worktree=str(wts["B"]))
        before = self.everything(repos["A"], repos["B"])
        r = self.close("f", "--force")
        self.code(r, 1)
        self.has(r.err, "worktree outside workspace: %s" % realpath(wts["B"]), r)
        self.assertEqual(self.everything(repos["A"], repos["B"]), before)

    def test_plain_dir_in_place_of_the_worktree(self):
        # Right path, but the source's worktree registry doesn't list it.
        ws, repos, wts = self.pushed_ws("f", "A")
        self.sb.git(repos["A"], "worktree", "remove", "--force", str(wts["A"]))
        wts["A"].mkdir()
        (wts["A"] / "mine.txt").write_text("not a checkout\n")
        before = self.everything(repos["A"])
        for extra in ((), ("--force",)):
            with self.subTest(extra=extra):
                r = self.close("f", *extra)
                self.code(r, 1)
                self.has(r.err, "worktree outside workspace: %s" % realpath(wts["A"]), r)
                self.assertEqual(self.everything(repos["A"]), before)

    def test_gc_apply_skips_and_keeps_going(self):
        env = {"TEAM_STALE_DAYS": "0"}
        wsf, rf, wf = self.pushed_ws("f", "A")
        victim = self.sb.tmp / "victim"
        victim.mkdir()
        (victim / "important.txt").write_text("do not delete\n")
        self.edit_entry("f", "A", worktree=str(victim))
        self.pushed_ws("g", "B")
        r = self.gc("--apply", env=env)
        self.code(r, 0)
        self.has(r.out, "skipped f", r)
        self.has(r.out, "worktree outside workspace", r)
        self.has(r.out, "closed g", r)
        self.assertEqual(text_of(victim / "important.txt"), "do not delete\n")
        self.assertTrue(wsf.exists())


class TestR1_3_IgnoredFilesListed(Round1Case):

    def ignored_repo_ws(self, feature):
        ws = self.new_ws(feature)
        repo = self.sb.new_repo("A", parent=self.sb.tmp / "src" / feature)
        self.sb.commit_file(repo, ".gitignore", "*.env\nbuild/\n", "ignore")
        self.sb.add_remote(repo)
        wt = self.attach(repo, feature)
        (wt / "local.env").write_text("SECRET=1\n")
        (wt / "build").mkdir()
        (wt / "build" / "out.o").write_text("obj\n")
        return ws, repo, wt

    def test_close_lists_then_deletes_ignored_entries(self):
        ws, repo, wt = self.ignored_repo_ws("f")
        r = self.close("f")
        self.ok(r)
        self.has(r.out, "deleting ignored: A/local.env", r)
        self.has(r.out, "deleting ignored: A/build/", r)
        self.lacks(r.out, "A/build/out.o", r)
        self.assertFalse(wt.exists())

    def test_gc_apply_lists_them_too(self):
        ws, repo, wt = self.ignored_repo_ws("f")
        r = self.gc("--apply", env={"TEAM_STALE_DAYS": "0"})
        self.ok(r)
        self.has(r.out, "deleting ignored: A/local.env", r)
        self.has(r.out, "closed f", r)

    def test_ignored_files_dont_hide_real_problems(self):
        ws, repo, wt = self.ignored_repo_ws("f")
        (wt / "untracked.txt").write_text("wip\n")
        r = self.close("f")
        self.code(r, 1)
        self.has(r.err, "uncommitted changes", r)
        self.assertTrue((wt / "local.env").exists())
        self.lacks(r.out, "deleting ignored", r)


class TestR1_4_HoldoutOwner(Round1Case):

    def test_close_leaves_another_roots_holdout(self):
        root2 = self.sb.tmp / "ws2"
        ws1 = self.new_ws("f")
        ho = Path(self.ok(self.sb.tg("holdout-path", cwd=ws1)).out.strip())
        (ho / "s.md").write_text("root 1 scenario\n")
        env2 = {"TEAM_WORKSPACE_ROOT": str(root2)}
        self.ok(self.sb.tg("workspace", "new", "f", env=env2))
        r = self.close("f", env=env2)
        self.ok(r)
        self.has(r.out + r.err, "warning: holdout %s belongs to another workspace root" % realpath(ho), r)
        self.assertEqual(text_of(ho / "s.md"), "root 1 scenario\n")
        self.assertTrue(ws1.exists())

    def test_gc_leaves_a_holdout_whose_marker_names_another_root(self):
        ws1 = self.new_ws("g")
        ho = Path(self.ok(self.sb.tg("holdout-path", cwd=ws1)).out.strip())
        (ho / "s.md").write_text("root 1 scenario\n")
        env2 = {"TEAM_WORKSPACE_ROOT": str(self.sb.tmp / "ws2")}
        r = self.gc("--apply", env=env2)
        self.code(r, 0)
        self.assertEqual(text_of(ho / "s.md"), "root 1 scenario\n")
        self.lacks(r.out, "removed %s" % realpath(ho), r)

    def test_forged_marker_is_not_trusted(self):
        ws = self.new_ws("f")
        ho = self.sb.ho_root / "f"
        ho.mkdir(parents=True)
        (ho / ".workspace").write_text(json.dumps({"feature": "f", "workspace": "/somewhere/else/f"}))
        (ho / "s.md").write_text("someone else's\n")
        r = self.close("f")
        self.ok(r)
        self.has(r.out + r.err, "warning: holdout %s belongs to another workspace root" % realpath(ho), r)
        self.assertTrue((ho / "s.md").exists())


class TestR1_5_NoTracebacks(Round1Case):

    def test_pre_push_outside_a_repo(self):
        r = self.sb.tg("pre-push", "origin", "x", cwd=self.sb.scratch, input="")
        self.code(r, 1)
        self.has(r.err, "team-guard: not in a git repository", r)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "chmod can't block root")
    def test_close_oserror_then_resume(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        (ws / "spec.md").write_text("# spec\n")
        self.sb.ar_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.sb.ar_root, 0o555)
        try:
            r = self.close("f")
        finally:
            os.chmod(self.sb.ar_root, 0o755)
        self.code(r, 1)
        self.no_traceback(r)
        self.assertRegex(r.err, r"(?m)^team-guard: .*close.*: ", self.show(r))
        r = self.close("f")
        self.ok(r)
        self.assertEqual(text_of(self.sb.ar_root / "f" / "spec.md"), "# spec\n")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "chmod can't block root")
    def test_gc_oserror(self):
        self.pushed_ws("f", "A")
        self.sb.ar_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.sb.ar_root, 0o555)
        try:
            r = self.gc("--apply", env={"TEAM_STALE_DAYS": "0"})
        finally:
            os.chmod(self.sb.ar_root, 0o755)
        self.code(r, 1)
        self.no_traceback(r)
        self.assertRegex(r.err, r"(?m)^team-guard: .*gc.*: ", self.show(r))


class TestR1_6_HoldoutSearch(Round1Case):

    def ev(self, cwd, agent, tool, path):
        return json.dumps({"cwd": str(cwd), "agent_type": agent, "tool_name": tool,
                           "tool_input": {"pattern": "scenario", "path": str(path)}})

    def test_search_over_an_ancestor_is_denied(self):
        ws = self.new_ws("f")
        self.ok(self.sb.tg("holdout-path", cwd=ws))
        ho = self.sb.ho_root
        for tool in ("Grep", "Glob"):
            for path in (ho, ho.parent, Path("/"), os.path.relpath(ho.parent, ws)):
                with self.subTest(tool=tool, path=str(path)):
                    r = self.sb.tg("hook-pretool", input=self.ev(ws, "software-engineer", tool, path))
                    self.assert_deny(r, "Holdout scenarios are hidden")
                    self.assert_no_decision(self.sb.tg("hook-pretool", input=self.ev(ws, "reviewer", tool, path)))

    def test_search_elsewhere_is_allowed(self):
        ws = self.new_ws("f")
        self.ok(self.sb.tg("holdout-path", cwd=ws))
        for tool in ("Grep", "Glob"):
            for path in (ws, self.sb.scratch, self.sb.ws_root):
                with self.subTest(tool=tool, path=str(path)):
                    self.assert_no_decision(self.sb.tg("hook-pretool", input=self.ev(ws, "software-engineer", tool, path)))


class TestR1_7_Docs(unittest.TestCase):

    def test_tester_role_names_both_hooks(self):
        body = (ROOT / "core" / "roles" / "tester.md").read_text()
        seal_lines = [p for p in re.split(r"\n\s*\n", body) if "seal" in p.lower() and "pre-commit" in p]
        self.assertTrue(any("pre-push" in p for p in seal_lines), "tester.md: sealing installs pre-commit and pre-push")

    def test_reviewer_runs_list_in_each_worktree(self):
        body = (ROOT / "core" / "roles" / "reviewer.md").read_text()
        paras = [p for p in re.split(r"\n\s*\n", body) if "team-guard list" in p]
        self.assertTrue(any("each attached worktree" in p for p in paras),
                        "reviewer.md: run `team-guard list` in each attached worktree")

    def test_readme_known_limits(self):
        limits = section((ROOT / "README.md").read_text(), "Known limits")
        bullets = [l for l in limits.splitlines() if re.match(r"^\s*[-*]\s", l)]
        self.assertTrue(any("pre-push" in b and "source checkout" in b for b in bullets),
                        "Known limits: pushing from the source checkout skips the worktree's pre-push check")
        self.assertTrue(any(".git" in b and ("nested" in b or "inside another repo" in b) for b in bullets),
                        "Known limits: a workspace root nested inside another repo can hide a missing .git file")


class TestR1_8_RunShIsolation(unittest.TestCase):

    def test_run_sh_ignores_user_git_config(self):
        lines = (ROOT / "tests" / "run.sh").read_text().splitlines()
        first_git = next(i for i, l in enumerate(lines)
                         if re.search(r"(^|[\s;&(])git\s", l) and not l.lstrip().startswith("#"))
        head = "\n".join(lines[:first_git])
        self.assertRegex(head, r"export[^\n]*GIT_CONFIG_GLOBAL=")
        self.assertRegex(head, r"export[^\n]*GIT_CONFIG_NOSYSTEM=1")


if __name__ == "__main__":
    unittest.main()
