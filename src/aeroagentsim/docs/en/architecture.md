## AeroAgentSim System Architecture

### 1. Introduction

AeroAgentSim combines a discrete-event simulation backend with a developer workbench for workflow configuration, validation, run control, and runtime inspection.

The current architecture centers on the `aeroagentsim` simulation core built on SimPy. The workbench layers on top of that core to expose catalog metadata, config snapshots, graph inspection, run diagnostics, trajectories, and logs.

### 2. Architectural Layers

* **Simulation Core:** environment, agents, components, tasks, workflows, triggers, and managers
* **Registry Layer:** file-backed custom definitions under `registry/aeroagentsim/` with lightweight DB indexes and run references
* **Catalog and Config Layer:** builtin/custom catalog extraction, compatibility lookup, config snapshots, validation, and graph generation
* **Runtime Artifact Layer:** immutable config snapshots and per-run directories under `runtime/aeroagentsim/`
* **Workbench API Layer:** REST endpoints for catalog, config, and run control plus WebSocket status streaming
* **Visualization Layer:** live 2D markers, trajectory polylines, and the workflow-agent-state relation graph

### 3. Workbench Interaction Model

The workbench is organized around:

* `Overview`
* `Class Catalog`
* `Workflow Studio`
* `Run Console`
* `Trajectories & Logs`

`Workflow Studio` uses forms and tables for persisted configuration edits. The relation graph is an interactive inspection canvas with auto layout, wheel or trackpad zoom scoped to the canvas, blank-background panning, and node drag refinement for layout cleanup.

### 4. Runtime Persistence Model

* Config snapshots are stored in `runtime/aeroagentsim/configs/`
* Each run receives its own `run_id`
* Run outputs are stored in `runtime/aeroagentsim/runs/<run_id>/`
* Logs, workflow state diffs, trajectories, spatial snapshots, and metrics are persisted separately
* SQLite is used for active-run indexing and lightweight cache duties

### 5. Control and Streaming

* REST handles configuration save, validation, graph generation, and run control
* REST also handles registry CRUD and definition-level validation
* WebSocket pushes `sim_status`, `workflow_state_diff`, `spatial_snapshot`, and `log_event`
* Status payloads are scoped by `run_id` so the frontend can distinguish active and historical runs

Runtime launch is gated by preflight. `POST /api/configs/{config_id}/preflight` feeds the run-start path, and `POST /api/runs` proceeds only when preflight reports no blocking errors.

Compatibility findings such as skipped default `create_airspace` or `create_frequency` injection remain visible as warnings.

### 6. Registry and Proxy Compilation

Custom `agent`, `task`, and `workflow` definitions are declarative and file-backed. The backend loads definitions from `registry/aeroagentsim/`, validates them, and compiles them into proxy or adapter objects that execute through the existing runtime primitives.

### 7. Core Simulation Model

The backend execution model is built on:

* `Environment`
* `Agent`
* `Component`
* `Task`
* `Workflow`
* `Trigger`
* resource and service managers

These primitives remain the execution source of truth. The AeroAgentSim workbench adds catalog extraction, configuration adaptation, runtime observability, and frontend inspection tools around them.
