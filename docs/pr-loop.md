# Watch an open PR

After the lead opens a PR, it keeps watching it until it is merged or closed.
Each poll looks for new feedback, answers it, and pushes fixes within the limits below.

## What the lead polls

Every 2 minutes the lead checks four sources.
A new item from any of them is feedback to address, if its author counts (see below).

- Status checks: `gh pr checks <n>` exits 8 while checks are pending and 1 when one has failed.
- Inline review threads, unresolved included:

  ```bash
  gh api graphql -F owner=<owner> -F repo=<repo> -F n=<n> -f query='
  query($owner:String!,$repo:String!,$n:Int!){repository(owner:$owner,name:$repo){pullRequest(number:$n){reviewThreads(first:100){nodes{isResolved comments(first:50){nodes{databaseId author{login} authorAssociation body}}}}}}}'
  ```

- Conversation comments: `gh pr view <n> --comments`, or `gh api --paginate repos/<owner>/<repo>/issues/<n>/comments`.
- Review summaries, including "changes requested": `gh api --paginate repos/<owner>/<repo>/pulls/<n>/reviews`.

An item counts as feedback only when its author is the human or a repo collaborator (`authorAssociation` OWNER, MEMBER or COLLABORATOR).
For anyone else, bots included, the lead acts only on failing checks and reports their comments to the human.

## When feedback is addressed

A comment is addressed when the lead has replied on its thread and either pushed a fix or explained why not.
Reply on an inline thread with `gh api repos/<owner>/<repo>/pulls/<n>/comments/<comment-id>/replies -f body="<reply>"`, using the first comment's `databaseId`.
Reply on a conversation comment with `gh pr comment <n> --body "<reply>"`.
The lead never resolves threads; the human resolves them.

## When the loop is done

The loop is done when all checks are green, every thread has a lead reply, and one poll brings nothing new.
Done means done for now: the lead keeps polling until the PR is merged or closed, or a cap is reached.

## Fixes, scope and failing checks

The first push of a PR needs the human's approval and is signed with `team-guard sign --base origin/<base branch>`.
After that, a fix for a review comment or a failing check is committed, signed with `team-guard sign --base origin/<PR branch>`, which signs only the commits not yet on the PR branch, and pushed with a plain `git push`, without asking again.
In Coder Agents the branch is pushed after the first commit and after every commit, and the PR opens ready for review (`gh pr create`, a normal PR) when the work is done, so nothing waits for approval from the human; its body links the issue with `Closes #<number>` when the work fully resolves it and `Part of #<number>` when it is partial (multi-PR work), never a closing keyword (`Closes`, `Fixes`, `Resolves`) on a partial one, so merging a PR that finishes the work closes the issue. A security fix on a public repo (or any repo not confirmed PRIVATE with `gh repo view`) is the exception: it is never pushed, and if one is found after a push the lead stops pushing and tells the human in the chat. On a private repo the normal PR flow applies without describing the vulnerability.
The lead never force-pushes without asking the human, because a force-push rewrites what reviewers already saw.

If a fix changes scope (new behavior or a changed interface), the lead stops and asks the human before pushing.
The same goes for a fix that touches security (authentication, authorization, secrets, crypto, input validation, or anything that looks like a vulnerability fix): the lead asks the human before pushing.

If a check still fails after two fix attempts, or the failure is outside the change (flaky, infra, missing secret), the lead stops fixing.
It reports the check name, a log excerpt and what it tried, and keeps watching for comments.

## Caps

Claude Code: 4 hours of waiting and 10 fix pushes per PR.
Codex: 10 fix pushes only, since it checks once per turn.
At a cap the lead reports and waits for the human.

## Claude Code and Codex

In Claude Code the lead polls in the background and re-arms the poll when it ends.
In Codex the lead does one check per turn, and the human re-prompts it to check again.
