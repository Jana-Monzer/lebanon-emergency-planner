# Lebanon Emergency Response Planner

COE749 (Advanced LLM) course project. It is a LangGraph multi-agent system. Given a place in Lebanon, it finds the
nearest school designated as a shelter and the nearest hospital, then writes a structured emergency plan.
Facility data comes live from two public ArcGIS feature layers.

## Setup

Requires Python 3.10+.

```bash
cd emergency-planner
python -m venv .venv
.venv\Scripts\activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env            # macOS/Linux: cp .env.example .env
```

Edit `.env` and paste your key into `OPENAI_API_KEY`.

- **Google Gemini (default in `.env.example`)**: get a key at <https://aistudio.google.com/apikey>. The defaults
  are `gemini-3.5-flash-lite` for chat and `gemini-embedding-2` for embeddings. Embeddings go through Gemini's
  OpenAI-compatible endpoint (`OPENAI_BASE_URL`). Chat uses LangChain's native Gemini client
  (`langchain-google-genai`), because Gemini 3 models reject multi-step tool loops unless each tool call's "thought
  signature" is sent back, and `ChatOpenAI` drops it. The switch is automatic when `OPENAI_BASE_URL` is Google's.
- **OpenAI**: use "Option B" in `.env.example` and clear `OPENAI_BASE_URL`. Everything then goes through
  `ChatOpenAI` and the OpenAI SDK. The embedding thresholds in `tools/geocode.py` were calibrated on
  `gemini-embedding-2`, so re-check them if you switch.

Check the setup. This runs one call each for the layers, chat, structured output, tool calling and embeddings:

```bash
python check_setup.py
```

If the chat check fails with "model not found", the checker lists the models your key can use. Put one of them in
`OPENAI_MODEL`. If only the structured-output check fails, set `STRUCTURED_OUTPUT_METHOD=function_calling`.

**Gemini free-tier limits** (observed with a free key, September 2026):

| Model | Limit |
|---|---|
| `gemini-3.8-flash` | 5 requests/min, **20/day** (too few for the demo) |
| `gemini-3.5-flash-lite` | used instead |
| `gemini-embedding-*` | 100 texts/min, 1,000 texts/day |

`LLM_REQUESTS_PER_MINUTE=5` spaces the chat calls out so they don't fail. The demo makes about 30 LLM calls, so it
takes around 7 minutes. Set it to `0` once billing is enabled on the key.

Building the gazetteer embeddings is a one-time cost of about 800 texts, roughly 9 minutes on the free tier. It is
saved after every batch, so an interrupted build resumes where it stopped.

## Running

```bash
python main.py --demo      # scripted scenarios (see below)
python main.py             # interactive chat; /new starts a new thread, /state dumps the checkpoint
python main.py --radius 3  # change the base search radius (km)
python main.py --quiet     # hide the per-node state updates
python main.py --diagram   # print the compiled graph as Mermaid
```

**Web dashboard.** The Angular dashboard in the repository root (`src/`) is the graphical interface. It talks to
`server.py`, a FastAPI wrapper around the same compiled graph:

```bash
.venv\Scripts\python -m uvicorn server:app --port 8000   # then, in the repo root: npm start -> http://localhost:4200
```

`POST /api/chat {message, thread_id}` streams newline-delimited JSON: one event per graph node (its partial state
update), then the final result (plan, clarification candidates or error). Each chat session in the dashboard uses
its own `thread_id`, so answering a clarification resumes from the MemorySaver checkpoint, just as in the CLI. The
dashboard shows the agents' progress live, draws the plan on the 3D map, and has an "Agent trace" view of every
node's state update.

After every node, the CLI prints the fields that node wrote to the state. This shows the state filling in step by
step.

The first run downloads a gazetteer of place names from the schools layer to `data/`. The first typo lookup embeds
that gazetteer once. Both are cached.

## Data sources (verified against the live layers)

| Layer | REST endpoint | Fields used |
|---|---|---|
| Schools | `services3.arcgis.com/tuNLpt6Wfhd22qmO/.../SchoolForEmergencyPlan_Map/FeatureServer/0` (1,480 schools) | Shelter flag **`مركز_ايواء`** (Arabic for "shelter centre"): `'Yes'` for 600 schools, `''` otherwise. Also `School_Name`, `School_Name___Arabic`, `Cadastral`, `Caza`, `Governorate`, `Total_Nb_Class` (class sections, the only capacity proxy), `رقم_الهاتف` (phone), `وضع_المدرسة` (status: `مقفلة` = closed; `موجودة مرتين` = duplicate record, excluded) |
| Hospitals | `services7.arcgis.com/WOCTeelWa8YfhXRF/.../Hospital_Labs/FeatureServer/0` (220 facilities) | The layer mixes hospitals and labs. Only rows with **`Facility_T = 'Hospitals , مستشفيات'`** are used (145). Also `Facility_E`/`Facility_A` (name), `ownership`, `Blood_bank`, `Radiology`, `Lab`, `Phone`, `district`. The layer has **no emergency-room field**. |

Both layers are queried through the standard `/query` endpoint with a point geometry, `distance` and
`units=esriSRUnit_Kilometer`. Distances are then recomputed with the haversine formula and sorted.

## Architecture

```
START ──► router ──(general_question)───────────────────────────────► response_formatter ──► END
             │                                                               ▲
             └─(find_shelter | find_hospital | full_emergency_plan)          │
                        ▼                                                    │
                 location_resolver ──(resolved_location set)──► facility_finder
                        │
                        ├──(is_ambiguous == True)──► clarification ──► END   (the next user turn resumes here)
                        └──(nothing resolved)──────► resolution_error ──► END
```

Both branch points are `add_conditional_edges` (see [graph.py](graph.py)): `route_after_router` and
`route_after_resolver`. The second one reads `state.is_ambiguous` / `state.resolved_location`.

| Agent | File | How it works | Reads → Writes |
|---|---|---|---|
| Router | [agents/router.py](agents/router.py) | No tools. SOM with `RouterDecision(intent, is_clarification_reply: bool, rationale)` | `user_query`, `awaiting_clarification`, `candidates` → `intent`, `router_rationale`, `turn`, `history` |
| Location Resolver | [agents/location_resolver.py](agents/location_resolver.py) | Tool calling with **{geocode_location}** | `user_query`, `candidates` → `resolved_location`, `is_ambiguous`, `candidates`, `candidate_locations`, `error` |
| Facility Finder | [agents/facility_finder.py](agents/facility_finder.py) | Tool-calling loop with **{query_schools_layer, query_hospitals_layer, haversine_distance}** | `resolved_location`, `intent`, `search_radius_km` → `nearest_shelters`, `nearest_hospitals`, `effective_radius_km`, `radius_expanded`, `shelter_to_hospital_km` |
| Response Formatter | [agents/response_formatter.py](agents/response_formatter.py) | SOM with `EmergencyPlan` | everything above → `plan`, `assistant_message` |

**Location resolution** ([tools/geocode.py](tools/geocode.py)) tries four tiers in order:

1. **Exact** match on normalized names (accents, punctuation and `el-`/`al-` removed). The names come from a
   gazetteer of the 783 cadastral areas and 26 cazas in the schools layer.
2. **Substring** match (whole words).
3. **Nominatim** (OpenStreetMap), limited to Lebanon. Results within 5 km of each other count as the same place.
4. **Embedding** cosine similarity against the gazetteer, which catches typos. Calibrated on
   `gemini-embedding-2`: typos score 0.74–0.97 against the right name (`Hermell` 0.74, `Beirutt` 0.79), while junk
   scores lower (`Paris` 0.64, `xyzqq` 0.46). A match is accepted at ≥ 0.72 with a margin of ≥ 0.05. Candidates are
   offered at ≥ 0.68.

The resolution is *ambiguous* when a tier returns more than one distinct place. That happens for real names in this
data: `Mejdlaya` (Aley / Zgharta), `Kfar Hatta` (Koura / Saida), `Niha` (Zahle / Chouf). Nominatim also returns two
places called `Hamra` (Beirut / Nabatieh).

**Grounding safeguards.** The Facility Finder always queries at the resolved coordinates, whatever the LLM passes. If
the model skips a required layer or does not retry an empty result, the node runs that call itself. The Formatter's
`nearest_shelter`/`nearest_hospital` fields are overwritten with the actual layer records, so the plan cannot
contain invented facilities. The LLM nodes also have a LangGraph `RetryPolicy` that re-runs the node after a
transient network error.

**State and checkpointing** ([models/state.py](models/state.py)): `EmergencyPlanState` is one Pydantic model. Its
fields include `int`, `float`, `bool`, `List[str]`, `List[Dict[str, str]]`, nested models (`ResolvedLocation`,
`List[SchoolShelter]`, `List[Hospital]`, `EmergencyPlan`) and `Optional` fields that start as `None`. The graph is
compiled with `MemorySaver` and run with a `thread_id`. When the clarification node asks "which Mejdlaya?", the turn
ends, and the state is saved with `awaiting_clarification=True` and the candidates. The user's next message on the
same thread continues from that checkpoint. The router recognizes the message as a clarification reply and keeps
the original intent.

## Project layout

```
agents/   router.py, location_resolver.py, facility_finder.py, response_formatter.py
tools/    geocode.py, arcgis_query.py, distance.py
models/   entities.py (nested models), state.py (graph state), schemas.py (SOM schemas)
graph.py  nodes, edges, conditional edges, MemorySaver
main.py   CLI + demo scenarios
server.py FastAPI chat API used by the web dashboard (../src)
check_setup.py
```

`models/entities.py` is a third models file. It holds the nested models used by both `state.py` and `schemas.py`,
which avoids a circular import.

## Example runs

These are real outputs of `python main.py --demo` (Gemini `gemini-3.5-flash-lite`, 5 km base radius). They are
trimmed only where the router's reset of per-request fields is repeated. Facility names, distances and phone numbers
come from the live layers; the generated wording varies from run to run.

### 1. Clear single match (full emergency plan)

```
👤 There is shelling near Achrafieh, I need a full emergency plan
  ▸ router
      intent                 = 'full_emergency_plan'
      …
  ▸ location_resolver
      location_text          = 'Achrafieh'
      node_trace             = ['router', 'location_resolver']
      tool_trace             =
          · geocode_location(place='Achrafieh') -> resolved via exact: 'Achrafieh' matched Achrafieh (Beirut)
      awaiting_clarification = False
      is_ambiguous           = False
      resolved_location      = ResolvedLocation(name='Achrafieh (Beirut)', lat=33.88617259500006, lon=35.52278152400005, source='exact', con…
      candidates             = []
      candidate_locations    = []
      error                  = None

  ▸ facility_finder
      node_trace             = ['router', 'location_resolver', 'facility_finder']
      tool_trace             =
          · geocode_location(place='Achrafieh') -> resolved via exact: 'Achrafieh' matched Achrafieh (Beirut)
          · query_schools_layer(lon=35.52278152400005, max_results=5, radius_km=5, lat=33.88617259500006) -> 5 found within 5.0 km
          · query_hospitals_layer(max_results=5, lat=33.88617259500006, lon=35.52278152400005, radius_km=5) -> 5 found within 5.0 km
          · haversine_distance(lat1=33.88715323, lon1=35.5255536600001, lat2=33.8812399998262, lon2=35.5191500001724) -> 0.884 km
      nearest_shelters       = [5] El Tabaris El Namozajia for Girls Public School (0.28 km), El Achrafieh First for Boys Secondary Public s…
      nearest_hospitals      = [5] Hotel Dieu De France (0.64 km), University Medical Center - Rizk Hospital (0.77 km), St Georges Hospital …
      effective_radius_km    = 5.0
      radius_expanded        = False
      shelter_to_hospital_km = 0.88
      error                  = None

  ▸ response_formatter
      plan                   = EmergencyPlan(summary='Shelling has been reported near Achrafieh. Please follow the'…)
      assistant_message      = 'Shelling has been reported near Achrafieh. Please follow the safety instructions below and utilize the neare…
      history                = <2 messages>
      node_trace             = ['router', 'location_resolver', 'facility_finder', 'response_formatter']

──────────────────────────────────────────────────────────────────────────────
  EMERGENCY PLAN — Achrafieh (Beirut)
──────────────────────────────────────────────────────────────────────────────
  Shelling has been reported near Achrafieh. Please follow the safety
  instructions below and utilize the nearest available shelter and hospital
  within your search radius.

  🏫 Nearest shelter: El Tabaris El Namozajia for Girls Public School  —  0.28 km
     Achrafieh, Beirut | status: closed
     map: https://www.google.com/maps?q=33.887153,35.525554

  🏥 Nearest hospital: Hotel Dieu De France  —  0.64 km
     Private | Beirut | blood bank: yes | radiology: yes | tel 01-615300
     map: https://www.google.com/maps?q=33.881240,35.519150

  Alternatives: El Achrafieh First for Boys Secondary Public school (0.55 km); El Achrafieh Second Secondary Public School (0.55 km); University Medical Center - Rizk Hospital (0.77 km); St Georges Hospital Hadath (0.89 km)

  Instructions:
   1. Take cover immediately away from windows and exterior walls due to the
      reported shelling near Achrafieh.
   2. Prepare to move to the nearest shelter, El Tabaris El Namozajia for
      Girls Public School, located 0.28 km away.
   3. If medical attention is needed, head to Hotel Dieu De France, located
      0.64 km away.
   4. Stay tuned to official updates and keep your emergency kit ready.
   5. Contact emergency services if you require immediate assistance or
      rescue.
```

### 2. Ambiguous location → conditional edge to `clarification` → next turn resumes from the checkpoint

```
👤 Where is the nearest shelter to Mejdlaya?
  ▸ router
      intent                 = 'find_shelter'
      …
  ▸ location_resolver
      location_text          = 'Mejdlaya'
      node_trace             = ['router', 'location_resolver']
      tool_trace             =
          · geocode_location(place='Mejdlaya') -> ambiguous via exact: 'Mejdlaya' matches 2 known areas
      awaiting_clarification = False
      is_ambiguous           = True
      resolved_location      = None
      candidates             = ['Mejdlaya (Aley)', 'Mejdlaya (Zgharta)']
      candidate_locations    = [2] Mejdlaya (Aley), Mejdlaya (Zgharta)
      error                  = None

  ▸ clarification
      awaiting_clarification = True
      assistant_message      = 'I found more than one place matching "Mejdlaya":\n  1. Mejdlaya (Aley)\n  2. Mejdlaya (Zgharta)\nWhich one d…
      history                = <2 messages>
      node_trace             = ['router', 'location_resolver', 'clarification']

🤖 I found more than one place matching "Mejdlaya":
  1. Mejdlaya (Aley)
  2. Mejdlaya (Zgharta)
Which one do you mean? Reply with the number or the name.

  [checkpoint] thread demo-b99bc79e saved (5 checkpoints): awaiting_clarification=True, intent='find_shelter', candidates=['Mejdlaya (Aley)', 'Mejdlaya (Zgharta)']

👤 the one in Zgharta

  ▸ router
      turn                   = 2
      history                = <3 messages>
      node_trace             = ['router']
      router_rationale       = 'The user is selecting one of the proposed locations for a shelter search.'
      is_ambiguous           = False

  ▸ location_resolver
      location_text          = 'Mejdlaya (Zgharta)'
      node_trace             = ['router', 'location_resolver']
      tool_trace             =
          · geocode_location(place='Mejdlaya (Zgharta)') -> resolved via clarification: user picked Mejdlaya (Zgharta)
      awaiting_clarification = False
      is_ambiguous           = False
      resolved_location      = ResolvedLocation(name='Mejdlaya (Zgharta)', lat=34.42550011666672, lon=35.87867663666672, source='clarificati…
      candidates             = []
      candidate_locations    = []
      error                  = None

  ▸ facility_finder
      node_trace             = ['router', 'location_resolver', 'facility_finder']
      tool_trace             =
          · geocode_location(place='Mejdlaya (Zgharta)') -> resolved via clarification: user picked Mejdlaya (Zgharta)
          · query_schools_layer(lat=34.42550011666672, max_results=5, radius_km=5, lon=35.87867663666672) -> 5 found within 5.0 km
      nearest_shelters       = [5] El Ayrouniyeh Mixed Public School (0.2 km), Al Fawar Mixed Public School (0.97 km), Mijdlaya Mixed Public…
      nearest_hospitals      = []
      effective_radius_km    = 5.0
      radius_expanded        = False
      shelter_to_hospital_km = None
      error                  = None

  ▸ response_formatter
      plan                   = EmergencyPlan(summary='The nearest emergency shelter to Mejdlaya (Zgharta) is El Ay'…)
      assistant_message      = 'The nearest emergency shelter to Mejdlaya (Zgharta) is El Ayrouniyeh Mixed Public School, located approximat…
      history                = <4 messages>
      node_trace             = ['router', 'location_resolver', 'facility_finder', 'response_formatter']

──────────────────────────────────────────────────────────────────────────────
  EMERGENCY PLAN — Mejdlaya (Zgharta)
──────────────────────────────────────────────────────────────────────────────
  The nearest emergency shelter to Mejdlaya (Zgharta) is El Ayrouniyeh Mixed
  Public School, located approximately 0.2 km away. You can contact them at
  06384029.

  🏫 Nearest shelter: El Ayrouniyeh Mixed Public School  —  0.2 km
     Mejdlaya, Zgharta | 17 class sections | tel 06384029
     map: https://www.google.com/maps?q=34.426902,35.877388

  Alternatives: Al Fawar Mixed Public School (0.97 km); Mijdlaya Mixed Public School (1.02 km)

  Instructions:
   1. Prepare your essential belongings and documents before departure.
   2. Proceed to El Ayrouniyeh Mixed Public School, located 0.2 km away from
      your location.
   3. Call the shelter at 06384029 in advance if you require specific
      assistance or confirmation of space.
   4. Follow guidance from shelter management upon arrival.

  ☎ Lebanese Red Cross: 140 | Civil Defense: 125 | Internal Security Forces: 112
──────────────────────────────────────────────────────────────────────────────
```

### 3. Nothing within the radius (retried once at 10 km, still empty)

```
👤 Full emergency plan for Hermel please
  ▸ router
      intent                 = 'full_emergency_plan'
      …
  ▸ location_resolver
      location_text          = 'Hermel'
      node_trace             = ['router', 'location_resolver']
      tool_trace             =
          · geocode_location(place='Hermel') -> resolved via exact: 'Hermel' matched Hermel (Hermel)
      awaiting_clarification = False
      is_ambiguous           = False
      resolved_location      = ResolvedLocation(name='Hermel (Hermel)', lat=34.42882616850005, lon=36.40439674600006, source='exact', confid…
      candidates             = []
      candidate_locations    = []
      error                  = None

  ▸ facility_finder
      node_trace             = ['router', 'location_resolver', 'facility_finder']
      tool_trace             =
          · geocode_location(place='Hermel') -> resolved via exact: 'Hermel' matched Hermel (Hermel)
          · query_schools_layer(lat=34.42882616850005, radius_km=5, max_results=5, lon=36.40439674600006) -> 0 found within 5.0 km
          · query_hospitals_layer(lon=36.40439674600006, lat=34.42882616850005, radius_km=5, max_results=5) -> 0 found within 5.0 km
          · query_schools_layer(lat=34.42882616850005, max_results=5, lon=36.40439674600006, radius_km=10) -> 0 found within 10.0 km
          · query_hospitals_layer(lat=34.42882616850005, max_results=5, lon=36.40439674600006, radius_km=10) -> 0 found within 10.0 km
      nearest_shelters       = []
      nearest_hospitals      = []
      effective_radius_km    = 10.0
      radius_expanded        = True
      shelter_to_hospital_km = None
      error                  = None

  ▸ response_formatter
      plan                   = EmergencyPlan(summary='No emergency shelters or hospitals were found within the 10.'…)
      assistant_message      = 'No emergency shelters or hospitals were found within the 10.0 km search radius around Hermel. Please contact…
      history                = <2 messages>
      node_trace             = ['router', 'location_resolver', 'facility_finder', 'response_formatter']

──────────────────────────────────────────────────────────────────────────────
  EMERGENCY PLAN — Hermel (Hermel)
──────────────────────────────────────────────────────────────────────────────
  No emergency shelters or hospitals were found within the 10.0 km search
  radius around Hermel. Please contact emergency services immediately for
  assistance.

  Instructions:
   1. Stay calm and remain in a safe indoor location away from windows.
   2. Prepare an emergency kit with essential supplies, water, and
      documents.
   3. Call the Lebanese Red Cross at 140 for immediate medical assistance.
   4. Call Civil Defense at 125 for rescue and emergency support.
   5. Call Internal Security Forces at 112 for security assistance.

  ⚠ Warnings:
   - No shelters found within the 10.0 km radius. Advise calling emergency
     services instead.
   - No hospitals found within the 10.0 km radius. Advise calling emergency
     services instead.

  ☎ Lebanese Red Cross: 140 | Civil Defense: 125 | Internal Security Forces: 112
──────────────────────────────────────────────────────────────────────────────
```

### Also in the demo

- **`Hamra` → `2`**: Nominatim returns two distinct Hamras (Ras Beirut and Nabatieh). The clarification lists them,
  and the reply `2` resolves from the stored candidate coordinates. The nearest hospital is Alhamid Medical
  Center–Ghandour Hospital, 4.59 km away.
- **Typo `Beirutt`**: Nominatim finds nothing, and the embedding tier resolves it to Beirut District:

  ```
            · geocode_location(place='Beirutt') -> resolved via embedding: 'Beirutt' matched Beirut District
  ```
- **General question** (*"What should I pack in an emergency go-bag?"*): `route_after_router` sends it straight to
  `response_formatter` (`node_trace = ['router', 'response_formatter']`), and the plan has no facilities.
