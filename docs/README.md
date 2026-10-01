# Agentic workflow docs

These guides show you how to run a feature through the agent team, from spec to close-out.

| Guide | Read it to |
|---|---|
| [Workflow](./workflow.md) | Pick a variant, follow its steps through the design gate, the review, and the human gate. |
| [Workspaces](./workspaces.md) | Set up a feature across one or more repos, then close and collect it. |
| [Watch an open PR](./pr-loop.md) | Keep a PR moving after it opens: poll, answer feedback, push fixes, and know when to stop. |
| [Guard rails](./guard-rails.md) | Seal tests, verify them, and harden them with mutation testing. |
| [Decisions with Jev](./decisions-with-jev.md) | Settle small choices fast, and know when not to. |
| [Harnesses](./harnesses.md) | Build and install the workflow for Claude Code or Codex CLI. |

## Start a feature

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

`team-guard --help` lists every command.
After install, the tools live in `~/.claude/team/bin/` (Claude Code) or `~/.codex/team/bin/` (Codex).

## Learn the key terms

- _Workspace_: a per-feature directory that holds the workflow artifacts and one git worktree per attached repo.
- _Contract_: the architect's `contract.md`, with the public interfaces and how each acceptance criterion is verified. It's the only architecture doc the tester reads.
- _Sealed test_: a committed test the engineer must make pass without changing it.
- _Holdout scenarios_: a few end-to-end scenarios the engineer never sees. The reviewer runs them in the last review pass.
- _Surviving mutant_: a small change to the new code that no test noticed. When mutation testing is opted in, the tester writes a test for each one that matters or records why it doesn't.
- _Jev_: a third-party decision model for small, bounded choices.
- _Human gate_: the point where you review the result before anything is pushed.

## Next steps

- New to the workflow? Start with [Workflow](./workflow.md).
- Installing it? See [Harnesses](./harnesses.md).
