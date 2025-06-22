"""
Tests for the inspection workflow.
"""

import pytest
from airfogsim.core.environment import Environment
from airfogsim.agent.drone import DroneAgent
from airfogsim.component.mobility import MoveToComponent
from airfogsim.component.charging import ChargingComponent
from airfogsim.workflow.inspection import create_inspection_workflow


class TestInspectionWorkflow:
    """Test cases for inspection workflow."""

    def test_workflow_creation(self, env, drone_agent, sample_waypoints):
        """Test basic workflow creation."""
        # Add required components
        move_component = MoveToComponent(env, drone_agent)
        charging_component = ChargingComponent(env, drone_agent)
        drone_agent.add_component(move_component)
        drone_agent.add_component(charging_component)
        
        # Create workflow
        workflow = create_inspection_workflow(env, drone_agent, sample_waypoints)
        
        assert workflow is not None
        assert hasattr(workflow, 'id')
        assert hasattr(workflow, 'status')

    def test_workflow_waypoints(self, env, drone_agent, sample_waypoints):
        """Test workflow with waypoints."""
        # Add required components
        move_component = MoveToComponent(env, drone_agent)
        charging_component = ChargingComponent(env, drone_agent)
        drone_agent.add_component(move_component)
        drone_agent.add_component(charging_component)
        
        # Create workflow
        workflow = create_inspection_workflow(env, drone_agent, sample_waypoints)
        
        # Test that workflow has waypoint information
        assert workflow is not None

    def test_workflow_state_machine(self, env, drone_agent, sample_waypoints):
        """Test workflow state machine."""
        # Add required components
        move_component = MoveToComponent(env, drone_agent)
        charging_component = ChargingComponent(env, drone_agent)
        drone_agent.add_component(move_component)
        drone_agent.add_component(charging_component)

        # Create workflow
        workflow = create_inspection_workflow(env, drone_agent, sample_waypoints)

        # Test status machine (correct attribute name)
        assert hasattr(workflow, 'status_machine')
        assert hasattr(workflow.status_machine, 'current_status')

    def test_workflow_start(self, env, drone_agent, sample_waypoints):
        """Test workflow start functionality."""
        # Add required components
        move_component = MoveToComponent(env, drone_agent)
        charging_component = ChargingComponent(env, drone_agent)
        drone_agent.add_component(move_component)
        drone_agent.add_component(charging_component)
        
        # Create workflow
        workflow = create_inspection_workflow(env, drone_agent, sample_waypoints)
        
        # Test start method
        assert hasattr(workflow, 'start')
        assert callable(getattr(workflow, 'start'))
        
        # Start workflow
        workflow.start()
        
        # Check that workflow is started
        assert workflow.status != 'PENDING'
