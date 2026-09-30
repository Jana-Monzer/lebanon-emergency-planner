"""Router agent: classifies intent with Structured Output Mode. No tools."""

from langchain_core.messages import HumanMessage, SystemMessage

from config import structured
from models.schemas import RouterDecision
from models.state import PER_REQUEST_DEFAULTS, EmergencyPlanState

SYSTEM_PROMPT = """You are the Router agent of a Lebanon emergency response assistant.
Classify the user's latest message into exactly one intent:
- find_shelter: they need a safe place / shelter / somewhere to stay or evacuate to
- find_hospital: they need medical care, an injury, an ambulance destination, a hospital
- full_emergency_plan: they want an overall plan for a location, or both a shelter and a hospital,
  or describe an emergency (shelling, fire, earthquake...) at a place without being more specific
- general_question: anything answerable without looking up facilities near a specific place
Set is_clarification_reply=true only if the assistant is waiting for the user to pick one of
several places and the message is that choice (a number, a place name, 'the second one', ...)."""


def router_node(state: EmergencyPlanState) -> dict:
    context = ""
    if state.awaiting_clarification and state.candidates:
        options = "\n".join(f"{i}. {c}" for i, c in enumerate(state.candidates, 1))
        context = (f"The assistant is waiting for the user to choose one of these places "
                   f"(original request: {state.original_query!r}):\n{options}\n\n")

    decision: RouterDecision = structured(RouterDecision).invoke(
        [SystemMessage(SYSTEM_PROMPT), HumanMessage(f"{context}User message: {state.user_query}")]
    )

    common = {
        "turn": state.turn + 1,
        "history": state.history + [{"role": "user", "content": state.user_query}],
        "node_trace": ["router"],
        "router_rationale": decision.rationale,
    }
    if state.awaiting_clarification and decision.is_clarification_reply:
        # Continue the pending request: keep its intent and candidates from the checkpoint.
        return {**common, "is_ambiguous": False}
    return {**PER_REQUEST_DEFAULTS, **common, "intent": decision.intent,
            "router_rationale": decision.rationale, "original_query": state.user_query}