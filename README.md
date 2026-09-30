# agentic-workflow

An agent-team workflow for Claude Code and Codex CLI: product-owner, researcher, architect, software-engineer, tester, reviewer, and doc-writer, coordinated through `CLAUDE.md` and backed by a few guard-rail scripts.

The first commit is a snapshot of the previous workflow. The workflow is written once in a harness-neutral `core/` and rendered per harness into `dist/`, so to see the whole redesign as Claude Code gets it, diff the baseline against the Claude Code build:

```bash
git diff 78427bc HEAD -M -- agents CLAUDE.md dist/claude-code
```

## Documentation

Guides live in [docs/](./docs/README.md):

- [Workflow](./docs/workflow.md): the five variants, their steps, the design gate, the review, and the human gate.
- [Workspaces](./docs/workspaces.md): set up, close, and collect per-feature workspaces.
- [Guard rails](./docs/guard-rails.md): sealed tests, the three enforcement layers, and mutation testing.
- [Decisions with Jev](./docs/decisions-with-jev.md): when to ask Jev, and what each verdict means.
- [Harnesses](./docs/harnesses.md): how `build.py` renders and installs the workflow for Claude Code and Codex.

## What changed and why

The old workflow had the engineer and tester, on the same model and reading the same docs, write tests alongside or after the code. Their tests shared the code's blind spots. The redesign is grounded in current research on agentic software factories:

| Change | Evidence |
|---|---|
| **Tester writes one sealed test per criterion first, from the spec only** | TDFlow (2025): 94.3% of SWE-bench Verified solved with human-written tests up front vs 68.0% with self-written tests. AgentCoder: an isolated test designer beat same-context tests on accuracy and coverage. Claude Code best practices: "have one Claude write tests, then another write code to pass them." |
| **Sealed tests the engineer can't change** | ImpossibleBench (2025): Claude models mostly cheat by editing tests; read-only tests were the best mitigation, especially for Claude. |
| **Engineer's tests become an inner loop under one sealed test per criterion** | The engineer drives each sealed test green with unit tests it owns; they aren't sealed and don't count as evidence. A 2026 study of six frontier models: agent-written tests didn't raise resolution rates; suppressing them cut tokens 33–49%. |
| **Small holdout set of hidden scenarios** | StrongDM's software factory keeps scenarios outside the codebase as a holdout set. Fully hiding all tests hurts legitimate performance (ImpossibleBench), so the holdout set stays small. |
| **Sampled mutation testing, opt-in for risk-flagged modules, replaces 80% line coverage** | Meta's ACH (FSE 2025): mutation-guided test generation at scale. Mutants are an objective, LLM-free check on blind spots. It is off by default because full runs on everything took hours and filled disks. |
| **EARS acceptance criteria, product owner on Sonnet** | AWS Kiro derives property-based tests from EARS criteria. The criteria are now the tests' only source of truth, so they're too important for the cheapest model. |
| **Architect's `contract.md` with a sprint contract** | Anthropic's long-running-agent harness has generator and evaluator agree on "done" and how it's checked before coding. MAST (NeurIPS 2025): most multi-agent failures are spec ambiguity and weak verification. |
| **One fresh-context review plus a targeted re-check of the fix diff** | Cognition (2026): a context-free reviewer finds ~2 bugs per PR. Stripe Minions cap CI feedback at 1–2 rounds. |
| **Reviewer drops "at least one finding"** | Claude Code best practices: a reviewer asked for gaps always finds some, which drives over-engineering. |
| **Tester on Opus, engineer on Sonnet** | Errors are correlated even across models (ICML 2025); a different *and* stronger test author beats a weaker second opinion (Self-MoA, TMLR 2026). |

## Workflow

```
Variants  Chore | Bug fix | Spike | Standard | Full  (the lead proposes, you confirm)
Step 1    design: product-owner (upfront questions, EARS criteria), then architect (options brief if needed, contract.md)
Step 2    design gate: one Crit review, zero comments to pass; nothing is sealed before it
Step 3    tests in parallel with the scaffold
           ├─ engineer: scaffold contract as failing stubs
           └─ tester (isolated): one sealed test per criterion → prove red → seal; holdout scenarios
Step 4    engineer makes sealed tests pass (inner red-green loop)
Step 5    one fresh reviewer (guard rails, then review) + targeted re-check of the fix diff; holdout satisfaction
Human gate  review, questions, re-sign the branch, merge-risk footer
After     doc-writer (only if user-facing docs changed) + product-owner close-out
```

## Vendored skills

These are [Matt Pocock's skills](https://github.com/mattpocock/skills), MIT licensed, copied into `core/skills/` and pinned at one commit recorded in `core/skills/UPSTREAM.json`.
The plugin is not installed, and the copies never auto-update, so prompts that run commands can't change under you.
Every copy carries its `LICENSE` and a provenance line naming the source, the commit and whether it was modified.
A patched skill is upstream plus a stored patch in `core/skills/patches/`, which keeps workflow files in the workspace dir and never in a project repo.

| Skill | Status | Used by |
|---|---|---|
| `grilling` | as-is | product-owner, for the upfront round of questions |
| `codebase-design` | as-is | architect, for an options brief |
| `writing-for-agents` | as-is | whoever edits `core/` |
| `domain-modeling` | patched | architect: ADRs as `adr/NNNN-<slug>.md` and an optional `GLOSSARY.md`, both in the workspace dir |
| `handoff` | patched | the human, as a slash command; writes `handoff-<slug>.md` in the workspace dir |
| `to-questionnaire` | patched | the human, as a slash command; writes `to-questionnaire-<slug>.md` in the workspace dir |
| `diagnosing-bugs` | patched | Bug fix: the tester builds the feedback loop and the regression test, the engineer fixes |
| `tdd-inner-loop` | patched (upstream `tdd`) | software-engineer only |

The Skill denial for `tdd-inner-loop` is enforced on Claude Code where the agent is identifiable (the `hook-pretool` matcher includes `Skill`); it is not enforced on Codex, which has no Skill hook, so there the rule rests on the role prose and `allow_implicit_invocation: false`.

Run `scripts/vendor-check` to see drift: it compares each copy with upstream at the pin plus its patch (`local drift`) and with a newer ref (`upstream changed`, `upstream removed`, or a `patch conflict`), exits 1 on drift and 2 when it can't run, and never changes anything.
Moving the pin is a person's decision: update the copies and patches, then the pin.

The byte-for-byte checks need a local clone of upstream at the pin. Run the suite with `VENDOR_UPSTREAM_CLONE=<path to a clone of mattpocock/skills at the pin> tests/run.sh`; without it those tests skip with that reason, and nothing touches the network.

## Workspaces

Workflow artifacts (spec, ADRs, `contract.md`, review reports, the Jev log) never land in a project repo. Each feature gets a **workspace** instead, and a feature can span several repos:

```
~/.agentic-workflow/work/<feature>/     workspace dir (TEAM_WORKSPACE_ROOT)
  workspace.json                        feature, branch, attached repos
  jev-decisions.md                      Jev decision log
  spec.md, contract.md, ...             workflow artifacts
  repos/<repo>/                         one git worktree per attached repo, all on feat/<feature>
~/.agentic-workflow/holdout/<feature>/  holdout scenarios (TEAM_HOLDOUT_ROOT), outside the workspace dir
~/.agentic-workflow/archive/<feature>/  what `workspace close` keeps (TEAM_ARCHIVE_ROOT)
```

```bash
team-guard workspace new payments                 # from anywhere
cd ~/.agentic-workflow/work/payments
team-guard workspace attach ~/code/api            # worktree at repos/api on feat/payments, from the default branch
team-guard workspace attach ~/code/web --base develop
team-guard workspace show                         # repos, source paths, worktrees, bases
team-guard workspace-path                         # the workspace dir, from it or any attached worktree
```

Start the team from the workspace dir. Each attached worktree points back at its workspace through a file in its git admin dir (`team-workspace`); nothing is written to a source repo's config, working tree or tracked files. A repo that isn't attached to a workspace behaves exactly as before.

- **Seals are per repo** and record the file's content hash as well as the commit, so a rebase or squash of the sealed commit doesn't break `verify`. Older locks without hashes keep working against their commit.
- **`team-guard verify`, and the task and stop hooks, check every attached repo** when run inside a workspace, and name the repo in each violation. `hook-pretool` finds the repo from each edited file's path, not from the working directory.
- **Sealing installs `pre-commit` and `pre-push` hooks.** `pre-push` refuses a push while the repo's sealed files differ from their seals, and chains any hook that was already there.
- **`team-guard workspace list`** prints each workspace's age, size and status: `merged` (every attached branch merged into its base, or deleted), `stale` (idle for `TEAM_STALE_DAYS`, default 30, or a source repo is missing) or `active`.
- **`team-guard workspace close <feature>`**, run from outside the workspace after the human merges, archives the artifacts (and each repo's seal lock) under the archive root, then removes the attached worktrees and the holdouts. Branches are never deleted. It refuses while a worktree has uncommitted changes or commits on no remote-tracking branch; `--force` discards them.
- **`team-guard gc`** lists stale workspaces, orphaned holdouts, expired archives and legacy holdouts idle longer than `TEAM_ARCHIVE_RETENTION_DAYS` (default 180). It changes nothing until you add `--apply`, which closes workspaces without `--force`. `workspace new` prints a hint when something is stale. Nothing runs in the background.

| Variable | Default |
|---|---|
| `TEAM_WORKSPACE_ROOT` | `~/.agentic-workflow/work` |
| `TEAM_HOLDOUT_ROOT` | `~/.agentic-workflow/holdout` |
| `TEAM_ARCHIVE_ROOT` | `~/.agentic-workflow/archive` |
| `TEAM_STALE_DAYS` | `30` |
| `TEAM_ARCHIVE_RETENTION_DAYS` | `180` |

## Layout

| Path | What |
|---|---|
| `core/instructions.md` | Global instructions, including the team workflow and Jev decision protocol |
| `core/roles/*.md` | Role prose, harness-neutral |
| `core/team.json` | Each role's description and model tier (`strong`, `standard`, `fast`) |
| `core/bin/team-guard` | Seal/verify tests, workspaces, holdout path, `gc`, hook entry points, `refs`, `routing record` / `routing summary`, `risk`, `sign` and `team-guard gate review` / `reply` / `record` / `check` for design gates (see [Guard rails](./docs/guard-rails.md)) |
| `core/bin/mutation-check` | Mutation-test changed code only (Go: gremlins; TypeScript: Stryker), bounded by `--min-free-gb` (default 20, exit 3), `--max-concurrency` (2), `--time-box` (15 minutes, exit 4) and `--sample` (50 changed lines) |
| `adapters/<harness>/adapter.json` | Tier → model mapping and values for the `{{placeholders}}` in core |
| `adapters/<harness>/install.sh` | Installs that harness's build |
| `core/skills/` | Skills shipped to every harness: `jev-decide` and the vendored set, with `UPSTREAM.json` and `patches/` (not shipped) |
| `scripts/vendor-check` | Report drift of the vendored skills from upstream |
| `dist/<harness>/` | Generated output, committed so diffs stay readable. Don't edit; run `./build.py <harness>` |
| `build.py` | Renders core through an adapter; `--check` fails if `dist/` is stale |
| `tests/run.sh` | Self-tests, including that every `dist/` is up to date (`--slow` adds the mutation-check test) |

## Install (Claude Code)

```bash
adapters/claude-code/install.sh
```

Then merge `adapters/claude-code/settings.hooks.json` into `~/.claude/settings.json`: add the `PreToolUse` entry alongside your existing ones, add the `SubagentStop` and `TaskCompleted` hooks, and add the allow rules. The installer doesn't edit settings.

The installer overwrites `~/.claude/CLAUDE.md` and the agent files (after backing them up). Make future edits in `core/`, rebuild, and reinstall, or the copies drift.

## Install (Codex CLI)

```bash
adapters/codex/install.sh
```

It installs `~/.codex/AGENTS.md`, one `~/.codex/agents/<role>.toml` per role, the tools in `~/.codex/team/bin/`, a rules file at `~/.codex/rules/agentic-workflow.rules` that lets those tools run outside the sandbox, and the skills in `~/.agents/skills/`. Then:

1. Merge `adapters/codex/hooks.json` into `~/.codex/hooks.json`, keeping your existing hooks. Hooks need `[features] hooks = true` in `config.toml`.
2. Run `/hooks` in Codex and trust the two `team-guard` hooks. Codex doesn't run untrusted hooks.

### How the harnesses differ

| | Claude Code | Codex CLI |
|---|---|---|
| Instructions | `~/.claude/CLAUDE.md` | `~/.codex/AGENTS.md` (no `@imports`; 32 KiB cap together with project `AGENTS.md` files) |
| Roles | `agents/*.md`, frontmatter + prose | `agents/*.toml`, `developer_instructions` + `model` + `model_reasoning_effort` |
| Models (strong / standard / fast) | `claude-opus-5-5` / `claude-sonnet-5-5` / `claude-haiku-4-5` | `gpt-6-astra` / `gpt-6-sol` / `gpt-6-luna` |
| Step ordering | the lead starts one subagent per step and waits for its report | the lead spawns each step with `spawn_agent` (context not forked) and `wait_agent`s before the next |
| Agent-to-agent messages | `SendMessage` | none between subagents: the engineer reports a disputed test to the lead |
| Edit guard | `PreToolUse` on `Edit`/`Write` (`file_path`) | `PreToolUse` on `apply_patch` (paths parsed from the patch) |
| Completion guard | `SubagentStop` (JSON `block` decision, once per stop) and `TaskCompleted` (exit 2, agent teams) | `SubagentStop`, JSON `block` decision, once per stop |
| Tool permissions | allow rules in `settings.json` | `prefix_rule`s in `~/.codex/rules/` |
| Skills | `~/.claude/skills/` | `~/.agents/skills/` (`$jev-decide`) |

Both harnesses share the workspace, holdout and archive directories under `~/.agentic-workflow/`, so a team can mix them.

## Known limits

- **Three layers, from strongest to weakest.** (1) `team-guard verify` compares sealed files with the content hash recorded when they were sealed, so tampering is always *detected*, by any tool, at `SubagentStop`, `TaskCompleted` and in review. (2) Git `pre-commit` and `pre-push` hooks, installed on first seal, *refuse* commits and pushes that change sealed tests in any harness; `--no-verify` bypasses them, which layer 1 still catches. (3) Harness hooks block edits *before* they happen, but only where the harness identifies the agent: Claude Code passes `agent_type` inside subagents, but documents no equivalent for agent-team teammates, and teammates don't apply a definition's frontmatter `hooks`.
- **The global Codex `AGENTS.md` is large.** At about 30.6 KB it leaves about 1.4 KB of Codex's 32 KiB cap for project `AGENTS.md` files.
- **Other tools can replace the git hook.** Running `lefthook install` (as Grafana's `make lefthook-install` does) overwrites it; `team-guard verify` warns when that happens, and `team-guard git-hook install` re-chains it.
- **The tester's review-data denial has gaps.** `hook-pretool` denies a tester any path tool call into a workspace's `.crit/` and `reviews/`, but Codex's hook matcher doesn't cover reads and shell reads aren't covered in either harness, so there the rule rests on the spawn prompt and the reviewer.
- **`core.hooksPath` hides the git hooks.** Husky and similar tools set it, and git then ignores `.git/hooks`. `team-guard verify` warns, but `team-guard git-hook install` would write into the tool's directory, so it isn't done for you; `verify` and the reviewer are the remaining checks.
- **Workspaces are per feature and local.** `team-guard` doesn't push, open PRs or enforce a ship order across repos; the order is recorded as a decision only. It isn't safe to run several `team-guard` commands against one workspace at once, and a repo can't be detached from a live workspace.
- **Squash merges aren't detected.** A squash-merged branch isn't an ancestor of its base, so the workspace stays `active` until it goes stale; `workspace close` is the primary cleanup and `gc` the backstop.
- **`pre-push` checks the working tree, not the pushed commits**, like `verify`. The git hooks live in the source repo's shared hooks dir and stay after `close`; they do nothing without a lock.
- **Closing loses no audit trail**, but a worktree's seal lock lives in its git dir and is copied to the archive before the worktree is removed. Legacy holdouts (`<repo>/<branch>`) are collected by age, not by whether their repo still exists.
- **Pushing from the source checkout skips the check.** The lock lives in the attached worktree's git dir, so pushing the workflow branch from the source checkout, not the worktree, skips the worktree's `pre-push` check.
- **A workspace root nested inside another repo can hide a missing `.git` file.** If an attached worktree loses its `.git` file, git finds the enclosing repo, and `verify` may report clean instead of `repository unavailable`.
- **Ignored files go with the worktree.** `workspace close` lists ignored files (such as a local `.env`) as `deleting ignored:` and then deletes them; only uncommitted changes and unpushed commits block a close.
- **An engineer can still run `team-guard unseal`.** It's logged, and the reviewer treats any unseal without a matching tester note as a blocker.
- **Holdout isolation is by path.** Engineers are denied reads of the holdout directory where identifiable and told not to look; a `Bash` `cat` isn't intercepted.
- **gremlins v0.5.1's `--diff` mode** skips every mutant when given a package path, so `mutation-check` runs gremlins per changed package and filters survivors to changed lines itself. Cost scales with package size.
- **Codex identifies agents only in subagents.** `agent_type` on `PreToolUse` is filled for subagents and omitted on the main thread, and it's in Codex's source and generated schemas but not yet its docs. The main thread (the lead) is covered by the git hook and `verify` only.
- **Codex has no `Read` tool to hook**, so holdout isolation there is by instruction and path only; the engineer reads files through shell commands.
- **The Codex adapter hasn't run a live team yet.** Its files parse, its rules pass `codex execpolicy check`, and its hook handlers pass tests with sample events; the first real run is the remaining check.
- **The TypeScript (Stryker) path is untested.** It assumes a jest setup Stryker's jest runner can discover; expect to tune it on first use in a large monorepo.
