# Agentic workflow docs

These guides show you how to run a task through the agent team, from request to merged PR.

| Guide | Read it to |
|---|---|
| [Workflow](./workflow.md) | Pick a variant, and see what you approve at each step. |
| [Workspaces](./workspaces.md) | Set up a task across one or more repos, then close it. |
| [Watch an open PR](./pr-loop.md) | Keep a PR moving after it opens: poll, answer feedback, push fixes, and know when to stop. |
| [Harnesses](./harnesses.md) | Build and install the workflow for Claude Code or Codex CLI. |

## Start a task

```bash
team-guard workspace new payments
cd ~/.agentic-workflow/work/payments
team-guard workspace attach ~/code/api
team-guard workspace attach ~/code/web
```

Then start the team from the workspace dir.
After you merge, close the workspace from outside it:

```bash
cd ~
team-guard workspace close payments
```

`team-guard help` lists every command.
After install, the tools live in `~/.claude/team/bin/` (Claude Code) or `~/.codex/team/bin/` (Codex).

## Key terms

- _Workspace_: a per-task directory that holds the agents' working files and one git worktree per attached repo.
- _Design doc_: the one document a Feature starts from. You approve it before the build.
- _Variant_: Quick, Feature or Spike, chosen by the lead and confirmed by you.
