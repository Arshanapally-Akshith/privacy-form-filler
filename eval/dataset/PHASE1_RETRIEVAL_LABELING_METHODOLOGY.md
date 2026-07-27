# Labeling methodology — Phase 1 retrieval evaluation pairs

Covers `eval/dataset/phase1_retrieval_pairs.json` and the source documents in
`eval/dataset/fixtures/phase1_retrieval/`, used for the recall@k measurement in
`BUILD.md` Phase 1 tasks 7-8.

## Provenance

- The evaluation pairs were **authored by the project owner** (Akshith), not labeled by an
  independent annotator. The same person who wrote the four synthetic source documents also
  determined which (field_label → chunk) pairs count as correct, because the ground truth
  is known by construction: the documents were written specifically to contain the fields
  being tested.
- They are **deterministic fixtures**: committed source documents plus a committed pairs
  file, not generated at random and not regenerated per run. Re-running the harness against
  the same fixtures produces the same chunking and, modulo the embedding-provider caveat
  noted below, the same retrieval results.
- They are **not an independently annotated dataset**. This is a ~20-pair sanity check at
  Phase 1 scale (`BUILD.md` Phase 1 task 7), explicitly distinct from the Phase 6 evaluation
  dataset (50-60 semi-synthetic cases, `ARCHITECTURE.md` §8, D8), which has its own
  generation methodology and is not built here.

## Scope

Four documents, drawn from both demonstration form domains (KYC/Account Opening and
Insurance Policy Application, `DECISIONS.md` R9) so field labels span the Axis 2 field
types named in `ARCHITECTURE.md` §8 (V2): Identifier, Date, Location, Name,
Numeric-financial. One document is rendered as a scanned/OCR-required image so the
native-vs-OCR split (`DECISIONS.md` V17/V18) has real coverage, not just native text.

## Ground truth matching rule

Pairs match by `(expected_document_id, expected_page_number)`, not by exact
`chunk_index`. Chunk boundaries are a function of `DECISIONS.md` E13/E14, which are still
provisional — pinning ground truth to a specific chunk index would make this measurement
fragile to a future chunk-size retune for reasons that have nothing to do with retrieval
quality. A hit is "the right page's content appeared in the top-k," not "the exact same
character window."
