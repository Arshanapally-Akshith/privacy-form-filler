# ARCHITECTURE.md

**Project:** Privacy-Preserving AI Document Automation
**Status:** Design frozen. Implementation not started.
**Document owner:** Akshith
**Last updated:** 2026-07-26

---

## 1. Summary

A document automation system that fills structured forms from a set of supporting
documents, where every value written into the form is traceable to a specific page of a
specific source document, and where no raw personally identifiable information is ever
transmitted to a third-party language model.

The system is built on top of an existing OpenAI-compatible privacy gateway. The gateway
provides the pseudonymization mechanism; this project provides a realistic, PII-dense
workload on top of it and measures what the privacy layer costs in accuracy.

**One-sentence positioning:** an OpenAI-compatible privacy gateway, plus a document
form-filling workload built on it to demonstrate that the privacy layer does not destroy
downstream task performance.

---

## 2. Goals and non-goals

### Goals

| # | Goal |
|---|------|
| G1 | Fill a structured form from supporting documents with per-field provenance (source document + page). |
| G2 | Guarantee that no raw PII leaves the trusted boundary in any request to an external LLM. |
| G3 | Preserve downstream reasoning capability where possible via derived and generalized attributes, rather than blanket tokenization. |
| G4 | Abstain rather than hallucinate: a field whose value cannot be evidenced is left empty and flagged, never invented. |
| G5 | Quantify the accuracy cost of privacy protection across privacy modes and field types. |
| G6 | Quantify the re-identification risk introduced by exposing derived attributes. |
| G7 | Deploy as a single container reachable from one URL, with no client-side setup. |

### Non-goals

These are deliberately out of scope. Each is excluded for a stated reason, not by oversight.

| # | Non-goal | Reason |
|---|----------|--------|
| N1 | Automatic schema extraction from arbitrary blank PDF forms | This is an open research problem. Form schemas — the *blank form's* field definitions — are hand-authored JSON, authored once per supported form type by the developer. This does not apply to user data: users upload unstructured documents only and never supply structured input (§6.1). |
| N2 | Multi-tenant auth, user accounts, billing | No bearing on the technical claims being made. |
| N3 | Handwritten content, and OCR robustness on poor-quality scans | Printed and cleanly scanned documents are in scope. Handwriting recognition and degraded-scan robustness are separate problems that would consume the timeline without strengthening any claim the project makes. |
| N4 | Formal privacy guarantees (differential privacy, provable anonymity) | The re-identification analysis is a bounded empirical sanity check, not a security audit. Claimed as such throughout. |
| N5 | Horizontal scaling, queueing, multi-worker orchestration | Single-container deployment is a stated constraint, not a limitation to apologize for. |

### Demonstration scope

The engine is **form-agnostic**. Form schemas are data, and no component contains
domain-specific logic. Two form types are implemented to demonstrate the system; the
specific choices are pinned in `DECISIONS.md` R9 as implementation choices and can change
without any architectural change.

| Form | Demonstrates |
|------|-------------|
| KYC / Account Opening | Identifier-dense workload — Tokenize under maximum load |
| Insurance Policy Application | Age band and city are the *functionally correct* inputs (premium tier, risk zone), so Generalize and Derive cost nothing |

The pairing is deliberate: the first form shows what tokenization costs, the second shows a
case where generalization costs nothing because the coarse value was all the form ever
required. Both draw on a shared document pool (identity, address proof, income), which keeps
dataset generation cost roughly flat across the two.

---

## 3. Trust boundary

The trust boundary is the single most important concept in this system. Every design
decision below follows from it.

```
╔══════════════════════════════ TRUSTED BOUNDARY ══════════════════════════════╗
║                                                                              ║
║   Uploaded documents (raw PII)                                               ║
║   Document parsing and chunking                                              ║
║   Vector store and retrieval                                                 ║
║   Field extraction orchestration                                             ║
║   Session-scoped pseudonym map (reversible)                                  ║
║   Filled output PDF                                                          ║
║                                                                              ║
╚═══════════════════════════════════╤══════════════════════════════════════════╝
                                    │
                      Privacy Policy Engine applies HERE
                      (outbound: protect · inbound: reverse)
                                    │
                    ════════════════▼═══════════════════
                         UNTRUSTED — external LLM
                    ═══════════════════════════════════
```

### Invariant P1 — Boundary placement

> The Privacy Policy Engine is applied **only** at the LLM-call boundary. It is never
> applied before retrieval, before parsing, or before storage.

**Rationale.** Retrieval is semantic. A pseudonym token carries no semantic relationship
to the query that would locate it — a token replacing a person's name does not embed near
the label "applicant name" the way the real name does. Pseudonymizing before retrieval
would degrade recall for reasons that have nothing to do with privacy, and would
contaminate the accuracy measurements this project exists to produce.

**Enforcement.** This invariant is enforced mechanically, not by discipline: an
import-boundary test asserts that no privacy module is reachable from the retrieval
package's import graph. The test fails CI if the invariant is violated.

### Invariant P2 — No raw PII outbound

> In `policy_engine` and `full_tokenize` modes, no raw PII value appears in any payload
> sent to an external LLM.

**Enforcement.** Every outbound request is captured during evaluation runs and asserted
against the ground-truth PII values for that case. This is the loudest test in the suite,
because it is the project's central claim.

---

## 4. Component architecture

```
                        [React SPA]
                             │  built to static assets
                             ▼
        ┌────────────────────────────────────────────────┐
        │                  FastAPI                       │   single container
        │   static mount  ·  REST API  ·  OpenAI-compat  │
        └────────────────────────┬───────────────────────┘
                                 │
                                 ▼
        ┌────────────────────────────────────────────────┐
        │              LangGraph orchestrator            │
        │   state · conditional edges · retries ·        │
        │   checkpoints · human-review routing           │
        └───┬──────────────┬──────────────┬──────────────┘
            │              │              │
            ▼              ▼              ▼
   ┌────────────┐  ┌──────────────┐  ┌──────────────────┐
   │   Field    │  │  Per-field   │  │    Verifier      │
   │ Extractor  │  │  Retriever   │  │   (LLM judge)    │
   │  (node)    │  │   (node)     │  │    — AGENT       │
   └────────────┘  └──────┬───────┘  └──────────────────┘
                          │
                          ▼
                 ┌─────────────────┐
                 │  Vector store   │  raw text, inside boundary
                 │  + page index   │
                 └─────────────────┘

   All LLM calls from any node route through:
        ┌────────────────────────────────────────────┐
        │         Privacy Policy Engine              │
        │  Tokenize │ Generalize │ Derive │ Pass     │
        │  session-scoped map · co-occurrence guard  │
        └────────────────────────────────────────────┘
                          │
                          ▼
                   external LLM API
```

### 4.1 Node / agent classification

A component is an **agent** only if it performs autonomous reasoning that determines
control flow. Everything else is a node. This distinction is applied honestly, because
overclaiming it is more damaging than underclaiming it.

| Component | Classification | Justification |
|-----------|---------------|---------------|
| Field Extractor | Node | Iterates a fixed, hand-authored form schema. No planning, no tool selection, no branching decisions. |
| Per-field Retriever | Node | Deterministic retrieval call per field. Acts, but does not decide. |
| PDF Filler | Node | Pure output rendering. |
| Policy Engine | Service | Config-driven transformation. Deterministic by requirement. |
| **Verifier** | **Agent** | Reasons over ambiguous evidence — conflicting values across documents, low-confidence extractions — and decides whether to re-retrieve, accept, or route to human review. Its output determines the graph's next edge. |

**System description used in writing and in interviews:**
a LangGraph-orchestrated pipeline with a single LLM-judge agent, not a multi-agent system.

---

## 5. Privacy Policy Engine

### 5.1 Actions

Four actions, deliberately distinguished. The Generalize/Derive split exists because the
two have different leakage profiles and collapsing them would hide that difference.

| Action | Definition | Example | Reversible |
|--------|-----------|---------|------------|
| **Tokenize** | Replace value with a format-preserving ciphertext (FF1). Same input → same token within a session. | Aadhaar, PAN, Passport, account numbers | Yes, via session map |
| **Generalize** | Coarsen the *same* attribute so it carries less information. Output is a lossy version of the input's own semantic content. | DOB → age band | No |
| **Derive** | Compute a *different* attribute via a deterministic trusted lookup. Output is categorically different information, not a coarser version of the input. | PIN code → city, state | No |
| **Pass-through** | Transmit unchanged. | Non-sensitive labels, form metadata | N/A |

**Why the split matters.** A generalized attribute's information loss is bounded and easy
to reason about — an age band of width 10 always maps at least ten years of DOB values to
one output. A derived attribute's leakage depends on the cardinality of the lookup: a PIN
code in a dense metro maps to a city shared by millions, but a rural PIN may map to a town
of a few thousand, narrowing the individual far more than the age band would. Treating both
as one bucket would make the re-identification analysis meaningless.

**Constraint on both:** only deterministic transformations and trusted lookups are
permitted. No probabilistic inference, no LLM-based derivation. A derived attribute must be
reproducible from the input and the pinned dataset version alone.

### 5.2 Co-occurrence guard

Individually harmless derived attributes recombine into a quasi-identifier — the classic
result being that a small combination of coarse demographic attributes can uniquely
identify a large share of a population. The engine therefore constrains which derived and
generalized attributes may be exposed *together* within a session.

Policy configs declare permitted combinations explicitly. A config that would expose a
banned combination **fails at load time with a loud error**, not silently at runtime. This
is validated by test.

### 5.3 Policy configuration

Policy is data, not code. Configs are versioned JSON validated against a committed schema.
Each field in a form schema maps to a policy action; a default action applies to detected
PII not covered by an explicit rule, and that default is **Tokenize** — fail closed.

Named configs used in evaluation (illustrative, finalized during Phase 6):

| Config | Exposes |
|--------|---------|
| `strict` | Nothing derived. Everything sensitive tokenized. |
| `age_state` | Age band + state |
| `ageband_city` | Age band + city |

### 5.4 Pinned decisions

These affect reproducibility of every number the project reports and are therefore recorded
here rather than left to implementation.

| Decision | Value |
|----------|-------|
| Age reference date | **The form's creation/submission date**, not the source document's date. Rationale: a case has multiple source documents with differing dates, which would make age retrieval-order dependent; a form asking for "age" means age at submission. Implemented as one function, called from one place. |
| Age band width | 10 years, aligned to decade boundaries |
| PIN → city/state dataset | Pinned source and version recorded in `DECISIONS.md`; the file is committed, not fetched at runtime |
| Tokenization scheme | FF1 format-preserving encryption |
| Pseudonym map scope | Session-scoped. Same value → same token within a session; different tokens across sessions. |

---

## 6. Ingestion and retrieval

### 6.1 Ingestion

Users upload unstructured documents only — text PDFs, scanned PDFs, and photographs of
documents. Users never supply structured data about themselves. All structure is derived by
the system.

Text acquisition is handled inside the existing parser abstraction, which normalizes every
input to `(text, page_number, document_id)` regardless of source format. **OCR is a strategy
within that abstraction, not a separate architectural component**, so nothing downstream
knows or cares how a page's text was obtained.

**Routing rule.** Native text extraction is attempted first. OCR is used as a fallback when
a page's text layer is absent or insufficient — the sufficiency threshold is a pinned
constant in `DECISIONS.md`, not an inline literal. Partial-text-layer PDFs are therefore
handled per page rather than per document, since a single document may mix machine-generated
and scanned pages.

**Measured separately.** Extraction accuracy is reported for OCR-derived text and native
text independently (§8). Without that split, OCR noise and extraction error blend into one
number and neither can be diagnosed.

### 6.2 Retrieval

Per-field evidence retrieval, not conversational RAG. For each field in the form schema,
the retriever issues a query derived from the field label and returns ranked chunks from
the case's documents.

- **Chunking** preserves source document identity and page number on every chunk. This is
  not metadata decoration — provenance display (G1) depends on it, and a chunk without a
  page reference is unusable.
- **Scope** is per-case. A case's vector index contains only that case's documents.
- **Extraction** is grounded strictly in retrieved chunks. If no chunk supports a value,
  the field returns empty-flagged (G4).
- **Measured early.** Recall@k on hand-labeled field→chunk pairs is measured in Phase 1,
  before anything is built on top of it, because every downstream accuracy number inherits
  retrieval quality.

---

## 7. Orchestration

LangGraph is used for orchestration, retries, checkpointing, and human-review routing.
It is not used to make a linear pipeline appear agentic.

**Graph state** carries: case ID, form schema, per-field records (value, confidence,
provenance, state), retry counts, and verifier reasoning traces.

**Conditional edges** — the reason the graph exists rather than a function chain:

| Condition | Edge |
|-----------|------|
| Verifier accepts | → next field / PDF filler |
| Verifier rejects, retries remaining | → re-retrieve with adjusted query |
| Verifier detects conflicting values across documents | → human review |
| Retry budget exhausted | → flag field, continue |

**Bounded retries.** Retry budget is fixed per field. Exhaustion flags the field rather
than looping.

**Checkpointing.** Graph state is checkpointed so a crashed run resumes without
re-processing completed fields. Resume-produces-identical-state is a test, not an
assumption.

### 7.1 Verifier agent

The only genuinely agentic component. An LLM judge that examines a candidate value
alongside its retrieved evidence and decides: accept, re-retrieve, or escalate to human
review.

**Reasoning traces are persisted per decision from the first commit.** They are required
input to the verifier audit in evaluation; capturing them later would mean re-running
everything.

**The verifier is itself evaluated.** A judge whose own accuracy is unknown is not a
quality control, and "how do you know your judge is any good" is a question that will be
asked. Metrics and sample size are pinned before implementation to avoid retrofitting
favorable criteria — see §8.

---

## 8. Evaluation methodology

Detailed results live in `RESULTS.md`. This section fixes the methodology so it cannot
drift once numbers start appearing.

**Dataset.** 50–60 semi-synthetic cases: template documents populated with generated
identities, which is the only tractable way to obtain ground-truth labels at this volume
solo. Deliberately includes adversarial-to-the-system cases: conflicting values across
documents, missing required fields, near-duplicate names.

**Axis 1 — Privacy mode.** `none` / `full_tokenize` / `policy_engine`.
The mode is a first-class runtime switch, built as such in Phase 4 rather than retrofitted,
so that the three arms differ **only** in the privacy layer. Retrofitting the switch would
produce three subtly divergent code paths and make the comparison worthless.

**Axis 2 — Field type.** Identifier / Date / Location / Name / Numeric-financial.
Crossed with Axis 1 to produce a field-accuracy matrix. The expected and interesting result
is asymmetry: identifiers should cost nothing because they were never used for reasoning,
while location and date fields should show measurable degradation under tokenization and
recovery under Derive/Generalize.

Reported alongside accuracy: **correct abstentions** (field genuinely unavailable and
correctly left empty) counted separately from hallucinations. A system that fills every
field is not better than one that admits ignorance.

**Axis 3 — Re-identification.** k-anonymity check across the evaluation dataset for 2–3
named policy configs. For each case, count how many other cases share the same exposed
attribute combination; report **minimum k per config**.

Scope is deliberately bounded and stated as such: this is an empirical sanity check on a
synthetic dataset, not a security audit, and it does not constitute a formal anonymity
guarantee. The k-threshold and its justification are fixed in `DECISIONS.md` **before**
results are generated.

**Verifier audit.** Manual review of 10–15 ambiguous cases. Precision on "flagged as
low-confidence," recall on "should have been flagged but wasn't." Sample size and metrics
pinned before implementation.

**Adversarial suite.** Separate from the re-identification analysis, and kept separate in
reporting. The adversarial suite tests whether the *mechanism* can be bypassed (prompt
injection attempting to elicit raw values, reversal attacks). The re-identification
analysis tests whether the *policy design* leaks even when the mechanism works perfectly.
These are different failure classes — an implementation bug versus a design-level limit —
and conflating them would signal not understanding the distinction. **Unfixed bypasses are
reported plainly.**

---

## 9. Deployment

Single container, single deployment target, single URL.

```
React build  ──►  static assets  ──►  mounted by FastAPI  ──►  one image  ──►  one host
```

**Rationale.** The demo constraint is one click, no setup. A split frontend/backend
deployment reintroduces CORS configuration, two hosts, and free-tier cold-start latency on
the first request — which is precisely the moment a reviewer decides whether to keep
looking.

**Cross-team contract.** The static build output path and build command are agreed between
backend and frontend owners in Phase 0, not at integration time. This is a backend routing
concern as much as a frontend one.

**Frontend ownership.** The React SPA is built by a teammate against a frozen OpenAPI
contract. Ownership is stated explicitly in the README rather than left ambiguous.

---

## 10. Engineering decisions

Decisions with their rejected alternatives. The rejected column is the part that
demonstrates judgment.

| # | Decision | Rejected alternative | Reason |
|---|----------|---------------------|--------|
| D1 | Policy engine at LLM boundary only | Pseudonymize documents at ingestion | Destroys retrieval quality; contaminates the accuracy measurement. See Invariant P1. |
| D2 | Four distinct policy actions | Single "redact" action | Blanket redaction breaks any field requiring the actual value; measuring the difference is the project's central result. |
| D3 | Generalize and Derive as separate actions | One combined action | Different leakage profiles; combining them makes the k-anonymity analysis uninterpretable. |
| D4 | Hand-authored form schemas | Automatic schema extraction from blank PDFs | Open research problem; would consume the entire timeline and is not what is being demonstrated. |
| D5 | One agent (verifier), everything else nodes | Label every component an agent | Overclaiming collapses under one follow-up question. The honest description is stronger. |
| D6 | Single container, static-served SPA | Separate frontend/backend hosts | CORS, two deploy targets, cold-start latency on first impression. |
| D7 | Privacy mode as first-class runtime switch | Separate branches or builds per mode | Ablation arms must differ only in the privacy layer, or the comparison means nothing. |
| D8 | Semi-synthetic evaluation dataset | Real documents | Ground-truth labels at n=50+ are not obtainable solo from real documents; synthetic generation also avoids handling real PII. Stated as a limitation. |
| D9 | Fail-closed default policy (Tokenize) | Fail-open (pass-through) | Undetected-but-sensitive values must not leak by default. |
| D10 | Age from form submission date | Source document date | Multiple source documents produce multiple dates, making age retrieval-order dependent and results irreproducible. |
| D11 | OCR as a strategy inside the parser abstraction | OCR as a separate pipeline component | Downstream code already consumes a normalized `(text, page_number, document_id)` tuple; exposing text-acquisition method upward would add coupling for no gain. |
| D12 | Native text first, OCR on fallback, decided per page | OCR everything uniformly | OCR on machine-generated text is slower and strictly less accurate. Per-page decisions handle mixed documents, which are the common real-world case. |
| D13 | Two demonstration forms across two industries, shared document pool | One industry; or three or more forms | One industry invites "does this only work for banking"; each extra form multiplies Phase 6 dataset generation, not Phase 2 schema authoring. |

---

## 11. Known limitations

Stated here so they are volunteered rather than discovered.

1. **Synthetic evaluation data.** Results characterize the system on generated documents;
   real-world document noise is not represented.
2. **Re-identification analysis is bounded.** A k-anonymity check on a synthetic dataset of
   ~50 cases. Not a formal privacy guarantee, and small-n makes k values optimistic
   relative to a real population.
3. **Derived attributes leak by design.** Exposing city or age band is a deliberate
   accuracy/privacy trade; the co-occurrence guard limits but does not eliminate the risk.
4. **Verifier is an LLM and can be wrong.** Its error rate is measured and reported rather
   than assumed to be zero.
5. **Form schemas are hand-authored**, limiting the system to prepared form types.
6. **OCR errors propagate into extraction accuracy.** Printed and cleanly scanned documents
   are supported; handwritten content and poor-quality scans are out of scope (N3). Accuracy
   on OCR-derived text is measured and reported separately from native-text accuracy so the
   two error sources remain distinguishable.
7. **Session-scoped pseudonym map lives in process memory**, consistent with
   single-container deployment. Not durable across restarts.
8. **Adversarial suite is not exhaustive.** Unfixed bypasses are reported.

---

## 12. Open items before implementation

| Item | Owner | Blocking phase |
|------|-------|---------------|
| Confirm or override age reference date (§5.4 D10) | Akshith | Phase 0 |
| Fix k-threshold and written justification | Akshith | Phase 0 |
| Pin PIN→city/state dataset source and version | Akshith | Phase 0 |
| Freeze OpenAPI contract and send to frontend owner | Akshith | Phase 0 |
| Agree static build output path and build command | Both | Phase 0 |

---

*Architecture frozen. Changes from this point require a discovered implementation blocker,
not a preference.*
