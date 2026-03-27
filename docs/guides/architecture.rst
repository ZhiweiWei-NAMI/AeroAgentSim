Architecture Guide
==================

This guide describes the current repository architecture.

``AeroAgentSim`` is the current product/workbench name. ``airfogsim`` remains
the Python package name and the technical execution namespace.

High-Level Structure
--------------------

The repository has three practical layers:

* the ``airfogsim`` simulation core
* the AeroAgentSim workbench backend under ``src/airfogsim/visualization/``
* the React frontend under ``frontend/``

The old ``Dashboard`` wording in legacy docs should now be read as historical
only. The current frontend is a REST + WebSocket driven 2D workbench.

Core Simulation Layer
---------------------

The execution model is still built from:

* ``Environment``
* ``Agent``
* ``Component``
* ``Task``
* ``Workflow``
* ``Trigger``
* manager classes for resources and coordination

These classes remain the runtime source of truth.

Workbench Backend Layer
-----------------------

The workbench backend adds:

* catalog extraction for builtin and custom definitions
* config snapshot storage and validation
* graph generation for workflow-agent-state relationships
* runtime preflight checks
* run lifecycle control
* artifact persistence for logs, trajectories, spatial snapshots, and workflow
  state diffs

The backend is exposed through a FastAPI app.

Current API groups include:

* ``/api/catalog/*``
* ``/api/registry/*``
* ``/api/configs/*``
* ``/api/runs/*``
* ``/api/agents/*``
* ``/api/workflows/*``
* ``/api/templates/*``
* ``/api/traffic/*``
* ``/api`` entity endpoints
* ``/api/runtime/reset``
* ``/api/health``
* ``/ws``

Frontend Layer
--------------

The frontend is a five-page developer workbench:

* ``Overview``
* ``Class Catalog``
* ``Workflow Studio``
* ``Run Console``
* ``Trajectories & Logs``

It is intentionally 2D only.

Key frontend behaviors:

* form/table editing is the source of truth
* the relation graph is for inspection, highlighting, zoom, and pan inside the graph canvas
* ``Run Console`` handles active runtime control
* ``Trajectories & Logs`` handles persisted historical run inspection
* the UI supports ``zh-CN`` and ``en-US``

Control and Update Channels
---------------------------

The current split is:

* REST for commands and config requests
* WebSocket for state and log pushes

REST is authoritative for:

* save
* validate
* preflight
* start
* pause
* resume
* reset
* delete run

Runtime preflight is the run-start gate. Warnings remain visible in diagnostics, but only preflight errors block ``POST /api/runs``.

WebSocket is used for:

* ``sim_status``
* ``workflow_state_diff``
* ``spatial_snapshot``
* ``log_event``

Configuration and Runtime Storage
---------------------------------

The current persistence model is layered:

* config snapshots under ``runtime/aeroagentsim/configs/``
* run artifacts under ``runtime/aeroagentsim/runs/<run_id>/``
* custom definition files under ``registry/aeroagentsim/``

SQLite is used as a lightweight index/cache layer rather than the primary
historical store.

Visualization Model
-------------------

The repository no longer ships a 3D page set.

The current visualization model includes:

* live 2D markers in ``Run Console``
* historical 2D trajectory replay in ``Trajectories & Logs``
* a workflow-agent-state relation graph in ``Workflow Studio``

Supported coordinate modes:

* ``simulation_plane``
* ``geo_osm``

Extension Model
---------------

Custom ``agent``, ``task``, and ``workflow`` definitions are file-backed and
declarative. They are loaded from ``registry/aeroagentsim/``, validated, and
compiled into backend proxy/adaptor objects that still run through the
``airfogsim`` execution model.

This means:

* the frontend can extend definitions without uploading Python code
* runtime execution still stays inside controlled backend abstractions

What Changed From Legacy Docs
-----------------------------

The current architecture differs from older docs in these ways:

* there is no exported ``Dashboard`` class in ``airfogsim.visualization``
* the integration layer is no longer a dashboard embedding model
* the frontend is no longer a 3D monitoring UI
* registry files and config snapshots are now first-class architectural parts
* historical run inspection is organized by ``run_id``
* legacy ``/api/simulation/*`` endpoints are intentionally disabled with ``410``

Related Docs
------------

* ``README.md``
* ``INSTALL.md``
* ``docs/getting_started.rst``
* ``docs/user_guide.rst``
* ``src/airfogsim/docs/en/architecture.md``
