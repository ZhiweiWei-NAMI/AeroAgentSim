# -*- coding: utf-8 -*-
import requests
from airfogsim.utils.logging_config import get_logger
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
import json

# Configure logging
logger = get_logger(__name__)
import os

def _fetch_api_data(api_key: str, lat: float, lon: float, endpoint: str = "weather") -> Optional[Dict[str, Any]]:
    """
    Helper function to fetch data from a specific OpenWeatherMap API endpoint.

    Args:
        api_key: Your OpenWeatherMap API key.
        lat: Latitude.
        lon: Longitude.
        endpoint: API endpoint ('weather' for current, 'forecast' for forecast).

    Returns:
        API response as a dictionary, or None if an error occurs.
    """
    base_url = "https://api.openweathermap.org/data/2.5/"
    url = f"{base_url}{endpoint}?lat={lat}&lon={lon}&APPID={api_key}&units=metric"
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)
        logger.debug(f"Successfully fetched data from {endpoint} for ({lat}, {lon})")
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching OpenWeatherMap {endpoint} data for ({lat}, {lon}): {e}")
        return None
    except json.JSONDecodeError:
        logger.error(f"Error decoding JSON response from OpenWeatherMap {endpoint} for ({lat}, {lon})")
        return None

def fetch_current_weather(api_key: str, lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """Fetches current weather data."""
    return _fetch_api_data(api_key, lat, lon, "weather")

def fetch_forecast(api_key: str, lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """Fetches 5-day/3-hour forecast data."""
    return _fetch_api_data(api_key, lat, lon, "forecast")

def _determine_severity(api_data: Dict[str, Any]) -> str:
    """
    Determine weather severity based on API data.

    Args:
        api_data: A dictionary representing a single weather data point from the API.

    Returns:
        Severity level as string ('NORMAL', 'MODERATE', 'HEAVY_RAIN', 'HIGH_WINDS', 'STORM', etc.)
    """
    severity = 'NORMAL'
    condition_id = 800 # Default to Clear if not present

    if 'weather' in api_data and len(api_data['weather']) > 0:
        condition_id = api_data['weather'][0].get('id', 800)

        # Determine severity based on weather condition ID (OWM codes)
        if 200 <= condition_id < 300: severity = 'STORM'          # Thunderstorm
        elif 300 <= condition_id < 400: severity = 'MODERATE'       # Drizzle
        elif 500 <= condition_id < 600:                           # Rain
            if condition_id >= 502: severity = 'HEAVY_RAIN'       # Heavy intensity rain and above
            else: severity = 'MODERATE'
        elif 600 <= condition_id < 700:                           # Snow
            if condition_id >= 602: severity = 'HEAVY_SNOW'       # Heavy snow
            else: severity = 'MODERATE'
        elif 700 <= condition_id < 800: severity = 'MODERATE'       # Atmosphere (fog, mist, etc.)
        # 800 (Clear) and 80x (Clouds) are generally NORMAL unless wind is high

    # Check wind speed (m/s)
    wind_speed = 0
    if 'wind' in api_data:
        wind_speed = api_data['wind'].get('speed', 0)

    if wind_speed > 15: # Threshold for high winds (adjust as needed)
        # Prioritize more severe conditions like STORM over HIGH_WINDS
        if severity in ['NORMAL', 'MODERATE']:
             severity = 'HIGH_WINDS'
    elif wind_speed > 10 and severity == 'NORMAL': # Moderate wind
        severity = 'MODERATE'

    return severity

def convert_api_data_to_event(api_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a single OpenWeatherMap API data point (current or forecast item)
    into the event format expected by the simulation's WeatherDataProvider.

    Args:
        api_data: Dictionary representing one weather data point from the API.

    Returns:
        Dictionary with event details in the simulation's format.
    """
    event = {}

    # Timestamp (Unix UTC)
    event['timestamp_utc'] = api_data.get('dt', int(datetime.now().timestamp()))

    # Region (using coordinates directly for now, could be expanded)
    # Use 'coord' for current weather, take lat/lon from request for forecast
    lat = api_data.get('coord', {}).get('lat')
    lon = api_data.get('coord', {}).get('lon')
    # Note: Forecast data doesn't include lat/lon per item, assume it's the requested location
    event['region'] = {'lat': lat, 'lon': lon, 'type': 'point'} # Simple point region for now

    # Wind
    if 'wind' in api_data:
        event['wind_speed'] = api_data['wind'].get('speed', 0)  # m/s
        event['wind_direction'] = api_data['wind'].get('deg', 0)  # degrees
    else:
        event['wind_speed'] = 0
        event['wind_direction'] = 0

    # Precipitation (combining rain and snow, taking the max rate)
    precip_rate = 0
    if 'rain' in api_data:
        # OWM provides rain volume for the last 1 or 3 hours. Use '1h' if available.
        precip_rate = max(precip_rate, api_data['rain'].get('1h', 0), api_data['rain'].get('3h', 0) / 3)
    if 'snow' in api_data:
        # OWM provides snow volume for the last 1 or 3 hours. Use '1h' if available.
        precip_rate = max(precip_rate, api_data['snow'].get('1h', 0), api_data['snow'].get('3h', 0) / 3)
    event['precipitation_rate'] = precip_rate # mm/h (approximated for 3h data)

    # Temperature, Humidity, Pressure
    if 'main' in api_data:
        event['temperature'] = api_data['main'].get('temp')      # Celsius
        event['humidity'] = api_data['main'].get('humidity')    # %
        event['pressure'] = api_data['main'].get('pressure')    # hPa
    else: # Defaults if 'main' is missing
        event['temperature'] = None
        event['humidity'] = None
        event['pressure'] = None

    # Weather Condition and Description
    if 'weather' in api_data and len(api_data['weather']) > 0:
        event['condition'] = api_data['weather'][0].get('main', 'Unknown')
        event['description'] = api_data['weather'][0].get('description', '')
        event['condition_id'] = api_data['weather'][0].get('id') # Keep original ID for reference
    else:
        event['condition'] = 'Unknown'
        event['description'] = ''
        event['condition_id'] = None

    # Severity
    event['severity'] = _determine_severity(api_data)

    # Add source information
    event['data_source'] = 'openweathermap_api'

    # Filter out None values for cleaner output
    return {k: v for k, v in event.items() if v is not None}


def fetch_and_convert_weather_data(api_key: str, lat: float, lon: float) -> Dict[float, Dict[str, Any]]:
    """
    Fetches current and forecast weather data from OpenWeatherMap API
    and converts it into a dictionary format suitable for WeatherDataProvider's schedule.

    The dictionary keys are simulation times (relative seconds from now),
    and values are the converted weather event dictionaries.

    Args:
        api_key: Your OpenWeatherMap API key.
        lat: Latitude.
        lon: Longitude.

    Returns:
        A dictionary mapping simulation time (float seconds from now) to weather event data.
        Returns an empty dictionary if fetching or conversion fails.
    """
    weather_schedule = {}
    current_time_utc = datetime.utcnow()
    current_timestamp = current_time_utc.timestamp()

    # 1. Fetch and process current weather
    current_weather_raw = fetch_current_weather(api_key, lat, lon)
    if current_weather_raw:
        # Ensure the raw data has coordinates for the event conversion
        if 'coord' not in current_weather_raw:
             current_weather_raw['coord'] = {'lat': lat, 'lon': lon}
        current_event = convert_api_data_to_event(current_weather_raw)
        # Current weather applies from simulation time 0 onwards until the first forecast point
        weather_schedule[0.0] = current_event
        logger.info(f"Processed current weather for ({lat}, {lon})")
    else:
        logger.warning(f"Could not fetch current weather for ({lat}, {lon}). Schedule might be incomplete.")

    # 2. Fetch and process forecast weather
    forecast_raw = fetch_forecast(api_key, lat, lon)
    if forecast_raw and 'list' in forecast_raw:
        count = 0
        for forecast_item in forecast_raw['list']:
            # Ensure the forecast item has coordinates for event conversion
            # Forecast items don't have 'coord', so add the requested lat/lon
            forecast_item['coord'] = {'lat': lat, 'lon': lon}

            forecast_event = convert_api_data_to_event(forecast_item)
            forecast_timestamp = forecast_event.get('timestamp_utc', current_timestamp)

            # Calculate simulation time relative to now
            # Ensure timestamps are handled correctly as floats
            time_diff_seconds = float(forecast_timestamp) - float(current_timestamp)

            # Only add future forecast points
            if time_diff_seconds > 0:
                 # Use the time difference as the key in the schedule
                 sim_time = time_diff_seconds
                 weather_schedule[sim_time] = forecast_event
                 count += 1
            else:
                 # If a forecast point is in the past or exactly now, update time 0 if it's newer
                 # This handles cases where the 'current' weather might be slightly older than the first forecast point
                 if 0.0 in weather_schedule and float(forecast_timestamp) > weather_schedule[0.0].get('timestamp_utc', 0):
                     weather_schedule[0.0] = forecast_event
                 elif 0.0 not in weather_schedule: # If current weather failed, use the first forecast point
                      weather_schedule[0.0] = forecast_event


        logger.info(f"Processed {count} future forecast points for ({lat}, {lon})")
    else:
        logger.warning(f"Could not fetch or process forecast for ({lat}, {lon}). Schedule will only contain current weather (if available).")

    # Ensure the schedule is sorted by time (although dicts are ordered in Python 3.7+, explicit sort is safer)
    # Convert to list of tuples, sort, then convert back to dict
    sorted_schedule = dict(sorted(weather_schedule.items()))

    return sorted_schedule

if __name__ == '__main__':
    # Example Usage (replace with your actual API key and location)
    # Make sure to set the API key as an environment variable or use a config file in real applications
    import os
    API_KEY = os.environ.get("OPENWEATHERMAP_API_KEY") # Get key from environment variable
    LATITUDE = 40.7128 # Example: New York City
    LONGITUDE = -74.0060

    if not API_KEY:
        print("Error: OPENWEATHERMAP_API_KEY environment variable not set.")
        print("Please set the environment variable and try again.")
        print("Example: export OPENWEATHERMAP_API_KEY='your_actual_api_key'")
    else:
        print(f"Fetching weather data for Latitude={LATITUDE}, Longitude={LONGITUDE}...")
        schedule = fetch_and_convert_weather_data(API_KEY, LATITUDE, LONGITUDE)

        if schedule:
            print("\n--- Weather Schedule (Sim Time vs Event Data) ---")
            for sim_time, event_data in schedule.items():
                event_time_utc = datetime.utcfromtimestamp(event_data.get('timestamp_utc', 0))
                print(f"\nSim Time: {sim_time:.2f} seconds from now")
                print(f"  Actual Time (UTC): {event_time_utc}")
                print(f"  Condition: {event_data.get('condition', 'N/A')} ({event_data.get('description', 'N/A')})")
                print(f"  Severity: {event_data.get('severity', 'N/A')}")
                print(f"  Temp: {event_data.get('temperature', 'N/A')}°C")
                print(f"  Wind: {event_data.get('wind_speed', 'N/A')} m/s at {event_data.get('wind_direction', 'N/A')}°")
                print(f"  Precipitation: {event_data.get('precipitation_rate', 'N/A')} mm/h")
        else:
            print("\nFailed to retrieve or process weather data.")