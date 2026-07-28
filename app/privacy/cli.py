"""Privacy engine CLI (BUILD.md Phase 3, task 9: "CLI for manual inspection: text in,
protected text out, map dumped").

Thin orchestration only -- no detection, transformation, or validation logic of its own.
Composes app.privacy.detection, app.privacy.dispatch, and app.privacy.tokenize (all
unmodified) for the `protect` command, plus app.privacy.policy_config (also unmodified,
used only within `demo`).

`protect` operates on arbitrary free text, driven only by explicit --action overrides plus
the engine's existing fail-closed default. It deliberately does NOT accept a named policy
config: PolicyConfig.field_actions is keyed by form field name, a concept that does not
exist for arbitrary prose -- resolving one action from a config and applying it uniformly
to whatever entities happen to appear in free text would misrepresent how policies actually
operate. `demo` instead loads each named config and shows, for representative
(field name, value) pairs matching that config's own field vocabulary, exactly what action
it resolves and what dispatching it produces -- the one context where a policy config's
field-name-keyed resolution is actually meaningful.
"""

import argparse
import json
import sys
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.privacy.detection import DetectedEntity, EntityType, detect_entities
from app.privacy.dispatch import (
    DeriveAttribute,
    PolicyAction,
    PolicyDispatchError,
    apply_action,
    resolve_action,
)
from app.privacy.policy_config import load_policy_config
from app.privacy.tokenize import reverse_entity

_POLICY_CONFIGS_DIR = Path(__file__).resolve().parent.parent / "config" / "policy_configs"

_ActionOverride = tuple[EntityType, PolicyAction, str | None]


@dataclass
class EntityResult:
    entity_type: EntityType
    text: str
    action: PolicyAction
    result: str | None = None
    error: str | None = None
    reversed_ok: bool | None = None


# --- argument parsing --------------------------------------------------------------------


def _parse_action_override(raw: str) -> _ActionOverride:
    if "=" not in raw:
        raise argparse.ArgumentTypeError(f"--action must be TYPE=ACTION or TYPE=ACTION:PARAM, got {raw!r}")
    type_part, action_part = raw.split("=", 1)
    action_str, _, param = action_part.partition(":")

    try:
        entity_type = EntityType(type_part)
    except ValueError as exc:
        valid = ", ".join(sorted(t.value for t in EntityType))
        raise argparse.ArgumentTypeError(f"Unknown entity type {type_part!r}. Valid: {valid}") from exc
    try:
        action = PolicyAction(action_str)
    except ValueError as exc:
        valid = ", ".join(sorted(a.value for a in PolicyAction))
        raise argparse.ArgumentTypeError(f"Unknown action {action_str!r}. Valid: {valid}") from exc

    return entity_type, action, (param or None)


def _parse_date_arg(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--reference-date must be YYYY-MM-DD, got {raw!r}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.privacy.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    protect_parser = subparsers.add_parser(
        "protect", help="Detect and protect PII in free text using the privacy engine."
    )
    protect_parser.add_argument("text", nargs="?", help="Text to protect. Omit to use --file or stdin.")
    protect_parser.add_argument("--file", type=Path, help="Read text from a file instead of the positional argument.")
    protect_parser.add_argument("--session", default=None, help="Session id (default: a generated UUID).")
    protect_parser.add_argument(
        "--reference-date", type=_parse_date_arg, default=None, help="YYYY-MM-DD, used for Generalize. Default: today."
    )
    protect_parser.add_argument(
        "--action",
        action="append",
        default=[],
        type=_parse_action_override,
        metavar="TYPE=ACTION[:PARAM]",
        help="Override the action for one detected entity type, e.g. pin_code=derive:state. "
        "Repeatable. Anything not overridden uses the engine's fail-closed default (tokenize).",
    )
    protect_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of text.")

    subparsers.add_parser(
        "demo", help="Run a built-in demonstration of all four actions and the three named policy configs."
    )

    return parser


# --- dispatch + reconstruction -----------------------------------------------------------


def _dispatch_one(
    entity: DetectedEntity,
    overrides: dict[EntityType, tuple[PolicyAction, str | None]],
    session_id: str,
    reference_date: date,
) -> EntityResult:
    override = overrides.get(entity.entity_type)
    explicit_action = override[0] if override else None
    action = resolve_action(explicit_action)

    derive_attribute = None
    if override is not None and override[1] is not None:
        try:
            derive_attribute = DeriveAttribute(override[1])
        except ValueError:
            return EntityResult(
                entity.entity_type,
                entity.text,
                action,
                error=f"Invalid derive attribute {override[1]!r}; expected 'state' or 'district'",
            )

    try:
        result = apply_action(
            action, entity, session_id=session_id, reference_date=reference_date, derive_attribute=derive_attribute
        )
    except PolicyDispatchError as exc:
        return EntityResult(entity.entity_type, entity.text, action, error=str(exc))

    reversed_ok = None
    if action is PolicyAction.TOKENIZE:
        # Same process, same session -- the key tokenize_entity just created is guaranteed
        # present; there is no failure mode to handle here.
        reversed_ok = reverse_entity(session_id, entity.entity_type, result) == entity.text

    return EntityResult(entity.entity_type, entity.text, action, result=result, reversed_ok=reversed_ok)


def _reconstruct_protected_text(
    text: str, entities: list[DetectedEntity], results: list[EntityResult]
) -> tuple[str, list[bool]]:
    """ADDRESS is detection.py's one documented span-overlap exception: its window can
    contain other already-claimed entities (e.g. a nested PIN_CODE). Substituting
    largest-span-first and skipping anything fully nested inside an already-substituted
    span keeps reconstruction correct without special-casing entity types."""
    order = sorted(range(len(entities)), key=lambda i: entities[i].end - entities[i].start, reverse=True)
    substituted_spans: list[tuple[int, int]] = []
    used = [False] * len(entities)
    for i in order:
        e = entities[i]
        if any(e.start >= s and e.end <= en for s, en in substituted_spans):
            continue
        substituted_spans.append((e.start, e.end))
        used[i] = True

    pieces: list[str] = []
    cursor = 0
    for i in sorted(range(len(entities)), key=lambda i: entities[i].start):
        if not used[i]:
            continue
        entity, result = entities[i], results[i]
        pieces.append(text[cursor : entity.start])
        pieces.append(result.result if result.result is not None else f"<{entity.entity_type.value}:ERROR>")
        cursor = entity.end
    pieces.append(text[cursor:])
    return "".join(pieces), used


# --- reporting --------------------------------------------------------------------------


def _build_report(
    text: str,
    protected_text: str,
    session_id: str,
    entities: list[DetectedEntity],
    results: list[EntityResult],
    used: list[bool],
) -> dict:
    return {
        "session_id": session_id,
        "input_text": text,
        "protected_text": protected_text,
        "entities": [
            {
                "entity_type": entity.entity_type.value,
                "text": result.text,
                "action": result.action.value,
                "result": result.result,
                "error": result.error,
                "reversed_ok": result.reversed_ok,
                "substituted_in_output": was_used,
            }
            for entity, result, was_used in zip(entities, results, used)
        ],
    }


def _print_text_report(
    protected_text: str, session_id: str, results: list[EntityResult], used: list[bool]
) -> None:
    print(f"Session: {session_id}")
    print()
    print("Protected text:")
    print(protected_text)
    print()
    print("Map:")
    if not results:
        print("  (no entities detected)")
    for result, was_used in zip(results, used):
        line = f"  [{result.entity_type.value}] {result.text!r} -> action={result.action.value}"
        if result.error is not None:
            line += f" -> ERROR: {result.error}"
        else:
            line += f" -> {result.result!r}"
            if result.reversed_ok is not None:
                line += f" (reverses ok: {result.reversed_ok})"
        if not was_used:
            line += " [nested inside a larger substituted span; not separately substituted in output]"
        print(line)


# --- commands ---------------------------------------------------------------------------


def _read_input_text(args: argparse.Namespace) -> str:
    if args.text is not None:
        return str(args.text)
    if args.file is not None:
        return args.file.read_text(encoding="utf-8")
    return sys.stdin.read()


def _run_protect(args: argparse.Namespace) -> int:
    text = _read_input_text(args)
    session_id = args.session or str(uuid.uuid4())
    # CLI-layer convenience default only -- generalize_dob() itself still requires an
    # explicit reference_date and never reads the clock internally (generalize.py).
    reference_date = args.reference_date or date.today()  # noqa: DTZ011
    overrides = {entity_type: (action, param) for entity_type, action, param in args.action}

    entities = detect_entities(text)
    results = [_dispatch_one(e, overrides, session_id, reference_date) for e in entities]
    protected_text, used = _reconstruct_protected_text(text, entities, results)

    if args.json:
        print(json.dumps(_build_report(text, protected_text, session_id, entities, results, used), indent=2))
    else:
        _print_text_report(protected_text, session_id, results, used)

    return 1 if any(r.error is not None for r in results) else 0


_DEMO_SAMPLE_TEXT = (
    "Applicant PAN: ABCDE1234F\n"
    "Date of Birth: 15/06/1990\n"
    "PIN Code: 110001\n"
    "Phone: 9876543210\n"
)

_DEMO_ACTIONS: dict[EntityType, tuple[PolicyAction, str | None]] = {
    EntityType.PAN: (PolicyAction.TOKENIZE, None),
    EntityType.DATE: (PolicyAction.GENERALIZE, None),
    EntityType.PIN_CODE: (PolicyAction.DERIVE, "state"),
    EntityType.PHONE: (PolicyAction.PASS_THROUGH, None),
}

# Representative (field, value) pairs matching the field vocabulary the three named
# configs actually declare (app/config/policy_configs/) -- this is the one context where
# a policy config's field-name-keyed resolution is meaningful, unlike arbitrary free text.
_CONFIG_DEMO_FIELDS: dict[str, tuple[EntityType, str]] = {
    "date_of_birth": (EntityType.DATE, "15/06/1990"),
    "pin_code": (EntityType.PIN_CODE, "110001"),
    "pan_number": (EntityType.PAN, "ABCDE1234F"),
}

_CONFIG_DERIVE_ATTRIBUTES = {
    "age_state": DeriveAttribute.STATE,
    "ageband_city": DeriveAttribute.DISTRICT,
}


def _demo_named_configs(reference_date: date) -> int:
    had_error = False
    for config_name in ("strict", "age_state", "ageband_city"):
        config = load_policy_config(_POLICY_CONFIGS_DIR / f"{config_name}.json")
        print(f"\n{config_name}:")
        for field_name, (entity_type, value) in _CONFIG_DEMO_FIELDS.items():
            explicit_action = config.field_actions.get(field_name)
            action = resolve_action(explicit_action)
            entity = DetectedEntity(entity_type=entity_type, text=value, start=0, end=len(value))
            derive_attribute = _CONFIG_DERIVE_ATTRIBUTES.get(config_name) if action is PolicyAction.DERIVE else None

            try:
                result = apply_action(
                    action,
                    entity,
                    session_id=f"demo-{config_name}",
                    reference_date=reference_date,
                    derive_attribute=derive_attribute,
                )
                print(f"  {field_name}: action={action.value} -> {result!r}")
            except PolicyDispatchError as exc:
                had_error = True
                print(f"  {field_name}: action={action.value} -> ERROR: {exc}")
    return 1 if had_error else 0


def _run_demo() -> int:
    reference_date = date(2026, 7, 28)

    print("=" * 70)
    print("Section 1: all four actions on a sample document")
    print("=" * 70)
    entities = detect_entities(_DEMO_SAMPLE_TEXT)
    results = [_dispatch_one(e, _DEMO_ACTIONS, "demo-session", reference_date) for e in entities]
    protected_text, used = _reconstruct_protected_text(_DEMO_SAMPLE_TEXT, entities, results)
    _print_text_report(protected_text, "demo-session", results, used)
    exit_code = 1 if any(r.error is not None for r in results) else 0

    print()
    print("=" * 70)
    print("Section 2: named policy configs (app/config/policy_configs/)")
    print("=" * 70)
    exit_code |= _demo_named_configs(reference_date)

    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "protect":
        return _run_protect(args)
    if args.command == "demo":
        return _run_demo()
    raise AssertionError(f"Unhandled command: {args.command!r}")  # argparse required=True makes this unreachable


if __name__ == "__main__":
    sys.exit(main())
