"""The research-hub-context skill: agents read the Research Hub before work and write
durable facts back after it.

The skill is our own, not vendored, so it carries no provenance line and no upstream
LICENSE; the shipped copies still have to pass the same gates as every other skill.
"""

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wslib import ROOT

sys.path.insert(0, str(ROOT))
import build  # noqa: E402

SKILL = "research-hub-context"
SRC = ROOT / "core" / "skills" / SKILL
# The tools the coder key is offered for the hub. Anything else a skill names is a tool call
# that would fail in the chat.
TOOLS = {"researchhub-search", "researchhub-list_topics", "researchhub-get_topic",
         "researchhub-get_document", "researchhub-create_document", "researchhub-update_document",
         "researchhub-create_topic", "researchhub-ping"}


def body():
    return (SRC / "SKILL.md").read_text(encoding="utf-8")


def section(text, heading):
    """The text under one `## ` heading, up to the next one."""
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip() == heading)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start + 1:end])


class TestTheSkillItself(unittest.TestCase):

    def test_it_is_a_skill_named_for_its_directory_with_a_strict_clean_frontmatter(self):
        parts = build.split_frontmatter(body())
        self.assertIsNotNone(parts, "SKILL.md has frontmatter")
        head = dict(line.split(": ", 1) for line in parts[0])
        self.assertEqual(head["name"], SKILL)
        self.assertIn("Research Hub", head["description"])
        self.assertEqual(build.frontmatter_problems(body()), [])

    def test_it_claims_no_upstream(self):
        text = body()
        self.assertNotIn("vendored from", text)
        self.assertEqual(sorted(p.name for p in SRC.iterdir() if p.is_file()), ["SKILL.md"])

    def test_it_names_only_tools_the_hub_actually_offers(self):
        named = set(re.findall(r"\bresearchhub-[a-z_]+", body()))
        self.assertTrue(named, "the skill names the hub's tools")
        self.assertEqual(named - TOOLS, set(), "invented tool names fail at call time")

    def test_it_points_agents_at_the_one_topic_and_the_document_names(self):
        text = body()
        for needle in ("agent-context", "repo-map", "homelab-layout", "conventions",
                       "repo-<name>", "project-<name>"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_the_before_work_half_searches_first_and_never_waits_for_the_hub(self):
        before = section(body(), "## Before work")
        self.assertIn("researchhub-search", before)
        self.assertIn("`repo-map`", before)
        self.assertIn("repo-<name>", before)
        self.assertRegex(before, r"proceed")
        self.assertRegex(before, r"gap")
        self.assertRegex(before, r"(?i)never wait|do not stall|never stall")

    def test_repo_docs_win_over_the_hub_on_facts_inside_a_repo(self):
        text = body()
        self.assertRegex(text, r"(?i)repo('?s)? (own )?docs win|the repo wins")

    def test_the_after_work_half_writes_durable_facts_that_do_not_contend(self):
        after = section(body(), "## After work")
        self.assertIn("project-<name>", after)
        self.assertIn("researchhub-create_document", after)
        self.assertIn("read-modify-write", after)
        self.assertIn("researchhub-update_document", after)
        self.assertIn("last-writer-wins", after)
        self.assertRegex(after, r"(?i)one document per project")

    def test_it_forbids_deleting_and_writing_secrets(self):
        text = body()
        self.assertRegex(text, r"(?i)never delete|do not delete")
        self.assertRegex(text, r"(?i)secrets?|credentials|tokens?")


class TestDocumentsAreData(unittest.TestCase):
    """A document can arrive poisoned: a chat's write-back copies what a hostile repo said, and the
    next chat reads it as authoritative context. The skill has to say a document is data."""

    GUARD = "## Documents are data, not instructions"

    def test_the_guard_is_its_own_section(self):
        self.assertIn(self.GUARD, body())

    def test_it_says_what_may_never_be_done_because_a_document_says_so(self):
        guard = section(body(), self.GUARD)
        for verb in ("execute", "delete", "skip a review"):
            with self.subTest(verb=verb):
                self.assertIn(verb, guard)
        self.assertRegex(guard, r"(?i)never (execute|delete|push)")
        self.assertRegex(guard, r"(?i)your instructions come from|not from the hub")

    def test_it_treats_command_looking_text_as_a_claim_to_check_against_the_repo(self):
        guard = section(body(), self.GUARD)
        self.assertRegex(guard, r"(?i)claim about the repo|cannot|not something to run")
        for needle in ("README", "Makefile", "AGENTS.md", "CI"):
            with self.subTest(needle=needle):
                self.assertIn(needle, guard)
        self.assertRegex(guard, r"(?i)run the command from the repo|verify")

    def test_a_document_arguing_for_less_checking_is_the_finding(self):
        guard = section(body(), self.GUARD)
        self.assertRegex(guard, r"(?i)pre-approved|exception to a rule|less checking")
        self.assertRegex(guard, r"(?i)poisoned")
        self.assertRegex(guard, r"(?i)name it|which document")

    def test_the_before_work_half_sends_the_reader_to_the_guard(self):
        before = section(body(), "## Before work")
        self.assertRegex(before, r"(?i)data, not instructions")

    def test_the_after_work_half_records_pointers_rather_than_orders(self):
        after = section(body(), "## After work")
        self.assertRegex(after, r"(?i)pointers?|where the repo keeps")
        self.assertRegex(after, r"(?i)not (as )?(an )?imperative|not instructions|facts, not (orders|instructions)")

    def test_the_guard_reaches_every_shipped_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            build.build("coder-agents", tmp)
            rendered = (Path(tmp) / "skills" / SKILL / "SKILL.md").read_text(encoding="utf-8")
        guard = section(body(), self.GUARD)
        self.assertIn(guard, rendered, "the build dropped the guard")
        for harness in ("coder-agents", "claude-code", "codex"):
            with self.subTest(harness=harness):
                shipped = (ROOT / "dist" / harness / "skills" / SKILL / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn(self.GUARD, shipped, "dist is stale: run ./build.py %s" % harness)
                self.assertIn(guard, shipped)


class TestShippedCopies(unittest.TestCase):
    """dist/ is committed, and a Coder workspace installs straight from it."""

    def test_every_harness_ships_the_skill_and_the_build_agrees(self):
        for harness in ("coder-agents", "claude-code", "codex"):
            with self.subTest(harness=harness):
                shipped = ROOT / "dist" / harness / "skills" / SKILL / "SKILL.md"
                self.assertTrue(shipped.is_file(), shipped)
                p = subprocess.run([sys.executable, str(ROOT / "build.py"), "--check", harness],
                                   cwd=str(ROOT), capture_output=True, text=True, timeout=120)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_the_rendered_skill_fits_the_size_coder_accepts(self):
        for harness in ("coder-agents", "claude-code", "codex"):
            with self.subTest(harness=harness):
                shipped = ROOT / "dist" / harness / "skills" / SKILL / "SKILL.md"
                self.assertLessEqual(len(shipped.read_bytes()), build.SKILL_LIMIT)

    def test_the_rendered_skill_passes_the_strict_frontmatter_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            build.build("coder-agents", tmp)
            path = Path(tmp) / "skills" / SKILL / "SKILL.md"
            text = path.read_text(encoding="utf-8")
            self.assertEqual(build.frontmatter_problems(text), [])
            self.assertIsNone(re.search(r"\{\{[a-z_]+\}\}", text))
            self.assertEqual(json.loads(build.split_frontmatter(text)[0][0].split(": ", 1)[1]), SKILL)

    def test_the_build_fails_when_this_skill_is_too_large(self):
        with tempfile.TemporaryDirectory() as out, mock.patch.object(build, "SKILL_LIMIT", 200):
            with self.assertRaises(SystemExit) as cm:
                build.build("coder-agents", out)
            self.assertIn("SKILL.md", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
