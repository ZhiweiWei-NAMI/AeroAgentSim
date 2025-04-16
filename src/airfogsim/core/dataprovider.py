# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
import logging
from typing import Dict, Any, Optional

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class DataProvider(ABC):
    """
    Abstract base class for external data providers (e.g., weather, traffic).

    DataProviders load external data and trigger events within the simulation
    environment based on that data at appropriate times. They also provide
    standard callback functions that Agents or Managers can subscribe to,
    allowing the external data to passively influence the simulation state.
    """
    def __init__(self, env, config=None):
        """
        Initialize the DataProvider.

        Args:
            env: The simulation environment instance.
            config (dict, optional): Configuration specific to this provider. Defaults to None.
        """
        self.env = env
        self.config = config if config is not None else {}
        logger.info(f"Initializing {self.__class__.__name__} with config: {self.config}")

    @abstractmethod
    def load_data(self):
        """
        Load the external data required by this provider.
        This could involve reading from files, databases, or APIs.
        This method should be called before the simulation starts.
        """
        pass

    @abstractmethod
    def start_event_triggering(self):
        """
        Start the process(es) that will trigger events based on the loaded data
        at the appropriate simulation times.
        This method is typically called after `load_data` and before `env.run()`.
        It might start one or more SimPy processes.
        """
        pass

    # Note: Standard callback methods (like on_weather_changed) will be defined
    # in the concrete subclasses (e.g., WeatherDataProvider) as they are specific
    # to the type of data and events the provider handles.


class DataIntegration(ABC):
    """
    Abstract base class for data integration modules.

    DataIntegration classes are responsible for integrating external data providers
    into the simulation environment. They handle the configuration, initialization,
    and event handling for data providers, and provide a unified interface for
    simulation components to interact with external data.

    Unlike DataProviders which directly load and trigger events based on external data,
    DataIntegration classes act as a bridge between DataProviders and the simulation,
    handling the registration, configuration, and event subscription logic.
    """

    def __init__(self, env, config=None):
        """
        Initialize the DataIntegration.

        Args:
            env: The simulation environment instance.
            config (dict, optional): Configuration for the integration. Defaults to None.
        """
        self.env = env
        self.config = config or {}

        # Default configuration to be overridden by subclasses
        self.default_config = {}

        # Merge configurations
        self.config = {**self.default_config, **self.config}

        logger.info(f"Initializing {self.__class__.__name__} with config: {self.config}")

        # Initialize data provider
        self._initialize_provider()

        # Register event listeners
        self._register_event_listeners()

    @abstractmethod
    def _initialize_provider(self):
        """
        Initialize the data provider(s) used by this integration.
        This method should create and configure the necessary data provider instances.
        """
        pass

    @abstractmethod
    def _register_event_listeners(self):
        """
        Register event listeners for the data provider(s).
        This method should subscribe to the relevant events from the data provider(s).
        """
        pass