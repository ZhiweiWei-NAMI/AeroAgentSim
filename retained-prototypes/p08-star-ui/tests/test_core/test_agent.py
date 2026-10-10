"""
Tests for the Agent base class and DroneAgent implementation.
"""

import pytest
from airfogsim.core.environment import Environment
from airfogsim.agent.drone import DroneAgent
from airfogsim.component.mobility import MoveToComponent
from airfogsim.component.charging import ChargingComponent


class TestAgent:
    """Test cases for Agent functionality."""

    def test_agent_initialization(self, drone_agent):
        """Test basic agent initialization."""
        assert drone_agent.id is not None
        assert drone_agent.get_state('position') == (0, 0, 0)
        assert drone_agent.get_state('battery_level') == 100
        assert hasattr(drone_agent, 'state')

    def test_agent_state_management(self, drone_agent):
        """Test agent state management."""
        # Test initial state
        assert 'position' in drone_agent.state
        assert 'battery_level' in drone_agent.state
        
        # Test state updates
        drone_agent.update_state('battery_level', 80)
        assert drone_agent.get_state('battery_level') == 80

    def test_component_addition(self, env, drone_agent):
        """Test adding components to agent."""
        move_component = MoveToComponent(env, drone_agent)
        drone_agent.add_component(move_component)
        
        assert len(drone_agent.components) > 0
        assert move_component in drone_agent.components.values()

    def test_multiple_components(self, env, drone_agent):
        """Test adding multiple components."""
        move_component = MoveToComponent(env, drone_agent)
        charging_component = ChargingComponent(env, drone_agent)
        
        drone_agent.add_component(move_component)
        drone_agent.add_component(charging_component)
        
        assert len(drone_agent.components) >= 2

    def test_agent_status(self, drone_agent):
        """Test agent status management."""
        # Test initial status through state
        assert 'status' in drone_agent.state

        # Test status updates
        drone_agent.update_state('status', 'active')
        assert drone_agent.get_state('status') == 'active'

    def test_agent_position_update(self, drone_agent):
        """Test position updates."""
        new_position = (10, 20, 30)
        drone_agent.update_state('position', new_position)
        assert drone_agent.get_state('position') == new_position

    def test_agent_battery_management(self, drone_agent):
        """Test battery level management."""
        # Test battery consumption
        initial_battery = drone_agent.get_state('battery_level')
        drone_agent.update_state('battery_level', initial_battery - 10)
        assert drone_agent.get_state('battery_level') == initial_battery - 10

        # Test battery bounds
        drone_agent.update_state('battery_level', 0)
        assert drone_agent.get_state('battery_level') == 0
