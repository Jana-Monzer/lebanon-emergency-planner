"""Facility Finder agent: finds the nearest shelters and hospitals around the resolved point.

Toolset: {query_schools_layer, query_hospitals_layer, haversine_distance} - disjoint from the
Location Resolver's. The LLM drives the tool calls (which layers, whether to widen the
radius); the node then verifies the required searches actually happened, so a skipped
call from the model can't silently produce an empty plan.
"""

from langchain_core.messages import HumanMessage, SystemMessage

from config import get_chat_model
from models.state import EmergencyPlanState
from tools.arcgis_query import query_hospitals_layer, query_schools_layer
from tools.distance import haversine_distance, haversine_km

TOOLS = [query_schools_layer, query_hospitals_layer, haversine_distance]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}
LAYER_TOOLS = {query_schools_layer.name, query_hospitals_layer.name}
MAX_STEPS = 5
RADIUS_EXPANSION = 2.0
MIN_RESULTS = 3

SYSTEM_PROMPT = """You are the Facility Finder agent of a Lebanon emergency response assistant.
Use your tools to find facilities near the user's location:
- intent find_shelter -> query_schools_layer; find_hospital -> query_hospitals_layer;
  full_emergency_plan -> both (call them in parallel).
- If a query returns count 0, call that same tool once more with the radius doubled. Never more than once.
- When you have both a nearest shelter and a nearest hospital, call haversine_distance between them.
- When done, reply with one short sentence and no tool calls."""


def _needed_layers(intent: str) -> dict:
    return {
        query_schools_layer.name: intent in ("find_shelter", "full_emergency_plan"),
        query_hospitals_layer.name: intent in ("find_hospital", "full_emergency_plan"),
    }


def facility_finder_node(state: EmergencyPlanState) -> dict:
    loc = state.resolved_location
    base_radius = state.search_radius_km
    needed = _needed_layers(state.intent)
    results = {name: None for name in LAYER_TOOLS}   # latest artifact per layer tool
    trace = list(state.tool_trace)

    def run(call):
        if call["name"] in LAYER_TOOLS:
            # Ground the coordinates (the model must not query a mistyped point) and keep
            # enough results to offer alternatives.
            max_results = max(int(call["args"].get("max_results", MIN_RESULTS)), MIN_RESULTS)
            call = {**call, "args": {**call["args"], "lat": loc.lat, "lon": loc.lon, "max_results": max_results}}
        msg = TOOLS_BY_NAME[call["name"]].invoke(call)
        if call["name"] in LAYER_TOOLS:
            results[call["name"]] = msg.artifact
            summary = f"{len(msg.artifact['items'])} found within {msg.artifact['radius_km']} km"
            if msg.artifact["error"]:
                summary += f" (error: {msg.artifact['error']})"
        else:
            summary = f"{msg.content} km"
        trace.append(f"{call['name']}({', '.join(f'{k}={v}' for k, v in call['args'].items())}) -> {summary}")
        return msg

    llm = get_chat_model().bind_tools(TOOLS)
    messages = [
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage(f"Location: {loc.name} (lat {loc.lat:.5f}, lon {loc.lon:.5f})\n"
                     f"Intent: {state.intent}\nSearch radius: {base_radius} km"),
    ]
    for _ in range(MAX_STEPS):
        ai = llm.invoke(messages)
        messages.append(ai)
        if not ai.tool_calls:
            break
        messages.extend(run(call) for call in ai.tool_calls)

    # Verify the model's work: every needed layer searched, empty results retried once wider.
    for name, is_needed in needed.items():
        if not is_needed:
            continue
        if results[name] is None:
            trace.append(f"[check] model skipped {name}; running it")
            run({"name": name, "args": {"radius_km": base_radius}, "id": f"check-{name}", "type": "tool_call"})
        if not results[name]["items"] and results[name]["radius_km"] < base_radius * RADIUS_EXPANSION:
            trace.append(f"[check] {name} empty at {results[name]['radius_km']} km; retrying wider")
            run({"name": name, "args": {"radius_km": base_radius * RADIUS_EXPANSION},
                 "id": f"retry-{name}", "type": "tool_call"})

    shelters = results[query_schools_layer.name]["items"] if needed[query_schools_layer.name] else []
    hospitals = results[query_hospitals_layer.name]["items"] if needed[query_hospitals_layer.name] else []
    used_radii = [r["radius_km"] for n, r in results.items() if needed[n] and r]
    between = None
    if shelters and hospitals:
        between = round(haversine_km(shelters[0].lat, shelters[0].lon, hospitals[0].lat, hospitals[0].lon), 2)

    return {
        "node_trace": state.node_trace + ["facility_finder"],
        "tool_trace": trace,
        "nearest_shelters": shelters,
        "nearest_hospitals": hospitals,
        "effective_radius_km": max(used_radii) if used_radii else base_radius,
        "radius_expanded": any(r > base_radius for r in used_radii),
        "shelter_to_hospital_km": between,
        "error": next((r["error"] for n, r in results.items() if needed[n] and r and r["error"]), None),
    }
