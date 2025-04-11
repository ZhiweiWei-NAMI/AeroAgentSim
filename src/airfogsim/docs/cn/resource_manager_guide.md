# AirFogSim 资源管理指南

本文档解释了 AirFogSim 中的资源管理系统，重点关注 `Resource` 基类、通用的 `ResourceManager` 基类以及像 `LandingResource` 和 `LandingManager` 这样的具体实现。

## 1. 核心概念

*   **资源 (Resource):** 代表模拟中任何可以被 Agent 或组件利用或消耗的实体。示例包括物理位置（着陆点）、通信信道（频段）、计算能力等。每个资源通常具有 ID、属性和状态（例如，可用、已分配）。
*   **资源管理器 (ResourceManager):** 负责特定*类型*资源的专用管理器。其职责包括：
    *   注册和注销单个资源实例。
    *   根据特定需求查找资源。
    *   将可用资源分配给请求的 Agent/组件。
    *   在不再需要时释放资源。
    *   跟踪每个资源的分配状态。

## 2. 基类

### 2.1 `Resource` (`airfogsim.core.resource.Resource`)

*   **目的:** 所有资源类型的基本基类。
*   **关键属性:**
    *   `id` (str): 资源实例的唯一标识符。
    *   `attributes` (Dict): 包含资源各种属性的字典（例如，容量、速度、功率）。通过 `get_attribute(key, default)` 访问。
    *   `status` (str): 当前操作状态（例如，"available", "allocated", "maintenance"）。
    *   `current_allocations` (Set[str]): 包含当前使用此资源的活动分配 ID 的集合。（注意：特定的资源实现可能会以不同方式跟踪分配，例如 `LandingResource` 使用 `occupied_slots` 并且 `current_allocations` 存储 Agent ID）。

### 2.2 `ResourceManager<R>` (`airfogsim.core.resource.ResourceManager`)

*   **目的:** 用于管理特定类型 `R`（其中 `R` 继承自 `Resource`）资源的通用基类。
*   **关键特性:**
    *   **资源存储 (`resources`):** 将资源 ID 映射到资源对象的字典 (`Dict[str, R]`)。
    *   **分配跟踪:** 维护字典以按分配 ID (`allocations`)、资源 ID (`resource_allocations`) 和用户 ID (`user_allocations`) 跟踪分配。*注意：基础实现的分配跟踪可能被特定的管理器子类取代或调整。*
    *   **核心方法:**
        *   `register_resource(resource: R)`: 向管理器添加资源实例。
        *   `unregister_resource(resource_id: str)`: 移除资源实例。
        *   `update_resource(resource_id: str, attributes: Dict)`: 更新已注册资源的属性。
        *   `find_resources(requirements: Dict) -> List[R]`: **抽象方法（必须由子类实现）**，用于查找符合给定标准的资源。
        *   `allocate_resource(user_id: str, requirements: Dict) -> Tuple[Optional[str], Optional[str]]`: 尝试根据需求查找并分配合适的资源。*注意：子类通常提供更具体的分配方法。*
        *   `release_allocation(allocation_id: str)`: 释放特定的分配。*注意：子类通常根据资源和用户 ID 提供更具体的释放方法。*
        *   `get_user_allocations(user_id: str)` / `get_resource_allocations(resource_id: str)` / `get_allocation(allocation_id: str)`: 查询分配信息的方法。

## 3. 示例：着陆资源

### 3.1 `LandingResource` (`airfogsim.resource.landing.LandingResource`)

*   **继承自:** `Resource`。
*   **代表:** 无人机的物理着陆点。
*   **特定属性:**
    *   `location` (tuple): (x, y, z) 坐标。
    *   `radius` (float): 着陆区域的物理半径。
    *   `max_capacity` (int): 可以同时容纳多少架无人机。
    *   `has_charging` (bool): 是否提供充电。
    *   `has_data_transfer` (bool): 是否提供数据传输设施。
    *   `occupied_slots` (int): 当前分配的无人机数量。
    *   `condition` (str): 操作条件 ("normal", "damaged", "maintenance")。
*   **特定方法:**
    *   `allocate(agent_id: str)`: 增加 `occupied_slots` 并将 `agent_id` 添加到 `current_allocations`。
    *   `release(agent_id: str)`: 减少 `occupied_slots` 并从 `current_allocations` 中移除 `agent_id`。
    *   `is_allocated(agent_id: str)`: 检查特定 Agent 是否已分配。
    *   `has_capacity()`: 检查 `occupied_slots < max_capacity`。
    *   `is_within_range(x, y, altitude)`: 检查坐标是否在着陆半径内。
    *   `update_condition(condition)`: 更新操作条件。
    *   `get_charging_power()` / `get_data_transfer_rate()`: 获取能力值（如果可用）。

### 3.2 `LandingManager` (`airfogsim.manager.landing.LandingManager`)

*   **继承自:** `ResourceManager[LandingResource]`。
*   **管理:** `LandingResource` 的实例。
*   **关键特性和覆盖:**
    *   **与 `AirspaceManager` 集成:** 使用环境的 `airspace_manager`（如果可用）进行高效的空间查询（`find_resources`, `find_nearest_landing_spot`）。注册/注销 `LandingResource` 时，它也会更新 `AirspaceManager`。
    *   **`find_resources(requirements: Dict)`:** 实现抽象方法。如果可能，使用空间查询，并根据容量、充电/数据传输需求、半径和条件进行过滤。
    *   **`find_nearest_landing_spot(...)`:** 一个特定的方法，用于根据坐标和可选需求（如充电）查找最近的可用着陆点。通过 `AirspaceManager` 使用空间查询。
    *   **`request_resource(resource_id: str, agent, priority: int)`:** 实现请求机制。如果特定的 `resource_id` 可用且有容量，则立即使用 `allocate_resource` 进行分配。否则，请求将添加到优先级队列 (`request_queues`)。
    *   **`allocate_resource(resource_id: str, agent)`:** 将特定的 `resource_id` 分配给 `agent`。它更新 `LandingResource` 状态（`allocate` 方法），并使用 `EventRegistry` 设置监听器，以便在 Agent 触发此资源的 `possessing_object_removed` 事件时自动调用 `release_resource`。
    *   **`release_resource(resource_id: str, agent_id: str)`:** 释放分配给 `agent_id` 的资源。它更新 `LandingResource` 状态（`release` 方法），取消订阅事件监听器，然后处理 `request_queues` (`_process_request_queue`)，以可能将现在释放的资源分配给等待的 Agent。
    *   **`_process_request_queue()`:** 迭代等待的请求，并在资源可用时分配资源。
    *   **`create_landing_spot(...)`:** 轻松创建和注册新 `LandingResource` 的辅助方法。

## 4. 使用模式

1.  **初始化:** 在环境设置期间，创建特定的 `ResourceManager` 实例（例如 `LandingManager`）并将其注册到环境中（例如 `env.landing_manager = LandingManager(env)`）。
2.  **资源创建:** 创建特定 `Resource` 类型的实例（例如 `LandingResource`）并将其注册到相应的管理器（`landing_manager.register_resource(spot1)`）。
3.  **Agent/组件交互:**
    *   Agent 或组件根据其当前任务或状态确定其资源需求。
    *   它们与适当的 `ResourceManager` 交互以查找合适的资源（`find_resources`, `find_nearest_landing_spot`）。
    *   它们使用诸如 `request_resource` 之类的方法请求特定资源。
    *   管理器处理分配逻辑，如果资源繁忙，可能涉及队列。
    *   一旦分配，Agent/组件就使用该资源。分配可以隐式跟踪（例如，Agent 移动到着陆点）或显式跟踪（例如，Agent 将资源添加到其 `possessing_objects`）。
    *   完成后，Agent/组件发出释放信号（例如，通过从 `possessing_objects` 中移除对象，这会触发 `LandingManager` 中 `allocate_resource` 设置的监听器，从而导致 `release_resource`）。
    *   管理器更新资源状态并处理任何等待的请求。

## 5. 可扩展性

*   通过继承 `airfogsim.core.resource.Resource` 创建新的资源类型。
*   通过继承 `airfogsim.core.resource.ResourceManager[YourResourceType]` 创建相应的管理器。
*   在您的自定义管理器中实现 `find_resources` 方法。
*   根据需要实现特定的分配/释放逻辑，可能覆盖或补充基础 `ResourceManager` 方法。
*   将您的自定义管理器实例注册到仿真环境中。