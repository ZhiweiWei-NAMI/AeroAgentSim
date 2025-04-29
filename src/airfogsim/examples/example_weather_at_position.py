#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Example demonstrating the use of WeatherDataProvider's get_weather_at method.
This example shows how to get weather data at specific positions in the simulation.
"""

import os
import simpy
from airfogsim.utils.logging_config import get_logger
import random
from typing import List, Tuple

from airfogsim.core.environment import Environment
from airfogsim.dataprovider.weather import WeatherDataProvider

# Configure logging
logger = get_logger(__name__)

def run_weather_at_position_example():
    """
    Run an example simulation demonstrating the use of get_weather_at method.
    """
    # Create simulation environment
    env = Environment()

    # Get API key from environment variable
    api_key = os.environ.get("OPENWEATHERMAP_API_KEY")
    if not api_key:
        logger.error("OPENWEATHERMAP_API_KEY environment variable not set. Using mock data.")
        # For demonstration purposes, we'll continue with mock data

    # Define a base location (New York City)
    base_location = {'lat': 40.7128, 'lon': -74.0060}

    # Create WeatherDataProvider
    weather_config = {
        'api_key': api_key,
        'location': base_location,
        'update_interval': 3600,  # Check for updates every hour in simulation time
        'api_refresh_interval': 3600  # Refresh API data every hour in real time
    }

    weather_provider = WeatherDataProvider(env, weather_config)

    # Load weather data
    weather_provider.load_data()

    # Start event triggering
    weather_provider.start_event_triggering()

    # Define test positions (in simulation coordinates)
    # These positions are relative to the base location
    test_positions = [
        (0, 0, 0),           # Origin (base location)
        (1000, 1000, 100),   # 1 km east, 1 km north, 100 m altitude
        (5000, -3000, 200),  # 5 km east, 3 km south, 200 m altitude
        (10000, 8000, 500),  # 10 km east, 8 km north, 500 m altitude
        (-15000, -12000, 300)  # 15 km west, 12 km south, 300 m altitude
    ]

    # Run simulation for a few hours
    simulation_hours = 5

    # Process to check weather at different positions
    def check_weather_process():
        for hour in range(simulation_hours):
            # Wait for an hour of simulation time
            yield env.timeout(3600)

            logger.info(f"\n=== Weather at simulation time {env.now} seconds (Hour {hour+1}) ===")

            # Check weather at each test position
            for i, position in enumerate(test_positions):
                weather_data = weather_provider.get_weather_at(position)

                # Log the weather data
                logger.info(f"\nPosition {i+1}: {position}")

                # Format latitude and longitude with proper handling for N/A values
                sim_lat = weather_data.get('sim_position_lat')
                sim_lon = weather_data.get('sim_position_lon')
                if isinstance(sim_lat, (int, float)) and isinstance(sim_lon, (int, float)):
                    logger.info(f"  Converted to: Lat {sim_lat:.6f}, Lon {sim_lon:.6f}")
                else:
                    logger.info(f"  Converted to: Lat {sim_lat}, Lon {sim_lon}")

                logger.info(f"  Condition: {weather_data.get('condition', 'N/A')} ({weather_data.get('description', 'N/A')})")
                logger.info(f"  Severity: {weather_data.get('severity', 'N/A')}")
                logger.info(f"  Temperature: {weather_data.get('temperature', 'N/A')}°C")
                logger.info(f"  Wind: {weather_data.get('wind_speed', 'N/A')} m/s at {weather_data.get('wind_direction', 'N/A')}°")
                logger.info(f"  Precipitation: {weather_data.get('precipitation_rate', 'N/A')} mm/h")
                logger.info(f"  Data Source: {weather_data.get('data_source', 'N/A')}")

            # Also check a random position
            random_position = (
                random.uniform(-20000, 20000),  # x: -20 km to 20 km
                random.uniform(-20000, 20000),  # y: -20 km to 20 km
                random.uniform(0, 1000)         # z: 0 to 1000 m
            )

            weather_data = weather_provider.get_weather_at(random_position)

            logger.info(f"\nRandom Position: {random_position}")

            # Format latitude and longitude with proper handling for N/A values
            sim_lat = weather_data.get('sim_position_lat')
            sim_lon = weather_data.get('sim_position_lon')
            if isinstance(sim_lat, (int, float)) and isinstance(sim_lon, (int, float)):
                logger.info(f"  Converted to: Lat {sim_lat:.6f}, Lon {sim_lon:.6f}")
            else:
                logger.info(f"  Converted to: Lat {sim_lat}, Lon {sim_lon}")

            logger.info(f"  Condition: {weather_data.get('condition', 'N/A')} ({weather_data.get('description', 'N/A')})")
            logger.info(f"  Severity: {weather_data.get('severity', 'N/A')}")
            logger.info(f"  Temperature: {weather_data.get('temperature', 'N/A')}°C")
            logger.info(f"  Wind: {weather_data.get('wind_speed', 'N/A')} m/s at {weather_data.get('wind_direction', 'N/A')}°")
            logger.info(f"  Precipitation: {weather_data.get('precipitation_rate', 'N/A')} mm/h")
            logger.info(f"  Data Source: {weather_data.get('data_source', 'N/A')}")

    # Start the process
    env.process(check_weather_process())

    # Run the simulation
    logger.info("Starting simulation...")
    env.run(until=simulation_hours * 3600)
    logger.info("Simulation completed.")

if __name__ == "__main__":
    run_weather_at_position_example()
