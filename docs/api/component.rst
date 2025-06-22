Component Classes
================

Components provide functional capabilities to agents. Each component encapsulates specific functionality and manages the execution of related tasks.

Base Component
--------------

.. autoclass:: airfogsim.core.component.Component
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Mobility Components
-------------------

MoveToComponent
~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.component.mobility.MoveToComponent
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Power Management
----------------

ChargingComponent
~~~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.component.charging.ChargingComponent
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Communication Components
------------------------

CommunicationComponent
~~~~~~~~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.component.communication.CommunicationComponent
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Computation Components
----------------------

CPUComponent
~~~~~~~~~~~~

.. autoclass:: airfogsim.component.computation.CPUComponent
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

ComputationComponent
~~~~~~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.component.computation.ComputationComponent
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Sensing Components
------------------

ImageSensingComponent
~~~~~~~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.component.img_sensor.ImageSensingComponent
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

EMSensingComponent
~~~~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.component.em_sensor.EMSensingComponent
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

ObjectSensorComponent
~~~~~~~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.component.object_sensor.ObjectSensorComponent
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Logistics Components
--------------------

LogisticsComponent
~~~~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.component.logistics.LogisticsComponent
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Component Development Guide
---------------------------

Creating Custom Components
~~~~~~~~~~~~~~~~~~~~~~~~~~

To create a custom component, inherit from the base Component class:

.. code-block:: python

   from airfogsim.core.component import Component
   
   class CustomComponent(Component):
       """Custom component implementation."""

       # Define which agent states this component monitors
       MONITORED_STATES = ['battery_level', 'position']

       # Define which metrics this component produces
       PRODUCED_METRICS = ['processing_speed', 'energy_consumption']

       def __init__(self, env, agent, name=None, supported_events=None, properties=None):
           super().__init__(env, agent, name or "Custom",
                          supported_events or [], properties or {})

           # Component-specific initialization
           self.custom_property = self.properties.get('custom_property', 0)

       def can_execute(self, task):
           """Check if this component can execute the given task."""
           # Check task compatibility - default implementation checks component name
           return task.component_name == self.name
       
       def _calculate_performance_metrics(self):
           """Calculate performance metrics based on agent state."""
           battery = self.agent.get_state('battery_level', 100)
           
           # Example: processing speed depends on battery level
           processing_speed = battery * 0.01  # 1% per battery percent
           energy_consumption = 5.0  # Base consumption
           
           return {
               'processing_speed': processing_speed,
               'energy_consumption': energy_consumption
           }

Task Execution Framework
~~~~~~~~~~~~~~~~~~~~~~~~

Components manage task execution through a standardized framework:

.. code-block:: python

   def execute_task(self, task):
       """Execute a task through this component."""
       # Framework automatically handles:
       # 1. Task validation (via can_execute())
       # 2. Performance metric calculation (via _calculate_performance_metrics())
       # 3. State monitoring and metric updates
       # 4. Task execution (calls task.execute() with metrics)
       # 5. Event triggering (task_started, task_completed, etc.)
       # 6. Cleanup and error handling

       # Your component needs to implement:
       # - can_execute(task) -> bool
       # - _calculate_performance_metrics() -> Dict[str, Any]

Performance Metrics
~~~~~~~~~~~~~~~~~~~

Components calculate performance metrics based on agent state:

.. code-block:: python

   def _calculate_performance_metrics(self):
       """Calculate metrics based on current conditions."""
       # Get relevant agent states
       battery = self.agent.get_state('battery_level', 100)
       weather = self.agent.get_state('weather_condition', 'clear')
       
       # Calculate performance based on conditions
       base_speed = 10.0  # m/s
       
       # Battery affects performance
       battery_factor = battery / 100.0
       
       # Weather affects performance
       weather_factors = {
           'clear': 1.0,
           'cloudy': 0.9,
           'rain': 0.7,
           'storm': 0.3
       }
       weather_factor = weather_factors.get(weather, 1.0)
       
       actual_speed = base_speed * battery_factor * weather_factor
       energy_consumption = 5.0 / battery_factor  # More consumption when low battery
       
       return {
           'movement_speed': actual_speed,
           'energy_consumption': energy_consumption
       }

State Monitoring
~~~~~~~~~~~~~~~~

Components automatically monitor specified agent states:

.. code-block:: python

   class WeatherSensitiveComponent(Component):
       # Monitor weather-related states
       MONITORED_STATES = ['weather_condition', 'wind_speed', 'temperature']
       
       def _on_agent_state_changed(self, event_data):
           """Called when monitored states change."""
           state_key = event_data.get('key')
           new_value = event_data.get('new_value')

           if state_key == 'weather_condition':
               # Custom handling for weather changes
               print(f"Weather changed to: {new_value}")
               # The framework automatically recalculates metrics

           # Call parent implementation (handles metric recalculation)
           super()._on_agent_state_changed(event_data)

Event Handling
~~~~~~~~~~~~~~

Components trigger events during task execution:

.. code-block:: python

   # Standard events triggered automatically:
   # - ComponentName.task_started
   # - ComponentName.task_completed  
   # - ComponentName.task_failed
   # - ComponentName.task_canceled
   # - ComponentName.metric_changed
   
   # Trigger custom events
   self.trigger_event('custom_event', {
       'custom_data': 'value',
       'timestamp': self.env.now
   })

Error Handling
~~~~~~~~~~~~~~

Components should handle errors gracefully:

.. code-block:: python

   def _calculate_performance_metrics(self):
       """Calculate metrics with error handling."""
       try:
           # Normal calculation
           return self._do_calculation()
       except Exception as e:
           # Log error and return safe defaults
           logger.error(f"Error calculating metrics: {e}")
           return {
               'processing_speed': 0.0,
               'energy_consumption': 10.0  # High consumption in error state
           }

Component Lifecycle
~~~~~~~~~~~~~~~~~~~

Components have a defined lifecycle:

.. code-block:: python

   # 1. Initialization
   component = CustomComponent(env, agent, properties={'custom_property': 10})
   agent.add_component(component)

   # 2. Task execution (multiple times)
   # Tasks are executed through workflows, not directly
   # The component will receive tasks from the agent's task scheduler

   # 3. State monitoring (continuous)
   # Component automatically monitors MONITORED_STATES and updates metrics

   # 4. Error handling
   if error_condition:
       component.disable()  # Disables component and cancels tasks

   # 5. Recovery
   component.enable()  # Re-enables component

Best Practices
~~~~~~~~~~~~~~

1. **State Dependencies**: Clearly define MONITORED_STATES for performance calculation
2. **Metric Consistency**: Ensure PRODUCED_METRICS match what _calculate_performance_metrics returns
3. **Error Recovery**: Implement graceful error handling and recovery mechanisms
4. **Resource Management**: Clean up resources in task completion/failure handlers
5. **Event Communication**: Use events for loose coupling with other components
6. **Performance**: Optimize metric calculations as they're called frequently

Example: Advanced Sensor Component
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   class AdvancedSensorComponent(Component):
       """Advanced sensor with environmental adaptation."""
       
       MONITORED_STATES = ['battery_level', 'weather_condition', 'altitude']
       PRODUCED_METRICS = ['sensing_accuracy', 'sensing_range', 'energy_consumption']
       
       def __init__(self, env, agent, name=None, supported_events=None, properties=None):
           super().__init__(env, agent, name or "AdvancedSensor",
                          supported_events or [], properties or {})
           self.sensor_type = self.properties.get('sensor_type', 'optical')
           self.calibration_factor = self.properties.get('calibration_factor', 1.0)
       
       def _calculate_performance_metrics(self):
           """Calculate sensor performance based on conditions."""
           battery = self.agent.get_state('battery_level', 100)
           weather = self.agent.get_state('weather_condition', 'clear')
           altitude = self.agent.get_state('altitude', 0)
           
           # Base performance
           base_accuracy = 0.95
           base_range = 1000  # meters
           base_consumption = 3.0  # watts
           
           # Battery impact
           battery_factor = max(0.1, battery / 100.0)
           
           # Weather impact on optical sensors
           weather_impact = {
               'clear': 1.0,
               'cloudy': 0.8,
               'rain': 0.5,
               'fog': 0.2,
               'storm': 0.1
           }
           weather_factor = weather_impact.get(weather, 1.0)
           
           # Altitude impact (atmospheric effects)
           altitude_factor = max(0.5, 1.0 - (altitude / 10000.0))
           
           # Calculate final metrics
           accuracy = base_accuracy * battery_factor * weather_factor * altitude_factor
           sensing_range = base_range * battery_factor * weather_factor
           consumption = base_consumption / battery_factor
           
           return {
               'sensing_accuracy': min(1.0, accuracy * self.calibration_factor),
               'sensing_range': sensing_range,
               'energy_consumption': consumption
           }
       
       def can_execute(self, task):
           """Check if sensor can execute sensing tasks."""
           return (task.component_name == self.name and 
                   task.task_name in ['SensingTask', 'ScanTask'])

For more examples and detailed guides, see the component development documentation.
