AeroAgentSim Documentation
==========================

AeroAgentSim combines a discrete-event simulation core with a developer workbench for configuration editing, workflow inspection, runtime validation, and execution analysis.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   getting_started
   user_guide
   api/index
   examples
   guides/index
   contributing

Overview
--------

AeroAgentSim is organized around these developer workflows:

* define and inspect agents, components, tasks, and workflows
* build configs from builtin and custom definitions
* validate drafts and run preflight checks
* launch runs and observe structured logs plus live spatial snapshots
* replay trajectories and inspect stored run artifacts

.. image:: images/workflow-studio-relation-graph.png
   :alt: Workflow Studio relation graph with auto layout and interactive canvas controls
   :width: 100%

Quick Start
-----------

.. code-block:: bash

   python -m venv aeroagentsim_env
   source aeroagentsim_env/bin/activate
   pip install -e .[dev]
   python -c "import aeroagentsim, airfogsim; from aeroagentsim import Environment; from airfogsim import Environment as LegacyEnvironment; print(Environment.__name__, Environment is LegacyEnvironment)"

Key Docs
--------

* :doc:`getting_started`
* :doc:`user_guide`
* :doc:`api/index`
* :doc:`examples`

Runtime Model
-------------

* Config snapshots are stored under ``runtime/aeroagentsim/configs/``
* Each launched run receives its own ``run_id``
* Run artifacts are stored under ``runtime/aeroagentsim/runs/<run_id>/``
* Run start performs runtime preflight; ``warning`` entries remain visible and ``error`` entries block launch
* The workbench uses REST for control and WebSocket for live updates
* Custom ``agent`` / ``task`` / ``workflow`` definitions are file-backed under ``registry/aeroagentsim/``

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
