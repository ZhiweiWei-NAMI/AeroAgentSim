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

### Core Components

- **🤖 Agents**: Autonomous entities (UAVs, ground stations) with decision-making capabilities
- **🔧 Components**: Modular capabilities (mobility, computation, sensing) that agents can use
- **📋 Tasks**: Specific actions that agents perform through their components
- **🔄 Workflows**: Higher-level goals that coordinate multiple tasks
- **⚡ Triggers**: Event-driven conditions that drive workflow transitions
- **📊 Resources**: Shared simulation resources (airspace, spectrum, landing spots)
- **🎯 Managers**: Centralized management of resources and system services

For detailed architecture documentation, see [System Architecture Guide](src/airfogsim/docs/en/architecture.md).

### Visualization System

AirFogSim includes an integrated visualization system for real-time monitoring:

- **📊 Dashboard**: Simulation status and agent monitoring
- **🗺️ UAV Tracking**: Real-time position and trajectory visualization
- **⚙️ Workflow Monitor**: Configuration and execution tracking
- **📈 Analytics**: Resource usage and performance metrics

<div align="center">
  <img src="src/airfogsim/docs/img/状态监控.png" alt="Status Monitoring Interface" width="600">
  <p><em>Real-time UAV monitoring and status tracking</em></p>
</div>

**Architecture**: React frontend + FastAPI backend + WebSocket communication

For visualization setup, see [Installation Guide](INSTALL.md#visualization-setup).

## 🚀 Installation Guide

### Quick Start

```bash
pip install airfogsim
```

📋 **Detailed Setup**: See [INSTALL.md](INSTALL.md) for complete installation guide including system requirements, development setup, and troubleshooting.

### Basic Installation

#### Option 1: Install from PyPI (Recommended)

```bash
pip install airfogsim
```

#### Option 2: Install from Source

```bash
git clone https://github.com/ZhiweiWei-NAMI/AirFogSim.git
cd AirFogSim
pip install -e .[dev]
```

For visualization system setup and advanced configuration options, please refer to the [detailed installation guide](INSTALL.md).


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

## 🧪 Examples and Testing

### Examples

AirFogSim provides a rich set of example programs demonstrating various features and use cases. These examples are located in the `src/airfogsim/examples` directory:

- **Basic Trigger System**: `example_trigger_basic.py` - Shows how to use different types of triggers to create and manage workflows
- **Workflow Diagram Generation**: `example_workflow_diagram.py` - Demonstrates how to convert workflow state machines to visual diagrams
- **Image Processing Workflow**: `example_workflow_image_processing.py` - Shows a complete workflow for environmental image sensing and processing
- **Multi-Task Contract**: `example_workflow_contract.py` - Demonstrates how contract workflows manage multiple tasks
- **Drone Inspection**: `example_workflow_inspection.py` - Shows drone inspection path planning and automatic charging
- **Weather Data Integration**: `example_weather_provider.py` - Demonstrates integration of real-time weather data into simulations
- **Benchmark Multi-Workflow**: `example_benchmark_multi_workflow.py` - JOSS paper benchmark example with inspection, logistics, and charging workflows

### Running Examples

```bash
# List all available examples
airfogsim examples

# Run specific examples
airfogsim examples workflow_diagram trigger_basic

# Run a single example directly
cd src/airfogsim/examples
python example_trigger_basic.py
```

### Automated Testing

AirFogSim includes a comprehensive test suite to ensure reliability and catch regressions:

```bash
# Install test dependencies
pip install -e .[dev]

# Run all tests
pytest tests/ -v

# Run tests with coverage
pytest tests/ --cov=airfogsim --cov-report=html

# Run only fast tests
pytest tests/ -m "not slow"
```

The test suite includes:
- **Unit tests** for core functionality
- **Integration tests** for component interactions
- **Example tests** to verify all examples run correctly
- **Continuous Integration** via GitHub Actions

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
├── INSTALL.md                # Detailed installation guide
├── CONTRIBUTING.md           # Contributing guidelines
├── main_for_visualization.py # Visualization system startup script (for local development)
├── pyproject.toml            # Python project configuration file (including dependencies)
├── README.md                 # This document (project overview)
├── requirements.txt          # Python locked dependencies (generated by pip-compile)
├── docs/                     # User documentation (Sphinx-based)
│   ├── README.md             # Documentation navigation hub
│   ├── api/                  # Auto-generated API reference
│   └── guides/               # User guides and tutorials
├── src/                      # Backend source code
│   └── airfogsim/            # Core simulation framework
│       ├── agent/            # Agent implementations
│       ├── component/        # Component implementations
│       ├── core/             # Core classes and interfaces
│       ├── docs/             # Technical documentation (developer-focused)
│       │   ├── en/           # English technical guides
│       │   ├── cn/           # Chinese technical guides
│       │   └── img/          # Documentation images
│       ├── event/            # Event handling
│       ├── examples/         # Example code and tutorials
│       ├── helper/           # Development helper tools
│       ├── manager/          # Various managers
│       ├── resource/         # Resource implementations
│       ├── task/             # Task implementations
│       ├── visualization/    # Visualization-related (FastAPI application)
│       └── workflow/         # Workflow implementations
└── ... (other configuration files, test files, etc.)
```

## 📚 Documentation

### 📖 For Users
- **[Getting Started](docs/getting_started.html)** - Installation and first simulation
- **[User Guide](docs/user_guide.html)** - Comprehensive usage guide
- **[API Reference](docs/api/index.html)** - Complete API documentation
- **[Examples](docs/examples.html)** - Ready-to-run examples

### 🔧 For Developers
- **[System Architecture](src/airfogsim/docs/en/architecture.md)** - Detailed system design
- **[Development Guides](src/airfogsim/docs/en/)** - Technical documentation
- **[Helper Tools](src/airfogsim/helper/README.md)** - Development utilities

### 🌍 中文文档
- **[系统架构](src/airfogsim/docs/cn/architecture.md)** - 系统设计详解
- **[开发指南](src/airfogsim/docs/cn/)** - 技术文档

**📋 Documentation Hub**: See [docs/README.md](docs/README.md) for complete navigation

## 🤝 Contributing

We welcome contributions of all kinds! Please see our [Contributing Guide](CONTRIBUTING.md) for detailed information on:

- How to report bugs and request features
- Development setup and coding standards
- Testing guidelines and best practices
- Pull request process
- Community guidelines

### Quick Start for Contributors

```bash
# Fork and clone the repository
git clone https://github.com/YOUR_USERNAME/AirFogSim.git
cd AirFogSim

# Set up development environment
pip install -e .[dev]

# Check existing classes before creating new ones
python -m airfogsim.helper.class_finder --all

# Run tests
pytest tests/ -v
```

For detailed contribution guidelines, please read [CONTRIBUTING.md](CONTRIBUTING.md).

## 📄 License

This project is licensed under the Apache 2.0 - see the [LICENSE](LICENSE) file for details.

---

**AirFogSim** - Powerful simulation tools for low-altitude vehicular fog computing research
