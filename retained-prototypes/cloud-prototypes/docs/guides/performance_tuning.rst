Performance Tuning Guide
========================

This guide covers techniques for optimizing ``aeroagentsim`` runtime performance
and the AeroAgentSim workbench experience for large-scale simulations and
resource-constrained environments.

Overview
--------

AeroAgentSim performance depends on several factors including the number of agents, event frequency, component complexity, and data provider update rates. This guide provides strategies for optimizing each aspect.

Performance Fundamentals
------------------------

Key Performance Metrics
~~~~~~~~~~~~~~~~~~~~~~~

Monitor these metrics to identify performance bottlenecks:

- **Events per second**: Rate of event processing
- **Memory usage**: RAM consumption over time
- **CPU utilization**: Processor usage patterns
- **Simulation time ratio**: Real time vs. simulation time
- **Agent update frequency**: How often agents make decisions

.. code-block:: python

   from aeroagentsim.utils.profiler import SimulationProfiler
   
   # Enable performance profiling
   profiler = SimulationProfiler(env)
   profiler.start()
   
   # Run simulation
   env.run(until=1000)
   
   # Get performance report
   report = profiler.get_report()
   print(f"Events/sec: {report['events_per_second']}")
   print(f"Memory usage: {report['peak_memory_mb']} MB")

Environment Optimization
------------------------

Current Public Tuning Surface
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from aeroagentsim.core.environment import Environment
   
   # Prefer explicit constructor arguments and component/provider intervals.
   env = Environment(visual_interval=10)

The current public API does not expose a documented ``Environment(config=...)``
dictionary for performance tuning. Prefer these levers instead:

* reduce ``visual_interval`` updates when you do not need frequent frontend pushes
* increase agent decision intervals and provider polling intervals
* avoid attaching unnecessary components or overly chatty state updates
* limit logging volume during large simulation runs
* inspect per-run artifacts in ``runtime/aeroagentsim/runs/<run_id>/`` instead
  of keeping extra in-memory debug state

Event System Optimization
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Optimize event triggering frequency
   class OptimizedAgent(Agent):
       def __init__(self, env, agent_name, properties=None):
           super().__init__(env, agent_name, properties)
           self.update_interval = 10.0  # Reduce update frequency
           self.last_state_update = 0
       
       def update_state(self, key, value):
           # Batch state updates to reduce event frequency
           current_time = self.env.now
           if current_time - self.last_state_update > self.update_interval:
               super().update_state(key, value)
               self.last_state_update = current_time
           else:
               # Store for batch update
               self._pending_updates[key] = value

Agent Optimization
------------------

Efficient Agent Design
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   class HighPerformanceAgent(Agent):
       """Optimized agent for large-scale simulations."""
       
       def __init__(self, env, agent_name, properties=None):
           super().__init__(env, agent_name, properties)
           
           # Optimize decision intervals
           self.decision_interval = properties.get('decision_interval', 30.0)
           self.last_decision_time = 0
           
           # Cache frequently accessed data
           self._cached_neighbors = []
           self._cache_timestamp = 0
           self._cache_duration = 60.0
       
       def _decision_logic(self):
           """Optimized decision logic with reduced frequency."""
           while True:
               try:
                   current_time = self.env.now
                   
                   # Only make decisions at specified intervals
                   if current_time - self.last_decision_time >= self.decision_interval:
                       self._make_decision()
                       self.last_decision_time = current_time
                   
                   # Use adaptive sleep based on activity level
                   sleep_time = self._calculate_adaptive_sleep()
                   yield self.env.timeout(sleep_time)
                   
               except Exception as e:
                   self.logger.error(f"Decision logic error: {e}")
                   yield self.env.timeout(60)  # Error recovery delay
       
       def _calculate_adaptive_sleep(self):
           """Calculate sleep time based on agent activity."""
           # Sleep longer when agent is idle
           if self.get_state('status') == 'idle':
               return 60.0
           elif self.get_state('status') == 'moving':
               return 10.0
           else:
               return 30.0

Memory Management
~~~~~~~~~~~~~~~~~

.. code-block:: python

   class MemoryEfficientAgent(Agent):
       """Agent optimized for memory usage."""
       
       def __init__(self, env, agent_name, properties=None):
           super().__init__(env, agent_name, properties)
           
           # Limit state history
           self.max_state_history = 100
           
           # Use weak references for non-critical data
           import weakref
           self._weak_references = weakref.WeakValueDictionary()
       
       def update_state(self, key, value):
           """Update state with memory management."""
           super().update_state(key, value)
           
           # Limit state history to prevent memory leaks
           if hasattr(self, '_state_history'):
               if len(self._state_history[key]) > self.max_state_history:
                   self._state_history[key] = self._state_history[key][-self.max_state_history:]
       
       def cleanup_resources(self):
           """Clean up resources to free memory."""
           # Clear old event subscriptions
           self._cleanup_old_subscriptions()
           
           # Clear cached data
           self._cached_data.clear()
           
           # Force garbage collection
           import gc
           gc.collect()

Component Optimization
----------------------

Efficient Component Design
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   class OptimizedComponent(Component):
       """High-performance component implementation."""
       
       def __init__(self, env, owner):
           super().__init__(env, owner)
           
           # Optimize metric calculation frequency
           self.metric_update_interval = 30.0
           self.last_metric_update = 0
           
           # Pre-allocate data structures
           self.metrics_buffer = {}
           self.task_queue = []
       
       def _update_metrics(self):
           """Optimized metrics calculation."""
           current_time = self.env.now
           
           # Only update metrics at specified intervals
           if current_time - self.last_metric_update < self.metric_update_interval:
               return
           
           # Batch metric calculations
           self._calculate_all_metrics()
           self.last_metric_update = current_time
       
       def execute_task(self, task):
           """Optimized task execution."""
           # Use object pooling for task objects
           if hasattr(self, '_task_pool') and self._task_pool:
               pooled_task = self._task_pool.pop()
               pooled_task.reset(task.properties)
               task = pooled_task
           
           # Execute with minimal overhead
           yield from self._fast_execute(task)
           
           # Return to pool
           if hasattr(self, '_task_pool'):
               task.cleanup()
               self._task_pool.append(task)

Resource Manager Optimization
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   class OptimizedResourceManager:
       """High-performance resource management."""
       
       def __init__(self, env):
           self.env = env
           
           # Use spatial indexing for fast lookups
           from rtree import index
           self.spatial_index = index.Index()
           
           # Cache frequently accessed resources
           self.resource_cache = {}
           self.cache_size_limit = 1000
       
       def find_nearest_resources(self, position, resource_type, count=1):
           """Optimized resource search using spatial indexing."""
           # Use spatial index for fast nearest neighbor search
           x, y, z = position
           search_radius = 100.0
           
           # Query spatial index
           candidates = list(self.spatial_index.nearest(
               (x-search_radius, y-search_radius, x+search_radius, y+search_radius),
               count * 2  # Get extra candidates for filtering
           ))
           
           # Filter by type and return closest
           filtered = [r for r in candidates if r.type == resource_type]
           return sorted(filtered, key=lambda r: self._distance(position, r.position))[:count]

Data Provider Optimization
--------------------------

Efficient Data Updates
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   class OptimizedDataProvider(DataProvider):
       """High-performance data provider."""
       
       def __init__(self, env, config=None):
           super().__init__(env, config)
           
           # Optimize update intervals
           self.min_update_interval = config.get('min_update_interval', 60.0)
           self.adaptive_updates = config.get('adaptive_updates', True)
           
           # Use data compression for large datasets
           self.compress_data = config.get('compress_data', True)
           
           # Implement data caching
           self.cache_size = config.get('cache_size', 10000)
           self.data_cache = {}
       
       def _adaptive_update_interval(self):
           """Calculate adaptive update interval based on data volatility."""
           if self.adaptive_updates:
               # Increase interval when data is stable
               volatility = self._calculate_data_volatility()
               if volatility < 0.1:
                   return self.min_update_interval * 2
               elif volatility > 0.5:
                   return self.min_update_interval * 0.5
           
           return self.min_update_interval
       
       def _compress_data(self, data):
           """Compress data to reduce memory usage."""
           if self.compress_data and len(str(data)) > 1000:
               import pickle
               import gzip
               return gzip.compress(pickle.dumps(data))
           return data

Large-Scale Simulation Strategies
---------------------------------

Agent Pooling
~~~~~~~~~~~~~

.. code-block:: python

   class AgentPool:
       """Pool of reusable agent objects for large-scale simulations."""
       
       def __init__(self, env, agent_class, pool_size=1000):
           self.env = env
           self.agent_class = agent_class
           self.available_agents = []
           self.active_agents = {}
           
           # Pre-create agent pool
           for i in range(pool_size):
               agent = agent_class(env, f"pooled_agent_{i}")
               agent.deactivate()
               self.available_agents.append(agent)
       
       def get_agent(self, agent_id, properties=None):
           """Get an agent from the pool."""
           if self.available_agents:
               agent = self.available_agents.pop()
               agent.reset(agent_id, properties)
               agent.activate()
               self.active_agents[agent_id] = agent
               return agent
           else:
               # Pool exhausted, create new agent
               return self.agent_class(self.env, agent_id, properties)
       
       def return_agent(self, agent_id):
           """Return an agent to the pool."""
           if agent_id in self.active_agents:
               agent = self.active_agents.pop(agent_id)
               agent.cleanup()
               agent.deactivate()
               self.available_agents.append(agent)

Hierarchical Simulation
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   class HierarchicalSimulation:
       """Multi-level simulation for handling large numbers of agents."""
       
       def __init__(self, env):
           self.env = env
           self.macro_agents = []  # High-level aggregate agents
           self.micro_agents = []  # Detailed individual agents
           self.detail_threshold = 1000  # Distance for detailed simulation
       
       def update_simulation_level(self, observer_position):
           """Switch between macro and micro simulation based on observer."""
           for agent in self.env.agents:
               distance = self._calculate_distance(observer_position, agent.position)
               
               if distance < self.detail_threshold:
                   # Use detailed micro simulation
                   if agent.simulation_level != 'micro':
                       self._switch_to_micro(agent)
               else:
                   # Use aggregate macro simulation
                   if agent.simulation_level != 'macro':
                       self._switch_to_macro(agent)

Monitoring and Profiling
------------------------

Performance Monitoring
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   class PerformanceMonitor:
       """Real-time performance monitoring."""
       
       def __init__(self, env):
           self.env = env
           self.metrics = {}
           self.start_time = time.time()
           
       def start_monitoring(self):
           """Start performance monitoring."""
           self.env.process(self._monitoring_loop())
       
       def _monitoring_loop(self):
           """Main monitoring loop."""
           while True:
               # Collect metrics
               self._collect_metrics()
               
               # Check for performance issues
               self._check_performance_thresholds()
               
               # Wait for next monitoring cycle
               yield self.env.timeout(60)  # Monitor every minute
       
       def _collect_metrics(self):
           """Collect current performance metrics."""
           import psutil
           
           self.metrics.update({
               'timestamp': self.env.now,
               'cpu_percent': psutil.cpu_percent(),
               'memory_percent': psutil.virtual_memory().percent,
               'agent_count': len(self.env.agents),
               'event_queue_size': len(self.env._queue),
               'simulation_speed': self._calculate_simulation_speed()
           })

Best Practices Summary
----------------------

1. **Reduce Event Frequency**
   - Batch state updates
   - Use appropriate update intervals
   - Implement adaptive timing

2. **Optimize Memory Usage**
   - Limit state history
   - Use object pooling
   - Implement garbage collection

3. **Use Spatial Indexing**
   - Enable spatial optimization
   - Use appropriate resolution
   - Implement efficient search algorithms

4. **Monitor Performance**
   - Track key metrics
   - Identify bottlenecks
   - Implement adaptive optimization

5. **Scale Appropriately**
   - Use hierarchical simulation
   - Implement level-of-detail
   - Consider distributed processing

For more information, see:

- :doc:`troubleshooting` - Performance troubleshooting
- :doc:`simulation_setup` - Efficient simulation configuration
- :doc:`../api/core` - Core performance APIs
