## AirFogSim Agent Developer Documentation

This document provides guidance on understanding and extending the `airfogsim.core.agent.Agent` class within the AirFogSim framework. It explains the core concepts, how to define custom agent types, and how to implement their behavior.

### 1. Overview of the `Agent` Class

The `Agent` class represents an active, decision-making entity within the simulation environment (`simpy.Environment`). Agents are the primary actors that interact with the simulated world by:

1.  **Maintaining State:** Tracking internal properties (e.g., position, status) and potentially properties of possessed objects (e.g., charging station status) using a structured state management system.
2.  **Owning Components:** Utilizing functional units (`Component`) like sensors, actuators (e.g., mobility systems), or compute resources to perform actions.
3.  **Executing Tasks:** Creating `Task` objects and delegating their execution to appropriate components.
4.  **Reacting to Events:** Responding to changes in the environment or their own internal state via a publish-subscribe event system.
5.  **Managing Possessed Objects:** Holding references to other simulation objects (like charging stations, landing spots) and accessing their states.
6.  **Interacting with Contracts:** (Optional) Creating, accepting, and managing task offloading contracts via the `ContractManager`.
7.  **Defining Behavior:** Implementing the core decision-making logic within the `live()` method.

The base `Agent` class provides the foundational structure and mechanisms for these capabilities. Subclasses define specific agent types (like `DroneAgent`, `GroundStationAgent`, etc.) by adding specialized states, components, possessed objects, and behavior logic.

### 2. Core Concepts

#### 2.1 State Management (`StateTemplate`, `AgentMeta`)

*   **Purpose:** Agents track their properties using a `self.state` dictionary. To ensure consistency, type safety, and define requirements, the framework uses `StateTemplate`. Agents can also access the state of objects they possess using composite keys (e.g., `'charging_station.status'`).
*   **`StateTemplate`:** Defines the expected properties of a state variable:
    *   `key` (str): The name of the state variable.
    *   `value_type` (type, Optional): The expected Python type (e.g., `int`, `float`, `str`, `tuple`, `List[int]`). `None` allows any type. Supports basic generics like `List`.
    *   `required` (bool): Whether this state *must* be initialized. A warning is issued if missing.
    *   `validator` (callable, Optional): A function that takes the value and returns `True` if valid, `False` otherwise.
    *   `description` (str, Optional): A human-readable description.
*   **`AgentMeta` (Metaclass):** This metaclass automatically handles the inheritance and aggregation of `StateTemplate` definitions across the class hierarchy. Each agent instance validates its state updates against the *combined* templates of its entire class hierarchy.
*   **Registering Templates:**
    *   **Recommended (via Metaclass):** Define a custom metaclass inheriting from `AgentMeta` (like `DroneAgentMeta`) and call `mcs.register_template(cls, ...)` within its `__new__` method. This is cleaner for organizing type-specific states.
    *   **Directly on Class:** Use the `@classmethod` decorator `register_state_template` directly on your agent subclass.
        ```python
        # Example using class decorator
        @Agent.register_state_template('position', value_type=tuple, required=True, description="Agent's 3D position")
        @Agent.register_state_template('status', value_type=str, required=True, default='idle')
        class MySimpleAgent(Agent):
            # ... Agent implementation ...
            pass

        # Example using Metaclass (preferred for complex agents)
        class DroneAgentMeta(AgentMeta):
            def __new__(mcs, name, bases, attrs):
                cls = super().__new__(mcs, name, bases, attrs)
                mcs.register_template(cls, 'battery_level', float, True, validator=lambda x: 0.0 <= x <= 100.0)
                mcs.register_template(cls, 'flight_mode', str, False, default='manual')
                # ... other drone-specific states
                return cls

        class DroneAgent(Agent, metaclass=DroneAgentMeta):
            # ... Agent implementation ...
            pass
        ```
*   **Using State:**
    *   `initialize_states(**states)`: Set initial state values during `__init__`. Checks required states.
    *   `update_state(key, value)` / `set_state(key, value)`: Update a single state value. Triggers validation and the `state_changed` event if the value changes.
    *   `update_states(state_dict)`: Update multiple states.
    *   `get_state(key, default=None)`: Retrieve a state value. Supports composite keys (e.g., `agent.get_state('my_station.is_available')`).
    *   `has_state(key)`: Check if a state (including composite keys) exists.
    *   `get_current_states()`: Get a copy of the agent's own state dictionary (does not include possessed object states).
    *   `get_state_templates()`: (Class method) Get all applicable templates for the class.

#### 2.2 Component Management

*   **Purpose:** Components (`airfogsim.core.Component`) represent the functional parts of an agent (e.g., 'MobilitySystem', 'CPUEngine', 'CameraSensor'). Agents delegate task execution to their components.
*   **Usage:**
    *   `add_component(component)`: Attach a `Component` instance to the agent. The component gets a reference back to the agent (`component.agent`). The agent checks if it has the necessary state templates defined for the component's `MONITORED_STATES`.
    *   `get_component(component_name)`: Retrieve a component by its name.
    *   `get_components()`: Get a list of all attached component instances.
    *   `get_component_names()`: List the names of all attached components.
*   **Example:** An agent needs a `MobilityComponent` to move and a `ComputeComponent` to process data. These would be added during initialization or configuration.

#### 2.3 Event Handling

*   **Purpose:** Agents can react to and announce significant occurrences using a publish-subscribe system managed by `env.event_registry`.
*   **Standard Agent Events:** The base `Agent` automatically registers and triggers:
    *   `state_changed`: Fired when `update_state` changes a value (for the agent's own state or indirectly via possessed objects). Event data includes `key`, `old_value`, `new_value`, `time`, and potentially `object_name`.
    *   `task_started`: Fired by the agent when `execute_task` successfully initiates a task.
    *   `task_completed`: Fired by the agent's internal monitor (`_monitor_task_execution`) when a task it initiated completes (successfully, failed, or canceled).
    *   `possessing_object_added` / `possessing_object_removed`: Fired when objects are added/removed via `add_possessing_object` / `remove_possessing_object`.
    *   *(Note: Component-specific events like `ComponentName.task_completed` are triggered by the Component itself, but the agent can subscribe to them).*
*   **Usage:**
    *   `register_event(event_name)`: Declare an event this agent might trigger.
    *   `trigger_event(event_name, value=None)`: Fire an event, notifying all subscribers. `agent_id` is automatically added to the value.
    *   `subscribe(source_id, event_name, callback, listener_id=None)`: Register a `callback` function to be called when the `source_id` triggers `event_name`.
    *   `unsubscribe(source_id, event_name, listener_id)`: Stop listening to an event.
    *   `unsubscribe_all()`: Remove all subscriptions *made by* this agent.
    *   `get_event(event_name)`: Get the underlying `simpy.Event` object for this agent's event (useful for `yield`ing).
    *   `has_event(event_name)`: Check if the agent has registered a specific event.

#### 2.4 Task Execution

*   **Purpose:** Agents decide *what* needs to be done and *which component* should do it. They create `Task` objects (via `env.task_manager`) and ask components to execute them.
*   **`Task` Object:** Represents a unit of work with properties like name, status, start/end times, associated workflow, etc. Specific task types (e.g., `MoveToTask`, `ComputeTask`) inherit from `airfogsim.core.Task`.
*   **`execute_task(...)` Method:**
    *   This is the agent's primary way to initiate work.
    *   It takes the `component_name`, `task_name`, `task_class` (string name of the Task subclass), `target_state`, `properties` for the task, optional `workflow_id`, and optional `task_id`.
    *   It uses `env.task_manager.create_task` to instantiate the `Task`.
    *   **Crucially, it's non-blocking.** It triggers the `task_started` event, asks the component to start execution (`component.execute_task(task)`), starts an internal monitoring process (`_monitor_task_execution`), and returns the `Task` object *immediately*.
    *   Returns `None` if the component is not found.
*   **`_monitor_task_execution(...)` (Internal):**
    *   A SimPy process that `yield`s the component's execution process (`component_exec_proc`).
    *   Handles completion, failure, or interruption of the task execution by the component.
    *   Updates the `Task` status/result based on the outcome (primarily relying on the Task object's final state).
    *   Triggers the agent's `task_completed` event.
    *   Manages the `self.managed_tasks` dictionary (tracking active tasks initiated by the agent).
*   **`cancel_task(task_id)`:**
    *   Attempts to interrupt the running process associated with the task, both within the component (`component.task_processes`) and the agent's monitor.
    *   Updates the task status to `CANCELED`.
    *   Triggers a `ComponentName.task_canceled` event.
*   **Workflow:** An agent's `live()` method typically decides *when* and *which* tasks to execute based on its state, goals, assigned workflows, or incoming events.

#### 2.5 Possessing Objects Management

*   **Purpose:** Allows an agent to hold references to other simulation entities (e.g., a drone possessing a specific charging station or landing spot) and interact with their state.
*   **Usage:**
    *   `add_possessing_object(object_name, obj)`: Stores the `obj` under the given `object_name`. Automatically subscribes to the object's `state_changed` event (if the object has an `id`) and forwards these changes as agent `state_changed` events with composite keys (e.g., `state_changed` with key `'my_station.status'`). Triggers `possessing_object_added`.
    *   `remove_possessing_object(object_name)`: Removes the object and unsubscribes from its events. Triggers `possessing_object_removed`.
    *   `get_possessing_object(object_name)`: Retrieves the possessed object instance.
    *   `get_possessing_object_names()`: Lists the names of all possessed objects.
    *   `get_state(f'{object_name}.attribute_name')`: Access the state of a possessed object using dot notation.

#### 2.6 Contract Management (Optional)

*   **Purpose:** Provides an interface for agents to participate in task offloading using a central `ContractManager` (if available in the environment).
*   **Usage (requires `env.contract_manager`):**
    *   `create_contract(task_info, target_agent_ids, reward, ...)`: Creates a new task offloading contract.
    *   `accept_contract(contract_id)`: Accepts a contract offered to this agent.
    *   `get_available_contracts()`: Finds contracts pending acceptance that are assigned to this agent.
    *   `get_agent_contracts(role=None, status=None)`: Retrieves contracts where this agent is the issuer or executor, optionally filtered by status.

### 3. Implementing a Custom Agent Subclass

Follow these steps to create your own agent type:

1.  **Define the Class:**
    *   Inherit from `Agent`.
    *   (Recommended) Create a companion metaclass inheriting from `AgentMeta` OR use the `@Agent.register_state_template` decorator to register type-specific states.
    ```python
    from airfogsim.core.agent import Agent, AgentMeta, StateTemplate
    from typing import Tuple, List # etc.

    # Define the metaclass (optional but good practice for states)
    class MyAgentMeta(AgentMeta):
        def __new__(mcs, name, bases, attrs):
            cls = super().__new__(mcs, name, bases, attrs)
            # Register states specific to MyAgent
            mcs.register_template(cls, 'my_custom_state', str, True, description="A required custom state")
            mcs.register_template(cls, 'optional_counter', int, False, validator=lambda x: x >= 0)
            return cls

    # Define the Agent subclass
    class MyAgent(Agent, metaclass=MyAgentMeta):
        # Register base states common to all agents if not done in base AgentMeta
        # AgentMeta.register_template(cls, 'status', str, True, default='idle') # Example

        # ... implementation ...
    ```

2.  **Implement `__init__`:**
    *   Call `super().__init__(env, agent_name, properties)`.
    *   Add any custom instance variables.
    *   **Crucially:** Initialize the agent's state using `self.initialize_states(...)`, providing values for all `required=True` states defined in its class hierarchy.
    *   Add necessary components using `self.add_component(...)`. Components might be passed in or created here.
    *   (Optional) Add possessed objects using `self.add_possessing_object(...)`.
    ```python
    from airfogsim.component.mobility import MobilityComponent # Example component
    from airfogsim.resource import ChargingStation # Example possessed object

    class MyAgent(Agent, metaclass=MyAgentMeta):
        def __init__(self, env, agent_name: str, initial_custom_state: str, properties=None, mobility_params=None, charge_station_obj=None):
            super().__init__(env, agent_name, properties)

            # Add custom attributes
            self.my_internal_tracker = 0

            # Initialize states (must include required ones)
            self.initialize_states(
                my_custom_state=initial_custom_state,
                optional_counter=0,
                status='initializing' # Example base state
            )

            # Add components
            if mobility_params:
                mobility_comp = MobilityComponent(env, self, "MobilitySystem", **mobility_params) # Pass self (agent)
                self.add_component(mobility_comp)
            # Add other components...

            # Add possessed objects
            if charge_station_obj:
                self.add_possessing_object("my_station", charge_station_obj)

            # Final state after setup
            self.update_state('status', 'idle')
    ```

3.  **Implement `live()`:**
    *   This method **must** be implemented by subclasses.
    *   It defines the agent's core behavior loop as a SimPy process (it must contain at least one `yield`).
    *   Inside `live()`, the agent decides what to do based on its current `self.state`, incoming events, assigned workflows (`self._get_active_workflows()`), possessed object states (`self.get_state('object.state')`), or other internal logic.
    *   Common patterns:
        *   Wait for a duration: `yield self.env.timeout(duration)`
        *   Wait for an event: `yield self.get_event('some_event_name')` or `yield some_simpy_event`
        *   Wait for multiple events: `yield self.env.any_of([event1, event2])`
        *   Execute tasks: Call `self.execute_task(...)`. Remember it returns the `Task` object immediately.
        *   Monitor task completion: Use the `task_completed` event or check `self.managed_tasks`. You can `yield` the monitor process (`self.managed_tasks[task_id]['process']`) if synchronous waiting is needed, but often asynchronous handling via events is preferred.
        *   Update state: Call `self.update_state(...)`.
        *   Interact with possessed objects: `station = self.get_possessing_object('my_station')`, then call its methods or check its state.
        *   Check active workflows: `workflows = self._get_active_workflows()` and adapt behavior based on `workflow.status_machine.current_status` or `workflow.get_details()`.
    ```python
    class MyAgent(Agent, metaclass=MyAgentMeta):
        # ... __init__ ...

        def live(self):
            print(f"Time {self.env.now}: {self.name} starting life.")
            yield self.env.timeout(1) # Initial delay

            while True:
                current_status = self.get_state('status', 'idle')
                active_workflows = self._get_active_workflows() # Check for assigned goals

                if active_workflows:
                    # Example: Prioritize workflow tasks
                    workflow = active_workflows[0] # Simplistic: handle first active workflow
                    print(f"Time {self.env.now}: {self.name} working on workflow {workflow.id} (state: {workflow.status_machine.current_status})")
                    # --- Add workflow-driven logic here ---
                    # e.g., if workflow.status_machine.current_status == 'needs_movement':
                    #    target = workflow.get_details().get('target_location')
                    #    self.execute_task('MobilitySystem', 'Move', 'MoveToTask', properties={'target_position': target})
                    #    self.update_state('status', 'moving_for_workflow')

                    yield self.env.timeout(1) # Check workflow status periodically

                elif current_status == 'needs_action':
                    print(f"Time {self.env.now}: {self.name} deciding to perform an action.")
                    # Example: Execute a task using the 'MobilitySystem' component
                    move_task = self.execute_task(
                        component_name='MobilitySystem',
                        task_name='Move to Target',
                        task_class='MoveToTask', # Use string name
                        properties={'target_position': (10, 20, 5)},
                        target_state={'position': (10, 20, 5)} # Expected final state for task
                    )

                    if move_task:
                        print(f"Time {self.env.now}: {self.name} initiated move task {move_task.id}. Continuing...")
                        self.update_state('status', 'moving')
                        # Wait for task completion via event or polling self.managed_tasks
                        # Example: Wait for the specific task_completed event
                        task_completed_event = self.env.event_registry.get_event(self.id, 'task_completed')
                        # Need a way to filter for the specific task ID in the callback or event data
                        yield task_completed_event # Simplified wait - real implementation needs filtering
                        print(f"Time {self.env.now}: {self.name} detected task finished.")
                        self.update_state('status', 'idle') # Reset status after task
                    else:
                        print(f"Time {self.env.now}: {self.name} failed to initiate move task.")
                        self.update_state('status', 'error')
                        yield self.env.timeout(5)

                elif current_status == 'idle':
                    print(f"Time {self.env.now}: {self.name} is idle. Waiting for something.")
                    # Wait for an external trigger or a timeout
                    event_trigger = self.get_event('start_mission') # Example custom event
                    yield event_trigger | self.env.timeout(10)

                    if event_trigger.triggered:
                        print(f"Time {self.env.now}: {self.name} received start_mission event!")
                        self.update_state('status', 'needs_action')
                    else:
                        print(f"Time {self.env.now}: {self.name} timed out waiting.")
                        # Decide what to do on timeout

                else:
                     # Handle other statuses ('moving', 'error', 'moving_for_workflow', etc.)
                     yield self.env.timeout(1) # Generic wait
    ```

### 4. LLM Integration (Example: `DroneAgent`)

The `Agent` class itself is agnostic to Large Language Models (LLMs), but subclasses can easily integrate them for intelligent decision-making, particularly task planning. The `DroneAgent` demonstrates this:

1.  **LLM Client:** An LLM client (like `openai.OpenAI`) is passed during `__init__` and stored in `self.llm_client`.
2.  **Prompt Engineering:** A method constructs a detailed prompt for the LLM. This prompt includes:
    *   The agent's current state (`self.get_current_states()`).
    *   Information about the current goal (e.g., `workflow.get_details()`, `workflow.status_machine.current_status`).
    *   Available actions (components: `self.get_component_names()`, task classes known to the agent).
    *   The desired output format (e.g., JSON array of task specifications).
3.  **LLM Call:** The method calls the LLM API (e.g., `self.llm_client.chat.completions.create(...)`).
4.  **Response Parsing:** A method extracts and validates the structured task information (e.g., JSON) from the LLM's response. It ensures the tasks have the required fields (`component_name`, `task_name`, `task_class`, `properties`, etc.).
5.  **Integration into `live()`:** The `live()` method calls the LLM analysis function. If the LLM provides valid tasks, the agent proceeds to execute them using `self.execute_task`. If the LLM is unavailable or fails, the agent might fall back to simpler, pre-programmed logic.

This pattern allows agents to leverage sophisticated planning capabilities while maintaining a core simulation structure.

### 5. Key Takeaways & Best Practices

*   **Inherit from `Agent`:** It provides the core structure.
*   **Define States:** Use `StateTemplate` via a metaclass or decorators for clarity and validation. Initialize all required states in `__init__`.
*   **Implement `__init__`:** Call `super().__init__`, initialize states (`initialize_states`), add components (`add_component`), and potentially possessed objects (`add_possessing_object`).
*   **Implement `live()`:** This is the heart of your agent's behavior. Use `yield` for time passage or event waiting. Structure logic based on state, events, and workflows.
*   **Use `execute_task`:** Initiate actions via components using the string name of the `task_class`. Remember it's non-blocking. Handle the returned `Task` object or `None`.
*   **Leverage Events:** Use `trigger_event` and `subscribe` for communication and reaction. The `task_completed` event is crucial for reacting to completed actions initiated by the agent. Subscribe to component or possessed object events as needed.
*   **Components do the Work:** Agents decide *what* and *when*, Components define *how*.
*   **Possessed Objects:** Use `add_possessing_object` to link agents to resources/locations and access their state via composite keys (`get_state`).
*   **Keep `live()` Manageable:** Break down complex logic into helper methods. Handle different agent statuses and workflow states clearly.
*   **Error Handling:** Check return values of `execute_task`. Handle potential exceptions in `live()` and callbacks.

By following these guidelines, you can effectively create diverse and capable agents within the AirFogSim framework. Refer to the `Agent` source code and specific agent examples (like `DroneAgent`) for concrete implementation details.
