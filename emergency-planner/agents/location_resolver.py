"""Location Resolver agent: turns the user's free text into a geocoded point.

Toolset: {geocode_location}. The LLM extracts the place name (or picks the candidate the
user chose after a clarification question) and calls the tool; the tool's structured
result decides whether the location is resolved, ambiguous or not found.
"""

from langchain_core.messages import HumanMessage, SystemMessage

from config import get_chat_model
from models.state import EmergencyPlanState
from tools.geocode import GeocodeOutcome, geocode_location

SYSTEM_PROMPT = """You are the Location Resolver agent of a Lebanon emergency response assistant.
Call geocode_location exactly once with the place in Lebanon the user is talking about.
- Pass only the place name, keeping any qualifier that helps disambiguate ("Hamra, Beirut").
- Keep the user's spelling, even if it looks like a typo; the tool handles fuzzy matching.
- If the user is choosing from a numbered list of candidates, pass that candidate's exact text.
- If no place is mentioned at all, pass an empty string."""


def location_resolver_node(state: EmergencyPlanState) -> dict:
    if state.awaiting_clarification and state.candidates:
        options = "\n".join(f"{i}. {c}" for i, c in enumerate(state.candidates, 1))
        prompt = (f"Original request: {state.original_query}\nCandidates offered to the user:\n{options}\n"
                  f"User's choice: {state.user_query}")
    else:
        prompt = f"User message: {state.user_query}"

    # "any" (= must call a tool) rather than naming the tool: it is the only tool,
    # and "any" is accepted by both the OpenAI and Gemini chat clients.
    llm = get_chat_model().bind_tools([geocode_location], tool_choice="any")
    ai = llm.invoke([SystemMessage(SYSTEM_PROMPT), HumanMessage(prompt)])
    if ai.tool_calls:
        call = ai.tool_calls[0]
    else:
        call = {"name": geocode_location.name, "args": {"place": state.user_query}, "id": "fallback", "type": "tool_call"}
    place = call["args"].get("place", "")

    # A pick from the offered list reuses the stored coordinates: geocoder candidates
    # (e.g. two different "Hamra"s) can't be looked up again reliably by label.
    chosen = next((c for c in state.candidate_locations if c.name.lower() == place.strip().lower()), None)
    if state.awaiting_clarification and chosen:
        outcome = GeocodeOutcome("resolved", "clarification",
                                 chosen.model_copy(update={"source": "clarification", "confidence": 1.0}),
                                 message=f"user picked {chosen.name}")
    else:
        outcome = geocode_location.invoke(call).artifact

    update = {
        "location_text": place,
        "node_trace": state.node_trace + ["location_resolver"],
        "tool_trace": [f"geocode_location(place={place!r}) -> {outcome.status} via {outcome.method}: {outcome.message}"],
        "awaiting_clarification": False,
        "is_ambiguous": outcome.status == "ambiguous",
        "resolved_location": outcome.location,
        "candidates": [c.name for c in outcome.candidates],
        "candidate_locations": outcome.candidates,
        "error": None,
    }
    if outcome.status == "not_found":
        update["error"] = outcome.message
    return update