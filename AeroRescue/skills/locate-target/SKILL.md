---
name: locate-target
description: Back-project valid registered depth with same-time PX4 estimated pose.
---

# locate-target

Back-project valid registered depth with same-time PX4 estimated pose.

Input: frame_id, detection_id from actual stored inference.

Run through the already-running local worker:

    python -m aerorescue.companion.tools locate-target --data-dir /data --request '{"frame_id":"FRAME_ID","detection_id":"DETECTION_ID"}'

The CLI queues the request and returns accepted. Poll with `python -m aerorescue.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
