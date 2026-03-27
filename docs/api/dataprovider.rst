DataProvider Classes
====================

DataProviders integrate external data sources into AirFogSim simulations, enabling realistic scenarios with real-world data.

Base Classes
------------

DataProvider
~~~~~~~~~~~~

.. autoclass:: airfogsim.core.dataprovider.DataProvider
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

DataIntegration
~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.core.dataprovider.DataIntegration
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Weather Data Providers
-----------------------

WeatherDataProvider
~~~~~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.dataprovider.weather.WeatherDataProvider
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

WeatherIntegration
~~~~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.dataprovider.weather_integration.WeatherIntegration
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Traffic Data Providers
-----------------------

TrafficDataProvider
~~~~~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.dataprovider.traffic.TrafficDataProvider
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Signal Data Providers
----------------------

SignalDataProvider
~~~~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.dataprovider.signal.SignalDataProvider
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

SignalSource
~~~~~~~~~~~~

.. autoclass:: airfogsim.dataprovider.signal.SignalSource
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

ExternalSignalSourceIntegration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.dataprovider.signal_integration.ExternalSignalSourceIntegration
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

DataProvider Development Guide
------------------------------

Creating Custom DataProviders
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To create a custom data provider, inherit from the base DataProvider class and implement the required abstract methods:

.. code-block:: python

   from airfogsim.core.dataprovider import DataProvider

   class CustomDataProvider(DataProvider):
       """Custom data provider example."""

       def __init__(self, env, config=None):
           super().__init__(env, config)

           # Provider-specific configuration
           self.update_interval = self.config.get('update_interval', 300)
           self.data_source = self.config.get('data_source', 'file')

           # Data storage
           self.current_data = {}

       def load_data(self):
           """Load the external data required by this provider."""
           # Implement data loading logic here
           # This could involve reading from files, databases, or APIs
           pass

       def start_event_triggering(self):
           """Start the process that will trigger events based on loaded data."""
           # Implement event triggering logic here
           # This typically starts one or more SimPy processes
           pass

Usage Examples
~~~~~~~~~~~~~~

Weather Data Provider
^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   from airfogsim.core.environment import Environment
   from airfogsim.dataprovider.weather import WeatherDataProvider

   # Create environment
   env = Environment()

   # Configure weather provider
   weather_config = {
       'api_key': 'your_openweathermap_api_key',
       'location': {'lat': 40.7128, 'lon': -74.0060},  # New York
       'update_interval': 3600  # 1 hour
   }

   # Create and initialize weather provider
   weather_provider = WeatherDataProvider(env, weather_config)
   weather_provider.load_data()
   weather_provider.start_event_triggering()

Signal Data Provider
^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   from airfogsim.dataprovider.signal import SignalDataProvider, SignalSource

   # Create signal provider
   signal_config = {
       'propagation_model': 'free_space',
       'default_noise_floor': -100.0
   }
   signal_provider = SignalDataProvider(env, signal_config)

   # Add signal sources
   signal_source = SignalSource(
       source_id='transmitter_1',
       position=(100, 200, 50),
       center_frequency=2.4e9,  # 2.4 GHz
       transmit_power=20.0,     # 20 dBm
       bandwidth=20e6           # 20 MHz
   )
   signal_provider.add_signal_source(signal_source)

Traffic Data Provider
^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   from airfogsim.dataprovider.traffic import TrafficDataProvider

   # SUMO integration
   sumo_config = {
       'source': 'sumo',
       'update_interval': 1.0,
       'sumo_config': {
           'host': 'localhost',
           'port': 8813
       }
   }
   traffic_provider = TrafficDataProvider(env, sumo_config)

   # File-based traffic data
   file_config = {
       'source': 'file',
       'file_config': {
           'path': 'traffic_data.csv',
           'time_column': 'timestamp',
           'id_column': 'vehicle_id',
           'pos_columns': ['x', 'y', 'z']
       }
   }
   traffic_provider = TrafficDataProvider(env, file_config)

Integration Examples
~~~~~~~~~~~~~~~~~~~~

Weather Integration
^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   from airfogsim.dataprovider.weather_integration import WeatherIntegration

   # Create weather integration
   weather_config = {
       'api_key': 'your_api_key',
       'location': {'lat': 40.7128, 'lon': -74.0060},
       'update_interval': 3600
   }
   weather_integration = WeatherIntegration(env, weather_config)

Signal Integration
^^^^^^^^^^^^^^^^^^

.. code-block:: python

   from airfogsim.dataprovider.signal_integration import ExternalSignalSourceIntegration

   # Create signal integration
   signal_config = {
       'external_sources': [
           {
               'source_id': 'radar_1',
               'position': [1000, 2000, 100],
               'frequency': 10e9,  # 10 GHz
               'power': 30.0       # 30 dBm
           }
       ]
   }
   signal_integration = ExternalSignalSourceIntegration(env, signal_config)

For more detailed examples and integration patterns, see the examples directory.
