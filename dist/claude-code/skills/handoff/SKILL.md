---
name: handoff
description: Compact the current conversation into a handoff document for another agent to pick up.
argument-hint: "What will the next session be used for?"
disable-model-invocation: true
---
<!-- vendored from https://github.com/mattpocock/skills skills/productivity/handoff at d81f3a183412e71a5b1e84ca21bc1a35eea03a60; license: MIT (see LICENSE); modified by agentic-workflow (core/skills/patches/handoff.patch) -->

Write a handoff document summarising the current conversation so a fresh agent can continue the work. Save it to `handoff-<slug>.md` (slug from the topic) in `$(team-guard workspace-path)`, never in a project repo, and report the path. If `team-guard workspace-path` fails because there is no workspace, use `git rev-parse --absolute-git-dir` instead, never the working tree.

Include a "suggested skills" section in the document, naming which skills the next agent should load, written like call the Skill tool with `diagnosing-bugs`.

Do not duplicate content already captured in other artifacts (specs, plans, ADRs, issues, commits, diffs). Reference them by path or URL instead.

Redact any sensitive information, such as API keys, passwords, or personally identifiable information.

If the user passed arguments, treat them as a description of what the next session will focus on and tailor the doc accordingly.
