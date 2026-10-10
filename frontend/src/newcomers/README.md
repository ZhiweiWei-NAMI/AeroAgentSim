The newcomer console follows the shared template, KPI, business-event and algorithm contracts.

Home loads `/v1/templates`; Run now posts `{knobs}` to `/{id}/runs` and opens Simple view. Customize preserves declared knob types. Open in Studio posts to `/{id}/workspace`.

Simple view reads additive commit `events` and `kpis` at the same cut as the city. Clicking an event seeks its originating commit and selects the actual entity identity. Missing measurements stay unmeasured. Per-cut snapshots take precedence over the summary endpoint; time-only summaries cannot distinguish same-nanosecond cuts.

API shapes not specified in the shared design:
- `/v1/runs/{id}/kpis`: `{run_id,final,series:[{commitIndex,at_ns,kpis}]}`.
- `/v1/runs/compare?ids=...`: `{runs:[{id,title,kpis,knobs,algorithms}]}`; additional aligned tables are accepted.
- Additive `GET /v1/algorithms`: `[{id,slot,label,description}]`. Studio saves `algorithms:{slot:id}` unchanged; backend validation decides availability.

Production has no fixture fallback. `frontend/e2e/ux-fixtures.ts` explicitly mocks pending catalog endpoints and adapts accident start/workspace calls to the real authoring/compiler/runtime API. Fixture screenshots are marked. These tests do not prove the pending endpoints' runtime implementation.

Recording flows require the merged template endpoints by default. `AEROAGENTSIM_UX_API_FIXTURES=1` enables a visibly labeled fixture preview, not release media. The console e2e waits for the demo's junction at 8 simulated seconds before injecting: an injection near 1 s was admitted, but this demo's proximity predicate never became true and no capture was produced. That is a scenario/runtime limitation, not a successful response to the early event.
