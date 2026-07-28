"""Policy config loading, structural validation, and the semantic co-occurrence guard
(BUILD.md Phase 3, task 7; guard closed out in Phase 4 per that task's own deferral note).

`load_policy_config` does structural validation only: does a config file conform to the
frozen Phase 0 schema (app/config/policy_config.schema.json)? Reuses that schema and
jsonschema.validate rather than re-encoding its rules -- the schema is the single source of
truth for config shape. This module does not modify or extend that schema. Mirrors
app.config.form_schema's loader pattern: parse -> validate -> typed model, wrapping the
validation library's exception in a project-specific error so callers only ever catch one
type regardless of what failed underneath.

`check_cooccurrence_guard` is the semantic half (ARCHITECTURE.md §5.2, DECISIONS.md P11 --
"a config declaring a banned combination fails at load"), deliberately a *separate* function
from `load_policy_config` rather than a parameter on it: config loading is a static
operation on a file, and does not need request-specific context to do its job. Which
attribute a Derive field actually exposes (state vs district) is exactly that kind of
context -- `field_actions` only says a field's action is `derive`, never which attribute --
so the guard takes it as an explicit `derive_attributes` parameter from its caller, the same
stance app.privacy.dispatch.apply_action already takes for `derive_attribute` at dispatch
time. This function never inspects `config.name` or otherwise special-cases which named
config it was given; it is a pure function of (config, derive_attributes) -> pass or raise.

A caller that needs both a validated PolicyConfig and the co-occurrence guarantee calls
`load_policy_config` then `check_cooccurrence_guard` explicitly, in that order -- there is
no single function that does both, by design.
"""

import json
from pathlib import Path

import jsonschema
from pydantic import BaseModel, ConfigDict

from app.privacy.constants import GENERALIZE_ATTRIBUTE_NAME
from app.privacy.dispatch import DeriveAttribute, PolicyAction

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "config" / "policy_config.schema.json"
_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


class PolicyConfigError(ValueError):
    """Base class for policy config loading and validation failures."""


class PolicyConfigSchemaError(PolicyConfigError):
    """Raised when a config file does not conform to the frozen policy config JSON schema
    (app/config/policy_config.schema.json)."""


class PolicyConfigMissingDeriveAttributeError(PolicyConfigError):
    """Raised when `derive_attributes` does not exactly match the config's Derive fields --
    either a Derive field has no entry (the guard cannot know what it exposes) or an entry
    exists for a field that is not a Derive field in this config (almost certainly a caller
    typo; CLAUDE.md §5 -- fail loudly rather than silently ignore it)."""


class PolicyConfigCooccurrenceError(PolicyConfigError):
    """Raised when a config's actual exposed combination of derived/generalized attributes
    is not declared in its own `permitted_cooccurrence_sets` (ARCHITECTURE.md §5.2)."""


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    dataset_version: str
    field_actions: dict[str, PolicyAction]
    permitted_cooccurrence_sets: list[list[str]]


def load_policy_config(path: Path) -> PolicyConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(instance=raw, schema=_SCHEMA)
    except jsonschema.ValidationError as exc:
        raise PolicyConfigSchemaError(f"Invalid policy config at {path}: {exc.message}") from exc
    return PolicyConfig.model_validate(raw)


def check_cooccurrence_guard(config: PolicyConfig, derive_attributes: dict[str, DeriveAttribute] | None = None) -> None:
    """ARCHITECTURE.md §5.2 / DECISIONS.md P11: fails loudly if `config` would expose a
    combination of derived/generalized attributes it has not itself declared permitted.
    Scoped to Generalize and Derive only -- a tokenized value is reversible ciphertext of
    the same field, not a new quasi-identifier, so Tokenize/Pass-through fields are not
    part of the exposed combination this guard reasons about.
    """
    derive_attributes = derive_attributes or {}
    derive_fields = {name for name, action in config.field_actions.items() if action is PolicyAction.DERIVE}

    missing = derive_fields - derive_attributes.keys()
    extra = derive_attributes.keys() - derive_fields
    if missing or extra:
        raise PolicyConfigMissingDeriveAttributeError(
            f"Config {config.name!r}: derive_attributes must have exactly one entry per Derive "
            f"field -- missing {sorted(missing)}, unexpected {sorted(extra)}"
        )

    exposed: set[str] = set()
    for field_name, action in config.field_actions.items():
        if action is PolicyAction.GENERALIZE:
            exposed.add(GENERALIZE_ATTRIBUTE_NAME)
        elif action is PolicyAction.DERIVE:
            exposed.add(derive_attributes[field_name].value)

    if not exposed:
        return  # Nothing derived or generalized -- nothing to permit (e.g. `strict`).

    permitted = {frozenset(s) for s in config.permitted_cooccurrence_sets}
    if frozenset(exposed) not in permitted:
        raise PolicyConfigCooccurrenceError(
            f"Config {config.name!r} exposes attribute combination {sorted(exposed)}, which is "
            f"not declared in its permitted_cooccurrence_sets {config.permitted_cooccurrence_sets}"
        )
