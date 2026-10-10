# One-order native parcel slice

This proposal implements one explicitly authored order, one independent parcel entity, one real PX4/Gazebo carrier and pickup/dropoff facilities in the existing BENCH city. It does not change the accepted inspection scenario or claim that its report deliveries are orders. `contract-proposal.json` records the proposed identities and demo tolerances; those authored values are not measurements. Exact facility geometry, world-frame binding, pose-reference calibration and parcel body-local transform remain required compile inputs.

## Existing interfaces to reuse

`tasks/logistics/runtime_hook.py` receives closed SceneState, PX4 provider events and stage barriers, derives physical observations and calls the real `logistics.observation.ingest` RPC. `facility_presence.py` assesses explicit landed/in-air state, 3D speed, body footprint and contact height. `dwell_eligibility.py` validates contiguous windows and independent sealed provenance. `containers/logistics-business/service.py` currently refuses physical transitions. The new slice must connect these contracts rather than toggle `LOGISTICS_RUNTIME_IMPLEMENTED`. Compose an existing runtime hook; never overwrite it or advance the harness inside a hook.

Runtime admission and final verification have separate authorities. A runtime decision may use the genuine closed observation journal; it cannot manufacture an independently sealed provenance object. The dedicated sealed logistics verifier must replay the same command, observation, dwell, attachment and transfer records before issuing the final verdict. Existing strict sealed checks remain intact.

## Expected UI and evidence sequence

1. Edit the single order/facility/carrier configuration in the real Studio editor, save, reload and compile a new immutable version. Verify that Start selects that exact compilation and its corrected datum-aware verifier source/OCI closure.
2. Start the one isolated native logistics run through the normal authenticated Control UI. Save non-secret start/run/compilation identities before any reload.
3. Show the parcel at the pickup facility with `awaiting_pickup` and facility custody. Select the order or parcel and display the same exact carrier, route and destination in both panels.
4. Admit pickup only after the authorized action and contiguous closed native dwell pass. Record facility-to-carrier custody, then display `loaded` and the declared body-relative parcel pose.
5. Show two distinct real airborne poses with `in_transit`; derive the parcel transform in the backend from the actual carrier transform. The original GLB city/UAV assets remain. The existing parcel marker may visualize the actual declared parcel state; no new toy city or procedural vehicle replacement.
6. At destination, require actual tolerance/dwell plus admitted handoff/dropoff. Record carrier-to-facility custody, detach and display `delivered`. A network message receipt alone cannot finish this stage.
7. Seal and independently verify the run, reconnect its UI, seek backward and forward without future bindings, then capture the actual supported operations in readable GIFs with timestamps.

## Verdict criteria

The independent result must check exact identities/source pins; pickup action and contiguous grounded window; attached parcel transform across at least two airborne samples; unchanged parcel ID and coherent custody history; destination geometry and dropoff dwell; one admitted handoff and detached terminal parcel; duplicate action idempotency; no delivery before destination/handoff; terminal native carrier state; and configuration/compilation/run/image consistency. Missing source facts remain UNKNOWN and fail the corresponding required goal.

The requested corrected pin is `localhost:5000/aero-bench/verifier@sha256:e8c2d1dc09369aa4d800e0f2ee975e97641922f01ac94d950964f884a7e74eec` (image config ID `5d9c75ae…`, source closure `97c9a5a5…`). Its current component is **inspection.verifier**. `containers/inspection-verifier/entrypoint.py` resolves an inspection package before dispatching inspection v3 or legacy inspection verification. It has no native-parcel dispatch. Pinning it does not certify custody or delivery. This is a concrete compilation/verifier integration gap: preserve this corrected datum pin and define an independently digest-pinned logistics verdict component before executing the new task. Do not relabel the inspection image, mutate its immutable digest, or replace its strict region checks. The review must reconcile the requested pin with the additional logistics component. No logistics compilation or flight has been created.

Original compilation `a504dba5…`, its old verifier `082f4ecd…`, failed verdict and separate passing reevaluation remain immutable.

## Review boundary and current implementation

This document is the contract review handoff, not a runtime acceptance report. The forward catalog/start-discovery patch and typed parcel admission modules are being completed in their existing Flash sessions. The native hook and physical presence contracts exist. Physical RPC transitions, parcel motion-stage ownership/projection and the dedicated sealed verdict still need integration. The seven UI steps above are expected steps until a new exact compilation, source closure, run and evidence manifest prove them.

Independent review can use the public source files listed above. The accepted inspection evidence establishes native flight and city rendering; it supplies no logistics orders. The two inspection network deliveries are now presented as task/report records, with order, parcel and custody UNKNOWN.
