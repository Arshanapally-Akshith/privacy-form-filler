"""Policy config loading and structural validation (BUILD.md Phase 3, task 7 -- structural
half only).

Structural validation only: does a config file conform to the frozen Phase 0 schema
(app/config/policy_config.schema.json)? Reuses that schema and jsonschema.validate rather
than re-encoding its rules -- the schema is the single source of truth for config shape.
This module does not modify or extend that schema.

The semantic co-occurrence guard (ARCHITECTURE.md §5.2, DECISIONS.md P11 -- "a config
declaring a banned combination fails at load") is deliberately NOT implemented here.
Checking it requires knowing which attribute a `derive` field actually exposes (state vs
district), and today's schema has no field for that -- resolving it depends on the
form-schema bindings BUILD.md Phase 4 task 5 introduces ("field actions read from the form
schema and active policy config"). Enforcing it now would require guessing or extending the
frozen schema, both out of scope for this commit; it is deferred to Phase 4 whole.

Mirrors app.config.form_schema's loader pattern: parse -> validate -> typed model, wrapping
the validation library's exception in a project-specific error so callers only ever catch
one type regardless of what failed underneath.
"""

import json
from pathlib import Path

import jsonschema
from pydantic import BaseModel, ConfigDict

from app.privacy.dispatch import PolicyAction

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "config" / "policy_config.schema.json"
_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


class PolicyConfigError(ValueError):
    """Base class for policy config loading failures."""


class PolicyConfigSchemaError(PolicyConfigError):
    """Raised when a config file does not conform to the frozen policy config JSON schema
    (app/config/policy_config.schema.json)."""


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
