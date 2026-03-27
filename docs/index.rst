AeroAgentSim Documentation
==========================

`AeroAgentSim` is the external product name for the current simulation workbench. The Python package name and import path remain ``airfogsim``.

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

AeroAgentSim combines the existing ``airfogsim`` simulation core with a developer-oriented 2D workbench for:

* catalog exploration
* builtin and custom definition management
* workflow-agent-state coupling inspection
* config snapshot editing
* draft review and consistency validation
* run control
* live 2D spatial monitoring
* trajectory and log review by ``run_id``

.. image:: images/workflow-studio-relation-graph.png
   :alt: Workflow Studio relation graph with zoom and pan controls
   :width: 100%

Quick Start
-----------

.. code-block:: bash

   conda activate airfogsim
   pip install airfogsim
   python -c "import airfogsim; from airfogsim import Environment, AirFogSimEnv; print('ok')"

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
* Run start performs runtime preflight; warnings can be non-blocking, errors block launch
* The workbench uses REST for control and WebSocket for live updates
* Custom ``agent`` / ``task`` / ``workflow`` definitions are file-backed under ``registry/aeroagentsim/``

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
