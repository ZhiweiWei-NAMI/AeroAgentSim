API Reference
=============

This section provides comprehensive API documentation for the current
``aeroagentsim`` package. The AeroAgentSim workbench is built on top of these
runtime primitives.

.. toctree::
   :maxdepth: 2
   :caption: API Documentation:

   core
   agent
   component
   workflow
   dataprovider
   manager
   task
   resource
   statistics
   utils

Overview
--------

The AeroAgentSim API is organized into several key modules:

Core Framework
~~~~~~~~~~~~~~

The core framework provides the fundamental building blocks:

* :doc:`core` - Base classes and core functionality
* :doc:`manager` - Resource and service management
* :doc:`task` - Task execution framework
* :doc:`resource` - Resource modeling and allocation

Agent System
~~~~~~~~~~~~

The agent system implements autonomous entities:

* :doc:`agent` - Agent implementations and base classes
* :doc:`component` - Component capabilities and interfaces

Workflow and Coordination
~~~~~~~~~~~~~~~~~~~~~~~~~

High-level coordination and process management:

* :doc:`workflow` - Workflow state machines and coordination
* :doc:`dataprovider` - External data integration

Utilities and Extensions
~~~~~~~~~~~~~~~~~~~~~~~~

Supporting functionality and tools:

* :doc:`statistics` - Data collection and analysis
* The AeroAgentSim workbench is documented in the top-level guides and uses the ``aeroagentsim.visualization`` FastAPI app
* :doc:`utils` - Utility functions and helpers

Quick Reference
---------------

Core Classes
~~~~~~~~~~~~

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   aeroagentsim.core.agent.Agent
   aeroagentsim.core.component.Component
   aeroagentsim.core.workflow.Workflow
   aeroagentsim.core.dataprovider.DataProvider
   aeroagentsim.core.environment.Environment
   aeroagentsim.core.task.Task

Agent Types
~~~~~~~~~~~

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   aeroagentsim.agent.drone.DroneAgent
   aeroagentsim.agent.terminal.TerminalAgent
   aeroagentsim.agent.delivery_drone.DeliveryDroneAgent
   aeroagentsim.agent.sensing_agent.SensingAgent

Component Types
~~~~~~~~~~~~~~~

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   aeroagentsim.component.mobility.MoveToComponent
   aeroagentsim.component.charging.ChargingComponent
   aeroagentsim.component.communication.CommunicationComponent
   aeroagentsim.component.computation.ComputationComponent
   aeroagentsim.component.img_sensor.ImageSensingComponent
   aeroagentsim.component.em_sensor.EMSensingComponent
   aeroagentsim.component.object_sensor.ObjectSensorComponent

Workflow Types
~~~~~~~~~~~~~~

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   aeroagentsim.workflow.inspection.InspectionWorkflow
   aeroagentsim.workflow.logistics.LogisticsWorkflow
   aeroagentsim.workflow.charging.ChargingWorkflow
   aeroagentsim.workflow.image_processing.ImageProcessingWorkflow

Data Providers
~~~~~~~~~~~~~~

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   aeroagentsim.dataprovider.weather.WeatherDataProvider
   aeroagentsim.dataprovider.traffic.TrafficDataProvider
   aeroagentsim.dataprovider.signal.SignalDataProvider

Managers
~~~~~~~~

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   aeroagentsim.manager.agent.AgentManager
   aeroagentsim.manager.task.TaskManager
   aeroagentsim.manager.workflow.WorkflowManager
   aeroagentsim.manager.frequency.FrequencyManager
   aeroagentsim.manager.landing.LandingManager

Usage Patterns
--------------

Common API usage patterns and examples:

Creating Agents
~~~~~~~~~~~~~~~

.. code-block:: python

   from aeroagentsim.core.environment import Environment
   from aeroagentsim.agent import DroneAgent
   
   env = Environment()
   drone = env.create_agent(
       DroneAgent,
       "drone1",
       properties={"position": [0, 0, 100], "battery_level": 100},
   )

Adding Components
~~~~~~~~~~~~~~~~~

.. code-block:: python

   from aeroagentsim.component import MoveToComponent, ChargingComponent
   
   drone.add_component(MoveToComponent(env, drone))
   drone.add_component(ChargingComponent(env, drone))

Executing Tasks
~~~~~~~~~~~~~~~

.. code-block:: python

   # Execute a movement task
   task = drone.execute_task(
       "MoveTo",
       "Move to waypoint",
       "MoveToTask",
       target_state={"position": (100, 100, 50)},
       properties={"target_position": (100, 100, 50)},
   )

Creating Workflows
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from aeroagentsim.workflow.inspection import create_inspection_workflow
   
   workflow = create_inspection_workflow(
       env,
       drone,
       [(0, 0, 100), (50, 50, 100), (100, 100, 100)],
   )

For more detailed examples, see the :doc:`../examples` section.
