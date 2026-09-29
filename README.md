# agentic-workflow

A Claude Code agent-team workflow: product-owner, researcher, architect, software-engineer, tester, reviewer, and doc-writer, coordinated through `CLAUDE.md` and backed by a few guard-rail scripts.

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
| `dist/<harness>/` | Generated output, committed so diffs stay readable. Don't edit; run `./build.py <harness>` |
| `build.py` | Renders core through an adapter; `--check` fails if `dist/` is stale |
| `tests/run.sh` | Self-tests, including that every `dist/` is up to date (`--slow` adds the mutation-check test) |

## Install (Claude Code)

```bash
adapters/claude-code/install.sh
```

Then merge `adapters/claude-code/settings.hooks.json` into `~/.claude/settings.json`: add the `PreToolUse` entry alongside your existing ones, add the `TaskCompleted` hook, and add the allow rules. The installer doesn't edit settings.

The installer overwrites `~/.claude/CLAUDE.md` and the agent files (after backing them up). Make future edits in `core/`, rebuild, and reinstall, or the copies drift.

## Known limits

- **Teammate identity.** Claude Code passes `agent_type` to tool hooks inside subagents, but documents no equivalent for agent-team teammates, and teammates don't apply a definition's frontmatter `hooks`. So the `PreToolUse` block only fires when the agent is identifiable; `team-guard verify` in the `TaskCompleted` hook catches tampering by anyone, because sealed files are compared with the commit they were sealed at.
- **An engineer can still run `team-guard unseal`.** It's logged, and the reviewer treats any unseal without a matching tester note as a blocker.
- **Holdout isolation is by path.** Engineers are denied reads of the holdout directory where identifiable and told not to look; a `Bash` `cat` isn't intercepted.
- **gremlins v0.5.1's `--diff` mode** skips every mutant when given a package path, so `mutation-check` runs gremlins per changed package and filters survivors to changed lines itself. Cost scales with package size.
- **The TypeScript (Stryker) path is untested.** It assumes a jest setup Stryker's jest runner can discover; expect to tune it on first use in a large monorepo.
