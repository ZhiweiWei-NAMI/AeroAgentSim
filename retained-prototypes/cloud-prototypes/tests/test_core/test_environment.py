"""
Tests for the Environment class.
"""

import pytest
from airfogsim.core.environment import Environment
from airfogsim.agent.drone import DroneAgent


class TestEnvironment:
    """Test cases for Environment class."""

    def test_environment_creation(self):
        """Test basic environment creation."""
        env = Environment()
        assert env is not None
        assert hasattr(env, 'now')
        assert env.now == 0

    def test_agent_creation(self, env):
        """Test agent creation through environment."""
        agent = env.create_agent(
            DroneAgent,
            "test_drone",
            properties={
                'position': (10, 20, 30),
                'battery_level': 80
            }
        )

        assert agent is not None
        assert agent.get_state('position') == (10, 20, 30)
        assert agent.get_state('battery_level') == 80

    def test_multiple_agents(self, env):
        """Test creating multiple agents."""
        agent1 = env.create_agent(
            DroneAgent,
            "drone1",
            properties={
                'position': (0, 0, 0),
                'battery_level': 100
            }
        )

        agent2 = env.create_agent(
            DroneAgent,
            "drone2",
            properties={
                'position': (100, 100, 100),
                'battery_level': 90
            }
        )

        assert agent1.id != agent2.id
        assert agent1.get_state('position') != agent2.get_state('position')

    def test_environment_managers(self, env):
        """Test that environment has required managers."""
        assert hasattr(env, 'agent_manager')
        assert hasattr(env, 'airspace_manager')
        assert hasattr(env, 'landing_manager')
        assert hasattr(env, 'frequency_manager')
        assert hasattr(env, 'workflow_manager')
        assert hasattr(env, 'task_manager')

    def test_event_registry(self, env):
        """Test event registry functionality."""
        assert hasattr(env, 'event_registry')

        # Test event subscription and triggering
        events_received = []

        def event_handler(event_data):
            events_received.append(event_data)

        # Subscribe with proper parameters (source_id, event_name, listener_id, callback)
        env.event_registry.subscribe("test_source", "test_event", "test_listener", event_handler)
        env.event_registry.trigger_event("test_source", "test_event", {"data": "test"})

        assert len(events_received) == 1
        assert events_received[0]["data"] == "test"
