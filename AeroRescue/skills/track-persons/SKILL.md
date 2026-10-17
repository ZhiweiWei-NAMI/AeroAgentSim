---
name: track-persons
description: Associate anonymous detections across actual frames.
---

# track-persons

Associate anonymous detections across actual frames.

Input: frame_id. Detections and locations are loaded from the local archive.

Run through the already-running local worker:

    python -m aerorescue.companion.tools track-persons --data-dir /data --request '{"frame_id":"FRAME_ID"}'

The CLI queues the request and returns accepted. Poll with `python -m aerorescue.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
