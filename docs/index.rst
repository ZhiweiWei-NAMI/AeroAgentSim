AirFogSim Documentation
=======================

Welcome to AirFogSim's documentation! AirFogSim is a comprehensive low altitude space simulation system designed for modeling and analyzing fog computing scenarios with autonomous agents.

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   getting_started
   user_guide
   api/index
   examples
   guides/index
   contributing

Overview
--------

AirFogSim provides a flexible framework for simulating autonomous agents (such as drones, ground stations) in fog computing environments. The system is built on discrete event simulation principles using SimPy and offers:

* **Agent-based modeling**: Autonomous decision-making entities with customizable behaviors
* **Component-based architecture**: Modular capabilities like mobility, sensing, computation
* **Workflow management**: High-level goal coordination and task orchestration  
* **Event-driven communication**: Decoupled interaction through publish-subscribe patterns
* **Resource management**: Dynamic allocation and contention modeling
* **Data integration**: External data sources for realistic simulation scenarios

Key Features
------------

* **Extensible Architecture**: Easy to add new agent types, components, and workflows
* **Real-time Integration**: Support for real-world data sources and APIs
* **Visualization**: Built-in dashboard and monitoring capabilities
* **Benchmarking**: Comprehensive performance analysis and metrics collection
* **Multi-language Support**: Documentation and examples in English and Chinese

Quick Start
-----------

Install AirFogSim:

.. code-block:: bash

   pip install airfogsim

Basic usage example:

.. code-block:: python

   from airfogsim.core.environment import Environment
   from airfogsim.agent import DroneAgent
   from airfogsim.component import MoveToComponent, ChargingComponent
   
   # Create simulation environment
   env = Environment()
   
   # Create a drone agent
   drone = env.create_agent(
       DroneAgent,
       "drone1", 
       initial_position=(10, 10, 0),
       initial_battery=100
   )
   
   # Add components
   drone.add_component(MoveToComponent(env, drone))
   drone.add_component(ChargingComponent(env, drone))
   
   # Run simulation
   env.run(until=1000)

Architecture Overview
--------------------

AirFogSim follows a layered architecture:

* **Core Layer**: Base classes for Agent, Component, Task, Workflow, DataProvider
* **Agent Layer**: Specific agent implementations (Drone, Terminal, etc.)
* **Component Layer**: Functional capabilities (Mobility, Sensing, Communication)
* **Workflow Layer**: High-level process coordination
* **Manager Layer**: Resource and service management
* **Integration Layer**: External data sources and real-world interfaces

For detailed architecture information, see the :doc:`guides/architecture` guide.

API Reference
-------------

The complete API documentation is available in the :doc:`api/index` section, covering:

* :doc:`api/core` - Core framework classes
* :doc:`api/agent` - Agent implementations  
* :doc:`api/component` - Component capabilities
* :doc:`api/task` - Task execution framework
* :doc:`api/workflow` - Workflow coordination
* :doc:`api/dataprovider` - Data integration

Examples and Tutorials
----------------------

Comprehensive examples are available in the :doc:`examples` section, including:

* Basic agent creation and simulation
* Component integration and task execution
* Workflow design and state machines
* Data provider integration
* Benchmarking and performance analysis

Developer Guides
----------------

For developers looking to extend AirFogSim:

* :doc:`guides/agent_development` - Creating custom agents
* :doc:`guides/component_development` - Building new components
* :doc:`guides/workflow_development` - Designing workflows
* :doc:`guides/dataprovider_development` - Integrating data sources

License
-------

AirFogSim is released under the Apache 2.0. See the LICENSE file for details.

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
