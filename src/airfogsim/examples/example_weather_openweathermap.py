# -*- coding: utf-8 -*-
"""
Demo script for using the OpenWeatherMap adapter directly.

This script shows how to fetch current and forecast weather data
from the OpenWeatherMap API and convert it into the simulation's
event schedule format using the adapter functions.

**Prerequisites:**
- Install the 'requests' library: pip install requests
- Set the OPENWEATHERMAP_API_KEY environment variable with your API key.
  Example: export OPENWEATHERMAP_API_KEY='your_actual_api_key'
"""

import os
import sys
from datetime import datetime

# Add the project root to the Python path to allow importing airfogsim modules
# This assumes the script is run from the 'examples' directory or the project root
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir) # Go up one level from 'examples'
src_root = os.path.join(project_root, 'src') # Go into 'src'
if src_root not in sys.path:
    sys.path.insert(0, src_root)

try:
    from airfogsim.dataprovider.api_adapters.openweathermap_adapter import fetch_and_convert_weather_data
except ImportError:
    print("Error: Could not import the OpenWeatherMap adapter.")
    print("Please ensure the script is run from the 'examples' directory or the project root,")
    print("and the airfogsim package structure is correct.")
    sys.exit(1)

# --- Configuration ---
# Get API key from environment variable
API_KEY = os.environ.get("OPENWEATHERMAP_API_KEY")

# Example Location (New York City)
LATITUDE = 40.7128
LONGITUDE = -74.0060

# --- Main Execution ---
if __name__ == "__main__":
    print("--- OpenWeatherMap Adapter Demo ---")

    if not API_KEY:
        print("\nError: OPENWEATHERMAP_API_KEY environment variable not set.")
        print("Please set the environment variable before running this demo.")
        print("Example: export OPENWEATHERMAP_API_KEY='your_actual_api_key'")
        sys.exit(1)
    else:
        # Mask parts of the API key for printing
        masked_key = API_KEY[:4] + "****" + API_KEY[-4:]
        print(f"\nUsing API Key: {masked_key}")

    print(f"Fetching and converting weather data for:")
    print(f"  Latitude: {LATITUDE}")
    print(f"  Longitude: {LONGITUDE}")

    # Call the adapter function
    weather_schedule = fetch_and_convert_weather_data(API_KEY, LATITUDE, LONGITUDE)

    if weather_schedule:
        print("\n--- Generated Weather Schedule ---")
        print("(Keys are simulation time in seconds from now)")

        for sim_time, event_data in weather_schedule.items():
            # Convert UTC timestamp back to datetime for display
            event_time_utc = datetime.utcfromtimestamp(event_data.get('timestamp_utc', 0))

            print(f"\nSim Time Key: {sim_time:.2f}")
            print(f"  Actual Event Time (UTC): {event_time_utc.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"  Severity: {event_data.get('severity', 'N/A')}")
            print(f"  Condition: {event_data.get('condition', 'N/A')} ({event_data.get('description', 'N/A')})")
            print(f"  Temperature: {event_data.get('temperature', 'N/A')} °C")
            print(f"  Wind Speed: {event_data.get('wind_speed', 'N/A')} m/s")
            print(f"  Wind Direction: {event_data.get('wind_direction', 'N/A')} °")
            print(f"  Precipitation Rate: {event_data.get('precipitation_rate', 'N/A')} mm/h")
            # print(f"  Region: {event_data.get('region', 'N/A')}") # Uncomment to see region details
            # print(f"  Raw Timestamp: {event_data.get('timestamp_utc')}") # Uncomment for raw timestamp

        print("\n--- Demo Finished ---")
    else:
        print("\n--- Failed to retrieve or process weather data. ---")
        print("Please check your API key, internet connection, and location coordinates.")

    print("\nNote: The 'Sim Time Key' represents the number of seconds from the moment")
    print("this script was run until the corresponding weather event is scheduled to occur")
    print("in a simulation starting now.")