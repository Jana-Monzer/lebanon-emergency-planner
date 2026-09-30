"""Nested domain models shared by the graph state and the structured-output schemas.

Kept in their own module so that ``state.py`` and ``schemas.py`` can both import them
without a circular import (the state holds an ``EmergencyPlan``, which nests these).
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

Intent = Literal["find_shelter", "find_hospital", "full_emergency_plan", "general_question"]

ResolutionSource = Literal["exact", "substring", "geocoder", "embedding", "clarification"]


class ResolvedLocation(BaseModel):
    """A free-text place name resolved to a WGS84 point."""

    name: str
    lat: float
    lon: float
    caza: Optional[str] = Field(default=None, description="District (caza) when known")
    source: ResolutionSource = Field(description="Which resolution tier produced this point")
    confidence: float = Field(ge=0.0, le=1.0)


class SchoolShelter(BaseModel):
    """A school flagged as an emergency shelter (field ``مركز_ايواء`` = 'Yes')."""

    name: str
    name_ar: Optional[str] = None
    cadastral: Optional[str] = None
    caza: Optional[str] = None
    governorate: Optional[str] = None
    lat: float
    lon: float
    distance_km: float
    num_classes: Optional[int] = Field(
        default=None,
        description="Class sections in 2022-2023; the layer's only capacity proxy",
    )
    phone: Optional[str] = None
    status: Optional[str] = Field(default=None, description="School status from the layer, e.g. 'closed'")


class Hospital(BaseModel):
    """A hospital from the Hospital_Labs layer (``Facility_T`` = hospitals)."""

    name: str
    name_ar: Optional[str] = None
    ownership: Optional[str] = None
    cadastral: Optional[str] = None
    district: Optional[str] = None
    governorate: Optional[str] = None
    lat: float
    lon: float
    distance_km: float
    has_lab: Optional[bool] = None
    has_blood_bank: Optional[bool] = None
    has_radiology: Optional[bool] = None
    phone: Optional[str] = None
