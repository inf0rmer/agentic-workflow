"""The installers clean up after themselves, against a fake home laid out like an older install.

Files the new build no longer ships are moved to the backup, never deleted. A file the user's
own settings or hooks still use stays, with a warning. Only our own hook entries leave the
settings file, after a backup. A second install changes nothing.
"""

import json
import unittest

from test_workspace_installers import InstallCase
from wslib import ROOT, tree_snapshot

OLD_AGENTS = ("architect", "doc-writer", "product-owner", "researcher", "reviewer", "software-engineer", "tester")
OLD_SKILLS = ("jev-decide", "codebase-design", "domain-modeling", "to-questionnaire", "tdd-inner-loop", "grilling",
              "handoff", "diagnosing-bugs", "writing-for-agents")
RETIRED_SKILLS = ("jev-decide", "codebase-design", "domain-modeling", "to-questionnaire", "tdd-inner-loop")
USER_HOOK = {"type": "command", "command": "~/.claude/hooks/rtk-rewrite.sh"}
GUARD_RULE = "Bash(~/.claude/team/bin/team-guard:*)"


def touch(path, text="old\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class CleanupCase(InstallCase):

    def old_claude_settings(self):
        return {
            "model": "opus",
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [USER_HOOK]},
                    {"matcher": "Edit|Write", "hooks": [
                        {"type": "command", "command": "~/.claude/team/bin/team-guard hook-pretool", "timeout": 10}]},
                ],
                "SubagentStop": [{"hooks": [
                    {"type": "command", "command": "~/.claude/team/bin/team-guard hook-stop", "timeout": 20}]}],
                "TaskCompleted": [{"hooks": [
                    {"type": "command", "command": "~/.claude/team/bin/team-guard hook-task", "timeout": 20}]}],
                "Notification": [{"hooks": [{"type": "command", "command": "~/.claude/hooks/notify.sh"}]}],
            },
            "permissions": {"allow": [
                "Bash(git status:*)",
                "Bash(~/.claude/team/bin/team-guard:*)",
                "Bash(~/.claude/team/bin/mutation-check:*)",
            ]},
        }

    def old_claude_home(self, jev_hook=True):
        d = self.home / ".claude"
        for a in OLD_AGENTS:
            touch(d / "agents" / (a + ".md"))
        touch(d / "CLAUDE.md")
        touch(d / "team" / "bin" / "team-guard", "#!/bin/sh\n")
        touch(d / "team" / "bin" / "mutation-check", "#!/bin/sh\n")
        for n in ("guard-rails.md", "decisions-with-jev.md", "pr-loop.md"):
            touch(d / "team" / "docs" / n)
        for s in OLD_SKILLS:
            touch(d / "skills" / s / "SKILL.md")
        touch(d / "skills" / "my-own-skill" / "SKILL.md", "mine\n")
        touch(d / "agents" / "my-own-agent.md", "mine\n")
        (d / "settings.json").write_text(json.dumps(self.old_claude_settings(), indent=2) + "\n")
        touch(d / "hooks" / "notify.sh", "#!/bin/sh\n")
        if jev_hook:
            touch(d / "hooks" / "jev-permission-hook",
                  "import os, sys\nsys.path.insert(0, os.path.expanduser(\"~/.claude/skills/jev-decide/scripts\"))\n")
        return d

    def settings(self, d):
        return json.loads((d / "settings.json").read_text())

    def backup(self, harness):
        backups = sorted((self.dest(harness) / "backups").glob("agentic-workflow-*"))
        self.assertTrue(backups, "a backup was made")
        return backups[-1]

    def rest_of_home(self, harness):
        return tree_snapshot(self.dest(harness), skip=("backups", "team/installed.json"))


class TestClaudeCodeCleanup(CleanupCase):

    def test_retired_files_are_moved_to_the_backup_and_the_ones_you_use_stay(self):
        d = self.old_claude_home(jev_hook=True)
        r = self.install("claude-code")
        b = self.backup("claude-code") / "retired"
        self.assertFalse((d / "agents" / "tester.md").exists())
        self.assertEqual((b / "dest" / "agents" / "tester.md").read_text(), "old\n")
        self.assertFalse((d / "team" / "bin" / "mutation-check").exists())
        self.assertTrue((b / "dest" / "team" / "bin" / "mutation-check").is_file())
        for n in ("guard-rails.md", "decisions-with-jev.md"):
            self.assertFalse((d / "team" / "docs" / n).exists())
            self.assertTrue((b / "dest" / "team" / "docs" / n).is_file())
        for s in RETIRED_SKILLS:
            if s == "jev-decide":
                continue
            self.assertFalse((d / "skills" / s).exists(), s)
            self.assertTrue((b / "skills" / s / "SKILL.md").is_file(), s)
        # Your own hook imports jev-decide: it stays, and the install says so.
        self.assertTrue((d / "skills" / "jev-decide" / "SKILL.md").is_file())
        self.assertFalse((b / "skills" / "jev-decide").exists())
        self.has(r.err, "left %s in place" % (d / "skills" / "jev-decide"), r)
        self.has(r.err, "jev-permission-hook", r)
        # Your own things are untouched.
        self.assertEqual((d / "skills" / "my-own-skill" / "SKILL.md").read_text(), "mine\n")
        self.assertEqual((d / "agents" / "my-own-agent.md").read_text(), "mine\n")
        self.assertEqual((d / "hooks" / "notify.sh").read_text(), "#!/bin/sh\n")
        # The new build is in place.
        self.assertFalse((d / "skills" / "tdd-inner-loop").exists())
        self.assertTrue((d / "skills" / "tdd" / "SKILL.md").is_file())
        self.assertEqual((d / "CLAUDE.md").read_bytes(), (ROOT / "dist" / "claude-code" / "CLAUDE.md").read_bytes())

    def test_a_skill_nothing_uses_is_retired_even_when_it_is_jev_decide(self):
        d = self.old_claude_home(jev_hook=False)
        s = self.settings(d)
        self.install("claude-code")
        self.assertFalse((d / "skills" / "jev-decide").exists())
        self.assertTrue((self.backup("claude-code") / "retired" / "skills" / "jev-decide" / "SKILL.md").is_file())
        self.assertEqual(s["model"], self.settings(d)["model"])

    def test_a_settings_rule_that_names_a_skill_keeps_it_in_place(self):
        d = self.old_claude_home(jev_hook=False)
        s = self.settings(d)
        s["permissions"]["allow"].append("Bash(~/.claude/skills/jev-decide/scripts/ask-jev:*)")
        (d / "settings.json").write_text(json.dumps(s))
        r = self.install("claude-code")
        self.assertTrue((d / "skills" / "jev-decide" / "SKILL.md").is_file())
        self.has(r.err, "settings.json still mentions it", r)
        self.assertIn("Bash(~/.claude/skills/jev-decide/scripts/ask-jev:*)", self.settings(d)["permissions"]["allow"])

    def test_only_our_hook_entries_leave_settings_after_a_backup(self):
        d = self.old_claude_home()
        before = (d / "settings.json").read_text()
        r = self.install("claude-code")
        s = self.settings(d)
        flat = json.dumps(s["hooks"])
        self.assertNotIn("team-guard hook-", flat)
        self.assertNotIn("mutation-check", json.dumps(s))
        self.assertIn("~/.claude/hooks/rtk-rewrite.sh", flat, "your hook stays")
        self.assertIn("~/.claude/hooks/notify.sh", flat, "your other hook stays")
        self.assertEqual(s["hooks"]["PreToolUse"], [{"matcher": "Bash", "hooks": [USER_HOOK]}],
                         "the group that held only our hook is gone")
        self.assertNotIn("SubagentStop", s["hooks"])
        self.assertNotIn("TaskCompleted", s["hooks"])
        self.assertEqual(s["model"], "opus")
        self.assertEqual(s["permissions"]["allow"].count(GUARD_RULE), 1)
        self.assertIn("Bash(git status:*)", s["permissions"]["allow"])
        self.assertEqual((self.backup("claude-code") / "settings.json").read_text(), before)
        self.has(r.out, "removed hook PreToolUse: ~/.claude/team/bin/team-guard hook-pretool", r)

    def test_the_guard_allow_rule_is_added_when_missing(self):
        d = self.old_claude_home()
        s = self.settings(d)
        s["permissions"]["allow"].remove(GUARD_RULE)
        (d / "settings.json").write_text(json.dumps(s))
        self.install("claude-code")
        self.assertEqual(self.settings(d)["permissions"]["allow"].count(GUARD_RULE), 1)

    def test_a_second_install_changes_nothing(self):
        self.old_claude_home()
        self.install("claude-code")
        first = self.rest_of_home("claude-code")
        settings = (self.home / ".claude" / "settings.json").read_bytes()
        r = self.install("claude-code")
        self.assertEqual(self.rest_of_home("claude-code"), first)
        self.assertEqual((self.home / ".claude" / "settings.json").read_bytes(), settings)
        self.lacks(r.out, "retired", r)
        self.lacks(r.out, "updated", r)

    def test_a_file_dropped_between_two_builds_is_retired_through_the_manifest(self):
        self.install("claude-code")
        d = self.home / ".claude"
        manifest = json.loads((d / "team" / "installed.json").read_text())
        paths = {(e["root"], e["path"]) for e in manifest["entries"]}
        self.assertIn(("dest", "agents/architect.md"), paths)
        self.assertIn(("skills", "tdd"), paths)
        # A role the previous build shipped, and a file of yours in the same dir.
        touch(d / "agents" / "retired-role.md")
        touch(d / "agents" / "my-own-agent.md", "mine\n")
        manifest["entries"].append({"root": "dest", "path": "agents/retired-role.md"})
        (d / "team" / "installed.json").write_text(json.dumps(manifest))
        self.install("claude-code")
        self.assertFalse((d / "agents" / "retired-role.md").exists())
        self.assertTrue((self.backup("claude-code") / "retired" / "dest" / "agents" / "retired-role.md").is_file())
        self.assertEqual((d / "agents" / "my-own-agent.md").read_text(), "mine\n")
        new = json.loads((d / "team" / "installed.json").read_text())
        self.assertNotIn({"root": "dest", "path": "agents/retired-role.md"}, new["entries"])

    def test_a_settings_file_that_is_not_json_stops_the_install_and_is_left_alone(self):
        d = self.old_claude_home()
        (d / "settings.json").write_text("{ not json")
        r = self.run_install_expecting_failure("claude-code")
        self.has(r.err, "settings.json is not valid JSON", r)
        self.assertEqual((d / "settings.json").read_text(), "{ not json")

    def test_a_team_guard_hook_entry_it_cannot_remove_fails_the_install_loudly(self):
        d = self.old_claude_home()
        s = self.settings(d)
        s["statusLine"] = {"type": "command", "command": "~/.claude/team/bin/team-guard hook-stop"}
        (d / "settings.json").write_text(json.dumps(s))
        r = self.run_install_expecting_failure("claude-code")
        self.has(r.err, "still has a team-guard hook entry", r)

    def test_the_install_says_how_to_remove_old_git_hooks_from_every_repo(self):
        self.old_claude_home()
        r = self.install("claude-code")
        self.has(r.out, "scripts/remove-old-git-hooks --scan ~/Code --dry-run", r)
        self.has(r.out, "without --dry-run", r)

    def test_the_settings_rewrite_keeps_the_file_mode(self):
        d = self.old_claude_home()
        (d / "settings.json").chmod(0o600)
        self.install("claude-code")
        self.assertEqual((d / "settings.json").stat().st_mode & 0o777, 0o600)
        self.assertNotIn("team-guard hook-", (d / "settings.json").read_text())

    def test_the_settings_rewrite_goes_through_a_symlink_to_its_target(self):
        d = self.old_claude_home()
        target = self.home / "dotfiles" / "settings.json"
        target.parent.mkdir()
        (d / "settings.json").rename(target)
        (d / "settings.json").symlink_to(target)
        self.install("claude-code")
        self.assertTrue((d / "settings.json").is_symlink(), "the link stays a link")
        self.assertEqual((d / "settings.json").resolve(), target.resolve())
        self.assertNotIn("team-guard hook-", target.read_text())
        self.assertIn(GUARD_RULE, json.loads(target.read_text())["permissions"]["allow"])

    def test_a_number_json_cannot_write_back_stops_the_install_and_leaves_the_file_alone(self):
        d = self.old_claude_home()
        raw = json.dumps(self.old_claude_settings()).replace('"model": "opus"', '"model": "opus", "limit": 1e400')
        self.assertIn("1e400", raw)
        (d / "settings.json").write_text(raw)
        r = self.run_install_expecting_failure("claude-code")
        self.has(r.err, "settings.json", r)
        self.has(r.err, "not a finite number", r)
        self.assertEqual((d / "settings.json").read_text(), raw)

    def run_install_expecting_failure(self, harness):
        env = {"HOME": str(self.home), "CLAUDE_HOME": str(self.home / ".claude"), "CODEX_HOME": str(self.home / ".codex")}
        r = self.sb.run(["bash", str(self.repo / "adapters" / harness / "install.sh")], cwd=self.sb.scratch, env=env)
        self.nonzero(r)
        return r


class TestCodexCleanup(CleanupCase):

    def old_codex_home(self):
        d = self.home / ".codex"
        skills = self.home / ".agents" / "skills"
        for a in OLD_AGENTS:
            touch(d / "agents" / (a + ".toml"))
        touch(d / "AGENTS.md")
        touch(d / "team" / "bin" / "mutation-check", "#!/bin/sh\n")
        touch(d / "team" / "docs" / "guard-rails.md")
        touch(d / "rules" / "agentic-workflow.rules", 'prefix_rule(pattern=["x/mutation-check"], decision="allow")\n')
        touch(d / "rules" / "mine.rules", "mine\n")
        for s in OLD_SKILLS:
            touch(skills / s / "SKILL.md")
        hooks = {"hooks": {
            "PreToolUse": [{"matcher": "^(apply_patch|Edit|Write)$", "hooks": [
                {"type": "command", "command": "~/.codex/team/bin/team-guard hook-pretool", "timeout": 10}]}],
            "SubagentStop": [{"hooks": [
                {"type": "command", "command": "~/.codex/team/bin/team-guard hook-stop", "timeout": 20},
                {"type": "command", "command": "~/.codex/herdr-agent-state.sh"}]}],
        }}
        (d / "hooks.json").write_text(json.dumps(hooks, indent=2) + "\n")
        return d, skills

    def test_codex_files_skills_and_hooks_are_cleaned_up_too(self):
        d, skills = self.old_codex_home()
        self.install("codex")
        b = self.backup("codex") / "retired"
        self.assertFalse((d / "agents" / "tester.toml").exists())
        self.assertTrue((b / "dest" / "agents" / "tester.toml").is_file())
        for s in RETIRED_SKILLS:
            self.assertFalse((skills / s).exists(), s)
            self.assertTrue((b / "skills" / s / "SKILL.md").is_file(), s)
        self.assertTrue((skills / "tdd" / "SKILL.md").is_file())
        self.assertFalse((d / "team" / "bin" / "mutation-check").exists())
        rules = (d / "rules" / "agentic-workflow.rules").read_text()
        self.assertNotIn("mutation-check", rules)
        self.assertIn("team-guard", rules)
        self.assertEqual((d / "rules" / "mine.rules").read_text(), "mine\n")
        hooks = json.loads((d / "hooks.json").read_text())
        self.assertEqual(hooks, {"hooks": {"SubagentStop": [{"hooks": [
            {"type": "command", "command": "~/.codex/herdr-agent-state.sh"}]}]}})
        self.assertTrue((self.backup("codex") / "hooks.json").is_file())

    def test_a_second_codex_install_changes_nothing(self):
        self.old_codex_home()
        self.install("codex")
        first = (self.rest_of_home("codex"), tree_snapshot(self.home / ".agents"))
        self.install("codex")
        self.assertEqual((self.rest_of_home("codex"), tree_snapshot(self.home / ".agents")), first)


if __name__ == "__main__":
    unittest.main()
