---
name: send-evidence
description: Transfer real file bytes or structured report through damaged radio.
---

# send-evidence

Transfer real file bytes or structured report through damaged radio.

Input: artifact_id for bytes, or data for a structured report; destination; optional route. A confirmed report uses data.observation_verdict="confirmed", data.first/data.second={frame_id,detection_id}, and data.evidence_transfer.message_id from this task’s completed send. The wrapper recalculates verification and exclusion geometry from both real frames and requires the transferred original/crop to contain a verified detection. A delivery_verified report must reference the package_id already verified by the local payload ledger.

A relay advertisement uses data.kind="relay_ready", data.service_peer as the public endpoint, and data.probe_results=[{peer:"centre",result:{message_id:PROBE_ID}},{peer:SERVICE_PEER,result:{message_id:PROBE_ID}}]. Send only to centre or that service endpoint. The wrapper reads the actual source-bound probe results, requires both ACKs within 5 simulated seconds, and derives the expiry. Probe and notify one centre/endpoint pair at a time; a stale advertisement must be remeasured.

A distinct later observation can report data.kind="new_incident_clue" with frame_id/detection_id and reference_frame_id/reference_detection_id. Positions, deadline and the independent event ID are derived by the wrapper. A person observation alone does not assert rescue demand.

Run through the already-running local worker:

    python -m aerorescue.companion.tools send-evidence --data-dir /data --request '{"artifact_id":"ARTIFACT_ID","destination":"centre"}'

The CLI queues the request and returns accepted. Poll with `python -m aerorescue.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
