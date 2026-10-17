---
name: compare-observations
description: Compare old and new local evidence without treating occlusion as departure.
---

# compare-observations

Compare old and new local evidence without treating occlusion as departure.

Input: first_frame_id, second_frame_id.

Run through the already-running local worker:

    python -m aerorescue.companion.tools compare-observations --data-dir /data --request '{"first_frame_id":"FIRST_FRAME_ID","second_frame_id":"SECOND_FRAME_ID"}'

The CLI queues the request and returns accepted. Poll with `python -m aerorescue.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
