# DECISIONS.md

Single source of truth for pinned engineering constants and measured values.

**Rules for this document**

- Every value here is derived from `ARCHITECTURE.md` or `BUILD.md`. Nothing is invented here.
- Code reads these values from config or a single named constant. No duplicated literals.
- `TBD` means intentionally unresolved, with the phase that resolves it named.
- Measured values are filled in when measured, never estimated in advance.
- Changing a pinned value after it has been used to produce a measurement requires
  re-running the affected measurement. Note the change in §6.

| | |
|---|---|
| **Status** | Phase 1, in progress |
| **Last updated** | 2026-07-26 |
| **Last change** | OCR engine pinned; text-layer threshold and minimum image resolution provisionally pinned (E10-E12); see §6 |

---

## 1. Engineering constants

| ID | Constant | Value | Source |
|----|----------|-------|--------|
| E1 | Age reference date | Form submission/creation date | ARCH §5.4, D10 |
| E2 | Age band width | 10 years, decade-aligned | ARCH §5.4 |
| E3 | Tokenization scheme | FF1 format-preserving encryption | ARCH §5.4 |
| E4 | Pseudonym map scope | Session-scoped | ARCH §5.4 |
| E5 | Default policy action for detected sensitive values without an explicit rule | Tokenize (fail closed) | ARCH §5.3, D9 |
| E6 | Retry budget per field | **TBD — Phase 5** | ARCH §7 requires a fixed budget; no value specified in any document |
| E7 | Confidence score definition | **TBD — Phase 2** | BUILD P2, task 4 |
| E8 | Embedding provider and model | **TBD — Phase 1** | BUILD P1, task 3 |
| E9 | LLM provider and model | **TBD — Phase 1** | Implied by BUILD P1/P2; not specified |
| E10 | OCR engine | Tesseract, via `pytesseract`. CPU-only; no GPU runtime pulled in (unlike e.g. EasyOCR). | ARCH §6.1, D11; BUILD P1 task 1a |
| E11 | Text-layer sufficiency threshold for OCR fallback | **Provisional: 20 characters.** Native page text shorter than this triggers OCR. Validated only against the golden-file fixtures in `tests/ingest/`; not yet confirmed against a broader document sample. Do not treat as final. | ARCH §6.1; BUILD P1 task 1b |
| E12 | Minimum accepted image resolution | **Provisional: 600×600 px.** Standalone images below this on either dimension are rejected outright rather than sent through OCR. Validated only against the golden-file fixtures in `tests/ingest/`; not yet confirmed against a broader document sample. Do not treat as final. | BUILD P1 task 1c |

E6–E12 are unresolved by design. Do not infer them; pin them in the stated phase and record
them here in the same commit.

---

## 2. Privacy decisions

| ID | Decision | Value | Source |
|----|----------|-------|--------|
| P1 | Policy actions | Tokenize / Generalize / Derive / Pass-through | ARCH §5.1 |
| P2 | Tokenized entity types | Aadhaar, PAN, Passport, account numbers | ARCH §5.1 |
| P3 | Generalize mapping | DOB → age band (per E1, E2) | ARCH §5.1 |
| P4 | Derive mapping | PIN code → city, state | ARCH §5.1 |
| P5 | Permitted transformation class | Deterministic transformations and trusted lookups only. No probabilistic inference, no LLM-based derivation. | ARCH §5.1 |
| P6 | PIN → city/state dataset source | "All India Pincode Directory till last month", published by Department of Posts, Ministry of Communications, Government of India, via data.gov.in (Open Government Data Platform India). API resource id `5c2f62fe-5afa-4119-a499-fec9d604d5bd`. License: Government Open Data License – India (GODL-India). Dataset page: https://www.data.gov.in/resource/all-india-pincode-directory-till-last-month | ARCH §5.4 |
| P7 | PIN → city/state dataset version | No formal version number is published by the source. Pinned by retrieval date (P8) and the dataset's own last-updated timestamp per API metadata: 2025-10-03T04:04:14Z. 165,627 rows / 19,586 unique pincodes at retrieval. | ARCH §5.4 |
| P8 | PIN dataset retrieval date | 2026-07-26 | ARCH §5.4 |
| P9 | PIN dataset committed path | `app/config/data/pincode_district_state.csv` (derived: pincode, district, statename only, deduplicated to 21,162 rows). Attribution and derivation notes: `app/config/data/PINCODE_DATASET_ATTRIBUTION.md`. | ARCH §5.4 |
| P10 | Unknown PIN handling | Explicit fallback to Tokenize; never silently passed through | BUILD P3 risk table; CLAUDE §5 |
| P11 | Co-occurrence violation behavior | Fails at config load with a specific error | ARCH §5.2 |
| P12 | Detected entity types | Aadhaar, PAN, Passport, account numbers, phone, email, names, dates, PIN codes, addresses | BUILD P3, task 1 |

### Named policy configs

| Config | Exposes | Source |
|--------|---------|--------|
| `strict` | Nothing derived; all sensitive values tokenized | ARCH §5.3 |
| `age_state` | Age band + state | ARCH §5.3 |
| `ageband_city` | Age band + city | ARCH §5.3 |

---

## 3. Evaluation decisions

| ID | Decision | Value | Source |
|----|----------|-------|--------|
| V1 | Privacy modes | `none` / `full_tokenize` / `policy_engine` | ARCH §8 |
| V2 | Field types | Identifier / Date / Location / Name / Numeric-financial | ARCH §8 |
| V3 | Dataset size | 50–60 semi-synthetic cases | ARCH §8 |
| V4 | Adversarial case proportion | ~20% of the dataset | BUILD P6, task 5 |
| V5 | Adversarial case types | Conflicting values across documents; required field absent; near-duplicate names; unusual value formats | BUILD P6, task 5 |
| V6 | Identity pool constraint | ~8–10 cities, realistic age distribution | BUILD P6, task 2 |
| V7 | k-anonymity threshold | **k ≥ 5** | ARCH §8; fixed before results per ARCH §8 |
| V8 | k-anonymity reported statistics | Minimum k, median k, percentage of cases at k = 1 | BUILD P7, task 2 |
| V9 | Configs analyzed for re-identification | `strict`, `age_state`, `ageband_city` | BUILD P7, task 2 |
| V10 | Below-threshold config handling | Reported as a result; config disabled by default with a one-line rationale | BUILD P7, task 2 |
| V11 | Verifier audit sample size | 10–15 ambiguous cases | ARCH §7.1, §8 |
| V12 | Verifier audit metrics | Precision on flagged as low-confidence; recall on should-have-been-flagged | ARCH §8 |
| V13 | Abstention accounting | Correct abstentions counted separately from hallucinations | ARCH §8 |
| V14 | Adversarial suite scope | Prompt injection to elicit raw values; reversal attacks; detection evasion via formatting | BUILD P7, task 4 |
| V15 | Unfixed bypass policy | Reported plainly in `RESULTS.md` | ARCH §8 |
| V16 | Failure-case table size | 5–8 concrete examples | BUILD P7, task 5 |
| V17 | OCR evaluation coverage | A **representative subset** of documents rendered to scanned/photographed form — spanning all document types, both form types, and both clean-scan and photograph-like conditions. Deliberately not a fixed percentage; composition and count recorded in §5 once generated. | BUILD P6 task 3a |
| V18 | OCR accuracy reporting | Accuracy on OCR-derived text reported separately from native text | ARCH §6.1, §11.6; BUILD P7 task 1 |

**V7 is frozen.** It was fixed before any result was generated, specifically so it cannot be
adjusted afterward. Moving it post-hoc is a documented anti-pattern for this project
(BUILD P7 risk table).

---

## 4. Runtime decisions

| ID | Decision | Value | Source |
|----|----------|-------|--------|
| R1 | Deployment model | Single container; React build served as static assets by FastAPI | ARCH §9 |
| R2 | Field state enum | `filled` / `low_confidence` / `conflict` / `missing` / `human_reviewed` | BUILD P0, task 4 |
| R3 | Privacy mode selection | Request-level parameter, threaded through graph state | BUILD P4, task 2 |
| R4 | Retrieval scope | Per-case index; cross-case retrieval is a correctness failure | ARCH §6 |
| R5 | Chunk provenance | Every chunk carries non-null `document_id` and `page_number` | ARCH §6 |
| R6 | Verifier decisions | Accept / re-retrieve / escalate to human review | ARCH §7.1 |
| R7 | Verifier trace persistence | Persisted per decision from first commit | ARCH §7.1 |
| R8 | Session map durability | Process memory; not durable across restarts | ARCH §11.7 |
| R9 | Implemented form schemas | **KYC / Account Opening** and **Insurance Policy Application**. Hand-authored JSON, two only. Implementation choice; the engine is form-agnostic and this can change without architectural change. | ARCH §2 Demonstration scope, D13 |
| R9a | Accepted input formats | Text PDF, scanned PDF, JPG, PNG. Unstructured documents only; users never supply structured personal data. | ARCH §6.1 |
| R9b | Text acquisition routing | Native text first; OCR fallback when a page's text layer is absent or below E11. Decided per page, not per document. | ARCH §6.1, D12 |
| R9c | Text-acquisition path logging | Recorded per page, so OCR error and extraction error stay separable | BUILD P1 task 1b |
| R10 | LLM response caching | Cached by input hash for evaluation runs | BUILD P6 risk table |
| R11 | Static build output path | `frontend/dist/` (Vite default) | ARCH §9, BUILD P0 task 6 |
| R12 | Frontend build command | `npm run build` (Vite) | BUILD P0, task 6 |
| R12a | Same-origin serving | SPA served from the same origin as the API — FastAPI mounts `frontend/dist/` as static assets in the same container/process (R1). No separate frontend host, no CORS configuration needed. | ARCH §9, BUILD P0 task 6 |
| R13 | Deployment host | **TBD — Phase 8** | BUILD P8, task 2 |

---

## 5. Measurements

Filled in when measured. Do not estimate. Every value must be reproducible from a stored
run.

### Phase 1

| Metric | Value | Date |
|--------|-------|------|
| Retrieval recall@k (k = TBD) on ~20 hand-labeled pairs | *pending* | |
| Labeled pair count | *pending* | |
| Recall@k — native-text documents | *pending* | |
| Recall@k — OCR-derived documents | *pending* | |

### Phase 2

| Metric | Value | Date |
|--------|-------|------|
| Field accuracy, 10 dev cases, no privacy layer | *pending* | |
| Correct abstention rate | *pending* | |

### Phase 3

| Metric | Value | Date |
|--------|-------|------|
| Detection recall — Aadhaar | *pending* | |
| Detection recall — PAN | *pending* | |
| Detection recall — Passport | *pending* | |
| Detection recall — account number | *pending* | |
| Detection recall — phone | *pending* | |
| Detection recall — email | *pending* | |
| Detection recall — name | *pending* | |
| Detection recall — date | *pending* | |
| Detection recall — PIN code | *pending* | |
| Detection recall — address | *pending* | |

### Phase 4

| Metric | Value | Date |
|--------|-------|------|
| Dev-case accuracy — `none` | *pending* | |
| Dev-case accuracy — `full_tokenize` | *pending* | |
| Dev-case accuracy — `policy_engine` | *pending* | |

### Phase 6

| Metric | Value | Date |
|--------|-------|------|
| Final dataset case count | *pending* | |
| Case count per form type (KYC / Insurance) | *pending* | |
| Adversarial case count | *pending* | |
| OCR-rendered document count and composition | *pending* | |
| Estimated LLM calls per full-matrix run | *pending* | |

### Phase 7

Full results live in `RESULTS.md`. Headline values are mirrored here.

| Metric | Value | Date |
|--------|-------|------|
| Accuracy delta, `none` → `policy_engine` | *pending* | |
| Field type with largest degradation | *pending* | |
| Accuracy — native-text documents | *pending* | |
| Accuracy — OCR-derived documents | *pending* | |
| Minimum k — `strict` | *pending* | |
| Minimum k — `age_state` | *pending* | |
| Minimum k — `ageband_city` | *pending* | |
| Configs below k ≥ 5 | *pending* | |
| Verifier precision | *pending* | |
| Verifier recall | *pending* | |
| Unfixed bypass count | *pending* | |

### CI regression thresholds

Pinned in Phase 7 from measured baselines. Do not lower to make CI pass (CLAUDE §4).

| Threshold | Value | Date |
|-----------|-------|------|
| Minimum field accuracy, `policy_engine` | *pending* | |
| Minimum retrieval recall@k | *pending* | |

---

## 6. Change log

Any change to a pinned value after it has produced a measurement is recorded here with the
affected measurements re-run.

| Date | ID | From | To | Reason | Measurements re-run |
|------|----|------|----|--------|---------------------|
| 2026-07-26 | R9 | "2–3 hand-authored schemas" | KYC / Account Opening + Insurance Policy Application | Demonstration forms frozen. Implementation choice; engine remains form-agnostic. | None — no measurements taken yet |
| 2026-07-26 | E10–E12, R9a–R9c, V17–V18 | *(absent)* | Added | OCR intake path was implied by input formats but had no named mechanism, routing rule, or evaluation coverage. | None — no measurements taken yet |
| 2026-07-26 | P6–P9 | `TBD — Phase 0` | Dataset source, version, retrieval date, and committed path (see §2) | Candidate evaluated and selected per `ARCHITECTURE.md` §12 open item; official data.gov.in "All India Pincode Directory till last month" (GODL-India licensed) chosen over community/third-party mirrors for license clarity. Derived to `app/config/data/pincode_district_state.csv`. | None — no measurements taken yet |
| 2026-07-26 | R11, R12, R12a | `TBD — Phase 0, agreed with frontend developer` | `frontend/dist/`, `npm run build`, same-origin static mount | This is a solo project — no separate frontend developer exists, so `BUILD.md` task 6's "agree with frontend developer" collapses to a single-owner decision. Vite convention chosen as the current standard for new React SPAs. | None — no measurements taken yet |
| 2026-07-26 | E10 | `TBD — Phase 1` | Tesseract via `pytesseract` | CPU-only requirement (ARCH §9 deployment constraint) rules out GPU-backed engines (e.g. EasyOCR); mature, small container footprint. | None — no measurements taken yet |
| 2026-07-26 | E11 | `TBD — Phase 1` | Provisional: 20 characters | Starting value for per-page native-vs-OCR routing (BUILD P1 task 1b), validated against golden-file fixtures only. **Not final** — pending validation against a broader document sample before end of Phase 1. | None — no measurements taken yet |
| 2026-07-26 | E12 | `TBD — Phase 1` | Provisional: 600×600 px | Starting value for standalone-image rejection (BUILD P1 task 1c), validated against golden-file fixtures only. **Not final** — pending validation against a broader document sample before end of Phase 1. | None — no measurements taken yet |

---

## 7. Open inconsistencies

Documented, not resolved. See the accompanying notes; resolution requires an explicit
decision.

| # | Inconsistency | Documents involved |
|---|--------------|--------------------|
| C1 | Crash-resume is required to reproduce identical state, but the session pseudonym map is in process memory and does not survive a restart | ARCH §11.7 vs. BUILD P5 |
| C2 | Phase 0 exit criterion states "no TBD remaining," but measurement placeholders remain open until Phase 7 by design | BUILD P0 vs. BUILD P1–P7 |
| C3 | Named policy configs are described as finalized in Phase 6 but authored in Phase 3 | ARCH §5.3 vs. BUILD P3 task 8 |
| C4 | A fixed retry budget is required but no value is specified in any document | ARCH §7, CLAUDE §6 |
