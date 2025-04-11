# AirFogSim Resource Management Guide

This document explains the resource management system in AirFogSim, focusing on the `Resource` base class, the generic `ResourceManager` base class, and specific implementations like `LandingResource` and `LandingManager`.

## 1. Core Concepts

*   **Resource:** Represents any entity within the simulation that can be utilized or consumed by agents or components. Examples include physical locations (landing spots), communication channels (frequency bands), computational power, etc. Each resource typically has an ID, attributes, and a status (e.g., available, allocated).
*   **ResourceManager:** A dedicated manager responsible for a specific *type* of resource. Its duties include:
    *   Registering and unregistering individual resource instances.
    *   Finding resources based on specific requirements.
    *   Allocating available resources to requesting agents/components.
    *   Releasing resources when they are no longer needed.
    *   Tracking the allocation status of each resource.

## 2. Base Classes

### 2.1 `Resource` (`airfogsim.core.resource.Resource`)

*   **Purpose:** The fundamental base class for all resource types.
*   **Key Attributes:**
    *   `id` (str): A unique identifier for the resource instance.
    *   `attributes` (Dict): A dictionary holding various properties of the resource (e.g., capacity, speed, power). Accessed via `get_attribute(key, default)`.
    *   `status` (str): The current operational status (e.g., "available", "allocated", "maintenance").
    *   `current_allocations` (Set[str]): A set containing the IDs of the current active allocations using this resource. (Note: Specific resource implementations might track allocations differently, e.g., `LandingResource` uses `occupied_slots` and `current_allocations` stores agent IDs).

### 2.2 `ResourceManager<R>` (`airfogsim.core.resource.ResourceManager`)

*   **Purpose:** A generic base class for managing resources of a specific type `R` (where `R` inherits from `Resource`).
*   **Key Features:**
    *   **Resource Storage (`resources`):** A dictionary mapping resource IDs to resource objects (`Dict[str, R]`).
    *   **Allocation Tracking:** Maintains dictionaries to track allocations by allocation ID (`allocations`), by resource ID (`resource_allocations`), and by user ID (`user_allocations`). *Note: The base implementation's allocation tracking might be superseded or adapted by specific manager subclasses.*
    *   **Core Methods:**
        *   `register_resource(resource: R)`: Adds a resource instance to the manager.
        *   `unregister_resource(resource_id: str)`: Removes a resource instance.
        *   `update_resource(resource_id: str, attributes: Dict)`: Updates the attributes of a registered resource.
        *   `find_resources(requirements: Dict) -> List[R]`: **Abstract method (must be implemented by subclasses)** to find resources matching given criteria.
        *   `allocate_resource(user_id: str, requirements: Dict) -> Tuple[Optional[str], Optional[str]]`: Attempts to find and allocate a suitable resource based on requirements. *Note: Subclasses often provide more specific allocation methods.*
        *   `release_allocation(allocation_id: str)`: Releases a specific allocation. *Note: Subclasses often provide more specific release methods based on resource and user IDs.*
        *   `get_user_allocations(user_id: str)` / `get_resource_allocations(resource_id: str)` / `get_allocation(allocation_id: str)`: Methods to query allocation information.

## 3. Example: Landing Resources

### 3.1 `LandingResource` (`airfogsim.resource.landing.LandingResource`)

*   **Inherits from:** `Resource`.
*   **Represents:** A physical landing spot for drones.
*   **Specific Attributes:**
    *   `location` (tuple): (x, y, z) coordinates.
    *   `radius` (float): The physical radius of the landing area.
    *   `max_capacity` (int): How many drones can occupy the spot simultaneously.
    *   `has_charging` (bool): Whether charging is available.
    *   `has_data_transfer` (bool): Whether data transfer facilities are available.
    *   `occupied_slots` (int): Current number of drones allocated.
    *   `condition` (str): Operational condition ("normal", "damaged", "maintenance").
*   **Specific Methods:**
    *   `allocate(agent_id: str)`: Increments `occupied_slots` and adds `agent_id` to `current_allocations`.
    *   `release(agent_id: str)`: Decrements `occupied_slots` and removes `agent_id` from `current_allocations`.
    *   `is_allocated(agent_id: str)`: Checks if a specific agent is allocated.
    *   `has_capacity()`: Checks if `occupied_slots < max_capacity`.
    *   `is_within_range(x, y, altitude)`: Checks if coordinates are within the landing radius.
    *   `update_condition(condition)`: Updates the operational condition.
    *   `get_charging_power()` / `get_data_transfer_rate()`: Get capability values (if available).

### 3.2 `LandingManager` (`airfogsim.manager.landing.LandingManager`)

*   **Inherits from:** `ResourceManager[LandingResource]`.
*   **Manages:** Instances of `LandingResource`.
*   **Key Features & Overrides:**
    *   **Integration with `AirspaceManager`:** Uses the environment's `airspace_manager` (if available) for efficient spatial queries (`find_resources`, `find_nearest_landing_spot`). When registering/unregistering a `LandingResource`, it also updates the `AirspaceManager`.
    *   **`find_resources(requirements: Dict)`:** Implements the abstract method. Uses spatial queries (if possible) and filters based on capacity, charging/data transfer needs, radius, and condition.
    *   **`find_nearest_landing_spot(...)`:** A specific method to find the closest available landing spot based on coordinates and optional requirements (like charging). Uses spatial queries via `AirspaceManager`.
    *   **`request_resource(resource_id: str, agent, priority: int)`:** Implements a request mechanism. If the specific `resource_id` is available and has capacity, it allocates immediately using `allocate_resource`. Otherwise, the request is added to a priority queue (`request_queues`).
    *   **`allocate_resource(resource_id: str, agent)`:** Allocates the specific `resource_id` to the `agent`. It updates the `LandingResource` state (`allocate` method) and sets up a listener using the `EventRegistry` to automatically call `release_resource` if the agent triggers the `possessing_object_removed` event for this resource.
    *   **`release_resource(resource_id: str, agent_id: str)`:** Releases the resource allocated to the `agent_id`. It updates the `LandingResource` state (`release` method), unsubscribes the event listener, and then processes the `request_queues` (`_process_request_queue`) to potentially allocate the now-freed resource to a waiting agent.
    *   **`_process_request_queue()`:** Iterates through waiting requests and allocates resources if they become available.
    *   **`create_landing_spot(...)`:** Helper method to easily create and register a new `LandingResource`.

## 4. Usage Pattern

1.  **Initialization:** During environment setup, create specific `ResourceManager` instances (e.g., `LandingManager`) and register them with the environment (e.g., `env.landing_manager = LandingManager(env)`).
2.  **Resource Creation:** Create instances of specific `Resource` types (e.g., `LandingResource`) and register them with their corresponding manager (`landing_manager.register_resource(spot1)`).
3.  **Agent/Component Interaction:**
    *   Agents or Components determine their resource needs based on their current task or state.
    *   They interact with the appropriate `ResourceManager` to find suitable resources (`find_resources`, `find_nearest_landing_spot`).
    *   They request a specific resource using methods like `request_resource`.
    *   The manager handles allocation logic, potentially involving queues if the resource is busy.
    *   Once allocated, the agent/component uses the resource. The allocation might be tracked implicitly (e.g., agent moves to the landing spot) or explicitly (e.g., agent adds the resource to its `possessing_objects`).
    *   When finished, the agent/component signals the release (e.g., by removing the object from `possessing_objects`, which triggers the listener set up by `allocate_resource` in `LandingManager`, leading to `release_resource`).
    *   The manager updates the resource state and processes any waiting requests.

## 5. Extensibility

*   Create new resource types by inheriting from `airfogsim.core.resource.Resource`.
*   Create corresponding managers by inheriting from `airfogsim.core.resource.ResourceManager[YourResourceType]`.
*   Implement the `find_resources` method in your custom manager.
*   Implement specific allocation/release logic as needed, potentially overriding or supplementing the base `ResourceManager` methods.
*   Register your custom manager instance with the simulation environment.