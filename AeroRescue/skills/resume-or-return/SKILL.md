---
name: resume-or-return
description: Resume explicitly saved task obligations or fly a public return route and land.
---

# resume-or-return

Resume explicitly saved task obligations or fly a public return route and land.

The relay release authorization is read from the actual local inbox, never from model-supplied receipt fields.

Input: remaining_route or return_position_enu_m; received centre closure required while a relay role is held.

Run through the already-running local worker:

    python -m aerorescue.companion.tools resume-or-return --data-dir /data --request '{"return_position_enu_m":[-3,0,3]}'

The CLI queues the request and returns accepted. Poll with `python -m aerorescue.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
