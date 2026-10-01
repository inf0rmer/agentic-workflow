# Global Instructions

## Codebase Memory MCP

When the `codebase-memory` MCP server is available, **always use it** to enrich your understanding of the codebase before making changes:

- **Before starting work:** call `get_architecture` or `search_code` to understand relevant components and their relationships.
- **Before modifying a file:** call `get_code_snippet` to review how the code fits into the broader dependency graph.
- **After significant changes:** call `detect_changes` so the knowledge graph stays up to date.
- **When exploring unfamiliar areas:** prefer `search_graph` and `query_graph` over raw file searches — they surface architectural context that grep alone misses.

This applies to all agents (main conversation, subagents and teammates alike). If the MCP server is not connected, fall back to normal file search tools.

## Pre-commit Discipline

Before every commit, always run the project's formatter, linter, and test suite — in that order. Check the project's `{{instructions_file}}`, `Makefile`, or CI config to find the right commands. Fix any issues before committing. Never skip these checks.

## Read Before You Change

Read the project's relevant documentation before making changes. Look for design docs, architecture specs, roadmaps, and READMEs. Understand the context and intent behind existing code before modifying it.

## Agent Team Workflow

{{team_intro}}

### Recommended team structure

Match the process to the task with a workflow variant (below). Global role definitions live in `{{agents_dir}}/` and are available across all projects. The roles:

| Agent | Role | Owns | Definition |
|-------|------|------|------------|
| **product-owner** | Opens the design step with one round of questions, writes the spec with EARS acceptance criteria; roadmap and issues | Roadmap, issues, spec | `{{agents_dir}}/product-owner{{agent_ext}}` |
| **researcher** | Research requests outside the pipeline | Research notes | `{{agents_dir}}/researcher{{agent_ext}}` |
| **architect** | Options brief (when there are real options), architecture specs, ADRs and the test contract | Architecture docs, ADRs, `contract.md` | `{{agents_dir}}/architect{{agent_ext}}` |
| **software-engineer** | Scaffold and implementation | Source code | `{{agents_dir}}/software-engineer{{agent_ext}}` |
| **tester** | Sealed tests, holdout scenarios, opt-in hardening | Test files, holdout scenarios | `{{agents_dir}}/tester{{agent_ext}}` |
| **reviewer** | Code review & gap analysis | Nothing (read-only) | `{{agents_dir}}/reviewer{{agent_ext}}` |
| **doc-writer** | User-facing docs | Documentation | `{{agents_dir}}/doc-writer{{agent_ext}}` |

Projects can override these by placing project-specific versions in `{{project_agents_dir}}/`.

Skills are Matt Pocock's, MIT, pinned at one commit (the plugin is not installed and the copies never auto-update; `scripts/vendor-check` reports drift): `grilling` (chat fallback for the upfront questions), `codebase-design` and `domain-modeling` (architect), `diagnosing-bugs` (Bug fix), `tdd-inner-loop` (software-engineer only), `writing-for-agents` (editing `core/`), and `handoff` and `to-questionnaire`, which the human runs as `{{user_skill_sigil}}handoff` and `{{user_skill_sigil}}to-questionnaire`. The lead tells the human to run handoff when switching harness or resetting context, and to-questionnaire for a question only another team can answer.

### Workspaces

Every feature gets one **workspace**, at `~/.agentic-workflow/work/<feature>/` (override the root with `TEAM_WORKSPACE_ROOT`). The workspace dir holds `workspace.json`, the workflow artifacts (spec, briefs, ADRs in `adr/`, `contract.md`, review packets, `routing.md`, `questions.md`, `followups.md`, the decision log), and `repos/<repo>/`, one attached worktree per repo the feature touches, all on the same branch name. Workflow artifacts are kept in the workspace dir and **never in a project repo**; only the project's own code, tests and user-facing docs go in a repo.

- `team-guard workspace new <feature>` creates the workspace dir. Run it from anywhere.
- `team-guard workspace attach <repo-path>` adds a git worktree of that repo under `repos/`, on branch `feat/<feature>` (override with `--branch` on `new`, `--base` on `attach`). Run it from the workspace dir, or pass `--feature`.
- `team-guard workspace-path` prints the workspace dir from the workspace dir or any attached worktree, and fails elsewhere. `team-guard workspace show` lists the attached repos.
- Launch the team from the workspace dir. Seal, `team-guard list` and commit from inside an attached worktree, because seals are per repo. `team-guard verify` from the workspace dir covers every attached repo.
- A repo that isn't attached to a workspace behaves as it always did.

### Worktree-first workflow

{{worktree_section}}

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
| 2 | Security-relevant (5): flag it to the human now, since security questions stop the lead | **Full** |
| 3 | More than one repo, a public contract, persisted data, cross-team, or not revertible | **Full** |
| 4 | An open design question, or more than 15 files | **Standard** |
| 5 | A defect with a repro, 15 files or fewer, no flags | **Bug fix** |
| 6 | No behavior change a user can see, 3 files or fewer, no flags | **Chore** |
| 7 | Anything else: a new or changed behavior in one repo | **Standard** |

The lead proposes the variant with its reason, and the human confirms or overrides it in one line before any work starts. Jev does not route. A variant may be upgraded mid-run and is never downgraded. A design question moves a task up to at least Standard.

The lead records the routing decision with `team-guard routing record route`, at kickoff once the human has confirmed, and the wall time of each step with `team-guard routing record step` after each step (`upgrade` records a mid-run upgrade). Each record goes to `routing.md` in the workspace dir (outside a workspace, as for a Chore, in the git directory) and to the shared append-only log `~/.agentic-workflow/routing-log.jsonl`. `team-guard routing summary` reports override and upgrade rates and time per variant.

### Steps by variant

{{phase_intro}}

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

**Chore:** the engineer makes the change against a checklist of up to three items; one review; human code review. **Bug fix:** the lead writes a spec of one to five criteria; the tester builds the feedback loop and seals one regression test that covers them and fails on the defect, following `diagnosing-bugs`; the engineer fixes it; one fresh review (a second only if there are blockers); human gate. **Spike:** see Workflow variants.

The researcher is no longer a pipeline step: the architect absorbs the researcher's brief, and the researcher stays available for requests outside the pipeline. The doc-writer runs only when user-facing docs change. The product-owner's upfront round runs in Crit (see Upfront questions in Crit); `grilling` is the chat fallback.

After the human merges, the product-owner closes out: roadmap and issues are updated, follow-ups from `followups.md` become issues, and `team-guard workspace close <feature>` runs from outside the workspace. It archives the artifacts, removes the attached worktrees and holdouts, and keeps every branch. `team-guard gc` lists (and with `--apply` collects) stale workspaces, orphan holdouts and expired archives.

### Design gate

The default is one design gate over the spec, `contract.md` and any brief. The human discusses them, and each decision is recorded as an ADR in the workspace `adr/` directory (`adr/NNNN-title.md`). D1 (spec and brief) and D2 (contract and ADRs) run as separate reviews only when the brief has an open option and the contract depends on the choice.

Nothing is sealed until the gate passes (Bug fix and Chore have no gate). The gate passes when the human finishes the review with zero comments. The lead then records the approval in `design-gate.md` in the workspace dir, with the sha256 of each reviewed file.

If `contract.md` or another reviewed file changes afterwards, the gate reopens: `team-guard gate check <gate>` exits 2, and the lead runs the gate again before any further tests are sealed.

### Crit at a gate

In Claude Code, where Crit is installed, the lead runs the review with `team-guard gate review <gate> <files…>`, locally. Review output goes under `<workspace>/.crit`, and every Crit process runs with `CRIT_NO_UPDATE_CHECK=1`. Each comment goes to the owner of the file it is on (the spec to the product-owner; the contract, brief and ADRs to the architect), who edits the file and answers with `team-guard gate reply <gate> --file <json> --author <role>`. The reply file is a JSON array of `{"reply_to", "body"}` entries, and a reply never resolves a thread. The lead never uses Crit's `share`, `push`, `pull` or `--pr` at a gate. `--no-open` skips opening a browser, and `--new` starts a fresh review instead of continuing the current one. The gates are `design` (the single gate), or `D1` and `D2` when it is split; recording the same gate name again supersedes the earlier approval. Reply only after the human finishes a round, since a reply needs the round's Crit session alive; if it fails, re-run `team-guard gate review <gate>` first.

Approval deletes the review data. So `team-guard gate review` exports each round's comments to `<workspace>/reviews/<gate>-round<N>.json` when the round starts and again when it ends, and `reply` re-exports the round. The comments are always exported before re-running the review, so the threads survive, and earlier rounds are never overwritten.

Run `team-guard gate review` in the background with the harness's longest background timeout. Crit itself is started detached, so it survives a stopped wait. If the wait is stopped, re-run `team-guard gate review <gate>`: it continues the same session and round. After a Crit error or a shut-down daemon, run `team-guard gate review <gate> --new <files…>`. The last line it prints is `gate <gate> round <N>: approved=<true|false> comments=<K>`, with exit 0 either way.

When the review ends with `approved=true` (Crit approves only with no open comments, so the count is 0), run `team-guard gate record <gate> --crit`, which appends the reviewed files' sha256 to `design-gate.md`. Never edit that file by hand. An approved round's export may be the last copy taken before Crit deleted the review, with a `.approved.json` note beside it.

`team-guard gate check [<gate>]` compares the files with the last recorded approval: exit 0 means they still match, and exit 2 means a reviewed file changed or is missing, so the gate reopens.

### Gate fallback without Crit

Where Crit is unavailable (`team-guard gate review` exits with exit 3 and names the reply for the gate), or the harness is Codex, the lead posts numbered questions in chat about the reviewed files, using the `grilling` skill. The human answers them and the owners revise. The reply that ends the single gate is the literal `approve design`; when the gates are split, it is `approve D1` or `approve D2`. No other reply counts as approval.

Then the lead records it with `team-guard gate record <gate> <files…> --chat "<reply>"`, which checks the reply and appends the current sha256 of the files to `design-gate.md`.

### Upfront questions in Crit

The product-owner writes its upfront questions to `questionnaire.md` in the workspace dir. The lead opens it with `team-guard gate review questions <file>`. This review writes no approval record, and `team-guard gate check` does not apply to it.

When the human finishes the review, the round is answered: zero comments means every proposal is accepted, and each comment is an answer or a change. The lead hands the round's export in `reviews/` to the product-owner. At most one follow-up round is allowed, made by editing the same file; any remaining doubt goes to the design gate as an open question.

Where `gate review` exits 3 (Crit unavailable) or the harness is Codex, the lead posts the same numbered questions in chat with the `grilling` skill.

### Double-loop tests

The isolated tester seals one test per criterion at a contract seam, plus a second only for a documented error or a stated invariant. End-to-end tests are not a default deliverable; the holdouts cover that. The engineer drives each sealed test green with an inner red-green loop of unit tests it owns; those are not sealed and are not evidence of a criterion. In Full, the tester seals per slice, just in time, from the spec and that slice's part of the contract. Bug fix is one cycle: the tester writes one sealed regression test that covers the criteria and fails on the defect, and the engineer makes it pass.

When end-to-end tests are written, the tester runs them once against the live base app before sealing, to confirm the failure is "feature missing" and not a wrong selector, route or setup.

### Mutation testing

Mutation testing is off by default in every variant. In Full it is opt-in for risk-flagged modules only, and then it is sampled and time-boxed: the tester runs `mutation-check` on a sample of changed lines within a fixed time and writes a test for each survivor that matters, or a note on why it doesn't. Python has no supported mutation path. `mutation-check` bounds every run: `--min-free-gb` (default 20; exit 3 below it), `--max-concurrency` (default 2), `--time-box` (minutes, default 15; exit 4 when reached) and `--sample` (default 50 changed lines, chosen by `--seed`), and it cleans up its temp files on every exit.

### Speed rules

These apply to every variant.

- **Questions are batched.** A non-blocking question, and a Jev DEFER that doesn't block the work, goes into `questions.md` in the workspace dir while the agent continues with its own choice marked PROVISIONAL. The human answers them together at the next gate. Only blocking or security questions interrupt at once.
- **One subagent per step.** The lead starts one subagent per step and waits for its report. Agent-team teammates are used only for parallel work within one step (Codex has none; spawn parallel subagents instead).
- **Scope stays fixed.** A finding that is neither a blocker nor security goes to `followups.md`, not into the current run. Cleanups across branches become a separate Chore.
- **Warm environment.** The dev or e2e environment stays running between rounds and restarts only when its config changes.
- **Timing.** The lead records the wall time of each step in `routing.md`.
- **Unsigned commits during the run.** Agents commit unsigned with `git -c commit.gpgsign=false commit …`, a per-command setting that writes no config: it exists only in that command in the agent session environment, never in repo or global config. If a commit still asks to sign, stop and report it; never run `git config` for signing. After the human's code-review approval at the human gate, and before anything is pushed, the lead runs `team-guard sign` to re-sign every commit on the branch in every attached repo, with the human's key through their configured signer; whether the signer prompts the human depends on its approval settings. `team-guard sign` refuses a dirty or mid-rebase checkout, restores the branch if anything fails, and warns when another pre-commit hook runs on every amend. Seals check file contents, so the new commit IDs don't break them.

### How the lead runs the steps

The lead drives each step itself: it starts the next step only on the previous subagent's report, and acts on printed evidence (test output, `team-guard verify`), never on an agent's claim alone. It stops at every design gate and at the human gate, and never pushes without the human's approval. The design gate runs once per feature; in Full the review, the human gate, signing and push run per slice. Bug fix, Chore and Spike have no design gate. No scaffolding or tests before the design gate passes, and a sealed test changes only through the tester's logged unseal and reseal.

The lead stops and hands to the human when blockers remain after the re-check, a Jev `DEFER` blocks the work, a security question comes up, or a step needs a human decision. It checks in after a cap of turns per variant: Bug fix 20, Standard 40, Full 60, counted since the last human check-in; the run up to the design gate uses a cap of 20. At the cap it reports and waits. At each step it reports the latest test and `team-guard verify` output as evidence.

A slice over budget with no approved exception (approving the contract at the design gate approves a stated exception) stops the lead before signing, pushing or opening its PR, and the lead asks the human. On the literal reply `approve over budget` it runs `team-guard routing record step --step over-budget-approved --seconds 0` and continues. Any other reply sends the slice back to the architect to be split.

### Context isolation

Separation between agents only helps if their contexts stay separate. Shared context means shared blind spots.

- **Tester spawn prompt:** only the paths to the spec and `contract.md` in their approved state, and the task. Never paste design notes, implementation plans, review comments or engineer output into it. The tester never reads `.crit/` or `reviews/`. In Claude Code the `hook-pretool` hook denies the tester any path in them; on Codex the rule rests on this prompt and the reviewer.
- **Reviewer spawn prompt:** the spec, `contract.md`, and the base branch. Spawn a **new** reviewer for the re-check, passing the first report, not the engineer's explanations.
- **Holdout scenarios** live outside the repo and the workspace dir (`{{bin}}/team-guard holdout-path`). Never mention their content to the engineer.

### Guard rails

Tooling lives in `{{bin}}/`:

- `team-guard seal|unseal|verify|list` — the tester seals committed tests; `verify` fails if any sealed file's content changed since it was sealed (inside a workspace it checks every attached repo). Sealing installs git `pre-commit` and `pre-push` hooks (chaining any existing ones) that refuse commits and pushes changing sealed tests, whatever tool makes them. {{guard_hooks}}
- `team-guard refs [--base <ref>]` — exits 2 when a changed line or file name cites a workflow artifact (criterion IDs, review rounds, `contract.md`-style names). `team-guard verify` also warns when `core.hooksPath` (for example Husky) hides these git hooks.
- `mutation-check --base <branch>` — mutation-tests only changed code (Go via gremlins, TypeScript via Stryker) and lists surviving mutants on changed lines. Run it only where mutation testing is opted in.

The lead runs `team-guard verify` from the workspace dir after every subagent report, before starting the next step.

Deterministic checks (tests, `team-guard verify`, mutation results when they were run, lint) come before any LLM judgment, including Jev's and the reviewer's.

### File ownership

Agents working in parallel within one step must own non-overlapping files. If two need the same file, run them one after the other.

### Decision protocol (Jev fast lane)

Small decisions shouldn't wait on a full research pass or block a step. Any agent facing a **bounded choice between known options** uses the {{jev_skill}}:

- **`ACT`** → take the choice, record it as `Decided via Jev: <choice> — <one-line reason>` in the task notes, and continue.
- **`ESCALATE`** → follow the normal path (architect, or human), passing along Jev's leading choice and reasons.
- **`DEFER`** → a risk (public contract, persisted data, security, cross-team) is clearly present. Jev's choice is withheld so it doesn't anchor anyone. If the work can continue and you have a reversible choice, record the question in `questions.md` with Jev's reasons and proceed with that choice marked `PROVISIONAL`. With no reversible choice, or if it blocks the work or touches security, it is blocking: ask the human at once. Never proceed on a DEFER without a reversible `PROVISIONAL` choice.
- **Blocked on a missing doc?** Instead of stopping, ask Jev; on `ACT` or `ESCALATE`, proceed with its choice marked `PROVISIONAL` in your output and keep going; on `DEFER`, follow the `DEFER` rule above. The reviewer must confirm or reject every `PROVISIONAL` decision in the review.
- **Never use Jev** for routing, open-ended design, security fixes, or anything that can't be described without PII, customer or tenant data, secrets, or source code. Jev is a third-party service.
- **Jev never judges work.** Not code, tests, diffs, or whether a task is done. It gates small decisions and escalates; it is never the final arbiter, and no agent iterates until Jev approves.

`jev-decide` logs to `jev-decisions.md` in the workspace dir when it runs from the workspace dir or an attached worktree, and to the git directory outside a workspace. The reviewer reads that log as part of every review.

### Merge-risk footer

Every PR carries an advisory merge-risk footer of High, Medium or Low, with a reason in project terms when a scope or process rule applies. The lead adds it after the human approves the human-gate summary, for example:

```text
Merge risk: high (advisory) — changes the stored config format; a revert needs the old format restored.
```

The level comes from a rule table over facts about the change, checked top-down; a breaking public-contract change gives High, an additive public-contract change or a change over the review budget gives Medium; the workflow guide lists every trigger. Any unknown input counts as "yes". Review findings, unseals and skipped mutation testing can only raise the level, never lower it. The footer never names size: the reason is the one or two highest scope rules that fired, in project terms, and size alone gives `Merge risk: <level> (advisory).` with no reason. `team-guard risk` computes the level from the routing answers, git facts and the process flags `--majors-fixed`, `--holdouts-failed` and `--mutation-skipped`. The lead runs it before adding the footer, and `--packet` prints the rules and inputs for the human's review packet. Review outcomes, holdout scores and unseals stay in the human's review packet, not in the footer.

### Commit messages and PRs

Commit messages and PR descriptions must read as ordinary project changes. Don't use workflow jargon in them: no "sealed", "holdout", "Jev", or "mutation survivors". Describe what changed and why, in the project's own terms.

In Full, each slice gets its own PR, and slices go one at a time, with the human gate per slice: the slice passes its review, then the human gate for that slice. On approval the lead runs `team-guard sign --base origin/<base branch>` (the same ref the slice is rebased onto), pushes, and opens the PR against the base branch with a `Where to look` section. The human reviews and merges it; changes requested in the PR are new agent commits, re-signed with the same command before they are pushed. The next slice's PR opens only after the previous one has merged: later slices keep building locally, each on its own branch cut from the previous slice's tip, so the pushed branch only ever holds the slice under review and changes requested in its PR are committed there, with the later branch rebased onto it. Before the next slice's human gate, the lead fetches the merged base (`git fetch`) and rebases that slice onto `origin/<base branch>`. Standard, Bug fix and Chore follow this once and produce one PR; Spike has no PR. Each PR stays within about 400 changed lines to review (tests and generated code excluded; `--pr-budget N` or `TEAM_PR_BUDGET` changes it), and `team-guard risk` warns past it. The `Where to look` section is in project terms, copied from `review-guide.md`, which the reviewer of the pass that ends the review writes in the workspace dir.

### Watching the open PR

After opening a PR, the lead keeps watching it until it is merged or closed; the commands and details are in `{{docs}}/pr-loop.md`. Every 2 minutes it polls status checks, review threads (unresolved included), conversation comments, and review summaries (including changes requested). A new item is feedback only if its author is the human or a repo collaborator (association OWNER, MEMBER or COLLABORATOR). For anyone else, bots included, the lead acts only on failing checks and reports their comments to the human.

A comment is addressed only when the lead has replied on its thread and either pushed a fix or explained why not. The lead never resolves threads; the human resolves them. Done means done for now: the loop is done when all checks are green, every thread has a lead reply, and one poll brings nothing new, and the lead keeps polling until the PR is merged or closed, or a cap is reached.

The first push of a PR needs the human's approval and is signed with `team-guard sign --base origin/<base branch>`. After that, a fix for feedback or a failing check is signed with `team-guard sign --base origin/<PR branch>`, which signs only the new commits, then pushed with a plain `git push`, without asking again. The lead never force-pushes without asking the human. If a fix changes scope (new behavior, a changed contract, or a sealed test change) or touches security (authentication, authorization, secrets, crypto, input validation, or any vulnerability fix), the lead must stop and ask the human before pushing. If a check still fails after two fix attempts, or fails outside the change (flaky, infra, missing secret), the lead stops fixing and reports the check name, a log excerpt and what it tried, while it keeps watching for comments.

Waiting turns do not count toward the turn caps. Caps: in Claude Code, 4 hours of waiting and 10 fix pushes per PR; in Codex, 10 fix pushes only. At a cap the lead reports and waits for the human. Claude Code polls in the background and re-arms the poll; Codex does one check per turn and the human re-prompts.
