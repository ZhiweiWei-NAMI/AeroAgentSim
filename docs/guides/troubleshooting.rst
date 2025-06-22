Troubleshooting Guide
====================

This guide helps you diagnose and resolve common issues when working with AirFogSim.

Common Issues and Solutions
---------------------------

Installation Problems
~~~~~~~~~~~~~~~~~~~~

**Issue: ImportError when importing AirFogSim**

.. code-block:: text

   ImportError: No module named 'airfogsim'

**Solutions:**

1. Verify installation:

   .. code-block:: bash

      pip list | grep airfogsim

2. Reinstall AirFogSim:

   .. code-block:: bash

      pip uninstall airfogsim
      pip install airfogsim

3. Check Python path:

   .. code-block:: python

      import sys
      print(sys.path)

**Issue: Missing dependencies**

.. code-block:: text

   ModuleNotFoundError: No module named 'simpy'

**Solution:**

.. code-block:: bash

   pip install -e ".[dev]"  # Install all dependencies

Simulation Runtime Errors
~~~~~~~~~~~~~~~~~~~~~~~~~

**Issue: Agent creation fails**

.. code-block:: text

   ValueError: Agent with id 'drone1' already exists

**Solutions:**

1. Use unique agent IDs:

   .. code-block:: python

      # Generate unique IDs
      import uuid
      agent_id = f"drone_{uuid.uuid4().hex[:8]}"

2. Check existing agents:

   .. code-block:: python

      existing_ids = [agent.id for agent in env.agents]
      print(f"Existing agents: {existing_ids}")

**Issue: Component execution fails**

.. code-block:: text

   ComponentError: Required metrics not available

**Solutions:**

1. Check component dependencies:

   .. code-block:: python

      # Verify component has required metrics
      component = drone.get_component('MoveToComponent')
      print(f"Available metrics: {component.metrics.keys()}")

2. Initialize components properly:

   .. code-block:: python

      # Ensure components are added before use
      drone.add_component(MoveToComponent(env, drone))
      drone.initialize_components()

**Issue: Workflow state machine errors**

.. code-block:: text

   WorkflowError: Invalid transition from 'idle' to 'completed'

**Solutions:**

1. Check workflow state transitions:

   .. code-block:: python

      # Debug workflow state
      print(f"Current state: {workflow.status_machine.current_status}")
      print(f"Valid transitions: {workflow.status_machine.get_valid_transitions()}")

2. Verify trigger conditions:

   .. code-block:: python

      # Check trigger status
      for trigger in workflow.status_machine.triggers:
          print(f"Trigger {trigger.name}: {trigger.is_active()}")

Performance Issues
~~~~~~~~~~~~~~~~~

**Issue: Simulation runs slowly**

**Diagnostic steps:**

1. Enable performance profiling:

   .. code-block:: python

      from airfogsim.utils.profiler import SimulationProfiler
      
      profiler = SimulationProfiler(env)
      profiler.start()
      
      # Run simulation
      env.run(until=1000)
      
      # Get performance report
      report = profiler.get_report()
      print(report)

2. Check event queue size:

   .. code-block:: python

      print(f"Event queue size: {len(env._queue)}")
      print(f"Events per second: {env.event_count / env.now}")

**Solutions:**

1. Reduce event frequency:

   .. code-block:: python

      # Increase agent decision intervals
      agent.decision_interval = 60.0  # Reduce from default

2. Enable performance optimizations:

   .. code-block:: python

      env.config.update({
          'event_batching': True,
          'spatial_indexing': True,
          'parallel_agents': True
      })

**Issue: Memory usage grows continuously**

**Diagnostic steps:**

1. Monitor memory usage:

   .. code-block:: python

      import psutil
      import gc
      
      def check_memory():
          process = psutil.Process()
          memory_mb = process.memory_info().rss / 1024 / 1024
          print(f"Memory usage: {memory_mb:.1f} MB")
          print(f"Objects in memory: {len(gc.get_objects())}")

**Solutions:**

1. Limit state history:

   .. code-block:: python

      # Configure agents to limit history
      agent.max_state_history = 100

2. Enable garbage collection:

   .. code-block:: python

      env.config['garbage_collection_interval'] = 1000

Data Provider Issues
~~~~~~~~~~~~~~~~~~~

**Issue: External API failures**

.. code-block:: text

   ConnectionError: Failed to connect to weather API

**Solutions:**

1. Implement fallback data:

   .. code-block:: python

      class RobustWeatherProvider(WeatherDataProvider):
          def _fetch_weather_data(self):
              try:
                  return super()._fetch_weather_data()
              except Exception as e:
                  self.logger.warning(f"API failed, using fallback: {e}")
                  return self._get_fallback_data()

2. Add retry logic:

   .. code-block:: python

      import time
      
      def fetch_with_retry(self, max_retries=3):
          for attempt in range(max_retries):
              try:
                  return self._fetch_data()
              except Exception as e:
                  if attempt == max_retries - 1:
                      raise
                  time.sleep(2 ** attempt)  # Exponential backoff

**Issue: Data synchronization problems**

.. code-block:: text

   DataError: Timestamp mismatch in weather data

**Solutions:**

1. Synchronize data timestamps:

   .. code-block:: python

      def normalize_timestamp(self, data):
          # Convert to simulation time
          data['timestamp'] = self.env.now
          return data

2. Validate data consistency:

   .. code-block:: python

      def validate_data(self, data):
          required_fields = ['timestamp', 'temperature', 'humidity']
          for field in required_fields:
              if field not in data:
                  raise ValueError(f"Missing field: {field}")

Debugging Techniques
-------------------

Logging and Diagnostics
~~~~~~~~~~~~~~~~~~~~~~

1. **Enable detailed logging:**

   .. code-block:: python

      import logging
      
      # Configure logging
      logging.basicConfig(level=logging.DEBUG)
      logger = logging.getLogger('airfogsim')
      logger.setLevel(logging.DEBUG)

2. **Add custom debug information:**

   .. code-block:: python

      class DebuggableAgent(Agent):
          def _decision_logic(self):
              while True:
                  self.logger.debug(f"Agent {self.id} making decision at {self.env.now}")
                  self.logger.debug(f"Current state: {self.get_all_states()}")
                  
                  # Decision logic here
                  yield self.env.timeout(30)

3. **Use simulation checkpoints:**

   .. code-block:: python

      def create_checkpoint(env, filename):
          """Save simulation state for debugging."""
          import pickle
          
          state = {
              'time': env.now,
              'agents': [(a.id, a.get_all_states()) for a in env.agents],
              'events': list(env._queue)
          }
          
          with open(filename, 'wb') as f:
              pickle.dump(state, f)

Interactive Debugging
~~~~~~~~~~~~~~~~~~~~

1. **Use simulation stepping:**

   .. code-block:: python

      def debug_simulation(env, step_size=10):
          """Run simulation in debug mode with stepping."""
          current_time = 0
          
          while current_time < 1000:
              print(f"\n--- Time: {current_time} ---")
              
              # Print agent states
              for agent in env.agents:
                  print(f"Agent {agent.id}: {agent.get_state('status')}")
              
              # Run for step_size time units
              env.run(until=current_time + step_size)
              current_time += step_size
              
              # Wait for user input
              input("Press Enter to continue...")

2. **Add breakpoints in workflows:**

   .. code-block:: python

      class DebuggableWorkflow(Workflow):
          def _setup_transitions(self):
              super()._setup_transitions()
              
              # Add debug callback to all transitions
              for transition in self.status_machine.transitions:
                  original_callback = transition.callback
                  transition.callback = lambda ctx: self._debug_transition(ctx, original_callback)
          
          def _debug_transition(self, context, original_callback):
              print(f"Workflow {self.name} transitioning: {context}")
              if original_callback:
                  return original_callback(context)

Testing and Validation
----------------------

Unit Testing
~~~~~~~~~~~

1. **Test agent behavior:**

   .. code-block:: python

      import pytest
      from airfogsim.core.environment import Environment
      from airfogsim.agent.drone import DroneAgent
      
      def test_agent_creation():
          env = Environment()
          drone = DroneAgent(env, "test_drone", properties={
              'position': (0, 0, 100),
              'battery_level': 100
          })
          
          assert drone.id == "test_drone"
          assert drone.get_state('position') == (0, 0, 100)
          assert drone.get_state('battery_level') == 100

2. **Test component functionality:**

   .. code-block:: python

      def test_component_execution():
          env = Environment()
          drone = DroneAgent(env, "test_drone")
          component = MoveToComponent(env, drone)
          
          # Test component metrics
          assert 'speed' in component.PRODUCED_METRICS
          
          # Test task execution
          task = MoveToTask(env, {'target_position': (100, 100, 100)})
          result = component.execute_task(task)
          assert task.status == TaskStatus.COMPLETED

Integration Testing
~~~~~~~~~~~~~~~~~~

1. **Test complete workflows:**

   .. code-block:: python

      def test_inspection_workflow():
          env = Environment()
          
          # Setup complete scenario
          drone = DroneAgent(env, "test_drone")
          drone.add_component(MoveToComponent(env, drone))
          env.register_agent(drone)
          
          # Create workflow
          workflow = InspectionWorkflow(
              env, "test_inspection", drone,
              properties={'inspection_points': [(100, 100, 100)]}
          )
          workflow.start()
          
          # Run simulation
          env.run(until=500)
          
          # Verify results
          assert workflow.status_machine.current_status == 'completed'

Common Error Messages
--------------------

**"Agent not found in environment"**
   - Ensure agent is registered: ``env.register_agent(agent)``

**"Component not available for agent"**
   - Add component: ``agent.add_component(component)``

**"Invalid workflow state transition"**
   - Check workflow state machine definition
   - Verify trigger conditions are met

**"Resource allocation failed"**
   - Check resource availability
   - Verify resource manager configuration

**"Task execution timeout"**
   - Increase task timeout values
   - Check for infinite loops in task logic

**"Memory allocation error"**
   - Reduce simulation scale
   - Enable memory optimization settings

Getting Help
-----------

When reporting issues:

1. **Provide minimal reproduction case:**

   .. code-block:: python

      # Minimal example that reproduces the issue
      from airfogsim.core.environment import Environment
      
      env = Environment()
      # Add minimal code that causes the problem

2. **Include system information:**

   .. code-block:: python

      import sys
      import airfogsim
      
      print(f"Python version: {sys.version}")
      print(f"AirFogSim version: {airfogsim.__version__}")
      print(f"Platform: {sys.platform}")

3. **Provide error logs:**
   - Include complete error traceback
   - Add relevant log messages
   - Specify when the error occurs

4. **Check existing resources:**
   - Search GitHub issues
   - Review documentation
   - Check examples for similar use cases

For more help:

- **GitHub Issues**: https://github.com/ZhiweiWei-NAMI/AirFogSim/issues
- **Documentation**: :doc:`../index`
- **Examples**: :doc:`../examples`
