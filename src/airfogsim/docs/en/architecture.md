## AeroAgentSim / airfogsim System Architecture

### 1. Introduction

`AeroAgentSim` is the external product name for the current developer workbench and simulation experience. The technical Python package name remains `airfogsim`.

The architecture still centers on the `airfogsim` discrete-event simulation core built on SimPy. The frontend layer has been refocused on a 2D developer workbench for configuration, workflow coupling inspection, run control, trajectories, and logs.

### 2. Architectural Layers

* **Simulation Core:** event-driven environment, agents, components, tasks, workflows, triggers, and managers
* **Registry Layer:** file-backed custom definitions under `registry/aeroagentsim/` plus lightweight DB indexes and run references
* **Catalog and Config Layer:** merged builtin/custom catalog extraction, compatibility lookup, config snapshots, validation, and graph generation
* **Runtime Artifact Layer:** immutable config snapshots and per-run directories under `runtime/aeroagentsim/`
* **Workbench API Layer:** REST endpoints for catalog/config/run control and WebSocket status streaming
* **2D Visualization Layer:** Leaflet-based live markers, trajectory polylines, and workflow-agent-state relation graph

### 3. Current Workbench Focus

The current visualization model intentionally avoids 3D rendering. The supported views are:

* `Overview`
* `Class Catalog`
* `Workflow Studio`
* `Run Console`
* `Trajectories & Logs`

Workflow editing is form-driven. The graph is a visualization and inspection surface, not a drag-and-drop source of truth. Readers can zoom and pan the graph for inspection, but edits still happen through tables and forms. Wheel and trackpad zoom are scoped to the graph canvas itself.

The workbench also adds:

* global `zh-CN` / `en-US` UI switching
* a centralized `Review / Validate` path for draft and graph checks
* merged builtin/custom definition views for `agent`, `task`, and `workflow`

### 4. Runtime Persistence Model

* Config snapshots are stored in `runtime/aeroagentsim/configs/`
* Each run receives its own `run_id`
* Run outputs are stored in `runtime/aeroagentsim/runs/<run_id>/`
* Logs, workflow state diffs, trajectories, and spatial snapshots are persisted separately
* SQLite is retained only for active-run indexing and lightweight cache duties

### 5. Control and Streaming

* REST handles configuration save, validation, graph generation, and run control
* REST also handles registry CRUD and definition-level validation
* WebSocket pushes `sim_status`, `workflow_state_diff`, `spatial_snapshot`, and `log_event`
* Status payloads are scoped by `run_id` so the frontend can distinguish active and historical runs

Runtime launch is gated by preflight. `POST /api/configs/{config_id}/preflight` feeds the run-start path, and `POST /api/runs` is blocked only when preflight reports errors.

Preflight warnings remain visible but non-blocking. This includes the compatibility case where the current runtime does not expose `create_airspace` or `create_frequency`, so default resource injection is skipped.

### 6. Registry and Proxy Compilation

Custom `agent`, `task`, and `workflow` definitions are declarative rather than code-upload driven. The backend loads file definitions from `registry/aeroagentsim/`, validates them, and compiles them into proxy/runtime adapters that still execute through the existing `airfogsim` primitives.

This keeps extension paths visible in the frontend while preserving backend control over executable behavior.

### 7. Core Simulation Model

The `airfogsim` backend architecture is still based on:

* `Environment`
* `Agent`
* `Component`
* `Task`
* `Workflow`
* `Trigger`
* resource and service managers

Those backend primitives remain the source of truth for execution logic, while the AeroAgentSim workbench adds metadata extraction, configuration adaptation, and frontend-friendly graph/runtime views on top.
