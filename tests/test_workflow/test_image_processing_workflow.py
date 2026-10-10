"""
Tests for the image processing workflow.
"""

from airfogsim.agent.drone import DroneAgent
from airfogsim.component.computation import ComputationComponent
from airfogsim.component.img_sensor import ImageSensingComponent
from airfogsim.component.mobility import MoveToComponent
from airfogsim.core.trigger import TimeTrigger
from airfogsim.workflow.image_processing import create_image_processing_workflow


class TestImageProcessingWorkflow:
    """Regression coverage for image-processing workflow startup semantics."""

    def test_image_processing_workflow_uses_one_time_start_trigger(self, env):
        """The default demo workflow should use a one-time delayed start trigger."""
        drone = env.create_agent(
            DroneAgent,
            "image_drone",
            properties={
                "position": (0, 0, 0),
                "battery_level": 100,
            },
        )
        drone.add_component(MoveToComponent(env, drone))
        drone.add_component(ComputationComponent(env, drone))
        drone.add_component(ImageSensingComponent(env, drone))

        workflow = create_image_processing_workflow(
            env,
            drone,
            sensing_locations=[(10, 10, 5)],
        )

        triggers = env.workflow_manager.workflow_triggers[workflow.id]
        assert len(triggers) == 1

        start_trigger = triggers[0]
        assert isinstance(start_trigger, TimeTrigger)
        assert start_trigger.interval is None
        assert start_trigger.trigger_time == env.now + 10
