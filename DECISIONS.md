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
| **Status** | Phase 4 complete |
| **Last updated** | 2026-07-28 |
| **Last change** | Phase 4 task 6: live dev-case accuracy evaluation across all three privacy modes. `full_tokenize`/`policy_engine` measured at 0.000 (0/36 fields) — a structural finding, not a partial cost: compact multi-field documents retrieve as one shared evidence chunk, so every field's prompt contains the applicant's name, and Tokenize has no FF1 alphabet for `NAME`/`EMAIL`/`ADDRESS`/`DATE` (already-documented Phase 3 gap, now shown to be more severe in practice than previously characterized). `none`-mode baseline blocked, not measured, by provider free-tier quota exhaustion — an evaluation-environment limitation, not a code defect. See §5 Phase 4 and §6 for full detail. |

---

## 1. Engineering constants

| ID | Constant | Value | Source |
|----|----------|-------|--------|
| E1 | Age reference date | Form submission/creation date | ARCH §5.4, D10 |
| E2 | Age band width | 10 years, decade-aligned | ARCH §5.4 |
| E3 | Tokenization scheme | FF1 format-preserving encryption | ARCH §5.4 |
| E4 | Pseudonym map scope | Session-scoped | ARCH §5.4 |
| E5 | Default policy action for detected sensitive values without an explicit rule | Tokenize (fail closed) | ARCH §5.3, D9 |
| E6 | Retry budget per field | **1** (max 2 attempts per field: initial + 1 retry) | ARCH §7; BUILD P5 task 6. Pinned in Phase 5 commit 1 — see §6 change log for rationale. Closes C4 (§7). |
| E7 | Confidence score definition | The LLM's own self-assessment, elicited in the same structured-output call as the extraction (no second LLM call, no separate scoring model). A float in `[0.0, 1.0]` — bounds enforced on the response schema itself (`app/extraction/extractor.py`, `_FieldExtractionResponse.confidence`) — reflecting how directly and unambiguously the cited evidence chunk supports the extracted value; 1.0 means the evidence states it explicitly and unambiguously. Defined only when a value is extracted: an abstained field has `confidence = None`, mirroring the value/provenance pairing (I5/G4 — nothing to be confident about when nothing was extracted). A value returned without a confidence score is treated as a broken provider contract and raises, not defaults (CLAUDE §5). Not yet validated against dev-case correctness — that sanity check is BUILD P2 task 8 (see the Phase 2 risk table: "low-confidence fields should correlate with actual errors, or the Phase 5 retry logic has nothing to trigger on"). | BUILD P2, task 4 |
| E8 | Embedding provider and model | Google Gemini `gemini-embedding-001`, hosted API (`google-genai` SDK). Originally pinned to OpenAI `text-embedding-3-small`; migrated 2026-07-27, see §6 change log for the reason and for why the OpenAI implementation was removed rather than kept as a dormant alternative. See C5 for the trust-boundary scope of this call — unchanged by the provider swap. | BUILD P1, task 3 |
| E9 | LLM provider and model | Google Gemini `gemini-3.5-flash`, hosted API (`google-genai` SDK), via structured-output mode (`response_mime_type="application/json"`, Pydantic `response_schema`). Verified live against the project's Gemini account before pinning: `gemini-2.5-flash` returns `404` ("no longer available to new users"); `gemini-2.0-flash-001` returned a transient `503`; `gemini-3.5-flash` and `gemini-3.5-flash-lite` both returned correctly parsed structured output — the full model chosen over the lite variant for extraction quality. Single-vendor consistency with E8, as anticipated in E8's own change-log entry. | BUILD P2, task 3 |
| E10 | OCR engine | Tesseract, via `pytesseract`. CPU-only; no GPU runtime pulled in (unlike e.g. EasyOCR). | ARCH §6.1, D11; BUILD P1 task 1a |
| E11 | Text-layer sufficiency threshold for OCR fallback | **Provisional: 20 characters.** Native page text shorter than this triggers OCR. Validated only against the golden-file fixtures in `tests/ingest/`; not yet confirmed against a broader document sample. Do not treat as final. | ARCH §6.1; BUILD P1 task 1b |
| E12 | Minimum accepted image resolution | **Provisional: 600×600 px.** Standalone images below this on either dimension are rejected outright rather than sent through OCR. Validated only against the golden-file fixtures in `tests/ingest/`; not yet confirmed against a broader document sample. Do not treat as final. | BUILD P1 task 1c |
| E13 | Chunk size | **Provisional: 500 characters.** Not yet tuned; no recall@k measurement has run against it. | BUILD P1 task 2 |
| E14 | Chunk overlap | **Provisional: 50 characters.** Not yet tuned; no recall@k measurement has run against it. | BUILD P1 task 2 |
| E15 | Vector index implementation | In-process, per-case, pure-Python brute-force cosine similarity. No FAISS/ANN index, no external vector database, no numpy — unjustified by the per-case data volume (tens to low hundreds of chunks). | BUILD P1 task 4 |
| E16 | Field-label synonym table | **Provisional.** 10 static synonym groups (`app/retrieval/query.py`, `FIELD_LABEL_SYNONYM_GROUPS`) seeded from the entity types already named in P12 — PAN, Aadhaar, DOB, passport, phone, email, address, PIN code, account number, name. Hand-authored, not tuned; recall@k (BUILD P1 tasks 7-8) is what will validate it. No LLM-based expansion. | BUILD P1 task 5 |
| E17 | Per-field retrieval fan-in (`top_k` passed to the extraction node's retrieval call) | **Provisional: 5.** Not yet tuned; no accuracy measurement has run against it — same treatment as E13/E14/E16 in Phase 1. | BUILD P2 task 3 |
| E18 | Retry retrieval fan-in (`top_k` used on a field's re-retrieve attempt, after the verifier rejects the initial candidate) | **Provisional: 10** (double E17's baseline). Not yet tuned — same treatment as E13/E14/E16/E17. Defines what ARCH §7's "re-retrieve with adjusted query" concretely means here: a wider evidence set on retry, reusing `extract_field`'s existing `top_k` parameter rather than adding query-string rewriting (`app/retrieval/query.py` already declines LLM-based query rewriting by design; "the verifier handles ambiguity later" is read as the verifier's retry/escalate *decision*, not a rewritten query string). | ARCH §7; BUILD P5 task 6 |

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
| V19 | Recall@k evaluation k-value | **k = 5.** Fixed before the Phase 1 retrieval measurement runs, for the same reason V7 is frozen before results — so it cannot be adjusted after seeing the number. | BUILD P1 tasks 7-8 |

**V7 is frozen.** It was fixed before any result was generated, specifically so it cannot be
adjusted afterward. Moving it post-hoc is a documented anti-pattern for this project
(BUILD P7 risk table).

**V19 is frozen** for the same reason, fixed in this commit before the Phase 1 recall
measurement (§5) was run.

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
| R14 | Vector index lifecycle | In-memory, per-process; not durable across restarts, consistent with R8. A container restart loses all case indices and requires re-ingestion. Chosen to avoid persistence-layer complexity not required by Phase 1. | BUILD P1 task 4 |
| R15 | `Settings` extra-field handling | `extra="ignore"` (changed from `"forbid"` in Phase 0). The project-root `.env` is a shared file: it now also holds `OPENAI_API_KEY`/`GEMINI_API_KEY` for the embedding SDK, which reads them directly from the process environment and never through this class. pydantic-settings' dotenv source does not filter unprefixed keys out before its extra-field check (verified empirically — a real unprefixed OS environment variable was correctly filtered; an unprefixed `.env`-file key was not), so `extra="forbid"` broke `Settings()` construction entirely once those keys existed in `.env`. Trade-off, stated plainly: this also stops catching a genuine typo in an `APP_`-prefixed key, which `"forbid"` did catch. | Provider migration, 2026-07-27 |

---

## 5. Measurements

Filled in when measured. Do not estimate. Every value must be reproducible from a stored
run.

### Phase 1

| Metric | Value | Date |
|--------|-------|------|
| Retrieval recall@5 (V19) on 20 hand-labeled pairs | **1.000 (20/20)** | 2026-07-27 |
| Labeled pair count | 20 | 2026-07-27 |
| Recall@5 — native-text documents | **1.000 (16/16)** | 2026-07-27 |
| Recall@5 — OCR-derived documents | **1.000 (4/4)** | 2026-07-27 |

Measured by `eval/harness/measure_recall.py` against `gemini-embedding-001` (E8). Stored
run: `eval/harness/results/phase1_recall_result.json`; replayable offline from
`eval/harness/fixtures/phase1_embedding_cache.json` (see the regression test). Read
plainly, not as a general retrieval-quality claim: this is a 20-pair sanity check across 4
documents (`BUILD.md` Phase 1 task 7), not the Phase 6 evaluation dataset — a perfect
score here says the pipeline works end-to-end and the E13/E14/E16 starting values aren't
obviously wrong at this scale, not that retrieval is flawless at the Phase 6 scale or under
adversarial conditions (near-duplicate names, conflicting values) that this fixture set
doesn't contain.

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
| Dev-case accuracy — `none` | **Blocked — not measured.** All 36/36 field extractions failed on provider quota exhaustion (429 `RESOURCE_EXHAUSTED`, free-tier `generate_content_free_tier_requests` limit for `gemini-3.5-flash`), confirmed persistent even after a 65s wait-and-retry from a clean, isolated, zero-concurrency call. This is an evaluation-environment limitation, not a code defect or an accuracy finding — recorded as blocked rather than estimated. | 2026-07-28 |
| Dev-case accuracy — `full_tokenize` | **0.000 (0/36 fields correct).** Every field failed with `UnsupportedActionForEntityTypeError` inside `protect()`, before any network call — not a partial accuracy cost. See note below. | 2026-07-28 |
| Dev-case accuracy — `policy_engine` | **0.000 (0/36 fields correct).** Same failure mode and cause as `full_tokenize`; the active config (`age_state`) does not change the outcome because the failing entity is never the field it governs. See note below. | 2026-07-28 |

**Methodology.** `eval/harness/measure_dev_case_accuracy.py` against `eval/dataset/phase4_dev_cases.json` — 4 informal dev cases (2 KYC / 2 Insurance, one complete and one adversarial-missing-required-field case per form type), calling the real pipeline (parse → chunk → embed → retrieve → extract) through `app.extraction.extractor.extract_field` directly, one fresh case_id per (case, mode) pair for isolation. Deliberately smaller than BUILD.md Phase 2 task 8's "10 dev cases" — that set was never actually assembled in Phase 2 (its own accuracy measurement above is still `*pending*`); 4 cases kept this informal first-signal measurement's cost and runtime bounded. Full per-field results, including every raw error message, are in `eval/harness/results/phase4_dev_case_result.json`.

**Finding: protected modes fail completely, not partially, on compact documents (structural, not a bug).** 32/36 failures were `Tokenize has no defined alphabet for entity type 'name'`; the remaining 4 (exactly the four `email_address` field extractions) were the same failure for `'email'`. Root cause: these dev-case documents are short enough that every field's evidence retrieves the same single chunk, so every field's prompt contains the applicant's full name (and, for the email field specifically, their email) alongside whatever value is actually being extracted. Tokenize has no FF1 alphabet for `NAME`/`ADDRESS`/`EMAIL`/`DATE` — an already-documented Phase 3 limitation (`app/privacy/tokenize.py`) — and `resolve_action`'s fail-closed default (Invariant I4) means `protect()` aborts the entire call rather than send anything unprotected. `policy_engine` mode is not spared: `age_state` governs `pan_number`/`aadhaar_number`/`phone_number`/`pin_code`/`date_of_birth`/`linked_account_number` correctly, but the *other* entity in the same shared chunk (the applicant's name) still fails closed, and `protect()` aborts on the first failing entity regardless of which field the call was for. This is the real-world severity of a gap this project has known about since Phase 3: not a bounded accuracy cost, a complete inability to extract any field from a realistic, compact, multi-field document once evidence co-locates a Tokenize-incompatible entity type — which, for documents structured the way real KYC/insurance forms are, is close to unavoidable. Per this project's own instruction, no production code was changed in response to this measurement; expanding entity coverage is a deliberate, separate decision for after Phase 4 closes.

**Trade-off/limitation: `none`-mode baseline unmeasured this session.** The free-tier quota for the pinned model (DECISIONS.md E9) turned out to be far more restrictive than anticipated — a single, isolated, retried call still failed after a 65-second wait, ruling out simple rate-limiting as the sole cause. Proactive pacing and long single-retry logic were added to the harness (not to `app/extraction` or `app/boundary`) but could not produce a real signal within this session. The `none`-mode baseline should be re-measured (`uv run python -u -m eval.harness.measure_dev_case_accuracy`) once quota is available; the harness and dataset are otherwise complete and ready to run unchanged.

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
| 2026-07-26 | E13, E14 | *(absent)* | Added: 500 chars / 50 chars | Chunking (BUILD P1 task 2) needed a size and overlap constant that no prior document named. Conventional starting values, deliberately not tuned yet — recall@k (BUILD P1 tasks 7-8) is what will validate them. | None — no measurements taken yet |
| 2026-07-26 | E8 | `TBD — Phase 1` | OpenAI `text-embedding-3-small` | Hosted API required by BUILD P1 task 3 (free-tier deployment constraint excludes local models). Chosen for consistency with this project's OpenAI-compatible framing (ARCH §1), keeping a single vendor relationship across embeddings (Phase 1) and the eventual generative LLM (E9, Phase 2). See C5 for the trust-boundary reasoning this decision required. | None — no measurements taken yet |
| 2026-07-26 | E15, R14 | *(absent)* | Added | Per-case vector index (BUILD P1 task 4) needed a named implementation choice and lifecycle. In-process brute-force cosine similarity chosen over FAISS/external vector DB — unjustified by per-case data volume and would violate the single-container constraint (ARCH N5, §9). Lifecycle mirrors R8 (process-memory, not durable). | None — no measurements taken yet |
| 2026-07-26 | E16 | *(absent)* | Added: 10 synonym groups | Query construction (BUILD P1 task 5) needed a versioned starting point for its synonym table. Hand-authored, deliberately not tuned — recall@k (BUILD P1 tasks 7-8) is what will validate it, same treatment as E13/E14. | None — no measurements taken yet |
| 2026-07-26 | V19 | *(absent)* | Added: k = 5 | Recall@k needed a fixed k before the Phase 1 measurement runs (BUILD P1 tasks 7-8), same discipline as V7. Fixed here, before any pairs are queried. | None — measurement not yet run |
| 2026-07-27 | §5 Phase 1 recall@5 | *pending* | 1.000 overall (20/20), 1.000 native (16/16), 1.000 OCR (4/4) | Measured via `eval/harness/measure_recall.py` against `gemini-embedding-001` (E8), after the OpenAI-to-Gemini migration below. Stored run: `eval/harness/results/phase1_recall_result.json`. | This measurement itself, run against Gemini rather than the originally-planned OpenAI model — no prior OpenAI-based measurement was ever taken, so nothing else needed re-running |
| 2026-07-27 | E9 | `TBD — Phase 1` | Google Gemini `gemini-3.5-flash` | Pinned for BUILD P2 task 3 (extraction node). Verified live rather than assumed, the same discipline E8's migration used: `gemini-2.5-flash` (the model this project would otherwise have defaulted to, by analogy with common Gemini usage) returned `404 "no longer available to new users"` on this account; `gemini-2.0-flash-001` returned a transient `503`. `gemini-3.5-flash` and `gemini-3.5-flash-lite` both returned correctly parsed structured output against a live smoke prompt; the full model was chosen over the lite variant on the reasoning that extraction quality matters more than per-call cost for a project whose headline results are an accuracy comparison. Single-vendor consistency with E8, as anticipated in E8's own change-log entry below. | None — no extraction accuracy measurement has run yet (BUILD P2 task 8) |
| 2026-07-27 | E7 | `TBD — Phase 2` | LLM self-assessed confidence, `[0.0, 1.0]`, elicited in the extraction call itself | Pinned for BUILD P2 task 4. Kept deliberately simple (a single self-reported number from the same call, no second LLM pass, no ensemble with the retrieval similarity score) per CLAUDE §9 — an untested weighting scheme combining signals would be speculative. Placed entirely in `app/extraction/extractor.py` rather than `llm_client.py` or `retriever.py`, so the provider client stays a generic `(prompt, schema) -> T` and the retriever's similarity score stays a retrieval-quality signal, not a correctness signal. | None yet — correlation with actual extraction correctness is BUILD P2 task 8, not this task |
| 2026-07-27 | E17 | *(absent)* | Added: `top_k = 5` | Extraction node (BUILD P2 task 3) needed a retrieval fan-in constant no prior document named. Conventional starting value, deliberately not tuned — same treatment as E13/E14/E16. | None — no measurements taken yet |
| 2026-07-27 | E8 | OpenAI `text-embedding-3-small` | Google Gemini `gemini-embedding-001` | The provisioned OpenAI account had no usable quota (`insufficient_quota`, verified via a live 429 from the API, not a code defect) when the Phase 1 recall@5 measurement was attempted. A working Gemini API key was verified live (model list, a direct `embedContent` call, and a batch call via the official `google-genai` SDK) before switching. The prior OpenAI implementation was **removed**, not kept as a dormant alternative: no `BUILD.md` task calls for multi-provider support, an unused code path would go untested and silently drift out of sync with future SDK versions, and it would have doubled the dependency/secret-management surface (CLAUDE §6, §9 — no unjustified dependency, no speculative configurability). The `embed_texts()` / `EmbeddingProviderError` contract in `app/retrieval/embedder.py` is unchanged; every existing test in `tests/retrieval/` and `tests/api/` passed unmodified after the swap, which is the actual proof the provider-neutral design held. If OpenAI is ever wanted again (e.g. for consistency with a future E9 choice), it is a small, contained re-implementation behind the same contract — nothing else in the codebase depends on which provider is behind it. | Recall@5 measurement re-run against Gemini (below) |
| 2026-07-28 | §5 Phase 4 dev-case accuracy | `*pending*` | `full_tokenize`/`policy_engine`: 0.000 (0/36, structural — see note); `none`: blocked, not measured (provider quota exhaustion) | BUILD P4 task 6 live evaluation via `eval/harness/measure_dev_case_accuracy.py` against `eval/dataset/phase4_dev_cases.json` (4 dev cases). No pinned engineering constant changed and no production code modified in response — this measurement's own conclusion is that the existing, already-documented Tokenize entity-type gap (E10–E12 era, `app/privacy/tokenize.py`) is more severe in practice than previously characterized, not that anything is newly broken. | None — first measurement of this metric |
| 2026-07-29 | E6 | `TBD — Phase 5` | `1` (max 2 attempts per field: initial + 1 retry) | Phase 5 commit 1. Grounded in a measured project fact rather than a default: the Phase 4 dev-case measurement above already showed the pinned Gemini free-tier quota (E9) is fragile enough that a single, isolated, retried call still failed after a 65s wait. `BUILD.md`'s own Phase 5 risk table names "retry loops burn API quota" directly, and no Phase 6/7 data yet exists to justify a larger budget's accuracy benefit against that cost. Closes C4 (§7). | None — E6 has never gated a measurement before this pin |
| 2026-07-29 | E18 | *(absent)* | Added: retry retrieval `top_k` = 10 | Phase 5 commit 1. Needed to give ARCH §7's "re-retrieve with adjusted query" a concrete, non-speculative meaning: a wider retrieval fan-in on a field's retry attempt (double E17's baseline), reusing `extract_field`'s existing `top_k` parameter rather than adding query-string-rewriting plumbing to `app/retrieval/query.py`, whose own docstring already declines LLM-based query rewriting ("the verifier handles ambiguity later" — read as the verifier's retry/escalate decision, not a rewritten query). Provisional starting value, same treatment as E13/E14/E16/E17. | None — no measurements taken yet |

---

## 7. Open inconsistencies

Documented, not resolved. See the accompanying notes; resolution requires an explicit
decision.

| # | Inconsistency | Documents involved |
|---|--------------|--------------------|
| C1 | Crash-resume is required to reproduce identical state, but the session pseudonym map is in process memory and does not survive a restart | ARCH §11.7 vs. BUILD P5 |
| C2 | Phase 0 exit criterion states "no TBD remaining," but measurement placeholders remain open until Phase 7 by design | BUILD P0 vs. BUILD P1–P7 |
| C3 | Named policy configs are described as finalized in Phase 6 but authored in Phase 3 | ARCH §5.3 vs. BUILD P3 task 8 |
| C4 | ~~A fixed retry budget is required but no value is specified in any document~~ — **Resolved 2026-07-29**: pinned as E6 = 1 retry (§1). See §6 change log. | ARCH §7, CLAUDE §6 |
| C5 | **Hosted embedding boundary clarification.** Phase 1 embedding generation (E8) sends raw, un-pseudonymized chunk text to the hosted embedding provider, in every privacy mode. This is required because retrieval quality depends on semantic embeddings — a pseudonymized/tokenized chunk carries no semantic relationship to a query, so pseudonymizing before embedding would degrade retrieval accuracy for reasons that have nothing to do with privacy (the same reasoning Invariant P1 already applies to retrieval generally). This decision applies specifically to embedding generation, not to any other outbound call: the Privacy Policy Engine gates generative LLM calls (the field-extraction and verifier calls behind the boundary in ARCH §3's diagram, per Invariant P2), not the Phase 1 embedding call, which sits outside that gate by P1's own design. Recorded here rather than silently assumed because ARCH §1's positioning statement ("no raw PII is ever transmitted to a third-party language model") could otherwise be read more broadly than Invariant P2 actually claims. | ARCH §1, §3, Invariants P1/P2 vs. BUILD P1 task 3 |
