---
name: reobserve-target
description: Move to a reachable geometrically different viewpoint and capture a new frame.
---

# reobserve-target

Move to a reachable geometrically different viewpoint and capture a new frame.

Input: frame_id, detection_id. Generates a legal independent viewpoint from actual camera calibration.

Run through the already-running local worker:

    python -m aerorescue.companion.tools reobserve-target --data-dir /data --request '{"frame_id":"FRAME_ID","detection_id":"DETECTION_ID"}'

The CLI queues the request and returns accepted. Poll with `python -m aerorescue.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
