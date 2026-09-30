"""Hash-carrying seals, target-path hooks, workspace-wide verify, the pre-push hook and
backwards compatibility."""

import itertools
import json
import os
import random
import unittest
from pathlib import Path

from wslib import (WSTestCase, patch_text, read_bytes, realpath, tree_snapshot)

SEALED_A = "pkg/a_test.go"   # sealed in A
SEALED_B = "pkg/b_test.go"   # sealed in B


class GuardCase(WSTestCase):

    def git_dir(self, wt):
        return Path(self.sb.git(wt, "rev-parse", "--absolute-git-dir"))

    def lock(self, wt):
        return json.loads((self.git_dir(wt) / "team-test-lock.json").read_text())

    def seal(self, wt, *files):
        r = self.sb.tg("seal", *files, cwd=wt)
        self.ok(r, "seal %s in %s" % (files, wt))
        return r

    def sealed_ws(self, remotes=False):
        ws, A, B, wtA, wtB = self.two_repo_ws(remotes=remotes)
        self.seal(wtA, SEALED_A)
        self.seal(wtB, SEALED_B)
        return ws, A, B, wtA, wtB

    def standalone(self, name="R", remote=False):
        R = self.sb.new_repo(name)
        bare = self.sb.add_remote(R) if remote else None
        return R, bare

    def expire_unreachable(self, repo):
        self.sb.git(repo, "reflog", "expire", "--expire=now", "--all")
        self.sb.git(repo, "gc", "-q", "--prune=now")

    def assert_unreachable(self, repo, sha):
        r = self.sb.git(repo, "cat-file", "-e", sha + "^{commit}", check=False)
        self.assertNotEqual(r.code, 0, "harness: %s should be gone after gc" % sha)

    def hook_task(self, cwd):
        return self.sb.tg("hook-task", input=json.dumps({"cwd": str(cwd)}))

    def hook_stop(self, cwd, active=False):
        return self.sb.tg("hook-stop", input=json.dumps({"cwd": str(cwd), "stop_hook_active": active}))


class TestHashSeals(GuardCase):

    def test_seal_records_commit_and_blob(self):
        ws, A, B, wtA, wtB = self.two_repo_ws()
        r = self.seal(wtA, SEALED_A)
        self.has(r.out, "sealed 1 file(s) at", r)
        lock = json.loads(self.ok(self.sb.tg("list", cwd=wtA)).out)
        head = self.sb.git(wtA, "rev-parse", "HEAD")
        self.assertEqual(lock["files"][SEALED_A], head)
        self.assertEqual(lock.get("blobs", {}).get(SEALED_A), self.sb.git(wtA, "rev-parse", "HEAD:" + SEALED_A))

    def test_unseal_removes_commit_and_blob(self):
        R, _ = self.standalone()
        self.seal(R, SEALED_A, SEALED_B)
        self.ok(self.sb.tg("unseal", SEALED_A, cwd=R))
        lock = self.lock(R)
        self.assertNotIn(SEALED_A, lock["files"])
        self.assertIn(SEALED_B, lock.get("blobs", {}), "seal writes blobs")
        self.assertNotIn(SEALED_A, lock.get("blobs", {}))

    def test_verify_accepts_amended_sealed_commit_in_workspace(self):
        ws, A, B, wtA, wtB = self.two_repo_ws()
        c2 = self.sb.commit_file(wtA, SEALED_A, "func TestA2(t *testing.T) {}\n", "tests")
        self.seal(wtA, SEALED_A)
        self.assertEqual(self.lock(wtA)["files"][SEALED_A], c2)
        (wtA / "pkg" / "a.go").write_text("package pkg // amended\n")
        self.sb.git(wtA, "add", "pkg/a.go")
        self.sb.git(wtA, "commit", "-q", "--amend", "-m", "tests and code")
        self.expire_unreachable(wtA)
        self.assert_unreachable(wtA, c2)
        for cwd in (wtA, ws):
            with self.subTest(cwd=str(cwd)):
                r = self.sb.tg("verify", cwd=cwd)
                self.ok(r)
                self.has(r.out, "unchanged", r)
                self.lacks(r.err, "modified", r)
                self.lacks(r.err, "changed since", r)
                self.no_traceback(r)
        self.assertEqual(self.hook_task(wtA).code, 0)

    def test_verify_accepts_squashed_sealed_commit_single_repo(self):
        R, _ = self.standalone()
        c2 = self.sb.commit_file(R, SEALED_A, "func TestA2(t *testing.T) {}\n", "tests")
        self.seal(R, SEALED_A)
        self.sb.commit_file(R, "pkg/a.go", "package pkg // impl\n", "impl")
        self.sb.git(R, "reset", "-q", "--soft", "HEAD~2")
        self.sb.git(R, "commit", "-qm", "squashed")
        self.expire_unreachable(R)
        self.assert_unreachable(R, c2)
        r = self.sb.tg("verify", cwd=R)
        self.ok(r)
        self.assertEqual(r.out, "team-guard: all sealed tests unchanged\n")
        self.assertEqual(r.err, "")

    def test_verify_accepts_rebased_sealed_commit(self):
        R, _ = self.standalone()
        self.sb.git(R, "checkout", "-q", "-b", "feat/x")
        c2 = self.sb.commit_file(R, SEALED_A, "func TestA2(t *testing.T) {}\n", "tests")
        self.seal(R, SEALED_A)
        self.sb.git(R, "checkout", "-q", "main")
        self.sb.commit_file(R, "README.md", "moved on\n", "main moves")
        self.sb.git(R, "checkout", "-q", "feat/x")
        self.sb.git(R, "rebase", "-q", "main")
        self.expire_unreachable(R)
        self.assert_unreachable(R, c2)
        r = self.sb.tg("verify", cwd=R)
        self.ok(r)
        self.assertEqual(r.out, "team-guard: all sealed tests unchanged\n")

    def test_verify_catches_changed_content_uncommitted_and_committed(self):
        for where in ("single", "workspace"):
            with self.subTest(where=where):
                if where == "single":
                    wt, _ = self.standalone("S")
                    self.seal(wt, SEALED_A)
                    cwd, line = wt, "  %s (changed since " % SEALED_A
                else:
                    ws, A, B, wt, wtB = self.sealed_ws()
                    cwd, line = ws, "  A: %s (changed since " % SEALED_A
                (wt / SEALED_A).write_text("// weakened\n")
                r = self.sb.tg("verify", cwd=cwd)
                self.code(r, 2)
                self.has(r.err, "team-guard: sealed tests were modified:", r)
                self.has(r.err, line, r)
                self.sb.git(wt, "commit", "-qam", "weaken", "--no-verify")
                r = self.sb.tg("verify", cwd=cwd)
                self.code(r, 2)
                self.has(r.err, line, r)

    def test_verify_reports_deleted_sealed_file(self):
        R, _ = self.standalone()
        self.seal(R, SEALED_A)
        (R / SEALED_A).unlink()
        r = self.sb.tg("verify", cwd=R)
        self.code(r, 2)
        self.has(r.err, "  %s (deleted)" % SEALED_A, r)
        ws, A, B, wtA, wtB = self.sealed_ws()
        (wtB / SEALED_B).unlink()
        r = self.sb.tg("verify", cwd=wtA)
        self.code(r, 2)
        self.has(r.err, "  B: %s (deleted)" % SEALED_B, r)

    def test_verify_reads_legacy_lock_without_blobs(self):
        R, _ = self.standalone()
        head = self.sb.git(R, "rev-parse", "HEAD")
        lock_path = self.git_dir(R) / "team-test-lock.json"
        lock_path.write_text(json.dumps({"files": {SEALED_A: head}, "history": []}))
        self.ok(self.sb.tg("git-hook", "install", cwd=R))
        before = read_bytes(lock_path)
        r = self.sb.tg("verify", cwd=R)
        self.ok(r)
        self.has(r.out, "unchanged", r)
        self.assertEqual(read_bytes(lock_path), before, "verify must not rewrite a legacy entry")
        (R / SEALED_A).write_text("// weakened\n")
        r = self.sb.tg("verify", cwd=R)
        self.code(r, 2)
        self.has(r.err, "%s (changed since %s)" % (SEALED_A, head[:12]), r)
        self.assertEqual(read_bytes(lock_path), before)
        # hook-pretool still enforces it
        self.assert_deny(self.pretool(R, "software-engineer", "Edit", SEALED_A), SEALED_A)
        # pre-commit still enforces it
        self.sb.git(R, "add", SEALED_A)
        r = self.sb.git(R, "commit", "-qm", "weaken", check=False)
        self.nonzero(r)
        self.has(r.err + r.out, "changes sealed tests", r)
        self.has(r.err + r.out, SEALED_A, r)
        self.sb.git(R, "reset", "-q", "--hard")
        # sealing another file leaves the legacy entry alone
        self.seal(R, SEALED_B)
        lock = json.loads(lock_path.read_text())
        self.assertEqual(lock["files"][SEALED_A], head)
        self.assertIsInstance(lock["files"][SEALED_A], str)
        self.assertNotIn(SEALED_A, lock.get("blobs", {}))
        self.assertIn(SEALED_B, lock.get("blobs", {}))
        # resealing it gives it a hash
        self.seal(R, SEALED_A)
        self.assertIn(SEALED_A, self.lock(R).get("blobs", {}))

    def test_pre_commit_checks_by_hash_after_rebase_away(self):
        R, _ = self.standalone()
        sealed_text = "func TestA2(t *testing.T) {}\n"
        c2 = self.sb.commit_file(R, SEALED_A, sealed_text, "tests")
        self.seal(R, SEALED_A)
        (R / "pkg" / "a.go").write_text("package pkg // amended\n")
        self.sb.git(R, "add", "pkg/a.go")
        self.sb.git(R, "commit", "-q", "--amend", "-m", "amended")
        self.expire_unreachable(R)
        self.assert_unreachable(R, c2)
        # a staged change to the sealed file is refused
        (R / SEALED_A).write_text("// weakened\n")
        self.sb.git(R, "add", SEALED_A)
        r = self.sb.git(R, "commit", "-qm", "weaken", check=False)
        self.code(r, 1)
        self.has(r.err + r.out, "changes sealed tests", r)
        self.has(r.err + r.out, SEALED_A, r)
        # staging the sealed content again is allowed
        (R / SEALED_A).write_text(sealed_text)
        (R / "pkg" / "a.go").write_text("package pkg // more\n")
        self.sb.git(R, "add", SEALED_A, "pkg/a.go")
        r = self.sb.git(R, "commit", "-qm", "ok", check=False)
        self.code(r, 0)
        # restoring after a bypassed commit is allowed: content equals the hash, HEAD differs
        self.sb.commit_file(R, SEALED_A, "// weakened\n", "bypass", no_verify=True)
        (R / SEALED_A).write_text(sealed_text)
        self.sb.git(R, "add", SEALED_A)
        r = self.sb.git(R, "commit", "-qm", "restore", check=False)
        self.code(r, 0)
        self.ok(self.sb.tg("verify", cwd=R))

    def test_lock_is_kept_per_worktree(self):
        ws, A, B, wtA, wtB = self.two_repo_ws()
        self.seal(wtA, SEALED_A)
        lockA = self.git_dir(wtA) / "team-test-lock.json"
        self.assertTrue(lockA.exists())
        self.assertFalse((self.git_dir(wtB) / "team-test-lock.json").exists())
        self.assertFalse((A / ".git" / "team-test-lock.json").exists(), "the source checkout's lock is separate")
        a_bytes = read_bytes(lockA)
        self.seal(wtB, SEALED_B)
        self.assertEqual(read_bytes(lockA), a_bytes)
        self.ok(self.sb.tg("unseal", SEALED_B, cwd=wtB))
        self.assertEqual(read_bytes(lockA), a_bytes)
        lock = json.loads(self.ok(self.sb.tg("list", cwd=wtA / "pkg")).out)
        self.assertEqual(sorted(lock["files"]), [SEALED_A])

    def test_repo_commands_refuse_workspace_dir(self):
        ws, A, B, wtA, wtB = self.two_repo_ws()
        for args in (("seal", "x"), ("unseal", "x"), ("list",), ("git-hook", "status"), ("git-hook", "install")):
            with self.subTest(args=args):
                r = self.sb.tg(*args, cwd=ws)
                self.code(r, 1)
                self.has(r.err, "is a workspace dir, not a repo", r)
                self.has(r.err, realpath(wtA), r)
                self.has(r.err, realpath(wtB), r)
                self.no_traceback(r)

    def test_list_in_empty_workspace_fails(self):
        ws = self.new_ws("e")
        r = self.sb.tg("list", cwd=ws)
        self.code(r, 1)
        self.has(r.err, "no repos attached yet", r)

    def test_repo_commands_outside_any_repo_fail_cleanly(self):
        for args in (("seal", "x"), ("unseal", "x"), ("list",), ("git-hook", "status"), ("git-hook", "install")):
            with self.subTest(args=args):
                r = self.sb.tg(*args, cwd=self.sb.scratch)
                self.code(r, 1)
                self.has(r.err, "not a git repository", r)
                self.no_traceback(r)
        r = self.sb.tg("verify", cwd=self.sb.scratch)
        self.nonzero(r)
        self.no_traceback(r)


class TestPretoolHook(GuardCase):

    def test_pretool_repo_comes_from_target_not_cwd_property(self):
        ws, A, B, wtA, wtB = self.sealed_ws()
        (wtA / "pkg" / "sub").mkdir()
        cwds = [ws, wtA, wtB, wtA / "pkg" / "sub", Path("/"), self.sb.scratch, A]
        targets = [
            (wtA / SEALED_A, "A"), (wtA / SEALED_B, None), (wtB / SEALED_B, "B"),
            (wtB / SEALED_A, None), (wtA / "pkg" / "a.go", None), (A / SEALED_A, None),
        ]
        for cwd, (target, owner), tool in itertools.product(cwds, targets, ("Edit", "Write")):
            with self.subTest(cwd=str(cwd), target=str(target), tool=tool):
                r = self.pretool(cwd, "software-engineer", tool, target)
                if owner:
                    reason = self.assert_deny(r, os.path.relpath(target, wtA if owner == "A" else wtB))
                    in_repo = str(cwd).startswith(str(wtA if owner == "A" else wtB))
                    if not in_repo:
                        self.has(reason, owner + ":", r)
                else:
                    self.assert_no_decision(r)

    def test_pretool_with_workspace_dir_cwd(self):
        ws, A, B, wtA, wtB = self.sealed_ws()
        reason = self.assert_deny(self.pretool(ws, "software-engineer", "Edit", "repos/A/" + SEALED_A), SEALED_A)
        self.has(reason, "A:")

    def test_pretool_multi_repo_patch(self):
        ws, A, B, wtA, wtB = self.sealed_ws()
        mixed = patch_text(("update", "repos/A/pkg/a.go"), ("update", "repos/B/" + SEALED_B))
        reason = self.assert_deny(self.pretool(ws, "software-engineer", "apply_patch", patch=mixed), SEALED_B)
        self.has(reason, "B:")
        only_a = patch_text(("update", "repos/A/pkg/a.go"), ("add", "repos/A/pkg/new.go"),
                            ("update", "repos/A/" + SEALED_B))
        self.assert_no_decision(self.pretool(ws, "software-engineer", "apply_patch", patch=only_a))
        delete = patch_text(("delete", "repos/A/" + SEALED_A))
        self.assert_deny(self.pretool(ws, "software-engineer", "apply_patch", patch=delete), SEALED_A)
        both = patch_text(("update", "repos/A/" + SEALED_A), ("update", "repos/B/" + SEALED_B))
        reason = self.assert_deny(self.pretool(ws, "software-engineer", "apply_patch", patch=both))
        self.has(reason, SEALED_A)
        self.has(reason, SEALED_B)

    def test_pretool_relative_paths_use_event_cwd(self):
        ws, A, B, wtA, wtB = self.sealed_ws()
        self.assert_deny(self.pretool(wtA, "software-engineer", "Edit", SEALED_A, proc_cwd=self.sb.scratch), SEALED_A)
        # pkg/b_test.go is sealed in B only; the process cwd must not decide
        self.assert_deny(self.pretool(wtB, "software-engineer", "Edit", SEALED_B, proc_cwd=wtA), SEALED_B)
        self.assert_no_decision(self.pretool(wtA, "software-engineer", "Edit", SEALED_B, proc_cwd=wtB))

    def test_pretool_new_and_deleted_paths(self):
        ws, A, B, wtA, wtB = self.sealed_ws()
        (wtA / SEALED_A).unlink()
        self.assert_deny(self.pretool(ws, "software-engineer", "Write", wtA / SEALED_A), SEALED_A)
        self.sb.git(wtA, "checkout", "-q", "--", SEALED_A)
        add = patch_text(("add", "repos/A/new/dir/x.go"))
        self.assert_no_decision(self.pretool(ws, "software-engineer", "apply_patch", patch=add))
        self.assert_no_decision(self.pretool(ws, "software-engineer", "Write", wtA / "new" / "dir" / "x_test.go"))

    def test_pretool_ignores_paths_outside_repos(self):
        ws, A, B, wtA, wtB = self.sealed_ws()
        for target in (ws / "contract" ".md", self.sb.scratch / "loose" / "x.txt", "/tmp/x", self.sb.tmp / "nowhere.md"):
            with self.subTest(target=str(target)):
                self.assert_no_decision(self.pretool(ws, "software-engineer", "Write", target))

    def test_pretool_denies_holdout_from_any_cwd(self):
        ws, A, B, wtA, wtB = self.sealed_ws()
        ho = Path(self.ok(self.sb.tg("holdout-path", cwd=ws)).out.strip())
        (ho / "s.md").write_text("scenario\n")
        for cwd in (ws, wtA, Path("/"), self.sb.scratch):
            with self.subTest(cwd=str(cwd)):
                self.assert_deny(self.pretool(cwd, "software-engineer", "Read", ho / "s.md"), "Holdout scenarios are hidden")
                self.assert_deny(self.pretool(cwd, "software-engineer", "Edit", ho / "s.md"), "Holdout scenarios are hidden")
                self.assert_no_decision(self.pretool(cwd, "reviewer", "Read", ho / "s.md"))
        ev = {"cwd": str(ws), "agent_type": "software-engineer", "tool_name": "Grep", "tool_input": {"path": str(ho)}}
        self.assert_deny(self.sb.tg("hook-pretool", input=json.dumps(ev)), "Holdout scenarios are hidden")
        rel = os.path.relpath(ho / "s.md", ws)
        self.assert_deny(self.pretool(ws, "software-engineer", "Read", rel), "Holdout scenarios are hidden")

    def test_pretool_leaves_unidentified_agent_and_tester_alone(self):
        ws, A, B, wtA, wtB = self.sealed_ws()
        self.assert_no_decision(self.pretool(ws, None, "Edit", "repos/A/" + SEALED_A))
        self.assert_no_decision(self.pretool(wtA, None, "Edit", SEALED_A))
        self.assert_no_decision(self.pretool(ws, "tester", "Edit", "repos/A/" + SEALED_A))
        both = patch_text(("update", "repos/A/" + SEALED_A), ("update", "repos/B/" + SEALED_B))
        self.assert_no_decision(self.pretool(ws, "tester", "apply_patch", patch=both))


class TestWorkspaceVerify(GuardCase):

    def test_verify_passes_iff_all_sealed_files_match_property(self):
        ws, A, B, wtA, wtB = self.sealed_ws()
        C = self.sb.new_repo("C")
        wtC = self.attach(C)
        head = self.sb.git(wtC, "rev-parse", "HEAD")
        (self.git_dir(wtC) / "team-test-lock.json").write_text(json.dumps({"files": {SEALED_A: head}, "history": []}))
        self.ok(self.sb.tg("git-hook", "install", cwd=wtC))
        files = {"A": (wtA, SEALED_A), "B": (wtB, SEALED_B), "C": (wtC, SEALED_A)}
        rng = random.Random(48)
        for n in range(4):
            for subset in itertools.combinations("ABC", n):
                kinds = {name: rng.choice(("edit", "delete")) for name in subset}
                with self.subTest(tampered=kinds):
                    for name, kind in kinds.items():
                        wt, f = files[name]
                        if kind == "edit":
                            (wt / f).write_text("// weakened %s\n" % name)
                        else:
                            (wt / f).unlink()
                    for cwd in (ws, wtA):
                        r = self.sb.tg("verify", cwd=cwd)
                        t = self.hook_task(cwd)
                        s = self.hook_stop(cwd)
                        if not subset:
                            self.code(r, 0)
                            self.has(r.out, "unchanged in 3 attached repo(s)", r)
                            self.code(t, 0)
                            self.assertEqual(t.out + t.err, "", self.show(t))
                            self.assertEqual(s.out, "", self.show(s))
                            continue
                        self.code(r, 2)
                        self.code(t, 2)
                        self.has(t.err, "can't complete", t)
                        self.assertEqual(json.loads(s.out).get("decision"), "block", self.show(s))
                        for name in "ABC":
                            wt, f = files[name]
                            line = "  %s: %s (" % (name, f)
                            if name in subset:
                                self.has(r.err, line, r)
                                self.has(t.err, "%s: %s" % (name, f), t)
                                self.has(json.loads(s.out)["reason"], f, s)
                            else:
                                self.lacks(r.err, line, r)
                    for name in subset:
                        wt, f = files[name]
                        self.sb.git(wt, "checkout", "-q", "--", f)

    def test_verify_reports_violations_per_repo(self):
        ws, A, B, wtA, wtB = self.sealed_ws()
        self.sb.commit_file(wtB, SEALED_B, "// weakened\n", "weaken", no_verify=True)
        for cwd in (wtA, ws):
            with self.subTest(cwd=str(cwd)):
                r = self.sb.tg("verify", cwd=cwd)
                self.code(r, 2)
                self.has(r.err, "team-guard: sealed tests were modified:", r)
                self.has(r.err, "  B: %s (changed since " % SEALED_B, r)
                t = self.hook_task(cwd)
                self.code(t, 2)
                self.has(t.err, "can't complete", t)
                self.has(t.err, "B: %s" % SEALED_B, t)
                s = self.hook_stop(cwd, active=False)
                self.code(s, 0)
                data = json.loads(s.out)
                self.assertEqual(data["decision"], "block")
                self.has(data["reason"], SEALED_B, s)
                s = self.hook_stop(cwd, active=True)
                self.code(s, 0)
                data = json.loads(s.out)
                self.assertNotIn("decision", data)
                self.has(data["systemMessage"], "team-guard:", s)
                self.has(data["systemMessage"], SEALED_B, s)

    def test_verify_reports_missing_worktree(self):
        ws, A, B, wtA, wtB = self.sealed_ws()
        import shutil
        shutil.rmtree(wtB)
        r = self.sb.tg("verify", cwd=ws)
        self.code(r, 2)
        self.has(r.err, "  B: worktree missing: %s" % realpath(wtB), r)
        r = self.sb.tg("verify", cwd=wtA)
        self.code(r, 2)
        self.has(r.err, "worktree missing", r)
        t = self.hook_task(wtA)
        self.code(t, 2)
        self.has(t.err, "worktree missing", t)
        s = self.hook_stop(ws)
        self.assertEqual(json.loads(s.out)["decision"], "block", self.show(s))
        self.has(json.loads(s.out)["reason"], "worktree missing", s)

    def test_verify_clean_workspace(self):
        ws, A, B, wtA, wtB = self.sealed_ws()
        for cwd in (ws, wtA, wtB / "pkg"):
            with self.subTest(cwd=str(cwd)):
                r = self.sb.tg("verify", cwd=cwd)
                self.ok(r)
                self.has(r.out, "team-guard: all sealed tests unchanged in 2 attached repo(s)", r)
                for res in (self.hook_task(cwd), self.hook_stop(cwd), self.hook_stop(cwd, active=True)):
                    self.code(res, 0)
                    self.assertEqual(res.out + res.err, "", self.show(res))

    def test_invalid_workspace_json_fails_closed(self):
        ws, A, B, wtA, wtB = self.sealed_ws()
        (ws / "workspace.json").write_text("{")
        r = self.sb.tg("verify", cwd=ws)
        self.code(r, 2)
        self.has(r.err, "workspace.json", r)
        t = self.hook_task(ws)
        self.code(t, 2)

    def test_dangling_pointer_checks_current_repo_only(self):
        ws, A, B, wtA, wtB = self.sealed_ws()
        (self.git_dir(wtA) / "team-workspace").write_text("/nonexistent\n")
        (wtB / SEALED_B).write_text("// weakened\n")  # not A's business any more
        r = self.sb.tg("verify", cwd=wtA)
        self.code(r, 0)
        self.has(r.err, "dangling", r)
        (wtA / SEALED_A).write_text("// weakened\n")
        r = self.sb.tg("verify", cwd=wtA)
        self.code(r, 2)
        self.has(r.err, "dangling", r)
        self.has(r.err, SEALED_A, r)


class TestPrePush(GuardCase):

    def hooks_dir(self, repo):
        p = Path(self.sb.git(repo, "rev-parse", "--git-path", "hooks"))
        return p if p.is_absolute() else Path(repo) / p

    def foreign_pre_push(self, repo, code=0, marker="foreign-pre-push-ran"):
        stdin_copy = self.sb.tmp / ("%s-pre-push-stdin" % Path(repo).name)
        hook = self.hooks_dir(repo) / "pre-push"
        hook.parent.mkdir(parents=True, exist_ok=True)
        text = "#!/bin/sh\necho %s\ncat > '%s'\nexit %d\n" % (marker, stdin_copy, code)
        hook.write_text(text)
        hook.chmod(0o755)
        return hook, text, stdin_copy

    def remote_ref(self, bare, ref="main"):
        return self.sb.git(bare, "rev-parse", ref, check=False).out.strip()

    def test_seal_installs_pre_push(self):
        R, bare = self.standalone(remote=True)
        r = self.seal(R, SEALED_A)
        self.has(r.out, "team-guard: installed pre-push hook", r)
        self.assertEqual(self.ok(self.sb.tg("git-hook", "status", "pre-push", cwd=R)).out, "installed\n")
        self.assertEqual(self.ok(self.sb.tg("git-hook", "status", "pre-commit", cwd=R)).out, "installed\n")
        self.assertEqual(self.ok(self.sb.tg("git-hook", "status", cwd=R)).out, "installed\n")
        self.sb.commit_file(R, "pkg/a.go", "package pkg // v2\n", "v2")
        r = self.sb.git(R, "push", "-q", "origin", "main", check=False)
        self.code(r, 0)
        self.assertEqual(self.remote_ref(bare), self.sb.git(R, "rev-parse", "HEAD"))

    def test_foreign_pre_push_is_chained(self):
        R, bare = self.standalone(remote=True)
        _, _, stdin_copy = self.foreign_pre_push(R)
        r = self.seal(R, SEALED_A)
        self.has(r.out, "installed pre-push hook (chaining the existing one)", r)
        self.assertEqual(self.ok(self.sb.tg("git-hook", "status", "pre-push", cwd=R)).out, "installed\n")
        self.sb.commit_file(R, "pkg/a.go", "package pkg // v2\n", "v2")
        r = self.sb.git(R, "push", "origin", "main", check=False)
        self.code(r, 0)
        self.has(r.out + r.err, "foreign-pre-push-ran", r)
        self.has(stdin_copy.read_text(), "refs/heads/")

    def test_chained_pre_push_exit_status_is_honoured(self):
        R, bare = self.standalone(remote=True)
        self.foreign_pre_push(R, code=1)
        self.seal(R, SEALED_A)
        self.assertEqual(self.ok(self.sb.tg("git-hook", "status", "pre-push", cwd=R)).out, "installed\n",
                         "team-guard's hook is in front, the foreign one chained behind it")
        self.assertTrue((self.hooks_dir(R) / "pre-push.team-guard-chained").exists())
        before = self.remote_ref(bare)
        self.sb.commit_file(R, "pkg/a.go", "package pkg // v2\n", "v2")
        r = self.sb.git(R, "push", "origin", "main", check=False)
        self.nonzero(r)
        self.has(r.out + r.err, "foreign-pre-push-ran", r)
        self.assertEqual(self.remote_ref(bare), before)

    def test_push_refused_then_allowed(self):
        R, bare = self.standalone(remote=True)
        _, _, stdin_copy = self.foreign_pre_push(R)
        sealed_text = (R / SEALED_A).read_text()
        self.seal(R, SEALED_A)
        before = self.remote_ref(bare)
        self.sb.commit_file(R, SEALED_A, "// weakened\n", "weaken", no_verify=True)
        r = self.sb.git(R, "push", "origin", "main", check=False)
        self.nonzero(r)
        self.has(r.err, "push refused", r)
        self.has(r.err, SEALED_A, r)
        self.assertEqual(self.remote_ref(bare), before)
        self.assertFalse(stdin_copy.exists(), "the chained hook runs only after a passing verify")
        self.sb.commit_file(R, SEALED_A, sealed_text, "restore")
        r = self.sb.git(R, "push", "origin", "main", check=False)
        self.code(r, 0)
        self.has(r.out + r.err, "foreign-pre-push-ran", r)
        self.assertEqual(self.remote_ref(bare), self.sb.git(R, "rev-parse", "HEAD"))

    def test_pre_push_command_directly(self):
        R, bare = self.standalone(remote=True)
        self.seal(R, SEALED_A)
        r = self.sb.tg("pre-push", "origin", str(bare), cwd=R, input="")
        self.code(r, 0)
        self.assertEqual(r.out + r.err, "")
        (R / SEALED_A).write_text("// weakened\n")
        r = self.sb.tg("pre-push", "origin", str(bare), cwd=R, input="")
        self.code(r, 1)
        self.has(r.err, "push refused", r)
        self.has(r.err, "  %s (changed since " % SEALED_A, r)

    def test_pre_push_checks_only_the_pushed_repo(self):
        ws, A, B, wtA, wtB = self.sealed_ws(remotes=True)
        (wtB / SEALED_B).write_text("// weakened\n")
        self.sb.commit_file(wtA, "pkg/a.go", "package pkg // A work\n", "A work")
        r = self.sb.git(wtA, "push", "-q", "origin", "feat/f", check=False)
        self.code(r, 0, "B's violation must not block pushing A")
        r = self.sb.tg("pre-push", "origin", "x", cwd=wtB, input="")
        self.code(r, 1)
        self.lacks(r.err, SEALED_A, r)

    def test_uninstall_restores_and_install_rechains(self):
        R, _ = self.standalone()
        hook, text, _ = self.foreign_pre_push(R)
        self.seal(R, SEALED_A)
        r = self.ok(self.sb.tg("git-hook", "uninstall", cwd=R))
        lines = r.out.splitlines()
        self.assertEqual(len(lines), 2, self.show(r))
        self.has(lines[0], "pre-commit", r)
        self.has(lines[-1], "team-guard: removed pre-push hook and restored the previous one", r)
        self.assertEqual(hook.read_text(), text)
        self.assertEqual(self.ok(self.sb.tg("git-hook", "status", "pre-push", cwd=R)).out, "foreign\n")
        r = self.ok(self.sb.tg("git-hook", "install", cwd=R))
        self.assertEqual(len(r.out.splitlines()), 2, self.show(r))
        self.has(r.out.splitlines()[-1], "team-guard: installed pre-push hook (chaining the existing one)", r)
        # another tool replaces our hook; install re-chains the new one
        hook.write_text("#!/bin/sh\necho other-tool\n")
        hook.chmod(0o755)
        self.assertEqual(self.ok(self.sb.tg("git-hook", "status", "pre-push", cwd=R)).out, "foreign\n")
        r = self.ok(self.sb.tg("git-hook", "install", cwd=R))
        self.has(r.out, "pre-push hook (chaining the existing one)", r)
        chained = hook.parent / "pre-push.team-guard-chained"
        self.assertIn("other-tool", chained.read_text())
        self.assertEqual(self.ok(self.sb.tg("git-hook", "status", "pre-push", cwd=R)).out, "installed\n")

    def test_uninstall_leaves_foreign_hook_alone(self):
        R, _ = self.standalone()
        self.ok(self.sb.tg("git-hook", "install", cwd=R))
        hook = self.hooks_dir(R) / "pre-push"
        hook.write_text("#!/bin/sh\necho mine\n")
        r = self.ok(self.sb.tg("git-hook", "uninstall", cwd=R))
        self.has(r.out, "team-guard: pre-push hook is not team-guard's; left alone", r)
        self.assertEqual(hook.read_text(), "#!/bin/sh\necho mine\n")

    def test_seal_writes_only_into_hooks_dir(self):
        R, _ = self.standalone()
        files = self.sb.git(R, "ls-files")
        self.seal(R, SEALED_A)
        self.assertEqual(self.sb.git(R, "status", "--porcelain", "--ignored"), "")
        self.assertEqual(self.sb.git(R, "ls-files"), files)
        self.assertTrue((self.hooks_dir(R) / "pre-push").exists())


class TestCompatibility(GuardCase):

    def test_unattached_verify_output_is_unchanged(self):
        R, _ = self.standalone()
        self.seal(R, SEALED_A)
        r = self.sb.tg("verify", cwd=R)
        self.code(r, 0)
        self.assertEqual(r.out, "team-guard: all sealed tests unchanged\n")
        self.assertEqual(r.err, "")
        (R / SEALED_A).write_text("// weakened\n")
        r = self.sb.tg("verify", cwd=R)
        self.code(r, 2)
        self.assertEqual(r.out, "")
        self.assertTrue(r.err.startswith("team-guard: sealed tests were modified:\n"), self.show(r))
        self.has(r.err, "\n  %s (changed since " % SEALED_A, r)
        self.sb.git(R, "checkout", "-q", "--", SEALED_A)

    def test_missing_pre_push_is_not_warned(self):
        R, _ = self.standalone()
        self.seal(R, SEALED_A)
        hooks = Path(self.sb.git(R, "rev-parse", "--git-path", "hooks"))
        hooks = hooks if hooks.is_absolute() else R / hooks
        self.assertTrue((hooks / "pre-push").exists(), "seal installs pre-push")
        (hooks / "pre-push").unlink()
        r = self.sb.tg("verify", cwd=R)
        self.code(r, 0)
        self.assertEqual(r.err, "")
        (hooks / "pre-commit").unlink()
        r = self.sb.tg("verify", cwd=R)
        self.code(r, 0)
        self.has(r.err, "not installed", r)

    def test_source_checkout_is_not_in_the_workspace(self):
        ws, A, B, wtA, wtB = self.sealed_ws()
        self.seal(A, SEALED_B)
        (wtA / SEALED_A).write_text("// weakened\n")
        r = self.sb.tg("verify", cwd=A)
        self.code(r, 0)
        self.assertEqual(r.out, "team-guard: all sealed tests unchanged\n")
        t = self.hook_task(A)
        self.code(t, 0)

    def test_unattached_command_output_is_unchanged(self):
        R, _ = self.standalone()
        r = self.ok(self.sb.tg("git-hook", "install", cwd=R))
        lines = r.out.splitlines()
        self.assertEqual(lines, ["team-guard: installed pre-commit hook", "team-guard: installed pre-push hook"],
                         self.show(r))
        r = self.ok(self.sb.tg("git-hook", "install", cwd=R))
        self.assertEqual(r.out.splitlines(), ["team-guard: pre-commit hook already installed",
                                              "team-guard: pre-push hook already installed"])
        r = self.ok(self.sb.tg("git-hook", "uninstall", cwd=R))
        self.assertEqual(r.out.splitlines(), ["team-guard: removed pre-commit hook",
                                              "team-guard: removed pre-push hook"])
        R2, _ = self.standalone("R2")
        r = self.seal(R2, SEALED_A)
        head = self.sb.git(R2, "rev-parse", "HEAD")
        self.assertIn("sealed 1 file(s) at %s" % head[:12], r.out.splitlines(), self.show(r))
        self.assertEqual(self.ok(self.sb.tg("git-hook", "status", cwd=R2)).out, "installed\n")
        r = self.sb.tg("workspace-path", cwd=R2)
        self.code(r, 1)
        self.assertEqual(r.out, "")


class TestNoWorkingTreeWrites(GuardCase):

    def test_no_command_writes_into_working_trees(self):
        ws, A, B, wtA, wtB = self.two_repo_ws(remotes=True)
        sources = {"A": A, "B": B}
        wts = {"A": wtA, "B": wtB}

        def snapshot():
            return ({n: self.sb.source_state(p) for n, p in sources.items()},
                    {n: (tree_snapshot(p, skip=(".git",)), self.sb.git(p, "status", "--porcelain", "--ignored"))
                     for n, p in wts.items()})

        before = snapshot()
        ev = lambda cwd, **kw: json.dumps(dict({"cwd": str(cwd)}, **kw))
        steps = [
            (ws, ("workspace-path",), None), (wtA, ("workspace-path",), None),
            (self.sb.scratch, ("workspace", "show", "f"), None), (ws, ("workspace", "show"), None),
            (self.sb.scratch, ("workspace", "list"), None),
            (ws, ("holdout-path",), None), (wtA, ("holdout-path",), None),
            (wtA, ("seal", SEALED_A), None), (wtB, ("seal", SEALED_B), None),
            (wtA, ("list",), None), (wtB, ("git-hook", "status", "pre-push"), None),
            (wtA, ("git-hook", "install"), None),
            (ws, ("verify",), None), (wtA, ("verify",), None), (A, ("verify",), None),
            (ws, ("hook-task",), ev(ws)), (wtB, ("hook-stop",), ev(wtB, stop_hook_active=False)),
            (ws, ("hook-pretool",), ev(ws, agent_type="software-engineer", tool_name="Edit",
                                        tool_input={"file_path": "repos/A/" + SEALED_A})),
            (wtA, ("pre-push", "origin", "x"), ""),
            (wtB, ("unseal", SEALED_B), None), (wtB, ("seal", SEALED_B), None),
            (self.sb.scratch, ("gc",), None),
            (self.sb.scratch, ("workspace", "list"), None),
        ]
        for cwd, args, stdin in steps:
            with self.subTest(step=" ".join(args), cwd=str(cwd)):
                r = self.sb.tg(*args, cwd=cwd, input=stdin)
                self.no_traceback(r)
                self.assertNotEqual(r.code, 70, "not implemented: %s\n%s" % (" ".join(args), self.show(r)))
                self.assertEqual(snapshot(), before, "%s changed a working tree" % " ".join(args))


if __name__ == "__main__":
    unittest.main()
