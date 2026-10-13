These shared components serve the current Workbench's template settings, KPIs, business events and run comparison.

Workbench loads `/v1/templates`. `TemplateKnobControl` preserves declared knob types; `presentation.ts` provides the shared labels. The retired gallery, customization page and experiment panel have been removed. Batch experiments remain available through the backend and CLI.

Event and KPI views read committed `events` and `kpis` at the same cut as the city. Clicking an event seeks its originating commit and selects the actual entity identity. Missing measurements stay unmeasured. Per-cut snapshots take precedence over the summary endpoint; time-only summaries cannot distinguish same-nanosecond cuts.

API shapes not specified in the shared design:
- `/v1/runs/{id}/kpis`: `{run_id,final,series:[{commitIndex,at_ns,kpis}]}`.
- `/v1/runs/compare?ids=...`: `{runs:[{id,title,kpis,knobs,algorithms}]}`; additional aligned tables are accepted.
- Additive `GET /v1/algorithms`: `[{id,slot,label,description}]`. Studio saves `algorithms:{slot:id}` unchanged; backend validation decides availability.

Current browser workflows live in `frontend/e2e/workbench.spec.ts` and `frontend/e2e/assistant.spec.ts`. The latter uses a scripted model server to verify the conversation and runtime wiring; it does not evaluate a live model's decision quality.
