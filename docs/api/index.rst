API Reference
=============

This section provides comprehensive API documentation for all AirFogSim classes and functions.

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
   visualization
   utils

Overview
--------

The AirFogSim API is organized into several key modules:

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
~~~~~~~~~~~~~~~~~~~~~~~

Supporting functionality and tools:

* :doc:`statistics` - Data collection and analysis
* :doc:`visualization` - Dashboard and monitoring
* :doc:`utils` - Utility functions and helpers

Quick Reference
---------------

Core Classes
~~~~~~~~~~~~

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   airfogsim.core.agent.Agent
   airfogsim.core.component.Component
   airfogsim.core.workflow.Workflow
   airfogsim.core.dataprovider.DataProvider
   airfogsim.core.environment.Environment
   airfogsim.core.task.Task

Agent Types
~~~~~~~~~~~

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   airfogsim.agent.drone.DroneAgent
   airfogsim.agent.terminal.TerminalAgent
   airfogsim.agent.delivery_drone.DeliveryDroneAgent
   airfogsim.agent.sensing_agent.SensingAgent

Component Types
~~~~~~~~~~~~~~~

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   airfogsim.component.mobility.MoveToComponent
   airfogsim.component.charging.ChargingComponent
   airfogsim.component.communication.CommunicationComponent
   airfogsim.component.computation.ComputationComponent
   airfogsim.component.img_sensor.ImageSensorComponent
   airfogsim.component.em_sensor.EMSensingComponent
   airfogsim.component.object_sensor.ObjectSensorComponent

Workflow Types
~~~~~~~~~~~~~~

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   airfogsim.workflow.inspection.InspectionWorkflow
   airfogsim.workflow.logistics.LogisticsWorkflow
   airfogsim.workflow.charging.ChargingWorkflow
   airfogsim.workflow.image_processing.ImageProcessingWorkflow

Data Providers
~~~~~~~~~~~~~~

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   airfogsim.dataprovider.weather.WeatherDataProvider
   airfogsim.dataprovider.traffic.TrafficDataProvider
   airfogsim.dataprovider.signal.SignalDataProvider

Managers
~~~~~~~~

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   airfogsim.manager.agent.AgentManager
   airfogsim.manager.task.TaskManager
   airfogsim.manager.workflow.WorkflowManager
   airfogsim.manager.frequency.FrequencyManager
   airfogsim.manager.landing.LandingManager

Usage Patterns
--------------

Common API usage patterns and examples:

Creating Agents
~~~~~~~~~~~~~~~

.. code-block:: python

   from airfogsim.core.environment import Environment
   from airfogsim.agent import DroneAgent
   
   env = Environment()
   drone = env.create_agent(DroneAgent, "drone1", 
                           initial_position=(0, 0, 100))

Adding Components
~~~~~~~~~~~~~~~~~

.. code-block:: python

   from airfogsim.component import MoveToComponent, ChargingComponent
   
   drone.add_component(MoveToComponent(env, drone))
   drone.add_component(ChargingComponent(env, drone))

Executing Tasks
~~~~~~~~~~~~~~~

.. code-block:: python

   # Execute a movement task
   task = drone.execute_task("MoveToTask", 
                            target_position=(100, 100, 50))

Creating Workflows
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from airfogsim.workflow.inspection import create_inspection_workflow
   
   workflow = create_inspection_workflow(
       env, "inspection1", drone,
       waypoints=[(0, 0, 100), (50, 50, 100), (100, 100, 100)]
   )

For more detailed examples, see the :doc:`../examples` section.
