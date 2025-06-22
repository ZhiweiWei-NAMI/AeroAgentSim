# -*- coding: utf-8 -*-
from __future__ import annotations
import functools
from airfogsim.utils.logging_config import get_logger
import simpy
# import pandas as pd # No longer needed if only using API
from typing import TYPE_CHECKING, Dict, Any, Optional, List, Tuple
import time # For potential API refresh logic

from ..core.dataprovider import DataProvider
from airfogsim.core.enums import ResourceStatus
# Import the adapter
from .api_adapters.openweathermap_adapter import fetch_and_convert_weather_data

# Type hinting for Environment, Agent, Managers to avoid circular imports
if TYPE_CHECKING:
    from airfogsim.core.environment import Environment
    from airfogsim.core.agent import Agent
    from airfogsim.manager.landing import LandingManager
    from airfogsim.manager.frequency import FrequencyManager
    # Import other managers if needed for callbacks

# Configure logging
logger = get_logger(__name__)

class WeatherDataProvider(DataProvider):
    """
    Provides weather data and triggers weather-related events in the simulation.
    """
    EVENT_WEATHER_CHANGED = 'WeatherChanged'

    def __init__(self, env: 'Environment', config: Optional[Dict[str, Any]] = None):
        """
        Initialize the WeatherDataProvider.

        Args:
            env: The simulation environment instance.
            config (dict, optional): Configuration specific to this provider.
                                     Expected keys:
                                     - 'api_key': Your OpenWeatherMap API key.
                                     - 'location': Dict with 'lat' and 'lon'.
                                     - 'update_interval' (optional): How often the provider *checks* for the next event in sim time (default: 3600).
                                     - 'api_refresh_interval' (optional): How often to re-fetch data from the API in *real* seconds (default: 3600). Set to 0 or None to disable refresh.
                                     Defaults to None.
        """
        super().__init__(env, config)
        self.api_key = self.config.get('api_key')
        self.location = self.config.get('location')
        self.update_interval = self.config.get('update_interval', 3600) # Sim time interval for internal checks
        self.api_refresh_interval = self.config.get('api_refresh_interval', 3600) # Real time interval for API refresh

        if not self.api_key:
            logger.error("WeatherDataProvider requires 'api_key' in config.")
            raise ValueError("Missing 'api_key' in WeatherDataProvider config")
        if not self.location or 'lat' not in self.location or 'lon' not in self.location:
            logger.error("WeatherDataProvider requires 'location' with 'lat' and 'lon' in config.")
            raise ValueError("Missing or invalid 'location' in WeatherDataProvider config")

        # Store weather data indexed by simulation time (relative to start or last refresh)
        self._weather_schedule: Dict[float, Dict[str, Any]] = {}
        self._last_api_refresh_sim_time: float = -1.0 # Track sim time of last refresh

    def load_data(self):
        """
        Load weather data by fetching from the OpenWeatherMap API using the adapter.
        Populates the internal `_weather_schedule`.
        """
        logger.info(f"Loading weather data from OpenWeatherMap API for location: {self.location}")
        try:
            lat = self.location['lat']
            lon = self.location['lon']
            # Fetch and convert data using the adapter
            # The adapter returns a schedule with keys as relative sim time (seconds from now)
            api_schedule = fetch_and_convert_weather_data(self.api_key, lat, lon)

            if not api_schedule:
                logger.warning("Failed to fetch or convert weather data from API. Weather schedule will be empty.")
                self._weather_schedule = {}
                return

            # Store the fetched schedule. The keys are already relative simulation time offsets.
            self._weather_schedule = api_schedule
            self._last_api_refresh_sim_time = self.env.now # Record sim time of this load/refresh

            logger.info(f"Successfully loaded and processed {len(self._weather_schedule)} weather events from API.")
            # Log the first few events for debugging
            for i, (sim_time, event) in enumerate(self._weather_schedule.items()):
                 if i < 3:
                     logger.debug(f"  Schedule item {i}: Sim Time Key={sim_time:.2f}, Event Time UTC={event.get('timestamp_utc')}, Severity={event.get('severity')}")
                 else:
                     break

        except Exception as e:
            logger.error(f"Error during weather data loading from API: {e}", exc_info=True)
            self._weather_schedule = {} # Ensure schedule is empty on error

    def start_event_triggering(self):
        """
        Start the SimPy process(es) to trigger WeatherChanged events based on the schedule
        and optionally refresh data from the API periodically.
        """
        if not self._weather_schedule:
            # Attempt to load data if the schedule is empty (e.g., if load_data wasn't called explicitly)
            logger.warning("Weather schedule is empty. Attempting to load data now.")
            self.load_data()
            if not self._weather_schedule:
                 logger.error("Failed to load weather data. Cannot start event triggering.")
                 return

        # Start the main event triggering loop
        self.env.process(self._weather_update_loop())
        logger.info(f"{self.__class__.__name__} event triggering process started.")

        # Start the API refresh loop if interval is set
        if self.api_refresh_interval and self.api_refresh_interval > 0:
            self.env.process(self._api_refresh_loop())
            logger.info(f"{self.__class__.__name__} API refresh process started with interval {self.api_refresh_interval}s.")
        else:
            logger.info(f"{self.__class__.__name__} API refresh is disabled.")

    def _weather_update_loop(self):
        """
        SimPy process that triggers WeatherChanged events at scheduled times.
        """
        # The keys in _weather_schedule are relative simulation times from the last refresh
        # We need to convert them to absolute simulation times
        base_sim_time = self._last_api_refresh_sim_time if self._last_api_refresh_sim_time >= 0 else 0.0
        absolute_scheduled_times = sorted([base_sim_time + relative_time for relative_time in self._weather_schedule.keys()])

        last_triggered_abs_time = -1

        current_schedule_ref = self._weather_schedule # Keep a reference to the schedule used at the start of this loop instance

        for abs_trigger_time in absolute_scheduled_times:
            # Check if the schedule was refreshed while we were waiting
            if self._weather_schedule is not current_schedule_ref:
                 logger.info(f"Weather schedule refreshed during update loop. Restarting loop.")
                 # Restart the process to use the new schedule
                 # Note: This might cause a slight delay or skip an event if refresh happens exactly when an event should trigger
                 self.env.process(self._weather_update_loop())
                 return # Exit this instance of the loop

            relative_trigger_time = abs_trigger_time - base_sim_time

            # Ensure we only process future events relative to the current simulation time
            if abs_trigger_time >= self.env.now and abs_trigger_time > last_triggered_abs_time:
                try:
                    # Wait until the absolute scheduled event time
                    wait_duration = abs_trigger_time - self.env.now
                    if wait_duration > 0:
                         yield self.env.timeout(wait_duration)
                    elif wait_duration < 0:
                         # Should not happen if logic is correct, but log if it does
                         logger.warning(f"Attempting to trigger event in the past? abs_trigger_time={abs_trigger_time}, env.now={self.env.now}")
                         continue # Skip this event

                    # Re-check if schedule was refreshed while waiting
                    if self._weather_schedule is not current_schedule_ref:
                        logger.info(f"Weather schedule refreshed while waiting for event at {abs_trigger_time}. Restarting loop.")
                        self.env.process(self._weather_update_loop())
                        return

                    # Get the event data using the original relative time key
                    if relative_trigger_time in self._weather_schedule:
                        event_data = self._weather_schedule[relative_trigger_time].copy() # Use copy to avoid modification issues
                        event_data['sim_timestamp'] = self.env.now # Add current sim time to the event payload

                        logger.info(f"Triggering {self.EVENT_WEATHER_CHANGED} at sim_time {self.env.now:.2f} (scheduled for {abs_trigger_time:.2f})")
                        logger.debug(f"Event Data: {event_data}")
                        self.env.event_registry.trigger_event(
                            source_id=self.__class__.__name__, # Identify the source
                            event_name=self.EVENT_WEATHER_CHANGED,
                            event_value=event_data
                        )
                        last_triggered_abs_time = abs_trigger_time
                    else:
                         logger.warning(f"Scheduled relative time {relative_trigger_time} not found in current schedule after waiting. Skipping.")

                except simpy.Interrupt:
                     logger.info(f"Weather update loop interrupted (likely due to API refresh). Restarting.")
                     # The loop will be restarted by the refresh mechanism or manually if needed
                     return # Exit this instance
                except Exception as e:
                     logger.error(f"Error in weather update loop: {e}", exc_info=True)
                     # Decide how to handle errors, e.g., retry or stop? For now, just log and continue if possible.
                     # This might skip the problematic event.
                     last_triggered_abs_time = abs_trigger_time # Mark as processed to avoid infinite loops on error

            elif abs_trigger_time < self.env.now:
                 logger.debug(f"Skipping past weather event scheduled for absolute time {abs_trigger_time:.2f} (current time {self.env.now:.2f})")
            # else: # abs_trigger_time <= last_triggered_abs_time (already processed)
                 # logger.debug(f"Skipping already processed event at {abs_trigger_time}")

        logger.info("Weather update loop finished processing current schedule.")


    # --- Standard Callback Method ---

    def on_weather_changed(self, subscriber: Any, event_data: Dict[str, Any]):
        """
        Standard callback for WeatherChanged events.
        Modifies the subscriber's state based on weather conditions in their region.

        Args:
            subscriber: The object that subscribed to the event (e.g., Agent, LandingManager).
            event_data (dict): The data associated with the WeatherChanged event.
                               Expected keys: 'timestamp', 'region', 'wind_speed', etc.
        """
        # Import necessary classes locally to avoid top-level circular dependency issues if needed
        from airfogsim.core.agent import Agent
        from airfogsim.manager.landing import LandingManager
        from airfogsim.manager.frequency import FrequencyManager

        # Region data from API is now {'lat': float, 'lon': float, 'type': 'point'}
        # The is_in_region logic needs to handle this format.
        region_data = event_data.get('region', None)

        # --- Handle Agent Subscribers ---
        if isinstance(subscriber, Agent):
            agent = subscriber
            agent_pos = agent.get_state('position') # Assuming position is (x, y, z) or similar
            # Check if agent is affected by the weather event (based on location)
            # The default is_in_region might need adjustment based on how agent position relates to lat/lon
            if agent_pos and self.is_in_region(agent_pos, region_data):
                external_force = self._calculate_external_force(event_data)
                agent.update_states({'external_force': external_force})
                logger.debug(f"Agent {agent.id} external_force updated to {external_force} due to weather at {self.env.now:.2f}")

        # --- Handle LandingManager Subscribers ---
        elif isinstance(subscriber, LandingManager):
            landing_manager = subscriber
            # Determine the appropriate status based on weather severity
            severity = event_data.get('severity', 'NORMAL').upper()
            new_status = ResourceStatus.AVAILABLE # Default
            if severity in ['STORM', 'HEAVY_RAIN', 'HIGH_WINDS']: # Example severe conditions
                new_status = ResourceStatus.UNAVAILABLE_WEATHER
            elif severity == 'NORMAL':
                 # If weather becomes normal, potentially make resources available again
                 # Careful: Only revert UNAVAILABLE_WEATHER, not other unavailable states.
                 # LandingManager needs logic to handle this state transition safely.
                 pass # LandingManager needs a method like 'try_set_available_from_weather'

            # LandingManager needs a method to update resources in a region
            # We pass the full event_data in case the manager needs more context
            landing_manager.update_resource_status_by_region(
                region_data,
                new_status=new_status,
                condition_data=event_data
            )
            logger.debug(f"Landing resources potentially affected by weather update in region {region_data} at {self.env.now:.2f}. Status: {new_status}")

        # --- Handle FrequencyManager Subscribers ---
        elif isinstance(subscriber, FrequencyManager):
            frequency_manager = subscriber
            # FrequencyManager needs a method to update its models based on weather
            # Ensure the method can handle the event_data format from the API adapter
            frequency_manager.update_pathloss_parameters(event_data)
            logger.debug(f"Frequency pathloss parameters potentially updated due to weather at {self.env.now:.2f}")

        # --- Add other Manager types as needed ---
        # elif isinstance(subscriber, SomeOtherManager):
        #     pass

    # --- Helper Methods ---

    def _calculate_external_force(self, weather_data: Dict[str, Any]) -> List[float]:
        """Placeholder: Calculate external force vector based on weather data."""
        # TODO: Implement realistic physics based on wind speed, direction, agent properties (e.g., cross-section)
        wind_speed = weather_data.get('wind_speed', 0)
        # Simplified: Assume wind directly translates to force in x-direction
        force_x = wind_speed * 0.1 # Needs proper scaling factor
        return [force_x, 0.0, 0.0]

    def _convert_sim_coords_to_latlon(self, position: Tuple[float, float, float]) -> Tuple[float, float]:
        """
        Convert simulation coordinates (x, y, z) to latitude and longitude.

        This implementation assumes a simple conversion where:
        - The simulation origin (0, 0, 0) corresponds to the base location specified in config
        - x and y coordinates are in meters from the origin
        - 111,111 meters = 1 degree of latitude (approximate)
        - 111,111 * cos(latitude) meters = 1 degree of longitude (approximate)

        Args:
            position: Agent's position (x, y, z) in simulation coordinates (meters)

        Returns:
            Tuple of (latitude, longitude) in degrees
        """
        # Get the base location from config
        base_lat = self.location.get('lat', 0.0)
        base_lon = self.location.get('lon', 0.0)

        # Extract x and y from position
        x, y, _ = position

        # Convert meters to degrees
        # 1 degree of latitude is approximately 111,111 meters
        lat_offset = y / 111111.0

        # 1 degree of longitude varies with latitude
        # At the equator, 1 degree of longitude is approximately 111,111 meters
        # At higher latitudes, the distance decreases by cos(latitude)
        import math
        lon_offset = x / (111111.0 * math.cos(math.radians(base_lat)))

        # Calculate final latitude and longitude
        lat = base_lat + lat_offset
        lon = base_lon + lon_offset

        return (lat, lon)

    def _calculate_distance(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """
        Calculate the great-circle distance between two points on Earth.
        Uses the Haversine formula.

        Args:
            lat1, lon1: Latitude and longitude of first point in degrees
            lat2, lon2: Latitude and longitude of second point in degrees

        Returns:
            Distance in meters
        """
        import math

        # Convert degrees to radians
        lat1 = math.radians(lat1)
        lon1 = math.radians(lon1)
        lat2 = math.radians(lat2)
        lon2 = math.radians(lon2)

        # Haversine formula
        dlon = lon2 - lon1
        dlat = lat2 - lat1
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        c = 2 * math.asin(math.sqrt(a))

        # Earth radius in meters
        r = 6371000

        # Calculate distance
        return c * r

    def is_in_region(self, position: Tuple[float, float, float], region_data: Dict[str, Any]) -> bool:
        """
        Check if a 3D position (agent's position) is within the weather region.
        Currently, the API adapter provides a point region {'lat': ..., 'lon': ..., 'type': 'point'}.
        This implementation assumes the weather affects a certain radius around the point.

        Args:
            position: Agent's position, e.g., (x, y, z) in simulation coordinates.
            region_data: Weather event region data, e.g., {'lat': ..., 'lon': ..., 'type': 'point'}.

        Returns:
            True if the agent is considered within the affected region, False otherwise.
        """
        if region_data is None or region_data.get('type') != 'point':
            logger.debug("No valid point region data in weather event, assuming agent is not affected.")
            return False

        # Convert simulation coordinates to latitude and longitude
        sim_lat, sim_lon = self._convert_sim_coords_to_latlon(position)

        # Get region coordinates
        region_lat = region_data.get('lat')
        region_lon = region_data.get('lon')

        if region_lat is None or region_lon is None:
            logger.warning("Region data missing latitude or longitude. Assuming agent is not affected.")
            return False

        # Calculate distance between agent and region center
        distance = self._calculate_distance(sim_lat, sim_lon, region_lat, region_lon)

        # Define the affected radius (in meters)
        # This could be made configurable or based on weather severity
        AFFECTED_RADIUS_METERS = 10000  # 10 km radius

        # Check if agent is within the affected radius
        return distance < AFFECTED_RADIUS_METERS

    def get_weather_at(self, position: Tuple[float, float, float]) -> Dict[str, Any]:
        """
        Get the current weather conditions at a specific position in the simulation.

        Args:
            position: A tuple (x, y, z) representing the position in simulation coordinates.

        Returns:
            A dictionary containing weather data for the specified position.
            If no weather data is available, returns a default weather data dictionary.
        """
        # Check if we have any weather data
        if not self._weather_schedule:
            logger.warning("No weather data available. Returning default weather conditions.")
            return self._get_default_weather()

        # Convert simulation coordinates to latitude and longitude
        sim_lat, sim_lon = self._convert_sim_coords_to_latlon(position)

        # Get the current simulation time
        current_sim_time = self.env.now

        # Find the most recent weather event in the schedule
        base_sim_time = self._last_api_refresh_sim_time if self._last_api_refresh_sim_time >= 0 else 0.0

        # Convert schedule keys to absolute simulation times
        absolute_times = {base_sim_time + rel_time: rel_time for rel_time in self._weather_schedule.keys()}

        # Find the most recent weather event time that is less than or equal to current_sim_time
        most_recent_time = None
        for abs_time in sorted(absolute_times.keys()):
            if abs_time <= current_sim_time:
                most_recent_time = abs_time
            else:
                break

        if most_recent_time is None:
            logger.warning("No weather data available for the current simulation time. Returning default weather conditions.")
            return self._get_default_weather()

        # Get the relative time key for the most recent weather event
        rel_time_key = absolute_times[most_recent_time]

        # Get the weather data for the most recent event
        weather_data = self._weather_schedule[rel_time_key].copy()

        # Check if the position is within the region affected by this weather event
        region_data = weather_data.get('region')
        if not self.is_in_region(position, region_data):
            logger.debug(f"Position {position} is outside the affected region of the current weather event. Returning default weather conditions.")
            return self._get_default_weather()

        # Add position information to the weather data
        weather_data['position'] = position
        weather_data['sim_position_lat'] = sim_lat
        weather_data['sim_position_lon'] = sim_lon

        return weather_data

    def _get_default_weather(self) -> Dict[str, Any]:
        """
        Return default weather conditions when no data is available.

        Returns:
            A dictionary with default weather data.
        """
        return {
            'condition': 'Clear',
            'description': 'clear sky',
            'severity': 'NORMAL',
            'wind_speed': 0.0,
            'wind_direction': 0,
            'temperature': 20.0,  # Celsius
            'humidity': 50,       # %
            'pressure': 1013.25,  # hPa (standard atmospheric pressure)
            'precipitation_rate': 0.0,
            'data_source': 'default',
            'sim_timestamp': self.env.now
        }

    # _parse_polygon is likely no longer needed if using API point data

    # --- API Refresh Loop ---
    def _api_refresh_loop(self):
        """
        SimPy process that periodically re-fetches weather data from the API.
        """
        while True:
            yield self.env.timeout(self.api_refresh_interval)

            current_sim_time = self.env.now
            logger.info(f"API Refresh Triggered at sim_time {current_sim_time:.2f}. Fetching new data...")

            # Store the old schedule temporarily
            old_schedule = self._weather_schedule

            # Fetch new data - this replaces self._weather_schedule
            self.load_data()
            if self._weather_schedule and self._weather_schedule != old_schedule:
                 logger.info("API data refreshed. The _weather_update_loop will detect the change.")
                 # The update loop should handle the restart internally.
            elif not self._weather_schedule:
                 logger.warning("API refresh failed to load new data. Continuing with old schedule (if any).")
                 self._weather_schedule = old_schedule # Restore old schedule if refresh failed
            else:
                 logger.info("API data refreshed, but schedule content is the same.")