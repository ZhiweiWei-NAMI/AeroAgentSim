## AeroAgentSim Data Provider Framework Documentation

This document explains the Data Provider framework in AeroAgentSim, focusing on how external data sources can be integrated into the simulation environment to create more realistic and dynamic scenarios.

### 1. Overview

The Data Provider framework in AeroAgentSim enables the integration of external data sources (such as weather, traffic, and accidents) into the simulation environment. This allows for more realistic and dynamic scenarios where agents can react to changing environmental conditions.

*   **`DataProvider`:** Abstract base class for external data providers. It defines the common interface for loading data and triggering events within the simulation environment.

*   **`DataIntegration`:** Abstract base class for data integration modules. It acts as a bridge between DataProviders and the simulation, handling the registration, configuration, and event subscription logic.

*   **Concrete Implementations:** AeroAgentSim includes several concrete implementations of DataProviders, such as `WeatherDataProvider`, `TrafficDataProvider`, and `AccidentDataProvider`.

### 2. Core Concepts

#### 2.1 `DataProvider` Abstract Base Class

The `DataProvider` class defines the common interface for all data providers:

```python
class DataProvider(ABC):
    def __init__(self, env, config=None):
        self.env = env
        self.config = config if config is not None else {}
        
    @abstractmethod
    def load_data(self):
        """Load the external data required by this provider."""
        pass
        
    @abstractmethod
    def start_event_triggering(self):
        """Start the process(es) that will trigger events based on the loaded data."""
        pass
```

* **Initialization:** Takes a simulation environment and an optional configuration dictionary.
* **`load_data()`:** Abstract method that loads external data from files, databases, or APIs.
* **`start_event_triggering()`:** Abstract method that starts the process(es) for triggering events based on the loaded data.

#### 2.2 `DataIntegration` Abstract Base Class

The `DataIntegration` class acts as a bridge between DataProviders and the simulation:

```python
class DataIntegration(ABC):
    def __init__(self, env, config=None):
        self.env = env
        self.config = config or {}
        self.default_config = {}
        self.config = {**self.default_config, **self.config}
        self._initialize_provider()
        self._register_event_listeners()
        
    @abstractmethod
    def _initialize_provider(self):
        """Initialize the data provider(s) used by this integration."""
        pass
        
    @abstractmethod
    def _register_event_listeners(self):
        """Register event listeners for the data provider(s)."""
        pass
```

* **Initialization:** Takes a simulation environment and an optional configuration dictionary.
* **`_initialize_provider()`:** Abstract method that initializes the data provider(s) used by this integration.
* **`_register_event_listeners()`:** Abstract method that registers event listeners for the data provider(s).

### 3. Concrete Implementations

#### 3.1 Weather Data Provider

The `WeatherDataProvider` provides weather data and triggers weather-related events in the simulation:

```python
class WeatherDataProvider(DataProvider):
    EVENT_WEATHER_CHANGED = 'WeatherChanged'
    
    def __init__(self, env, config=None):
        super().__init__(env, config)
        self.api_key = self.config.get('api_key')
        self.location = self.config.get('location')
        self.update_interval = self.config.get('update_interval', 3600)
        self.api_refresh_interval = self.config.get('api_refresh_interval', 3600)
        # ...
        
    def load_data(self):
        """Load weather data from OpenWeatherMap API."""
        # ...
        
    def start_event_triggering(self):
        """Start the process for triggering weather events."""
        # ...
```

* **Configuration:**
  * `api_key`: OpenWeatherMap API key.
  * `location`: Dictionary with `lat` and `lon` keys.
  * `update_interval`: How often the provider checks for the next event in simulation time (default: 3600 seconds).
  * `api_refresh_interval`: How often to re-fetch data from the API in real seconds (default: 3600 seconds).

* **Events:**
  * `WeatherChanged`: Triggered when weather conditions change.

#### 3.2 Traffic Data Provider

The `TrafficDataProvider` provides ground vehicle traffic data and triggers traffic-related events in the simulation:

```python
class TrafficDataProvider(DataProvider):
    def __init__(self, env, config=None):
        super().__init__(env, config)
        self.source = self.config.get('source')
        self.update_interval = float(env.visual_interval)
        # ...
        
    def load_data(self):
        """Load traffic data from the configured source."""
        # ...
        
    def start_event_triggering(self):
        """Start the process for triggering traffic events."""
        # ...
        
    def get_all_vehicle_states(self):
        """Get the complete state of all vehicles."""
        # ...
```

* **Configuration:**
  * `source`: Data source type, either 'sumo' or 'file'.
  * `update_interval`: Update interval in seconds (default: environment's visual_interval).
  * `sumo_config`: Configuration for SUMO data source (required when source='sumo').
  * `file_config`: Configuration for file data source (required when source='file').
  * `center_coordinates`: Center coordinates for coordinate conversion (optional but recommended for SUMO).

* **Events:**
  * `TrafficUpdate`: Triggered when vehicle positions are updated.

#### 3.3 Weather Integration

The `WeatherIntegration` class integrates weather data into the simulation:

```python
class WeatherIntegration(DataIntegration):
    def __init__(self, env, config=None):
        self.default_config = {
            'location': {'lat': 31.2304, 'lon': 121.4737},  # Shanghai
            'api_refresh_interval': 1800,  # 30 minutes
            'simulation_start_time': None,
            'time_scale': 1.0,
            'use_mock_data': True,
        }
        # ...
        super().__init__(env, self.default_config)
        
    def _initialize_provider(self):
        """Initialize the weather data provider."""
        # ...
        
    def _register_event_listeners(self):
        """Register event listeners for the weather data provider."""
        # ...
```

* **Configuration:**
  * `location`: Dictionary with `lat` and `lon` keys.
  * `api_refresh_interval`: How often to re-fetch data from the API in real seconds.
  * `simulation_start_time`: Simulation start time (real time).
  * `time_scale`: Time scale ratio (simulation time / real time).
  * `use_mock_data`: Whether to use mock data instead of real API data.

### 4. Usage Examples

#### 4.1 Using Weather Data Provider

```python
from aeroagentsim.core.environment import Environment
from aeroagentsim.dataprovider.weather import WeatherDataProvider

# Create simulation environment
env = Environment()

# Configure WeatherDataProvider
weather_config = {
    'api_key': 'your_openweathermap_api_key',
    'location': {'lat': 40.7128, 'lon': -74.0060},  # New York City
    'api_refresh_interval': 1800  # Refresh every 30 minutes (real time)
}

# Create and register WeatherDataProvider
weather_provider = WeatherDataProvider(env, config=weather_config)

# Subscribe to weather events
def handle_weather_event(subscriber_id, event_data):
    sim_time = event_data.get('sim_timestamp', 'N/A')
    severity = event_data.get('severity', 'N/A')
    condition = event_data.get('condition', 'N/A')
    temp = event_data.get('temperature', 'N/A')
    print(f"Weather changed at {sim_time}: {condition}, {temp}°C, Severity: {severity}")

env.event_registry.subscribe(
    source_id=WeatherDataProvider.__name__,
    listener_id="WeatherListener",
    event_name=WeatherDataProvider.EVENT_WEATHER_CHANGED,
    callback=handle_weather_event
)

# Start event triggering
weather_provider.start_event_triggering()

# Run simulation
env.run(until=3600)  # Run for 1 hour
```

#### 4.2 Using Traffic Data Provider with SUMO

```python
from aeroagentsim.core.environment import Environment
from aeroagentsim.dataprovider.traffic import TrafficDataProvider
from aeroagentsim.examples.run_traffic_simulation import start_sumo

# Start SUMO
sumo_process = start_sumo("sumo_configs/simple.sumocfg", port=8813, gui=True)

# Create simulation environment
env = Environment()

# Configure TrafficDataProvider
traffic_config = {
    'source': 'sumo',
    'update_interval': 1.0,
    'sumo_config': {
        'host': 'localhost',
        'port': 8813
    }
}

# Create and register TrafficDataProvider
traffic_provider = TrafficDataProvider(env, traffic_config)
env.add_data_provider('traffic', traffic_provider)

# Load data and start event triggering
traffic_provider.load_data()
traffic_provider.start_event_triggering()

# Run simulation
env.run(until=60)  # Run for 60 seconds

# Clean up resources
traffic_provider.close()
sumo_process.terminate()
```

#### 4.3 Using Traffic Data Provider with File Source

```python
from aeroagentsim.core.environment import Environment
from aeroagentsim.dataprovider.traffic import TrafficDataProvider

# Create simulation environment
env = Environment()

# Configure TrafficDataProvider
traffic_config = {
    'source': 'file',
    'file_config': {
        'path': 'data/vehicle_trajectories.csv',
        'format': 'csv',
        'time_column': 'timestamp',
        'id_column': 'vehicle_id',
        'pos_columns': ['x', 'y', 'z']
    }
}

# Create and register TrafficDataProvider
traffic_provider = TrafficDataProvider(env, traffic_config)
env.add_data_provider('traffic', traffic_provider)

# Load data and start event triggering
traffic_provider.load_data()
traffic_provider.start_event_triggering()

# Run simulation
env.run(until=60)  # Run for 60 seconds
```

#### 4.4 Using Weather Integration

```python
from aeroagentsim.core.environment import Environment
from aeroagentsim.dataprovider.weather_integration import WeatherIntegration

# Create simulation environment
env = Environment()

# Configure and create WeatherIntegration
weather_config = {
    'location': {'lat': 31.2304, 'lon': 121.4737},  # Shanghai
    'use_mock_data': True  # Use mock data instead of real API data
}
weather_integration = WeatherIntegration(env, weather_config)

# Run simulation
env.run(until=3600)  # Run for 1 hour
```

### 5. Creating Custom Data Providers

To create a custom data provider, you need to:

1. **Inherit from `DataProvider`:**
   ```python
   from aeroagentsim.core.dataprovider import DataProvider
   
   class CustomDataProvider(DataProvider):
       EVENT_CUSTOM_UPDATE = 'CustomUpdate'
       
       def __init__(self, env, config=None):
           super().__init__(env, config)
           # Initialize your data provider
           
       def load_data(self):
           # Load data from external source
           
       def start_event_triggering(self):
           # Start the process for triggering events
   ```

2. **Implement the required methods:**
   * `load_data()`: Load data from external source.
   * `start_event_triggering()`: Start the process for triggering events.

3. **Define event names as class constants:**
   * `EVENT_CUSTOM_UPDATE = 'CustomUpdate'`

4. **Trigger events using the environment's event registry:**
   ```python
   self.env.event_registry.trigger_event(
       source_id=self.__class__.__name__,
       event_name=self.EVENT_CUSTOM_UPDATE,
       event_data={
           'sim_timestamp': self.env.now,
           'custom_data': custom_data
       }
   )
   ```

### 6. Best Practices

1. **Use the environment's event registry for event triggering and subscription:**
   * Trigger events using `env.event_registry.trigger_event()`.
   * Subscribe to events using `env.event_registry.subscribe()`.

2. **Register data providers with the environment:**
   * Use `env.add_data_provider(key, provider)` to register data providers.
   * Access registered data providers using `env.get_data_provider(key)`.

3. **Handle API keys and sensitive data securely:**
   * Store API keys in environment variables.
   * Use masked keys in logs and debug output.

4. **Implement proper cleanup:**
   * Implement a `close()` method to clean up resources.
   * Call `close()` when the simulation ends.

5. **Use mock data for testing and development:**
   * Implement a mock data provider for testing and development.
   * Use the `use_mock_data` configuration option to switch between real and mock data.

### 7. Conclusion

The Data Provider framework in AeroAgentSim enables the integration of external data sources into the simulation environment, allowing for more realistic and dynamic scenarios. By using the provided data providers or creating custom ones, you can enhance your simulations with real-world data and create more complex and realistic scenarios.
