"""Tests for the Phase 6 representative OCR-subset generator (BUILD.md Phase 6 task 3a,
DECISIONS.md V17). Writes to pytest's tmp_path, never to the committed
eval/dataset/fixtures/phase6_ocr/ directory -- generate() takes an explicit output_dir for
exactly this reason.

Most tests share one module-scoped generate() call (`_generated`) -- rendering ~18 images
twice over (once per condition, per document) is the expensive part of this module, and
these tests only ever read its output, never mutate it. test_generate_is_deterministic and
the tesseract-marked recoverability test are the only ones that need their own call.
"""

from pathlib import Path

import pytest

from eval.dataset.case_builder import (
    ACADEMIC_RECORD,
    ADDRESS_PROOF,
    ID_PROOF,
    INCOME_DOCUMENT,
    NOMINEE_DECLARATION,
    Case,
)
from eval.dataset.generate_ocr_subset import (
    CLEAN_SCAN,
    PHOTO_LIKE,
    OcrFixture,
    build_representative_cases,
    generate,
)

_SHARED_DOCUMENT_TYPES = {ID_PROOF, ADDRESS_PROOF, INCOME_DOCUMENT, ACADEMIC_RECORD}


@pytest.fixture(scope="module")
def _generated(tmp_path_factory: pytest.TempPathFactory) -> tuple[list[OcrFixture], Path]:
    output_dir = tmp_path_factory.mktemp("phase6_ocr_shared")
    return generate(output_dir), output_dir


@pytest.fixture(scope="module")
def _cases_by_id() -> dict[str, Case]:
    return {case.case_id: case for case in build_representative_cases()}


def test_generate_covers_every_shared_document_type_and_both_conditions(
    _generated: tuple[list[OcrFixture], Path],
) -> None:
    fixtures, _ = _generated

    document_types = {f.document_type for f in fixtures}
    assert _SHARED_DOCUMENT_TYPES <= document_types
    assert NOMINEE_DECLARATION in document_types  # from the insurance case only

    conditions = {f.condition for f in fixtures}
    assert conditions == {CLEAN_SCAN, PHOTO_LIKE}


def test_generate_covers_both_form_schemas(_cases_by_id: dict[str, Case]) -> None:
    schema_ids = {case.form_schema_id for case in _cases_by_id.values()}
    assert schema_ids == {"kyc_account_opening", "insurance_policy_application"}


def test_every_document_type_appears_in_both_conditions_for_every_case(
    _generated: tuple[list[OcrFixture], Path],
) -> None:
    fixtures, _ = _generated
    seen: dict[tuple[str, str], set[str]] = {}
    for f in fixtures:
        seen.setdefault((f.source_case_id, f.document_type), set()).add(f.condition)

    for conditions in seen.values():
        assert conditions == {CLEAN_SCAN, PHOTO_LIKE}


def test_each_fixture_links_back_to_a_real_document_in_its_source_case(
    _generated: tuple[list[OcrFixture], Path], _cases_by_id: dict[str, Case]
) -> None:
    fixtures, _ = _generated

    for fixture in fixtures:
        case = _cases_by_id[fixture.source_case_id]
        matching = [d for d in case.documents if d.document_id == fixture.source_document_id]
        assert len(matching) == 1
        assert matching[0].document_type == fixture.document_type


def test_ground_truth_parity_between_fixture_and_source_case(
    _generated: tuple[list[OcrFixture], Path], _cases_by_id: dict[str, Case]
) -> None:
    """The core requirement: an OCR-rendered fixture's ground truth is identical to its
    source case's -- rebuilt independently here, not read back from generate()'s own
    bookkeeping."""
    fixtures, _ = _generated

    assert fixtures  # sanity: the loop below would vacuously pass on an empty list
    for fixture in fixtures:
        source_case = _cases_by_id[fixture.source_case_id]
        assert fixture.ground_truth == source_case.ground_truth
        assert fixture.form_schema_id == source_case.form_schema_id


def test_generated_image_files_exist_and_are_referenced_correctly(
    _generated: tuple[list[OcrFixture], Path],
) -> None:
    fixtures, output_dir = _generated
    for fixture in fixtures:
        image_path = output_dir / fixture.image_file
        assert image_path.exists()
        assert image_path.stat().st_size > 0
        suffix = ".png" if fixture.condition == CLEAN_SCAN else ".jpg"
        assert fixture.image_file.endswith(suffix)


def test_generate_is_deterministic(tmp_path: Path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    first = generate(first_dir)
    second = generate(second_dir)

    assert first == second
    for fixture in first:
        assert (first_dir / fixture.image_file).read_bytes() == (second_dir / fixture.image_file).read_bytes()


def test_build_representative_cases_is_deterministic() -> None:
    first = build_representative_cases()
    second = build_representative_cases()
    assert first == second


@pytest.mark.requires_tesseract
def test_a_rendered_clean_scan_image_is_genuinely_ocr_recoverable(
    _generated: tuple[list[OcrFixture], Path],
) -> None:
    """Not just format-compatible with the OCR path -- actually recoverable by it. Proves
    the fixture is real OCR fodder rather than merely accepted-by-extension."""
    from app.ingest.parser import TextSource, parse_document

    fixtures, output_dir = _generated
    id_proof_fixture = next(
        f for f in fixtures if f.document_type == ID_PROOF and f.condition == CLEAN_SCAN and f.source_case_id == "phase6_ocr_kyc"
    )
    image_path = output_dir / id_proof_fixture.image_file

    pages = parse_document(image_path, document_id=id_proof_fixture.source_document_id)

    assert len(pages) == 1
    assert pages[0].source == TextSource.OCR
    assert "IDENTITY PROOF" in pages[0].text.upper()
