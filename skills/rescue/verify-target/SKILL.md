---
name: verify-target
description: Compare independently acquired, geometrically different observations.
---

# verify-target

Compare independently acquired, geometrically different observations.

Input: first_frame_id, first_detection_id, second_frame_id, second_detection_id.

Run through the already-running local worker:

    python -m aeroagentsim.companion.tools verify-target --data-dir /data --request '{}'

The CLI queues the request and returns accepted. Poll with `python -m aeroagentsim.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
