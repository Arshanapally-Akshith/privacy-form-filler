# BUILD.md

**Project:** Privacy-Preserving AI Document Automation
**Source of truth for design:** `ARCHITECTURE.md` (frozen)
**This document:** implementation sequencing only. Where a "why" is needed, it is cited to
an ARCHITECTURE.md section rather than re-argued here.

---

## How to use this document

Phases are sequential. Each produces a runnable milestone. **From the end of Phase 2
onward, the application is always demoable** — every later phase adds a layer behind a
switch rather than rewriting what works. If a phase runs long, the fallback is to ship the
previous milestone, not to leave the system in a half-migrated state.

Test-first applies where the test is cheap and the contract is known: schema validation,
invariant enforcement, determinism, boundary assertions. It does not apply to exploratory
work like prompt shaping, where writing the test first means guessing at an interface you
haven't discovered yet. Each phase states which of its tests are written before the code.

**Do not skip Phase 7.** Under time pressure the evaluation is the first thing that feels
optional and the only thing that actually differentiates the project.

### Timeline

| Phase | Duration | Cumulative |
|-------|----------|-----------|
| 0 — Contracts and skeleton | 3 days | 0.5 wk |
| 1 — Ingestion and retrieval | 4–5 days | 1.5 wk |
| 2 — Vertical slice, no privacy | 1 week | 2.5 wk |
| 3 — Policy engine library | 1 week | 3.5 wk |
| 4 — Boundary integration | 4–5 days | 4.5 wk |
| 5 — Orchestration and verifier | 1 week | 5.5 wk |
| 6 — Evaluation dataset and harness | 1 week | 6.5 wk |
| 7 — Results and analysis | 1 week | 7.5 wk |
| 8 — Deploy, docs, buffer | 4–5 days | 8.5 wk |

Roughly half a week of slack exists across the plan. It is already spoken for.

### Cross-team checkpoints

Two integration points with the frontend developer are non-negotiable. Both involve the
UI hitting the real API, not a mock.

| When | What |
|------|------|
| End of Phase 2 | First real integration: upload → field results → filled PDF |
| End of Phase 5 | Human-review flow, the only stateful interaction in the UI |

If both slip to Phase 8, the deployment architecture described in `ARCHITECTURE.md` §9
will not be the one actually running.

---

## Phase 0 — Contracts and skeleton

**Duration:** 3 days

### Objective

Freeze everything that two people or two subsystems must agree on, while it is still
cheap to change. Nothing in this phase is throwaway.

### Deliverables

- Repo skeleton with the package layout below
- FastAPI application that boots and serves `/health`
- `DECISIONS.md` with all pinned values
- OpenAPI contract, committed as a snapshot, sent to the frontend developer
- Policy config JSON schema
- CI running lint + tests on push
- Dockerfile that builds and runs the skeleton

### Repo layout

```
app/
  api/            FastAPI routers, request/response models
  ingest/         parsing, chunking
  retrieval/      embedding, vector store, query
  extraction/     field extraction nodes
  orchestration/  LangGraph graph, state, edges
  privacy/        policy engine (library, no app imports)
  filling/        PDF output, provenance panel
  config/         policy configs, form schemas
eval/
  dataset/        generators, fixtures
  harness/        run matrix, write results
tests/
docs/             ARCHITECTURE.md, BUILD.md, DECISIONS.md, RESULTS.md
frontend/         teammate's React app; build output mounted by FastAPI
```

`app/privacy/` must not import from `app/retrieval/` or `app/ingest/`, and vice versa.
Enforced by test in Phase 1.

### Implementation tasks

1. Initialize repo, dependency management, lockfile. Pin versions — an unpinned LangGraph
   or embedding library breaking mid-project costs a day you do not have.
2. FastAPI app factory, `/health`, structured logging, settings via environment.
3. Write `DECISIONS.md`. Every value below is fixed here and referenced everywhere else:
   - Age reference date: **form submission/creation date**. Single function, single call site.
   - Age band width: 10 years, decade-aligned.
   - k-threshold: **k ≥ 5**. Report minimum k, median k, and percentage of cases at k=1.
   - Verifier audit: 10–15 ambiguous cases; precision on flagged, recall on should-have-flagged.
   - PIN → city/state dataset: source name, version, retrieval date, committed file path.
   - Default policy action for undetected sensitive values: **Tokenize** (fail closed).
4. Define and freeze the OpenAPI contract:

   | Endpoint | Purpose |
   |----------|---------|
   | `POST /api/cases` | Create case: upload form template ref + supporting documents |
   | `GET /api/cases/{id}/status` | Processing state, progress |
   | `GET /api/cases/{id}/fields` | Per-field: value, confidence, provenance, state |
   | `POST /api/cases/{id}/fields/{name}/review` | Human decision on a flagged field |
   | `GET /api/cases/{id}/result` | Filled PDF |
   | `GET /api/form-schemas` | Available form templates |

   Field state enum, fixed now: `filled | low_confidence | conflict | missing | human_reviewed`.
5. Policy config JSON schema: per-field action mapping, permitted co-occurrence sets,
   dataset version reference.
6. Agree with frontend developer, in writing: static build output path, build command,
   and that the SPA is served from the same origin as the API.
7. Dockerfile, multi-stage, with a placeholder static mount.
8. CI: lint, type check, tests.

### Testing requirements

**Written before implementation:**
- Policy config schema validation — valid fixture passes, each invalid fixture fails with
  a specific error (unknown action, missing dataset version, banned co-occurrence declared)
- OpenAPI snapshot test: generated schema matches the committed snapshot; drift fails CI

**Written alongside:**
- `/health` returns 200
- Docker image builds and the container serves `/health`

### Exit criteria

- [ ] `docker build` succeeds; container serves `/health`
- [ ] OpenAPI snapshot committed and sent to frontend developer
- [ ] `DECISIONS.md` complete — no `TBD` remaining
- [ ] CI green on a clean clone
- [ ] Static build path agreed in writing

### Risks

| Risk | Mitigation |
|------|-----------|
| Contract churn after frontend work starts | Snapshot test makes breaking changes fail loudly rather than silently |
| PIN dataset turns out to be unavailable or unlicensed | Resolve in this phase, not Phase 3 — it is a §5.4 pinned decision |

---

## Phase 1 — Ingestion and retrieval

**Duration:** 4–5 days

### Objective

Retrieve evidence from raw documents inside the trusted boundary, with page-level
provenance. No LLM involved yet.

### Deliverables

- Document parsing (text PDF, scanned PDF, JPG/PNG) with page numbers preserved
- OCR fallback inside the parser abstraction, routed per page
- Chunking that carries `(document_id, page_number)` on every chunk
- Embedding + per-case vector store
- `POST /api/debug/retrieve` — field label in, ranked chunks with provenance out
- Recall@k measured on hand-labeled pairs

### Implementation tasks

1. Parser abstraction over document types; normalize to `(text, page_number, document_id)`.
1a. OCR as a strategy **inside** that abstraction (`ARCHITECTURE.md` D11). Select the engine
   and record it as E10. A CPU-only engine is required — no GPU dependency, per the
   deployment constraint.
1b. Per-page routing: attempt native text extraction; fall back to OCR when the page's text
   layer is absent or below the sufficiency threshold (E11). Decide per page, not per
   document — mixed documents are the common case. Log which path each page took; you will
   need it to separate OCR error from extraction error later.
1c. Input validation: reject images below the minimum resolution (E12) with an explicit
   error rather than attempting OCR on unreadable input (`CLAUDE.md` §5).
2. Chunking with provenance metadata. A chunk without a page reference is a bug, not a
   degraded case — provenance display depends on it (`ARCHITECTURE.md` §6).
3. Embedding via a hosted API. Local models are excluded by the free-tier deployment
   constraint; decide now and record it.
4. Per-case vector index. Case isolation is a correctness property: cross-case retrieval
   is a data leak between users.
5. Query construction from field labels — start with the label plus lightweight synonym
   expansion. Keep it simple; the verifier handles ambiguity later.
6. Debug retrieval endpoint. This stays in the final build behind a flag; it is the
   fastest way to diagnose a bad field result during evaluation.
7. Hand-label ~20 (field → correct chunk) pairs across 3–4 documents.
8. Measure recall@k. **Record the number in `DECISIONS.md`.**

### Testing requirements

**Written before implementation:**
- Import-boundary test: `app.privacy` is not reachable from `app.retrieval` or
  `app.ingest` import graphs. This mechanically enforces Invariant P1
  (`ARCHITECTURE.md` §3) and must exist before there is anything to violate it.
- Case isolation: retrieval for case A never returns a chunk from case B.

**Written alongside:**
- Golden-file parser tests on 3–4 representative documents, covering all three input forms:
  text PDF, scanned PDF, and JPG/PNG
- OCR path: an image-only document produces text with correct page attribution
- Routing: a text-layer PDF does **not** invoke OCR; a mixed document invokes OCR only on
  the pages that need it
- Sub-minimum-resolution image is rejected with an explicit error
- Every chunk carries non-null `document_id` and `page_number`
- Recall@k regression test pinned to the measured baseline

### Exit criteria

- [ ] All three input formats parse end to end with correct page attribution
- [ ] Routing verified: OCR invoked only where the text layer is insufficient
- [ ] E10, E11, E12 pinned in `DECISIONS.md`
- [ ] Debug endpoint returns correct chunks with page references for hand-labeled fields
- [ ] Recall@k measured and recorded, split by native-text vs OCR-derived source
- [ ] Import-boundary test green
- [ ] Case isolation test green

### Risks

| Risk | Mitigation |
|------|-----------|
| Recall is poor and everything downstream inherits it | Measuring here is the point. If recall@5 is weak, fix retrieval now — chunk size, query expansion — before building on it. Do not proceed hoping the LLM compensates. |
| Parser fails on scanned documents | `ARCHITECTURE.md` N3 excludes handwriting and poor-quality scans. Enforce it in dataset selection and input validation rather than expanding scope. |
| OCR noise silently degrades every downstream number | Log the text-acquisition path per page from day one, and measure recall and accuracy split by path. Blended numbers cannot be diagnosed. |

---

## Phase 2 — Vertical slice, no privacy

**Duration:** 1 week

### Objective

A complete, working, demoable product with zero privacy machinery. This is the safety net:
from here on there is always something to show.

### Deliverables

- 2–3 hand-authored form schemas as JSON field specs
- Per-field extraction producing value + confidence + provenance
- PDF filling with a provenance panel
- End-to-end API path: upload → extract → filled PDF
- Field accuracy measured on 10 dev cases

### Implementation tasks

1. Form schema format: field name, label, type, required flag, expected format, policy
   action reference (unused until Phase 4, present in the schema now to avoid a later
   migration).
2. Author the two schemas pinned in `DECISIONS.md` R9: **KYC / Account Opening** and
   **Insurance Policy Application**. **Do not attempt automatic schema extraction from blank
   PDFs** — excluded by `ARCHITECTURE.md` N1, and it will consume the timeline. Do not add a
   third form; each one multiplies Phase 6, not Phase 2.
3. Extraction node: for each field, retrieve → prompt → parse structured output. Direct
   LLM calls for now; the boundary layer arrives in Phase 4.
4. Confidence scoring. Define what the number means and record it in `DECISIONS.md` — a
   confidence score whose semantics are undefined is not usable as a retry trigger later.
5. Abstention path: no supporting evidence → field returns `missing`, empty. Never
   invented (G4).
6. PDF filling with the provenance panel: per field, source document and page.
7. Wire the real API endpoints from the Phase 0 contract.
8. Assemble 10 dev cases (informal; the real dataset is Phase 6). Measure field accuracy.

### Testing requirements

**Written before implementation:**
- Abstention test: a case where a required value is genuinely absent from all documents
  must return `missing`, not a value. **This is the single most important behavioral test
  in the project** — it is the one an interviewer will probe.
- Provenance correctness: the cited page actually contains the extracted value.

**Written alongside:**
- End-to-end smoke test: upload → PDF, no exceptions, all fields have a state
- Field accuracy baseline on 10 dev cases, recorded

### Exit criteria

- [ ] Full path works end to end via the API
- [ ] Abstention and provenance tests green
- [ ] Baseline accuracy recorded in `DECISIONS.md`
- [ ] **Frontend integration checkpoint 1 complete** — teammate's UI hits the real API

### Risks

| Risk | Mitigation |
|------|-----------|
| Extraction prompt tuning expands without bound | Timebox to two days. Record the accuracy achieved and move on; Phase 7 will show whether it matters. |
| Confidence scores are meaningless | Sanity check against the dev cases: low-confidence fields should correlate with actual errors. If not, the Phase 5 retry logic has nothing to trigger on. |

---

## Phase 3 — Policy engine as a standalone library

**Duration:** 1 week

### Objective

Build the policy engine in isolation — importable, testable, and usable via CLI without
the application running.

### Deliverables

- PII detection over text
- Four action implementations: Tokenize (FF1), Generalize, Derive, Pass-through
- Session-scoped pseudonym map with reversal
- Co-occurrence guard enforced at config load
- CLI for manual inspection: text in, protected text out, map dumped

### Implementation tasks

1. Detection layer for the target entity types: Aadhaar, PAN, Passport, account numbers,
   phone, email, names, dates, PIN codes, addresses.
2. Tokenize via FF1, format-preserving per entity type. Reuse the existing gateway
   implementation rather than rewriting it.
3. Generalize: DOB → age band. Age computed against the **form submission date**
   (`DECISIONS.md`), via one function called from one place.
4. Derive: PIN → city/state against the committed, versioned dataset. No network fetch at
   runtime.
5. Session map: same value → same token within a session, different across sessions.
6. Reversal for the inbound path — tokens in the LLM's response map back to original
   values before the result crosses back into the trusted zone.
7. Co-occurrence guard: a config declaring a banned combination fails at load with a
   specific error. **Loud at load, never silent at runtime.**
8. Author the named eval configs: `strict`, `age_state`, `ageband_city`.
9. CLI for manual inspection.

### Testing requirements

**Written before implementation** — this phase's behavior is fully specified by
`ARCHITECTURE.md` §5, so tests come first throughout:

- Determinism: same input → same token within a session
- Session isolation: same input → different tokens across sessions
- FF1 format preservation per entity type (a tokenized PAN still looks like a PAN)
- Reversal round-trip: protect → reverse → original, exactly
- Generalize correctness against the pinned reference date, including edge cases (birthday
  on the submission date, band boundaries)
- Derive correctness against the pinned dataset version; unknown PIN handling is explicit
- Co-occurrence guard: banned-combination config fails at load
- Fail-closed default: a detected sensitive value with no explicit rule gets Tokenize

**Written alongside:**
- Detection recall on a labeled fixture set, per entity type

### Exit criteria

- [ ] Library importable and usable with no app dependency
- [ ] All determinism, reversal, and guard tests green
- [ ] Detection recall recorded per entity type
- [ ] CLI demonstrates all four actions on a sample document

### Risks

| Risk | Mitigation |
|------|-----------|
| Detection misses an entity type and it leaks | Fail-closed default limits blast radius; detection recall is measured, not assumed; Phase 7 adversarial suite probes for gaps |
| Indian PIN dataset has duplicates or gaps | Known issue. Handle unknown PINs explicitly (fall back to Tokenize), and document dataset limitations. |

---

## Phase 4 — Boundary integration

**Duration:** 4–5 days

### Objective

Insert the policy engine at the LLM-call boundary and make privacy mode a first-class
runtime switch.

### Deliverables

- All LLM calls routed through the boundary layer
- `privacy_mode ∈ {none, full_tokenize, policy_engine}` selectable per request
- Outbound payload capture for assertion and debugging
- Phase 2 demo still working, now in all three modes

### Implementation tasks

1. Boundary client wrapping every outbound LLM call. **No node calls an LLM directly after
   this phase** — a single call site is what makes P2 testable.
2. Privacy mode as a request-level parameter threaded through graph state. Build it
   properly now: this switch **is** the ablation mechanism (`ARCHITECTURE.md` D7). Bolted
   on later, the three arms diverge in more than the privacy layer and the comparison
   becomes worthless.
3. Outbound path: protect → send. Inbound path: receive → reverse → return to trusted zone.
4. Payload capture hook: record every outbound request when enabled.
5. Per-field policy actions read from the form schema and active policy config.
6. Re-run the Phase 2 dev cases in all three modes; record accuracy per mode.

### Testing requirements

**Written before implementation:**
- **Outbound PII assertion.** For every dev case in `full_tokenize` and `policy_engine`
  modes, capture all outbound payloads and assert no ground-truth raw PII value appears in
  any of them. This proves the project's central claim (Invariant P2) and should be the
  loudest test in the suite.
- Single call site: no module outside the boundary layer imports the LLM client directly.

**Written alongside:**
- Three-mode parity: all modes complete without error on all dev cases
- Reversal integration: values returned to the user are original, not tokens
- Mode switching does not leak state between requests

### Exit criteria

- [ ] All three modes run end to end
- [ ] Outbound PII assertion green in both protected modes
- [ ] Accuracy per mode recorded on dev cases — first real signal of the privacy cost
- [ ] Demo still works via the UI

### Risks

| Risk | Mitigation |
|------|-----------|
| Accuracy collapses in protected modes | This is a finding, not a failure — it is what the project measures. Investigate which field types broke; that asymmetry is the Phase 7 result. |
| A node bypasses the boundary during later work | Single-call-site test fails CI |

---

## Phase 5 — Orchestration and verifier agent

**Duration:** 1 week

### Objective

Replace the linear Phase 2 flow with the LangGraph graph and add the one genuinely agentic
component.

### Deliverables

- Graph with conditional edges, bounded retries, checkpointing
- Verifier agent with persisted reasoning traces
- Human-review routing and the review endpoint
- Crash-resume working

### Implementation tasks

1. Define graph state: case ID, form schema, per-field records, retry counts, verifier
   traces (`ARCHITECTURE.md` §7).
2. Port Phase 2 extraction and retrieval into nodes. Behavior unchanged — this is a
   restructure, and the Phase 2 tests must still pass afterward.
3. Verifier agent: LLM judge over candidate value + retrieved evidence, returning a
   decision (accept / re-retrieve / escalate) with reasoning.
4. **Persist reasoning traces from the first commit.** They are required input to the
   Phase 7 audit; adding persistence later means re-running everything.
5. Conditional edges per `ARCHITECTURE.md` §7.
6. Bounded retries: fixed budget per field; exhaustion flags rather than loops.
7. Conflict detection: same logical value differing across documents → escalate.
8. Checkpointing and resume.
9. Human-review endpoint per the Phase 0 contract.

### Testing requirements

**Written before implementation:**
- Forced-path tests, with the LLM stubbed so edges are tested deterministically:
  - injected low confidence → retry edge taken
  - conflicting DOBs across two documents → human-review edge taken
  - clean high-confidence field → accept edge, no retry
- Retry bound: repeated failure terminates at the budget, never loops

**Written alongside:**
- Checkpoint resume produces state identical to an uninterrupted run
- Verifier traces persisted and retrievable per field decision
- Human-review endpoint transitions field state correctly

### Exit criteria

- [ ] All conditional edges exercised by deterministic tests
- [ ] Retry bound enforced
- [ ] Resume-after-crash test green
- [ ] Traces persisted for every verifier decision
- [ ] **Frontend integration checkpoint 2 complete** — human-review flow works in the UI

### Risks

| Risk | Mitigation |
|------|-----------|
| Verifier is an if-statement in disguise | If it only thresholds confidence, there is no agent in the project (`ARCHITECTURE.md` §4.1). It must reason over evidence and produce a trace. Review its actual outputs before exiting the phase. |
| Graph refactor breaks Phase 2 behavior | Phase 2 tests are the regression suite; they must pass unchanged |
| Retry loops burn API quota | Bounded budget, tested |

---

## Phase 6 — Evaluation dataset and harness

**Duration:** 1 week

### Objective

Make the full result matrix reproducible with a single command.

### Deliverables

- 50–60 semi-synthetic cases with ground-truth labels
- Deliberately adversarial cases included
- Harness running the full matrix and writing results to disk
- Harness self-test proving it measures something real

### Implementation tasks

1. Identity generator producing consistent synthetic people (name, DOB, PIN, IDs)
   propagated across all of a case's documents.
2. **Constrain the identity pool.** Draw cities from ~8–10 and use a realistic age
   distribution. Identities generated uniformly at random across India make every case
   unique by construction and render the k-anonymity analysis meaningless
   (`ARCHITECTURE.md` §8, Axis 3).
3. Document templates per source type: ID, address proof, income document, academic record.
   Shared across both form types (`ARCHITECTURE.md` D13), which is what keeps two forms
   affordable here.
3a. **A representative subset of documents must be rendered to scanned/photographed form**
   so the OCR path is measured rather than assumed. Representative means: spanning all
   document types, both form types, and both clean-scan and photograph-like conditions —
   not a fixed percentage. Record the resulting count in `DECISIONS.md`. Without this, the
   OCR path ships with zero evaluation evidence behind it.
4. Case assembly: form + supporting documents + ground-truth field values.
5. Adversarial cases, roughly 20% of the set:
   - conflicting values across documents
   - required field absent from all documents
   - near-duplicate names within a case
   - values present but in unusual formats
6. Harness: iterate (privacy mode × case), record per-field results, write structured
   output plus a human-readable summary.
7. Field-type tagging on every field so the Axis 2 matrix can be computed.
8. Separate accounting for **correct abstentions** versus hallucinations — a system that
   fills every field is not better than one that admits ignorance.

### Testing requirements

**Written before implementation:**
- Harness self-test: a deliberately broken extractor stub must produce a visibly bad score.
  A harness that cannot detect a broken system is measuring nothing.

**Written alongside:**
- Generated cases: ground truth is internally consistent across a case's documents
- Determinism: two runs on the same config and seed produce identical numbers
- Adversarial cases are correctly labeled as such
- OCR-rendered documents carry the same ground truth as their source originals, so the two
  paths are directly comparable

### Exit criteria

- [ ] 50–60 cases generated with ground truth, covering both form types
- [ ] ~20% adversarial coverage across all four adversarial types
- [ ] Representative OCR subset rendered; composition and count recorded in `DECISIONS.md`
- [ ] Full matrix runs with one command
- [ ] Harness self-test green
- [ ] Determinism verified

### Risks

| Risk | Mitigation |
|------|-----------|
| Synthetic documents are too clean; results are optimistic | Stated as a limitation (`ARCHITECTURE.md` §11.1). Format variation in the adversarial subset adds some realism. |
| Generation eats the whole week | Timebox templates to two days. Four document types is enough; more types add cost without changing conclusions. |
| API quota exhausted by full-matrix runs | Estimate cost before the first full run. Cache LLM responses by input hash so re-runs are cheap. |

---

## Phase 7 — Results and analysis

**Duration:** 1 week

### Objective

Convert the machinery into the numbers that are the actual differentiator.

### Deliverables

- `RESULTS.md`: accuracy matrix, k-anonymity table, verifier audit, adversarial findings
- Failure-case table with 5–8 concrete examples
- Regression pinning so later refactors cannot silently degrade results

### Implementation tasks

1. Run the full matrix. Produce the (privacy mode × field type) accuracy table with
   abstentions counted separately. Report accuracy on OCR-derived text separately from
   native text, so OCR error and extraction error remain distinguishable
   (`ARCHITECTURE.md` §11.6).
2. k-anonymity analysis for `strict`, `age_state`, `ageband_city`. Report **minimum k,
   median k, and percentage of cases at k=1** per config. Compare against the k ≥ 5
   threshold fixed in `DECISIONS.md`.
   A config falling below threshold is a **result**: report it, and disable that config by
   default with a one-line rationale.
3. Verifier audit: manually review 10–15 ambiguous cases using persisted traces. Compute
   precision on flagged and recall on should-have-been-flagged.
4. Adversarial suite: prompt-injection attempts to elicit raw values, reversal attacks,
   detection-evasion via unusual formatting. **Report unfixed bypasses plainly** — this is
   a credibility asset, not a weakness.
5. Failure-case table: 5–8 concrete failures with the case, the field, what happened, and
   the diagnosis.
6. Write `RESULTS.md` leading with the headline number: the accuracy cost of the privacy
   layer, and which field types absorbed it.
7. Pin regression thresholds in CI against the measured baselines.

### Testing requirements

- Analysis scripts are deterministic and reproducible from stored run output
- k-anonymity computation validated against a small hand-computed fixture
- Regression tests fail if accuracy drops below the pinned baseline

### Exit criteria

- [ ] `RESULTS.md` complete with all four result sections
- [ ] Every claim traceable to a stored run
- [ ] Unfixed bypasses documented
- [ ] Regression thresholds in CI

### Risks

| Risk | Mitigation |
|------|-----------|
| Results are unflattering | Report them. A measured 8% accuracy cost with a per-field-type breakdown is a stronger artifact than an unmeasured claim of no cost. |
| Temptation to adjust the k-threshold after seeing results | It is fixed in `DECISIONS.md` in Phase 0 specifically to prevent this. Moving it now is visible and costs more credibility than a failing config does. |

---

## Phase 8 — Deploy, documentation, buffer

**Duration:** 4–5 days

### Objective

One URL, one click, no setup. Documentation that leads with results.

### Deliverables

- Single container: React build mounted by FastAPI, deployed to one target
- README leading with results
- 3-minute demo video
- Populated sample case for instant demo

### Implementation tasks

1. Multi-stage Docker build: frontend build → static assets → FastAPI image.
2. Deploy to one host. **Check cold-start behavior on the chosen free tier** — a reviewer
   clicking into a 40-second cold start forms their impression during the wait. If it is
   bad, use a keep-alive ping.
3. Pre-load a sample case so the demo works without the reviewer uploading anything.
4. README: what it does, the headline results table, architecture diagram, how to run,
   explicit statement of what you built versus what the frontend developer built.
5. Demo video, 3 minutes, results-first. A meaningful fraction of reviewers never click the
   link.
6. Verify all three privacy modes work in the deployed environment.

### Testing requirements

- Deployed smoke test: full path against the live URL
- Container starts clean with no local state
- All three privacy modes functional in production

### Exit criteria

- [ ] Live URL, working demo, no setup required
- [ ] Sample case pre-loaded
- [ ] README and demo video complete
- [ ] Ownership stated explicitly
- [ ] Repo clean: no secrets, no dead code, no `TBD`

### Risks

| Risk | Mitigation |
|------|-----------|
| Free-tier cold start ruins first impression | Test early in the phase, not on the last day. Keep-alive ping if needed. |
| API keys exposed in the client bundle | All LLM calls are server-side by design. Verify the built bundle contains no keys before deploying. |
| Demo quota exhausted by reviewers | Rate-limit the public demo; cache the sample case's results. |

---

## Global risks

| Risk | Mitigation |
|------|-----------|
| **Evaluation gets cut under time pressure** | The most likely failure mode, and the most costly. Phases 6–7 are the differentiator; if time runs short, cut a form schema or a document type instead. |
| Frontend integration slips to the end | Two mandatory checkpoints, Phases 2 and 5, against the real API |
| Scope creep via "one more feature" | `ARCHITECTURE.md` §2 non-goals are the reference. Adding a component requires a discovered blocker, not a preference. |
| LLM API cost | Cache by input hash; estimate full-matrix cost before the first run |
| Dependency breakage mid-project | Versions pinned in Phase 0; lockfile committed |

---

*Phases are sequential. Milestones are cumulative. The application is demoable from the
end of Phase 2 onward.*
