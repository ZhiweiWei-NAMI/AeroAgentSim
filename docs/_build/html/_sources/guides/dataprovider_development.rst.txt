DataProvider Development Guide
===============================

This guide covers how to create custom data providers that integrate external data sources into AeroAgentSim simulations.

Overview
--------

DataProviders are responsible for integrating external data sources (weather, traffic, sensor data) into the simulation environment. They provide real-time data updates and can trigger events based on data changes.

Key Concepts
------------

DataProvider Architecture
~~~~~~~~~~~~~~~~~~~~~~~~~

Every DataProvider in AeroAgentSim:

- **Manages external data sources** (APIs, files, databases)
- **Provides real-time data updates** to simulation components
- **Triggers events** when significant data changes occur
- **Handles data caching and refresh** for performance optimization
- **Integrates with simulation time** for synchronized updates

Base DataProvider Class
~~~~~~~~~~~~~~~~~~~~~~~

All data providers inherit from the base ``DataProvider`` class:

.. code-block:: python

   from aeroagentsim.core.dataprovider import DataProvider
   
   class CustomDataProvider(DataProvider):
       """Custom data provider implementation."""
       
       def __init__(self, env, config=None):
           super().__init__(env, config)
           
       def load_data(self):
           """Load initial data from external source."""
           pass
           
       def start_event_triggering(self):
           """Start automatic event triggering."""
           pass

Creating a Custom DataProvider
------------------------------

Step 1: Define DataProvider Class
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from aeroagentsim.core.dataprovider import DataProvider
   import requests
   import json
   
   class TrafficDataProvider(DataProvider):
       """Provider for real-time traffic data."""
       
       def __init__(self, env, config=None):
           super().__init__(env, config)
           self.api_key = config.get('api_key', '')
           self.api_url = config.get('api_url', 'https://api.traffic.com/v1/data')
           self.update_interval = config.get('update_interval', 300)  # 5 minutes
           self.locations = config.get('locations', [])
           self.traffic_data = {}
           self.last_update = 0

Step 2: Implement Data Loading
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def load_data(self):
       """Load initial traffic data from API."""
       try:
           for location in self.locations:
               location_id = location.get('id')
               lat = location.get('lat')
               lon = location.get('lon')
               
               # Fetch traffic data from API
               traffic_info = self._fetch_traffic_data(lat, lon)
               self.traffic_data[location_id] = traffic_info
               
           self.logger.info(f"Loaded traffic data for {len(self.locations)} locations")
           
       except Exception as e:
           self.logger.error(f"Failed to load traffic data: {e}")
           # Use mock data as fallback
           self._load_mock_data()
   
   def _fetch_traffic_data(self, lat, lon):
       """Fetch traffic data from external API."""
       params = {
           'lat': lat,
           'lon': lon,
           'api_key': self.api_key
       }
       
       response = requests.get(self.api_url, params=params, timeout=10)
       response.raise_for_status()
       
       data = response.json()
       return {
           'congestion_level': data.get('congestion', 0.0),
           'average_speed': data.get('avg_speed', 50.0),
           'incident_count': data.get('incidents', 0),
           'timestamp': self.env.now
       }
   
   def _load_mock_data(self):
       """Load mock traffic data for testing."""
       for location in self.locations:
           location_id = location.get('id')
           self.traffic_data[location_id] = {
               'congestion_level': 0.3,
               'average_speed': 45.0,
               'incident_count': 0,
               'timestamp': self.env.now
           }

Step 3: Implement Event Triggering
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def start_event_triggering(self):
       """Start automatic traffic data updates and event triggering."""
       self.env.process(self._update_loop())
   
   def _update_loop(self):
       """Main update loop for traffic data."""
       while True:
           try:
               # Wait for next update interval
               yield self.env.timeout(self.update_interval)
               
               # Update traffic data
               self._update_traffic_data()
               
               # Check for significant changes and trigger events
               self._check_traffic_changes()
               
           except Exception as e:
               self.logger.error(f"Error in traffic update loop: {e}")
               yield self.env.timeout(60)  # Wait 1 minute before retry
   
   def _update_traffic_data(self):
       """Update traffic data from external source."""
       old_data = self.traffic_data.copy()
       
       for location in self.locations:
           location_id = location.get('id')
           lat = location.get('lat')
           lon = location.get('lon')
           
           try:
               # Fetch updated traffic data
               new_data = self._fetch_traffic_data(lat, lon)
               self.traffic_data[location_id] = new_data
               
           except Exception as e:
               self.logger.warning(f"Failed to update traffic for {location_id}: {e}")
               # Keep old data if update fails
       
       self.last_update = self.env.now

Step 4: Implement Change Detection
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def _check_traffic_changes(self):
       """Check for significant traffic changes and trigger events."""
       for location_id, current_data in self.traffic_data.items():
           # Check for high congestion
           congestion = current_data.get('congestion_level', 0.0)
           if congestion > 0.8:
               self.trigger_event('high_congestion', {
                   'location_id': location_id,
                   'congestion_level': congestion,
                   'timestamp': self.env.now
               })
           
           # Check for traffic incidents
           incidents = current_data.get('incident_count', 0)
           if incidents > 0:
               self.trigger_event('traffic_incident', {
                   'location_id': location_id,
                   'incident_count': incidents,
                   'timestamp': self.env.now
               })
           
           # Check for speed changes
           speed = current_data.get('average_speed', 50.0)
           if speed < 20.0:  # Very slow traffic
               self.trigger_event('slow_traffic', {
                   'location_id': location_id,
                   'average_speed': speed,
                   'timestamp': self.env.now
               })

Step 5: Implement Data Access Methods
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def get_traffic_data(self, location_id=None):
       """Get current traffic data for a location or all locations."""
       if location_id:
           return self.traffic_data.get(location_id, {})
       return self.traffic_data.copy()
   
   def get_congestion_level(self, location_id):
       """Get current congestion level for a specific location."""
       data = self.traffic_data.get(location_id, {})
       return data.get('congestion_level', 0.0)
   
   def get_average_speed(self, location_id):
       """Get current average speed for a specific location."""
       data = self.traffic_data.get(location_id, {})
       return data.get('average_speed', 50.0)
   
   def is_data_fresh(self, max_age=600):
       """Check if traffic data is fresh (within max_age seconds)."""
       return (self.env.now - self.last_update) <= max_age

Advanced Features
-----------------

Data Caching and Performance
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def __init__(self, env, config=None):
       super().__init__(env, config)
       self.cache_duration = config.get('cache_duration', 300)
       self.cached_data = {}
       self.cache_timestamps = {}
   
   def _get_cached_data(self, location_id):
       """Get cached data if still valid."""
       if location_id in self.cached_data:
           cache_time = self.cache_timestamps.get(location_id, 0)
           if (self.env.now - cache_time) < self.cache_duration:
               return self.cached_data[location_id]
       return None
   
   def _cache_data(self, location_id, data):
       """Cache data with timestamp."""
       self.cached_data[location_id] = data
       self.cache_timestamps[location_id] = self.env.now

Data Validation and Error Handling
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def _validate_traffic_data(self, data):
       """Validate traffic data before using."""
       required_fields = ['congestion_level', 'average_speed', 'incident_count']
       
       for field in required_fields:
           if field not in data:
               raise ValueError(f"Missing required field: {field}")
       
       # Validate data ranges
       if not 0.0 <= data['congestion_level'] <= 1.0:
           raise ValueError("Congestion level must be between 0.0 and 1.0")
       
       if data['average_speed'] < 0:
           raise ValueError("Average speed cannot be negative")
       
       if data['incident_count'] < 0:
           raise ValueError("Incident count cannot be negative")
       
       return True

DataProvider Integration
------------------------

Using Your Custom DataProvider
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from aeroagentsim.core.environment import Environment
   from your_module import TrafficDataProvider
   
   # Create environment
   env = Environment()
   
   # Configure traffic data provider
   traffic_config = {
       'api_key': 'your_api_key_here',
       'api_url': 'https://api.traffic.com/v1/data',
       'update_interval': 300,  # Update every 5 minutes
       'locations': [
           {'id': 'downtown', 'lat': 40.7128, 'lon': -74.0060},
           {'id': 'airport', 'lat': 40.6892, 'lon': -74.1745},
           {'id': 'highway_1', 'lat': 40.7589, 'lon': -73.9851}
       ]
   }
   
   # Create and initialize traffic provider
   traffic_provider = TrafficDataProvider(env, traffic_config)
   traffic_provider.load_data()
   traffic_provider.start_event_triggering()
   
   # Register with environment
   env.traffic_provider = traffic_provider

Integrating with Agents
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Agents can subscribe to traffic events
   from aeroagentsim.agent.drone import DroneAgent
   
   drone = DroneAgent(env, "traffic_aware_drone", properties={
       'position': (40.7128, -74.0060, 100)
   })
   
   # Subscribe to traffic events
   drone.subscribe('traffic_provider', 'high_congestion', 
                   drone._handle_traffic_congestion)
   drone.subscribe('traffic_provider', 'traffic_incident',
                   drone._handle_traffic_incident)
   
   # Agents can also query traffic data directly
   def _plan_route(self):
       """Plan route considering current traffic conditions."""
       current_location = self.get_state('position')
       traffic_data = env.traffic_provider.get_traffic_data()
       
       # Use traffic data for route planning
       best_route = self._find_optimal_route(current_location, traffic_data)
       return best_route

Best Practices
--------------

Data Management
~~~~~~~~~~~~~~~

1. **Handle API failures gracefully** with fallback data sources
2. **Implement appropriate caching** to reduce API calls
3. **Validate data quality** before using in simulation
4. **Monitor data freshness** and handle stale data appropriately

Performance
~~~~~~~~~~~

1. **Use appropriate update intervals** to balance accuracy and performance
2. **Implement efficient data structures** for large datasets
3. **Cache frequently accessed data** to reduce computation
4. **Use asynchronous operations** where possible

Error Handling
~~~~~~~~~~~~~~

1. **Handle network failures** and API rate limits
2. **Provide fallback data** when external sources are unavailable
3. **Log errors appropriately** for debugging and monitoring
4. **Implement retry mechanisms** with exponential backoff

Testing
~~~~~~~

1. **Test with mock data** for development and testing
2. **Validate data provider behavior** under various conditions
3. **Test error conditions** and recovery mechanisms
4. **Monitor performance** with realistic data loads

For more information, see:

- :doc:`../api/dataprovider` - Complete DataProvider API reference
- :doc:`simulation_setup` - Integrating data providers in simulations
- :doc:`performance_tuning` - Optimizing data provider performance
