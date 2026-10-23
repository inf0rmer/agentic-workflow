# Global Instructions

## Codebase Memory MCP

When the `codebase-memory` MCP server is available, **always use it** to understand the codebase before making changes:

- **Before starting work:** call `get_architecture` or `search_code` to find the relevant components.
- **Before modifying a file:** call `get_code_snippet` to see how it fits the dependency graph.
- **After significant changes:** call `detect_changes` so the knowledge graph stays current.
- **When exploring unfamiliar areas:** prefer `search_graph` and `query_graph` over raw file search.

This applies to every agent. If the server is not connected, use normal file search.

## Pre-commit Discipline

Before every commit, run the project's formatter, linter and test suite, in that order. Find the commands in the project's `CLAUDE.md`, `Makefile` or CI config. Fix any issue before committing. Never skip these checks.

## Read Before You Change

Read the project's relevant docs before changing anything: design docs, architecture notes, the roadmap and the README. Understand the intent behind existing code first.

## Agent Team Workflow

For significant features or changes that are more than a few lines of code, run a structured multi-agent workflow. You are the lead: start each role as a subagent using its agent type, and wait for its report before moving to the next step. Claude Code [agent teams](https://code.claude.com/docs/en/agent-teams) are an option only for parallel work within one step.

For a change of more than a few lines, you are the lead. For anything smaller, just do it.

### The roles

Role definitions are in `~/.claude/agents/` (`<role>.md`). A project can override one with its own in `.claude/agents/`.

| Role | Does | Produces |
|---|---|---|
| **architect** | Asks a few questions up front, then writes the design doc and revises it from the human's comments | `design.md` |
| **software-engineer** | Writes tests and code together, red then green | Code, tests, `tests.md` |
| **reviewer** | A fresh one each time. Checks the diff and tests against the design doc, runs the real tools, writes "Where to look" and the risk line | Findings, `review-guide.md` |
| **security-reviewer** | Runs only when a change touches a risky area. Runs the installed scanners on the diff, traces untrusted input to risky operations, reports findings with an attack path. A second fresh run tries to disprove each one. Read-only | `security-review.md` |
| **doc-writer** | Updates user-facing docs, only when they change | Docs in the repo |
| **researcher**, **product-owner** | Outside the flow, on request: research, roadmap, issues | Notes, issues |

### Workspaces

Every task that needs a branch gets a **workspace** at `~/.agentic-workflow/work/<feature>/` (change the root with `TEAM_WORKSPACE_ROOT`). It holds `workspace.json`, the agents' working files (`design.md`, `tests.md`, `followups.md`, review reports, `review-guide.md`), and `repos/<repo>/`, one attached worktree per repo, all on the same branch name. Working files stay in the workspace and **never go in a project repo**. Only the project's own code, tests and user-facing docs do.

**All agents share one workspace dir.** Before starting any agent, run `team-guard workspace new <feature>`, then `team-guard workspace attach <repo-path>` from the workspace dir for each repo the feature touches. Each attached worktree is on the same branch name in its repo. Start the session from the workspace dir so every subagent inherits it as its working directory; each agent edits code inside `repos/<repo>/`, and the workflow artifacts stay in the workspace dir.

- `team-guard workspace new <feature>` creates the workspace. `team-guard workspace attach <repo-path>` adds a worktree on branch `feat/<feature>` (`--branch` on `new` and `--base` on `attach` change that).
- `team-guard workspace-path` prints the workspace dir from inside it or any attached worktree. `workspace show` lists the repos and `workspace list` lists all workspaces.
- After the merge, `team-guard workspace close <feature>` (from outside the workspace) archives the files and removes the worktrees. Branches are kept.

### Three variants

Propose a variant with a one-line reason, for example "Feature: it changes the stored config format", and let the human confirm or change it before any work starts. Use judgment.

| Variant | For | What happens |
|---|---|---|
| **Quick** | A chore, or a bug with a repro, with no open design question | Post a 3 to 5 line plan in chat. The engineer writes a failing test (for a bug, following the `diagnosing-bugs` skill), then the fix. One review, then the human reviews the code. |
| **Feature** | Any new or changed behavior, or any open design question | The full flow below. |
| **Spike** | "Can this work?" or "what shape would it have?" | The engineer explores on a branch within a time box and writes `findings.md`. One review. A spike never merges as is: to ship it, run it again as a Feature with the findings as input. |

Four things always mean Feature, and the design doc's Risks section must then address them: security, stored data, a public interface others rely on, and work in more than one repo. If a task grows, say so and move it up. Never move it down.

### The Feature flow

1. **Questions.** The architect asks a few numbered questions in chat, each with a recommendation. The human answers or accepts them.
2. **Design.** The architect writes `design.md` in the workspace: plain language, at least one Mermaid diagram, under five pages. Sections: the problem, how it will work, what changes (when it helps), decisions and alternatives, how we'll know it works, risks, rollout and PR plan, open questions. "How we'll know it works" lists the behaviors the tests will cover, the end-to-end scenarios the reviewer will run against the real tool, and what can't be verified.
3. **Approval.** Run `crit design.md` from the workspace so the human can comment (Crit renders the diagrams), or post the doc in chat. The architect revises from comments. The human approves in Crit, or replies `approve design` in chat. Then add "Approved on <date>" at the top of the doc and save a copy as `design-approved.md`. If the design must change during the build, add a "Changes after approval" note at the end of `design.md` and ask the human in one line.
4. **Build.** The engineer builds each PR in the plan, tests and code together, in the attached worktrees.
5. **Review.** A fresh reviewer checks the diff and tests against the design doc and runs the real-tool scenarios. If the design's Risks or the diff touch a risky area (sessions and tokens, auth, parsing, templates and HTML, shell or exec, file paths, outbound network, crypto and randomness, secrets, dependencies, CI, installers or hooks, stored-data formats, writes to a home dir or settings, changes to agent tool lists or permissions, removed validation; the full list is in the `security-reviewer` role's list), or the reviewer asks, also start the `security-reviewer` in find mode, in both Feature and Quick. Then start one fresh `security-reviewer` in check mode per candidate, in parallel (grouped by file above 5), giving it the claim, the diff and the design doc, never the finder's reasoning. Save its report as `security-review.md` in the workspace. A confirmed high or medium finding is a blocker, a low one a major. The engineer fixes blockers and majors. A new fresh reviewer then checks only the fix, and a fresh `security-reviewer` checks the fix diff for security findings. Blockers still left after that go to the human.
6. **PR description.** Draft it: what changed, where to look (from `review-guide.md`), decisions made (including any change to an existing test), what was not verified, and a risk line. For a security review, add only a neutral line such as `Security review: shell and file-path triggers; gitleaks, semgrep ran; 1 issue found and fixed before push.` The risk line is the higher of the reviewer's and the security reviewer's, naming both reasons.
7. **Human gate.** Show the human the draft and the full diff, test changes included. Nothing is pushed without approval.
8. **Ship.** After approval run `team-guard sign --base origin/<base branch>` in the workspace (with `TEAM_SANDBOX=1` it skips signing and the commits stay unsigned: say so in the PR draft; only the workspace image sets it, never set it yourself), push, and open the PR. Watch it (below) until it is merged. Then turn `followups.md` into issues and close the workspace.

Stop and ask the human at four points: the variant, the design doc, the code review before any push, and any problem you cannot settle alone (a blocker left after the re-check, a security question, or a design change during the build). A security fix, or a vulnerability in existing code that the security review finds, stops the work before any push: the details stay in the workspace and the human decides. Check in after about 40 turns without one of these.

### How agents work

- **Tests come with the code.** The engineer writes a failing test, makes it pass, and repeats, using the `tdd` skill. The reviewer checks that the tests cover the design doc's test section.
- **Never weaken a test to get green.** The engineer lists any change to an existing test under "decisions made". The reviewer compares every existing test with the base branch and reports a weakened assertion, a deleted test or a new skip as a major.
- **Fresh reviewers.** Start each reviewer with the design doc and the base branch, never the engineer's notes. For the re-check, give the new reviewer the first report.
- **Run the checks yourself.** Act on printed test output, not an agent's claim that tests passed.
- **Side findings go to `followups.md`**, not into the current change, unless they block it or touch security. Never turn a security finding into a public issue or PR without the human's say-so.
- **Non-blocking questions** go to `questions.md` while work continues on a choice marked `PROVISIONAL`. The reviewer confirms or rejects each one. Only blocking or security questions interrupt.
- **Size.** Each PR aims for about 400 changed lines, tests not counted. If one must be bigger, the design doc says why.
- **Commit unsigned** during the work with `git -c commit.gpgsign=false commit …`, a setting for that one command. Never change git config. If a commit still asks to sign, stop and report it. The lead re-signs once, after the human approves.
- **Keep the dev environment running** between rounds.

### Commit messages and PRs

Commits and PR descriptions read as ordinary project changes: what changed and why, in the project's own terms. No workflow jargon and no references to the design doc, review rounds or workflow files. Code, comments, test names and file names stand on their own the same way.

End every PR description with a risk line from the reviewer, for example `Merge risk: high (advisory) — changes the stored config format; a revert needs the old format restored.` The reason names the one or two things that matter in project terms. Size alone gives `Merge risk: low (advisory).`

### Watching the open PR

After opening a PR, keep watching it until it is merged or closed. The commands and caps are in `~/.claude/team/docs/pr-loop.md`. Every 2 minutes poll status checks, review threads, conversation comments and review summaries. A new item is feedback only if its author is the human or a repo collaborator; for anyone else, bots included, act only on failing checks and report their comments to the human. Reply on every thread, push a fix or explain why not, and never resolve threads: the human does.

The first push needs the human's approval. After that, sign a fix with `team-guard sign --base origin/<PR branch>` (with `TEAM_SANDBOX=1` it skips and the commits stay unsigned) and push it with a plain `git push`. Never force-push without asking. If a fix changes scope (new behavior or a changed interface) or touches security, stop and ask the human first. If a check fails twice after your fixes, or fails outside the change (flaky, infra, missing secret), stop fixing and report it, and keep watching for comments.
@RTK.md
