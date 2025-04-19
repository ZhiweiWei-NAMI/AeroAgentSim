---
title: 'AirFogSim: A Python Framework for Benchmarking Collaborative Intelligence in Low-Altitude Vehicular Fog Computing'
tags:
  - Python
  - UAV
  - fog computing
  - simulation
  - vehicular networks
  - collaborative intelligence
  - agent-based modeling
authors:
  - name: Zhiwei Wei
    orcid: 0000-0002-8756-9035
    affiliation: 1
    corresponding: true
  - name: Bing Li
    affiliation: 2
  - name: Xiang Cheng
    affiliation: 3
  - name: Liuqing Yang
    affiliation: 4
  - name: Rongqing Zhang
    affiliation: 2
affiliations:
 - name: Shanghai Research Institute for Intelligent Autonomous Systems, Tongji University, China
   index: 1
 - name: School of Software Engineering, Tongji University, China
   index: 2
 - name: School of Electronics, Peking University, China
   index: 3
 - name: Internet of Things Thrust & Intelligent Transportation Thrust, The Hong Kong University of Science and Technology (Guangzhou), China
   index: 4
date: 17 April 2024
bibliography: paper.bib
---

# Summary

The rapid proliferation of Unmanned Aerial Vehicles (UAVs) is reshaping the landscape of low-altitude airspace, driving the emergence of a novel paradigm termed the *Low-Altitude Economy* [@low_altitude_market_A]. This transformation extends aerial applications beyond traditional military and specialized domains into diverse civilian and commercial sectors, including logistics, precision agriculture, surveillance, and advanced air mobility [@uav_its_integration; @uav_challenges_survey]. To effectively manage this increasingly complex and dynamic environment, there is a pressing need for intelligent, scalable, and real-time management solutions [@joint_Y_Liu; @foggy_Muham]. Consequently, the concept of *Low-Altitude Vehicular Networks (LAVNs)* has been proposed, aiming to seamlessly integrate aerial vehicles with terrestrial infrastructure and services [@highway_uav]. AirFogSim provides a lightweight yet comprehensive Python-based simulation framework for modeling the complex dynamics, resource constraints, and collaborative intelligence strategies within these Low-Altitude Vehicular Fog Computing (LAVFC) systems, facilitating research and development in this emerging field.

# Statement of need

Existing simulation platforms exhibit notable limitations when applied to UAV-integrated Vehicular Fog Computing (VFC) scenarios [@vfogsim; @large_scale_vfc], particularly in their inability to simultaneously model communication, computation, energy, security, mobility (both UAV and vehicular), and realistic urban topologies [@se_vfc; @trust_comp]. General fog/edge simulators often lack realistic mobility and communication models [@ifogsim2; @edgecloudsim; @ifogsim], while network simulators may neglect energy or computation aspects [@fognetsim++; @veins; @omenet++]. UAV-specific simulators often lack detailed urban/vehicular context [@marsim; @skywalker]. There is a need for a unified platform to benchmark collaborative intelligence strategies considering these intertwined aspects [@madrl; @blockchain_evastrop].

AirFogSim addresses these gaps by providing a comprehensive, modular, and scalable simulation environment tailored explicitly for benchmarking collaborative intelligence in UAV-integrated fog computing scenarios [@cloudsim]. Key contributions include: 1) A high-performance event-driven core based on SimPy [@simpy]; 2) A flexible workflow-based framework for modeling complex tasks and dependencies [@folo_zhu; @contract_matching_zhou]; 3) Integration of standards-compliant models (e.g., 3GPP communication [@3gpp_36777], validated energy profiles [@winner2_model]); 4) An open-source platform with benchmark scenarios for comparative analysis of collaborative strategies, task offloading [@vfc_priority_Jinming_Shi; @ocvc], and resource management in LAVFC [@traffic_load_vec; @redundant_resource].

# AirFogSim Fundamentals

AirFogSim employs a modular, event-driven Agent-Based Modeling (ABM) architecture using the SimPy discrete-event simulation library [@simpy]. Key concepts include:

*   **`Environment`**: Orchestrates simulation time, manages agent/resource registries, and facilitates event communication via the `EventRegistry`. It serves as the central hub for all simulation entities and managers.
*   **`Agent`**: Represents active entities (UAVs, base stations, edge nodes). Each has an ID, internal `state` (position, battery), attached `components`, and can interact with workflows through the environment's workflow manager.
    ```python
    # Simplified Agent structure
    class Agent:
        def __init__(self, env, agent_name, properties=None):
            self.env = env; self.id = agent_name
            self.state = {}; self.components = {}
            self.managed_tasks = {}
            # ... (Initialize state based on properties) ...
    ```
*   **`Component`**: Encapsulates agent capabilities (e.g., `MobilityComponent`, `ComputationComponent`). Components monitor agent state, generate performance metrics, and execute relevant tasks.
    ```python
    # Simplified Component structure
    class Component:
        PRODUCED_METRICS = []  # Metrics produced by this component
        MONITORED_STATES = []  # Agent states this component monitors

        def __init__(self, env, agent, name=None, supported_events=[], properties=None):
            self.env = env; self.agent = agent; self.name = name or self.__class__.__name__
            self.current_metrics = {metric: None for metric in self.PRODUCED_METRICS}
            # ... (Task management, event registration) ...
        def _calculate_performance_metrics(self): pass # Implemented by subclass
        def execute_task(self, task): pass # Executes a task and returns a SimPy process
    ```
*   **`Task`**: An atomic unit of work (e.g., `MoveToTask`, `ImageProcessingTask`) executed by a component. Tasks consume metrics, potentially modify agent state (`PRODUCED_STATES`), and adhere to priorities.
    ```python
    # Simplified Task structure
    class Task:
        NECESSARY_METRICS = []  # Metrics required from the component
        PRODUCED_STATES = []    # Agent states this task will modify

        def __init__(self, env, agent, component_name, task_name,
                     workflow_id=None, target_state=None, properties=None):
            self.id = 'task_'+str(uuid.uuid4().hex[:8])
            self.agent = agent; self.component_name = component_name
            self.status = TaskStatus.PENDING
            self.priority = TaskPriority.from_string(properties.get('priority', 'normal'))
            # ... (Initialize other properties) ...

        def execute(self, env, initial_metrics): # SimPy generator process
            # Core execution loop
            while self.progress < 1.0:
                # Calculate remaining time based on metrics
                # yield env.timeout(duration) or wait for metric changes
                # Update task state and agent state
                self._update_task_state(self.current_metrics)  # Abstract method, implemented by subclass
                self.agent.update_states(self._get_task_specific_state_repr())  # Abstract method, implemented by subclass

            self.complete()
            return self.result
    ```
*   **`Workflow`**: Defines higher-level processes or mission plans (e.g., `InspectionWorkflow`, `ChargingWorkflow`) involving multiple tasks. Uses a `WorkflowStatusMachine` to manage states and transitions triggered by time, events, or agent state changes.
    ```python
    # Simplified Workflow structure
    class Workflow(metaclass=WorkflowMeta):
        def __init__(self, env, name, owner, timeout=None,
                     event_names=[], initial_status='idle',
                     callback=None, properties=None):
            self.id = f'workflow_'+str(uuid.uuid4().hex[:8])
            self.env = env; self.name = name; self.owner = owner
            self.status = WorkflowStatus.PENDING
            self.properties = properties or {}

            # Create the state machine
            self.status_machine = WorkflowStatusMachine(self, initial_status)
            self._setup_transitions() # Defined by subclass

        def _setup_transitions(self):
            # Define state machine transitions using the abstractmethod decorator
            # self.status_machine.add_transition(from_state, to_state, trigger)
            pass

        def get_current_suggested_task(self) -> Optional[Dict]:
            # Return a task suggestion based on current state
            # Agents can use this to decide what to do next
            return None
    ```
*   **`EventRegistry`**: Central publish-subscribe system for decoupled communication between simulation entities.
*   **`Resource` / `ResourceManager`**: Models limited shared assets (e.g., charging spots, spectrum) managed by specific managers.

The simulation proceeds via a chronological event queue managed by the `Environment`, enabling efficient modeling of asynchronous operations.

# Part 1: Developing Custom Extensions for AirFogSim

AirFogSim's object-oriented design facilitates extension through inheritance.

1.  **Custom Agent:** Inherit from `airfogsim.agent.Agent` (or subclasses like `DroneAgent`). Define custom `_state_templates` in `__init__` for unique properties and add necessary `Component` instances via `self.add_component()`.
2.  **Custom Component:** Inherit from `airfogsim.component.Component`. Implement `_calculate_performance_metrics` to determine capabilities based on `self.agent.state`. Optionally, override `execute_task` or implement SimPy processes for task handling.
3.  **Custom Task:** Inherit from `airfogsim.task.Task`. Define class attributes `NECESSARY_METRICS` and `PRODUCED_STATES`. Implement the core logic within the `execute(self, initial_metrics)` SimPy generator function, yielding delays (`env.timeout`), handling metric changes, and updating agent state via `self.agent.update_states()`.
4.  **Custom Workflow:** Inherit from `airfogsim.workflow.Workflow`. Implement `_setup_transitions` using `self.status_machine.add_state()` and `self.status_machine.add_transition()` with appropriate triggers (`StateTrigger`, `TimeTrigger`, `EventTrigger`). Implement callback methods for state entry/exit or transition actions, typically involving submitting tasks via `self.owner.execute_task()`.

# Part 2: Using AirFogSim - A Collaborative Intelligence Example

Setting up a simulation involves assembling these components:

1.  **Initialize Environment & Managers:** Create the main `Environment` and standard `ResourceManager` instances.
    ```python
    from airfogsim.core.environment import Environment
    env = Environment(visual_interval=1.0)
    # ResourceManagers are automatically created during Environment initialization
    ```
2.  **Create Static Agents:** Define base stations or edge nodes, add their components, and register them.
    ```python
    from airfogsim.agent.drone import DroneAgent # Base for many agent types
    from airfogsim.component.computation import ComputationComponent
    # Create Base Station via manager
    base_station = env.landing_manager.create_base_station(...)
    # Create Edge Node Agent
    edge_node = env.create_agent(DroneAgent, "edge_1", properties={'is_static': True, ...})
    edge_node.add_component(ComputationComponent(env, edge_node))
    ```
3.  **Create Mobile Agents (Drones):** Instantiate drone agents with specific properties and components.
    ```python
    from airfogsim.component.mobility import MoveToComponent
    from airfogsim.component.sensing import SensingComponent
    drone = env.create_agent(DroneAgent, "drone_1", properties={'max_speed': 15.0, ...})
    drone.add_component(MoveToComponent(env, drone))
    drone.add_component(SensingComponent(env, drone))
    # ... add other components ...
    ```
4.  **Define and Assign Workflows:** Instantiate workflow classes, assign them to agents (`owner`), provide parameters (`properties`), and define activation conditions (`start_trigger`).
    ```python
    from airfogsim.workflow.charging import ChargingWorkflow
    from airfogsim.workflow.image_processing import ImageProcessingWorkflow
    from airfogsim.core.trigger import StateTrigger, TimeTrigger
    charging_workflow = env.create_workflow(
        ChargingWorkflow, owner=drone, name=f"Charge_{drone.id}",
        properties={'battery_threshold': 30.0, ...},
        start_trigger=StateTrigger(env, agent_id=drone.id, state_key='battery_level',
                                  condition='<', value=30.0)
    )
    img_proc_workflow = env.create_workflow(
        ImageProcessingWorkflow, owner=drone, name=f"Process_{drone.id}",
        properties={'inspection_points': [...], ...},
        start_trigger=TimeTrigger(env, trigger_time=env.now + 10)
    )
    ```
5.  **Run Simulation:** Optionally start the visualization server and execute the simulation loop.
    ```python
    # Start the visualization server using the provided script
    # python main_for_visualization.py --backend-port 8002 --frontend-port 3000

    # Run the simulation
    env.run(until=3600) # Run for 3600 simulated seconds
    ```

# Visualization and Analysis

AirFogSim includes a web-based visualization system using FastAPI and React for real-time monitoring. It provides:

1.  **3D Map View**: Shows agent positions, trajectories, resource locations (Figure \ref{fig:monitor}) with support for UAV coverage optimization [@coverage_uav].
2.  **Dashboard**: Displays key performance indicators and system events.
3.  **Workflow Visualization**: Renders workflow state machines in diagrams.

Integration with SUMO [@sumo_2012] for traffic simulation (Figure \ref{fig:3d}) and PlantUML/Mermaid for static workflow diagrams is also supported. The system also incorporates radio propagation models [@winprop] for realistic communication simulation.

![AirFogSim's real-time monitoring interface showing UAV positions, states, and trajectories.](src/airfogsim/docs/img/状态监控.png){#fig:monitor}

![3D visualization of UAVs in an urban environment with integrated traffic simulation.](src/airfogsim/docs/img/前端2.png){#fig:3d}



# Acknowledgements

We acknowledge the support from the National Natural Science Foundation of China (Grant No. 62101370), the Shanghai Sailing Program (Grant No. 21YF1429400), and the Fundamental Research Funds for the Central Universities.

