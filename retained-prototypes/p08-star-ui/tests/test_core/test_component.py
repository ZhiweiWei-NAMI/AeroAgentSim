"""
Tests for the Component base class and implementations.
"""

import pytest
from airfogsim.core.environment import Environment
from airfogsim.agent.drone import DroneAgent
from airfogsim.component.mobility import MoveToComponent
from airfogsim.component.charging import ChargingComponent


class TestComponent:
    """Test cases for Component functionality."""

    def test_component_initialization(self, env, drone_agent):
        """Test basic component initialization."""
        component = MoveToComponent(env, drone_agent)

        assert component.env == env
        assert component.agent == drone_agent
        assert hasattr(component, 'name')
        assert hasattr(component, 'is_error')

    def test_component_metrics(self, env, drone_agent):
        """Test component metrics functionality."""
        component = MoveToComponent(env, drone_agent)

        # Test that component has required metrics
        assert hasattr(component, 'PRODUCED_METRICS')
        assert hasattr(component, 'MONITORED_STATES')

    def test_multiple_components_same_agent(self, env, drone_agent):
        """Test adding multiple components to the same agent."""
        move_component = MoveToComponent(env, drone_agent)
        charging_component = ChargingComponent(env, drone_agent)
        
        drone_agent.add_component(move_component)
        drone_agent.add_component(charging_component)
        
        assert len(drone_agent.components) >= 2
        assert move_component.name != charging_component.name

    def test_component_status_management(self, env, drone_agent):
        """Test component status management."""
        component = MoveToComponent(env, drone_agent)

        # Test initial error status
        assert hasattr(component, 'is_error')
        assert component.is_error == False

        # Test status updates
        component.disable()
        assert component.is_error == True

        component.enable()
        assert component.is_error == False

    def test_component_task_execution_interface(self, env, drone_agent):
        """Test that components have task execution interface."""
        component = MoveToComponent(env, drone_agent)
        
        # Test that component has execute_task method
        assert hasattr(component, 'execute_task')
        assert callable(getattr(component, 'execute_task'))

    def test_component_resource_requirements(self, env, drone_agent):
        """Test component resource requirements."""
        component = MoveToComponent(env, drone_agent)

        # Test that component can specify resource requirements
        assert hasattr(component, '_calculate_performance_metrics') or hasattr(component, 'PRODUCED_METRICS')

    def test_charging_component_specific(self, env, drone_agent):
        """Test charging component specific functionality."""
        charging_component = ChargingComponent(env, drone_agent)

        assert charging_component is not None
        assert hasattr(charging_component, 'PRODUCED_METRICS')
        assert hasattr(charging_component, 'MONITORED_STATES')

        # Test specific charging component attributes
        assert hasattr(charging_component, 'charging_factor')
        assert hasattr(charging_component, 'charging_efficiency')
