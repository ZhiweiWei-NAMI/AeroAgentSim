# P09 v12 X5 boundary repair for independent review

Scope: one actual X5 seed00 representative, five coupled iterations; metadata-only L6-4 v1/v2 copies. This is not acceptance of30episodes/allseeds/210+ARM. Published210 and UE capture remain unchanged. V10 is preserved. The first v12 bootstrap attempt failed explicitly on missing pad request authority; its output remains local. The successful source snapshot excludes pad arbitration and received-result reroute from numerical initialization. It uses actual native receipts for final admission.

## R1 received result timer

Original narrative delay remains30originalticks (0.1s/tick), arbitration-relative. The actor must first receive the exact pad result containing the trusted arbitration_ns and policy/version/lifetime. The receiver gate does not consult a global arbitration event. Native result TX58.5s, RX58.503246845s; received arbitration58s +3s admits reroute61s. The immutable modeled application ledger binds this payload to real UDP RX; this is not wire JSON decoding or a hardware/PX4 ACK. `actual_receipt_offline_probes.json` reuses the same final native result and changes only inaccessible global arbitration580→581; both admit at610. Missing/wrong-bound payloads fail explicitly; unreceived or expired results cannot admit movement.

## R2 lease boundary

Authorization is half-open: use_ns <65,500,000,000 (tick655). Real accepted request/result records remain historical evidence. Probes on all four final native records allow654 and deny655/656/660/900. Transport delivery deadline and current service-use expiry remain different quantities. No rejected record is silently rewritten to unreceived.

## R3 phase identity

Four actual event_trace rows are exported verbatim. Priority/second approaches both occur at280; second requests335 and priority575. Their payload.event_id, instance_id, source_event_id, source_topic, topic and sample_id each have four distinct values. See four_phase_event_trace.jsonl and phase_identity_check.json. Request topics identify request phases rather than reusing approach IDs.

## R4 RF narrative metadata

Both adopted scene_setup and event_script descriptions plus required_event describe cochannel UDP competition and owner-local/receipt-bound response. Legacy wideband_jamming IDs remain for trace references; they do not certify wideband jamming. The L6 copies here are metadata-only; no new L6 native execution is claimed. RF settings/packet/control mechanism remain unchanged.

## Actual execution and limits

X5 result/action endpoint: station repair65.5s; own healthy observations recover66s; landing actions69s/74.5s; both UAVs are stationary landed for896..900. Audit contains23rows:18ok handler records and5explicit omissions. Handler records include state updates as well as moves; they are not23physical motions. Parent's96v10 auditrows included omissions and must not be described as96executed physical actions. Independent review of the three named v10 cases found59rows (50ok,9omitted); the larger96scope has not been reconciled here.

33focused tests pass in35.516s, covering exact expiry, strict RX, timer binding, owner independence, weather availability, late landings and backup-contact binding. Source manifest pins the exact15files used. `exact-v10-v12.diff` includes the separately verified v11 L2 helper delta; it does not imply allv11/v12 scenarios were run. Original source references are retained in profiles.

Native ns-3 network with authored10Hz waypoint replay is the measured scope. PX4 aerodynamic motion and UE visuals were not executed. Synthetic test controls are separate from native packet receipts. Parent/native independent acceptance is still required before adoption.
