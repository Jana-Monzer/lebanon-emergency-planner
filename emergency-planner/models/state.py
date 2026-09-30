"""The single LangGraph state object, checkpointed by MemorySaver between turns."""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from models.entities import Hospital, Intent, ResolvedLocation, SchoolShelter
from models.schemas import EmergencyPlan

DEFAULT_RADIUS_KM = 5.0

# Fields that describe a single request. The router clears them when a new request
# starts, but keeps them when the user is answering a clarification question.
PER_REQUEST_DEFAULTS = {
    "intent": None,
    "router_rationale": None,
    "location_text": None,
    "resolved_location": None,
    "is_ambiguous": False,
    "awaiting_clarification": False,
    "candidates": [],
    "candidate_locations": [],
    "effective_radius_km": None,
    "radius_expanded": False,
    "nearest_shelters": [],
    "nearest_hospitals": [],
    "shelter_to_hospital_km": None,
    "plan": None,
    "assistant_message": None,
    "error": None,
    "tool_trace": [],
}


class EmergencyPlanState(BaseModel):
    # conversation
    user_query: str = ""
    original_query: Optional[str] = None
    turn: int = 0
    history: List[Dict[str, str]] = Field(default_factory=list)
    node_trace: List[str] = Field(default_factory=list)

    # router
    intent: Optional[Intent] = None
    router_rationale: Optional[str] = None

    # location resolver
    location_text: Optional[str] = None
    resolved_location: Optional[ResolvedLocation] = None
    is_ambiguous: bool = False
    awaiting_clarification: bool = False
    candidates: List[str] = Field(default_factory=list)
    candidate_locations: List[ResolvedLocation] = Field(default_factory=list)

    # facility finder
    search_radius_km: float = Field(default=DEFAULT_RADIUS_KM, gt=0)
    effective_radius_km: Optional[float] = None
    radius_expanded: bool = False
    nearest_shelters: List[SchoolShelter] = Field(default_factory=list)
    nearest_hospitals: List[Hospital] = Field(default_factory=list)
    shelter_to_hospital_km: Optional[float] = None
    tool_trace: List[str] = Field(default_factory=list)

    # output
    plan: Optional[EmergencyPlan] = None
    assistant_message: Optional[str] = None
    error: Optional[str] = None
