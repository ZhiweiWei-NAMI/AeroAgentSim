---
name: verify-delivery
description: Require detachment, a real radio contact receipt and a new RGB-D observation of the public package marker at the receiving site.
---

# verify-delivery

Require detachment, a real radio contact receipt and a new RGB-D observation of the public package marker at the receiving site.

Input: package_id and a real inbox receipt_message_id. Omit post_drop_frame_id to acquire a fresh post-contact frame; then call again with the resulting post_drop_frame_id to decode the inventory ArUco marker and back-project its actual numerical depth into the public receiving polygon. The frame must be newer than release and receipt. Site visibility alone cannot confirm the package. Unmarked, occluded, ambiguous or unresolved markers remain package_visual_unknown. The rule policy permits one additional new frame, then reports unresolved evidence. This only identifies the marked box at the site; human collection is not modeled. Arbitrary actuator/receipt objects are not accepted.

Run through the already-running local worker:

    python -m aerorescue.companion.tools verify-delivery --data-dir /data --request '{"package_id":"rescue-package-1","receipt_message_id":"RECEIPT_MESSAGE_ID"}'

The CLI queues the request and returns accepted. Poll with `python -m aerorescue.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
