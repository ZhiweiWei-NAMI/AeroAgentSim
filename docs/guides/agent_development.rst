Agent Development Guide
=======================

This guide covers how to create custom agent types in AirFogSim.

Overview
--------

Agents are autonomous entities that make decisions and perform actions in the simulation. They are the primary actors in AirFogSim and represent entities like drones, ground stations, or other autonomous systems.

Key Concepts
------------

Agent Architecture
~~~~~~~~~~~~~~~~~~

Every agent in AirFogSim consists of:

- **State Management**: Internal state variables with type validation
- **Decision Logic**: SimPy process defining behavior and decision-making
- **Component Ownership**: Components that provide capabilities
- **Event Handling**: Subscription and triggering of simulation events

Base Agent Class
~~~~~~~~~~~~~~~~~

All agents inherit from the base ``Agent`` class:

.. code-block:: python

   from airfogsim.core.agent import Agent
   
   class CustomAgent(Agent):
       """Custom agent implementation."""
       
       # Define the states this agent produces
       PRODUCED_STATES = ['custom_state', 'agent_status']
       
       def __init__(self, env, agent_name, properties=None):
           super().__init__(env, agent_name, properties)
           # Custom initialization
           
       def _setup_state_templates(self):
           """Define state templates for this agent."""
           super()._setup_state_templates()
           # Add custom state templates
           
       def _decision_logic(self):
           """Main decision-making process."""
           # Implement agent behavior

Creating a Custom Agent
-----------------------

Step 1: Define Agent Class
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from airfogsim.core.agent import Agent
   from airfogsim.core.enums import AgentStatus
   
   class WeatherMonitorAgent(Agent):
       """Agent that monitors weather conditions."""
       
       PRODUCED_STATES = [
           'weather_data',
           'monitoring_status', 
           'alert_level'
       ]
       
       def __init__(self, env, agent_name, properties=None):
           super().__init__(env, agent_name, properties)
           self.monitoring_interval = properties.get('monitoring_interval', 60)
           self.alert_threshold = properties.get('alert_threshold', 0.8)

Step 2: Setup State Templates
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def _setup_state_templates(self):
       """Define state templates for weather monitoring."""
       super()._setup_state_templates()
       
       # Weather data state
       self.register_state_template(
           'weather_data',
           value_type=dict,
           required=True,
           default_value={'temperature': 20.0, 'humidity': 0.5}
       )
       
       # Monitoring status
       self.register_state_template(
           'monitoring_status',
           value_type=str,
           required=True,
           default_value='idle'
       )
       
       # Alert level
       self.register_state_template(
           'alert_level',
           value_type=float,
           required=True,
           default_value=0.0
       )

Step 3: Implement Decision Logic
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def _decision_logic(self):
       """Main decision-making process for weather monitoring."""
       while True:
           try:
               # Check current weather conditions
               weather_data = self.get_state('weather_data')
               
               # Calculate alert level based on conditions
               alert_level = self._calculate_alert_level(weather_data)
               self.update_state('alert_level', alert_level)
               
               # Update monitoring status
               if alert_level > self.alert_threshold:
                   self.update_state('monitoring_status', 'alert')
                   self.trigger_event('weather_alert', {
                       'agent_id': self.id,
                       'alert_level': alert_level,
                       'weather_data': weather_data
                   })
               else:
                   self.update_state('monitoring_status', 'normal')
               
               # Wait for next monitoring cycle
               yield self.env.timeout(self.monitoring_interval)
               
           except Exception as e:
               self.logger.error(f"Error in decision logic: {e}")
               self.update_state('status', AgentStatus.ERROR)
               break

Step 4: Add Helper Methods
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def _calculate_alert_level(self, weather_data):
       """Calculate alert level based on weather conditions."""
       temperature = weather_data.get('temperature', 20.0)
       humidity = weather_data.get('humidity', 0.5)
       wind_speed = weather_data.get('wind_speed', 0.0)
       
       # Simple alert calculation (customize as needed)
       temp_factor = abs(temperature - 20) / 40  # Normalize temperature
       humidity_factor = abs(humidity - 0.5) / 0.5  # Normalize humidity
       wind_factor = min(wind_speed / 20, 1.0)  # Normalize wind speed
       
       return (temp_factor + humidity_factor + wind_factor) / 3

Agent Integration
-----------------

Using Your Custom Agent
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from airfogsim.core.environment import Environment
   from your_module import WeatherMonitorAgent
   
   # Create environment
   env = Environment()
   
   # Create weather monitor agent
   monitor = WeatherMonitorAgent(
       env=env,
       agent_name="weather_station_1",
       properties={
           'monitoring_interval': 30,  # Monitor every 30 seconds
           'alert_threshold': 0.7,     # Alert when level > 0.7
           'position': (100, 100, 10)  # Station position
       }
   )
   
   # Register agent with environment
   env.register_agent(monitor)
   
   # Run simulation
   env.run(until=1000)

Adding Components
~~~~~~~~~~~~~~~~~

Agents gain capabilities through components:

.. code-block:: python

   from airfogsim.component.communication import CommunicationComponent
   from airfogsim.component.sensing import SensorComponent
   
   # Add communication capability
   monitor.add_component(CommunicationComponent(env, monitor))
   
   # Add sensing capability
   monitor.add_component(SensorComponent(env, monitor))

Best Practices
--------------

State Management
~~~~~~~~~~~~~~~~

1. **Define clear state templates** with appropriate types and defaults
2. **Use meaningful state names** that describe the agent's condition
3. **Update states consistently** to trigger appropriate events
4. **Validate state changes** to prevent invalid transitions

Error Handling
~~~~~~~~~~~~~~

1. **Wrap decision logic in try-catch** blocks
2. **Log errors appropriately** for debugging
3. **Set error status** when critical failures occur
4. **Implement recovery mechanisms** where possible

Performance
~~~~~~~~~~~

1. **Use appropriate timeout intervals** to balance responsiveness and performance
2. **Avoid blocking operations** in decision logic
3. **Clean up resources** properly when agents are destroyed
4. **Monitor memory usage** for long-running simulations

Testing
~~~~~~~

1. **Test state transitions** thoroughly
2. **Verify event triggering** works correctly
3. **Test error conditions** and recovery
4. **Benchmark performance** with realistic workloads

Advanced Topics
---------------

Multi-Agent Coordination
~~~~~~~~~~~~~~~~~~~~~~~~

Agents can coordinate through:

- **Event communication**: Publishing and subscribing to events
- **Shared resources**: Accessing common simulation resources
- **Contract systems**: Formal agreements between agents
- **Workflow coordination**: Participating in multi-agent workflows

For more advanced topics, see:

- :doc:`component_development` - Creating custom components
- :doc:`workflow_development` - Designing agent workflows
- :doc:`../api/agent` - Complete agent API reference
