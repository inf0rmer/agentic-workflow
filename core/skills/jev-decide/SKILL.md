---
name: jev-decide
description: Get a fast, typed ruling on a small engineering decision from Jev (TypeSafe's decision model on OpenRouter) instead of waiting on a full research pass. Use when an agent-team member hits a bounded choice between known options — naming, placement, library pick, provisional answer to an unblocking question — and needs to know whether it can just act or must escalate. Not for open-ended design, code review, or anything involving PII, customer data, or secrets.
---

# jev-decide

Jev (`typesafe/jev-1.13`) is a **decision model**, not an LLM: it picks one of the options you give it and returns probabilities. It cannot explain itself, write code, or see your files. It answers in under a second for a fraction of a cent, so use it freely for small calls.

## Data rules — read before every call

Jev is a **third-party service** (TypeSafe, via OpenRouter). Anything you send leaves Grafana. **Never send:**

- **PII** — names of people, emails, usernames, phone numbers, IP addresses, anything tied to an individual.
- **Customer or tenant data** — customer or company names, stack/org/instance/tenant IDs or slugs, `*.grafana.net` URLs, Salesforce/Zendesk/support-escalation content, customer dashboards, queries, logs, or metrics.
- **Secrets** — tokens, keys, passwords, connection strings.
- **Source code, diffs, or file contents** — describe the decision in your own words instead.
- **Security-sensitive details** — anything about an unfixed vulnerability. Those decisions go to the human.

Write the decision **abstractly**: "a helper used by one component", not the customer or ticket that prompted it. If the decision only makes sense with any of the above, **don't use Jev** — escalate as you normally would.

`ask-jev` runs a local check (patterns for emails, IPs, IDs, Grafana Cloud URLs, credentials, and names in `denylist.txt`) and refuses to send anything that trips it. That check is a backstop, not a license: it cannot recognize every customer or person, so the responsibility is yours.

## How to call it

Write the decision as JSON and pipe it in:

```bash
{{skills_dir}}/jev-decide/scripts/ask-jev <<'EOF'
{
  "decision": "Where should the formatDuration() helper live?",
  "context": "Frontend monorepo. Used by exactly one component today; another team might need it next quarter, nothing committed.",
  "constraints": "Moving it later is a cheap refactor. The shared package is public API with deprecation obligations.",
  "options": {
    "colocated": "Keep it next to its only caller until a second caller exists",
    "core_utils": "Put it in the internal core utils module",
    "shared_package": "Put it in the published shared package now"
  },
  "caller": "software-engineer"
}
EOF
```

- `decision` and `options` (2+ entries, each with a one-line description) are required. Keep the whole thing under ~6,000 characters.
- Make options concrete and mutually exclusive. Jev only chooses among what you list.
- The script adds the risk, clarity, and reversibility questions itself; you only supply the decision.

## Reading the result

Besides the choice, the script asks Jev narrow yes/no questions — does the decision touch a **public contract**, **persisted data**, **security**, or **other teams** — plus whether the options are clear enough to choose between and how costly it is to reverse. Each verdict comes with named `reasons`.

```json
{ "verdict": "ACT", "choice": "colocated", "probabilities": {...}, "reasons": [],
  "risks": {"public_contract": 0.06, "persisted_data": 0.02, "security_relevant": 0.02, "cross_team": 0.08},
  "options_clear": 0.76, "reversibility": 0.01, "log": ".../jev-decisions.md" }
```

- **`ACT`** — every risk is clearly absent (< 0.2), the options are clear, and one option wins (≥ 0.75, or ≥ 0.6 when the decision is free to undo). Take `choice`, note it as `Decided via Jev: <choice> — <reason>`, and keep going.
- **`ESCALATE`** — something is unclear. Take the normal route (researcher, architect, or human) and pass along `choice` and `reasons` as a starting point. When blocked on a missing doc, you may proceed with `choice` marked `PROVISIONAL`.
- **`DEFER`** — a risk is clearly present (≥ 0.9) or the decision is very hard to undo. Jev's choice is withheld on purpose so it doesn't bias whoever decides. Hand it to the researcher or human with the `reasons`; **never** proceed as `PROVISIONAL`. For `security_relevant`, involve the human.
- Refused input or API errors come back as `ESCALATE` with an `error`.
- Answers vary by a few hundredths between runs. Don't re-ask the same question hoping for `ACT`.

Every call is appended to `jev-decisions.md`. Run from the workspace dir or an attached worktree, it goes to `jev-decisions.md` in the workspace dir, one log for the whole feature; outside a workspace it goes to `jev-decisions.md` in the git directory (per worktree, never committed). `ask-jev --print-log-path` prints the resolved path without sending anything. The reviewer reads the log; add a one-line reason for `ACT` decisions to your task notes, since Jev records none.

## Configuration

Environment overrides: `OPENROUTER_API_KEY` (else read from 1Password via `JEV_OP_REF`), `JEV_MODEL`, `JEV_DENYLIST`, and every threshold at the top of `scripts/ask-jev` (`JEV_MIN_CHOICE_PROBABILITY`, `JEV_RISK_CLEAR_BELOW`, `JEV_RISK_PRESENT_AT`, …). Tune thresholds from the decision log, not by guessing.
