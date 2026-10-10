Component Development Guide
===========================

This guide covers how to create custom components that provide capabilities to agents in AeroAgentSim.

Overview
--------

Components are modular capabilities that agents can use to perform specific tasks. They abstract functionality like mobility, sensing, computation, and communication, providing a clean separation of concerns.

Key Concepts
------------

Component Architecture
~~~~~~~~~~~~~~~~~~~~~~

Every component in AeroAgentSim:

- **Provides capabilities** to agents through task execution
- **Manages resources** required for task execution
- **Calculates metrics** based on resource usage and agent state
- **Triggers events** for task status and metric changes
- **Handles task lifecycle** from initiation to completion

Base Component Class
~~~~~~~~~~~~~~~~~~~~

All components inherit from the base ``Component`` class:

.. code-block:: python

   from aeroagentsim.core.component import Component
   
   class CustomComponent(Component):
       """Custom component implementation."""
       
       # Define metrics this component produces
       PRODUCED_METRICS = ['custom_metric', 'performance_score']
       
       # Define states this component monitors
       MONITORED_STATES = ['agent_status', 'battery_level']
       
       def __init__(self, env, owner):
           super().__init__(env, owner)
           # Custom initialization

Creating a Custom Component
---------------------------

Step 1: Define Component Class
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from aeroagentsim.core.component import Component
   from aeroagentsim.core.enums import ComponentStatus
   
   class DataProcessingComponent(Component):
       """Component for data processing tasks."""
       
       PRODUCED_METRICS = [
           'processing_power',    # FLOPS available
           'memory_usage',        # Memory consumption
           'processing_latency'   # Task completion time
       ]
       
       MONITORED_STATES = [
           'battery_level',       # Monitor power consumption
           'temperature',         # Monitor thermal conditions
           'cpu_load'            # Monitor current load
       ]
       
       def __init__(self, env, owner):
           super().__init__(env, owner)
           self.max_processing_power = 1000.0  # FLOPS
           self.memory_capacity = 8192.0       # MB
           self.current_tasks = []

Step 2: Implement Task Execution
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def execute_task(self, task):
       """Execute a data processing task."""
       try:
           # Validate task requirements
           if not self._can_execute_task(task):
               task.status = TaskStatus.FAILED
               return
           
           # Set task status to running
           task.status = TaskStatus.RUNNING
           self.current_tasks.append(task)
           
           # Calculate required resources
           required_power = task.properties.get('required_flops', 100.0)
           required_memory = task.properties.get('required_memory', 512.0)
           
           # Allocate resources
           self._allocate_resources(required_power, required_memory)
           
           # Execute task logic
           yield from self._process_data(task)
           
           # Release resources
           self._release_resources(task)
           self.current_tasks.remove(task)
           
           # Mark task as completed
           task.status = TaskStatus.COMPLETED
           
       except Exception as e:
           self.logger.error(f"Task execution failed: {e}")
           task.status = TaskStatus.FAILED
           self._cleanup_failed_task(task)

Step 3: Implement Resource Management
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def _allocate_resources(self, required_power, required_memory):
       """Allocate processing resources for task."""
       # Check resource availability
       available_power = self._get_available_processing_power()
       available_memory = self._get_available_memory()
       
       if required_power > available_power:
           raise ResourceError("Insufficient processing power")
       
       if required_memory > available_memory:
           raise ResourceError("Insufficient memory")
       
       # Update resource usage
       self.current_power_usage += required_power
       self.current_memory_usage += required_memory
       
       # Update metrics
       self._update_metrics()
   
   def _release_resources(self, task):
       """Release resources used by completed task."""
       required_power = task.properties.get('required_flops', 100.0)
       required_memory = task.properties.get('required_memory', 512.0)
       
       self.current_power_usage -= required_power
       self.current_memory_usage -= required_memory
       
       # Update metrics
       self._update_metrics()

Step 4: Implement Metrics Calculation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def _update_metrics(self):
       """Update component metrics based on current state."""
       # Calculate available processing power
       battery_level = self.owner.get_state('battery_level')
       temperature = self.owner.get_state('temperature', 25.0)
       
       # Adjust processing power based on battery and temperature
       power_factor = min(battery_level / 100.0, 1.0)
       temp_factor = max(0.5, 1.0 - (temperature - 25) / 50.0)
       
       available_power = self.max_processing_power * power_factor * temp_factor
       current_power = available_power - self.current_power_usage
       
       # Update metrics
       self.metrics['processing_power'] = max(0, current_power)
       self.metrics['memory_usage'] = self.current_memory_usage
       
       # Calculate processing latency based on load
       load_factor = self.current_power_usage / self.max_processing_power
       base_latency = 10.0  # Base latency in ms
       self.metrics['processing_latency'] = base_latency * (1 + load_factor * 2)
       
       # Trigger metrics update event
       self.trigger_event('metrics_updated', {
           'component_id': self.id,
           'metrics': self.metrics.copy()
       })

Step 5: Implement Task-Specific Logic
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def _process_data(self, task):
       """Process data according to task requirements."""
       processing_time = task.properties.get('processing_time', 10.0)
       data_size = task.properties.get('data_size', 1024.0)  # MB
       
       # Calculate actual processing time based on current performance
       current_power = self.metrics.get('processing_power', 100.0)
       actual_time = processing_time * (1000.0 / max(current_power, 1.0))
       
       # Simulate processing with periodic updates
       update_interval = min(actual_time / 10, 1.0)
       elapsed_time = 0
       
       while elapsed_time < actual_time:
           # Wait for update interval
           yield self.env.timeout(update_interval)
           elapsed_time += update_interval
           
           # Update task progress
           progress = min(elapsed_time / actual_time, 1.0)
           task.progress = progress
           
           # Update battery consumption
           power_consumption = self._calculate_power_consumption()
           self.owner.consume_battery(power_consumption * update_interval)
           
           # Check for thermal throttling
           if self.owner.get_state('temperature', 25.0) > 80.0:
               # Reduce processing speed due to overheating
               actual_time *= 1.2
           
           # Trigger progress event
           self.trigger_event('task_progress', {
               'task_id': task.id,
               'progress': progress,
               'component_id': self.id
           })

Component Integration
---------------------

Using Your Custom Component
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from aeroagentsim.core.environment import Environment
   from aeroagentsim.agent.drone import DroneAgent
   from your_module import DataProcessingComponent
   
   # Create environment and agent
   env = Environment()
   drone = DroneAgent(env, "processor_drone", properties={
       'position': (0, 0, 100),
       'battery_level': 100
   })
   
   # Add data processing component
   processor = DataProcessingComponent(env, drone)
   drone.add_component(processor)
   
   # Register agent
   env.register_agent(drone)

Creating Compatible Tasks
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from aeroagentsim.core.task import Task
   
   class DataProcessingTask(Task):
       """Task for processing data."""
       
       NECESSARY_METRICS = ['processing_power', 'memory_usage']
       PRODUCED_STATES = ['processing_status', 'processed_data']
       
       def __init__(self, env, properties=None):
           super().__init__(env, properties)
           self.required_flops = properties.get('required_flops', 100.0)
           self.required_memory = properties.get('required_memory', 512.0)
           self.data_size = properties.get('data_size', 1024.0)

Best Practices
--------------

Resource Management
~~~~~~~~~~~~~~~~~~~

1. **Check resource availability** before starting tasks
2. **Track resource usage** accurately throughout task execution
3. **Release resources promptly** when tasks complete or fail
4. **Handle resource contention** gracefully

Metrics and Monitoring
~~~~~~~~~~~~~~~~~~~~~~

1. **Update metrics regularly** to reflect current component state
2. **Use meaningful metric names** that describe component capabilities
3. **Trigger events** when significant metric changes occur
4. **Consider external factors** (battery, temperature) in calculations

Error Handling
~~~~~~~~~~~~~~

1. **Validate task requirements** before execution
2. **Handle resource allocation failures** gracefully
3. **Clean up properly** when tasks fail
4. **Log errors** for debugging and monitoring

Performance
~~~~~~~~~~~

1. **Optimize metric calculations** for frequent updates
2. **Use appropriate update intervals** for real-time metrics
3. **Avoid blocking operations** in task execution
4. **Monitor memory usage** for long-running components

Testing
~~~~~~~

1. **Test resource allocation and release** thoroughly
2. **Verify metric calculations** under various conditions
3. **Test error conditions** and recovery mechanisms
4. **Benchmark performance** with realistic workloads

Advanced Topics
---------------

Multi-Component Coordination
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Components can coordinate through:

- **Shared metrics**: Components can read metrics from other components
- **Event communication**: Publishing and subscribing to component events
- **Resource sharing**: Coordinating access to shared resources
- **Task dependencies**: Managing dependencies between component tasks

For more advanced topics, see:

- :doc:`workflow_development` - Coordinating component tasks through workflows
- :doc:`../api/component` - Complete component API reference
- :doc:`../api/task` - Task development guide
