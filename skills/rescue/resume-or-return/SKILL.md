---
name: resume-or-return
description: Resume explicitly saved task obligations or fly a public return route and land.
---

# resume-or-return

Resume explicitly saved task obligations or fly a public return route and land.

Input: remaining_route or return_position_enu_m; release_receipt required while a relay role is held.

Run through the already-running local worker:

    python -m aeroagentsim.companion.tools resume-or-return --data-dir /data --request '{}'

The CLI queues the request and returns accepted. Poll with `python -m aeroagentsim.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
