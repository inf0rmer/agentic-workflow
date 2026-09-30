# Harnesses

The workflow is written once in `core/` and rendered for each agent harness, Claude Code and Codex CLI, by `build.py` through an adapter.

```mermaid
flowchart LR
    C["core/<br/>instructions, roles,<br/>team.json, bin, skills"] --> B["build.py HARNESS"]
    A["adapters/HARNESS/adapter.json<br/>models and placeholder values"] --> B
    B --> D["dist/HARNESS/<br/>committed build output"]
    D --> I["adapters/HARNESS/install.sh"]
    I --> H["Harness home<br/>~/.claude or ~/.codex"]
```

## Know what each part supplies

| Part | Supplies |
|---|---|
| `core/instructions.md`, `core/roles/*.md` | The workflow and role prose, with `{{placeholders}}` wherever the harnesses differ. |
| `core/team.json` | Each role's description and model tier: `strong`, `standard`, or `fast`. |
| `core/bin/`, `core/skills/` | `team-guard`, `mutation-check`, and the `jev-decide` skill, shared by every harness. |
| `adapters/<harness>/adapter.json` | The `harness` type, the model for each tier, and a value for every placeholder. |

| Tier | Claude Code | Codex CLI |
|---|---|---|
| `strong` | `claude-opus-5-5` | `gpt-6-astra` |
| `standard` | `claude-sonnet-5-5` | `gpt-6-sol` |
| `fast` | `claude-haiku-4-5-20251001` | `gpt-6-luna` |

The Codex adapter also sets a reasoning effort per tier (`efforts`).

## Build

Build one harness per run, from the repo root:

```bash
./build.py claude-code
./build.py codex
./build.py --check claude-code   # exit 1 if dist/claude-code is stale
```

`build.py`:

- Renders `core/instructions.md` into `CLAUDE.md` or `AGENTS.md`, and appends the adapter's optional `instructions_footer`.
- Renders each role from its prose and its `team.json` entry.
- Renders each skill's `SKILL.md`, and copies the skill's other files and `core/bin/` unchanged.
- Fails if a file uses a placeholder the adapter doesn't define.

Role prose in `core/roles/` has no frontmatter; the build generates it from `team.json`:

| Harness | Role file | Generated from `team.json` and the adapter |
|---|---|---|
| Claude Code | `dist/claude-code/agents/<role>.md` | YAML frontmatter with `name`, `model`, and `description`, followed by the prose |
| Codex CLI | `dist/codex/agents/<role>.toml` | `name`, `description`, `model`, and `model_reasoning_effort`, with the prose in `developer_instructions` |

The build parses each TOML file before writing it, so it fails rather than ship a file Codex can't read.

`dist/` is committed so diffs stay readable.
Don't edit it: change `core/` or an adapter, then rebuild.
`tests/run.sh` checks that every `dist/` is up to date; `tests/run.sh --slow` adds the `mutation-check` test.

## Install

Each installer runs `tests/run.sh` first, backs up what it replaces to `backups/agentic-workflow-<timestamp>/` in the harness home, and copies the build.
Neither installer edits your settings or merges hooks.

### Install for Claude Code

```bash
adapters/claude-code/install.sh
```

It installs `~/.claude/CLAUDE.md`, `~/.claude/agents/*.md`, the tools in `~/.claude/team/bin/`, and the skills in `~/.claude/skills/`.
Then merge `adapters/claude-code/settings.hooks.json` into `~/.claude/settings.json`:

- Add the `PreToolUse` entry, which runs `team-guard hook-pretool` on `Edit`, `Write`, `MultiEdit`, `NotebookEdit`, `Read`, `Grep`, and `Glob`, alongside your existing entries.
- Add the `TaskCompleted` hook, which runs `team-guard hook-task`.
- Add the allow rules for `team-guard` and `mutation-check`.

> **Caution:** The installer overwrites `~/.claude/CLAUDE.md` and the agent files, after backing them up. Make future edits in `core/`, rebuild, and reinstall, or the copies drift.

### Install for Codex CLI

```bash
adapters/codex/install.sh
```

It installs `~/.codex/AGENTS.md`, `~/.codex/agents/<role>.toml`, the tools in `~/.codex/team/bin/`, and the skills in `~/.agents/skills/`.
It also writes `~/.codex/rules/agentic-workflow.rules`, which lets `team-guard`, `mutation-check`, and `ask-jev` run outside the sandbox.
Then:

1. Merge `adapters/codex/hooks.json` into `~/.codex/hooks.json`, keeping your existing hooks. It adds a `PreToolUse` hook on `apply_patch`, `Edit`, and `Write`, and a `SubagentStop` hook that runs `team-guard hook-stop`.
2. Turn hooks on with `[features] hooks = true` in `config.toml`.
3. Run `/hooks` in Codex and trust the two `team-guard` hooks. Codex doesn't run untrusted hooks.

## Compare the harnesses

| | Claude Code | Codex CLI |
|---|---|---|
| Instructions | `~/.claude/CLAUDE.md` | `~/.codex/AGENTS.md`, with no `@imports` and a 32 KiB cap together with project `AGENTS.md` files |
| Phase ordering | Agent-team task dependencies | The lead spawns each phase with `spawn_agent` and waits with `wait_agent` |
| Agent-to-agent messages | `SendMessage` | None between subagents: the engineer reports a disputed test to the lead |
| Edit guard | `PreToolUse` on `Edit` and `Write` (`file_path`) | `PreToolUse` on `apply_patch`, with paths parsed from the patch |
| Completion guard | `TaskCompleted`, exit 2 | `SubagentStop`, a JSON `block` decision, once per stop |
| Agent identification | `agent_type` inside subagents, none documented for agent-team teammates | `agent_type` for subagents, omitted on the main thread |
| Tool permissions | Allow rules in `settings.json` | `prefix_rule`s in `~/.codex/rules/` |
| Skills | `~/.claude/skills/` | `~/.agents/skills/` (`$jev-decide`) |

Where the harness doesn't identify the agent, the git hooks and `team-guard verify` still catch changes to sealed tests.
Codex has no `Read` tool to hook, so holdout isolation there relies on instructions and paths.

Both harnesses share the workspace, holdout, and archive directories under `~/.agentic-workflow/`, so one team can mix them.

## Add a harness

1. Create `adapters/<harness>/adapter.json` with `harness`, `models`, and a value for every placeholder in `core/`.
2. Add a builder for its output format to `BUILDERS` in `build.py`, unless its `harness` type reuses an existing one.
3. Run `./build.py <harness>`, and add an `install.sh` next to the adapter.

## Next steps

- Follow the phases in [Workflow](./workflow.md).
- See what the hooks enforce in [Guard rails](./guard-rails.md).
