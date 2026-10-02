"""Close safety: nothing is deleted without being archived, only owned and contained
worktrees are removed, ignored files are listed, and errors never surface as tracebacks.
"""

import json
import os
import re
import shutil
import unittest

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


class CloseSafetyCase(LifecycleCase):

    def edit_entry(self, feature, name, **fields):
        p = self.sb.ws_dir(feature) / "workspace.json"
        data = json.loads(p.read_text())
        for e in data["repos"]:
            if e["name"] == name:
                e.update(fields)
        p.write_text(json.dumps(data, indent=2))


class TestNothingDeletedUnarchived(CloseSafetyCase):

    def test_tmp_named_artifacts_are_archived(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        (ws / "notes.tmp").write_text("my notes\n")
        (ws / "draft.tmp").mkdir()
        (ws / "draft.tmp" / "x.md").write_text("draft\n")
        (ws / "plan.md").write_text("# plan\n")
        r = self.close("f")
        self.ok(r)
        ar = self.sb.ar_root / "f"
        self.assertEqual(text_of(ar / "notes.tmp"), "my notes\n", "notes.tmp was deleted without being archived")
        self.assertEqual(text_of(ar / "draft.tmp" / "x.md"), "draft\n")
        self.assertEqual(text_of(ar / "plan.md"), "# plan\n")
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


class TestContainedAndOwnedWorktrees(CloseSafetyCase):

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


class TestIgnoredFilesListed(CloseSafetyCase):

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


    def test_ignored_files_dont_hide_real_problems(self):
        ws, repo, wt = self.ignored_repo_ws("f")
        (wt / "untracked.txt").write_text("wip\n")
        r = self.close("f")
        self.code(r, 1)
        self.has(r.err, "uncommitted changes", r)
        self.assertTrue((wt / "local.env").exists())
        self.lacks(r.out, "deleting ignored", r)


class TestNoTracebacks(CloseSafetyCase):


    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "chmod can't block root")
    def test_close_oserror_then_resume(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        (ws / "plan.md").write_text("# plan\n")
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
        self.assertEqual(text_of(self.sb.ar_root / "f" / "plan.md"), "# plan\n")


class TestRunShIsolation(unittest.TestCase):

    def test_run_sh_ignores_user_git_config_before_it_runs_anything(self):
        lines = (ROOT / "tests" / "run.sh").read_text().splitlines()
        first_run = next(i for i, l in enumerate(lines)
                         if re.search(r"(python3|build\.py|git)\s", l) and not l.lstrip().startswith("#"))
        head = "\n".join(lines[:first_run])
        self.assertRegex(head, r"export[^\n]*GIT_CONFIG_GLOBAL=")
        self.assertRegex(head, r"export[^\n]*GIT_CONFIG_NOSYSTEM=1")


if __name__ == "__main__":
    unittest.main()
