# Workbench verification

AeroAgentSim is verified as an operational workflow: open or create a workspace,
edit the world and behaviours, validate the saved revision, run it, submit typed
inputs, inspect the resulting state and replay the same journal.

The independent aerokernel owns time, state and command receipts. Graph, city,
inspector and timeline consume one temporal store. Render interpolation never
writes simulation state. A source workspace revision, registry digest and pinned
run configuration digest identify different things and must not be substituted
for each other.

## Checks before delivery

- Build and type-check the frontend; run the existing tests for changed modules.
- Open the built application in Chromium. Exercise navigation and direct URLs,
  workspace save/reopen, validation, language switching, graph and city selection,
  split resizing, live control, injection, timeline seek, replay and stored photos.
- Exercise a traffic-accident run and a food-delivery run, including a typed
  disruption. Confirm that the journal records the resulting events and receipts.
- Compare loading on the same run and browser viewport. Record whether the
  renderer is software or hardware; report shell usability separately from full
  city readiness. Check cold and warm browser caches.
- Reload and reconnect an active view. The received journal prefix must remain
  contiguous, without resubmitting simulation commands.
- Keep representative real scenario outputs and documentation screenshots.
  Remove task-only scripts, traces, temporary outputs and caches from the
  delivered tree. Verify the pushed commit matches the tested sources.

There is no separate acceptance-dashboard command. Development browser scripts
and measurements may live outside the checkout and are removed after verification.
Native SUMO/PX4 profile configuration is not evidence that an external simulator
was started. Live model verification must record actual provider calls; replay
must make none.
