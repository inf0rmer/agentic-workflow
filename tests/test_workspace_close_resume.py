"""close must stay resumable until the workspace dir is gone.

`repos/` goes first and the workspace dir (which holds the `closing` marker) last, so a
rerun of close completes whatever is left after an interruption or a failed step.
"""

import os
import shutil
import unittest

from test_workspace_lifecycle import LifecycleCase
from wslib import realpath


class TestCloseTeardown(LifecycleCase):

    def assert_closed(self, ws, repos, feature="f"):
        self.assertFalse(os.path.lexists(ws), "no %s/ may be left behind" % feature)
        ar = self.sb.ar_root / feature
        self.assertEqual((ar / "notes.md").read_text() if (ar / "notes.md").exists() else None, "keep\n")
        self.assertTrue((ar / "archive.json").is_file())
        self.assertFalse((self.sb.ar_root / (feature + "+2")).exists(), "a resumed close reuses its archive")
        for repo in repos.values():
            self.assertEqual(self.sb.worktrees(repo), [realpath(repo)])
        r = self.sb.tg("workspace", "list")
        self.code(r, 0)
        self.assertNotIn(feature + "\t", r.out)
        g = self.gc()
        self.code(g, 0, "gc must not trip over leftovers")

    def test_close_after_repos_deleted_by_hand(self):
        ws, repos, wts = self.pushed_ws("f", "A", "B")
        (ws / "notes.md").write_text("keep\n")
        shutil.rmtree(ws / "repos")
        r = self.close("f")
        self.no_traceback(r)
        if r.code != 0:
            r = self.close("f")  # "succeeds, or is resumable on a rerun"
            self.ok(r, "rerun of close after repos/ was deleted by hand")
        self.has(r.out, "closed f", r)
        self.assert_closed(ws, repos)

    def test_gc_apply_after_repos_deleted_by_hand(self):
        ws, repos, wts = self.pushed_ws("f", "A")
        (ws / "notes.md").write_text("keep\n")
        shutil.rmtree(ws / "repos")
        r = self.gc("--apply")
        self.no_traceback(r)
        self.code(r, 0, "gc --apply on a workspace whose repos/ was deleted")
        self.has(r.out, "closed f", r)
        self.assert_closed(ws, repos)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "chmod can't block root")
    def test_rerun_completes_after_the_last_teardown_step_fails(self):
        # The final step removes the workspace dir itself; a read-only workspace root makes
        # exactly that step fail, after everything else has been done.
        ws, repos, wts = self.pushed_ws("f", "A")
        (ws / "notes.md").write_text("keep\n")
        os.chmod(self.sb.ws_root, 0o555)
        try:
            r = self.close("f")
        finally:
            os.chmod(self.sb.ws_root, 0o755)
        self.code(r, 1)
        self.no_traceback(r)
        self.assertRegex(r.err, r"(?m)^team-guard: .*close.*: ", self.show(r))
        self.assertTrue((self.sb.ar_root / "f" / "notes.md").exists())
        r = self.close("f")
        self.ok(r, "rerun after the final step failed")
        self.assert_closed(ws, repos)


if __name__ == "__main__":
    unittest.main()
