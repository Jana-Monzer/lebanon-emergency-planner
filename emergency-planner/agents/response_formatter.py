"""Response Formatter agent: writes the final EmergencyPlan with Structured Output Mode."""

import json

from langchain_core.messages import HumanMessage, SystemMessage

from config import structured
from models.schemas import EmergencyPlan
from models.state import EmergencyPlanState

SYSTEM_PROMPT = """You are the Response Formatter agent of a Lebanon emergency response assistant.
Write an EmergencyPlan for the user using ONLY the facts provided. Never invent facilities,
distances, phone numbers or capacities.
- nearest_shelter / nearest_hospital: copy the first entry of the corresponding list, or null if the
  list is empty or was not requested. Distances likewise.
- instructions: 4-7 short, concrete, ordered steps that name the actual facilities and distances.
- If a requested list is empty, say so in warnings (mention the radius searched) and advise calling
  emergency services instead. Do not mention or warn about a facility type that was not requested
  (shelters_requested / hospitals_requested are false): it was simply not searched.
- emergency_contacts: Lebanese Red Cross 140, Civil Defense 125, Internal Security Forces 112
  (include the ones relevant to the situation).
- For a general_question there is no location lookup: answer it in the summary, give practical
  preparedness instructions, and leave facilities and distances null.
- Note that shelter data comes from a schools layer; num_classes is only a rough size indicator."""


def _facts(state: EmergencyPlanState) -> dict:
    return {
        "user_request": state.original_query or state.user_query,
        "intent": state.intent,
        "shelters_requested": state.intent in ("find_shelter", "full_emergency_plan"),
        "hospitals_requested": state.intent in ("find_hospital", "full_emergency_plan"),
        "location": state.resolved_location.model_dump() if state.resolved_location else None,
        "search_radius_km": state.search_radius_km,
        "effective_radius_km": state.effective_radius_km,
        "radius_expanded": state.radius_expanded,
        "shelters_nearest_first": [s.model_dump() for s in state.nearest_shelters[:3]],
        "hospitals_nearest_first": [h.model_dump() for h in state.nearest_hospitals[:3]],
        "shelter_to_hospital_km": state.shelter_to_hospital_km,
        "lookup_error": state.error,
        "recent_conversation": state.history[-6:],
    }


def response_formatter_node(state: EmergencyPlanState) -> dict:
    plan: EmergencyPlan = structured(EmergencyPlan).invoke(
        [SystemMessage(SYSTEM_PROMPT),
         HumanMessage(json.dumps(_facts(state), ensure_ascii=False, indent=1))]
    )
    # The facility fields are overwritten from state so the plan can't drift from the layer data.
    shelter = state.nearest_shelters[0] if state.nearest_shelters else None
    hospital = state.nearest_hospitals[0] if state.nearest_hospitals else None
    plan = plan.model_copy(update={
        "nearest_shelter": shelter,
        "nearest_hospital": hospital,
        "distance_to_shelter_km": shelter.distance_km if shelter else None,
        "distance_to_hospital_km": hospital.distance_km if hospital else None,
    })
    return {
        "plan": plan,
        "assistant_message": plan.summary,
        "history": state.history + [{"role": "assistant", "content": plan.summary}],
        "node_trace": state.node_trace + ["response_formatter"],
    }