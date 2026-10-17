---
name: verify-delivery
description: Require observed detachment, a new frame and an actual radio contact receipt.
---

# verify-delivery

Require observed detachment, a new frame and an actual radio contact receipt.

Input: package_id and a real inbox receipt_message_id. Omit post_drop_frame_id to acquire a fresh post-contact frame; then call again with the resulting post_drop_frame_id to check actual receiving-site visibility and the payload ledger. Arbitrary actuator/receipt objects are not accepted.

Run through the already-running local worker:

    python -m aeroagentsim.companion.tools verify-delivery --data-dir /data --request '{}'

The CLI queues the request and returns accepted. Poll with `python -m aeroagentsim.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
