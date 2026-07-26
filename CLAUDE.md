# CLAUDE.md

Engineering operating manual for implementation work on this repository.
This document defines **how** to build. It does not describe what to build.

---

## 1. Authority

| Document | Role |
|----------|------|
| `docs/ARCHITECTURE.md` | Design source of truth. Frozen. |
| `docs/BUILD.md` | Implementation sequence. Frozen. |
| `docs/DECISIONS.md` | Pinned values. Read before using any constant. |
| `CLAUDE.md` | Engineering rules. This document. |

**Precedence:** ARCHITECTURE.md > BUILD.md > CLAUDE.md > inference.

If a task appears to require something these documents forbid, stop and raise it. Do not
resolve the conflict by choosing the interpretation that lets the work continue.

---

## 2. Invariants

These are not preferences. Violating one is a defect regardless of whether tests pass.

**I1 — Boundary placement.** The privacy policy engine is applied only at the LLM-call
boundary. Never before retrieval, parsing, or storage. `app/privacy/` must not be
importable from `app/retrieval/` or `app/ingest/`.

**I2 — No raw PII outbound.** In `full_tokenize` and `policy_engine` modes, no raw PII
value appears in any payload sent to an external LLM.

**I3 — Single LLM call site.** After Phase 4, no module outside the boundary layer imports
or calls an LLM client directly.

**I4 — Fail closed.** A detected sensitive value with no explicit policy rule is tokenized,
never passed through.

**I5 — Abstain, never invent.** A field with no supporting evidence returns empty and
flagged. Under no circumstance is a plausible value generated.

**I6 — Case isolation.** Retrieval for one case never returns content from another.

Each invariant has a test that enforces it. Those tests are described in §4.

---

## 3. Phase discipline

- Work through `BUILD.md` phases in order. Do not start a phase before the previous
  phase's exit criteria are all met.
- Do not implement a later phase's functionality early because it seems convenient.
  "While I'm in here" is how a phase plan stops being a phase plan.
- The application must be runnable at the end of every phase from Phase 2 onward. A phase
  that ends with the app broken is not complete, regardless of what was built.
- When a phase is complete, state which exit criteria are met and which are not. Do not
  mark a phase done with open criteria.

---

## 4. Testing

**Write tests first where `BUILD.md` says so.** Those cases are specified because the
contract is known in advance. Elsewhere — prompt shaping, extraction tuning — write the
implementation first and tests after.

**Invariant tests are protected.** The tests enforcing I1–I6 must never be weakened,
skipped, marked expected-failure, or deleted to make a change pass. If an invariant test
fails, the code is wrong, not the test. If you believe the test itself is wrong, stop and
raise it — do not edit it and continue.

**Rules for all tests:**

- No test may depend on a live external LLM. Stub or cache. Tests must pass offline.
- Deterministic tests only. No reliance on wall-clock time, random seeds, dict ordering, or
  network state.
- A test that never fails is not protecting anything. When adding a regression test, verify
  it fails against the broken behavior before committing it.
- Do not lower a pinned regression threshold to make CI pass. Investigate the regression.

---

## 5. Failure behavior

**Explicit failure beats silent degradation. Everywhere. No exceptions in this project.**

- No bare `except:` and no `except Exception: pass`. Catch specific exceptions and handle
  them meaningfully or let them propagate.
- No silent fallback to a default when a lookup, parse, or detection fails. An unknown PIN
  code, an unparseable date, a missing config key — each fails loudly or is handled with an
  explicit, logged, tested branch.
- Invalid configuration fails at load time, not at first use.
- No swallowed LLM errors. A failed call surfaces as a field-level error state, not an
  empty value that looks like a legitimate abstention.

Rationale: this system's central claim is about what does and does not leave the trusted
boundary. A silent fallback in that path is indistinguishable from a leak.

---

## 6. Code structure

- Respect the package layout in `BUILD.md` Phase 0. New code goes in the package that owns
  that concern.
- One-directional dependencies. `app/privacy/` depends on nothing in `app/`. Nodes depend
  on services; services do not depend on nodes.
- Pinned values live in `DECISIONS.md` and are read from config or a single named constant.
  No magic numbers, no duplicated literals — especially the age reference date, the age band
  width, the k-threshold, and retry budgets.
- One function, one call site for the age computation. It is duplicated in exactly zero
  other places.
- Type hints on public functions. Structured logging, not `print`.
- No new dependency without stating why an existing one is insufficient.

---

## 7. Commits

- Small and reviewable. One logical change per commit.
- Every commit leaves the test suite green. Do not commit a known-broken state to "fix it
  in the next one."
- Commit messages state what changed and why. Reference the phase: `[P3] add FF1 tokenizer
  with session-scoped map`.
- Never commit secrets, API keys, `.env` files, generated PII, or evaluation output
  containing real identifiers.

---

## 8. Deviations

The architecture is frozen. A deviation is permitted only when a **genuine implementation
blocker** is discovered — something that makes the specified design impossible or
demonstrably incorrect, not merely inconvenient, slower, or less elegant.

**These are not blockers:** a cleaner pattern exists; a library makes a different approach
easier; the specified approach requires more code; a component could be more general.

When a real blocker appears, stop and present, in this order:

1. What was attempted and the specific failure
2. Why it cannot be resolved within the current design
3. Two or more options, with trade-offs for each
4. A recommendation and what it costs

Then wait. Do not implement a deviation and report it afterward.

---

## 9. Scope

- Build what the current phase specifies. Nothing else.
- The non-goals in `ARCHITECTURE.md` §2 are decisions, not gaps. Do not implement toward
  them, and do not leave scaffolding for them.
- No speculative abstraction. No plugin systems, no strategy patterns, no configuration
  hooks for cases that do not exist. Two concrete implementations justify an interface; one
  does not.
- Do not add caching, retries, queues, metrics, or middleware not specified in `BUILD.md`.
- If something outside scope seems genuinely valuable, note it and move on. Do not build it.

---

## 10. Working style

- **Correctness over cleverness.** This code will be read by an interviewer. Obvious code
  that is clearly right beats compact code that requires explanation.
- **Do not refactor code you were not asked to change.** Especially not to "clean up" a
  module that an invariant test guards.
- When context is missing, read `DECISIONS.md` first, then ask. Do not infer a pinned value.
- State assumptions explicitly rather than embedding them silently in code.
- Report honestly. If accuracy is poor, a test is flaky, or a phase's results are
  disappointing, say so plainly. Measured bad results are an asset in this project;
  optimistic reporting destroys the only thing that makes it credible.
- Do not summarize work as complete when exit criteria are open.

---

## 11. Documentation

Update alongside code, in the same commit, only where `BUILD.md` requires it:

- `DECISIONS.md` — whenever a pinned value is set or measured (recall@k, baseline accuracy,
  detection recall)
- `RESULTS.md` — Phase 7 only, and every claim in it traceable to a stored run
- `README.md` — Phase 8, plus dependency or run-instruction changes as they happen

Do not write documentation for code that does not exist yet.

---

## 12. Session start

Before writing code in any session:

1. Read `DECISIONS.md` for pinned values.
2. Identify the current `BUILD.md` phase and its exit criteria.
3. Confirm the test suite is green before changing anything.
4. State what will be built this session and which exit criteria it advances.

---

*Rules exist to protect measurements. This project's value is its numbers; anything that
makes those numbers unreliable — a weakened invariant test, a silent fallback, an
unreported regression — costs more than the time it saves.*
