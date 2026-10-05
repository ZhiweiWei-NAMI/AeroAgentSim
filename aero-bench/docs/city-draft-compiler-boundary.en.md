# City draft compiler boundary

Status: the strict input, limited native-template compiler, HTTP publication
and authenticated native Control run passed. General city lowering and browser
execution integration remain open. Backend owner: Codex. Frontend owner:
Claude. Cross-session changes are recorded in
the project coordination record (session record, omitted from this export).

B1 establishes native inspection execution separately. B2 must compile the
current editable draft without substituting the reference inspection scene
for the authored city. B3 connects an accepted compilation to the existing
runner/control service and its public replay publication. B4 requires an
accepted new-city native package and implemented physical logistics.

## Input and scene identity

Use `aero-bench.city-workspace/v2`, as implemented in
`frontend/src/city-workspace-config.ts`. The environment uses
`timeOfDay: day|twilight|night`; units and wind convention are recorded in
F-001. Backend input accepts the current version only. Frontend migration is
explicit and does not authorize a backend v1 reader.

The compile request binds the unchanged draft to a backend-published native
registration by its ID and SHA-256. Its schema is
`aero-bench.city-compile-request/v1`; it wraps workspace v2 without adding an
editable draft format. Generated request/result/catalog/error schemas and
TypeScript validators come from `tools/generate_contracts.py`.

`scenePath` selects a presentation configuration. For the current default,
that configuration pins building-render, source-context, mesh-pack and
environment-source identities. A backend scene registration must verify those
bytes and bind them to a matching WorldPackage, native Provider inputs and
alignment manifest. Its frame authority supplies the origin and height datum.
Do not derive a formal origin from a filename, display name or mesh pixels.
The client must confirm the selected scene identity so a changed publication
cannot silently replace the city saved in the draft.

The current 414-building default has no such accepted native binding. Claude's
v6 road candidate passed the strict geometry audit, independently rerun in
`validation/backend-track-b-20260930/claude-v6-ground-audit.json`. The default
remains building-only; candidate geometry acceptance does not publish a native
WorldPackage or Task binding. A compile request for it returns a scene-binding blocker,
not choose the separate 18-building B1 reference world.

The implemented registration profile is explicitly `inspection.reference.v1`.
Its presentation document is the existing `aero-bench.public-scenario/v1`,
derived from and compared against the exact resolved native reference. The
catalog supplies `scene_url`, `scene_schema_version`, `scene_sha256` and
`scene_size_bytes`.
Its frame, geometry, public assets and native rendering bindings are verified;
all files are rechecked before compilation. It is not a
`aero-bench.city-scene-preview/v1` configuration and cannot be passed to
`parseCitySceneConfig`. Claude owns selecting/displaying this explicit native
profile through the existing public-scenario renderer, or supplying an accepted
native binding for the city editor's presentation. No such city registration is
created by the reference publisher.

## Lowering obligations

Every accepted field must be applied or retained with its declared authoring
meaning. Unsupported execution inputs receive a field-specific blocker. An
unsupported field cannot disappear from the resolved configuration or hash.

| Draft field | Backend obligation |
| --- | --- |
| `purpose`, `schema_version` | Require the current scenario-authoring contract; reject unknown fields and versions. |
| `name` | Retain authored identification without treating it as scene authority. |
| `scenePath` | Resolve and verify the explicit registered presentation/native scene binding. |
| `seed` | Apply it to the strict case and each required seed consumer; retain reproducibility. |
| `environment` | Preserve visual lighting/weather/reflection settings as authoring input. Physical weather needs a real declared Provider and applied native inputs. |
| `fleet` | Resolve each declared airframe to its actual native model/version and instance count; lower battery/reserve declarations without claiming measured energy. |
| `traffic` | Bind a regenerated native SUMO demand package for those counts; filtering an old recording cannot satisfy this field. |
| `facilities` | Bind canonical facility capabilities and native geometry with support/clearance evidence. Charger state alone cannot prove physical power transfer. |
| `airspace` | Lower east-up-south coordinates and seconds through verified frame authority; bind actual enforcement/event production. |
| `algorithms` | Bind implemented, versioned Agent/driver choices and apply every parameter. A UI enum or local planning result is insufficient. |
| `deployment` | Select the executor outside Domain Specs and bind the declared digest-pinned workload. Unavailable Kubernetes prerequisites block that profile. |
| `events`, `actionRules` | Validate references/times and lower to implemented runtime handlers; reject unsupported scheduled execution. |
| `stateKeyframes` | Retain labelled authored preview positions; never use them as Provider poses or task success evidence. |
| `labelRules` | Bind implemented derivation from sealed authoritative inputs; preview labels cannot become verifier conclusions. |

Workspace facilities and fleet are not the selected-scenario v3 models.
Selected logistics v2 is also a separate input with orders and performance
profiles. Reuse existing canonical lowerers where the shapes match; do not
silently reinterpret one of these drafts as another or infer missing orders.

### Implemented profile limits

The inspection profile applies `seed` to the strict case and Provider reset.
It preserves `name`, visual `environment`, and labelled `stateKeyframes` in a
hashed Task asset visible only to the Verifier. These edits change Run ID.
They do not change native physics or generate measured poses. The fixed
`fleet`, `traffic`, `algorithms`, `facilities`, `airspace`, `events`,
`actionRules` and `labelRules` must match the verified template exactly.
Each changed field receives a blocker at its JSON pointer. Battery/reserve
remain declared authoring quantities; they are not PX4 energy measurements.

The Agent must use its registered digest-pinned image and external/fixed
inspection policy. Preview-only airframes, missing images, unknown scene IDs,
changed registrations and unsupported executor profiles block compilation.
Kubernetes never falls back to Docker. The compiler also applies existing
task/Provider resolution and feasibility checks; Docker infrastructure preflight
runs later during explicit executor materialization.

## Available interfaces

The loopback authoring API enables compilation only when its fresh
`--compilation-output` directory is configured. `--native-scenes-manifest`
selects explicit pinned registrations; omission yields an empty catalog.

| Route | Response |
| --- | --- |
| `GET /authoring/v1/native-scenes` | Current registration catalog and pins. |
| `GET /authoring/v1/native-scenes/<registration_id>/scene` | The exact pinned public-scenario bytes, with their digest ETag. |
| `GET /authoring/v1/native-scenes/<registration_id>/assets/<sha256>` | Only the registered public asset/license bytes; never private inputs. |
| `POST /authoring/v1/compilations` | HTTP 201 for compiled; HTTP 422 for blocked. |
| `GET /authoring/v1/compilations/<compilation_id>` | Immutable result published by the active compiler session. |

The result is `aero-bench.city-compilation-result/v1`. A compiled result
contains a pinned Suite and resolved identities, with `executed: false` and
`verified: false`. A blocked result contains field-specific blockers, no Suite
and no runs. Malformed JSON/schema input returns HTTP 400 using
`aero-bench.authoring-error/v1`; wrong media returns 415; an unconfigured
compiler returns 503. These routes never start a workload or download private
evidence. Compilation publications are read-only; later execution uses a fresh
separate directory.

The command-line boundary is:

```bash
python3 tools/compile_city_workspace.py \
  --draft <saved-workspace-v2.json> \
  --registration-id <catalog-registration-id> \
  --registration-sha256 <catalog-registration-sha256> \
  --native-scenes-manifest <native-scenes.json> \
  --output <fresh-compilation-directory>
```

`tools/register_inspection_reference_scene.py` publishes only the explicit B1
native reference from its pinned Suite and alignment. It copies input closure,
not runtime evidence or control configuration. Operator-declared battery and
reserve values are required and remain labelled authoring quantities. It does
not register Shanghai or establish task success.

## Compilation and execution sequence

1. Validate the saved draft and selected registration; snapshot their exact
   identities. Resolve every execution field to its implementation or blocker.
2. Publish a fresh immutable bundle with strict Suite, Task, Environment,
   Agent and WorldPackage files. Use existing `resolve_suite` and Provider/task
   registries to obtain the immutable ResolvedRun. Executor materialization and
   capability/feasibility preflight remain explicit.
3. Start only an accepted bundle through the existing control/runner path.
   Harness time advances through actual staged Provider barriers. Preserve
   failures and never treat an accepted command as completed business work.
4. Seal authoritative artifacts, run the independent verifier, and publish
   the public trace/replay manifest. Frontend consumes that public publication,
   not private evidence or an authored trajectory.

Positive tests must compile a saved current draft against a real matching
registration, apply changed values and reproduce its Run ID. Negative tests
must cover changed pins/origin, missing capabilities, unsupported algorithms
and events, malformed input, and unavailable executor prerequisites. A browser
save/refresh/export and compile/run/replay check closes the integration gate.

Control loads a published result through `ControlRunManager.from_compilation`
or `aero-bench-control serve --compilation-id ... --compilation-root ...
--execution-output ... --runner-config ...`. It independently re-resolves the
publication, checks all recorded identities and feasibility, and requires the
compiled executor profile. Existing authentication and CSRF remain in force.
`tools/validate_compiled_city_run.py` exercises HTTP catalog/start/status,
actual native execution, seal, independent verification and a public-asset
download. Its audit explicitly records `browser_validated: false`.

The actual compiled-reference run passed on 2026-09-30. Compilation
`beafba9d964949cd9798278eedb673445af38e04d4d9ed70f3941d507ec286c5`
produced Run ID
`f316faaef72168afa89aceb10df10af4211b1aa98c28a4b64f6cc89aa056fc8b`.
The authenticated HTTP path completed 522 native ticks, sealed 13 artifacts,
passed all 15 independent OCI verifier goals, published verified public replay,
and downloaded a public asset through its authenticated endpoint. Credentials
were retained only in memory. The independent read-only replay audit also
passed. Evidence is in `validation/backend-track-b-20260930/compiled-native-control-r2/`
and `compiled-native-public-replay-audit.json`. This closes the backend handoff
for the limited explicit reference, not browser integration or Shanghai execution.

The browser-saved v2 sample is available at
`validation/frontend-opus-20260930/editor-review/exported-workspace.json`.
It is parsed unchanged; the unregistered scene, preview-only second airframe
and missing image are explicit blockers. The negative test's supplied unknown
registration pin is a test input, not an accepted scene identity.
Remaining dependencies are an accepted native binding for that selected city,
frontend compile/run/replay interaction, and implemented physical logistics,
charging, weather and the selected algorithm/action set. The native reference
compiler does not close those general city gates.
