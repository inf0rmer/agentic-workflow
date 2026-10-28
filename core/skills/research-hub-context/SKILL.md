---
name: research-hub-context
description: Read the homelab's shared context from the Research Hub before starting work, and write durable facts back after it. The context is the topic `agent-context`. Use this before exploring a repo you do not know, and again when a project finishes.
---

# Research Hub context

The homelab keeps its durable context in the Research Hub (`research.at.home`) in one topic,
`agent-context`: what each repo is, how the homelab is laid out, what our conventions are, and
what finished projects learned. Every Coder agent chat reads and writes it, so treat it as
notes for agents you will never meet, not as scratch space.

Read it before work. Write to it after work. The content lives there and not in the workspace
image, so an edit at `research.at.home` reaches the next chat with no rebuild.

## Before work

Search first, then explore. One `researchhub-search` with the words you would use yourself is
cheap; an hour of discovery that the `repo-map` already answered is not. `researchhub-get_document`
returns a whole document when a search result's preview is not enough.

| Question | Search | Then read |
|---|---|---|
| What is `<repo>`, is it ours, how do I run it? | `repo-map <repo>` | `repo-map`, then `<repo>`'s `repo-<name>` document |
| Where does this fit in the homelab? | `homelab layout` | `homelab-layout` |
| How do we do `<thing>` here? | `<thing> conventions` | `conventions` |
| What happened on `<project>`? | `<project>` | that `project-<name>` document |

The documents are named by convention:

- `repo-map` — the index: every repo, what it is, its stack, its default branch, where its docs are.
- `homelab-layout` — the cluster, namespaces, Flux, how deploys and image builds work.
- `conventions` — the standing rules, including name-to-repo mappings (`skim` means `inf0rmer-homelab/skim`).
- `repo-<name>` — per-repo know-how: how to build and test it, the traps.
- `project-<name>` — one document per finished project, written on the way out.

Read two documents at most before starting. When the search returns nothing useful, or the
topic is empty, or what it says is plainly wrong: proceed with the ordinary discovery you would
have done anyway, never stall waiting for a knowledge base, and say in your report which facts
you had to work out for yourself. A gap you name is a gap the next chat does not hit.

`researchhub-list_topics` and `researchhub-get_topic` tell you the topic exists and list its
documents. Use them when a search surprises you; not as a first step.

## After work

Write back once, when the work is finished: shipped, merged, or reported. Only durable facts —
the things a competent agent could not cheaply rediscover:

- What shipped, where: repo, branch or PR, the files that carry the change.
- The decisions a future agent would otherwise re-derive or undo: why this shape, what was rejected.
- The facts about this repo you had to work out: the build and test commands, the trap in the CI.

Not a transcript, not a status report, not what is already in the repo's `README` or `docs/`.
If a fact belongs in the repo's own docs, put it there instead.

Choose the document by what you learned:

- **A finished project** gets its own new `project-<name>` document, created with
  `researchhub-create_document`: kebab-case, specific — `project-skim-postgres-backup`, not
  `project-fixes`. One document per project means two chats finishing at the same time add
  separate documents instead of overwriting each other.
- **A repo's own know-how** goes in that repo's `repo-<name>` document, read-modify-write: read
  the current document with `researchhub-get_document`, merge your additions into it, and write
  the whole thing back with `researchhub-update_document`. An update replaces the content
  wholesale, so a blind update would drop whatever someone else added since you last read it.
  That is the only safe way to touch a shared document.
- **Misleading shared context** — a stale `repo-map` entry, a convention we no longer follow —
  is worth fixing the same read-modify-write way, then saying what you changed and why.

Writes are last-writer-wins and there is no lock. That is accepted: each document has one
logical owner, projects never share one, and the human can restore any document from the hub's
own backups. Do not retry a failed write in a loop — report it, and let the human decide.

Never delete a document or a topic, and never empty one to start over: this key cannot, and an
over-eager rewrite is hard to notice after the fact. Never write a secret — no tokens, keys,
kubeconfigs, passwords or personal data — into a document; the hub is readable by every chat and
by the human. Do not create a topic: `agent-context` is the one place. If it is missing, report
that instead.

## When the hub and a repo disagree

The hub is the truth for context. Each repo's own docs win on the facts inside that repo — its
test command, its layout, its default branch. On a conflict, follow the repo, then fix the hub's
context document so the next chat is not misled, and say which document you corrected.

## When the tools are not there

If `researchhub-*` tools are missing or every call fails, say so once in your report, work from
the repo as usual, and do not substitute another tool. A chat without the hub still has to
finish the job; the next chat with the hub picks up the write-back.
