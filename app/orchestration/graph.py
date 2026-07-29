"""Standalone orchestration graph (`ARCHITECTURE.md` §7; `BUILD.md` Phase 5, tasks 2-6).

Ports `app.api.cases._process_case`'s hand-written loop into a LangGraph graph, with the
full routing table `ARCH §7` specifies:

    extract_next_field -> verify_current_field -> apply_verifier_decision -> [routing]
        accept / escalate / retry-budget-exhausted -> next pending field, or END
        re_retrieve, budget remaining                -> retry_current_field -> back to
                                                          verify_current_field

Every field is extracted, verified, and has its verifier decision applied exactly once per
attempt; a field can loop through verify/apply/retry more than once, but only up to the
bounded retry budget (`DECISIONS.md` E6, enforced entirely inside
`app.orchestration.nodes.apply_verifier_decision` -- no possibility of an unbounded loop,
since that node is the only place `retry_count` is ever incremented and it never allows
`retry_count` to exceed E6). No checkpointing yet -- that is a later Phase 5 commit.

Wired into the API since commit 4 (`app.api.cases._process_case` calls `run_graph`
below). This module has no dependency on `app.api` at all -- only on
`app.orchestration.nodes` and `app.orchestration.state`.
"""

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.api.models import FieldState
from app.orchestration.nodes import (
    APPLY_VERIFIER_DECISION_NODE,
    EXTRACT_NEXT_FIELD_NODE,
    RETRY_CURRENT_FIELD_NODE,
    VERIFY_CURRENT_FIELD_NODE,
    apply_verifier_decision,
    extract_next_field,
    retry_current_field,
    verify_current_field,
)
from app.orchestration.state import OrchestrationState, VerifierDecision


def _route_on_pending_fields(state: OrchestrationState) -> str:
    return EXTRACT_NEXT_FIELD_NODE if state.pending_field_names else END


def _route_after_verifier_decision(state: OrchestrationState) -> str:
    """Reads, never writes -- `apply_verifier_decision` (which always runs immediately
    before this router) already owns every state transition; this function only decides
    the next node from what that transition produced.

    A retry is taken exactly when the latest decision is `re_retrieve` *and*
    `apply_verifier_decision` did not just flag the field `LOW_CONFIDENCE` -- the only
    signal that distinguishes "budget remaining, retry_count was just incremented" from
    "budget exhausted, no further increment" for the identical `re_retrieve` decision.
    Accept and escalate never retry, so both fall straight through to the same
    next-field-or-END check every non-retrying path uses.
    """
    field_name = state.current_field_name
    if field_name is None:
        raise AssertionError(
            "_route_after_verifier_decision ran with no current_field_name set -- "
            "apply_verifier_decision must always run immediately before this router"
        )
    field_graph_state = state.fields[field_name]
    last_decision = field_graph_state.verifier_traces[-1].decision
    if last_decision is VerifierDecision.RE_RETRIEVE and field_graph_state.record.state != FieldState.LOW_CONFIDENCE:
        return RETRY_CURRENT_FIELD_NODE
    return _route_on_pending_fields(state)


def build_graph() -> CompiledStateGraph[OrchestrationState, Any, Any, Any]:
    graph: StateGraph[OrchestrationState, Any, Any, Any] = StateGraph(OrchestrationState)
    graph.add_node(EXTRACT_NEXT_FIELD_NODE, extract_next_field)
    graph.add_node(VERIFY_CURRENT_FIELD_NODE, verify_current_field)
    graph.add_node(APPLY_VERIFIER_DECISION_NODE, apply_verifier_decision)
    graph.add_node(RETRY_CURRENT_FIELD_NODE, retry_current_field)

    graph.add_conditional_edges(START, _route_on_pending_fields)
    graph.add_edge(EXTRACT_NEXT_FIELD_NODE, VERIFY_CURRENT_FIELD_NODE)
    graph.add_edge(VERIFY_CURRENT_FIELD_NODE, APPLY_VERIFIER_DECISION_NODE)
    graph.add_conditional_edges(APPLY_VERIFIER_DECISION_NODE, _route_after_verifier_decision)
    graph.add_edge(RETRY_CURRENT_FIELD_NODE, VERIFY_CURRENT_FIELD_NODE)
    return graph.compile()


def run_graph(state: OrchestrationState) -> OrchestrationState:
    """Run the graph to completion and return a typed `OrchestrationState`.

    `CompiledStateGraph.invoke()` returns a plain `dict` of final channel values
    regardless of the state schema type -- verified directly against the installed
    langgraph==1.2.9, not assumed. Reconstructing `OrchestrationState` here means every
    caller of this module, including commit 4's API integration, works with the same
    typed state throughout rather than a raw dict.
    """
    result = build_graph().invoke(state)
    return OrchestrationState(**result)
