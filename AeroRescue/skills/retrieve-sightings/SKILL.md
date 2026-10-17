---
name: retrieve-sightings
description: Search this UAV's persistent history or reanalyze an actual old frame.
---

# retrieve-sightings

Search this UAV's persistent history or reanalyze an actual old frame.

Input: query, observed_after_ns, observed_before_ns, region_enu, source, limit; optional reanalyze_frame_id.

Run through the already-running local worker:

    python -m aerorescue.companion.tools retrieve-sightings --data-dir /data --request '{"query": "屋顶人员", "limit": 5}'

The CLI queues the request and returns accepted. Poll with `python -m aerorescue.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
