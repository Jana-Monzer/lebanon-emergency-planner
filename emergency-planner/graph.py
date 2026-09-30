"""Builds and compiles the LangGraph.

    START -> router --(general_question)--------------------------------> response_formatter -> END
                    \\-(find_*/full_emergency_plan)-> location_resolver
                                                        |-- is_ambiguous ----------> clarification -> END
                                                        |-- resolved_location set -> facility_finder -> response_formatter -> END
                                                        \\-- otherwise -------------> resolution_error -> END
"""

from typing import Literal

import httpx
import requests
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from agents.facility_finder import facility_finder_node
from agents.location_resolver import location_resolver_node
from agents.response_formatter import response_formatter_node
from agents.router import router_node
from models.state import EmergencyPlanState

# Our own Pydantic classes that the checkpointer is allowed to deserialize.
CHECKPOINT_TYPES = [
    ("models.state", "EmergencyPlanState"),
    ("models.schemas", "EmergencyPlan"),
    ("models.entities", "ResolvedLocation"),
    ("models.entities", "SchoolShelter"),
    ("models.entities", "Hospital"),
]


# Re-run an LLM node on transient network failures (DNS, dropped connections). Only those:
# retrying quota errors would just spend more of the quota.
NETWORK_RETRY = RetryPolicy(max_attempts=3, initial_interval=2.0,
                            retry_on=(httpx.TransportError, requests.ConnectionError, ConnectionError))


def clarification_node(state: EmergencyPlanState) -> dict:
    options = "\n".join(f"  {i}. {c}" for i, c in enumerate(state.candidates, 1))
    message = (f"I found more than one place matching \"{state.location_text}\":\n{options}\n"
               f"Which one do you mean? Reply with the number or the name.")
    return {
        "awaiting_clarification": True,
        "assistant_message": message,
        "history": state.history + [{"role": "assistant", "content": message}],
        "node_trace": state.node_trace + ["clarification"],
    }


def resolution_error_node(state: EmergencyPlanState) -> dict:
    message = (f"Sorry, I couldn't find that location in Lebanon ({state.error or 'no match'}). "
               f"Please name a city, town or neighbourhood, e.g. \"Achrafieh\" or \"Tyre\". "
               f"If this is a life-threatening emergency call the Lebanese Red Cross on 140.")
    return {
        "assistant_message": message,
        "history": state.history + [{"role": "assistant", "content": message}],
        "node_trace": state.node_trace + ["resolution_error"],
    }


def route_after_router(state: EmergencyPlanState) -> Literal["location_resolver", "response_formatter"]:
    return "response_formatter" if state.intent == "general_question" else "location_resolver"


def route_after_resolver(state: EmergencyPlanState) -> Literal["clarification", "facility_finder", "resolution_error"]:
    if state.is_ambiguous:
        return "clarification"
    if state.resolved_location is not None:
        return "facility_finder"
    return "resolution_error"


def build_graph(checkpointer=None):
    builder = StateGraph(EmergencyPlanState)
    builder.add_node("router", router_node, retry_policy=NETWORK_RETRY)
    builder.add_node("location_resolver", location_resolver_node, retry_policy=NETWORK_RETRY)
    builder.add_node("clarification", clarification_node)
    builder.add_node("resolution_error", resolution_error_node)
    builder.add_node("facility_finder", facility_finder_node, retry_policy=NETWORK_RETRY)
    builder.add_node("response_formatter", response_formatter_node, retry_policy=NETWORK_RETRY)

    builder.add_edge(START, "router")
    builder.add_conditional_edges("router", route_after_router)
    builder.add_conditional_edges("location_resolver", route_after_resolver)
    builder.add_edge("facility_finder", "response_formatter")
    builder.add_edge("response_formatter", END)
    builder.add_edge("clarification", END)
    builder.add_edge("resolution_error", END)

    if checkpointer is None:
        checkpointer = MemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES))
    return builder.compile(checkpointer=checkpointer)
