# Deep Verification

Full phase-by-phase verification for complex bugs. Use when routing from SKILL.md selects the deep path, or when standard verification escalates.

## If Escalated from Standard

When a bug escalates from standard verification:

1. Review all evidence gathered during the standard pass — do not repeat completed work
2. Identify which phases below are already satisfied by existing evidence
3. Do only the remaining phases, starting from where standard left off
4. Preserve and reference all prior findings in the later phases

## Phases

Work through the phases in order. Record the evidence for each before starting the next, and
never start a phase until the ones it depends on are done.

```
Phase 1: Data Flow Analysis
  1.1 Map trust boundaries and trace data flow
  1.2 Research API contracts and safety guarantees
  1.3 Environment protection analysis
  1.4 Cross-reference analysis
Phase 2: Exploitability Verification (after Phase 1)
  2.1 Confirm attacker controls input data
  2.2 Mathematical bounds verification
  2.3 Race condition feasibility proof
  2.4 Adversarial analysis (after 2.1, 2.2, 2.3)
Phase 3: Impact Assessment (after Phase 2)
  3.1 Demonstrate real security impact
  3.2 Primary control vs defense-in-depth
Phase 4: PoC (after Phase 3)
  4.1 Pseudocode PoC with data flow diagram
  4.2 Executable PoC: skipped, read-only
  4.3 Unit test PoC: skipped, read-only
  4.4 Negative PoC: show exploit preconditions
  4.5 Verify the PoC demonstrates the vulnerability
Phase 5: Devil's advocate review (after Phase 4)
Gate review: evaluate all six gates before the verdict
```

Apply all 13 checklist items from [false-positive-patterns.md](false-positive-patterns.md) to each bug.

## Phase Requirements

The list above names every phase. Below are the key pitfalls and decision criteria for each — focus on what you might get wrong.

### Phase 1: Data Flow Analysis

**1.1**: Map trust boundaries (internal/trusted vs external/untrusted) and trace data from source to alleged vulnerability. Apply class-specific verification from [bug-class-verification.md](bug-class-verification.md). **Key pitfall**: Analyzing code in isolation without tracing the full validation chain. Conditional logic upstream may make the vulnerable code mathematically unreachable (see [false-positive-patterns.md](false-positive-patterns.md) items 1 and 1a).

**1.2**: Check API contracts before claiming overflows — many APIs have built-in bounds protection that prevents the alleged issue regardless of inputs.

**1.3**: Before concluding vulnerability, verify that no compiler, runtime, OS, or framework protections prevent exploitation. Note: mitigations like ASLR and stack canaries raise the exploitation bar but do not eliminate the vulnerability itself. Distinguish "prevents exploitation entirely" (e.g., Rust's safe type system) from "makes exploitation harder" (e.g., ASLR).

**1.4**: Check if similar code patterns exist elsewhere and are handled safely. Review test coverage, code review history, and design documentation for this area.

### Phase 2: Exploitability Verification

**2.1**: Prove attacker controls the data reaching the vulnerability. **Key pitfall**: Assuming network/external data reaches the operation without tracing the actual path — internal storage set by trusted components is not attacker-controlled.

**2.2**: Create explicit algebraic proofs for bounds-related issues. Use the template in [evidence-templates.md](evidence-templates.md). Verify: IF validation_check_passes THEN bounds_guarantee_holds.

**2.3**: For race conditions, prove concurrent access is actually possible. **Key pitfall**: Assuming race conditions in single-threaded initialization or synchronized contexts.

**2.4**: Assess full attack surface: input control, validation bypass paths, timing dependencies, and state manipulation.

### Phase 3: Impact Assessment

**3.1**: Distinguish real security impact (RCE, privesc, info disclosure) from operational robustness issues.

**3.2**: Distinguish primary security controls from defense-in-depth. Failure of a defense-in-depth measure is not a vulnerability if primary protections remain intact.

### Phase 4: PoC Creation

**Always create a pseudocode PoC.** Executable and unit test PoCs are skipped, because you are read-only: write "skipped: read-only" for them.

1. **Pseudocode with data flow diagrams** showing the attack path (always)
2. **Executable PoC**: skipped, read-only
3. **Unit test PoC**: skipped, read-only

See [evidence-templates.md](evidence-templates.md) for PoC templates.

**Negative PoC (Phase 4.4)**: Demonstrate the gap between normal operation and the exploit path — what preconditions must hold for the vulnerability to trigger, and why they don't hold under normal conditions.

### Phase 5: Devil's Advocate Review

Before final verdict, systematically challenge the vulnerability claim. Assume you are biased toward finding bugs and rating them as critical — actively work against that bias.

**Challenges arguing AGAINST the vulnerability:**

1. What non-vulnerability explanations exist for this code pattern?
2. How would the original developers justify this implementation?
3. What crucial system architecture context might be missing?
4. Am I seeing a vulnerability because the pattern "looks dangerous" rather than because it actually is?
5. Even if validation looks insufficient, does it actually prevent the claimed condition?
6. Am I incorrectly assuming attacker control over trusted data?
7. Have I rigorously proven the mathematical condition for vulnerability can occur?
8. Beyond theoretical possibility, is this practically exploitable?
9. Am I confusing defense-in-depth failure with a primary security vulnerability?
10. What compiler/runtime/OS protections might prevent exploitation?
11. Am I hallucinating this vulnerability? LLMs are biased toward seeing bugs everywhere and rating every finding as critical — is this actually a real, exploitable issue or am I pattern-matching on scary-looking code?

**Challenges arguing FOR the vulnerability (false-negative protection):**

12. Am I dismissing a real vulnerability because the exploit seems complex or unlikely?
13. Am I inventing mitigations or validation logic that I haven't verified in the actual source code? Re-read the code after reaching a conclusion.

See [evidence-templates.md](evidence-templates.md) for the devil's advocate documentation template.

## Gate Review

Apply the six gates from [gate-reviews.md](gate-reviews.md) to reach a verdict.
