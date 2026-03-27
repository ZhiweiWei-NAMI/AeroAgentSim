User Guide
==========

This guide covers the current AeroAgentSim usage model. The underlying technical package remains ``airfogsim``.

Core Patterns
-------------

Agent-Component-Task Workflow
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Agents own components, components execute tasks, and workflows coordinate higher-level behavior.

.. code-block:: python

   from airfogsim import Environment
   from airfogsim.agent import DroneAgent
   from airfogsim.component import ChargingComponent, MoveToComponent

   env = Environment()
   drone = env.create_agent(
       DroneAgent,
       "drone1",
       properties={
           "position": [0, 0, 20],
           "battery_level": 100,
       },
   )
   drone.add_component(MoveToComponent(env, drone))
   drone.add_component(ChargingComponent(env, drone))

Configuration and Runs
~~~~~~~~~~~~~~~~~~~~~~

The workbench uses two different persistence concepts:

* config snapshots
* runs identified by ``run_id``

Config snapshots are immutable and stored under ``runtime/aeroagentsim/configs/``. Run artifacts are stored under ``runtime/aeroagentsim/runs/<run_id>/``.

Custom definitions are stored separately under ``registry/aeroagentsim/agents/``, ``registry/aeroagentsim/tasks/``, and ``registry/aeroagentsim/workflows/``. These files are the source of truth for custom registry content.

2D Workbench
------------

The current frontend is a developer workbench rather than a 3D monitor.

Workflow Studio
~~~~~~~~~~~~~~~

Use Workflow Studio to:

* edit agent definitions in table form
* edit workflow definitions in table and JSON form
* select builtin or custom definitions from a merged catalog
* run page-local ``Validate`` on the current draft
* use global ``Review / Validate`` for aggregated checks
* inspect workflow-agent-state coupling through the relation graph
* zoom and drag the graph viewport inside the graph canvas without changing the underlying config

Workflow Studio validation is draft-oriented. It should evaluate the current working form state instead of only the last saved snapshot.

Class Catalog
~~~~~~~~~~~~~

Use Class Catalog to:

* inspect builtin and custom definitions together
* review definition source and version
* create or update declarative custom definitions
* check definition-level validation before using them in configs

Run Console
~~~~~~~~~~~

Run Console is the operational view for:

* starting, pausing, resuming, and resetting runs
* observing critical path markers
* following live log events
* viewing the live 2D spatial snapshot

Run start is gated by runtime preflight. Warnings can still allow launch, but preflight errors block ``POST /api/runs``.

The UI supports ``zh-CN`` and ``en-US`` switching at the global header level. Raw runtime log content is not automatically translated.

Trajectories and Logs
~~~~~~~~~~~~~~~~~~~~~

Historical inspection is organized by ``run_id`` and focuses on:

* trajectory polylines
* log filtering
* run-specific spatial snapshots
* refresh-by-run inspection after reset or stop

API Interaction Model
---------------------

The current workbench uses:

* REST for control and configuration requests
* WebSocket for ``sim_status``, ``workflow_state_diff``, ``spatial_snapshot``, and ``log_event``

Registry CRUD and validation are also handled through REST so that custom definitions can be created, checked, versioned, and referenced without exposing executable code upload paths.

Active-run control is intentionally narrow: ``/api/runs/{run_id}/pause|resume|reset`` applies only to the active run and returns ``409`` for historical runs.

There is no ``Dashboard`` class in the current usage model, and the old 3D page workflow is no longer part of the frontend.
