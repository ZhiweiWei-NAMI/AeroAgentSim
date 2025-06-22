User Guide
==========

This comprehensive guide covers all aspects of using AirFogSim for simulation development.

.. toctree::
   :maxdepth: 2
   :caption: User Guide Topics:

   guides/architecture
   guides/agent_development
   guides/component_development
   guides/workflow_development
   guides/dataprovider_development
   guides/simulation_setup
   guides/performance_tuning
   guides/troubleshooting

Core Patterns
-------------

Agent-Component Pattern
~~~~~~~~~~~~~~~~~~~~~~~

Agents gain capabilities through components:

.. code-block:: python

   # Create agent
   drone = env.create_agent(DroneAgent, "drone1")
   
   # Add capabilities
   drone.add_component(MoveToComponent(env, drone))
   drone.add_component(SensorComponent(env, drone))
   drone.add_component(CommunicationComponent(env, drone))
   
   # Agent can now move, sense, and communicate

Event-Driven Communication
~~~~~~~~~~~~~~~~~~~~~~~~~~

Components and agents communicate through events:

.. code-block:: python

   # Subscribe to events
   drone.subscribe('weather_provider', 'weather_changed', 
                   drone._handle_weather_change)
   
   # Trigger events
   drone.trigger_event('battery_low', {'level': 15})

State Management
~~~~~~~~~~~~~~~~

Agents maintain structured state:

.. code-block:: python

   # Register state templates
   drone.register_state_template('altitude', value_type=float, required=True)
   
   # Initialize states
   drone.initialize_states(altitude=100.0, status='idle')
   
   # Update states (triggers events)
   drone.update_state('altitude', 150.0)

Workflow Coordination
~~~~~~~~~~~~~~~~~~~~~

Workflows coordinate high-level processes:

.. code-block:: python

   # Create workflow
   workflow = InspectionWorkflow(env, "mission1", drone,
                                waypoints=[(0,0,100), (100,100,100)])
   
   # Assign to agent
   env.workflow_manager.assign_workflow(drone, workflow)
   
   # Start workflow
   workflow.start()

Advanced Topics
---------------

Multi-Agent Coordination
~~~~~~~~~~~~~~~~~~~~~~~~

Coordinate multiple agents through contracts and communication:

.. code-block:: python

   # Create multiple agents
   drone1 = env.create_agent(DroneAgent, "drone1")
   drone2 = env.create_agent(DroneAgent, "drone2")
   station = env.create_agent(GroundStation, "station1")
   
   # Set up communication network
   for agent in [drone1, drone2, station]:
       agent.add_component(CommunicationComponent(env, agent))
   
   # Create coordination workflow
   coordination_workflow = CoordinationWorkflow(
       env, "coordination", [drone1, drone2],
       coordination_strategy="leader_follower"
   )

Real-time Data Integration
~~~~~~~~~~~~~~~~~~~~~~~~~~

Integrate real-world data sources:

.. code-block:: python

   # Weather data provider
   weather_provider = WeatherDataProvider(env, {
       'api_key': 'your_api_key',
       'locations': [{'lat': 40.7128, 'lon': -74.0060}],
       'update_interval': 300
   })
   
   # Load and start data updates
   weather_provider.load_data()
   weather_provider.start_event_triggering()
   
   # Agents automatically receive weather updates

Performance Optimization
~~~~~~~~~~~~~~~~~~~~~~~~

Optimize simulations for large-scale scenarios:

.. code-block:: python

   # Use efficient data structures
   env.config['use_spatial_indexing'] = True
   env.config['event_batching'] = True
   
   # Limit event frequency
   env.config['max_events_per_second'] = 1000
   
   # Use parallel processing
   env.config['parallel_agents'] = True
   env.config['worker_threads'] = 4

Debugging and Monitoring
~~~~~~~~~~~~~~~~~~~~~~~~

Monitor simulation progress and debug issues:

.. code-block:: python

   # Enable detailed logging
   import logging
   logging.getLogger('airfogsim').setLevel(logging.DEBUG)
   
   # Use built-in monitoring
   from airfogsim.visualization import Dashboard
   dashboard = Dashboard(env)
   dashboard.start()
   
   # Access simulation statistics
   stats = env.get_simulation_stats()
   print(f"Agents: {stats['agent_count']}")
   print(f"Events: {stats['event_count']}")

Best Practices
--------------

Code Organization
~~~~~~~~~~~~~~~~~

1. **Separate Concerns**: Keep agents, components, and workflows in separate modules
2. **Configuration Files**: Use YAML/JSON for simulation parameters
3. **Reusable Components**: Design components for reuse across agent types
4. **Clear Interfaces**: Define clear APIs between system components

Error Handling
~~~~~~~~~~~~~~

1. **Graceful Degradation**: Handle component failures gracefully
2. **Recovery Mechanisms**: Implement automatic recovery where possible
3. **Logging**: Use comprehensive logging for debugging
4. **Validation**: Validate inputs and configurations

Performance
~~~~~~~~~~~

1. **Efficient Algorithms**: Use appropriate data structures and algorithms
2. **Event Optimization**: Minimize unnecessary event triggering
3. **Memory Management**: Clean up resources properly
4. **Profiling**: Profile simulations to identify bottlenecks

Testing
~~~~~~~

1. **Unit Tests**: Test individual components and agents
2. **Integration Tests**: Test component interactions
3. **Scenario Tests**: Test complete simulation scenarios
4. **Performance Tests**: Benchmark simulation performance

Common Patterns and Examples
----------------------------

For detailed examples and patterns, see:

- :doc:`examples` - Complete simulation examples
- :doc:`guides/agent_development` - Custom agent development
- :doc:`guides/component_development` - Component creation
- :doc:`guides/workflow_development` - Workflow design
- :doc:`api/index` - Complete API reference
