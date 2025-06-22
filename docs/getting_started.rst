Getting Started
===============

This guide will help you get up and running with AirFogSim quickly.

Installation
------------

Install from PyPI
~~~~~~~~~~~~~~~~~

The easiest way to install AirFogSim is using pip:

.. code-block:: bash

   pip install airfogsim

Install from Source
~~~~~~~~~~~~~~~~~~~

For development or to get the latest features:

.. code-block:: bash

   git clone https://github.com/ZhiweiWei-NAMI/AirFogSim.git
   cd airfogsim
   pip install -e .

Development Installation
~~~~~~~~~~~~~~~~~~~~~~~~

For development with all dependencies:

.. code-block:: bash

   git clone https://github.com/ZhiweiWei-NAMI/AirFogSim.git
   cd airfogsim
   pip install -e ".[dev,docs]"

Verify Installation
~~~~~~~~~~~~~~~~~~~

Test your installation:

.. code-block:: python

   import airfogsim
   print(airfogsim.__version__)

Basic Concepts
--------------

Before diving into examples, let's understand the key concepts:

Environment
~~~~~~~~~~~

The simulation environment manages time, events, and resources:

.. code-block:: python

   from airfogsim.core.environment import Environment
   
   env = Environment()
   # Run simulation for 1000 time units
   env.run(until=1000)

Agents
~~~~~~

Agents are autonomous entities that make decisions and perform actions:

.. code-block:: python

   from airfogsim.agent import DroneAgent
   
   drone = env.create_agent(DroneAgent, "drone1", 
                           initial_position=(0, 0, 100))

Components
~~~~~~~~~~

Components provide capabilities to agents:

.. code-block:: python

   from airfogsim.component import MoveToComponent, ChargingComponent
   
   drone.add_component(MoveToComponent(env, drone))
   drone.add_component(ChargingComponent(env, drone))

Tasks
~~~~~

Tasks are specific actions that agents can perform:

.. code-block:: python

   # Execute a movement task
   task = drone.execute_task("MoveToTask", 
                            target_position=(100, 100, 50))

Workflows
~~~~~~~~~

Workflows coordinate high-level processes:

.. code-block:: python

   from airfogsim.workflow.inspection import InspectionWorkflow
   from airfogsim.core.enums import TaskPriority
   from airfogsim.core.trigger import TimeTrigger
   
   inspection_points = [(0, 0, 100), (50, 50, 100), (100, 100, 100)]
   task_priority = TaskPriority.NORMAL
   task_preemptive = False

   workflow = env.create_workflow(
      InspectionWorkflow,
      name=f"Inspection of {drone.id}",
      owner=drone,
      properties={
         'inspection_points': inspection_points,
         'task_priority': task_priority,
         'task_preemptive': task_preemptive
      },
      start_trigger=TimeTrigger(env, interval=100),
      max_starts=1
   )

Your First Simulation
---------------------

Let's create a simple simulation with a drone that moves between waypoints:

.. code-block:: python

   from airfogsim.core.environment import Environment
   from airfogsim.agent import DroneAgent
   from airfogsim.component import MoveToComponent, ChargingComponent
   from airfogsim.workflow.inspection import InspectionWorkflow
   from airfogsim.core.enums import TaskPriority
   from airfogsim.core.trigger import TimeTrigger
   
   # Create environment
   env = Environment()
   
   # Create drone agent
   drone = env.create_agent(
       DroneAgent,
       "drone1",
       initial_position=(0, 0, 100),
       initial_battery=100
   )
   
   # Add components
   drone.add_component(MoveToComponent(env, drone))
   drone.add_component(ChargingComponent(env, drone))
   
   # Define waypoints
   waypoints = [(50, 50, 100), (100, 0, 100), (0, 0, 100)]

   # Create inspection workflow
   workflow = env.create_workflow(
       InspectionWorkflow,
       name=f"Inspection of {drone.id}",
       owner=drone,
       properties={
           'inspection_points': waypoints,
           'task_priority': TaskPriority.NORMAL,
           'task_preemptive': False
       },
       start_trigger=TimeTrigger(env, interval=100),
       max_starts=1
   )
   
   # Run simulation
   env.run(until=500)
   
   # Check final position
   print(f"Final position: {drone.get_state('position')}")
   print(f"Final battery: {drone.get_state('battery_level')}")

Next Steps
----------

Now that you have a basic understanding, explore these topics:

1. **Agent Development**: Learn to create custom agent types
2. **Component System**: Build new capabilities for your agents  
3. **Workflow Design**: Coordinate complex multi-step processes
4. **Data Integration**: Connect real-world data sources
5. **Visualization**: Monitor simulations with the built-in dashboard

See the :doc:`user_guide` for detailed tutorials and the :doc:`api/index` for complete API reference.
