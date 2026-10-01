"""Checks that the open-PR loop is safe to run unattended, and that the docs it points to are
installed where the rendered instructions say they are.

Fix pushes sign only the new commits and never rewrite what is already pushed. Only the
human's and collaborators' comments count as feedback, and a security fix waits for the
human. The loop is done only for now, its caps are stated per harness, and the PR loop guide
gives commands an agent can run as written. The instructions name the installed docs, never
a repo-relative `docs/` path, and the human's own Codebase Memory MCP text is kept verbatim.
Patterns are case-insensitive regexes.
"""

import re
import unittest

from test_workflow_rules import (ROOT, RulesCase, agent_texts, fenced_blocks, instr_texts, read,
                                 section)

H_WATCH = "### Watching the open PR"
H_PRS = "### Commit messages and PRs"
H_KICKOFF = "### Kickoff (the design step, before engineering starts)"
H_ARCH_CLI = "## Contracts that wrap an external CLI"
H_TESTER_SKIP = "### Integration tests with skip guards"
PR_LOOP = ROOT / "docs" / "pr-loop.md"
CORE_INSTR = ROOT / "core" / "instructions.md"
DOCS_DIR = {"claude-code": "~/.claude/team/docs", "codex": "~/.codex/team/docs"}

# The human's own section, kept word for word.
CODEBASE_MEMORY = (
    "## Codebase Memory MCP\n\nWhen the `codebase-memory` MCP server is available, **always use it** to enrich your "
    "understanding of the codebase before making changes:\n\n- **Before starting work:** call `get_architecture` or "
    "`search_code` to understand relevant components and their relationships.\n- **Before modifying a file:** call "
    "`get_code_snippet` to review how the code fits into the broader dependency graph.\n- **After significant "
    "changes:** call `detect_changes` so the knowledge graph stays up to date.\n- **When exploring unfamiliar "
    "areas:** prefer `search_graph` and `query_graph` over raw file searches — they surface architectural context "
    "that grep alone misses.\n\nThis applies to all agents (main conversation, subagents and teammates alike). If "
    "the MCP server is not connected, fall back to normal file search tools.\n\n")

SIGN_PR_BRANCH = r"team-guard sign --base origin/<PR branch>"
SIGN_BASE_BRANCH = r"team-guard sign --base origin/<base branch>"
PLAIN_PUSH = r"plain `?git push`?|`git push`[^.\n]{0,40}(no|without)[^.\n]{0,20}force"
NO_FORCE = r"never force-push\w*[^.\n]{0,80}without asking"
COLLABORATORS = [r"\bOWNER\b", r"\bMEMBER\b", r"\bCOLLABORATOR\b"]
SECURITY_STOPS = r"security[^\n]{0,300}?\bask\w*[^.\n]{0,60}before push"
CAPS_CLAUDE = r"Claude Code[^.\n]{0,120}4 hours[^.\n]{0,80}10 fix pushes"
CAPS_CODEX = r"Codex[^.\n]{0,120}\b10 fix pushes\b"
CODEX_NO_TIME_CAP = r"Codex[^.\n]{0,60}4 hours"
BARE_DOCS = r"(?<![\w/.~-])docs/"


def pr_loop():
    return read(PR_LOOP) if PR_LOOP.is_file() else None


class PrLoopCase(RulesCase):

    def watch(self):
        return self.instr_section(H_WATCH)

    def role(self, role, heading):
        out = {}
        for name, doc in agent_texts(role).items():
            sec = section(doc, heading)
            self.assertIsNotNone(sec, "%s has no %r heading" % (name, heading))
            out[name] = sec
        return out


class TestFixPushes(PrLoopCase):

    def test_fix_pushes_sign_against_the_pr_branch_push_plainly_and_never_force_push_unasked(self):
        texts = dict(self.watch())
        texts["docs/pr-loop"] = pr_loop()
        for name, text in texts.items():
            self.assert_has(name, text, SIGN_PR_BRANCH, PLAIN_PUSH, NO_FORCE)
        prs, watch = self.instr_section(H_PRS), self.watch()
        for name in prs:
            self.assert_has(name, prs[name], SIGN_BASE_BRANCH)
            self.assert_has(name, prs[name] + "\n" + watch[name],
                            r"first push[^.\n]{0,160}" + re.escape("origin/<base branch>") +
                            r"|" + re.escape("origin/<base branch>") + r"[^.\n]{0,160}first push")


class TestWhoseFeedbackCounts(PrLoopCase):

    def test_only_human_and_collaborator_items_are_feedback_and_security_fixes_wait_for_the_human(self):
        texts = dict(self.watch())
        texts["docs/pr-loop"] = pr_loop()
        for name, text in texts.items():
            self.assert_has(name, text, *COLLABORATORS)
            self.assert_has(name, text, r"only[^.\n]{0,80}(failing )?checks|checks only",
                            r"report\w*[^.\n]{0,80}\bhuman\b", SECURITY_STOPS)


class TestInstalledDocs(PrLoopCase):

    def test_instructions_name_the_installed_docs_and_no_repo_relative_docs_path(self):
        for name, doc in instr_texts().items():
            with self.subTest(file=name):
                self.assertIn(DOCS_DIR[name] + "/pr-loop.md", doc)
                self.assertNotIn("{{docs}}", doc)
                bare = [l.strip()[:120] for l in doc.splitlines() if re.search(BARE_DOCS, l)]
                self.assertEqual(bare, [], "%s still names a bare docs/ path" % name)
        core = read(CORE_INSTR)
        self.assertIn("{{docs}}/pr-loop.md", core)
        self.assertEqual([l.strip()[:120] for l in core.splitlines() if re.search(BARE_DOCS, l)], [])


class TestCodebaseMemoryText(PrLoopCase):

    def test_codebase_memory_section_is_kept_word_for_word(self):
        for name, doc in [("core/instructions.md", read(CORE_INSTR))] + list(instr_texts().items()):
            with self.subTest(file=name):
                start = doc.find("## Codebase Memory MCP\n")
                self.assertGreaterEqual(start, 0, "%s has no Codebase Memory MCP section" % name)
                end = doc.find("\n## ", start + 3)
                self.assertEqual(doc[start:end + 1], CODEBASE_MEMORY)


class TestLoopDetails(PrLoopCase):

    def test_done_is_for_now_and_polling_continues_until_merge_close_or_a_cap(self):
        texts = dict(self.watch())
        texts["docs/pr-loop"] = pr_loop()
        for name, text in texts.items():
            self.assert_has(name, text, r"done for now", r"keeps? polling[^.\n]{0,120}merged or closed[^.\n]{0,80}cap")

    def test_caps_are_stated_per_harness(self):
        texts = dict(self.watch())
        texts["docs/pr-loop"] = section(pr_loop() or "", "## Caps")
        for name, text in texts.items():
            self.assert_has(name, text, CAPS_CLAUDE, CAPS_CODEX)
            self.assert_lacks(name, text or "", CODEX_NO_TIME_CAP)

    def test_pr_loop_guide_gives_runnable_reply_query_exit_code_and_pagination_details(self):
        doc = pr_loop()
        self.assert_has("docs/pr-loop", doc, r"gh pr comment\b",
                        r"pulls/[^\s`]*comments/[^\s`]*/replies|addPullRequestReviewThreadReply",
                        r"gh pr checks[^\n]{0,200}\b8\b[^\n]{0,60}pending|pending[^\n]{0,60}\b8\b",
                        r"\b1\b[^\n]{0,60}fail|fail\w*[^\n]{0,60}\b1\b", r"--paginate")
        queries = [body for _, body, _, _ in fenced_blocks(doc or "")
                   if all(k in body for k in ("reviewThreads", "isResolved", "authorAssociation"))]
        self.assertTrue(queries, "docs/pr-loop.md has a fenced GraphQL query with reviewThreads, isResolved "
                                 "and authorAssociation")
        for q in queries[:1]:
            self.assertRegex(q, r"gh api graphql|query\s*[({]")
            self.assertEqual(q.count("{"), q.count("}"), "the reviewThreads query has balanced braces")

    def test_the_human_resolves_threads(self):
        texts = dict(self.watch())
        texts["docs/pr-loop"] = pr_loop()
        for name, text in texts.items():
            self.assert_has(name, text, r"the human resolves")
            self.assert_lacks(name, text or "", r"the reviewer resolves")

    def test_architect_names_the_smoke_test_and_the_tester_writes_it(self):
        for name, sec in self.role("architect", H_ARCH_CLI).items():
            self.assert_has(name, sec, r"\bnames?\b[^.\n]{0,60}smoke test", r"tester writes")
        for name, sec in self.role("tester", H_TESTER_SKIP).items():
            self.assert_has(name, sec, r"\bwrites?\b[^.\n]{0,60}smoke test|smoke test[^.\n]{0,60}\bwrites?\b")

    def test_product_owner_questions_paragraph_covers_codex(self):
        for name, sec in self.role("product-owner", H_KICKOFF).items():
            paras = [p for p in re.split(r"\n\s*\n", sec) if "questionnaire.md" in p]
            self.assertTrue(paras, "%s: no Kickoff paragraph names the questions file" % name)
            self.assert_has(name, paras[0] if paras else "", r"Codex", r"grilling")


if __name__ == "__main__":
    unittest.main()
