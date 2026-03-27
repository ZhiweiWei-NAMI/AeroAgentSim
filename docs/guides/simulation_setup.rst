Simulation Setup Guide
======================

This guide describes the current setup model in this repository.

Use ``AeroAgentSim`` when referring to the product or frontend workbench. Use
``airfogsim`` for the Python package, imports, and source modules.

Current Setup Model
-------------------

There are two supported setup paths:

* direct Python simulation setup through ``airfogsim``
* workbench-driven setup through saved config snapshots and runs

The older ``Dashboard``-style visualization flow is no longer part of the
current repository behavior.

Python Environment Setup
------------------------

For local development, the expected baseline is:

.. code-block:: bash

   conda activate airfogsim
   pip install -e .[dev]

For browser-level workbench checks:

.. code-block:: bash

   pip install playwright
   python -m playwright install chromium

Direct Python Setup
-------------------

The current ``Environment`` constructor does not depend on the older
``Environment(config=...)`` pattern documented in legacy guides. Create the
environment directly and pass runtime state through agent properties.

.. code-block:: python

   from airfogsim import Environment
   from airfogsim.agent import DroneAgent
   from airfogsim.component.mobility import MoveToComponent
   from airfogsim.component.charging import ChargingComponent

   env = Environment()

   drone = env.create_agent(
       DroneAgent,
       "drone1",
       properties={
           "position": [0, 0, 20],
           "battery_level": 100,
           "status": "idle",
       },
   )

   drone.add_component(MoveToComponent(env, drone))
   drone.add_component(ChargingComponent(env, drone))

   env.run(until=100)

Key points:

* use ``properties`` for initial position and battery state
* do not rely on ``initial_position`` or ``initial_battery`` kwargs for the
  current ``DroneAgent`` constructor
* attach components after agent creation unless you are using a custom helper
  that builds component configs for you

Workbench Setup
---------------

The workbench starts both backend and frontend:

.. code-block:: bash

   python main_for_visualization.py --backend-port 8002 --frontend-port 3000

The current workbench is 2D only and organized into five pages:

* ``Overview``
* ``Class Catalog``
* ``Workflow Studio``
* ``Run Console``
* ``Trajectories & Logs``

Configuration Flow
------------------

The current workbench does not treat the database as the primary editable
configuration source.

Instead:

* config saves create immutable snapshots under
  ``runtime/aeroagentsim/configs/``
* custom registry definitions live under ``registry/aeroagentsim/``
* each launched run gets its own ``run_id`` and runtime directory under
  ``runtime/aeroagentsim/runs/<run_id>/``

Run directories typically contain:

* ``logs/``
* ``workflow_states/``
* ``trajectories/``
* ``spatial/``
* ``metrics/``

Run Control and Monitoring
--------------------------

The current control plane is split by transport:

* REST handles save, validate, preflight, start, pause, resume, reset, and
  delete operations
* WebSocket pushes ``sim_status``, ``workflow_state_diff``,
  ``spatial_snapshot``, and ``log_event``

Run start is gated by preflight. Warnings can still be reported without
blocking startup, but preflight errors prevent ``POST /api/runs`` from
launching the run.

The active runtime can be reset globally. Historical runs can be inspected and
deleted from the frontend once they are no longer active. Per-run
``pause``/``resume``/``reset`` controls return ``409`` for non-active runs.

Workflow Studio
---------------

``Workflow Studio`` is the primary configuration page for workflow editing.

It supports:

* table/form editing for agent and workflow instances
* builtin and custom definition selection
* ``Review / Validate`` checks
* a workflow-agent-state relation graph for inspection

The graph supports zoom and drag-to-pan navigation. It is not a drag-to-edit
canvas; the source of truth remains the form and table data. Wheel and
trackpad zoom apply inside the graph canvas rather than the outer page.

Trajectories and Logs
---------------------

Historical runs are reviewed by ``run_id``.

The ``Trajectories & Logs`` page shows:

* stored run status
* 2D trajectory replay
* filtered logs
* marker details derived from stored trajectory points

After a run is stopped or reset, the trajectory page reads persisted artifacts
from the corresponding run directory rather than relying on the active runtime.

What To Avoid
-------------

Do not rely on these older patterns:

* ``from airfogsim.visualization import Dashboard``
* ``Environment(config=...)`` as the primary documented setup path
* ``env.config`` as a universal runtime configuration store
* ``env.register_agents(...)`` in examples for the current public workflow
* references to a built-in 3D dashboard page

Reference Points
----------------

Use these files for the current behavior:

* ``README.md``
* ``INSTALL.md``
* ``docs/getting_started.rst``
* ``docs/user_guide.rst``
* ``src/airfogsim/docs/en/architecture.md``
