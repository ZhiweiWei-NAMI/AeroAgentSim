Getting Started
===============

This guide gets you running with the current AeroAgentSim repository quickly.

Install
-------

.. code-block:: bash

   python -m venv aeroagentsim_env
   source aeroagentsim_env/bin/activate
   pip install -e .[dev]

Verify imports:

.. code-block:: bash

   python -c "import aeroagentsim, airfogsim; from aeroagentsim import Environment; from airfogsim import Environment as LegacyEnvironment; print(Environment.__name__, Environment is LegacyEnvironment)"

First Simulation
----------------

.. code-block:: python

   from aeroagentsim import Environment
   from aeroagentsim.agent import DroneAgent
   from aeroagentsim.component import MoveToComponent

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
   env.run(until=100)

Start the Workbench
-------------------

.. code-block:: bash

   python main_for_visualization.py --backend-port 8002 --frontend-port 3000

The current workbench provides:

* ``Overview``
* ``Class Catalog``
* ``Workflow Studio``
* ``Run Console``
* ``Trajectories & Logs``

``Workflow Studio`` combines form-based editing with an interactive relation graph. Zoom stays inside the graph canvas, blank-canvas dragging pans the viewport, and node dragging refines the local layout for inspection.

.. image:: images/workflow-studio-relation-graph.png
   :alt: Workflow Studio relation graph
   :width: 100%

Runtime Layout
--------------

Config saves create immutable snapshots in ``runtime/aeroagentsim/configs/``.

Each simulation launch creates a unique ``run_id`` under ``runtime/aeroagentsim/runs/`` with logs, workflow states, trajectories, spatial snapshots, and metrics stored separately.

Browser Validation
------------------

For browser-level frontend testing, install Python Playwright in the active environment and then install Chromium:

.. code-block:: bash

   pip install playwright
   python -m playwright install chromium
