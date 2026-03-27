Getting Started
===============

This guide gets you running with AeroAgentSim quickly. The product name is `AeroAgentSim`, but the Python package name remains ``airfogsim``.

Install
-------

.. code-block:: bash

   conda activate airfogsim
   pip install airfogsim

Verify imports:

.. code-block:: bash

   python -c "import airfogsim; from airfogsim import Environment, AirFogSimEnv; print('ok')"

Use ``Environment`` for new code. ``AirFogSimEnv`` is available as a compatibility alias.

First Simulation
----------------

.. code-block:: python

   from airfogsim import Environment
   from airfogsim.agent import DroneAgent
   from airfogsim.component import MoveToComponent

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

The current workbench is 2D only. It provides:

* Overview
* Class Catalog
* Workflow Studio
* Run Console
* Trajectories & Logs

It also supports:

* global ``zh-CN`` / ``en-US`` language switching
* a global ``Review / Validate`` flow
* builtin and custom definition catalogs
* relation graph zoom and drag-to-pan inspection inside the graph canvas

.. image:: images/workflow-studio-relation-graph.png
   :alt: Workflow Studio relation graph
   :width: 100%

Custom definitions are file-backed under ``registry/aeroagentsim/`` and currently cover ``agent``, ``task``, and ``workflow`` definitions.

``Workflow Studio`` ``Validate`` checks the current draft. Run start performs runtime preflight; warnings remain visible but only preflight errors block the run.

Runtime Layout
--------------

Config saves create immutable snapshots in ``runtime/aeroagentsim/configs/``.

Each simulation launch creates a unique ``run_id`` under ``runtime/aeroagentsim/runs/`` with logs, workflow states, trajectories, spatial snapshots, and metrics stored separately.

For browser-level frontend testing, install Python Playwright in the active environment and then install Chromium:

.. code-block:: bash

   pip install playwright
   python -m playwright install chromium
