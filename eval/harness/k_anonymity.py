"""Deterministic k-anonymity (re-identification) analysis (Phase 7 Commit 1;
ARCHITECTURE.md §8 Axis 3; DECISIONS.md V7/V8/V9).

**Zero LLM dependency, by construction.** A tokenized value is session-scoped ciphertext of
the *same* field (E4), and a session here is scoped to one case (R16) -- it is never a stable
quasi-identifier across cases, so it plays no part in this analysis. The only things that can
ever make two cases distinguishable to an external observer under a given policy config are
that config's Generalize and Derive outputs (age band; district/state) -- both deterministic
functions of data already sitting in the committed evaluation dataset
(eval/dataset/phase6_eval_cases.json). Computing k-anonymity is therefore a pure function of
(dataset, policy config): no retrieval, no extraction, no provider call, nothing live. This
is what makes it safe to run and commit numbers for right now, independent of the Phase 7
live-matrix quota question (see the approved Phase 7 plan's D1).

**Reuses the real privacy primitives, not a re-implementation.** Every exposed value is
computed by calling app.privacy.generalize.generalize_dob / app.privacy.derive.derive_state /
derive_district directly against each case's real ground-truth `date_of_birth` /
`pin_code` -- the same functions app.privacy.dispatch.apply_action calls in production. This
module reimplements no age-banding or PIN-lookup logic of its own.

**Fail-closed parity with the real dispatch path for Derive.** app.privacy.dispatch.
apply_action's own DERIVE branch catches UnknownPinCodeError/AmbiguousPinCodeError and falls
back to Tokenize (a PIN the dataset cannot resolve unambiguously is never exposed raw or
derived). This module replicates exactly that: the same two exceptions are caught here and
the case is treated as exposing nothing for that attribute, never as an error and never as a
placeholder value. Reporting a different (falsely "successful") derivation here than the real
pipeline would actually produce would make the k-anonymity numbers describe a system that
does not exist. Generalize has no such fallback in production (app.privacy.dispatch's own
GENERALIZE branch does not catch DateParseError/ImplausibleDateOfBirthError), so this module
does not add one either -- a genuinely malformed DOB in the committed dataset should surface
as a loud failure here too, not be silently absorbed.

**Which Derive attribute a config exposes is not recorded on PolicyConfig itself.**
app.boundary.policy_engine's own module docstring already states this and names its solution:
a small, named-config -> DeriveAttribute mapping, "the only other place in the codebase
resolving this today" being app.privacy.cli's own copy. This module keeps a third, identical,
eval-scoped copy (_CONFIG_DERIVE_ATTRIBUTES below) rather than importing either module's
private (underscore-prefixed) attribute or changing app/ to export one -- consistent with the
project's own existing precedent, and avoiding an app/ change for something eval/ can express
on its own.

**"Percentage of singleton groups" (this commit's own phrasing) and DECISIONS.md V8's
"percentage of cases at k = 1" are the same quantity, computed over cases, not over groups.**
A singleton equivalence class contains exactly one case by definition, so "how many groups
are singletons" and "how many cases sit in a singleton group" are the same count -- but the
denominator that makes the number meaningful as a *re-identification risk* is the total
number of cases (what fraction of real people are uniquely identifiable), not the total
number of groups. `KAnonymityResult.percentage_singletons` is therefore
singleton_case_count / total_cases * 100, matching V8 exactly.
"""

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.privacy.constants import GENERALIZE_ATTRIBUTE_NAME
from app.privacy.derive import (
    AmbiguousPinCodeError,
    UnknownPinCodeError,
    derive_district,
    derive_state,
)
from app.privacy.dispatch import DeriveAttribute, PolicyAction
from app.privacy.generalize import generalize_dob
from app.privacy.policy_config import PolicyConfig, load_policy_config

DATASET_PATH = Path(__file__).resolve().parent.parent / "dataset" / "phase6_eval_cases.json"
POLICY_CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "app" / "config" / "policy_configs"
RESULT_PATH = Path(__file__).resolve().parent / "results" / "k_anonymity_result.json"

# DECISIONS.md V9: the three named configs the re-identification analysis covers.
NAMED_CONFIGS: tuple[str, ...] = ("strict", "age_state", "ageband_city")

# DECISIONS.md V7, fixed before any result was generated (frozen, per V7's own note in that
# document) -- not re-derived or adjusted here.
MIN_K_THRESHOLD = 5

# Mirrors app.boundary.policy_engine._CONFIG_DERIVE_ATTRIBUTES / app.privacy.cli's own
# identical copy -- see module docstring for why a third copy lives here rather than
# importing either module's private attribute.
_CONFIG_DERIVE_ATTRIBUTES: dict[str, DeriveAttribute] = {
    "age_state": DeriveAttribute.STATE,
    "ageband_city": DeriveAttribute.DISTRICT,
}

# The two form fields Generalize/Derive are pinned to (DECISIONS.md P3/P4) -- both KYC and
# Insurance schemas share these exact field names (app/config/form_schemas/*.json), so no
# per-schema branching is needed here.
GENERALIZE_FIELD_NAME = "date_of_birth"
DERIVE_FIELD_NAME = "pin_code"


@dataclass(frozen=True)
class CaseExposure:
    """What one case exposes under one policy config: a sorted tuple of
    (attribute_name, value) pairs, empty if the config derives/generalizes nothing for this
    case. A plain tuple (not a dict) so it is hashable and usable directly as a grouping key.
    """

    case_id: str
    exposed_attributes: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class EquivalenceClass:
    """All cases sharing one exact exposed-attribute combination under one config."""

    exposed_attributes: tuple[tuple[str, str], ...]
    case_ids: tuple[str, ...]

    @property
    def k(self) -> int:
        return len(self.case_ids)


@dataclass(frozen=True)
class KAnonymityResult:
    config_name: str
    total_cases: int
    equivalence_classes: tuple[EquivalenceClass, ...]
    total_equivalence_classes: int
    minimum_k: int
    median_k: float
    percentage_singletons: float
    meets_threshold: bool


def compute_case_exposure(
    *,
    case_id: str,
    ground_truth: dict[str, str | None],
    reference_date: date,
    config: PolicyConfig,
    derive_attribute: DeriveAttribute | None,
) -> CaseExposure:
    """The exposed-attribute tuple for one case under one config. Never raises for a Derive
    field whose PIN the dataset cannot resolve (see module docstring); does not special-case
    a Generalize field whose DOB fails to parse -- that mirrors production dispatch exactly.
    """
    exposed: dict[str, str] = {}

    if config.field_actions.get(GENERALIZE_FIELD_NAME) is PolicyAction.GENERALIZE:
        dob_value = ground_truth.get(GENERALIZE_FIELD_NAME)
        if dob_value is not None:
            exposed[GENERALIZE_ATTRIBUTE_NAME] = generalize_dob(dob_value, reference_date)

    if config.field_actions.get(DERIVE_FIELD_NAME) is PolicyAction.DERIVE:
        pin_value = ground_truth.get(DERIVE_FIELD_NAME)
        if pin_value is not None:
            if derive_attribute is None:
                raise ValueError(
                    f"Config {config.name!r} derives {DERIVE_FIELD_NAME!r} but no derive_attribute "
                    "was supplied -- see _CONFIG_DERIVE_ATTRIBUTES"
                )
            try:
                derived_value = (
                    derive_state(pin_value) if derive_attribute is DeriveAttribute.STATE else derive_district(pin_value)
                )
                exposed[derive_attribute.value] = derived_value
            except (UnknownPinCodeError, AmbiguousPinCodeError):
                # Fail-closed parity with app.privacy.dispatch.apply_action: an unresolvable
                # PIN falls back to Tokenize in the real pipeline, so nothing is exposed for
                # this case/attribute here either -- not an error, not a placeholder.
                pass

    return CaseExposure(case_id=case_id, exposed_attributes=tuple(sorted(exposed.items())))


def group_into_equivalence_classes(exposures: list[CaseExposure]) -> tuple[EquivalenceClass, ...]:
    groups: dict[tuple[tuple[str, str], ...], list[str]] = defaultdict(list)
    for exposure in exposures:
        groups[exposure.exposed_attributes].append(exposure.case_id)
    return tuple(
        EquivalenceClass(exposed_attributes=key, case_ids=tuple(case_ids)) for key, case_ids in groups.items()
    )


def summarize_equivalence_classes(
    *,
    config_name: str,
    equivalence_classes: tuple[EquivalenceClass, ...],
    total_cases: int,
    threshold: int,
) -> KAnonymityResult:
    if total_cases == 0 or not equivalence_classes:
        raise ValueError(f"Cannot summarize k-anonymity for {config_name!r} with zero cases")

    ks = [equivalence_class.k for equivalence_class in equivalence_classes]
    singleton_case_count = sum(equivalence_class.k for equivalence_class in equivalence_classes if equivalence_class.k == 1)

    return KAnonymityResult(
        config_name=config_name,
        total_cases=total_cases,
        equivalence_classes=equivalence_classes,
        total_equivalence_classes=len(equivalence_classes),
        minimum_k=min(ks),
        median_k=statistics.median(ks),
        percentage_singletons=(singleton_case_count / total_cases) * 100,
        meets_threshold=min(ks) >= threshold,
    )


def compute_k_anonymity(
    cases: list[dict[str, Any]],
    config: PolicyConfig,
    *,
    derive_attribute: DeriveAttribute | None,
    threshold: int = MIN_K_THRESHOLD,
) -> KAnonymityResult:
    """The library entry point: k-anonymity for `config` over `cases`. Pure and
    side-effect-free -- takes already-loaded data in, returns dataclasses out, never touches
    disk or prints anything itself."""
    exposures = [
        compute_case_exposure(
            case_id=case["case_id"],
            ground_truth=case["ground_truth"],
            reference_date=date.fromisoformat(case["reference_date"]),
            config=config,
            derive_attribute=derive_attribute,
        )
        for case in cases
    ]
    equivalence_classes = group_into_equivalence_classes(exposures)
    return summarize_equivalence_classes(
        config_name=config.name,
        equivalence_classes=equivalence_classes,
        total_cases=len(cases),
        threshold=threshold,
    )


def load_dataset_cases(path: Path = DATASET_PATH) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = payload["cases"]
    return cases


def load_named_configs(directory: Path = POLICY_CONFIGS_DIR) -> dict[str, PolicyConfig]:
    return {name: load_policy_config(directory / f"{name}.json") for name in NAMED_CONFIGS}


def compute_k_anonymity_for_named_configs(
    cases: list[dict[str, Any]] | None = None,
    configs: dict[str, PolicyConfig] | None = None,
    *,
    threshold: int = MIN_K_THRESHOLD,
) -> dict[str, KAnonymityResult]:
    """Convenience wrapper computing compute_k_anonymity for every named config
    (DECISIONS.md V9). Defaults load the real committed dataset/configs; both are
    overridable so tests never need the real files on disk."""
    resolved_cases = cases if cases is not None else load_dataset_cases()
    resolved_configs = configs if configs is not None else load_named_configs()
    return {
        name: compute_k_anonymity(
            resolved_cases,
            config,
            derive_attribute=_CONFIG_DERIVE_ATTRIBUTES.get(name),
            threshold=threshold,
        )
        for name, config in resolved_configs.items()
    }


def result_to_dict(result: KAnonymityResult) -> dict[str, Any]:
    """Pure serialization of one KAnonymityResult into a JSON-native dict -- the analysis
    dataclasses above are the library's real API; this exists only so the CLI wrapper (and
    any later RESULTS.md-generation script) has something json.dumps can take directly."""
    return {
        "config_name": result.config_name,
        "total_cases": result.total_cases,
        "total_equivalence_classes": result.total_equivalence_classes,
        "minimum_k": result.minimum_k,
        "median_k": result.median_k,
        "percentage_singletons": result.percentage_singletons,
        "meets_threshold": result.meets_threshold,
        "equivalence_classes": [
            {"exposed_attributes": dict(equivalence_class.exposed_attributes), "k": equivalence_class.k, "case_ids": list(equivalence_class.case_ids)}
            for equivalence_class in result.equivalence_classes
        ],
    }


def main() -> None:
    """Thin CLI wrapper (this commit's own instruction: "keep analysis independent of CLI
    output"). All computation above is already done by the time this function runs anything
    -- this only loads the real committed inputs, prints a small summary, and writes the
    stored artifact RESULTS.md will eventually cite (CLAUDE.md §11: every claim traceable to
    a stored run)."""
    results = compute_k_anonymity_for_named_configs()

    for name in NAMED_CONFIGS:
        result = results[name]
        status = "OK" if result.meets_threshold else f"BELOW THRESHOLD (< {MIN_K_THRESHOLD})"
        print(
            f"{name}: min_k={result.minimum_k} median_k={result.median_k} "
            f"singletons={result.percentage_singletons:.1f}% "
            f"classes={result.total_equivalence_classes} [{status}]"
        )

    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(
        json.dumps({name: result_to_dict(result) for name, result in results.items()}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Results written to {RESULT_PATH}")


if __name__ == "__main__":
    main()
