# Global Instructions

## Codebase Memory MCP

When the `codebase-memory` MCP server is available, **always use it** to enrich your understanding of the codebase before making changes:

- **Before starting work:** call `get_architecture` or `search_code` to understand relevant components and their relationships.
- **Before modifying a file:** call `get_code_snippet` to review how the code fits into the broader dependency graph.
- **After significant changes:** call `detect_changes` so the knowledge graph stays up to date.
- **When exploring unfamiliar areas:** prefer `search_graph` and `query_graph` over raw file searches — they surface architectural context that grep alone misses.

This applies to all agents (main conversation and teammates alike). If the MCP server is not connected, fall back to normal file search tools.

## Pre-commit Discipline

Before every commit, always run the project's formatter, linter, and test suite — in that order. Check the project's `CLAUDE.md`, `Makefile`, or CI config to find the right commands. Fix any issues before committing. Never skip these checks.

## Read Before You Change

Read the project's relevant documentation before making changes. Look for design docs, architecture specs, roadmaps, and READMEs. Understand the context and intent behind existing code before modifying it.

## Agent Team Workflow

For significant features or changes that are more than a few lines of code, use [Claude Code agent teams](https://code.claude.com/docs/en/agent-teams) with a structured multi-agent approach.

### Recommended team structure

Scale the team to the task — a small bug fix might only need an engineer + tester, while a large feature uses the full pipeline. Global role definitions live in `~/.claude/agents/` and are available across all projects. A typical full team:

| Agent | Role | Owns | Definition |
|-------|------|------|------------|
| **product-owner** | Product vision, roadmap & issues | Roadmap, issues | `~/.claude/agents/product-owner.md` |
| **researcher** | Research & design decisions | Design docs | `~/.claude/agents/researcher.md` |
| **architect** | Architecture specs | Architecture docs | `~/.claude/agents/architect.md` |
| **software-engineer** | Implementation | Source code | `~/.claude/agents/software-engineer.md` |
| **tester** | Tests & coverage | Test files | `~/.claude/agents/tester.md` |
| **reviewer** | Code review & gap analysis | Nothing (read-only) | `~/.claude/agents/reviewer.md` |
| **doc-writer** | User-facing docs | Documentation | `~/.claude/agents/doc-writer.md` |

Projects can override these by placing project-specific versions in `.claude/agents/`.

### Worktree-first workflow

**All teammates must work in the same worktree directory.** Before creating the team, set up a single worktree for the feature branch (use `/enter-worktree` or `git worktree add`). Then create the agent team from within that worktree so all teammates inherit it as their working directory.

### Phased execution with task dependencies

Use task dependencies to enforce phase ordering. Teammates within a phase can work in parallel; cross-phase work is blocked until dependencies complete.

- **Phase 1 — Scope & Design** (parallel): product-owner, researcher
- **Phase 2 — Architecture:** architect (depends on Phase 1 tasks)
- **Phase 3 — Implementation & Test** (parallel): software-engineer, tester
- **Phase 4 — Review:** reviewer (depends on Phase 3 tasks)
- **Phase 5 — Docs & Close-out** (parallel): doc-writer, product-owner

### File ownership

Teammates within the same phase must own non-overlapping files. If two teammates need the same file, make one task depend on the other.

### Task sizing

Aim for 5-6 tasks per teammate. Not every feature needs all agents — match the team size to the scope of the change.

### Decision protocol (Jev fast lane)

Small decisions shouldn't wait on a full research pass or block a phase. Any teammate facing a **bounded choice between known options** uses the `jev-decide` skill:

- **`ACT`** → take the choice, record it as `Decided via Jev: <choice> — <one-line reason>` in the task notes, and continue.
- **`ESCALATE`** → follow the normal path (researcher, architect, or human), passing along Jev's leading choice and reasons.
- **`DEFER`** → a risk (public contract, persisted data, security, cross-team) is clearly present. Hand it to the researcher or human with the reasons; Jev's choice is withheld so it doesn't anchor them.
- **Blocked on a missing doc?** Instead of stopping, ask Jev; on `ACT` or `ESCALATE`, proceed with its choice marked `PROVISIONAL` in your output and keep going. Never on `DEFER`. The reviewer must confirm or reject every `PROVISIONAL` decision in Phase 4.
- **Never use Jev** for open-ended design, security fixes, or anything that can't be described without PII, customer or tenant data, secrets, or source code. Jev is a third-party service.

The reviewer reads the decision log (`jev-decisions.md` in the git directory) as part of every review.

@RTK.md
