# agentic-workflow

An agent-team workflow for Claude Code and Codex CLI: product-owner, researcher, architect, software-engineer, tester, reviewer, and doc-writer, coordinated through `CLAUDE.md` and backed by a few guard-rail scripts.

The first commit is a snapshot of the previous workflow. The workflow is written once in a harness-neutral `core/` and rendered per harness into `dist/`, so to see the whole redesign as Claude Code gets it, diff the baseline against the Claude Code build:

```bash
git diff 78427bc HEAD -M -- agents CLAUDE.md dist/claude-code
```

## What changed and why

The old workflow had the engineer and tester, on the same model and reading the same docs, write tests alongside or after the code. Their tests shared the code's blind spots. The redesign is grounded in current research on agentic software factories:

| Change | Evidence |
|---|---|
| **Tester writes acceptance/integration/e2e tests first, from the spec only** | TDFlow (2025): 94.3% of SWE-bench Verified solved with human-written tests up front vs 68.0% with self-written tests. AgentCoder: an isolated test designer beat same-context tests on accuracy and coverage. Claude Code best practices: "have one Claude write tests, then another write code to pass them." |
| **Sealed tests the engineer can't change** | ImpossibleBench (2025): Claude models mostly cheat by editing tests; read-only tests were the best mitigation, especially for Claude. |
| **Engineer stops writing "ritual" tests** | A 2026 study of six frontier models: agent-written tests didn't raise resolution rates; suppressing them cut tokens 33–49%. |
| **Small holdout set of hidden scenarios** | StrongDM's software factory keeps scenarios outside the codebase as a holdout set. Fully hiding all tests hurts legitimate performance (ImpossibleBench), so the holdout set stays small. |
| **Mutation score on changed code replaces 80% line coverage** | Meta's ACH (FSE 2025): mutation-guided test generation at scale. Mutants are an objective, LLM-free check on blind spots. |
| **EARS acceptance criteria, product owner on Sonnet** | AWS Kiro derives property-based tests from EARS criteria. The criteria are now the tests' only source of truth, so they're too important for the cheapest model. |
| **Architect's `contract.md` with a sprint contract** | Anthropic's long-running-agent harness has generator and evaluator agree on "done" and how it's checked before coding. MAST (NeurIPS 2025): most multi-agent failures are spec ambiguity and weak verification. |
| **Build ⇄ fresh-context review loop, max two rounds** | Cognition (2026): a context-free reviewer finds ~2 bugs per PR. Stripe Minions cap CI feedback at 1–2 rounds. |
| **Reviewer drops "at least one finding"** | Claude Code best practices: a reviewer asked for gaps always finds some, which drives over-engineering. |
| **Tester on Opus, engineer on Sonnet** | Errors are correlated even across models (ICML 2025); a different *and* stronger test author beats a weaker second opinion (Self-MoA, TMLR 2026). |

## Workflow

```
Phase 1  product-owner (EARS criteria) + researcher
Phase 2  architect → contract.md
           ├─ engineer: scaffold contract as failing stubs
           └─ tester (isolated): spec tests → prove red → commit → seal; holdout scenarios
Phase 3  engineer makes sealed tests pass
           → tester: mutation-check, kill or justify survivors
           → fresh reviewer (guard rails, then review) ⇄ engineer, max 2 rounds
           → final round: holdout scenarios, satisfaction score
Phase 4  human gate
Phase 5  doc-writer + product-owner
```

## Layout

| Path | What |
|---|---|
| `core/instructions.md` | Global instructions, including the team workflow and Jev decision protocol |
| `core/roles/*.md` | Role prose, harness-neutral |
| `core/team.json` | Each role's description and model tier (`strong`, `standard`, `fast`) |
| `core/bin/team-guard` | Seal/verify tests, holdout path, and hook entry points |
| `core/bin/mutation-check` | Mutation-test changed code only (Go: gremlins; TypeScript: Stryker) |
| `adapters/<harness>/adapter.json` | Tier → model mapping and values for the `{{placeholders}}` in core |
| `adapters/<harness>/install.sh` | Installs that harness's build |
| `core/skills/` | Skills shipped to every harness (`jev-decide`) |
| `dist/<harness>/` | Generated output, committed so diffs stay readable. Don't edit; run `./build.py <harness>` |
| `build.py` | Renders core through an adapter; `--check` fails if `dist/` is stale |
| `tests/run.sh` | Self-tests, including that every `dist/` is up to date (`--slow` adds the mutation-check test) |

## Install (Claude Code)

```bash
adapters/claude-code/install.sh
```

Then merge `adapters/claude-code/settings.hooks.json` into `~/.claude/settings.json`: add the `PreToolUse` entry alongside your existing ones, add the `TaskCompleted` hook, and add the allow rules. The installer doesn't edit settings.

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
| Models (strong / standard / fast) | `claude-opus-5-5` / `claude-sonnet-5` / `claude-haiku-4-5` | `gpt-6-astra` / `gpt-6-sol` / `gpt-6-luna` |
| Phase ordering | agent-team task dependencies | the lead spawns each phase with `spawn_agent` (context not forked) and `wait_agent`s before the next |
| Agent-to-agent messages | `SendMessage` | none between subagents: the engineer reports a disputed test to the lead |
| Edit guard | `PreToolUse` on `Edit`/`Write` (`file_path`) | `PreToolUse` on `apply_patch` (paths parsed from the patch) |
| Completion guard | `TaskCompleted`, exit 2 | `SubagentStop`, JSON `block` decision, once per stop |
| Tool permissions | allow rules in `settings.json` | `prefix_rule`s in `~/.codex/rules/` |
| Skills | `~/.claude/skills/` | `~/.agents/skills/` (`$jev-decide`) |

Both harnesses share the holdout directory (`~/.agentic-workflow/holdout`), so a team can mix them.

## Known limits

- **Three layers, from strongest to weakest.** (1) `team-guard verify` compares sealed files with the commit they were sealed at, so tampering is always *detected*, by any tool, at `TaskCompleted` and in review. (2) A git `pre-commit` hook, installed on first seal, *refuses* commits that change sealed tests in any harness; `--no-verify` bypasses it, which layer 1 still catches. (3) Harness hooks block edits *before* they happen, but only where the harness identifies the agent: Claude Code passes `agent_type` inside subagents, but documents no equivalent for agent-team teammates, and teammates don't apply a definition's frontmatter `hooks`.
- **Other tools can replace the git hook.** Running `lefthook install` (as Grafana's `make lefthook-install` does) overwrites it; `team-guard verify` warns when that happens, and `team-guard git-hook install` re-chains it.
- **An engineer can still run `team-guard unseal`.** It's logged, and the reviewer treats any unseal without a matching tester note as a blocker.
- **Holdout isolation is by path.** Engineers are denied reads of the holdout directory where identifiable and told not to look; a `Bash` `cat` isn't intercepted.
- **gremlins v0.5.1's `--diff` mode** skips every mutant when given a package path, so `mutation-check` runs gremlins per changed package and filters survivors to changed lines itself. Cost scales with package size.
- **Codex identifies agents only in subagents.** `agent_type` on `PreToolUse` is filled for subagents and omitted on the main thread, and it's in Codex's source and generated schemas but not yet its docs. The main thread (the lead) is covered by the git hook and `verify` only.
- **Codex has no `Read` tool to hook**, so holdout isolation there is by instruction and path only; the engineer reads files through shell commands.
- **The Codex adapter hasn't run a live team yet.** Its files parse, its rules pass `codex execpolicy check`, and its hook handlers pass tests with sample events; the first real run is the remaining check.
- **The TypeScript (Stryker) path is untested.** It assumes a jest setup Stryker's jest runner can discover; expect to tune it on first use in a large monorepo.
