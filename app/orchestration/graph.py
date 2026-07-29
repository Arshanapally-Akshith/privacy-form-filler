"""Standalone orchestration graph (`ARCHITECTURE.md` §7; `BUILD.md` Phase 5, task 2).

Ports `app.api.cases._process_case`'s hand-written loop into a LangGraph graph with
unchanged behavior: `extract_next_field` (wrapping `extract_field`) is invoked once per
form-schema field, in schema-declared order, with the same arguments the existing loop
already uses. No retries, no verifier, no conditional routing beyond "more fields pending
or not," no checkpointing -- those are later Phase 5 commits.

Not wired into the API in this commit. `app.api.cases._process_case` is untouched and
remains the live orchestrator until commit 4 swaps it for `run_graph` below. This module
has no dependency on `app.api` at all -- only on `app.orchestration.nodes` and
`app.orchestration.state`.
"""

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.orchestration.nodes import EXTRACT_NEXT_FIELD_NODE, extract_next_field
from app.orchestration.state import OrchestrationState


def _route_on_pending_fields(state: OrchestrationState) -> str:
    return EXTRACT_NEXT_FIELD_NODE if state.pending_field_names else END


def build_graph() -> CompiledStateGraph[OrchestrationState, Any, Any, Any]:
    graph: StateGraph[OrchestrationState, Any, Any, Any] = StateGraph(OrchestrationState)
    graph.add_node(EXTRACT_NEXT_FIELD_NODE, extract_next_field)
    graph.add_conditional_edges(START, _route_on_pending_fields)
    graph.add_conditional_edges(EXTRACT_NEXT_FIELD_NODE, _route_on_pending_fields)
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
