---
name: search-area
description: Fly a public-area coverage route and collect actual new frames at reached viewpoints.
---

# search-area

Fly a public-area coverage route and collect actual new frames at reached viewpoints.

Input: polygon: public ENU xy vertices; altitude_m; footprint_width_m (planning spacing only)

Run through the already-running local worker:

    python -m aeroagentsim.companion.tools search-area --data-dir /data --request '{"polygon":[[35,35],[85,35],[85,85],[35,85]],"altitude_m":40,"footprint_width_m":15}'

The CLI queues the request and returns accepted. Poll with `python -m aeroagentsim.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
