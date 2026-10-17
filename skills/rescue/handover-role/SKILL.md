---
name: handover-role
description: Send the current role and outstanding obligations to a replacement through radio.
---

# handover-role

Send the current role and outstanding obligations to a replacement through radio.

This sends a radio handover proposal. This implementation retains the current relay obligation until a received centre closure; receiving or sending a proposal alone does not release it.

Input: destination, data: outstanding role/obligations, optional route. Handover is not accepted until its radio reply arrives.

Run through the already-running local worker:

    python -m aeroagentsim.companion.tools handover-role --data-dir /data --request '{"destination":"u3","data":{"role":"relay","pending_message_ids":["MESSAGE_ID"]}}'

The CLI queues the request and returns accepted. Poll with `python -m aeroagentsim.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
