---
name: select-drop-site
description: Choose a point inside the public receiving polygon away from uncertain people locations.
---

# select-drop-site

Choose a point inside the public receiving polygon away from uncertain people locations.

Input: frame_id and detection_id from local observations, or plan_message_id for the accepted radio delivery plan. Optional observation_refs lists frame_id/detection_id pairs; all recorded people in those frames enter the exclusion set, including people not observed in the newer frame.

Run through the already-running local worker:

    python -m aerorescue.companion.tools select-drop-site --data-dir /data --request '{"frame_id":"FRAME_ID","detection_id":"DETECTION_ID"}'

The CLI queues the request and returns accepted. Poll with `python -m aerorescue.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
