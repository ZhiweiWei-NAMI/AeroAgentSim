# Delivery evidence and AeroGraph consistency

AeroRescue follows AeroGraph's separation of observations, unknown state,
causal receipts and terminal outcomes. The current application installs the
flight and radio engines; it does **not** install a typed delivery-predicate
binding. Its Python delivery checks and retained evidence must not be described
as automatic full-graph validation.

## What establishes marked-box placement confirmation

The evidence chain has distinct stages:

1. A release command is submitted. Acceptance alone does not prove detachment.
2. An actuator observation establishes detachment, with source time or an
   explicitly observed interval. Unknown attachment state remains unknown.
3. The package contacts the configured receiving surface for the required
   continuous interval. A gap or nonmatching contact breaks that interval.
4. The contact receipt reaches the carrier and centre through the radio model.
   Receipt commit precedes the final ACK; missing ACK is not a missing commit.
5. A new post-receipt RGB-D frame identifies the publicly configured package
   marker and locates its visible top at the public receiving site.
6. The receiver learns that both original receipt transfers have final ACKs and
   sends one further radio confirmation to the centre. The centre must actually
   receive it before completing the task. That notice does not require another
   recursive confirmation notice.

Package, task revision, frame, receipt and confirmation references must remain
associated. Extending the simulation observation horizon does not extend the
business deadline. An unresolved observation cannot be converted to success by
renaming a status or by treating absence of evidence as false.

The native contact-sensor receipt is produced in
[`payload_receiver.py`](../../containers/px4-gazebo/service/payload_receiver.py)
(lines 32–63). This iteration instead executes the explicit mock contact model
in [`mock_backend.py`](../src/aerorescue/companion/mock_backend.py)
(`advance_payload`); it is not a native contact-sensor result. The receiver's
ACK-dependent notice is queued in
[`coordinator.py`](../src/aerorescue/companion/coordinator.py)
(`confirm_receiver_transfers`), and the centre associates the original receipt
IDs and their receive/ACK times in
[`centre.py`](../src/aerorescue/companion/centre.py) (`delivery_proof`). The centre also requires the actual received carrier report
to contain the new marker observation, associated frame/package/artifact
references, and acquisition time after release and receipt but no later than
the received report. Historical site-only success booleans do not satisfy it.

## Package observation: supported claim and limits

The previous implementation checked whether the selected receiving site was
visible. That was insufficient evidence of the package itself.

The marked-box implementation uses a public inventory mapping from package ID
to ArUco dictionary, ID, physical tag size and border width. The application
reads archived PNG pixels, decodes the ID, samples registered numerical depth,
and transforms through camera calibration and the aircraft's estimated pose.
It then checks the public receiving polygon and declared surface-height
allowance. It receives no simulator package pose.

See [`payload.py`](../src/aerorescue/companion/payload.py)
(`observe_marked_package`) and
[`tools.py`](../src/aerorescue/companion/tools.py) (`verify_delivery`). Missing,
wrong, non-unique, occluded or unresolved markers remain unknown. The height
allowance is an engineering assumption, not measured covariance. A historical
`observed_at_site` Boolean is not the new visual evidence record.

This identifies the configured marked box at the site. It does not establish
recipient collection and is not generic recognition of unmarked packages.
The native scene declares the inventory, but this change does not paint native
Gazebo assets with a marker; absent native pixels remain unknown.

The mock renderer uses an ENU-axis-aligned moving box without parcel rotation.
Its RGB, occlusion and depth are sampled from the same surface. Optical rays
have camera Z equal to one before rotation and are not normalized, so their
intersection parameter is optical-Z rather than Euclidean range.

## Checked component evidence

The retained `parcel-marker-render-01`, `parcel-marker-render-02` and
`parcel-marker-observe-review-01` development runs contain actual PNG, depth,
metadata and OpenCV outputs. Two landed views decode the expected ID and are
confirmed by the application observer. Seven negative images remain unknown:
wrong ID, no marker, side-wall occlusion, insufficient resolution, airborne
height, position outside the receiving polygon, and a higher supporting canopy.

The initial intended canopy-occlusion fixture caught the parcel on its top;
its visible-marker result is retained. A separate fixed side wall outside the
fall trajectory supplies the actual occlusion case. These are renderer and
observer component results, not a complete mission or native validation.

The subsequent `mock-package-visual-02` tool-chain runs exercise real inspector
flight and images, a radio-delivered report/plan, carrier release, contact,
receipt/ACK transfer and new capture. The marked branch verifies delivery from
the new frame; the unmarked branch obtains two new frames and remains unknown
despite receiving the same required contact evidence. Both return home. The
driver selects the tool calls and sends a diagnostic report; these runs do not
exercise five autonomous agents or the centre's final completion control.

## Cancellation is also an evidence question

A terminal failed operation does not establish physical cleanup. The kernel's
[command contract](../../aerokernel/docs/DESIGN.md) (lines 114–124) makes this
explicit: accepted cancellation is not completed cancellation; `canceled`
means cleanup completed. A completion that wins the cancellation race is
separately retained as `succeeded`.

The runtime therefore preserves failed cleanup as uncertain, withholds a
`no_active_operation` assertion and does not authorize a home-return operation
on that basis. This includes a cancellation arriving after an earlier failed
cleanup; an empty list of newly canceled operations is not positive evidence.

## Existing AeroGraph mechanisms

The [predicate engine](../../src/aeroagentsim/engines/predicate.py)
(lines 604–727) already checks committed input, producer and source-clock
identity; retains unknown diagnostics; and records the read cut, bindings,
clocks and causal inputs in a `SampleFrame`. An entered edge requires known
false followed by known true, not unknown followed by true. Temporal window
rules also require the necessary observed history.

Those are the rules used for this consistency review. A future typed delivery
binding would additionally need an authorized evidence producer and explicit
sample bindings. Merely configuring a predicate or copying JSON logs into a
registry would not create the missing measured facts.

The existing `predicate` plugin and `bindings.samples` are the reusable entry
points; `ingress-consumer` only records command receipts, while `records` needs
an existing source Fact. A later binding would therefore need a small
application-owned evidence producer, not a new simulator or ontology: publish
only the evidence actually available to the relevant recipient at its kernel
boundary, then bind those records to the existing evaluator. That optional
work is not implemented or counted as completed here.
