"""Shared helpers for the signing suites: a sandbox plus feature-branch repo helpers.

Everything runs offline in the per-test Sandbox from wslib.
"""

from pathlib import Path

from wslib import WSTestCase


class ToolCase(WSTestCase):

    def feature_repo(self, name="R", branch="feat/x"):
        """A repo on `branch` off main, with the Sandbox's standard files on main."""
        repo = self.sb.new_repo(name)
        self.sb.git(repo, "checkout", "-q", "-b", branch)
        return repo

    def git_dir(self, repo):
        return Path(self.sb.git(repo, "rev-parse", "--absolute-git-dir"))

    def config_list(self, repo):
        return self.sb.git(repo, "config", "--list", "--show-origin")
