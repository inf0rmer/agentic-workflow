# Watch an open PR

After the lead opens a PR, it keeps watching it until it is merged or closed.
Each poll looks for new feedback, answers it, and pushes fixes within the limits below.

## What the lead polls

Every 2 minutes the lead checks three sources.
A new item from any of them is feedback to address.

- Status checks: `gh pr checks <n>`.
- Inline review threads, unresolved included: the GraphQL `reviewThreads` field of the pull request, through `gh api graphql`.
- Conversation comments: `gh pr view <n> --comments`.
- Review summaries, including "changes requested": `gh api repos/<owner>/<repo>/pulls/<n>/reviews`.

## When feedback is addressed

A comment is addressed when the lead has replied on its thread and either pushed a fix or explained why not.
The lead never resolves threads; the reviewer does.

## When the loop is done

The loop is done when all checks are green, every thread has a lead reply, and one poll brings nothing new.

## Fixes, scope and failing checks

A fix for a review comment or a failing check is committed, re-signed with `team-guard sign` and pushed without asking again.
The first push of a PR still needs the human's approval.

If a fix changes scope (new behavior, a changed contract, or a sealed-test change), the lead stops and asks the human before pushing.

If a check still fails after two fix attempts, or the failure is outside the change (flaky, infra, missing secret), the lead stops fixing.
It reports the check name, a log excerpt and what it tried, and keeps watching for comments.

## Caps

Waiting turns do not count toward the per-variant turn caps.
The loop stops after 4 hours of waiting or 10 fix pushes per PR.
Then the lead reports and waits for the human.

## Claude Code and Codex

In Claude Code the lead polls in the background and re-arms the poll when it ends.
In Codex the lead does one check per turn, and the human re-prompts it to check again.
