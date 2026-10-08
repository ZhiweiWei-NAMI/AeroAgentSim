AeroAgentSim Documentation
==========================

AeroAgentSim is an ontology-driven, multi-engine simulation platform built on
an independent aerokernel. New applications use ``aeroagentsim.Simulation``.
The retained SimPy runtime is deprecated and requires the ``legacy`` extra.

.. toctree::
   :maxdepth: 2
   :caption: Kernel platform

   platform/CAPABILITIES
   platform/MIGRATION-v1
   platform/PLAN
   platform/p1
   platform/aerograph-compiler
   platform/adapters
   platform/px4-backend
   platform/sumo-backend
   platform/ns3-backend
   platform/packs
   platform/agents
   platform/frontend
   platform/studio

.. toctree::
   :maxdepth: 1
   :caption: Historical v1 references (legacy extra)

   getting_started
   user_guide
   api/index
   examples
   guides/index
   contributing

Install and run
---------------

Use Python 3.10+ and compatible sibling kernel source. The P1 scenario also
requires its pinned AeroGraph source; consult INSTALL.md for alternate layouts
and a portable snapshot-based pack example.

.. code-block:: bash

   pip install -e ../aerokernel -e '.[server]'
   aeroagentsim run scenarios/p1-slice.yaml --out runs
   aeroagentsim replay runs/<printed-directory>
   aeroagentsim serve --help

Each run pins its scenario and registry, records an append-only kernel journal,
and exposes a committed viewer feed. Replay executes no engines or model calls.
The capability checklist distinguishes verified results and pending migration
work; source presence does not establish that a native integration is complete.

Indices
-------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
