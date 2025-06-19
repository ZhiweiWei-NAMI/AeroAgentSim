# -*- coding: utf-8 -*-
"""
Demo script for using the WeatherDataProvider with OpenWeatherMap API.

This script sets up a simple AirFogSim environment and runs the
WeatherDataProvider configured to fetch data from the OpenWeatherMap API.
It demonstrates how weather events are loaded and triggered within the simulation.

**Prerequisites:**
- Install 'simpy' and 'requests': pip install simpy requests
- Set the OPENWEATHERMAP_API_KEY environment variable with your API key.
  Example: export OPENWEATHERMAP_API_KEY='your_actual_api_key'
"""

import simpy
import os
import sys
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)
# --- Setup Python Path ---
# Add the project root to the Python path to allow importing airfogsim modules
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir) # Go up one level from 'examples'
src_root = os.path.join(project_root, 'src') # Go into 'src'
if src_root not in sys.path:
    sys.path.insert(0, src_root)

# --- Imports ---
try:
    from airfogsim.core.environment import Environment
    from airfogsim.dataprovider.weather import WeatherDataProvider
    # Import EventRegistry to subscribe to events for demonstration
    from airfogsim.core.event import EventRegistry
except ImportError as e:
    logger.info(f"Error importing AirFogSim modules: {e}")
    logger.info("Please ensure the script is run from the 'examples' directory or the project root,")
    logger.info("and the airfogsim package structure is correct.")
    sys.exit(1)

# --- Configuration ---
# Get API key from environment variable
API_KEY = os.environ.get("OPENWEATHERMAP_API_KEY")

# Example Location (New York City)
LOCATION = {'lat': 40.7128, 'lon': -74.0060}

# Simulation duration (in simulation time units, e.g., seconds)
# Run long enough to potentially see a few forecast events trigger
SIMULATION_DURATION = 3 * 3600 # Simulate for 3 hours

# API Refresh Interval (real seconds) - set short for demo, or None to disable
# Note: Frequent refreshes might hit API rate limits on free tiers.
API_REFRESH_INTERVAL_SECONDS = 1800 # Refresh every 30 minutes (real time)

# Logging level
logger = get_logger("WeatherProviderDemo")

# --- Event Handler ---
def handle_weather_event(subscriber_id: str, event_data: dict):
    """Callback function to simply logger.info received weather events."""
    sim_time = event_data.get('sim_timestamp', 'N/A')
    severity = event_data.get('severity', 'N/A')
    condition = event_data.get('condition', 'N/A')
    temp = event_data.get('temperature', 'N/A')
    logger.info(f"--- [Event Received by {subscriber_id} at SimTime: {sim_time:.2f}] ---")
    logger.info(f"    Weather Changed: Severity={severity}, Condition={condition}, Temp={temp}°C")
    # logger.debug("    Full Event Data:")
    # logger.debug(plogger.info(event_data)) # Uncomment for full details

# --- Main Execution ---
if __name__ == "__main__":
    logger.info("--- WeatherDataProvider API Demo ---")

    if not API_KEY:
        logger.error("FATAL: OPENWEATHERMAP_API_KEY environment variable not set.")
        logger.error("Please set the environment variable before running this demo.")
        logger.error("Example: export OPENWEATHERMAP_API_KEY='your_actual_api_key'")
        sys.exit(1)
    else:
        masked_key = API_KEY[:4] + "****" + API_KEY[-4:]
        logger.info(f"Using API Key: {masked_key}")

    # 1. Create AirFogSim Environment
    # The Environment implicitly creates core managers like EventRegistry
    env = Environment()
    logger.info("AirFogSim Environment created.")

    # 2. Configure WeatherDataProvider
    weather_config = {
        'api_key': API_KEY,
        'location': LOCATION,
        'api_refresh_interval': API_REFRESH_INTERVAL_SECONDS # Set refresh interval
    }
    logger.info(f"WeatherDataProvider Config: {weather_config}")

    # 3. Instantiate WeatherDataProvider
    try:
        weather_provider = WeatherDataProvider(env, config=weather_config)
        logger.info("WeatherDataProvider instantiated.")
    except ValueError as e:
        logger.error(f"Failed to instantiate WeatherDataProvider: {e}")
        sys.exit(1)

    # 4. Subscribe to Weather Events (for demonstration)
    # Use the EventRegistry from the environment
    subscriber_name = "DemoLogger"
    env.event_registry.subscribe(
        source_id=WeatherDataProvider.__name__, # Use class name as source ID
        listener_id=subscriber_name,
        event_name=WeatherDataProvider.EVENT_WEATHER_CHANGED,
        callback=lambda event_data: handle_weather_event(subscriber_name, event_data)
    )
    logger.info(f"'{subscriber_name}' subscribed to '{WeatherDataProvider.EVENT_WEATHER_CHANGED}' events.")

    # 5. Load Initial Data and Start Triggering
    # load_data is called implicitly by start_event_triggering if schedule is empty
    weather_provider.start_event_triggering()
    # Note: start_event_triggering now handles initial load if needed and starts loops.

    # 6. Run Simulation
    logger.info(f"Starting simulation run for {SIMULATION_DURATION} seconds...")
    env.run(until=SIMULATION_DURATION)
    logger.info("--- Simulation Finished ---")