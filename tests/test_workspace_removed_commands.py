"""Commands that no longer exist, the one-release no-ops for old git hooks, and the cleanup script.

An earlier version installed git hooks in project repos that call `team-guard pre-commit` and
`team-guard pre-push`. Those two stay as silent no-ops for one release so such a repo can still
commit; `scripts/remove-old-git-hooks` removes the hooks themselves.
"""

import json
import re
import stat
import unittest

from wslib import GUARD, ROOT, WSTestCase

REMOVED = ["seal", "unseal", "verify", "list", "holdout-path", "hook-pretool", "hook-stop", "hook-task",
           "git-hook", "gc", "refs", "routing", "risk", "gate"]
KEPT = ["workspace new", "workspace attach", "workspace list", "workspace show", "workspace close", "workspace-path",
        "sign"]
SCRIPT = ROOT / "scripts" / "remove-old-git-hooks"
MARKER = "# managed by team-guard"


def old_hook(name, guard):
    """The hook an earlier version wrote: it runs team-guard, then the hook it wrapped."""
    return ('#!/bin/sh\n%s; the previous hook, if any, runs after it from %s.team-guard-chained\n'
            '"%s" %s || exit 1\nchained="$(dirname "$0")/%s.team-guard-chained"\n'
            'if [ -x "$chained" ]; then exec "$chained" "$@"; fi\n' % (MARKER, name, guard, name, name))


class TestRemovedCommands(WSTestCase):

    def test_each_removed_command_says_it_is_unknown(self):
        for cmd in REMOVED:
            with self.subTest(command=cmd):
                r = self.sb.tg(cmd)
                self.code(r, 2)
                self.has(r.err, "unknown command '%s'" % cmd, r)
                self.assertEqual(r.out, "")

    def test_help_lists_only_what_remains(self):
        for args in (("help",), ("--help",), ()):
            with self.subTest(args=args):
                r = self.ok(self.sb.tg(*args))
                for cmd in KEPT:
                    self.assertIn("team-guard " + cmd, r.out)
                for word in REMOVED + ["pre-commit", "pre-push"]:
                    self.assertIsNone(re.search(r"team-guard %s(?![\w-])" % re.escape(word), r.out), word)
                for word in ("mutation", "holdout", "sealed", "gate", "routing"):
                    self.assertNotIn(word, r.out)


class TestOldGitHooksStillCommit(WSTestCase):

    def test_pre_commit_and_pre_push_do_nothing_and_say_nothing(self):
        for cmd in ("pre-commit", "pre-push"):
            with self.subTest(command=cmd):
                r = self.ok(self.sb.tg(cmd, "origin", "file:///x", cwd=self.sb.scratch))
                self.assertEqual((r.out, r.err), ("", ""))

    def test_a_commit_through_an_old_hook_passes(self):
        repo = self.sb.new_repo("R")
        hook = repo / ".git" / "hooks" / "pre-commit"
        hook.write_text(old_hook("pre-commit", GUARD))
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
        pre_push = repo / ".git" / "hooks" / "pre-push"
        pre_push.write_text(old_hook("pre-push", GUARD))
        pre_push.chmod(pre_push.stat().st_mode | stat.S_IXUSR)
        (repo / "pkg" / "a_test.go").write_text("func TestA(t *testing.T) { /* edited */ }\n")
        r = self.sb.git(repo, "commit", "-qam", "edit a test", check=False)
        self.ok(r)
        self.assertEqual((r.out, r.err), ("", ""))


class TestRemoveOldGitHooks(WSTestCase):

    def run_script(self, *args):
        return self.sb.run([str(SCRIPT), "--state-dir", str(self.sb.tmp / "state"), *args])

    def repo_with_old_hooks(self, name, wrapped=None):
        repo = self.sb.new_repo(name)
        hooks = repo / ".git" / "hooks"
        if wrapped:
            chained = hooks / "pre-commit.team-guard-chained"
            chained.write_text(wrapped)
            chained.chmod(chained.stat().st_mode | stat.S_IXUSR)
        for h in ("pre-commit", "pre-push"):
            (hooks / h).write_text(old_hook(h, GUARD))
        (repo / ".git" / "team-test-lock.json").write_text('{"files": {}}')
        return repo

    def name_in_workspace(self, repo, where="work"):
        d = self.sb.tmp / "state" / where / "f"
        d.mkdir(parents=True)
        (d / "workspace.json").write_text(json.dumps({"repos": [{"source": str(repo)}]}))

    def test_it_removes_our_hooks_puts_back_the_wrapped_one_and_removes_the_lock(self):
        wrapped = "#!/bin/sh\necho lefthook\n"
        repo = self.repo_with_old_hooks("R", wrapped=wrapped)
        self.name_in_workspace(repo)
        r = self.ok(self.run_script())
        hooks = repo / ".git" / "hooks"
        self.assertEqual((hooks / "pre-commit").read_text(), wrapped)
        self.assertFalse((hooks / "pre-commit.team-guard-chained").exists())
        self.assertFalse((hooks / "pre-push").exists())
        self.assertFalse((repo / ".git" / "team-test-lock.json").exists())
        self.has(r.out, "removed pre-commit hook and put back the one it wrapped", r)
        # The repo commits cleanly, and the put-back hook runs.
        (repo / "README.md").write_text("changed\n")
        c = self.sb.git(repo, "commit", "-qam", "edit", check=False)
        self.ok(c)
        self.has(c.out + c.err, "lefthook", c)

    def test_it_finds_repos_in_archives_and_takes_paths_from_the_command_line(self):
        archived = self.repo_with_old_hooks("Archived")
        self.name_in_workspace(archived, where="archive")
        explicit = self.repo_with_old_hooks("Explicit")
        self.ok(self.run_script(str(explicit)))
        for repo in (archived, explicit):
            self.assertFalse((repo / ".git" / "hooks" / "pre-commit").exists(), repo)
            self.assertFalse((repo / ".git" / "team-test-lock.json").exists(), repo)

    def test_a_hook_that_is_not_team_guards_is_never_touched(self):
        repo = self.sb.new_repo("R")
        mine = repo / ".git" / "hooks" / "pre-commit"
        mine.write_text("#!/bin/sh\necho mine\n")
        r = self.ok(self.run_script(str(repo)))
        self.assertEqual(mine.read_text(), "#!/bin/sh\necho mine\n")
        self.has(r.out, "nothing to remove", r)

    def test_dry_run_changes_nothing(self):
        repo = self.repo_with_old_hooks("R", wrapped="#!/bin/sh\n")
        before = {p: (p.read_text() if p.is_file() else None) for p in sorted((repo / ".git").rglob("*"))
                  if "objects" not in p.parts and "hooks" in p.parts or p.name == "team-test-lock.json"}
        r = self.ok(self.run_script("--dry-run", str(repo)))
        after = {p: (p.read_text() if p.is_file() else None) for p in sorted((repo / ".git").rglob("*"))
                 if "objects" not in p.parts and "hooks" in p.parts or p.name == "team-test-lock.json"}
        self.assertEqual(before, after)
        self.has(r.out, "removed pre-commit hook", r)

    def test_a_second_run_has_nothing_to_do(self):
        repo = self.repo_with_old_hooks("R")
        self.ok(self.run_script(str(repo)))
        r = self.ok(self.run_script(str(repo)))
        self.has(r.out, "nothing to remove", r)

    def test_a_path_that_is_not_a_repo_is_skipped(self):
        r = self.ok(self.run_script(str(self.sb.scratch)))
        self.has(r.out, "not a git repository", r)


if __name__ == "__main__":
    unittest.main()
