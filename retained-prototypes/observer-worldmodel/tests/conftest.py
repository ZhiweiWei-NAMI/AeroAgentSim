"""
Pytest configuration and fixtures for AirFogSim tests.
"""

import pytest
import sys
import os
from pathlib import Path

# Add src directory to Python path for testing
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

from airfogsim.core.environment import Environment
from airfogsim.agent.drone import DroneAgent


@pytest.fixture
def env():
    """Create a test environment."""
    return Environment()


@pytest.fixture
def drone_agent(env):
    """Create a test drone agent."""
    return env.create_agent(
        DroneAgent,
        "test_drone",
        properties={
            'position': (0, 0, 0),
            'battery_level': 100
        }
    )


@pytest.fixture
def sample_waypoints():
    """Sample waypoints for testing."""
    return [
        (0, 0, 100),    # Take off
        (100, 100, 150), # Midpoint
        (200, 200, 150), # Destination
        (200, 200, 0),   # Land
    ]
