
## AirFogSim Agent Developer Documentation

This document provides guidance on understanding and extending the `airfogsim.core.agent.Agent` class within the AirFogSim framework. It explains the core concepts, how to define custom agent types, and how to implement their behavior. The `DroneAgent` example provided in the prompt will be used as a reference throughout.

### 1. Overview of the `Agent` Class

The `Agent` class represents an active, decision-making entity within the simulation environment (`simpy.Environment`). Agents are the primary actors that interact with the simulated world by:

1.  **Maintaining State:** Tracking internal properties like position, battery level, status, etc.
2.  **Owning Components:** Utilizing functional units (`Component`) like sensors, actuators (e.g., movement systems), or compute resources to perform actions.
3.  **Executing Tasks:** Defining and initiating actions (`Task`) to be performed by their components.
4.  **Reacting to Events:** Responding to changes in the environment or their own internal state.
5.  **Managing Workflows:** (Optionally) Participating in or managing larger sequences of tasks (`Workflow`).

The base `Agent` class provides the foundational structure and mechanisms for these capabilities. Subclasses define specific agent types (like `DroneAgent`, `GroundStationAgent`, etc.) by adding specialized states, components, and behavior logic.

### 2. Core Concepts

#### 2.1 State Management (`StateTemplate`, `AgentMeta`)

*   **Purpose:** Agents track their properties using a `self.state` dictionary. To ensure consistency, type safety, and define requirements, the framework uses `StateTemplate`.
*   **`StateTemplate`:** Defines the expected properties of a state variable:
    *   `key` (str): The name of the state variable.
    *   `value_type` (type, Optional): The expected Python type (e.g., `int`, `float`, `str`, `tuple`). `None` allows any type.
    *   `required` (bool): Whether this state *must* be initialized. A warning is issued if missing.
    *   `validator` (callable, Optional): A function that takes the value and returns `True` if valid, `False` otherwise.
    *   `description` (str, Optional): A human-readable description.
*   **`AgentMeta` (Metaclass):** This metaclass automatically handles the inheritance of `StateTemplate` definitions. When you define templates in a base class or a subclass's metaclass (like `DroneAgentMeta`), they are aggregated. Each agent instance validates its state updates against the *combined* templates of its entire class hierarchy.
*   **Registering Templates:**
    *   **Recommended:** Use the `@classmethod` decorator `register_state_template` directly on your agent subclass (less common but possible).
    *   **Standard (via Metaclass):** Define a custom metaclass inheriting from `AgentMeta` (like `DroneAgentMeta`) and call `mcs.register_template(cls, ...)` within its `__new__` method. This is cleaner for organizing type-specific states.
        ```python
        # Example from DroneAgentMeta
        class DroneAgentMeta(AgentMeta):
            def __new__(mcs, name, bases, attrs):
                cls = super().__new__(mcs, name, bases, attrs)
                mcs.register_template(cls, 'position', tuple, True, ...)
                mcs.register_template(cls, 'battery_level', float, True, ...)
                # ... other drone-specific states
                return cls

        class DroneAgent(Agent, metaclass=DroneAgentMeta):
            # ... Agent implementation ...
        ```
*   **Using State:**
    *   `initialize_states(**states)`: Set initial state values during `__init__`. Checks required states.
    *   `update_state(key, value)`: Update a single state value. Triggers validation and the `state_changed` event if the value changes.
    *   `update_states(state_dict)`: Update multiple states.
    *   `get_state(key, default=None)`: Retrieve a state value.
    *   `get_current_states()`: Get a copy of the entire state dictionary.
    *   `get_state_templates()`: (Class method) Get all applicable templates for the class.

#### 2.2 Component Management

*   **Purpose:** Components (`airfogsim.core.Component`) represent the functional parts of an agent (e.g., 'MobilitySystem', 'CPUEngine', 'CameraSensor'). Agents delegate task execution to their components.
*   **Usage:**
    *   `add_component(component)`: Attach a `Component` instance to the agent. The component gets a reference back to the agent (`component.agent`).
    *   `get_component(component_name)`: Retrieve a component by its name.
    *   `get_component_names()`: List the names of all attached components.
*   **Example:** An agent needs a `MobilityComponent` to move and a `ComputeComponent` to process data. These would be added during initialization or configuration.

#### 2.3 Event Handling

*   **Purpose:** Agents can react to and announce significant occurrences using a publish-subscribe system managed by `env.event_registry`.
*   **Standard Agent Events:** The base `Agent` automatically registers:
    *   `state_changed`: Fired when `update_state` changes a value.
    *   `task_started`: Fired when the agent initiates a task via `execute_task`.
    *   `task_finished`: Fired when the agent observes that a task it initiated has completed (successfully, failed, or canceled).
    *   `proof_created`, `proof_updated`, `proof_transferred`: Related to task proof management.
*   **Usage:**
    *   `register_event(event_name)`: Declare an event this agent might trigger.
    *   `trigger_event(event_name, value=None)`: Fire an event, notifying all subscribers.
    *   `subscribe(source_id, event_name, callback, listener_id=None)`: Register a `callback` function to be called when the `source_id` triggers `event_name`.
    *   `unsubscribe(source_id, event_name, listener_id)`: Stop listening to an event.
    *   `unsubscribe_all()`: Remove all subscriptions *made by* this agent.
    *   `get_event(event_name)`: Get the underlying `simpy.Event` object for this agent's event (useful for `yield`ing).

#### 2.4 Task Execution

*   **Purpose:** Agents decide *what* needs to be done and *which component* should do it. They create `Task` objects and ask components to execute them.
*   **`Task` Object:** Represents a unit of work with properties like name, status, start/end times, associated workflow/proof, etc. Specific task types (e.g., `MoveToTask`, `ComputeTask`) inherit from `airfogsim.core.Task`.
*   **`execute_task(...)` Method:**
    *   This is the agent's primary way to initiate work.
    *   It takes the `component_name`, `task_name`, optional `task_class` (defaults to `Task`), `target_state`, `properties` for the task, and optional `workflow_id`/`proof_id`.
    *   **Crucially, it's non-blocking.** It creates the `Task`, asks the component to start execution, triggers the `task_started` event, and returns the `Task` object *immediately*.
    *   It starts an internal monitoring process (`_monitor_task_execution`) to wait for the component's execution to finish.
*   **`_monitor_task_execution(...)` (Internal):**
    *   A SimPy process that `yield`s the component's execution process.
    *   Handles completion, failure, or interruption of the task execution by the component.
    *   Updates the `Task` status/result based on the outcome.
    *   Triggers the agent's `task_finished` event.
    *   Manages the `self.managed_tasks` dictionary (tracking active tasks initiated by the agent).
*   **Workflow:** An agent's `live()` method typically decides *when* and *which* tasks to execute based on its state, goals, or assigned workflows.

### 3. Implementing a Custom Agent Subclass

Follow these steps to create your own agent type (using `DroneAgent` as a guide):

1.  **Define the Class:**
    *   Inherit from `Agent`.
    *   (Recommended) Create a companion metaclass inheriting from `AgentMeta` to register type-specific states.
    ```python
    from airfogsim.core.agent import Agent, AgentMeta

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
        # ... implementation ...
    ```

2.  **Implement `__init__`:**
    *   Call `super().__init__(env, agent_name, properties)`.
    *   Add any custom instance variables (like the `llm_client` or `task_class_map` in `DroneAgent`).
    *   **Crucially:** Initialize the agent's state using `self.initialize_states(...)`, providing values for all `required=True` states defined in its class hierarchy.
    *   (Optional) Add necessary components using `self.add_component(...)`. Components might be passed in or created here.
    ```python
    from airfogsim.components.mobility import MobilityComponent # Example component

    class MyAgent(Agent, metaclass=MyAgentMeta):
        def __init__(self, env, agent_name: str, initial_custom_state: str, properties=None, mobility_params=None):
            super().__init__(env, agent_name, properties)

            # Add custom attributes
            self.my_internal_tracker = 0

            # Initialize states (must include required ones)
            self.initialize_states(
                my_custom_state=initial_custom_state,
                optional_counter=0
                # Other inherited states like 'status' might be set here too
            )

            # Add components
            if mobility_params:
                mobility_comp = MobilityComponent(env, "MobilitySystem", **mobility_params)
                self.add_component(mobility_comp)
            # Add other components...
    ```

3.  **Implement `live()`:**
    *   This method **must** be implemented.
    *   It defines the agent's core behavior loop as a SimPy process (it must contain at least one `yield`).
    *   Inside `live()`, the agent decides what to do based on its current `self.state`, incoming events, assigned workflows, or other logic.
    *   Common patterns:
        *   Wait for a duration: `yield self.env.timeout(duration)`
        *   Wait for an event: `yield self.get_event('some_event_name')` or `yield some_simpy_event`
        *   Wait for a task to complete: The monitoring is handled internally, but you might `yield` the process returned by `execute_task` *if* you need to wait synchronously (less common for complex agents). Often, you'll execute tasks and let the `task_finished` event or other triggers drive the next decision.
        *   Execute tasks: Call `self.execute_task(...)`.
        *   Update state: Call `self.update_state(...)`.
    ```python
    class MyAgent(Agent, metaclass=MyAgentMeta):
        # ... __init__ ...

        def live(self):
            print(f"时间 {self.env.now}: {self.name} starting life.")
            yield self.env.timeout(1) # Initial delay

            while True:
                current_status = self.get_state('status', 'idle') # Example state check

                if current_status == 'needs_action':
                    print(f"时间 {self.env.now}: {self.name} deciding to perform an action.")
                    # Example: Execute a task using the 'MobilitySystem' component
                    move_task = self.execute_task(
                        component_name='MobilitySystem',
                        task_name='Move to Target',
                        task_class=MoveToTask, # Assuming MoveToTask is imported
                        properties={'target_position': (10, 20, 5)},
                        target_state={'position': (10, 20, 5)} # Expected final state
                    )

                    if move_task and move_task.id in self.managed_tasks:
                        # Option 1: Wait for this specific task (synchronous step)
                        # print(f"时间 {self.env.now}: {self.name} waiting for move task {move_task.id}...")
                        # result = yield self.managed_tasks[move_task.id]['process']
                        # print(f"时间 {self.env.now}: {self.name} move task finished with result: {result}")

                        # Option 2: Continue operation, react later via 'task_finished' event (asynchronous)
                        print(f"时间 {self.env.now}: {self.name} initiated move task {move_task.id}. Continuing...")
                        self.update_state('status', 'moving')
                        # The loop will continue, or it might wait for another event
                        yield self.env.timeout(0.1) # Small delay before next check

                    else:
                        print(f"时间 {self.env.now}: {self.name} failed to initiate move task.")
                        self.update_state('status', 'error')
                        yield self.env.timeout(5) # Wait before retrying or stopping

                elif current_status == 'idle':
                    print(f"时间 {self.env.now}: {self.name} is idle. Waiting for something to happen.")
                    # Wait for an external trigger or a timeout
                    # Example: Wait for a specific event OR a timeout
                    event_trigger = self.get_event('start_mission') # Example event
                    yield event_trigger | self.env.timeout(10)

                    if event_trigger.triggered:
                        print(f"时间 {self.env.now}: {self.name} received start_mission event!")
                        self.update_state('status', 'needs_action')
                    else:
                        print(f"时间 {self.env.now}: {self.name} timed out waiting for mission.")
                        # Decide what to do on timeout

                else:
                     # Handle other statuses ('moving', 'error', etc.)
                     yield self.env.timeout(1) # Generic wait
    ```

### 4. LLM Integration (Example: `DroneAgent`)

The `Agent` class itself is agnostic to Large Language Models (LLMs), but subclasses can easily integrate them for intelligent decision-making, particularly task planning. The `DroneAgent` demonstrates this:

1.  **LLM Client:** An LLM client (like `openai.OpenAI`) is passed during `__init__` and stored in `self.llm`.
2.  **Prompt Engineering:** A method like `_analyze_workflow_with_llm` constructs a detailed prompt for the LLM. This prompt includes:
    *   The agent's current state (`self.get_current_states()`).
    *   Information about the current goal (e.g., `workflow.get_details()`, `workflow.status_machine.state`).
    *   Available actions (components: `self.get_component_names()`, task classes: `self.task_class_map`).
    *   The desired output format (e.g., JSON array of task specifications).
3.  **LLM Call:** The method calls the LLM API (e.g., `self.llm.chat.completions.create(...)`).
4.  **Response Parsing:** A method like `_parse_llm_response` extracts and validates the structured task information (e.g., JSON) from the LLM's potentially verbose response. It ensures the tasks have the required fields (`component`, `task_name`, `properties`, etc.).
5.  **Integration into `live()`:** The `live()` method calls the LLM analysis function (`_analyze_workflow_with_llm`). If the LLM provides valid tasks, the agent proceeds to execute them using `self.execute_task`. If the LLM is unavailable or fails, the agent might fall back to simpler, pre-programmed logic (like `_plan_inspection_tasks` in `DroneAgent`).

This pattern allows agents to leverage sophisticated planning capabilities while maintaining a core simulation structure.

### 5. Key Takeaways & Best Practices

*   **Inherit from `Agent`:** It provides the core structure.
*   **Define States:** Use `StateTemplate` via a metaclass (`XxxAgentMeta`) for clarity and validation.
*   **Implement `__init__`:** Call `super().__init__`, initialize states (`initialize_states`), and add components (`add_component`).
*   **Implement `live()`:** This is the heart of your agent's behavior. Use `yield` for time passage or event waiting.
*   **Use `execute_task`:** Initiate actions via components. Remember it's non-blocking.
*   **Leverage Events:** Use `trigger_event` and `subscribe` for communication and reaction. The `task_finished` event is particularly useful for reacting to completed actions.
*   **Components do the Work:** Agents decide *what* and *when*, Components define *how*.
*   **Keep `live()` Manageable:** Break down complex logic into helper methods (like `DroneAgent`'s `_analyze_workflow_with_llm`, `_plan_inspection_tasks`).

By following these guidelines, you can effectively create diverse and capable agents within the AirFogSim framework. Refer to the `Agent` source code and the `DroneAgent` example for concrete implementation details.

---