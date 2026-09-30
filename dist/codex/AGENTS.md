# Global Instructions

## Codebase Memory MCP

When the `codebase-memory` MCP server is available, **always use it** to enrich your understanding of the codebase before making changes:

- **Before starting work:** call `get_architecture` or `search_code` to understand relevant components and their relationships.
- **Before modifying a file:** call `get_code_snippet` to review how the code fits into the broader dependency graph.
- **After significant changes:** call `detect_changes` so the knowledge graph stays up to date.
- **When exploring unfamiliar areas:** prefer `search_graph` and `query_graph` over raw file searches — they surface architectural context that grep alone misses.

This applies to all agents (main conversation, subagents and teammates alike). If the MCP server is not connected, fall back to normal file search tools.

## Pre-commit Discipline

Before every commit, always run the project's formatter, linter, and test suite — in that order. Check the project's `AGENTS.md`, `Makefile`, or CI config to find the right commands. Fix any issues before committing. Never skip these checks.

## Read Before You Change

Read the project's relevant documentation before making changes. Look for design docs, architecture specs, roadmaps, and READMEs. Understand the context and intent behind existing code before modifying it.

## Agent Team Workflow

For significant features or changes that are more than a few lines of code, run a team of Codex subagents with a structured multi-agent approach. You are the lead: spawn each role with `spawn_agent` using its agent type, leave `fork_context` off so every role starts with only the prompt you give it, and use `wait_agent` to finish a step before starting the next.

### Recommended team structure

Match the process to the task with a workflow variant (below). Global role definitions live in `~/.codex/agents/` and are available across all projects. The roles:

| Agent | Role | Owns | Definition |
|-------|------|------|------------|
| **product-owner** | Opens the design step with one round of questions, writes the spec with EARS acceptance criteria; roadmap and issues | Roadmap, issues, spec | `~/.codex/agents/product-owner.toml` |
| **researcher** | Research requests outside the pipeline | Research notes | `~/.codex/agents/researcher.toml` |
| **architect** | Options brief (when there are real options), architecture specs, ADRs and the test contract | Architecture docs, ADRs, `contract.md` | `~/.codex/agents/architect.toml` |
| **software-engineer** | Scaffold and implementation | Source code | `~/.codex/agents/software-engineer.toml` |
| **tester** | Sealed tests, holdout scenarios, opt-in hardening | Test files, holdout scenarios | `~/.codex/agents/tester.toml` |
| **reviewer** | Code review & gap analysis | Nothing (read-only) | `~/.codex/agents/reviewer.toml` |
| **doc-writer** | User-facing docs | Documentation | `~/.codex/agents/doc-writer.toml` |

Projects can override these by placing project-specific versions in `.codex/agents/`.

### Workspaces

Every feature gets one **workspace**, at `~/.agentic-workflow/work/<feature>/` (override the root with `TEAM_WORKSPACE_ROOT`). The workspace dir holds `workspace.json`, the workflow artifacts (spec, briefs, ADRs in `adr/`, `contract.md`, review packets, `routing.md`, `questions.md`, `followups.md`, the decision log), and `repos/<repo>/`, one attached worktree per repo the feature touches, all on the same branch name. Workflow artifacts are kept in the workspace dir and **never in a project repo**; only the project's own code, tests and user-facing docs go in a repo.

- `team-guard workspace new <feature>` creates the workspace dir. Run it from anywhere.
- `team-guard workspace attach <repo-path>` adds a git worktree of that repo under `repos/`, on branch `feat/<feature>` (override with `--branch` on `new`, `--base` on `attach`). Run it from the workspace dir, or pass `--feature`.
- `team-guard workspace-path` prints the workspace dir from the workspace dir or any attached worktree, and fails elsewhere. `team-guard workspace show` lists the attached repos.
- Launch the team from the workspace dir. Seal, `team-guard list` and commit from inside an attached worktree, because seals are per repo. `team-guard verify` from the workspace dir covers every attached repo.
- A repo that isn't attached to a workspace behaves as it always did.

### Worktree-first workflow

**All agents share one workspace dir.** Before spawning any agent, run `team-guard workspace new <feature>`, then `team-guard workspace attach <repo-path>` from the workspace dir for each repo the feature touches. Each attached worktree is on the same branch name in its repo. Start Codex from the workspace dir so every subagent inherits it as its working directory; each agent edits code inside `repos/<repo>/`, and the workflow artifacts stay in the workspace dir.

### Workflow variants

Every task runs as one of five variants. The lead proposes one (next section) and the human confirms it.

| | Chore | Bug fix | Spike | Standard | Full |
|---|---|---|---|---|---|
| For | docs, bumps, renames, no behavior change | a defect with a repro, no risk flags | exploration: "can this work?" or "what's the shape of it?" | a new or changed behavior, one repo, no risk flags | several repos, public contract, persisted data, security, cross-team, hard to revert |
| Workspace | no | yes | yes | yes | yes |
| Spec / criteria | checklist of ≤3 items | lead writes it, 1–5 | none; a time box | PO, 5–20 | PO, 20–40 per slice (split past 40) |
| Design gate | – | – | – | one gate | one gate (D1 + D2 if split) |
| Isolated tester, sealed tests | – | regression test only | – | yes | yes |
| Holdouts | 0 | 0 | 0 | 1–2 | 2–4 |
| Mutation testing | off | off | off | off | off; opt-in for risk-flagged modules |
| Review rounds | 1 | 1 (2 if blockers) | 1 | 1 + fix re-check | 1 + fix re-check |
| Human code review | always | always | always | always | always |

A human reviews all code in every variant.

**Spike** is time-boxed exploration by one engineer, with no spec, no contract, no seal and no holdouts. The engineer works on a branch in a workspace and writes `findings.md`: what was tried, what worked, the shape of a real solution and the open questions. One review pass follows, then human review. The output is the branch plus `findings.md`. A Spike never merges as-is: to ship it, re-run as Standard or Full with the findings as input.

### Choosing a variant

Answer these ten questions about the task without reading any code:

1. How many repos does it touch?
2. Roughly how many files and lines change: 3 files or fewer, 4–15, or more than 15?
3. Does it change a public contract (API, CLI, file format, schema others rely on)?
4. Does it touch persisted data or migrations?
5. Does it touch security (auth, permissions, secrets, untrusted input)?
6. Is it cross-team (another team owns something it changes or consumes)?
7. Can a plain `git revert` undo it?
8. Does it change behavior a user can see? (Docs, comments, dependency bumps within semver and renames don't.)
9. Is there an open design question?
10. Is there a repro for a defect?

An unknown answer counts as "yes", so uncertainty routes to more process: read it as several repos (1), more than 15 files (2), a flag (3 to 6, 9), not revertible (7) and user-visible behavior (8). An unknown repro (10) counts as no repro. The rule table is evaluated top-down and the first match wins:

| # | Condition | Variant |
|---|---|---|
| 1 | The request is exploratory: "can this work?" or "what's the shape of it?", with no plan to ship it as is | **Spike** |
| 2 | Security-relevant (5): flag it to the human now, since security questions stop a goal | **Full** |
| 3 | More than one repo, a public contract, persisted data, cross-team, or not revertible | **Full** |
| 4 | An open design question, or more than 15 files | **Standard** |
| 5 | A defect with a repro, 15 files or fewer, no flags | **Bug fix** |
| 6 | No behavior change a user can see, 3 files or fewer, no flags | **Chore** |
| 7 | Anything else: a new or changed behavior in one repo | **Standard** |

The lead proposes the variant with its reason, and the human confirms or overrides it in one line before any work starts. Jev does not route. A variant may be upgraded mid-run and is never downgraded. A design question moves a task up to at least Standard.

The lead records the routing decision, and the wall time of each step, in `routing.md` in the workspace dir. A Chore has no workspace, so its record goes in `routing.md` in the git directory (`git rev-parse --absolute-git-dir`), like the Jev log outside a workspace; never in the repo. A shared append-only log at `~/.agentic-workflow/routing-log.jsonl` is planned; no tool writes it yet, so don't run a command for it.

### Steps by variant

Codex has no task dependencies, so you enforce step ordering as the lead: don't spawn the next step's agent until the previous one has finished and you've checked its output. Spawn agents in parallel only for parallel work within one step.

**Full (5 steps):**

1. **Design.** The product-owner opens with one upfront round of questions to the human and writes the spec (at most 40 criteria per slice). The architect then writes an options brief only if there are real design options, and then `contract.md` with a slice order.
2. **Design gate.** One review of the spec, `contract.md` and any brief; each decision becomes an ADR (see Design gate).
3. **Tests in parallel with the scaffold.** The tester writes and seals one test per criterion for each slice, plus 2–4 holdout scenarios, while the engineer scaffolds the contract as failing stubs. The tester proves each test red against the stubs.
4. **Build.** The engineer makes the sealed tests pass, using its inner red-green loop.
5. **Fresh review and re-check.** One fresh reviewer reviews the diff against the spec, then a targeted re-check covers the fix diff only. The holdouts run in the pass that ends the review: the first pass if it has no blockers or majors, otherwise the re-check.

**Standard (5 steps):**

1. **Design.** The product-owner opens with one upfront round of questions and writes the spec (5–20 criteria). The architect writes a lean contract, and an options brief only if there is an open design question.
2. **Design gate.** One review of the spec and the contract (and the brief, if there is one).
3. **Tests in parallel with the scaffold.** The tester writes and seals one test per criterion and 1–2 holdout scenarios while the engineer scaffolds the stubs.
4. **Build.** The engineer makes the sealed tests pass with its inner loop.
5. **Fresh review and re-check.** One fresh reviewer, then a targeted re-check of the fix diff only. The holdouts run in the pass that ends the review, as in Full.

Full and Standard both end at the **human gate**: the lead hands the human the change summary, the final review report, the holdout satisfaction, the decision log, the questions waiting in `questions.md`, and (for a feature spanning repos) the ship order with its reason, which the architect records in the workspace dir. Nothing is pushed without approval, and blockers that remain after the re-check go to the human.

**Chore:** the engineer makes the change against a checklist of up to three items; one review; human code review. **Bug fix:** the lead writes a spec of one to five criteria; the tester seals one regression test that covers them and fails on the defect; the engineer fixes it; one fresh review (a second only if there are blockers); human gate. **Spike:** see Workflow variants.

The researcher is no longer a pipeline step: the architect absorbs the researcher's brief, and the researcher stays available for requests outside the pipeline. The doc-writer runs only when user-facing docs change. A `grilling` interview skill for the product-owner's upfront round is planned; until it is vendored, the product-owner asks the questions directly.

After the human merges, the product-owner closes out: roadmap and issues are updated, follow-ups from `followups.md` become issues, and `team-guard workspace close <feature>` runs from outside the workspace. It archives the artifacts, removes the attached worktrees and holdouts, and keeps every branch. `team-guard gc` lists (and with `--apply` collects) stale workspaces, orphan holdouts and expired archives.

### Design gate

The default is one design gate over the spec, `contract.md` and any brief. The human discusses them, and each decision is recorded as an ADR in the workspace `adr/` directory (`adr/NNNN-title.md`). D1 (spec and brief) and D2 (contract and ADRs) run as separate reviews only when the brief has an open option and the contract depends on the choice.

Nothing is sealed until the gate passes (Bug fix and Chore have no gate). The gate passes when the human finishes the review with zero comments. The lead then writes `design-gate.md` in the workspace dir with the sha256 of each reviewed file. If `contract.md` changes afterwards, the gate reopens: the lead runs it again before any further tests are sealed.

### Crit at a gate

In Claude Code, where Crit is installed, the lead reviews at a gate with Crit, locally. Review output goes under `<workspace>/.crit`, and `CRIT_NO_UPDATE_CHECK=1` is set. Each comment goes to the owner of the file it is on (the spec to the product-owner; the contract, brief and ADRs to the architect), who edits the file and replies. The lead never uses Crit's `share`, `push`, `pull` or `--pr` at a gate.

Approval deletes the review data. So the lead exports each round's comments to `<workspace>/reviews/<gate>-round<N>.json` before re-running the review, and the threads survive. There is no wrapper or `team-guard` subcommand for Crit yet; the lead runs it directly.

### Gate fallback without Crit

Where Crit is unavailable, or the harness is Codex, the lead posts numbered questions in chat about the reviewed files. The human answers them and the owners revise. The reply that ends the single gate is the literal `approve design`; when the gates are split, it is `approve D1` or `approve D2`. No other reply counts as approval. The lead then writes `design-gate.md` as above.

### Double-loop tests

The isolated tester seals one test per criterion at a contract seam, plus a second only for a documented error or a stated invariant. End-to-end tests are not a default deliverable; the holdouts cover that. The engineer drives each sealed test green with an inner red-green loop of unit tests it owns; those are not sealed and are not evidence of a criterion. In Full, the tester seals per slice, just in time, from the spec and that slice's part of the contract. Bug fix is one cycle: the tester writes one sealed regression test that covers the criteria and fails on the defect, and the engineer makes it pass.

When end-to-end tests are written, the tester runs them once against the live base app before sealing, to confirm the failure is "feature missing" and not a wrong selector, route or setup.

### Mutation testing

Mutation testing is off by default in every variant. In Full it is opt-in for risk-flagged modules only, and then it is sampled and time-boxed: the tester runs a sample of mutants within a fixed time and writes a test for each survivor that matters, or a note on why it doesn't. Python has no supported mutation path. Hard limits in `mutation-check` (a free-disk check, capped concurrency, guaranteed cleanup, an enforced time box, sampling) are planned; until they land, the tester samples by hand (run only the risk-flagged package, or `--dry-run` and run a subset), stops at the time box, and only runs it where the disk allows.

### Speed rules

These apply to every variant.

- **Questions are batched.** A non-blocking question, and a Jev DEFER that doesn't block the work, goes into `questions.md` in the workspace dir while the agent continues with its own choice marked PROVISIONAL. The human answers them together at the next gate. Only blocking or security questions interrupt at once.
- **One subagent per step.** The lead starts one subagent per step and waits for its report. Agent-team teammates are used only for parallel work within one step (Codex has none; spawn parallel subagents instead).
- **Scope stays fixed.** A finding that is neither a blocker nor security goes to `followups.md`, not into the current run. Cleanups across branches become a separate Chore.
- **Warm environment.** The dev or e2e environment stays running between rounds and restarts only when its config changes.
- **Timing.** The lead records the wall time of each step in `routing.md`.
- **Unsigned commits during the run.** Agents commit unsigned with `git -c commit.gpgsign=false commit …`, a per-command setting that writes no config: it exists only in that command in the agent session environment, never in repo or global config. If a commit still asks to sign, stop and report it; never run `git config` for signing. At the human gate, after the human's code-review approval, every commit on the branch is re-signed, before anything is pushed, using the human's normal signing setup (usually one prompt for the whole branch). Seals check file contents, so the new commit IDs don't break them. A re-sign command is planned; until it exists this is a manual interim step. In every attached repo run `git rebase --keep-base --rebase-merges --exec 'git commit --amend --no-edit -S' <base>`, then check with `git log --show-signature <base>..`.

### Keep the loop running with `/goal`

Run the lead session under a goal so it keeps driving the steps without a prompt each time. Codex attaches the goal to the active chat as a persistent objective (`/goal` shows it; `/goal pause`, `resume`, `edit`, and `clear` manage it), and `max_goal_token_budget` in `config.toml` caps its spend. Codex's docs don't say whether it starts new turns on its own; if it waits after a turn, send `continue` or resume the goal.

Use one goal per gate, so the evaluator can't carry the lead past a review that is still pending. Each goal ends at its gate and never at a push. Paste the one that applies, filling in the feature name and adjusting the turn cap. Chore and Spike run without a goal.

**Design-gate goal (Standard, Full):**

```text
/goal Take the {feature} task to the design gate: the spec, `contract.md` and any options brief are written, and the review is with the human, each round's comments answered by the owner of the file. Stop at the design gate and wait for the human's approval: zero comments in Crit, or the literal reply `approve design` in the chat fallback; nothing else counts. Do not start tests or the scaffold. After each turn, print the step you are in. Stop and hand to the human instead if a security question comes up, a step is blocked on a human decision, or 20 turns have passed. Never push.
```

**D1 goal (split gates):**

```text
/goal Take the {feature} task to the D1 gate: the spec and the options brief are written, and the review is with the human, each round's comments answered by the owner of the file. Stop at the D1 gate and wait for the human's approval: zero comments in Crit, or the literal reply `approve D1` in the chat fallback; nothing else counts. Do not write the contract. After each turn, print the step you are in. Stop and hand to the human instead if a security question comes up, a step is blocked on a human decision, or 20 turns have passed. Never push.
```

**D2 goal (split gates):**

```text
/goal Take the {feature} task through the D2 gate: `contract.md` and the ADRs from D1 are written, and the review is with the human, each round's comments answered by the owner of the file. Stop at the D2 gate and wait for the human's approval: zero comments in Crit, or the literal reply `approve D2` in the chat fallback; nothing else counts. Do not start tests or the scaffold. After each turn, print the step you are in. Stop and hand to the human instead if a security question comes up, a step is blocked on a human decision, or 20 turns have passed. Never push.
```

**Human-gate goal (Bug fix):**

```text
/goal The {feature} fix has reached the human gate: the regression test passes, `team-guard verify` exits 0, and the last review report shows no blockers. After each turn, print the latest output of the regression test run and `team-guard verify`, and the step you are in. The sealed test may change only through the tester's logged unseal and reseal. Stop at the human gate. Stop and hand to the human instead if blockers remain after the re-check, a Jev verdict is DEFER and blocks the work, a security question comes up, a step is blocked on a human decision, or 20 turns have passed. Never push.
```

**Human-gate goal (Standard):**

```text
/goal The {feature} team has reached the human gate: the sealed tests pass, `team-guard verify` exits 0, and the report of the pass that ended the review (the first pass if it had no blockers or majors, otherwise the re-check) shows no blockers and a holdout satisfaction score. After each turn, print the latest output of the sealed test run and `team-guard verify`, and the step you are in. No sealed test may change except through the tester's logged unseal and reseal. Stop at the human gate. Stop and hand to the human instead if blockers remain after the re-check, a Jev verdict is DEFER and blocks the work, a security question comes up, a step is blocked on a human decision, or 40 turns have passed. Never push.
```

**Human-gate goal (Full):**

```text
/goal The {feature} team has reached the human gate: every slice's sealed tests pass, `team-guard verify` exits 0, any opted-in mutation results are in the report, and the report of the pass that ended the review (the first pass if it had no blockers or majors, otherwise the re-check) shows no blockers and a holdout satisfaction score. After each turn, print the latest output of the sealed test run and `team-guard verify`, and the step you are in. No sealed test may change except through the tester's logged unseal and reseal. Stop at the human gate. Stop and hand to the human instead if blockers remain after the re-check, a Jev verdict is DEFER and blocks the work, a security question comes up, a step is blocked on a human decision, or 60 turns have passed. Never push.
```

Keep the condition built on evidence the session prints — test results, exit codes, the review report — not on an agent's claim that it's done. The evaluator can only judge what appears in the conversation. The goal doesn't add a review round: after the re-check, the goal is met only by handing to the human.

### Context isolation

Separation between agents only helps if their contexts stay separate. Shared context means shared blind spots.

- **Tester spawn prompt:** only the paths to the spec and `contract.md` in their approved state, and the task. Never paste design notes, implementation plans, review comments or engineer output into it. The tester never reads `.crit/` or `reviews/`.
- **Reviewer spawn prompt:** the spec, `contract.md`, and the base branch. Spawn a **new** reviewer for the re-check, passing the first report, not the engineer's explanations.
- **Holdout scenarios** live outside the repo and the workspace dir (`~/.codex/team/bin/team-guard holdout-path`). Never mention their content to the engineer.

### Guard rails

Tooling lives in `~/.codex/team/bin/`:

- `team-guard seal|unseal|verify|list` — the tester seals committed tests; `verify` fails if any sealed file's content changed since it was sealed (inside a workspace it checks every attached repo). Sealing installs git `pre-commit` and `pre-push` hooks (chaining any existing ones) that refuse commits and pushes changing sealed tests, whatever tool makes them. A `PreToolUse` hook blocks subagents other than the tester from `apply_patch` edits to sealed files (in whichever attached repo the file lives), and a `SubagentStop` hook sends a subagent back to work while a sealed test is modified.
- `mutation-check --base <branch>` — mutation-tests only changed code (Go via gremlins, TypeScript via Stryker) and lists surviving mutants on changed lines. Run it only where mutation testing is opted in.

The lead runs `team-guard verify` from the workspace dir after every subagent report, before starting the next step.

Deterministic checks (tests, `team-guard verify`, mutation results when they were run, lint) come before any LLM judgment, including Jev's and the reviewer's.

### File ownership

Agents working in parallel within one step must own non-overlapping files. If two need the same file, run them one after the other.

### Decision protocol (Jev fast lane)

Small decisions shouldn't wait on a full research pass or block a step. Any agent facing a **bounded choice between known options** uses the `jev-decide` skill (`$jev-decide`):

- **`ACT`** → take the choice, record it as `Decided via Jev: <choice> — <one-line reason>` in the task notes, and continue.
- **`ESCALATE`** → follow the normal path (architect, or human), passing along Jev's leading choice and reasons.
- **`DEFER`** → a risk (public contract, persisted data, security, cross-team) is clearly present. Jev's choice is withheld so it doesn't anchor anyone. If the work can continue and you have a reversible choice, record the question in `questions.md` with Jev's reasons and proceed with that choice marked `PROVISIONAL`. With no reversible choice, or if it blocks the work or touches security, it is blocking: ask the human at once. Never proceed on a DEFER without a reversible `PROVISIONAL` choice.
- **Blocked on a missing doc?** Instead of stopping, ask Jev; on `ACT` or `ESCALATE`, proceed with its choice marked `PROVISIONAL` in your output and keep going; on `DEFER`, follow the `DEFER` rule above. The reviewer must confirm or reject every `PROVISIONAL` decision in the review.
- **Never use Jev** for routing, open-ended design, security fixes, or anything that can't be described without PII, customer or tenant data, secrets, or source code. Jev is a third-party service.
- **Jev never judges work.** Not code, tests, diffs, or whether a task is done. It gates small decisions and escalates; it is never the final arbiter, and no agent iterates until Jev approves.

`jev-decide` logs to `jev-decisions.md` in the workspace dir when it runs from the workspace dir or an attached worktree, and to the git directory outside a workspace. The reviewer reads that log as part of every review.

### Merge-risk footer

Every PR carries an advisory merge-risk footer of High, Medium or Low, with a reason in project terms. The lead adds it after the human approves the human-gate summary, for example:

```text
Merge risk: high (advisory) — changes the stored config format; a revert needs the old format restored.
```

The level comes from a rule table over facts about the change, checked top-down: persisted data or migrations, a public contract, several repos, auth or security, or hard to revert give High; an additive public-contract change, user-visible behavior, more than 15 files or 500 changed non-test lines, deploy, CI or config files, a major fixed in review, an unseal, skipped mutation testing on a risk-flagged module, or any holdout scenario failing (variants with no holdouts never trigger this) give Medium; none of those (docs, tests, or an internal change within limits) gives Low. Any unknown input counts as "yes". Review findings, unseals and skipped mutation testing can only raise the level, never lower it. The reason is the one or two highest rules that fired, in project terms. A merge-risk script that computes the level is planned; until it exists the lead applies the table by hand. Review outcomes, holdout scores and unseals stay in the human's review packet, not in the footer.

### Commit messages and PRs

Commit messages and PR descriptions must read as ordinary project changes. Don't use workflow jargon in them: no "sealed", "holdout", "Jev", or "mutation survivors". Describe what changed and why, in the project's own terms.
