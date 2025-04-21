<a href="https://joss.theoj.org/papers/3bf61975c569326131f0bf169bfe4db9"><img src="https://joss.theoj.org/papers/3bf61975c569326131f0bf169bfe4db9/status.svg"></a>

# AirFogSim: Benchmarking Collaborative Intelligence for Low-Altitude Vehicular Fog Computing

<div align="center">
  <img src="src/airfogsim/docs/img/logo.png" alt="AirFogSim Logo" width="300">
</div>

AirFogSim is a discrete-event simulation framework built on SimPy, designed for benchmarking collaborative intelligence in UAV-integrated fog computing environments. It provides a comprehensive platform for modeling complex interactions between heterogeneous aerial and terrestrial nodes, with a focus on realistic communication, computation, energy, and mobility modeling.

[中文版本](README_CN.md)

## 📋 Project Overview

AirFogSim offers a comprehensive simulation environment for:

- Simulating autonomous agents (like UAVs) in complex environments
- Researching resource allocation and task offloading strategies
- Evaluating collaborative intelligence in low-altitude vehicular fog computing
- Benchmarking different workflows and protocols
- Visualizing simulation processes and analyzing results

The framework employs a modular design, supporting highly customizable simulation scenarios, and provides an intuitive visualization interface for researchers and developers.

If you use AirFogSim in your research, please cite our paper:

```bibtex
@misc{wei2024airfogsimlightweightmodularsimulator,
      title={AirFogSim: A Light-Weight and Modular Simulator for UAV-Integrated Vehicular Fog Computing},
      author={Zhiwei Wei and Chenran Huang and Bing Li and Yiting Zhao and Xiang Cheng and Liuqing Yang and Rongqing Zhang},
      year={2024},
      eprint={2409.02518},
      archivePrefix={arXiv},
      primaryClass={cs.NI},
      url={https://arxiv.org/abs/2409.02518},
}
```

## ✨ Core Features

- **High-Performance Event-Driven Simulation Core:** Optimized event-driven simulation engine achieving sub-O(n log n) computational complexity for critical operations, enabling efficient simulation of large-scale scenarios.

- **Workflow-Based Task Composition Framework:** Flexible and modular workflow-driven task model that explicitly captures task dependencies, resource constraints, and collaborative interactions among heterogeneous nodes.

- **Standards-Compliant Realistic Modeling:** Comprehensive models grounded in established standards, including 3GPP-compliant communication channel models, empirically validated energy consumption profiles, and physics-based mobility patterns.

- **Agent-Centric Autonomy:** Agents (like UAVs) as primary actors with internal state, capable of autonomous decision-making based on their state, assigned workflows, and environmental perception.

- **Component-Based Capabilities:** Clear separation of concerns with components encapsulating specific functionalities (mobility, computation, sensing) and managing task execution environments.

- **Trigger-Based Reactivity:** Flexible mechanism for reacting to various conditions (events, state changes, time), driving workflow state machine transitions and enabling automated responses.

- **Managed Resources:** Simulation resources (landing spots, CPU, airspace, spectrum) managed by dedicated manager classes handling registration, allocation, contention, and dynamic attribute changes.

- **Real-time Visualization:** Integrated frontend interface supporting real-time monitoring and data analysis.

- **LLM Integration:** Support for task planning and decision-making through large language models.

## 🏗️ System Architecture

AirFogSim is built around an event-driven Agent-Based Modeling (ABM) architecture that enables efficient simulation of complex interactions between heterogeneous agents. The platform extends the SimPy discrete-event simulation library, providing specialized components for UAV-integrated fog computing scenarios.

### Backend Architecture

The backend architecture of AirFogSim is designed with a focus on modularity, extensibility, and performance. It consists of several key components:

#### 1. Simulation Environment (Environment)

The central hub of the simulation, extending SimPy's Environment for discrete-event scheduling:
- **Event Registry (EventRegistry):** Central bus for publishing and subscribing to named events across all simulation entities, enabling decoupled communication.
- **Airspace Manager (AirspaceManager):** Octree-based spatial management for position and collision information.
- **Landing Manager (LandingManager):** Management of landing spots and charging stations.
- **Frequency Manager (FrequencyManager):** Management of spectrum resources with 3GPP-compliant channel models.
- **Contract Manager (ContractManager):** Management of contracts and transactions.
- **Workflow Manager (WorkflowManager):** Management of workflow lifecycles.
- **Task Manager (TaskManager):** Management of task creation and execution.
- **Data Provider (DataProvider):** Provision of real-time data and statistics, including weather and traffic flow.

#### 2. Agent (Agent)

Autonomous decision-making entities like UAVs and ground stations:
- **State Management:** Maintains internal state with type validation through metaclass-based state templates.
- **Decision Logic:** SimPy process defining the agent's behavior loop, perceiving state, workflows, and events to decide which tasks to execute.
- **Component Ownership:** Owns components representing its capabilities (mobility, sensing, computation).
- **Task Initiation:** Initiates tasks by delegating execution to appropriate components.
- **Event Handling:** Triggers and subscribes to events for state changes, task lifecycle, and object possession.

#### 3. Component (Component)

Abstracts specific capabilities (mobility, computation, charging) and provides the execution environment for tasks:
- **Task Execution:** Manages task lifecycle, resource acquisition, metrics calculation, and cleanup.
- **Resource Interaction:** Defines resource requirements and requests resources from appropriate managers.
- **Metrics Calculation:** Calculates performance metrics based on resource attributes and agent state.
- **Event Emission:** Triggers namespaced events for task status and metric changes.

#### 4. Task (Task)

Encapsulates the logic for specific actions, defining how work is performed:
- **Execution Logic:** SimPy generator consuming performance metrics provided by components.
- **Metric Consumption:** Declares necessary metrics required from executing components.
- **State Production:** Updates agent state based on task logic and progress.
- **Lifecycle Management:** Manages task status (PENDING, RUNNING, COMPLETED, FAILED, CANCELED).

#### 5. Workflow (Workflow) & State Machine (WorkflowStatusMachine)

Represents higher-level goals or processes, acting as a monitor and coordinator:
- **State Machine:** Contains a WorkflowStatusMachine instance managing internal states and transitions.
- **Trigger-Driven Transitions:** Uses triggers to define rules based on agent state, events, or time.
- **Context/Guidance:** Provides workflow context and suggests next tasks for agents based on current state.

#### 6. Trigger (Trigger)

Monitors specific simulation conditions and executes callbacks when met:
- **Condition Monitoring:** Checks for event occurrences, agent state changes, or time passage.
- **Activation/Deactivation:** Can be activated to monitor and deactivated to stop.
- **Types:** EventTrigger, StateTrigger, TimeTrigger, and CompositeTrigger for different monitoring needs.

#### 7. Resource Layer (Resource & ResourceManager)

Models entities that are utilized or consumed:
- **Resource Base Class:** Defines common properties like id, attributes, and status.
- **ResourceManager Base Class:** Generic base for managing resources of specific types.
- **Specific Managers:** Implement resource-specific logic for finding, allocation, release, and modeling contention.

### Visualization System

AirFogSim integrates a complete visualization system, including:

- **Dashboard:** Displays simulation status, agent information, and system events
- **UAV Monitoring:** Real-time tracking of UAV positions, states, and trajectories
- **Workflow Configuration:** Configuration and monitoring of workflow execution
- **Data Analysis:** Resource usage and performance metrics analysis

<div align="center">
  <img src="src/airfogsim/docs/img/状态监控.png" alt="Status Monitoring Interface" width="800">
  <p><em>Status Monitoring Interface - Real-time tracking of UAV positions and states</em></p>
</div>

The visualization system employs a client-server architecture:
- **Frontend:** React-based web application
  <div align="center">
    <img src="src/airfogsim/docs/img/前端.png" alt="Frontend Interface" width="600">
    <p><em>Frontend Interface - User interaction and data visualization</em></p>
  </div>
- **Frontend:** SUMO-based 3D traffic simulation visualization
  <div align="center">
    <img src="src/airfogsim/docs/img/前端2.png" alt="Frontend Interface" width="600">
    <p><em>Frontend Interface - 3D traffic simulation visualization</em></p>
  </div>
- **Backend:** FastAPI service integrated with the simulation engine
  <div align="center">
    <img src="src/airfogsim/docs/img/后端.png" alt="Backend Architecture" width="600">
    <p><em>Backend Architecture - Data processing and simulation engine integration</em></p>
  </div>
- **Communication:** Real-time data transmission via WebSocket

## 🚀 Installation Guide

### Prerequisites

- Python 3.8+
- Node.js 14+ (only needed for visualization)
- npm 6+ (only needed for visualization)

### Installation Options

#### Option 1: Install from PyPI (Recommended)

The easiest way to install AirFogSim is directly from PyPI:

```bash
pip install airfogsim
```

This will install the core simulation framework. If you want to use the visualization system, you'll need to clone the repository as described in Option 2.

#### Option 2: Install from Source

1. Clone the repository

```bash
git clone https://github.com/ZhiweiWei-NAMI/AirFogSim.git
cd AirFogSim
```

2. Install Python dependencies

```bash
python -m venv airfogsim_venv
source airfogsim_venv/bin/activate  # On Windows: airfogsim_venv\Scripts\activate
pip install -r requirements.txt
pip install -e .  # Install in development mode
```

3. Install frontend dependencies

```bash
cd frontend
npm install
cd ..
```

4. Start the visualization system

```bash
python main_for_visualization.py
```

This will start the backend API service and frontend development server, and automatically open the visualization interface in your browser.


## 📝 Usage Examples

### Basic Simulation Example

```python
from airfogsim.core.environment import Environment
from airfogsim.agent import DroneAgent
from airfogsim.component import MoveToComponent, ChargingComponent
from airfogsim.workflow.inspection import create_inspection_workflow
from airfogsim.helper import check_all_classes, find_compatible_components

# Create environment
env = Environment()

# Check system classes
check_all_classes(env)

# Create drone agent
drone = env.create_agent(
    DroneAgent,
    "drone1",
    initial_position=(10, 10, 0),
    initial_battery=100
)

# Find suitable components
find_compatible_components(env, drone, ['speed'])

# Add components
move_component = MoveToComponent(env, drone)
charging_component = ChargingComponent(env, drone)
drone.add_component(move_component)
drone.add_component(charging_component)

# Create inspection workflow
waypoints = [
    (10, 10, 100),    # Take off
    (400, 400, 150),  # Midpoint
    (800, 800, 150),  # Destination
    (800, 800, 0),    # Land
    (800, 800, 100),  # Take off for return
    (10, 10, 0)       # Return to start
]
workflow = create_inspection_workflow(env, drone, waypoints)

# Start workflow
workflow.start()

# Run simulation
env.run(until=1000)
```

### Using Class Checker Tools

```bash
# Show all classes
python -m airfogsim.helper.class_finder --all

# Find agent classes supporting specific states
python -m airfogsim.helper.class_finder --find-agent position,battery_level

# Find component classes producing specific metrics
python -m airfogsim.helper.class_finder --find-component speed,processing_power
```

### Starting the Visualization Interface

```bash
python main_for_visualization.py --backend-port 8002 --frontend-port 3000
```

## 🧪 Examples and Automated Testing

AirFogSim provides a rich set of example programs demonstrating various features and use cases. These examples are located in the `src/airfogsim/examples` directory:

### Main Examples

- **Basic Trigger System**: `example_trigger_basic.py` - Shows how to use different types of triggers to create and manage workflows
- **Workflow Diagram Generation**: `example_workflow_diagram.py` - Demonstrates how to convert workflow state machines to visual diagrams
- **Image Processing Workflow**: `example_workflow_image_processing.py` - Shows a complete workflow for environmental image sensing and processing
- **Multi-Task Contract**: `example_workflow_contract.py` - Demonstrates how contract workflows manage multiple tasks
- **Drone Inspection**: `example_workflow_inspection.py` - Shows drone inspection path planning and automatic charging
- **Weather Data Integration**: `example_weather_provider.py` - Demonstrates integration of real-time weather data into simulations
- **Benchmark Multi-Workflow**: `example_benchmark_multi_workflow.py` - JOSS paper benchmark example with inspection, logistics, and charging workflows

### One-Click Testing

We provide an automated testing script to easily run and verify all examples:

```bash
# List all available examples
cd src/airfogsim/examples
python test_examples.py --list

# Run specific examples
python test_examples.py --run example_workflow_diagram example_trigger_basic

# Run all examples
python test_examples.py
```

The example testing script automatically checks necessary dependencies (like API keys) and provides detailed test result reports. This allows new users to quickly understand the framework's capabilities and developers to easily verify different modules.

## 📁 Project Structure

```
airfogsim-project/
├── .dockerignore             # Docker build ignore file (backend)
├── .env                      # Backend environment variables (local, not committed to Git)
├── Dockerfile                # Backend Dockerfile
├── docker-compose.yml        # Docker Compose orchestration file
├── frontend/                 # Frontend visualization interface
│   ├── .dockerignore         # Docker build ignore file (frontend)
│   ├── .env                  # Frontend environment variables (local, not committed to Git)
│   ├── Dockerfile            # Frontend Dockerfile
│   ├── build/                # Frontend build artifacts (locally generated)
│   ├── node_modules/         # (local, not committed to Git)
│   ├── package.json
│   ├── public/               # Static assets
│   └── src/                  # Frontend source code
│       ├── pages/            # Page components
│       └── services/         # API services
├── LICENSE                   # Project license
├── main_for_visualization.py # Visualization system startup script (for local development)
├── nginx.conf                # Nginx configuration file
├── pyproject.toml            # Python project configuration file (including dependencies)
├── README.md                 # This document
├── requirements.txt          # Python locked dependencies (generated by pip-compile)
├── src/                      # Backend source code
│   └── airfogsim/            # Core simulation framework
│       ├── agent/            # Agent implementations
│       ├── component/        # Component implementations
│       ├── core/             # Core classes and interfaces
│       ├── docs/             # Documentation
│       ├── event/            # Event handling
│       ├── examples/         # Example code
│       ├── helper/           # Development helper tools
│       ├── manager/          # Various managers
│       ├── resource/         # Resource implementations
│       ├── task/             # Task implementations
│       ├── visualization/    # Visualization-related (FastAPI application)
│       └── workflow/         # Workflow implementations
└── ... (other configuration files, test files, etc.)
```

## 📚 Documentation

Detailed documentation can be found at:

- [System Architecture](src/airfogsim/docs/en/architecture.md)
- [Agent Guide](src/airfogsim/docs/en/agent_guide.md)
- [Component Guide](src/airfogsim/docs/en/component_guide.md)
- [Task Guide](src/airfogsim/docs/en/task_guide.md)
- [Trigger Guide](src/airfogsim/docs/en/trigger_guide.md)
- [Workflow Guide](src/airfogsim/docs/en/workflow_guide.md)
- [Resource Management Guide](src/airfogsim/docs/en/resource_manager_guide.md)
- [Data Provider Guide](src/airfogsim/docs/en/dataprovider_guide.md)
- [Development Helper Tools](src/airfogsim/helper/README.md)
- [Examples](src/airfogsim/examples/README.md)

## 🤝 Contribution Guidelines

We welcome contributions of all kinds, including but not limited to:

- Reporting issues and suggesting improvements
- Submitting code improvements and new features
- Improving documentation and examples
- Sharing use cases and application scenarios

### Best Practices for Developing New Classes

Before developing new agent, component, task, or workflow classes, we recommend using the helper module's class checker tools to see if there are already classes that meet your requirements, avoiding duplicate creation.

```bash
# Check all classes in the system
python -m airfogsim.helper.class_finder --all

# Find agent classes supporting specific states
python -m airfogsim.helper.class_finder --find-agent position,battery_level
```

Please follow these steps:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Use the helper module to check existing classes
4. Commit your changes (`git commit -m 'Add some amazing feature'`)
5. Push to the branch (`git push origin feature/amazing-feature`)
6. Create a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

**AirFogSim** - Powerful simulation tools for low-altitude vehicular fog computing research
