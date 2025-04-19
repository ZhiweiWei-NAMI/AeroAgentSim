# AirFogSim Example Programs

This directory contains multiple example programs for the AirFogSim framework, demonstrating the usage of different features. Below is a brief introduction to each example:

## Basic Function Examples

-   **example_trigger_basic.py**: Demonstrates how to use AirFogSim's trigger system to create and manage workflows, including event-based, state-based, and time-based triggers.
-   **example_workflow_diagram.py**: Shows how to convert workflow state machines into visual diagrams (PlantUML and Mermaid formats).
-   **test_workflow_diagram.py**: Provides another example of workflow diagram generation, directly outputting to the console.

## Sensors and Data Processing

-   **example_weather_openweathermap.py**: Demonstrates how to use the OpenWeatherMap API to fetch real-time weather data and convert it into the simulation system's format.
-   **example_weather_provider.py**: Integrates the WeatherDataProvider within the simulation environment and subscribes to weather change events.
-   **example_workflow_image_processing.py**: Shows an environmental image perception processing workflow, including perception and data processing stages.

## Drone Tasks and Contracts

-   **example_workflow_contract.py**: Demonstrates how to create and execute contract workflows containing multiple tasks.
-   **example_workflow_inspection.py**: Shows a drone inspection path planning and automatic charging workflow.
-   **example_workflow_logistics.py**: Logistics workflow example, simulating courier stations and drone delivery processes.
-   **example_task_priority.py**: Demonstrates task priority and preemption mechanisms, showing the scheduling and preemption process for tasks with different priorities.
-   **example_task_duplicate_check.py**: Demonstrates the task duplicate check mechanism.
-   **example_task_queue_sort.py**: Demonstrates the task queue sorting mechanism.
-   **example_workflow_priority.py**: Demonstrates the workflow priority mechanism.

## Traffic Simulation Integration

-   **example_simulation_traffic.py**: Demonstrates the integration of the SUMO traffic simulator with AirFogSim (requires SUMO installation and configuration).

## Benchmark Examples

-   **example_benchmark_multi_workflow.py**: JOSS paper multi-workflow benchmark example, containing a comprehensive scenario with inspection, logistics, and charging workflows.

## Running Requirements

Some examples require additional setup:

1.  Weather-related examples require setting the OpenWeatherMap API key:
    ```bash
    export OPENWEATHERMAP_API_KEY='your_api_key'
    ```

2.  The traffic simulation example requires SUMO installation and configuration of related files.

## One-Click Testing

You can use the `test_examples.py` script to quickly test multiple examples:

```bash
python test_examples.py          # Test all examples
python test_examples.py --list   # List all available tests
python test_examples.py --run example_workflow_diagram example_trigger_basic  # Test specific examples
```

## Unit Testing

If you want to write tests for AirFogSim components in your own codebase, it is recommended to use Python's `unittest` or `pytest` frameworks. For example:

```python
import unittest
from airfogsim.core.environment import Environment
from airfogsim.agent.drone import DroneAgent

class TestDroneAgent(unittest.TestCase):
    def setUp(self):
        self.env = Environment()
        self.drone = DroneAgent(self.env, "test_drone", {"position": (0, 0, 0)})

    def test_drone_movement(self):
        # Test code...
        # Example assertion (adjust according to actual test logic)
        # Assuming some action moves the drone to (10, 10, 10)
        # self.drone.move_to((10, 10, 10)) # Example action
        # self.env.run(until=some_time)    # Example simulation step
        # self.assertEqual(self.drone.get_state("position"), (10, 10, 10))
        pass # Replace with actual test logic and assertions

if __name__ == "__main__":
    unittest.main()
```