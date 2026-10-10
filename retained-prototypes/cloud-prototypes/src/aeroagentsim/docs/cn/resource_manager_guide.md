# AeroAgentSim 资源管理指南

本文档解释了 AeroAgentSim 中的资源管理系统，重点关注 `Resource` 基类、通用 `ResourceManager<R>` 基类以及特定实现，如 `LandingResource` 和 `LandingManager`。

## 1. 核心概念

*   **资源：** 代表仿真中可以被代理或组件利用或消耗的任何有限共享资产。例如包括物理资源，如空域、着陆点和频谱。每个资源维护一个唯一 ID、一个特定属性的字典、一个操作状态，并跟踪其当前分配。

*   **资源管理器：** 负责特定*类型*资源的专用管理器。框架的核心是通用 `ResourceManager<R>` 基类，设计用于管理特定类型 R 的资源集合。其主要职责包括：
    *   资源生命周期管理（注册、注销、更新）
    *   分配管理（查找、分配、释放资源）
    *   状态跟踪（监控资源状态和分配）
    *   争用处理（排队、基于优先级的分配、抢占）

## 2. 基类

### 2.1 `Resource` (`aeroagentsim.core.resource.Resource`)

*   **目的：** 所有资源类型的基本基类，作为任何单个资源实例的基本表示。
*   **关键属性：**
    *   `id` (str)：资源实例的唯一标识符。
    *   `attributes` (Dict)：保存资源各种属性的字典（例如，容量、速度、功率）。通过 `get_attribute(key, default)` 访问。
    *   `status` (ResourceStatus)：当前操作状态（例如，`ResourceStatus.AVAILABLE`、`ResourceStatus.FULLY_ALLOCATED`、`ResourceStatus.MAINTENANCE`）。
    *   `current_allocations` (Set[str])：包含使用此资源的当前活动分配 ID 的集合。（注意：特定资源实现可能以不同方式跟踪分配，例如，`LandingResource` 使用 `occupied_slots`，而 `current_allocations` 存储代理 ID）。

### 2.2 `ResourceManager<R>` (`aeroagentsim.core.resource.ResourceManager`)

*   **目的：** 用于管理特定类型 `R`（其中 `R` 继承自 `Resource`）资源的通用基类。
*   **关键特性：**
    *   **资源存储 (`resources`)：** 将资源 ID 映射到资源对象的字典 (`Dict[str, R]`)。
    *   **分配跟踪：** 维护字典以按分配 ID (`allocations`)、按资源 ID (`resource_allocations`) 和按用户 ID (`user_allocations`) 跟踪分配。*注意：基础实现的分配跟踪可能被特定管理器子类取代或调整。*
    *   **核心方法：**
        *   `register_resource(resource: R)`：向管理器添加资源实例。
        *   `unregister_resource(resource_id: str)`：移除资源实例。
        *   `update_resource(resource_id: str, attributes: Dict)`：更新已注册资源的属性。
        *   `find_resources(requirements: Dict) -> List[R]`：**抽象方法（必须由子类实现）**，用于查找匹配给定条件的资源。
        *   `allocate_resource(user_id: str, requirements: Dict) -> Tuple[Optional[str], Optional[str]]`：尝试根据需求查找并分配合适的资源。*注意：子类通常提供更具体的分配方法。*
        *   `release_allocation(allocation_id: str)`：释放特定分配。*注意：子类通常根据资源和用户 ID 提供更具体的释放方法。*
        *   `get_user_allocations(user_id: str)` / `get_resource_allocations(resource_id: str)` / `get_allocation(allocation_id: str)`：查询分配信息的方法。

## 3. 特定资源管理器

AeroAgentSim 为不同类型的资源提供了几个专门的资源管理器：

### 3.1 `LandingManager` (`aeroagentsim.manager.landing.LandingManager`)

管理着陆点和充电站，处理着陆、起飞和充电操作的资源分配。

### 3.2 `FrequencyManager` (`aeroagentsim.manager.frequency.FrequencyManager`)

使用符合 3GPP 的信道模型管理频谱资源，处理频率分配、干扰建模和信号质量计算。

### 3.3 `AirspaceManager` (`aeroagentsim.manager.airspace.AirspaceManager`)

使用基于八叉树的空间索引管理空间查询和空域资源分配/冲突解决，用于高效的碰撞检测和邻近查询。


## 4. 示例：着陆资源

### 4.1 `LandingResource` (`aeroagentsim.resource.landing.LandingResource`)

*   **继承自：** `Resource`。
*   **代表：** 无人机的物理着陆点。
*   **特定属性：**
    *   `location` (tuple)：(x, y, z) 坐标。
    *   `radius` (float)：着陆区域的物理半径。
    *   `max_capacity` (int)：同时可以占用该点的无人机数量。
    *   `has_charging` (bool)：是否提供充电。
    *   `has_data_transfer` (bool)：是否提供数据传输设施。
    *   `occupied_slots` (int)：当前分配的无人机数量。
    *   `condition` (str)：操作条件（"normal"、"damaged"、"maintenance"）。
*   **特定方法：**
    *   `allocate(agent_id: str)`：增加 `occupied_slots` 并将 `agent_id` 添加到 `current_allocations`。
    *   `release(agent_id: str)`：减少 `occupied_slots` 并从 `current_allocations` 中移除 `agent_id`。
    *   `is_allocated(agent_id: str)`：检查特定代理是否已分配。
    *   `has_capacity()`：检查 `occupied_slots < max_capacity`。
    *   `is_within_range(x, y, altitude)`：检查坐标是否在着陆半径内。
    *   `update_condition(condition)`：更新操作条件。
    *   `get_charging_power()` / `get_data_transfer_rate()`：获取能力值（如果可用）。

### 4.2 `LandingManager` (`aeroagentsim.manager.landing.LandingManager`)

*   **继承自：** `ResourceManager[LandingResource]`。
*   **管理：** `LandingResource` 的实例。
*   **关键特性和重写：**
    *   **与 `AirspaceManager` 集成：** 使用环境的 `airspace_manager`（如果可用）进行高效的空间查询（`find_resources`、`find_nearest_landing_spot`）。在注册/注销 `LandingResource` 时，它也会更新 `AirspaceManager`。
    *   **`find_resources(requirements: Dict)`：** 实现抽象方法。使用空间查询（如果可能）并根据容量、充电/数据传输需求、半径和条件进行过滤。
    *   **`find_nearest_landing_spot(...)`：** 一个特定方法，根据坐标和可选需求（如充电）查找最近的可用着陆点。通过 `AirspaceManager` 使用空间查询。
    *   **`request_resource(resource_id: str, agent, priority: int)`：** 实现请求机制。如果特定的 `resource_id` 可用且有容量，它会立即使用 `allocate_resource` 分配。否则，请求会被添加到优先级队列（`request_queues`）。
    *   **`allocate_resource(resource_id: str, agent)`：** 将特定的 `resource_id` 分配给 `agent`。它更新 `LandingResource` 状态（`allocate` 方法）并使用 `EventRegistry` 设置监听器，如果代理为此资源触发 `possessing_object_removed` 事件，则自动调用 `release_resource`。
    *   **`release_resource(resource_id: str, agent_id: str)`：** 释放分配给 `agent_id` 的资源。它更新 `LandingResource` 状态（`release` 方法），取消订阅事件监听器，然后处理 `request_queues`（`_process_request_queue`）以可能将现在释放的资源分配给等待的代理。
    *   **`_process_request_queue()`：** 遍历等待的请求，如果资源变得可用，则分配资源。
    *   **`create_landing_spot(...)`：** 辅助方法，用于轻松创建和注册新的 `LandingResource`。

## 5. 资源分配过程

AeroAgentSim 中的资源分配过程遵循以下步骤：

1. 组件通过 `get_resource_requirements(task)` 识别任务的资源需求
2. 组件从适当的管理器请求资源（例如，`env.landing_manager.request_resource(...)`）
3. 管理器根据需求和当前可用性找到合适的资源
4. 管理器分配资源并通知组件
5. 组件根据分配的资源计算性能指标
6. 任务完成后，组件将资源释放回管理器

## 6. 争用处理

资源管理器实现各种处理资源争用的策略：

- **排队：** 对忙碌资源的请求被放入优先级队列
- **基于优先级的分配：** 更高优先级的请求可以在低优先级请求之前得到服务
- **抢占：** 关键任务可以中断并从低优先级任务中获取资源
- **负载平衡：** 在多个类似资源之间分配请求

## 7. 使用模式

1.  **初始化：** 在环境设置期间，创建特定的 `ResourceManager` 实例（例如，`LandingManager`）并将它们注册到环境（例如，`env.landing_manager = LandingManager(env)`）。
2.  **资源创建：** 创建特定 `Resource` 类型的实例（例如，`LandingResource`）并将它们注册到相应的管理器（`landing_manager.register_resource(spot1)`）。
3.  **代理/组件交互：**
    *   代理或组件根据当前任务或状态确定其资源需求。
    *   它们与适当的 `ResourceManager` 交互以查找合适的资源（`find_resources`、`find_nearest_landing_spot`）。
    *   它们使用如 `request_resource` 的方法请求特定资源。
    *   管理器处理分配逻辑，如果资源忙碌，可能涉及队列。
    *   一旦分配，代理/组件使用资源。分配可能被隐式跟踪（例如，代理移动到着陆点）或显式跟踪（例如，代理将资源添加到其 `possessing_objects`）。
    *   完成后，代理/组件发出释放信号（例如，通过从 `possessing_objects` 中移除对象，这会触发由 `LandingManager` 中的 `allocate_resource` 设置的监听器，导致 `release_resource`）。
    *   管理器更新资源状态并处理任何等待的请求。

## 8. 可扩展性

*   通过继承 `aeroagentsim.core.resource.Resource` 创建新的资源类型。
*   通过继承 `aeroagentsim.core.resource.ResourceManager[YourResourceType]` 创建相应的管理器。
*   在自定义管理器中实现 `find_resources` 方法。
*   根据需要实现特定的分配/释放逻辑，可能重写或补充基础 `ResourceManager` 方法。
*   将自定义管理器实例注册到仿真环境。
