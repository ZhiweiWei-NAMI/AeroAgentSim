---
name: handover-role
description: Send the current role and outstanding obligations to a replacement through radio.
---

# handover-role

Send the current role and outstanding obligations to a replacement through radio.

This version transfers a held relay role. The replacement must already have received and accepted a same-event task with `accept_relay_handover: true`, have public relay capability, and have no protected or existing relay duty. Sending or receiving a proposal alone does not release the original relay.

Input phases:
- `action: propose`, `destination`, optional `evidence_message_ids`: references to actual file transfers already fully acknowledged by the replacement. Existing retained-message obligations must also have real final ACKs. The actual held point, peer set and old task version come from local execution records.
- `action: accept`, `message_id`: a locally received handover offer. Required files must exist as complete received copies matching the offering task. Cordis rule policy then submits real arm/takeoff/hold/probe operations.
- `action: acknowledge`, `message_id`: the accepted offer. This sends responsibility acceptance only after the replacement actually holds the point and has fresh source-bound probe ACKs, including its own accepted task version.
- `action: release`, `message_id`: the received replacement acceptance. Only its matching radio evidence allows the old relay to stop holding and return. The replacement keeps the relay duty and refreshes route readiness.
- `action: status`: report an elapsed acceptance deadline; refusal or timeout keeps the original responsibility.

Run through the already-running local worker:

    python -m aerorescue.companion.tools handover-role --data-dir /data --request '{"action":"propose","destination":"u4","evidence_message_ids":["COMPLETED_FILE_MESSAGE_ID"]}'

The CLI queues the request and returns accepted. Poll with `python -m aerorescue.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
