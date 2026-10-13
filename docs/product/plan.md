# Workbench rebuild plan

Source of truth for behaviour: [workbench-logic.md](workbench-logic.md). This file
records the status against the logic, the backend/frontend contract and the
repository rules. Update it when a contract changes.

## Repository rules

- Work in the task's assigned git worktree and edit only owned files. Keep
  one web app (the workbench); no alternative pages or feature flags.
- Agents do not commit, branch, push or stash. The integrator reviews and commits.
  Use additional agents only when the current task explicitly authorizes them;
  parallel assignments must have disjoint file ownership. Do not write generated
  builds into `frontend/dist` during a source-only task.
- Existing servers belong to their operators. This documentation assignment
  does not start servers, builds, tests, typechecks or browsers.
- No fabricated state: missing capability, failed call, waiting and unknown are shown
  as such. No demo data presented as run results. No silent fallbacks.

## Documentation integration (2026-10-13)

INT-08, INT-09 and the documentation part of FE-10 are addressed through
[installation and acceptance](../../INSTALL.md), [asset guidance](../../ASSETS.md),
the English/Chinese README entries and the
[blank-project tutorial](../guides/blank-project-tutorial.md)
([中文](../guides/blank-project-tutorial.zh-CN.md)). The
[guide index](../guides/README.md) exposes the tutorial, `tools/build_map.py`
and region API. Documentation changes are not runtime acceptance evidence.
安装、资产、快捷键、失败语义及空白项目教程已更新；文档交付不等于运行验收。

Integration assumptions supplied with this task (not a claim that every old
server implements them):

- Asset fetch verifies the pinned archive SHA-256, with
  `AEROAGENTSIM_ASSET_PACK_SHA256` override; installation checks manifest sizes.
  Pack NOTICE contents remain unverified until the actual archive is inspected.
- `GET /v1/studio/catalog` supplies configured extract ids and known bounds.
  Region POST accepts `dry_run:true` and returns the C1 `preview` without writes;
  application keeps its existing updated-workspace response. The region contract
  has no revision argument. Applying changes the origin and invalidates the old
  network; positioned entities are not silently translated. City/region conflict
  validation reports section `space`, path `/city` (C5).
- Removal preview supplies dependencies (C3); confirmation removes the instance
  and dependent references atomically, with revision conflict reported as 409.
- Run records expose `results_final` (C6); false means aggregation remains pending,
  absence means unknown. Algorithm `consumers` (C8) identify real readers; absence
  or an empty list is not evidence of applicability.
- Beginner frontend labels are **Region**, **Preview region**, **Apply region**,
  **Generate road network**, **Add objects**, **Remove**, **Add relation**,
  **Add order stream**, **Add cooperation**, **Weather & traffic**, **Add gust**.
  Missing controls/404/422 are reported as unavailable or invalid, not success.

Remaining edits outside this assignment's ownership:

- `docs/README.md`: add the tutorial (both languages), guide index, map builder
  and region API to Get started/Guides.
- `docs/getting-started/install.md`: link the root INSTALL acceptance block,
  distinguish Windows shells, and replace its stale checksum/licensing guidance
  with ASSETS. Quickstart should link the blank-project tutorial.
- `docs/reference/http-api.md`: document C1 preview and C2/C3/C5/C6/C8 additions.
- `docs/product/workbench-logic.md` (read-only here): consider making the five-way
  distinction explicit by adding LLM as an optional decision implementation to
  its four-concept table; specify region preview/no-write, origin-change impact,
  dependent-reference removal, decision failure/re-decision and final aggregation
  boundaries. Preserve its existing explicit initialize-at-t=0/start contract.
- If a dedicated health endpoint is required, it needs an app-route change;
  the existing `/v1/templates` GET is documented as a limited HTTP readiness probe.
  Native Windows service support needs a replacement for the `fork` context.

上述未归属文件的修改需由集成者完成；主产品逻辑、QA 和演示文件未在此修改。

## Historical status against the product logic (2026-10-10)

| Logic | Status |
|---|---|
| One workbench | Done. `/` and `/w/<workspace>` render `frontend/src/workbench/`; old routes redirect with their context. Only `/agents/<run>` (agent console), `/docs/platform/*` and the headless capture renderer (`?capture=1`) remain separate. |
| Save version → initialize → explicit start | Done. Immutable versions (`versions/<n>/files`, digest); `POST /v1/runs {studio_workspace, version, autostart:false}` → `initialized`; `start` → `running`. |
| Template creates a draft | Done. The gallery creates a project; the console never runs a template directly. |
| Validation locates the error | Partly. Issues carry `section`, pointer `path`, `entity_id`/`field`/`binding`; the compiler still reports its first failure only. |
| Run uses a fixed version | Done. Run manifest records `workspace_version`, `workspace_digest`, `template`, `derived_from`. |
| Modify and retry | Done. `POST /v1/runs/{id}/derive-workspace`. |
| Commands with receipts | Partly. Ingress returns the kernel receipt; no command ledger endpoint yet (P0 item 6). |
| Runtime relation graph | Frontend projection from the feed (floating graph window); no backend graph endpoint (item 7). |
| Config by business meaning | Done in the UI: controls + seven sections + file, on one draft; structured rule editor in Tasks. |
| Instance vs contract | Partly. Objects table edits instances; no contract-checked instance endpoint (item 9). |
| Algorithm scope / hot swap | Not started (item 10). |
| Decision detail in replay | Via the agent console; no decisions endpoint (item 11). |
| Compare | KPI comparison; no config diff (item 12). |
| Contract inheritance | AeroGraph overlay; no `inherited_from` (item 13). |
| Simulation speed | Done. `GET/POST /v1/runs/{id}/speed` (0.05–1000× or max), separate from playback speed. |

## API contract

Existing endpoints keep working unless replaced below. All new responses are JSON;
errors use HTTP status + `{"detail": ...}`. `501` means "capability not
implemented", and the UI shows exactly that.

### P0 — lifecycle, versions, commands

1. **Versions**
   - `POST /studio/workspaces/{id}/versions` body `{"revision": int, "note"?: str}` →
     `201 {"version": int, "revision": int, "digest": str, "created_at": str, "validation": {...}}`.
     `409` if revision is stale; `422` if the draft at that revision has no passing validation.
   - `GET /studio/workspaces/{id}/versions` → list, newest first.
   - `GET /studio/workspaces/{id}/versions/{version}` → version incl. frozen `scenario`.
   - `GET /studio/workspaces/{id}` additionally returns `latest_version`, `template_source?`, `derived_from?`, and `dirty` (draft differs from latest version).
2. **Run lifecycle**
   - `POST /v1/runs` body `{"studio_workspace": id, "version": int}` → `201` run metadata
     with `status: "initialized"`: worker spawned, WAL header and t=0 state committed, sim time not advancing.
   - `POST /v1/runs/{id}/start` → `initialized → running`. `409` from any other status.
   - `pause` / `resume` / `stop` unchanged. `stop` is valid from `initialized`.
   - Status set: `initialized, running, paused, completed, stopped, faulted, interrupted, input_timeout`.
   - CLI, experiments and headless template runs pass `"autostart": true` (or call internals) and keep their behaviour.
   - Run metadata includes `workspace_id, workspace_version, workspace_revision, config_digest, template_source?, derived_from?`.
3. **Templates**: `POST /v1/templates/{id}/workspace` stores `template_source: {"id", "knobs"}` on the draft.
   The console does not call `POST /v1/templates/{id}/runs`.
4. **Derive**: `POST /v1/runs/{id}/derive-workspace` body `{"name"?: str}` → `201 {"workspace_id"}`;
   the draft holds the run's pinned scenario and `derived_from: {"run_id", "workspace_id"?, "version"?}`.
5. **Validation issues**: `POST /studio/workspaces/{id}/validate` result
   `issues: [{"code", "message", "section", "path", "entity_id"?, "field"?, "binding"?}]`,
   collecting all independent issues rather than stopping at the first.
   `section` ∈ `space, objects, relations, tasks, components, algorithms, run`.
   `path` is a JSON pointer into the draft `scenario`.
6. **Commands**
   - `GET /v1/runs/{id}/command-types?entity=` → commands admissible for that entity now, each with typed payload schema (from the injection manifest / AeroGraph command definitions).
   - `POST /v1/runs/{id}/commands` body `{"type", "target"?, "payload", "client_token"}` →
     `202 {"command_id", "state": "submitted"|"accepted"|"rejected", "reason"?}`.
     `client_token` makes resubmission idempotent (refresh must not duplicate).
   - `GET /v1/runs/{id}/commands?entity=&cut=` → ledger entries
     `{"command_id", "type", "target", "payload", "state", "history": [{"state", "sim_time_ns", "commit_index"}], "result"?}`,
     states `submitted → accepted|rejected → executing → completed|failed`, derived from journal records.
   - SSE `/v1/runs/{id}/stream` additionally emits `event: command` on each state change.

### P1 — graph, configuration sections, instances, algorithms, decisions

7. `GET /v1/runs/{id}/graph?cut=<commitIndex>` → `{"cut", "nodes": [{"id", "kind": "entity"|"task"|"order"|"resource"|"event"|"facility", "type", "label", "spatial": bool}], "edges": [{"source", "target", "relation"}]}`.
   Entity ids equal the viewer-feed entity keys. Works in live and replay from journal only.
8. `GET /studio/workspaces/{id}/sections` → seven sections, each a list of items
   `{"path", "label", "label_zh", "value", "schema": {"type", "unit"?, "crs"?, "enum"?, "min"?, "max"?}, "contract"?: {"type_id", "field"}}`.
   Writes use the existing `POST /studio/workspaces/{id}` (whole scenario + expected revision).
9. `POST /studio/workspaces/{id}/entities` body `{"type_id", "instance_id", "count"?, "position"?, "attributes", "model_asset"?, "components": [...]}` — checked against the contract; `DELETE /studio/workspaces/{id}/entities/{instance_id}`. `GET /studio/assets/models?type_id=` lists bindable models.
10. `GET /v1/algorithms` adds per algorithm `applies_to` (task kinds / entity groups / component), `inputs`, `outputs`, `params_schema`, `runtime_switchable` (true only if the engine supports a recorded switch). A switch on a live run is a command (`type: "algorithm.switch"`) and returns `501` when unsupported.
11. `GET /v1/runs/{id}/decisions?entity=&cut=` → recorded decisions (observation digest, proposal, checks, submitted command id, receipt). Never calls a model.
12. `GET /v1/runs/compare?ids=` adds `config_diff: [{"path", "values": {run_id: value}}]` and `alignment: "sim_time"`.
13. `GET /studio/types/{type_id}` fields carry `inherited_from`, `unit`, `crs`, `constraints`; plus `relations`, `predicates`, `events`, `commands`.

## Frontend structure

- `/` — gallery of the nine worlds (thumbnail → guide → create project) and your projects; `/w/:workspaceId` — the workbench.
  URL query carries context: `run`, `mode=live|replay`, `cut`, `sel`, `panel=<section>`, `records`, `compare=<id,id>`, `graph=1|2`, `gallery`, `contract`, `type`.
- Layout: full-bleed 3D stage with hovering glass panels — top bar (project, version, run status, sim time, initialize/start/pause/resume/stop, simulation speed),
  KPI cards, left object list, right inspector (state, story, operate), bottom dock (playback, timeline, events, results, photos, why), floating expandable
  relation graph (selecting flies the camera), camera pad, ⌘K palette.
- Configuration opens as a panel inside the workbench (the 3D draft preview stays): validate → issues jumping to section/field, save version, initialize.
  Unsaved drafts survive refresh (local persistence keyed to revision).
- Records panel: run list, replay, multi-select compare, "modify and retry" (derive). Contract tool (AeroGraph) is an overlay.
- Bilingual (zh-CN/en). Acceptance flow is `frontend/e2e/workbench.spec.ts`.

## Acceptance

The main line in workbench-logic.md, executed in a browser on food delivery, plus:
`pytest` green for touched backend modules; `npm run typecheck`, `npm test`, `npm run build` green;
refresh during a live run neither restarts, stops nor resubmits; replay makes no model or engine calls.
