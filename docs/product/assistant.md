# Conversational scenario building (the built-in assistant)

> Product contract for the optional agent-native mode. It extends
> [workbench-logic.md](workbench-logic.md) and never overrides it: plans are
> drafts until the user confirms; confirmation writes a draft and saves a
> version; nothing in the conversation starts a run.

## 1. What the user experiences

The user describes the situation they want to study; the assistant turns it
into a concrete scene they can see, adjust and run, without learning the
platform's vocabulary.

| State | What is on screen | Can change the world? |
|---|---|---|
| Conversation | Chat with the assistant; the current scene (if any) stays visible | No |
| **Proposed plan** vN | Plan board (cards, interaction diagram, assumptions, open questions, versions) and a 3D preview with proposed elements drawn as ghosts. Badge: "Proposed plan · not applied" | No |
| **Configured scene** · version k | The real draft and saved version; apply report lists what was created/changed; 3D shows the real draft. Badge: "Configured · v k · not running" | Only Confirm wrote it |
| Running / recorded run | Normal workbench; Story view compares the plan's expectations with recorded events | Only explicit Start/operations |

Journey: open conversation (from the gallery "Describe a scenario", from a
project's **Assistant** button, or the Form ↔ Conversation switch in Configure;
the switch keeps the same project and draft) → connect a model once → describe
an idea → the assistant proposes a concrete starting plan with sensible
defaults, lists its assumptions, and asks only questions whose answers change
what happens → revise in natural language ("make the incident later", "use two
logistics UAVs", "let the courier decide", "go back to the previous version of
that arrangement", "show me two options", "keep everything else the same") →
**Confirm** once → apply report + progressive 3D reveal → inspect, ask, revise
again → Initialize/Start as usual → Story: expected vs actual, with
explanations for anything that did not happen.

Principles the assistant follows (system prompt, tool design and UI all enforce them):

1. Grounding: every element of a plan is an operation from the capability
   catalogue (§3), compiled and validated against the real scenario. The
   assistant never claims a capability that has no operation, and says so
   plainly when asked for one ("the platform cannot model X yet; the closest is Y").
2. Setup vs behaviour: background conditions (orders, places, who is nearby,
   a person falling, wind) are *setup*; noticing, deciding, asking, accepting
   and responding are *behaviour*. Every behaviour is either **prescribed**
   (a deterministic rule) or **decided by the agent** (the connected model
   chooses among allowed options under constraints the engine enforces). The
   plan shows which, and the user can switch it.
3. Few questions: start concrete, mark assumptions, ask only material
   questions (at most two per turn), accept "use sensible defaults".
4. Minimal change: revisions change only the operations they concern; the
   plan diff proves it.
5. One approval: revisions are free; Confirm applies the latest validated
   plan version. The model has no tool that applies, initializes or starts.
6. Honest outcomes: the Story view reads recorded events; a missing expected
   step is reported with the recorded reason, never replayed as success.

## 2. Simulation capability: cooperative incident response with handoff

Owned by the low-altitude pack (`packs/lowalt`). Declared compactly inside the
task engine configuration and expanded at load time (like `generators.fleet`
and LLM allocation), so YAML stays readable and the form editor shows it under
*Tasks & behaviours*.

```yaml
engines:
  tasks:                     # the lowalt_tasks engine (id may differ)
    config:
      vehicle_task_kinds:    # optional per-vehicle override of vehicle_kinds
        courier-1: [food, parcel]
      cooperation:
        handoff_points:      # expanded to lowalt:HandoffPoint sites
          - {id: handoff-east, position: [x, y, z], label: East Roof locker, label_zh: 东侧屋顶交接柜}
        incidents:           # expanded to lowalt:Incident entities (+ one lowalt:Handoff record each)
          - {id: incident-1, kind: person_fall, position: [x, y, 0.0], at_s: 90.0,
             label: Person fell on the street, label_zh: 行人在街道跌倒}
        watchers:            # who can notice incidents
          vehicles: [logistics-1, logistics-2]
          notice_radius_m: 150.0
        responder:           # decision of the vehicle that noticed
          mode: prescribed   # prescribed | agent
          goal: "Help the person without abandoning the parcel."   # agent mode
          order: [respond_now, request_handoff, finish_then_respond, report_only]
          rules:
            protected_task_kinds: [medical]   # never interrupted or handed over
            require_replacement: true         # leave a carried task only after a helper accepted
            min_battery_pct: 30.0             # below: cannot respond
            max_response_distance_m: 600.0
        helpers:             # who can be asked to take over the task
          vehicles: [courier-1, courier-2]
          ask_radius_m: 500.0
          mode: prescribed   # prescribed | agent
          goal: "Keep your own orders on time; help when you reasonably can."
          rules:
            accept_when: idle   # idle | always   (prescribed mode)
            min_battery_pct: 25.0
          selection: nearest    # nearest | first   (when several accept)
        photograph: {dwell_s: 5.0, altitude_m: 35.0}
        provider: {profile: assistant}   # required when any mode is agent; operator-resolved
```

Semantics (all deterministic and journaled; agent replies are recorded and
replay never calls the model):

* At `at_s` an incident becomes `occurred` (event `lowalt.incident.occurred`).
  Each task tick, a watcher whose horizontal distance to an occurred,
  unattended incident is ≤ `notice_radius_m` notices it
  (`lowalt.incident.noticed`). The first watcher to notice an incident without a
  responder makes the responder decision.
* Allowed responder options are computed from the watcher's actual state:
  `respond_now` (idle; or carrying a non-protected task when
  `require_replacement` is false — the task is suspended and resumed after the
  response, using the existing preemption mechanics), `request_handoff`
  (carrying an assigned/started/picked-up, non-protected task),
  `finish_then_respond` (has a task), `report_only` (always). Battery below
  `min_battery_pct` or distance beyond `max_response_distance_m` removes the
  responding options; a protected task removes `respond_now` and
  `request_handoff`. Disallowed options are recorded with their reason.
* Prescribed mode takes the first allowed option in `order`. Agent mode asks
  the connected model (LangGraph engine, `lowalt.coop.decision_request` →
  `lowalt.coop.decision_result`) with the goal, the vehicle's state, the
  allowed options and the plain-language constraints; the engine re-checks the
  reply against current allowed options. A valid choice records
  `lowalt.incident.decision`
  `{incident, vehicle, mode, choice, reason, allowed, blocked}`. An invalid
  reply or model failure records `lowalt.incident.decision_failed`
  `{incident, vehicle, role, mode, reason}`; a responder failure clears the
  choice and sets the incident state to `decision_failed`. It never chooses a
  prescribed action on behalf of a failed agent. A helper failure is retained
  as a failed offer, not an acceptance or ordinary refusal.
  无效回复或模型失败会记录决策失败；响应者事件状态为 `decision_failed`，
  不会自动采用预设动作。协助者失败保留为失败的 offer，不冒充接受或普通拒绝。
* `request_handoff`: candidates are helper vehicles within `ask_radius_m` of
  the requester, with payload capacity and battery ≥ the helpers'
  `min_battery_pct`. All candidates are asked at once
  (`lowalt.handoff.requested`); each replies (`lowalt.handoff.offer`
  `{helper, accepted, reason, mode}`; prescribed: `idle` accepts only without a
  task and not charging, `always` accepts). With acceptances, `selection`
  picks one (`lowalt.handoff.accepted {helper, point, others}`), after checking
  eligibility again. Without an eligible acceptance, or if requester/point
  checks fail, handoff state becomes `failed` when any helper decision failed,
  otherwise `declined`; the corresponding `lowalt.handoff.failed` or
  `lowalt.handoff.declined` event records the reason. A valid eligible
  acceptance can still be selected if another helper failed. After an
  unsuccessful handoff, prescribed responder mode takes the next allowed
  option after `request_handoff`; agent mode emits a new
  `lowalt.coop.decision_request` and queries the model against current state.
  The failed/declined handoff now blocks another `request_handoff`. This is a
  new decision, not an automatic model retry or a conversion of failure into
  success; the original delivery is not silently discarded.
  无合格接受者或状态复核失败时，有协助者决策失败则交接为 `failed`，否则为
  `declined`；另一个协助者的有效接受仍可能被选中。失败交接之后，预设模式采用
  后续允许选项，智能体模式重新发出决策请求并真实询问模型；再次交接已被阻止。
* Accepted handoff of a picked-up task: the requester flies to the nearest
  handoff point and leaves the parcel (`lowalt.handoff.dropped`; task state
  `awaiting_transfer`); the helper is assigned the task with the handoff point
  as its pickup, flies there, waits until the parcel is there, picks it up
  (`lowalt.handoff.transferred`) and delivers to the original destination via
  the normal receipt-driven flow. A task not yet picked up is reassigned
  directly (transferred with `point: null`). A busy helper that accepted takes
  the task after its current one. Task kind eligibility does not block a
  helper that is explicitly listed; payload capacity does.
* Responding: the responder flies above the incident (`altitude_m`),
  `lowalt.incident.responding`, hovers `dwell_s`, then
  `lowalt.incident.photographed {incident, vehicle, position}`; when the
  scenario has a camera capture engine a real capture is requested from the
  UAV pose so the photo appears under Photos. `report_only` emits
  `lowalt.incident.reported`. `finish_then_respond` responds after the
  current task completes, if the incident is still unattended.
* Entities and contracts. `lowalt:Incident` (parent AeroGraph `oo:Incident`):
  `lowalt.incident_kind`, `lowalt.incident_position` (ENU m),
  `lowalt.incident_state` (`pending|occurred|noticed|responding|photographed|reported|decision_failed`),
  `lowalt.incident_time_ns`, `lowalt.incident_noticed_by`,
  `lowalt.incident_responder`. `lowalt:Handoff` record (parent AeroGraph
  `oo:CargoTransferReport`, nonspatial): `lowalt.handoff_state`
  (`idle|requested|accepted|declined|failed|dropped|transferred`),
  `lowalt.handoff_task`, `lowalt.handoff_from`, `lowalt.handoff_to`,
  `lowalt.handoff_point`. `lowalt:HandoffPoint` (parent `lowalt:Site`). The
  AeroGraph ancestors are frozen in `packs/lowalt/cooperation.snapshot.json`
  (compiled from AeroGraph, with source provenance) and merged into the
  scene's registry snapshot by the plan compiler.
* All new events declare narratives (EN/ZH) and entity subjects, so they appear
  in the event list, timeline, Why? trace, relation graph and inspector. The
  incident and handoff points have presentation markers in 3D.

## 3. Plans and the capability catalogue (`aeroagentsim.assistant`)

A **plan version** is `{base, operations[], assumptions[], open_questions[],
title, summary, variant?}`. `base` is `{"workspace": id}` (the scene the user
is working on) or `{"template": id, "controls": {...}}` (a starting point).
Operations apply in order to a copy of the base scenario; each has a stable
`id` (`op-1`…) so a later version can restore one arrangement without touching
others.

| Operation | Purpose (plain language) |
|---|---|
| `set_control {control, value}` | Any declared template control (fleet size, order rate, allocator, wind, reserve…) |
| `set_duration {seconds}`; `set_run_mode {mode}` | How long; scheduled vs interactive |
| `set_wind {speed_mps, from_deg?}`; `add_gust {start_s, end_s, speed_mps}`; `set_weather {precipitation_mm_h?, visibility_m?, light?}` | Weather; `from_deg` is the direction the wind comes FROM (clockwise from north); wind and gusts are simulated inputs that drive flight holds and energy; in `set_weather`, rain, visibility and light are visual only (3D view and UAV captures; flight, sensing and task outcomes don't use them) |
| `set_rule {rule, value}` | battery_reserve_pct, safety_distance_m, wind_limit_mps, medical_preemption |
| `set_algorithm {slot, value}` | From the installed algorithm catalogue |
| `add_place {id, role, at, label?, …}` | kitchen, customer, vertiport, charging_pad, handoff_point, incident_site…; `at` is a place-catalogue key |
| `update_place` / `remove_place` | Change or drop an unused place |
| `add_fleet {id, role, count, start, battery_pct?, payload_capacity_kg?, task_kinds?}` | A named group of UAVs (meal courier, parcel drone, medical drone, inspection drone, patrol drone, air taxi) |
| `set_fleet {fleet, count?, battery_pct?, start?}`; `set_vehicle {vehicle, battery_pct?, start?}` | Resize or adjust; one vehicle with low battery |
| `add_orders {id, kind, count, from[], to[], release, priority?, payload_kg?, delivery_window_s?}` | Parcels, meals, medical, inspections, … with scheduled times or a rate |
| `set_orders` / `set_task {task, destination?, release_s?, priority?}` / `remove_orders` | Revise workload; change one delivery |
| `add_incident {id, kind, at, time_s, label?, radius_m?, end_s?}`; `set_incident`; `remove_incident` | Point incident: person_fall, or fire ("Building fire"; fire smoke and flames are visual and scheduling markers — combustion and smoke spread are not simulated) |
| `schedule_event {event, at_s, params}` | Existing operator events at a time: close_vertiport, add_urgent_order, restaurant_delay, change_dropoff, prioritize_medical, investigate |
| `set_cooperation {watchers?, responder?, helpers?, handoff_points?, photograph?}` (merge) / `remove_cooperation` | Who notices, who decides how, constraints, who may help |

Compilation produces a self-contained plan directory (base files + compiled
scenario + merged registry snapshot) and runs the real loader; full
validation (the same `Simulation` construction as the workspace validator)
runs in the background, and Confirm is enabled only for a validated version.
Errors name the operation id and field, so the assistant can fix them.

Derived from the compiled scenario (never from model prose):

* **Scene summary**: environment, fleets (role, count, start, battery,
  contract type with its AeroGraph ancestry), places, workloads, background
  events, rules, algorithms, cooperation.
* **Diff** against the base and against the previous version (added /
  changed / removed elements, with entity ids for 3D highlighting).
* **Interactions**: lanes (participants and places) and ordered steps, each
  tagged `setup` / `behaviour`, `prescribed` / `agent`, with accept/decline
  branches — rendered as the interaction diagram and animated sequence.
* **Expectations**: what to watch, each with an event pattern
  (`schema` + payload match) and a geometric pre-check where possible (e.g.
  "the parcel route passes 80 m from the incident; notice radius 150 m").
* **Place catalogue**: named Huangpu rooftops and street points used by the
  shipped templates, with positions and distances.

**Story** for a run of an applied plan: expected steps marked happened (with
time and seek offset) / not happened / pending; a missing step is explained
from recorded events (declined offer and its reason, decision options blocked
by battery or protected task, recorded decision failure, no watcher within
radius with the closest recorded approach, run ended first). Failure narratives
are declared in both languages (`lowalt.incident.decision_failed` and
`lowalt.handoff.failed`); Story reads those records and never invents successful
handoff or response events. 决策失败与交接失败都有中英文事件叙述；Story 根据记录
解释未发生的步骤，不生成成功结果。

## 4. The harness

`assistant/harness.py` runs a bounded tool-calling loop over the existing
OpenAI-compatible provider (`agents/provider.py`): per user turn at most 10
model calls, per-call wall timeout, token budget, history compaction (recent
turns plus a state message with the scene summary and current plan). Tools:

| Tool | Returns |
|---|---|
| `get_current_scene` | Scene summary of the bound project's draft |
| `list_starting_points` / `describe_starting_point` | Templates in plain language with participants and controls |
| `list_capabilities {topic?}` | Operation catalogue with parameters, ranges, defaults, algorithms |
| `find_places {query?, near?, role?}` | Place catalogue entries |
| `lookup_contract {term}` | AeroGraph types/fields/relations matching a term, explained |
| `draft_plan {…}` / `revise_plan {…}` / `restore_operations {from_version, op_ids}` | New plan version with compile status, summary, diff |
| `propose_alternatives {variants[]}` | Two or three labelled plan variants |
| `explain_participant {id}` / `explain_relation {a, b}` | Responsibilities, tasks, rules, links |
| `list_runs` / `run_story {run_id}` | Runs of the project; expectations vs recorded events |

There is deliberately no tool to apply, initialize, start or operate.

Sessions persist under `<runs root>/assistant/` with the transcript, UI
events (sequence-numbered), plan versions and apply records. The model
connection (`base_url`, `model`, optional key) is set from the UI or by the
operator (`AEROAGENTSIM_ASSISTANT_BASE_URL`, `AEROAGENTSIM_ASSISTANT_MODEL`,
`AEROAGENTSIM_ASSISTANT_API_KEY_ENV`); the key is never returned to the
browser. The same connection is registered as the operator provider profile
`assistant`, so "let the agent decide" behaviours use it inside runs.

HTTP (`/v1/assistant`, GET/POST only): `connection` (get/set/test), `sessions`
(create/list/get), `sessions/{id}/messages` (send; the turn runs in the
background), `sessions/{id}/events?after=` (long-poll), `sessions/{id}/cancel`,
`sessions/{id}/plans/{v}` and `/scenario` (compiled preview),
`sessions/{id}/plans/{v}/choose` (pick a variant), `sessions/{id}/apply`
(the Confirm action), `sessions/{id}/story/{run_id}`.

## 5. Evaluation

* Deterministic: unit tests for the engine, compiler and harness with a
  scripted model; a Playwright journey against a fake OpenAI-compatible model.
* Live: `tools/assistant/eval.py` drives scripted user conversations against a
  real model (development default: local `glm-5.3-flashx`) and records turn
  latency, tool errors, compile failures, questions asked, plan validity and
  the flagship run's story.
* User test: an independent agent uses the product as a person would and
  reports confusing or broken behaviour.
