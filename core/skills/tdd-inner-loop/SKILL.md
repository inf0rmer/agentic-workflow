---
name: tdd-inner-loop
description: The software-engineer's inner loop for making sealed tests pass with unit tests the engineer owns. Loaded by name from the software-engineer role only.
---
<!-- vendored from https://github.com/mattpocock/skills skills/engineering/tdd at d81f3a183412e71a5b1e84ca21bc1a35eea03a60; license: MIT (see LICENSE); modified by agentic-workflow (core/skills/patches/tdd-inner-loop.patch) -->

# Test-Driven Development

TDD is the red → green loop. This skill is the reference that makes that loop produce tests worth keeping: what a good test is, where tests go, the anti-patterns, and the rules of the loop. Every section applies on every cycle: consult them before and during the loop, not after.

When exploring the codebase, read the optional `GLOSSARY.md` and the ADRs in `$(team-guard workspace-path)`, and the project's own glossary and ADRs if it keeps them, so test names and interface vocabulary match the project's domain language. Only read the project's; write in the workspace.

## What a good test is

Tests verify behavior through public interfaces, not implementation details. Code can change entirely; tests shouldn't. A good test reads like a specification: "user can checkout with valid cart" tells you exactly what capability exists, and it survives refactors because it doesn't care about internal structure.

See [tests.md](tests.md) for examples and [mocking.md](mocking.md) for mocking guidelines.

## Seams: where tests go

A **seam** is the public boundary you test at: the interface where you observe behavior without reaching inside. Tests live at seams, never against internals.

The seams come from `contract.md` and the sealed tests, which the tester wrote at those seams from the spec. You do not choose or confirm them. The sealed tests are the definition of done: make them pass.

Your own unit tests sit underneath those seams. For each failing sealed test, write one failing unit test for the smallest internal piece, make it pass, repeat, and record the internal seams you choose in your task notes. Unit tests are yours: they are not sealed, and they are not evidence that a criterion is met.

When the shape of an interface is itself in question, {{skill_call}} `{{skill_sigil}}codebase-design` for the shared vocabulary (module, interface, depth, seam, adapter, leverage, locality). It is a reference to consult, not a session to run.

## Anti-patterns

- **Implementation-coupled**: mocks internal collaborators, tests private methods, or verifies through a side channel (querying the database instead of using the interface). The tell: the test breaks when you refactor but behavior hasn't changed.
- **Tautological**: the assertion recomputes the expected value the way the code does (`expect(add(a, b)).toBe(a + b)`, a snapshot derived by hand the same way, a constant asserted equal to itself), so it passes by construction and can never disagree with the code. Expected values must come from an independent source of truth: a known-good literal, a worked example, the spec.
- **Horizontal slicing**: writing all tests first, then all implementation. Bulk tests verify _imagined_ behavior: you test the _shape_ of things rather than user-facing behavior, the tests go insensitive to real changes, and you commit to test structure before understanding the implementation. Work in **vertical slices** instead: one test → one implementation → repeat, each test a **tracer bullet** that responds to what the last cycle taught you.

## Rules of the loop

- **Red before green.** Write the failing test first, then only enough code to pass it. Don't anticipate future tests or add speculative features.
- **One slice at a time.** One seam, one test, one minimal implementation per cycle.
- **Refactoring is not part of the loop.** It belongs to the review stage (see the `code-review` skill), not the red → green implementation cycle.
- **Sealed tests are fixed.** You never edit, delete, skip or weaken a sealed test, or the fixtures it depends on.
- **A wrong sealed test is reported, not fixed.** If a sealed test looks wrong, report the failing assertion and why the spec supports your reading to the lead, who relays it to the tester.
