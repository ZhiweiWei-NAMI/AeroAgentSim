# -*- coding: utf-8 -*-
from __future__ import annotations
import functools
import logging
import random
import numpy as np
from typing import TYPE_CHECKING, Dict, Any, Optional, List, Callable, Tuple
import simpy

from ..core.dataprovider import DataProvider

# Type hinting
if TYPE_CHECKING:
    from airfogsim.core.environment import Environment
    from airfogsim.core.agent import Agent
    from airfogsim.manager.task_manager import TaskManager
    from airfogsim.dataprovider.weather import WeatherDataProvider
    from airfogsim.dataprovider.traffic import TrafficDataProvider

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class AccidentDataProvider(DataProvider):
    """
    Provides accident data and triggers accident-related events.

    This provider implements a Bayesian network-based event propagation system
    that can generate accidents based on weather and traffic conditions.
    It can also take over data provision from other providers during accidents.
    """
    EVENT_ACCIDENT_REPORTED = 'AccidentReported'
    EVENT_ACCIDENT_CLEARED = 'AccidentCleared'

    def __init__(self, env: 'Environment', config: Optional[Dict[str, Any]] = None):
        super().__init__(env, config)

        # Initialize accident data storage
        self.accident_data = {}
        self.data_file = self.config.get('data_file')

        # Accident state tracking
        self.active_accidents = {}  # {accident_id: accident_data}
        self.blocked_providers = {}  # {provider_id: unblock_time}

        # Bayesian network parameters
        self.bayesian_params = self.config.get('bayesian_params', {
            'weather_influence': {
                'THUNDERSTORM': 0.8,
                'SNOW': 0.6,
                'RAIN': 0.4,
                'FOG': 0.5,
                'CLOUDY': 0.2,
                'CLEAR': 0.1
            },
            'traffic_influence': {
                'congestion_threshold': 20,  # Number of vehicles in area
                'congestion_factor': 0.7,
                'speed_threshold': 10,  # m/s
                'speed_factor': 0.5
            },
            'base_probability': 0.01,  # Base probability per check
            'check_interval': 300,  # Check every 5 minutes
            'min_duration': 600,  # Minimum accident duration (10 minutes)
            'max_duration': 3600,  # Maximum accident duration (1 hour)
        })

        # Get other data providers
        self.weather_provider = None
        self.traffic_provider = None

        # Handler function registry
        self.handler_functions = {}

        # Register default handlers
        self._register_default_handlers()

    def load_data(self):
        """
        Load accident data (e.g., location, time, severity).
        """
        if self.data_file:
            logger.info(f"Loading accident data from {self.data_file}")
            try:
                # TODO: Implement file-based data loading if needed
                pass
            except Exception as e:
                logger.error(f"Error loading accident data: {e}")
        else:
            logger.info("No data file specified, using Bayesian network for accident generation")

        # Try to get other data providers from the environment
        try:
            self.weather_provider = self.env.get_data_provider('weather')
            if self.weather_provider:
                logger.info(f"Found weather provider: {self.weather_provider.__class__.__name__}")
                # Register as a source
                self.register_source(self.weather_provider)
            else:
                logger.warning("Weather provider not found in environment")
        except (AttributeError, KeyError):
            logger.warning("Could not access weather provider from environment")

        try:
            self.traffic_provider = self.env.get_data_provider('traffic')
            if self.traffic_provider:
                logger.info(f"Found traffic provider: {self.traffic_provider.__class__.__name__}")
                # Register as a source
                self.register_source(self.traffic_provider)
            else:
                logger.warning("Traffic provider not found in environment")
        except (AttributeError, KeyError):
            logger.warning("Could not access traffic provider from environment")

    def start_event_triggering(self):
        """
        Start the SimPy process to trigger AccidentReported events.
        """
        logger.info(f"{self.__class__.__name__} event triggering process starting")

        # Start the Bayesian network-based accident generation process
        self.env.process(self._accident_generation_loop())

        # Start the process to check and unblock providers
        self.env.process(self._check_blocked_providers_loop())

    def _accident_generation_loop(self):
        """SimPy process to generate and trigger accident events based on Bayesian network."""
        check_interval = self.bayesian_params.get('check_interval', 300)

        while True:
            # Wait for the next check interval
            yield self.env.timeout(check_interval)

            # Skip if this provider is blocked
            if self.is_blocked:
                continue

            # Calculate accident probability based on current conditions
            accident_prob = self._calculate_accident_probability()

            # Decide if an accident occurs
            if random.random() < accident_prob:
                # Generate accident data
                accident_data = self._generate_accident_data()
                accident_id = f"accident_{len(self.active_accidents) + 1}_{self.env.now}"
                accident_data['id'] = accident_id
                accident_data['start_time'] = self.env.now

                # Store in active accidents
                self.active_accidents[accident_id] = accident_data

                # Block affected providers
                self._block_affected_providers(accident_data)

                # Trigger accident event
                self._trigger_accident_event(accident_data)

                # Schedule accident clearance
                duration = accident_data.get('duration', 1800)  # Default 30 minutes
                self.env.process(self._clear_accident(accident_id, duration))

    def _check_blocked_providers_loop(self):
        """SimPy process to periodically check and unblock providers."""
        check_interval = 60  # Check every minute

        while True:
            # Wait for the next check interval
            yield self.env.timeout(check_interval)

            # Check for providers to unblock
            current_time = self.env.now
            providers_to_unblock = []

            for provider_id, unblock_time in self.blocked_providers.items():
                if current_time >= unblock_time:
                    providers_to_unblock.append(provider_id)

            # Unblock providers
            for provider_id in providers_to_unblock:
                provider = self.env.get_data_provider(provider_id)
                if provider:
                    provider.unblock()
                del self.blocked_providers[provider_id]

    def _calculate_accident_probability(self) -> float:
        """
        Calculate the probability of an accident based on current weather and traffic conditions.

        Returns:
            float: Probability of an accident (0.0 to 1.0)
        """
        base_prob = self.bayesian_params.get('base_probability', 0.01)
        weather_factor = 1.0
        traffic_factor = 1.0

        # Factor in weather conditions if available
        if self.weather_provider:
            try:
                # Get current weather condition
                weather_state = getattr(self.weather_provider, 'weather_state', {})
                condition = weather_state.get('condition', 'CLEAR')

                # Apply weather influence
                weather_influence = self.bayesian_params.get('weather_influence', {})
                weather_factor = weather_influence.get(condition, 0.1)

                logger.debug(f"Weather condition: {condition}, factor: {weather_factor}")
            except Exception as e:
                logger.warning(f"Error getting weather data: {e}")

        # Factor in traffic conditions if available
        if self.traffic_provider:
            try:
                # Get traffic data
                vehicle_states = getattr(self.traffic_provider, '_vehicle_dynamics', {})
                num_vehicles = len(vehicle_states)

                # Calculate average speed
                speeds = [data.get('speed', 0) for data in vehicle_states.values()]
                avg_speed = sum(speeds) / max(1, len(speeds))

                # Apply traffic influence
                traffic_params = self.bayesian_params.get('traffic_influence', {})
                congestion_threshold = traffic_params.get('congestion_threshold', 20)
                congestion_factor = traffic_params.get('congestion_factor', 0.7)
                speed_threshold = traffic_params.get('speed_threshold', 10)
                speed_factor = traffic_params.get('speed_factor', 0.5)

                # Calculate congestion influence
                if num_vehicles > congestion_threshold:
                    traffic_factor *= congestion_factor

                # Calculate speed influence
                if avg_speed < speed_threshold:
                    traffic_factor *= speed_factor

                logger.debug(f"Traffic: {num_vehicles} vehicles, avg speed: {avg_speed}, factor: {traffic_factor}")
            except Exception as e:
                logger.warning(f"Error getting traffic data: {e}")

        # Calculate final probability
        final_prob = min(1.0, base_prob * weather_factor * traffic_factor)
        logger.debug(f"Accident probability: {final_prob:.4f}")

        return final_prob

    def _generate_accident_data(self) -> Dict[str, Any]:
        """
        Generate data for a new accident.

        Returns:
            Dict[str, Any]: Accident data
        """
        # Generate random location (could be improved to use actual traffic data)
        location = {
            'x': random.uniform(-1000, 1000),
            'y': random.uniform(-1000, 1000),
            'z': 0
        }

        # Generate random severity (1-5)
        severity = random.randint(1, 5)

        # Generate random duration based on severity
        min_duration = self.bayesian_params.get('min_duration', 600)
        max_duration = self.bayesian_params.get('max_duration', 3600)
        duration_range = max_duration - min_duration
        duration = min_duration + (severity / 5.0) * duration_range

        # Generate accident type
        accident_types = ['COLLISION', 'FIRE', 'HAZMAT_SPILL', 'INFRASTRUCTURE_DAMAGE', 'MEDICAL_EMERGENCY']
        accident_type = random.choice(accident_types)

        # Create accident data
        accident_data = {
            'sim_timestamp': self.env.now,
            'location': location,
            'severity': severity,
            'type': accident_type,
            'duration': duration,
            'affected_radius': 50 + severity * 30,  # Radius affected by the accident
            'handler_function': self._get_handler_for_type(accident_type)
        }

        return accident_data

    def _block_affected_providers(self, accident_data: Dict[str, Any]):
        """
        Block data providers affected by an accident.

        Args:
            accident_data: Data about the accident
        """
        duration = accident_data.get('duration', 1800)
        severity = accident_data.get('severity', 3)

        # Only block providers for severe accidents
        if severity >= 3:
            # Block weather provider if available and accident is severe enough
            if self.weather_provider and severity >= 4:
                logger.info(f"Blocking weather provider for {duration} seconds due to accident")
                self.weather_provider.block(duration)
                self.blocked_providers['weather'] = self.env.now + duration

            # Block traffic provider if available
            if self.traffic_provider:
                logger.info(f"Blocking traffic provider for {duration} seconds due to accident")
                self.traffic_provider.block(duration)
                self.blocked_providers['traffic'] = self.env.now + duration

    def _trigger_accident_event(self, accident_data: Dict[str, Any]):
        """
        Trigger an accident event.

        Args:
            accident_data: Data about the accident
        """
        logger.info(f"Triggering accident event: {accident_data.get('type')} with severity {accident_data.get('severity')}")

        # Trigger the event
        self.env.event_registry.trigger_event(
            source_id=self.__class__.__name__,
            event_name=self.EVENT_ACCIDENT_REPORTED,
            event_value=accident_data
        )

    def _clear_accident(self, accident_id: str, duration: float):
        """
        SimPy process to clear an accident after its duration.

        Args:
            accident_id: ID of the accident to clear
            duration: Duration before clearing the accident
        """
        # Wait for the accident duration
        yield self.env.timeout(duration)

        # Get accident data
        if accident_id in self.active_accidents:
            accident_data = self.active_accidents[accident_id]

            # Update accident data
            accident_data['cleared_time'] = self.env.now
            accident_data['status'] = 'CLEARED'

            # Trigger accident cleared event
            self.env.event_registry.trigger_event(
                source_id=self.__class__.__name__,
                event_name=self.EVENT_ACCIDENT_CLEARED,
                event_value=accident_data
            )

            # Remove from active accidents
            del self.active_accidents[accident_id]

            logger.info(f"Accident {accident_id} cleared after {duration} seconds")

    def _register_default_handlers(self):
        """Register default handler functions for different accident types."""
        self.handler_functions = {
            'COLLISION': self._handle_collision_accident,
            'FIRE': self._handle_fire_accident,
            'HAZMAT_SPILL': self._handle_hazmat_accident,
            'INFRASTRUCTURE_DAMAGE': self._handle_infrastructure_accident,
            'MEDICAL_EMERGENCY': self._handle_medical_accident
        }

    def _get_handler_for_type(self, accident_type: str) -> Optional[str]:
        """
        Get the handler function name for a specific accident type.

        Args:
            accident_type: Type of accident

        Returns:
            str: Name of the handler function
        """
        if accident_type in self.handler_functions:
            return self.handler_functions[accident_type].__name__
        return None

    # --- Handler Functions ---
    def _handle_collision_accident(self, subscriber: Any, event_data: Dict[str, Any]):
        """
        Handle collision accident for a subscriber.

        Args:
            subscriber: The object that subscribed to the event
            event_data: Data about the accident
        """
        from airfogsim.core.agent import Agent

        if isinstance(subscriber, Agent):
            agent = subscriber
            location = event_data.get('location', {})
            severity = event_data.get('severity', 1)

            # Check if agent is in affected area
            agent_pos = agent.get_state('position')
            if agent_pos and self._is_in_affected_area(agent_pos, location, event_data.get('affected_radius', 100)):
                # Apply effects based on severity
                if severity >= 4:
                    # Severe collision - force landing
                    agent.trigger_emergency_landing(f"Emergency landing due to severe collision at {location}")
                elif severity >= 2:
                    # Moderate collision - reroute
                    agent.trigger_reroute(f"Rerouting due to collision at {location}")

                logger.info(f"Agent {agent.id} affected by collision accident")

    def _handle_fire_accident(self, subscriber: Any, event_data: Dict[str, Any]):
        """
        Handle fire accident for a subscriber.

        Args:
            subscriber: The object that subscribed to the event
            event_data: Data about the accident
        """
        from airfogsim.core.agent import Agent

        if isinstance(subscriber, Agent):
            agent = subscriber
            location = event_data.get('location', {})
            severity = event_data.get('severity', 1)

            # Check if agent is in affected area
            agent_pos = agent.get_state('position')
            if agent_pos and self._is_in_affected_area(agent_pos, location, event_data.get('affected_radius', 100)):
                # Apply effects based on severity
                if severity >= 3:
                    # Severe fire - force landing
                    agent.trigger_emergency_landing(f"Emergency landing due to fire at {location}")
                else:
                    # Moderate fire - reroute
                    agent.trigger_reroute(f"Rerouting due to fire at {location}")

                logger.info(f"Agent {agent.id} affected by fire accident")

    def _handle_hazmat_accident(self, subscriber: Any, event_data: Dict[str, Any]):
        """
        Handle hazardous material spill accident for a subscriber.

        Args:
            subscriber: The object that subscribed to the event
            event_data: Data about the accident
        """
        from airfogsim.core.agent import Agent

        if isinstance(subscriber, Agent):
            agent = subscriber
            location = event_data.get('location', {})
            severity = event_data.get('severity', 1)

            # Check if agent is in affected area
            agent_pos = agent.get_state('position')
            if agent_pos and self._is_in_affected_area(agent_pos, location, event_data.get('affected_radius', 100)):
                # Apply effects based on severity
                if severity >= 2:
                    # Any hazmat spill is serious - force landing
                    agent.trigger_emergency_landing(f"Emergency landing due to hazardous material spill at {location}")

                logger.info(f"Agent {agent.id} affected by hazardous material spill")

    def _handle_infrastructure_accident(self, subscriber: Any, event_data: Dict[str, Any]):
        """
        Handle infrastructure damage accident for a subscriber.

        Args:
            subscriber: The object that subscribed to the event
            event_data: Data about the accident
        """
        from airfogsim.core.agent import Agent

        if isinstance(subscriber, Agent):
            agent = subscriber
            location = event_data.get('location', {})
            severity = event_data.get('severity', 1)

            # Check if agent is in affected area
            agent_pos = agent.get_state('position')
            if agent_pos and self._is_in_affected_area(agent_pos, location, event_data.get('affected_radius', 100)):
                # Apply effects based on severity
                if severity >= 3:
                    # Severe infrastructure damage - force landing
                    agent.trigger_emergency_landing(f"Emergency landing due to infrastructure damage at {location}")
                else:
                    # Moderate damage - reroute
                    agent.trigger_reroute(f"Rerouting due to infrastructure damage at {location}")

                logger.info(f"Agent {agent.id} affected by infrastructure damage")

    def _handle_medical_accident(self, subscriber: Any, event_data: Dict[str, Any]):
        """
        Handle medical emergency accident for a subscriber.

        Args:
            subscriber: The object that subscribed to the event
            event_data: Data about the accident
        """
        from airfogsim.core.agent import Agent
        from airfogsim.manager.task_manager import TaskManager

        if isinstance(subscriber, Agent):
            agent = subscriber
            location = event_data.get('location', {})

            # Medical emergencies don't directly affect agents, but they might be assigned tasks
            logger.debug(f"Agent {agent.id} notified of medical emergency at {location}")

        elif isinstance(subscriber, TaskManager):
            task_manager = subscriber
            location = event_data.get('location', {})
            severity = event_data.get('severity', 1)

            # Generate emergency response task
            if severity >= 2:
                # Create a medical emergency response task
                task_manager.generate_emergency_task(
                    task_type="MEDICAL_RESPONSE",
                    location=location,
                    priority=severity,
                    description=f"Medical emergency response at {location}"
                )

                logger.info(f"Medical emergency response task generated for location {location}")

    # --- Helper Methods ---
    def _is_in_affected_area(self, position: Tuple[float, float, float],
                            accident_location: Dict[str, float],
                            radius: float) -> bool:
        """
        Check if a position is within the affected area of an accident.

        Args:
            position: Position to check (x, y, z)
            accident_location: Location of the accident {'x': x, 'y': y, 'z': z}
            radius: Radius of the affected area

        Returns:
            bool: True if the position is within the affected area
        """
        try:
            # Extract coordinates
            x1, y1, z1 = position
            x2 = accident_location.get('x', 0)
            y2 = accident_location.get('y', 0)
            z2 = accident_location.get('z', 0)

            # Calculate distance
            distance = ((x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2) ** 0.5

            return distance <= radius
        except Exception as e:
            logger.error(f"Error checking if position is in affected area: {e}")
            return False

    # --- Standard Callback Method ---
    def on_accident_reported(self, subscriber: Any, event_data: Dict[str, Any]):
        """
        Standard callback for AccidentReported events.
        Modifies subscriber state or triggers actions based on accidents.

        Args:
            subscriber: The object that subscribed (e.g., TaskManager, Agent).
            event_data (dict): Data about the reported accident (e.g., location, severity, type).
        """
        # Get handler function name from event data
        handler_func_name = event_data.get('handler_function')

        if handler_func_name and hasattr(self, handler_func_name):
            # Call the specific handler function
            handler_func = getattr(self, handler_func_name)
            handler_func(subscriber, event_data)
        else:
            # Default handling logic
            logger.warning(f"No specific handler found for accident type: {event_data.get('type')}")

            # Generic handling based on subscriber type
            from airfogsim.core.agent import Agent
            from airfogsim.manager.task_manager import TaskManager

            if isinstance(subscriber, Agent):
                agent = subscriber
                location = event_data.get('location', {})
                severity = event_data.get('severity', 1)

                # Check if agent is in affected area
                agent_pos = agent.get_state('position')
                if agent_pos and self._is_in_affected_area(agent_pos, location, event_data.get('affected_radius', 100)):
                    # Apply generic effects based on severity
                    if severity >= 4:
                        agent.trigger_emergency_landing(f"Emergency landing due to accident at {location}")
                    elif severity >= 2:
                        agent.trigger_reroute(f"Rerouting due to accident at {location}")

                    logger.info(f"Agent {agent.id} affected by generic accident handling")

            elif isinstance(subscriber, TaskManager):
                task_manager = subscriber
                location = event_data.get('location', {})
                severity = event_data.get('severity', 1)

                # Generate emergency task for severe accidents
                if severity >= 3:
                    task_manager.generate_emergency_task(
                        task_type="ACCIDENT_RESPONSE",
                        location=location,
                        priority=severity,
                        description=f"Accident response at {location}"
                    )

                    logger.info(f"Accident response task generated for location {location}")

    def on_accident_cleared(self, subscriber: Any, event_data: Dict[str, Any]):
        """
        Standard callback for AccidentCleared events.
        Notifies subscribers that an accident has been cleared.

        Args:
            subscriber: The object that subscribed (e.g., TaskManager, Agent).
            event_data (dict): Data about the cleared accident.
        """
        from airfogsim.core.agent import Agent
        from airfogsim.manager.task_manager import TaskManager

        if isinstance(subscriber, Agent):
            agent = subscriber
            accident_id = event_data.get('id', 'unknown')
            location = event_data.get('location', {})

            logger.debug(f"Agent {agent.id} notified that accident {accident_id} at {location} has been cleared")

            # If agent was rerouted due to this accident, it can potentially return to original route
            # This would require tracking which accidents affected which agents

        elif isinstance(subscriber, TaskManager):
            task_manager = subscriber
            accident_id = event_data.get('id', 'unknown')

            # Update or complete any tasks related to this accident
            task_manager.update_tasks_for_cleared_accident(accident_id)

            logger.info(f"Tasks updated for cleared accident {accident_id}")