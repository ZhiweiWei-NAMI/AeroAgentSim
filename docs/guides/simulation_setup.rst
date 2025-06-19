Simulation Setup Guide
======================

This guide covers how to set up and configure AirFogSim simulations for various scenarios.

Overview
--------

Setting up an AirFogSim simulation involves configuring the environment, creating agents with appropriate components, setting up data providers, and defining workflows that coordinate the simulation behavior.

Basic Simulation Setup
----------------------

Environment Configuration
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from airfogsim.core.environment import Environment
   
   # Create environment with custom configuration
   env = Environment(config={
       'simulation_time_limit': 10000,
       'real_time_factor': 1.0,
       'logging_level': 'INFO',
       'enable_visualization': True
   })

Agent Creation and Registration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from airfogsim.agent.drone import DroneAgent
   from airfogsim.agent.terminal import TerminalAgent
   from airfogsim.component.mobility import MoveToComponent
   from airfogsim.component.communication import CommunicationComponent
   
   # Create drone agents
   drones = []
   for i in range(5):
       drone = DroneAgent(env, f"drone_{i}", properties={
           'position': (i*100, 0, 100),
           'battery_level': 100,
           'max_speed': 20.0
       })
       
       # Add components
       drone.add_component(MoveToComponent(env, drone))
       drone.add_component(CommunicationComponent(env, drone))
       
       # Register with environment
       env.register_agent(drone)
       drones.append(drone)
   
   # Create ground station
   station = TerminalAgent(env, "base_station", properties={
       'position': (250, 250, 0)
   })
   station.add_component(CommunicationComponent(env, station))
   env.register_agent(station)

Resource Manager Setup
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from airfogsim.manager.airspace import AirspaceManager
   from airfogsim.manager.frequency import FrequencyManager
   from airfogsim.manager.landing import LandingManager
   
   # Configure airspace management
   airspace_manager = AirspaceManager(
       env=env,
       bounds=((0, 0, 0), (1000, 1000, 500)),
       resolution=10.0
   )
   env.airspace_manager = airspace_manager
   
   # Configure frequency management
   frequency_manager = FrequencyManager(
       env=env,
       total_bandwidth=200.0,
       block_bandwidth=10.0,
       start_frequency=2400.0
   )
   env.frequency_manager = frequency_manager
   
   # Configure landing spots
   landing_manager = LandingManager(env)
   landing_spots = [
       (100, 100, 0), (200, 200, 0), (300, 300, 0)
   ]
   for i, position in enumerate(landing_spots):
       landing_manager.add_landing_spot(f"spot_{i}", position)
   env.landing_manager = landing_manager

Advanced Configuration
----------------------

Data Provider Integration
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from airfogsim.dataprovider.weather_integration import WeatherIntegration
   from airfogsim.dataprovider.signal_integration import SignalIntegration
   
   # Weather integration
   weather_config = {
       'location': {'lat': 40.7128, 'lon': -74.0060},
       'use_mock_data': True,
       'update_interval': 300
   }
   weather_integration = WeatherIntegration(env, weather_config)
   
   # Signal integration
   signal_config = {
       'interference_check_interval': 10.0,
       'interference_threshold': 0.7,
       'auto_disconnect_on_interference': True
   }
   signal_integration = SignalIntegration(env, signal_config)

Workflow Configuration
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from airfogsim.workflow.inspection import InspectionWorkflow
   from airfogsim.workflow.logistics import LogisticsWorkflow
   from airfogsim.core.trigger import TimeTrigger, StateTrigger
   
   # Create inspection workflows for drones
   for i, drone in enumerate(drones[:3]):  # First 3 drones for inspection
       inspection_points = [
           (i*200, 100, 100),
           (i*200 + 100, 200, 100),
           (i*200, 300, 100)
       ]
       
       workflow = env.create_workflow(
           InspectionWorkflow,
           name=f"inspection_{i}",
           owner=drone,
           properties={'inspection_points': inspection_points},
           start_trigger=TimeTrigger(env, trigger_time=i*60),
           max_starts=1
       )
   
   # Create logistics workflows for remaining drones
   for i, drone in enumerate(drones[3:], 3):
       workflow = env.create_workflow(
           LogisticsWorkflow,
           name=f"delivery_{i}",
           owner=drone,
           properties={
               'pickup_location': (0, 0, 0),
               'delivery_location': (500, 500, 0),
               'payloads': [{'id': f'PKG_{i}', 'weight': 2.0}]
           },
           start_trigger=StateTrigger(
               env=env,
               agent_id=drone.id,
               state_name='battery_level',
               condition='>=',
               threshold=80
           ),
           max_starts=1
       )

Scenario-Specific Configurations
--------------------------------

Search and Rescue Scenario
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def setup_search_rescue_scenario(env):
       """Setup a search and rescue simulation scenario."""
       
       # Create search area
       search_area = {
           'bounds': ((0, 0, 0), (2000, 2000, 300)),
           'target_locations': [
               (500, 500, 0),   # Survivor location 1
               (1200, 800, 0),  # Survivor location 2
               (300, 1500, 0)   # Survivor location 3
           ]
       }
       
       # Create search drones with specialized components
       from airfogsim.component.img_sensor import ImageSensingComponent
       from airfogsim.component.em_sensor import EMSensingComponent
       
       search_drones = []
       for i in range(6):
           drone = DroneAgent(env, f"search_drone_{i}", properties={
               'position': (i*100, 0, 150),
               'battery_level': 100,
               'search_pattern': 'grid'
           })
           
           # Add search capabilities
           drone.add_component(MoveToComponent(env, drone))
           drone.add_component(ImageSensingComponent(env, drone))
           drone.add_component(EMSensingComponent(env, drone))
           drone.add_component(CommunicationComponent(env, drone))
           
           env.register_agent(drone)
           search_drones.append(drone)
       
       # Create command center
       command_center = TerminalAgent(env, "command_center", properties={
           'position': (1000, 1000, 0),
           'coordination_range': 5000
       })
       command_center.add_component(CommunicationComponent(env, command_center))
       env.register_agent(command_center)
       
       # Setup search workflows
       from airfogsim.workflow.search import SearchWorkflow
       
       for i, drone in enumerate(search_drones):
           # Divide search area among drones
           sector_width = 2000 // 3
           sector_height = 2000 // 2
           sector_x = (i % 3) * sector_width
           sector_y = (i // 3) * sector_height
           
           search_points = [
               (sector_x + 100, sector_y + 100, 150),
               (sector_x + sector_width - 100, sector_y + 100, 150),
               (sector_x + sector_width - 100, sector_y + sector_height - 100, 150),
               (sector_x + 100, sector_y + sector_height - 100, 150)
           ]
           
           workflow = env.create_workflow(
               SearchWorkflow,
               name=f"search_sector_{i}",
               owner=drone,
               properties={
                   'search_points': search_points,
                   'search_pattern': 'systematic',
                   'detection_range': 50.0
               },
               start_trigger=TimeTrigger(env, trigger_time=30),
               max_starts=1
           )
       
       return search_area, search_drones, command_center

Package Delivery Scenario
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def setup_delivery_scenario(env):
       """Setup a package delivery simulation scenario."""
       
       # Create delivery network
       from airfogsim.agent.delivery_station import DeliveryStation
       from airfogsim.agent.delivery_drone import DeliveryDroneAgent
       from airfogsim.component.logistics import LogisticsComponent
       from airfogsim.component.charging import ChargingComponent
       
       # Create distribution centers
       distribution_centers = []
       center_locations = [(0, 0, 0), (1000, 0, 0), (500, 866, 0)]  # Triangle
       
       for i, location in enumerate(center_locations):
           center = DeliveryStation(env, f"center_{i}", properties={
               'position': location,
               'capacity': 100,
               'processing_rate': 10
           })
           env.register_agent(center)
           distribution_centers.append(center)
       
       # Create delivery drones
       delivery_drones = []
       for i in range(12):  # 4 drones per center
           center_idx = i // 4
           home_center = distribution_centers[center_idx]
           
           drone = DeliveryDroneAgent(env, f"delivery_drone_{i}", properties={
               'position': home_center.get_state('position'),
               'battery_level': 100,
               'payload_capacity': 5.0,
               'home_station': home_center.id
           })
           
           # Add delivery capabilities
           drone.add_component(MoveToComponent(env, drone))
           drone.add_component(LogisticsComponent(env, drone))
           drone.add_component(ChargingComponent(env, drone))
           drone.add_component(CommunicationComponent(env, drone))
           
           env.register_agent(drone)
           delivery_drones.append(drone)
       
       # Create delivery requests
       delivery_requests = []
       for i in range(50):
           request = {
               'id': f"REQ_{i:03d}",
               'pickup_location': center_locations[i % 3],
               'delivery_location': (
                   random.uniform(0, 1000),
                   random.uniform(0, 1000),
                   0
               ),
               'weight': random.uniform(0.5, 4.0),
               'priority': random.choice(['normal', 'urgent', 'express']),
               'deadline': env.now + random.uniform(1800, 7200)  # 30min to 2hr
           }
           delivery_requests.append(request)
       
       # Setup delivery coordination
       from airfogsim.workflow.delivery_coordination import DeliveryCoordinationWorkflow
       
       coordinator = env.create_workflow(
           DeliveryCoordinationWorkflow,
           name="delivery_coordinator",
           owner=None,  # System-level workflow
           properties={
               'delivery_requests': delivery_requests,
               'available_drones': [d.id for d in delivery_drones],
               'optimization_strategy': 'minimize_time'
           },
           start_trigger=TimeTrigger(env, trigger_time=10),
           max_starts=1
       )
       
       return distribution_centers, delivery_drones, delivery_requests

Performance Optimization
------------------------

Large-Scale Simulations
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def setup_large_scale_simulation(env, num_agents=1000):
       """Setup optimized configuration for large-scale simulations."""
       
       # Configure environment for performance
       env.config.update({
           'spatial_indexing': True,
           'event_batching': True,
           'parallel_processing': True,
           'memory_optimization': True
       })
       
       # Use efficient agent creation
       agents = []
       batch_size = 100
       
       for batch in range(0, num_agents, batch_size):
           batch_agents = []
           
           for i in range(batch, min(batch + batch_size, num_agents)):
               # Create lightweight agents
               agent = DroneAgent(env, f"agent_{i}", properties={
                   'position': (
                       random.uniform(0, 10000),
                       random.uniform(0, 10000),
                       random.uniform(50, 200)
                   ),
                   'battery_level': random.uniform(80, 100)
               })
               
               # Add minimal components
               agent.add_component(MoveToComponent(env, agent))
               batch_agents.append(agent)
           
           # Register batch
           env.register_agents(batch_agents)
           agents.extend(batch_agents)
       
       return agents

Monitoring and Debugging
------------------------

Simulation Monitoring
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def setup_monitoring(env):
       """Setup simulation monitoring and logging."""
       
       # Configure logging
       import logging
       logging.getLogger('airfogsim').setLevel(logging.INFO)
       
       # Setup performance monitoring
       from airfogsim.utils.monitor import SimulationMonitor
       
       monitor = SimulationMonitor(env, config={
           'update_interval': 60,
           'metrics': ['agent_count', 'event_rate', 'memory_usage'],
           'export_format': 'csv',
           'export_path': './simulation_metrics.csv'
       })
       
       # Setup visualization
       if env.config.get('enable_visualization'):
           from airfogsim.visualization import Dashboard
           dashboard = Dashboard(env, port=8080)
           dashboard.start()
       
       return monitor

Running the Simulation
----------------------

Complete Simulation Example
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def run_complete_simulation():
       """Run a complete simulation with all components."""
       
       # Create and configure environment
       env = Environment()
       
       # Setup scenario (choose one)
       # setup_search_rescue_scenario(env)
       setup_delivery_scenario(env)
       
       # Setup monitoring
       monitor = setup_monitoring(env)
       
       # Run simulation
       try:
           print("Starting simulation...")
           env.run(until=10000)  # Run for 10,000 time units
           print("Simulation completed successfully")
           
       except Exception as e:
           print(f"Simulation failed: {e}")
           
       finally:
           # Export results
           monitor.export_results()
           print("Results exported")

   if __name__ == "__main__":
       run_complete_simulation()

For more information, see:

- :doc:`performance_tuning` - Optimizing simulation performance
- :doc:`troubleshooting` - Common issues and solutions
- :doc:`../examples` - Complete simulation examples
