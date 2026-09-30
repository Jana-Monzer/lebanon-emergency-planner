"""CLI for the Lebanon Emergency Response Planner.

    python main.py                 interactive chat (multi-turn, checkpointed per thread)
    python main.py --demo          scripted scenarios: clear match, ambiguous + clarification,
                                   no facilities nearby, general question
    python main.py --diagram       print the graph as Mermaid
"""

import argparse
import json
import sys
import textwrap
import uuid

from pydantic import BaseModel

from graph import build_graph
from models.schemas import EmergencyPlan
from models.state import DEFAULT_RADIUS_KM, EmergencyPlanState

DEMO_SCENARIOS = [
    ("Clear single match", ["There is shelling near Achrafieh, I need a full emergency plan"]),
    ("Ambiguous location -> clarification -> resumed from checkpoint",
     ["Where is the nearest shelter to Mejdlaya?", "the one in Zgharta"]),
    ("Ambiguous geocoder result, answered by number", ["I need a hospital near Hamra", "2"]),
    ("Nothing within the radius (expanded once, still empty)", ["Full emergency plan for Hermel please"]),
    ("Typo handled by embedding similarity", ["nearest hospital to Beirutt"]),
    ("General question (router skips the location pipeline)", ["What should I pack in an emergency go-bag?"]),
]

RULE = "─" * 78


# --------------------------------------------------------------------------- printing


def _short(value, width: int = 110) -> str:
    if isinstance(value, EmergencyPlan):
        return f"EmergencyPlan(summary={value.summary[:60]!r}…)"
    if isinstance(value, BaseModel):
        data = value.model_dump()
        keep = {k: data[k] for k in ("name", "distance_km", "lat", "lon", "source", "confidence") if k in data}
        text = f"{type(value).__name__}({', '.join(f'{k}={v!r}' for k, v in keep.items())})"
    elif isinstance(value, list) and value and isinstance(value[0], BaseModel):
        names = ", ".join(f"{getattr(v, 'name', v)}" + (f" ({v.distance_km} km)" if hasattr(v, "distance_km") else "")
                          for v in value[:3])
        text = f"[{len(value)}] {names}" + (" …" if len(value) > 3 else "")
    else:
        text = repr(value)
    return text if len(text) <= width else text[: width - 1] + "…"


def print_update(node: str, update: dict) -> None:
    print(f"\n  ▸ {node}")
    for key, value in (update or {}).items():
        if key == "history":
            print(f"      {key:22} = <{len(value)} messages>")
        elif key == "tool_trace":
            print(f"      {key:22} =")
            for line in value:
                print(f"          · {textwrap.shorten(line, 150)}")
        else:
            print(f"      {key:22} = {_short(value)}")


def _yn(flag) -> str:
    return {True: "yes", False: "no", None: "n/a"}[flag]


def print_plan(state: EmergencyPlanState) -> None:
    plan, loc = state.plan, state.resolved_location
    title = f"EMERGENCY PLAN — {loc.name}" if loc else "EMERGENCY GUIDANCE"
    print(f"\n{RULE}\n  {title}\n{RULE}")
    print(textwrap.fill(plan.summary, 76, initial_indent="  ", subsequent_indent="  "))

    if s := plan.nearest_shelter:
        print(f"\n  🏫 Nearest shelter: {s.name}  —  {s.distance_km} km")
        details = [s.cadastral and f"{s.cadastral}, {s.caza}", s.num_classes and f"{s.num_classes} class sections",
                   s.phone and f"tel {s.phone}", s.status and f"status: {s.status}"]
        print(f"     {' | '.join(d for d in details if d)}")
        print(f"     map: https://www.google.com/maps?q={s.lat:.6f},{s.lon:.6f}")
    if h := plan.nearest_hospital:
        print(f"\n  🏥 Nearest hospital: {h.name}  —  {h.distance_km} km")
        details = [h.ownership, h.district, f"blood bank: {_yn(h.has_blood_bank)}",
                   f"radiology: {_yn(h.has_radiology)}", h.phone and f"tel {h.phone}"]
        print(f"     {' | '.join(d for d in details if d)}")
        print(f"     map: https://www.google.com/maps?q={h.lat:.6f},{h.lon:.6f}")
    others = [f"{x.name} ({x.distance_km} km)" for x in state.nearest_shelters[1:3] + state.nearest_hospitals[1:3]]
    if others:
        print(f"\n  Alternatives: {'; '.join(others)}")

    print("\n  Instructions:")
    for i, step in enumerate(plan.instructions, 1):
        print(textwrap.fill(step, 76, initial_indent=f"   {i}. ", subsequent_indent="      "))
    if plan.warnings:
        print("\n  ⚠ Warnings:")
        for w in plan.warnings:
            print(textwrap.fill(w, 76, initial_indent="   - ", subsequent_indent="     "))
    print(f"\n  ☎ {' | '.join(plan.emergency_contacts)}\n{RULE}")


# --------------------------------------------------------------------------- running


def run_turn(graph, thread_id: str, text: str, radius_km: float, verbose: bool = True) -> EmergencyPlanState:
    config = {"configurable": {"thread_id": thread_id}}
    for chunk in graph.stream({"user_query": text, "search_radius_km": radius_km}, config, stream_mode="updates"):
        if verbose:
            for node, update in chunk.items():
                print_update(node, update)

    state = EmergencyPlanState(**graph.get_state(config).values)
    if state.plan and state.node_trace[-1] == "response_formatter":
        print_plan(state)
    else:
        print(f"\n🤖 {state.assistant_message}")
    return state


def run_demo(graph, radius_km: float, verbose: bool) -> None:
    for title, turns in DEMO_SCENARIOS:
        thread_id = f"demo-{uuid.uuid4().hex[:8]}"
        print(f"\n\n{'═' * 78}\n  SCENARIO: {title}   (thread {thread_id})\n{'═' * 78}")
        for text in turns:
            print(f"\n👤 {text}")
            state = run_turn(graph, thread_id, text, radius_km, verbose)
            if state.awaiting_clarification:
                checkpoints = len(list(graph.get_state_history({"configurable": {"thread_id": thread_id}})))
                print(f"\n  [checkpoint] thread {thread_id} saved ({checkpoints} checkpoints): "
                      f"awaiting_clarification=True, intent={state.intent!r}, candidates={state.candidates}")


def run_interactive(graph, radius_km: float, verbose: bool) -> None:
    thread_id = f"cli-{uuid.uuid4().hex[:8]}"
    print("Lebanon Emergency Response Planner — describe your situation and location.")
    print("Commands: /new (new conversation), /state (dump checkpointed state), /quit\n")
    while True:
        try:
            text = input("👤 ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        if text in ("/quit", "/exit"):
            return
        if text == "/new":
            thread_id = f"cli-{uuid.uuid4().hex[:8]}"
            print(f"Started new conversation ({thread_id}).")
            continue
        if text == "/state":
            values = graph.get_state({"configurable": {"thread_id": thread_id}}).values
            print(json.dumps(EmergencyPlanState(**values).model_dump(), indent=1, ensure_ascii=False) if values else "{}")
            continue
        try:
            run_turn(graph, thread_id, text, radius_km, verbose)
        except Exception as exc:  # keep the chat alive on network/API errors
            print(f"\n⚠ Error: {exc}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--demo", action="store_true", help="run the scripted demo scenarios")
    parser.add_argument("--diagram", action="store_true", help="print the graph as Mermaid and exit")
    parser.add_argument("--radius", type=float, default=DEFAULT_RADIUS_KM, help="search radius in km (default 5)")
    parser.add_argument("--quiet", action="store_true", help="hide per-node state updates")
    args = parser.parse_args()

    graph = build_graph()
    if args.diagram:
        print(graph.get_graph().draw_mermaid())
    elif args.demo:
        run_demo(graph, args.radius, not args.quiet)
    else:
        run_interactive(graph, args.radius, not args.quiet)


if __name__ == "__main__":
    main()
