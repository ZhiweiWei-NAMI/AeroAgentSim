## AirFogSim: 系统架构文档

### 1. 引言

AirFogSim 是一个基于 SimPy 库构建的离散事件仿真框架。它旨在建模和仿真涉及自主 Agent（如无人机、地面站）、动态资源、事件驱动交互、任务执行和面向目标的工作流的复杂系统。本文档概述了核心架构组件及其交互，提供了对系统设计的全面理解。

### 2. 核心理念

*   **以 Agent 为中心的自主性:** `Agent` 实例是主要参与者。它们拥有内部状态，拥有功能性 `Component`，并根据其状态、分配的 `Workflow`（目标）以及通过事件对环境的感知来自主决定执行 `Task`。
*   **事件驱动的交互:** 通信和同步严重依赖于集中的 `EventRegistry`。状态变化、任务生命周期事件、资源更新、工作流进展和触发器激活都作为事件发布，允许解耦的组件订阅和响应。
*   **基于组件的能力:** Agent 利用 `Component` 来封装特定功能（例如，移动性、计算、传感）。组件管理 `Task` 的执行环境，并与底层资源交互。
*   **任务封装:** `Task` 对象封装了特定动作的逻辑，定义了工作*如何*执行，需要哪些资源（通过组件隐式定义），消耗哪些指标，以及产生哪些 Agent 状态。
*   **工作流驱动的目标:** `Workflow` 代表更高级别的目标或过程。它们利用由 `Trigger` 驱动的 `WorkflowStatusMachine` 来监控仿真事件（Agent 状态、任务完成、时间），并在不直接执行任务的情况下协调整个过程。
*   **基于触发器的反应性:** `Trigger` 系统提供了一种灵活的机制来响应各种条件（事件、状态变化、时间）。触发器是驱动 `WorkflowStatusMachine` 转换和实现自动化响应的基础。
*   **托管资源:** 仿真资源（例如，着陆点、CPU、空域）由专门的 `Manager` 类管理，处理注册、分配、竞争和动态属性更改。
*   **模块化和可扩展性:** 该架构通过子类化核心实体（`Agent`, `Component`, `Task`, `Workflow`, `Trigger`, `Resource` 和特定的 `Manager`）来促进扩展。

### 3. 关键组件和交互

#### 3.1 仿真环境 (`airfogsim.core.environment.Environment`)

*   **核心:** 扩展 `simpy.Environment` 以进行离散事件调度。
*   **中心枢纽:** 作为核心管理器和服务的中央注册表和访问点。
*   **关键管理器 (通常是 `env` 的属性):**
    *   **`EventRegistry` (`airfogsim.core.event.EventRegistry`):** 用于在所有仿真实体之间发布和订阅命名事件的中央总线。实现解耦通信。
    *   **`TaskManager` (`airfogsim.manager.task_manager.TaskManager`):** 管理 `Task` 实例的创建和跟踪。提供访问任务信息的中心点。
    *   **`WorkflowManager` (`airfogsim.manager.workflow.WorkflowManager`):** 管理 `Workflow` 实例的生命周期（创建、启动、跟踪状态）。监听工作流状态更改事件。
    *   **`TriggerManager` (`airfogsim.manager.trigger.TriggerManager`):** 管理 `Trigger` 实例的生命周期和查找。
    *   **资源管理器 (特定):** 各种资源管理器（例如 `LandingManager`, `AirspaceManager`, `FrequencyManager`）的实例，负责特定资源类型。存在单一 `ResourceManager` 基类的概念，但具体管理器处理具体类型。
    *   **`ContractManager` (`airfogsim.manager.contract.ContractManager`):** (可选) 管理 Agent 之间的任务卸载合约。
    *   **`AirspaceManager` (`airfogsim.manager.airspace.AirspaceManager`):** (可选) 管理空间查询和潜在的空域资源分配/冲突解除。

#### 3.2 Agent (`airfogsim.core.agent.Agent`)

*   **角色:** 自主的决策者和状态维护实体。代表模拟的参与者，如无人机、地面站等。
*   **源代码:** `airfogsim/core/agent.py`
*   **关键特性:**
    *   **状态管理:** 维护由 `StateTemplate` 定义和验证的内部状态 (`self.state`)，使用 `AgentMeta` 元类。跟踪位置、状态、电池电量等属性。也可以通过复合键访问拥有对象的状态。
    *   **决策逻辑 (`live` 方法):** 一个必需的 SimPy 进程，定义 Agent 的行为循环。它感知其状态、分配的 `Workflow`（检查 `workflow.get_current_suggested_task()`）、传入事件，并决定执行哪些 `Task`。可以与 LLM 集成进行规划。
    *   **组件所有权:** 拥有 `Component` 实例 (`self.add_component()`)，代表其能力（例如，移动性、传感）。
    *   **任务启动:** 通过调用 `self.execute_task(...)` 启动任务，该方法将执行委托给适当的 `Component`。监控已启动的任务 (`self.managed_tasks`)。
    *   **事件处理:** 触发关于自身状态变化 (`state_changed`)、任务生命周期 (`task_started`, `task_completed`) 和拥有对象的事件。通过 `EventRegistry` 订阅来自组件、工作流、触发器或其他实体的事件。
    *   **拥有对象:** 可以使用 `add_possessing_object` 持有对其他模拟实体（例如，特定的 `LandingResource`）的引用，允许交互和状态监控。
*   **子类示例:** `airfogsim.agent.drone.DroneAgent`, `airfogsim.agent.delivery_drone.DeliveryDroneAgent`, `airfogsim.agent.delivery_station.DeliveryStationAgent`。

#### 3.3 Component (`airfogsim.core.component.Component`)

*   **角色:** 抽象特定能力（例如，移动性、计算、充电）。为 `Task` 提供执行环境，并充当 Agent/Task 与底层资源/管理器之间的接口。
*   **源代码:** `airfogsim/core/component.py`
*   **关键特性:**
    *   **任务执行 (`execute_task`, `_execute_task_wrapper`):** 管理来自 Agent 的任务执行请求的生命周期。验证是否可以执行任务，协调资源获取，运行任务逻辑，处理指标，并管理清理。
    *   **资源交互:** 实现 `get_resource_requirements(task)` (抽象) 以定义资源需求。通过适当的 `Manager` 请求资源（例如 `env.landing_manager`）。通过回调 (`_on_resource_update`) 处理资源更新。
    *   **指标计算 (`_calculate_performance_metrics` - 抽象):** 根据获取资源的属性计算性能指标（例如，速度、处理速率、能耗）。声明 `PRODUCED_METRICS`。
    *   **事件发射:** 通过 `self.trigger_event()` 在父 `Agent` 上触发命名空间事件（例如 `ComponentName.task_started`, `ComponentName.metric_changed`, `ComponentName.task_completed`）。
*   **子类示例:** `airfogsim.component.mobility.MobilityComponent`, `airfogsim.component.computation.ComputeComponent`, `airfogsim.component.charging.ChargingComponent`, `airfogsim.component.sensing.SensingComponent`。

#### 3.4 Task (`airfogsim.core.task.Task`)

*   **角色:** 封装特定动作的*逻辑*。定义*如何*根据提供的指标完成工作。代表 Agent 规划的结果。
*   **源代码:** `airfogsim/core/task.py`
*   **关键特性:**
    *   **执行逻辑 (`execute` 方法):** 一个*由组件运行*的 SimPy 生成器。消耗组件提供的性能 `metrics`。其核心循环通常等待时间流逝或指标更新 (`ComponentName.metric_changed` 事件)。
    *   **指标消耗:** 声明执行组件所需的 `NECESSARY_METRICS`。
    *   **状态生成:** 实现 `_update_task_state(metrics)` (抽象) 以更新内部进度。实现 `_get_task_specific_state_repr()` (抽象) 以根据其逻辑和进度计算*Agent*状态变化。声明 `PRODUCED_STATES`。通过 `self.agent.update_states()` 更新 Agent 状态。
    *   **生命周期:** 管理自身状态 (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELED`)。
    *   **拥有对象钩子:** 提供 `_possessing_object_on_complete/fail/cancel` 方法，供子类管理与任务生命周期相关的 Agent 拥有对象。
*   **子类示例:** `airfogsim.task.mobility.MoveToTask`, `airfogsim.task.compute.ComputeTask`, `airfogsim.task.charging.ChargeBatteryTask`, `airfogsim.task.logistics.LoadCargoTask`。

#### 3.5 Workflow (`airfogsim.core.workflow.Workflow`) & 状态机 (`WorkflowStatusMachine`)

*   **角色:** 代表更高级别的目标或过程。主要充当基于仿真事件的**监视器和协调器**，由其内部 `WorkflowStatusMachine` 驱动。
*   **源代码:** `airfogsim/core/workflow.py`
*   **关键特性 (`Workflow`):**
    *   **整体状态:** 管理高级别状态 (`WorkflowStatus` 枚举)。
    *   **属性:** 使用 `WorkflowPropertyTemplate` 定义预期参数（由 `WorkflowMeta` 管理）。
    *   **状态机 (`self.status_machine`):** 包含 `WorkflowStatusMachine` 实例。
    *   **转换设置 (`_setup_transitions` - 抽象):** 子类必须实现此方法，以使用 `self.status_machine.add_transition()` 定义状态机逻辑。
    *   **启动/重置:** `start()` 启动工作流和状态机；`reset()` 允许重新启动。
    *   **上下文/指导:** `get_details()` 提供工作流上下文；`get_current_suggested_task()` 根据当前内部状态建议 Agent 可能执行的下一个任务。
*   **关键特性 (`WorkflowStatusMachine`):**
    *   **内部状态:** 跟踪 `current_status` (字符串)。
    *   **事件驱动转换:** 使用 `add_transition` 根据 `Trigger` 激活（监听 Agent 状态、事件或时间）定义规则。
    *   **触发器管理:** 根据当前状态激活/停用相关触发器。
    *   **通知:** 在内部状态更改时通知父 `Workflow` (`workflow._trigger_status_changed`)。
*   **子类示例:** `airfogsim.workflow.inspection.InspectionWorkflow`, `airfogsim.workflow.charging.ChargingWorkflow`, `airfogsim.workflow.contract.ContractWorkflow`, `airfogsim.workflow.image_processing.ImageProcessingWorkflow`。

#### 3.6 Trigger (`airfogsim.core.trigger.Trigger`)

*   **角色:** 监控特定的仿真条件，并在满足条件时执行回调。驱动 `WorkflowStatusMachine` 转换。
*   **源代码:** `airfogsim/core/trigger.py`
*   **关键特性:**
    *   **条件监控:** 检查事件发生、Agent 状态变化或时间流逝。
    *   **激活/停用:** 必须激活 (`activate()`) 才能开始监控；可以停止 (`deactivate()`)。通常由 `WorkflowStatusMachine` 管理。
    *   **回调:** 在触发时执行注册的函数 (`add_callback()`)，并传递上下文。
*   **子类类型:**
    *   `EventTrigger`: 根据来源、名称和值条件响应 `EventRegistry` 事件。
    *   `StateTrigger`: 根据 Agent ID、状态键和值条件响应 `Agent.state_changed` 事件。
    *   `TimeTrigger`: 根据绝对时间、间隔或类似 cron 的计划（目前简化为间隔）触发。
    *   `CompositeTrigger`: 使用逻辑 AND 或 OR 组合多个触发器。

#### 3.7 资源层 (`airfogsim.core.resource.Resource` & 管理器)

*   **角色:** 对可以被 Agent 或组件利用或消耗的实体进行建模。
*   **`Resource` 基类 (`airfogsim.core.resource.Resource`):** 定义通用属性，如 `id`, `attributes`, `status`。子类代表特定的资源类型。
    *   **子类示例:** `airfogsim.resource.landing.LandingResource`, `airfogsim.resource.frequency.FrequencyResource`。
*   **`ResourceManager` 基类 (`airfogsim.core.resource.ResourceManager`):** 用于管理特定类型资源的通用基类。定义注册、查找、分配和释放的方法。
*   **特定管理器 (例如 `airfogsim.manager.landing.LandingManager`):**
    *   继承自 `ResourceManager`（或实现类似逻辑）。
    *   管理特定 `Resource` 子类的实例（例如 `LandingManager` 管理 `LandingResource`）。
    *   实现特定于资源的查找 (`find_resources`)、分配 (`allocate_resource`, `request_resource`)、释放 (`release_resource`) 逻辑，并可能对竞争或动态属性变化进行建模。
    *   通常与其他管理器交互（例如 `LandingManager` 使用 `AirspaceManager` 进行空间查询）。

### 4. 典型交互流程 (简化)

1.  创建 `Workflow` 并将其分配给 `Agent`（由 `WorkflowManager` 管理）。调用 `WorkflowManager.start_workflow()`。
2.  `Workflow` 状态变为 `RUNNING`。它启动其 `WorkflowStatusMachine`。
3.  `WorkflowStatusMachine` 进入其初始状态并激活该状态的相关 `Trigger`（例如，监控 `Agent.status` 的 `StateTrigger`，监听 `task_completed` 的 `EventTrigger`）。
4.  `Agent` 的 `live()` 循环运行。它检查其活动的 `Workflow`，可能调用 `workflow.get_current_suggested_task()` 获取指导。
5.  根据其内部逻辑和工作流指导，`Agent` 决定执行 `Task`。它调用 `agent.execute_task(component_name, task_class, properties, ...)`。
6.  `Agent` 找到指定的 `Component`。
7.  `Agent` 使用 `TaskManager` 创建 `Task` 实例。
8.  `Agent` 调用 `component.execute_task(task_instance)`。
9.  `Component` 的 `_execute_task_wrapper` 开始：
    a.  触发 `ComponentName.task_started` 事件。
    b.  调用 `component.get_resource_requirements(task)`。
    c.  从相关 `Manager` 请求资源（例如 `env.landing_manager.request_resource(...)`）。如果资源不可用，可能需要等待。
    d.  获取资源后，使用 `component._calculate_performance_metrics(resources)` 计算初始 `metrics`。
    e.  触发 `ComponentName.metric_changed` 事件。
    f.  调用并 yield `task.execute(initial_metrics)`。
10. 在 `task.execute()` 内部：
    a.  任务进入主循环，等待时间流逝或 `ComponentName.metric_changed` 事件。
    b.  如果指标发生变化，任务更新其内部状态。
    c.  调用 `task._update_task_state(metrics)` 更新进度。
    d.  调用 `task._get_task_specific_state_repr()` 获取 Agent 状态更新。
    e.  调用 `agent.update_states(...)` -> `Agent.state_changed` 事件触发。
11. 由 `WorkflowStatusMachine` 激活的 `Trigger` 可能触发：
    a.  **示例 1:** 监听 `Agent.state_changed` 事件（来自步骤 10e）的 `StateTrigger` 检测到符合其标准的条件。
    b.  **示例 2:** 监听 `ComponentName.task_completed` 的 `EventTrigger` 在任务完成时触发。
12. 激活的 `Trigger` 调用其回调，即 `WorkflowStatusMachine._on_trigger_activated`。
13. `_on_trigger_activated` 调用 `_change_status`，更新机器的 `current_status`。
14. `_change_status` 调用 `workflow._trigger_status_changed`。
15. `workflow._trigger_status_changed` 根据需要更新 `Workflow` 的整体状态（例如，将内部 'completed' 映射到 `WorkflowStatus.COMPLETED`）并触发 `sm_status_changed` 和可能的 `workflow_status_changed` 事件。
16. `WorkflowStatusMachine` 的监视器进程被中断，重新启动，停用旧触发器，并激活*新*内部状态的触发器。
17. 当 `task.execute()` 完成时，它向 `Component` 返回结果。
18. `Component` 触发 `ComponentName.task_completed/failed`，将资源释放回 `Manager`，并进行清理。
19. `Agent` 接收到 `agent.task_completed` 事件（如果已订阅）。
20. Agent 的 `live()` 循环继续，观察更新后的 Agent 状态和可能的新 `Workflow` 状态/建议，以规划下一个动作。

### 5. 数据流摘要

*   **状态:** 由 `Agent` 维护，主要由 `Task` 执行更新。由 `StateTrigger` 监控（由 `Workflow` 使用）。用于 `Agent` 决策。
*   **属性:** `Resource` 实例的属性。由特定的 `Manager` 管理和更新（建模竞争、波动）。
*   **指标:** 从资源 `attributes` 派生的性能特征。由 `Component` 计算，由 `Task` 消耗。
*   **事件:** 连接状态变化、任务生命周期、资源更新、触发器激活和工作流进展的主要通信机制。由 `EventRegistry` 管理。
*   **触发器:** 监控事件和状态以驱动 `WorkflowStatusMachine` 转换。
*   **工作流属性:** `Workflow` 的配置和目标参数。
*   **任务属性:** 特定 `Task` 实例的配置参数。

### 6. 可扩展性

通常通过创建以下类的子类来添加新功能：

*   `Agent`: 用于不同类型的自主实体。
*   `Component`: 用于新能力。
*   `Task`: 用于新的特定动作。
*   `Workflow`: 用于新的高级目标和状态机逻辑。
*   `Trigger`: 用于自定义条件监控（不太常见）。
*   `Resource`: 用于新型仿真资源。
*   特定 `Manager`: 用于管理新的资源类型或提供新的中央服务。