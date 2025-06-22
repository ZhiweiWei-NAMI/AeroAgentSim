Examples
========

This section provides comprehensive examples demonstrating various AirFogSim capabilities.

Understanding AirFogSim's Execution Model
------------------------------------------

AirFogSim uses an **event-driven, workflow-based execution model** where:

1. **Agents don't execute tasks directly** - Instead, they have internal task schedulers that automatically process task queues
2. **Workflows generate tasks automatically** - Workflows use state machines to monitor agent states and suggest appropriate tasks
3. **Tasks are managed by components** - Each component handles specific types of tasks (movement, sensing, charging, etc.)
4. **The system runs autonomously** - Once workflows are started, the simulation runs automatically without manual intervention

**Key Concepts:**

- **Agent.live()**: The main agent loop that handles event listening and task processing
- **Workflow.start()**: Activates a workflow's state machine to begin automatic task generation
- **Component.execute_task()**: Handles the actual execution of tasks assigned by workflows
- **Triggers**: Monitor conditions and drive state transitions in workflows

This means you typically don't call ``agent.execute_task()`` directly. Instead, you create workflows that automatically manage task execution based on the agent's current state and goals.

**Workflow Startup Mechanisms:**

AirFogSim provides two ways to start workflows:

1. **Trigger-based automatic startup** (Recommended):
   - Use ``env.create_workflow()`` with a ``start_trigger`` parameter
   - Workflows start automatically when trigger conditions are met
   - Supports various trigger types: TimeTrigger, StateTrigger, CompositeTrigger
   - Can specify ``max_starts`` for repeated execution

2. **Manual startup**:
   - Create workflow instance directly, then call ``workflow.start()``
   - Useful for immediate execution or custom control logic
   - Requires manual workflow registration with ``env.workflow_manager.register_workflow()``

Basic Examples
--------------

Simple Drone Workflow
~~~~~~~~~~~~~~~~~~~~~

A basic example showing how workflows automatically manage drone tasks:

.. code-block:: python

   from airfogsim.core.environment import Environment
   from airfogsim.agent.drone import DroneAgent
   from airfogsim.component.mobility import MoveToComponent

   # Create environment
   env = Environment()

   # Create drone agent
   drone = DroneAgent(env, agent_name="drone1", properties={
       'position': (0, 0, 100),
       'battery_level': 100
   })

   # Add movement component
   drone.add_component(MoveToComponent(env, drone))

   # Register agent with environment
   env.register_agent(drone)

   # Define waypoints for inspection workflow
   waypoints = [(50, 50, 100), (100, 0, 100), (0, 0, 100)]

   # Method 1: Create workflow with trigger-based automatic startup
   from airfogsim.workflow.inspection import InspectionWorkflow
   from airfogsim.core.trigger import TimeTrigger

   workflow = env.create_workflow(
       InspectionWorkflow,
       name="waypoint_inspection",
       owner=drone,
       properties={'inspection_points': waypoints},
       start_trigger=TimeTrigger(env, trigger_time=10),  # Start at time 10
       max_starts=1
   )

   # The workflow will start automatically when the trigger fires
   # No need to call workflow.start() manually

   env.run(until=1000)

Manual Workflow Startup
~~~~~~~~~~~~~~~~~~~~~~~~

Alternative approach with manual workflow startup:

.. code-block:: python

   from airfogsim.core.environment import Environment
   from airfogsim.agent.drone import DroneAgent
   from airfogsim.component.mobility import MoveToComponent
   from airfogsim.workflow.inspection import InspectionWorkflow

   env = Environment()

   # Create drone agent
   drone = DroneAgent(env, agent_name="drone1", properties={
       'position': (0, 0, 100),
       'battery_level': 100
   })
   drone.add_component(MoveToComponent(env, drone))
   env.register_agent(drone)

   # Method 2: Create workflow and start manually
   waypoints = [(50, 50, 100), (100, 0, 100), (0, 0, 100)]

   workflow = InspectionWorkflow(
       env=env,
       name="manual_inspection",
       owner=drone,
       properties={'inspection_points': waypoints}
   )

   # Register workflow with environment (without trigger)
   env.workflow_manager.register_workflow(workflow)

   # Start workflow manually
   workflow.start()

   env.run(until=1000)

Multi-Agent Coordination with Contracts
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Example showing contract-based coordination between agents:

.. code-block:: python

   from airfogsim.core.environment import Environment
   from airfogsim.agent.drone import DroneAgent
   from airfogsim.agent.terminal import TerminalAgent
   from airfogsim.component.mobility import MoveToComponent
   from airfogsim.component.img_sensor import ImageSensingComponent
   from airfogsim.manager.contract import ContractManager

   env = Environment()

   # Create contract manager for coordination
   contract_manager = ContractManager(env)

   # Create terminal agent (ground station)
   terminal = TerminalAgent(env, agent_name="terminal1", properties={
       'position': (50, 50, 0)
   })
   env.register_agent(terminal)

   # Create drone agent
   drone = DroneAgent(env, agent_name="drone1", properties={
       'position': (0, 0, 100),
       'battery_level': 100
   })
   drone.add_component(MoveToComponent(env, drone))
   drone.add_component(ImageSensingComponent(env, drone))
   env.register_agent(drone)

   # Define multi-task contract
   task_info = {
       "contract_type": "multi_task",
       "tasks": [
           {
               "component": "MoveToComponent",
               "task_class": "MoveToTask",
               "task_name": "Move to inspection point",
               "target_state": {"position": (100, 100, 100)},
               "properties": {"target_position": (100, 100, 100)}
           },
           {
               "component": "ImageSensingComponent",
               "task_class": "ImageSensingTask",
               "task_name": "Capture images",
               "target_state": {"image_sensing_status": "completed"},
               "properties": {"duration": 30}
           }
       ]
   }

   # Create contract between terminal and drone
   contract_id = contract_manager.create_contract(
       issuer_agent_id=terminal.id,
       task_info=task_info,
       reward=100,
       penalty=50,
       deadline=1000,
       appointed_agent_ids=[drone.id],
       description="Multi-task inspection contract"
   )

   # Set up automatic contract acceptance
   def accept_contract():
       yield env.timeout(10)  # Wait 10 seconds then accept
       success = contract_manager.accept_contract(contract_id, drone.id)
       print(f"Contract acceptance: {'Success' if success else 'Failed'}")

   env.process(accept_contract())

   env.run(until=2000)

Workflow Examples
-----------------

Inspection Mission
~~~~~~~~~~~~~~~~~~

A complete inspection workflow example:

.. code-block:: python

   from airfogsim.core.environment import Environment
   from airfogsim.agent.drone import DroneAgent
   from airfogsim.component.mobility import MoveToComponent
   from airfogsim.component.img_sensor import ImageSensingComponent
   from airfogsim.workflow.inspection import InspectionWorkflow

   env = Environment()

   # Create inspection drone
   drone = DroneAgent(env, agent_name="inspector1", properties={
       'position': (0, 0, 100),
       'battery_level': 100
   })
   drone.add_component(MoveToComponent(env, drone))
   drone.add_component(ImageSensingComponent(env, drone))
   env.register_agent(drone)

   # Define inspection points
   inspection_points = [
       (25, 25, 100),   # Point 1
       (75, 25, 100),   # Point 2
       (75, 75, 100),   # Point 3
       (25, 75, 100),   # Point 4
   ]

   # Create inspection workflow with trigger-based startup
   from airfogsim.core.trigger import TimeTrigger

   workflow = env.create_workflow(
       InspectionWorkflow,
       name="building_inspection",
       owner=drone,
       properties={'inspection_points': inspection_points},
       start_trigger=TimeTrigger(env, trigger_time=50),  # Start after 50 time units
       max_starts=1
   )

   # Workflow will start automatically when trigger fires
   env.run(until=3000)

Logistics Mission
~~~~~~~~~~~~~~~~~

Package delivery workflow with charging:

.. code-block:: python

   from airfogsim.core.environment import Environment
   from airfogsim.agent.delivery_drone import DeliveryDroneAgent
   from airfogsim.agent.delivery_station import DeliveryStation
   from airfogsim.component.mobility import MoveToComponent
   from airfogsim.component.logistics import LogisticsComponent
   from airfogsim.component.charging import ChargingComponent
   from airfogsim.workflow.logistics import LogisticsWorkflow

   env = Environment()

   # Create delivery station
   station = DeliveryStation(env, agent_name="depot1", properties={
       'position': (0, 0, 0)
   })
   env.register_agent(station)

   # Create delivery drone
   drone = DeliveryDroneAgent(env, agent_name="delivery1", properties={
       'position': (0, 0, 0),
       'battery_level': 100
   })
   drone.add_component(MoveToComponent(env, drone))
   drone.add_component(LogisticsComponent(env, drone))
   drone.add_component(ChargingComponent(env, drone))
   env.register_agent(drone)

   # Create logistics workflow with conditional trigger
   from airfogsim.core.trigger import StateTrigger

   # Create trigger that fires when drone is ready (battery > 80%)
   ready_trigger = StateTrigger(
       env=env,
       agent_id=drone.id,
       state_name='battery_level',
       condition='>=',
       threshold=80,
       name="drone_ready_trigger"
   )

   delivery = env.create_workflow(
       LogisticsWorkflow,
       name="package_delivery",
       owner=drone,
       properties={
           'pickup_location': (0, 0, 0),
           'delivery_location': (100, 100, 0),
           'payloads': [{'id': 'PKG001', 'weight': 2.5}],
           'source_agent_id': station.id,
           'target_agent_id': 'customer1'
       },
       start_trigger=ready_trigger,
       max_starts=1
   )

   # Workflow will start automatically when drone battery >= 80%
   env.run(until=2000)

Data Integration Examples
-------------------------

Weather Integration
~~~~~~~~~~~~~~~~~~~

Integrating weather data that automatically affects all agents in the simulation:

.. code-block:: python

   from airfogsim.core.environment import Environment
   from airfogsim.agent.drone import DroneAgent
   from airfogsim.component.mobility import MoveToComponent
   from airfogsim.workflow.inspection import InspectionWorkflow
   from airfogsim.dataprovider.weather_integration import WeatherIntegration

   env = Environment()

   # Create standard drone agents
   drones = []
   for i in range(3):
       drone = DroneAgent(env, agent_name=f"drone_{i}", properties={
           'position': (i*50, 0, 100),
           'battery_level': 100
       })
       drone.add_component(MoveToComponent(env, drone))
       env.register_agent(drone)
       drones.append(drone)

       # Create inspection workflow for each drone with staggered start times
       inspection_points = [
           (i*50, 0, 100),
           (i*50 + 25, 25, 100),
           (i*50 + 50, 50, 100)
       ]

       from airfogsim.core.trigger import TimeTrigger
       workflow = env.create_workflow(
           InspectionWorkflow,
           name=f"inspection_{i}",
           owner=drone,
           properties={'inspection_points': inspection_points},
           start_trigger=TimeTrigger(env, trigger_time=i*30),  # Stagger starts
           max_starts=1
       )

   # Set up weather integration - this will automatically affect all agents
   weather_config = {
       'location': {'lat': 40.7128, 'lon': -74.0060},
       'use_mock_data': True,  # Use simulated weather data
       'api_refresh_interval': 300  # Update every 5 minutes
   }

   # WeatherIntegration automatically modifies agent states based on weather
   weather_integration = WeatherIntegration(env, config=weather_config)

   # The integration will:
   # 1. Monitor weather changes automatically
   # 2. Calculate external forces from wind
   # 3. Update all agents' 'external_force' state directly
   # 4. Affect agent movement and battery consumption

   env.run(until=5000)

Signal Integration with Automatic Management
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Integrating signal data with automatic frequency management and interference detection:

.. code-block:: python

   from airfogsim.core.environment import Environment
   from airfogsim.agent.drone import DroneAgent
   from airfogsim.component.mobility import MoveToComponent
   from airfogsim.component.em_sensor import EMSensingComponent
   from airfogsim.component.communication import CommunicationComponent
   from airfogsim.dataprovider.signal import SignalDataProvider
   from airfogsim.dataprovider.signal_integration import SignalIntegration
   from airfogsim.manager.frequency import FrequencyManager

   env = Environment()

   # Set up frequency manager with signal integration
   frequency_manager = FrequencyManager(
       env=env,
       total_bandwidth=200.0,
       block_bandwidth=10.0,
       start_frequency=2400.0,
       power_limit=100.0,
       config={'use_signal_provider_for_sinr': True}
   )
   env.frequency_manager = frequency_manager

   # Set up signal data provider
   signal_provider = SignalDataProvider(
       env=env,
       config={
           'propagation_model': 'free_space',
           'default_noise_floor': -100.0,
           'weather_enabled': False
       }
   )
   env.signal_data_provider = signal_provider

   # Set up signal integration - automatically manages signal interference
   signal_integration = SignalIntegration(env, config={
       'interference_check_interval': 10.0,  # Check every 10 seconds
       'interference_threshold': 0.7,  # High interference threshold
       'auto_disconnect_on_interference': True
   })

   # Create communication drones
   comm_drones = []
   for i in range(3):
       drone = DroneAgent(env, agent_name=f"comm_drone_{i}", properties={
           'position': (i*100, 0, 100),
           'battery_level': 100
       })
       drone.add_component(MoveToComponent(env, drone))
       drone.add_component(CommunicationComponent(env, drone))
       env.register_agent(drone)
       comm_drones.append(drone)

   # Create sensing drone with EM sensing capability
   sensing_drone = DroneAgent(env, agent_name="sensing_drone", properties={
       'position': (150, 150, 100),
       'battery_level': 100
   })
   sensing_drone.add_component(MoveToComponent(env, sensing_drone))
   sensing_drone.add_component(EMSensingComponent(env, sensing_drone))
   env.register_agent(sensing_drone)

   # Create patrol workflow for sensing drone with periodic trigger
   patrol_points = [(100, 100, 100), (200, 200, 100), (300, 300, 100)]
   from airfogsim.workflow.inspection import InspectionWorkflow
   from airfogsim.core.trigger import TimeTrigger

   patrol_workflow = env.create_workflow(
       InspectionWorkflow,
       name="signal_patrol",
       owner=sensing_drone,
       properties={'inspection_points': patrol_points},
       start_trigger=TimeTrigger(env, interval=600),  # Repeat every 10 minutes
       max_starts=None  # Unlimited repeats
   )

   # The signal integration will automatically:
   # 1. Monitor signal interference between agents
   # 2. Update agent connection_status based on interference
   # 3. Manage frequency allocations and SINR calculations
   # 4. Handle signal detection events from EM sensors

   env.run(until=3600)

Advanced Examples
-----------------

Large-Scale Multi-Agent Simulation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Simulating large numbers of coordinated agents:

.. code-block:: python

   from airfogsim.core.environment import Environment
   from airfogsim.agent.drone import DroneAgent
   from airfogsim.component.mobility import MoveToComponent
   from airfogsim.component.communication import CommunicationComponent
   from airfogsim.workflow.inspection import InspectionWorkflow

   env = Environment()

   # Create swarm of drones
   swarm_size = 20
   drones = []

   for i in range(swarm_size):
       # Distribute drones in a grid
       x = (i % 5) * 50
       y = (i // 5) * 50

       drone = DroneAgent(env, agent_name=f"swarm_drone_{i}", properties={
           'position': (x, y, 100),
           'battery_level': 100
       })
       drone.add_component(MoveToComponent(env, drone))
       drone.add_component(CommunicationComponent(env, drone))
       env.register_agent(drone)
       drones.append(drone)

   # Create inspection workflows for each drone with coordinated triggers
   from airfogsim.core.trigger import TimeTrigger

   for i, drone in enumerate(drones):
       # Define inspection area for each drone
       base_x, base_y = (i % 5) * 50, (i // 5) * 50
       inspection_points = [
           (base_x, base_y, 100),
           (base_x + 25, base_y + 25, 100),
           (base_x + 50, base_y + 50, 100)
       ]

       # Stagger workflow starts to avoid conflicts
       start_time = 10 + (i * 5)  # Start every 5 time units

       workflow = env.create_workflow(
           InspectionWorkflow,
           name=f"inspection_{i}",
           owner=drone,
           properties={'inspection_points': inspection_points},
           start_trigger=TimeTrigger(env, trigger_time=start_time),
           max_starts=1
       )

   env.run(until=5000)

Real-time Monitoring
~~~~~~~~~~~~~~~~~~~~

Setting up real-time monitoring and statistics collection:

.. code-block:: python

   from airfogsim.core.environment import Environment
   from airfogsim.agent.drone import DroneAgent
   from airfogsim.component.mobility import MoveToComponent
   from airfogsim.statistics import StatsCollector

   env = Environment(visual_interval=10)  # Enable visualization updates

   # Set up statistics collection
   stats_collector = StatsCollector(
       env,
       output_dir='./simulation_stats',
       agent_collector_config={
           'listen_visual_update': True,
           'collect_interval': 10
       }
   )

   # Create monitored agents
   for i in range(10):
       drone = DroneAgent(env, agent_name=f"monitored_drone_{i}", properties={
           'position': (i*10, i*10, 100),
           'battery_level': 100
       })
       drone.add_component(MoveToComponent(env, drone))
       env.register_agent(drone)

   # Start monitoring (stats collector starts automatically)
   env.run(until=10000)

   # Analyze collected statistics
   from airfogsim.statistics import StatsAnalyzer
   analyzer = StatsAnalyzer('./simulation_stats')
   summary = analyzer.generate_summary()
   print(summary)

Performance Testing with Workflows
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Benchmarking simulation performance with realistic workflow scenarios:

.. code-block:: python

   import time
   from airfogsim.core.environment import Environment
   from airfogsim.agent.drone import DroneAgent
   from airfogsim.component.mobility import MoveToComponent
   from airfogsim.component.charging import ChargingComponent
   from airfogsim.workflow.inspection import InspectionWorkflow
   from airfogsim.workflow.charging import ChargingWorkflow

   def benchmark_simulation(agent_count, simulation_time):
       """Benchmark simulation with workflow-driven agents."""
       start_time = time.time()

       env = Environment()

       # Create agents with realistic workflows
       agents = []
       for i in range(agent_count):
           agent = DroneAgent(env, agent_name=f"bench_agent_{i}", properties={
               'position': (i*10, 0, 100),
               'battery_level': 100
           })
           agent.add_component(MoveToComponent(env, agent))
           agent.add_component(ChargingComponent(env, agent))
           env.register_agent(agent)
           agents.append(agent)

           # Create inspection workflow for each agent
           inspection_points = [
               (i*10, 0, 100),
               (i*10 + 50, 50, 100),
               (i*10, 100, 100)
           ]

           from airfogsim.core.trigger import TimeTrigger, StateTrigger

           inspection_workflow = env.create_workflow(
               InspectionWorkflow,
               name=f"inspection_{i}",
               owner=agent,
               properties={'inspection_points': inspection_points},
               start_trigger=TimeTrigger(env, trigger_time=i*2),  # Stagger starts
               max_starts=1
           )

           # Create charging workflow with battery-level trigger
           battery_trigger = StateTrigger(
               env=env,
               agent_id=agent.id,
               state_name='battery_level',
               condition='<=',
               threshold=30,
               name=f"battery_low_{i}"
           )

           charging_workflow = env.create_workflow(
               ChargingWorkflow,
               name=f"charging_{i}",
               owner=agent,
               properties={'target_charge_level': 90},
               start_trigger=battery_trigger,
               max_starts=None  # Can charge multiple times
           )

       # Run simulation - agents will execute workflows automatically
       env.run(until=simulation_time)

       end_time = time.time()

       # Calculate performance metrics
       real_time = end_time - start_time
       performance_ratio = simulation_time / real_time if real_time > 0 else 0

       return {
           'real_time': real_time,
           'sim_time': simulation_time,
           'agent_count': agent_count,
           'performance_ratio': performance_ratio
       }

   # Run benchmarks
   test_cases = [
       (5, 1000),    # 5 agents, 1000 time units
       (10, 1000),   # 10 agents, 1000 time units
       (20, 1000),   # 20 agents, 1000 time units
   ]

   for agent_count, sim_time in test_cases:
       result = benchmark_simulation(agent_count, sim_time)
       print(f"Agents: {result['agent_count']}, "
             f"Real time: {result['real_time']:.2f}s, "
             f"Performance: {result['performance_ratio']:.2f}x real-time")

Running Examples
----------------

All examples are available in the `src/airfogsim/examples/` directory of the AirFogSim repository. To run them:

.. code-block:: bash

   # Clone the repository
   git clone https://github.com/ZhiweiWei-NAMI/AirFogSim.git
   cd airfogsim-project

   # Install in development mode
   pip install -e .

   # Run examples
   python -m airfogsim.examples.example_trigger_basic
   python -m airfogsim.examples.example_workflow_inspection
   python -m airfogsim.examples.example_weather_provider
   python -m airfogsim.examples.example_signal_sensing

Available Examples
~~~~~~~~~~~~~~~~~~

The following examples are available in the examples directory:

* **example_trigger_basic.py** - Basic trigger system demonstration
* **example_workflow_inspection.py** - Inspection workflow with charging
* **example_workflow_logistics.py** - Logistics and delivery workflows
* **example_weather_provider.py** - Weather data integration
* **example_signal_sensing.py** - Signal detection and EM sensing
* **example_frequency_signal_integration.py** - Frequency management and signal interference
* **example_benchmark_multi_workflow.py** - Multi-workflow performance benchmarking

For more examples and detailed tutorials, see the :doc:`user_guide` and explore the examples directory in the source code.
