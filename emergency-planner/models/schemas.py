"""Pydantic schemas used with Structured Output Mode (``llm.with_structured_output``)."""

from typing import List, Optional

from pydantic import BaseModel, Field

from models.entities import Hospital, Intent, SchoolShelter


class RouterDecision(BaseModel):
    """Output of the Router agent."""

    intent: Intent = Field(
        description=(
            "find_shelter: user wants a shelter/safe place; find_hospital: user needs medical care; "
            "full_emergency_plan: user wants both or a general 'what do I do' plan for a location; "
            "general_question: no location-specific lookup needed"
        )
    )
    is_clarification_reply: bool = Field(
        description="True only if the message answers the assistant's pending 'which place did you mean?' question"
    )
    rationale: str = Field(description="One short sentence explaining the choice")


class EmergencyPlan(BaseModel):
    """Final plan produced by the Response Formatter agent."""

    summary: str = Field(description="2-3 sentence overview addressed to the user")
    nearest_shelter: Optional[SchoolShelter] = Field(description="Copy of the nearest shelter from the facts, or null")
    nearest_hospital: Optional[Hospital] = Field(description="Copy of the nearest hospital from the facts, or null")
    distance_to_shelter_km: Optional[float]
    distance_to_hospital_km: Optional[float]
    instructions: List[str] = Field(description="Ordered, concrete steps the user should take now")
    emergency_contacts: List[str] = Field(description="Relevant Lebanese emergency numbers, e.g. 'Lebanese Red Cross: 140'")
    warnings: List[str] = Field(description="Caveats such as nothing found within the radius or data limitations")
