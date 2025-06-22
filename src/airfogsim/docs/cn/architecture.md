## AirFogSim: 系统架构文档

### 1. 简介

AirFogSim 是一个基于 SimPy 库构建的离散事件仿真框架，旨在对 UAV 集成的雾计算环境中的协作智能进行基准测试。它提供了一个全面的平台，用于模拟异构空中和地面节点之间的复杂交互，重点关注真实的通信、计算、能源和移动性建模。本文档概述了核心架构组件及其交互，提供了对系统设计的全面理解。

### 2. 核心理念

*   **高性能事件驱动仿真核心:** AirFogSim 采用优化的事件驱动仿真引擎，关键操作的计算复杂度达到亚 O(n log n)。这使得能够高效地模拟涉及数百个异构 UAV 和数千个跨越多个时间尺度的并发任务的大规模场景。

*   **基于工作流的任务组合框架:** 该框架提供了灵活和模块化的工作流驱动任务模型，明确捕获任务依赖关系、资源约束和异构空中和地面节点之间的协作交互。这有助于对复杂的多阶段 UAV 任务进行真实建模。

*   **符合标准的真实建模:** AirFogSim 集成了基于已建立标准的综合模型，包括符合 3GPP 的通信信道模型、经验验证的能耗配置文件、真实的计算能力和基于物理的移动模式。此外，模块化的数据提供者系统允许无缝集成真实世界的数据集，以增强真实性。

*   **以代理为中心的自主性:** `Agent` 实例是主要角色。它们拥有内部状态，拥有功能性 `Component`，并根据其状态、分配的 `Workflow`（目标）和通过事件对环境的感知，自主决定执行 `Task`。

*   **事件驱动交互:** 通信和同步主要依赖于中央化的 `EventRegistry`。状态变化、任务生命周期事件、资源更新、工作流进展和触发器激活都作为事件发布，允许解耦的组件订阅和做出反应。

*   **基于组件的能力:** 代理利用 `Component` 封装特定功能（例如，移动性、计算、感知）。组件管理 `Task` 的执行环境并与底层资源交互。

*   **任务封装:** `Task` 对象封装特定动作的逻辑，定义*如何*执行工作，需要什么资源（通过组件隐式地），消耗什么指标，以及产生什么代理状态。

*   **工作流驱动目标:** `Workflow` 代表更高级别的目标或过程。它们利用由 `Trigger` 驱动的 `WorkflowStatusMachine` 监控仿真事件（代理状态、任务完成、时间）并协调整体过程，而不直接执行任务。

*   **基于触发器的反应性:** `Trigger` 系统提供了对各种条件（事件、状态变化、时间）做出反应的灵活机制。触发器是驱动 `WorkflowStatusMachine` 转换和启用自动响应的基础。

*   **管理资源:** 仿真资源（例如，着陆点、CPU、空域、频谱）由专用的 `Manager` 类管理，处理注册、分配、争用和动态属性变化。

*   **模块化和可扩展性:** 该架构通过子类化核心实体（`Agent`、`Component`、`Task`、`Workflow`、`Trigger`、`Resource` 和特定的 `Manager`）促进扩展。

### 3. 关键组件和交互

#### 3.1 仿真环境 (`airfogsim.core.environment.Environment`)

*   **核心:** 扩展 `simpy.Environment` 用于离散事件调度，为仿真的时间进展提供基础。
*   **中央枢纽:** 作为核心管理器和服务的中央注册表和访问点，促进对仿真资源和服务的协调访问。
*   **关键管理器（通常是 `env` 的属性）:**
    *   **`EventRegistry` (`airfogsim.core.event.EventRegistry`):** 用于在所有仿真实体之间发布和订阅命名事件的中央总线。通过发布-订阅模式实现解耦通信，支持源 ID 和事件名称的通配符。
    *   **`TaskManager` (`airfogsim.manager.task_manager.TaskManager`):** 管理 `Task` 实例的创建和跟踪。提供访问任务信息的中央点，包括任务状态、所有权和执行指标。
    *   **`WorkflowManager` (`airfogsim.manager.workflow.WorkflowManager`):** 管理 `Workflow` 实例的生命周期（创建、启动、跟踪状态）。监听工作流状态变化事件，并提供工作流创建、启动和状态跟踪的方法。
    *   **`TriggerManager` (`airfogsim.manager.trigger.TriggerManager`):** 管理 `Trigger` 实例的生命周期和查找，提供中央化的激活、停用和回调管理。
    *   **资源管理器:**
        *   **`LandingManager` (`airfogsim.manager.landing.LandingManager`):** 管理着陆点和充电站，处理着陆、起飞和充电操作的资源分配。
        *   **`AirspaceManager` (`airfogsim.manager.airspace.AirspaceManager`):** 使用基于八叉树的空间索引管理空间查询和空域资源分配/冲突解决，用于高效的碰撞检测和邻近查询。
        *   **`FrequencyManager` (`airfogsim.manager.frequency.FrequencyManager`):** 使用符合 3GPP 的信道模型管理频谱资源，处理频率分配、干扰建模和信号质量计算。
    *   **`ContractManager` (`airfogsim.manager.contract.ContractManager`):** 管理代理之间的任务外包合约，促进协作任务的协商、协议和执行。
    *   **数据提供者:**
        *   **`WeatherIntegration` (`airfogsim.dataprovider.weather_integration.WeatherIntegration`):** 提供实时或模拟的天气数据，影响飞行条件、能源消耗和通信质量。
        *   **`TrafficIntegration` (`airfogsim.dataprovider.traffic_integration.TrafficIntegration`):** 与 SUMO 等交通仿真工具集成，为 UAV-车辆协调场景提供真实的地面交通模式。
        *   **`SensorDataProvider` (`airfogsim.dataprovider.sensor.SensorDataProvider`):** 为环境监测、监视和感知任务提供模拟的传感器数据。

#### 3.2 代理 (`airfogsim.core.agent.Agent`)

*   **角色:** 自主决策者和状态维护实体。代表模拟的角色，如无人机、地面站等。
*   **源代码:** `airfogsim/core/agent.py`
*   **关键特性:**
    *   **状态管理:** 维护由 `StateTemplate` 使用 `AgentMeta` 元类定义和验证的内部状态（`self.state`）。跟踪位置、状态、电池电量等属性。还可以通过复合键访问拥有对象的状态。
    *   **决策逻辑（`live` 方法）:** 一个必需的 SimPy 进程，定义代理的行为循环。它感知其状态、分配的 `Workflow`（检查 `workflow.get_current_suggested_task()`）、传入事件，并决定执行哪些 `Task`。可以与 LLM 集成进行规划。
    *   **组件所有权:** 拥有 `Component` 实例（`self.add_component()`），代表其能力（例如，移动性、感知）。
    *   **任务启动:** 通过调用 `self.execute_task(...)`，将执行委托给适当的 `Component` 来启动任务。监控已启动的任务（`self.managed_tasks`）。
    *   **事件处理:** 触发关于其自身状态变化（`state_changed`）、任务生命周期（`task_started`、`task_completed`）和拥有对象的事件。通过 `EventRegistry` 订阅来自组件、工作流、触发器或其他实体的事件。
    *   **拥有对象:** 可以使用 `add_possessing_object` 持有对其他仿真实体（例如，特定的 `LandingResource`）的引用，允许交互和状态监控。
*   **子类示例:** `airfogsim.agent.drone.DroneAgent`, `airfogsim.agent.delivery_drone.DeliveryDroneAgent`, `airfogsim.agent.delivery_station.DeliveryStationAgent`.

#### 3.3 组件 (`airfogsim.core.component.Component`)

*   **角色:** 抽象特定能力（例如，移动性、计算、充电）。为 `Task` 提供执行环境，并作为 Agent/Task 和底层 Resources/Managers 之间的接口。
*   **源代码:** `airfogsim/core/component.py`
*   **关键特性:**
    *   **任务执行 (`execute_task`, `_execute_task_wrapper`):** 管理来自 Agent 的任务执行请求的生命周期。验证是否可以执行任务，协调资源获取，运行任务逻辑，处理指标，并管理清理。
    *   **资源交互:** 实现 `get_resource_requirements(task)`（抽象）以定义资源需求。通过适当的 `Manager`（例如，`env.landing_manager`）请求资源。通过回调（`_on_resource_update`）处理资源更新。
    *   **指标计算 (`_calculate_performance_metrics` - 抽象):** 根据获取的资源的属性计算性能指标（例如，速度、处理率、能源消耗）。声明 `PRODUCED_METRICS`。
    *   **事件发射:** 通过 `self.trigger_event()` 在父 `Agent` 上触发命名空间事件（例如，`ComponentName.task_started`、`ComponentName.metric_changed`、`ComponentName.task_completed`）。
*   **子类示例:** `airfogsim.component.mobility.MobilityComponent`, `airfogsim.component.computation.ComputeComponent`, `airfogsim.component.charging.ChargingComponent`, `airfogsim.component.sensing.ImageSensingComponent`.

#### 3.4 任务 (`airfogsim.core.task.Task`)

*   **角色:** 封装特定动作的*逻辑*。根据提供的指标定义*如何*完成工作。代表仿真中的原子执行单元。
*   **源代码:** `airfogsim/core/task.py`
*   **关键特性:**
    *   **执行逻辑 (`execute` 方法):** 一个*由组件*运行的 SimPy 生成器。消耗由组件提供的性能 `metrics`。其核心循环通常等待时间流逝或指标更新（`ComponentName.metric_changed` 事件）。
    *   **指标消耗:** 声明从执行组件所需的 `NECESSARY_METRICS`，确保任务只在所有所需资源和能力可用时执行。
    *   **状态生成:** 实现 `_update_task_state(metrics)`（抽象）以更新内部进度。实现 `_get_task_specific_state_repr()`（抽象）以根据其逻辑和进度计算*Agent*状态变化。声明 `PRODUCED_STATES`。通过 `self.agent.update_states()` 更新 Agent 状态。
    *   **生命周期:** 管理自己的状态（`PENDING`、`RUNNING`、`COMPLETED`、`FAILED`、`CANCELED`），支持优先级和抢占属性，允许代理根据重要性和紧急性管理任务执行。
    *   **拥有对象钩子:** 为子类提供 `_possessing_object_on_complete/fail/cancel` 方法，以管理与任务生命周期相关的代理拥有的对象。
    *   **优先级和抢占:** 任务可以有优先级级别（例如，`TaskPriority.CRITICAL`、`TaskPriority.HIGH`）和抢占标志，允许系统模拟可以中断低优先级任务的紧急任务。
*   **子类示例:** `airfogsim.task.mobility.MoveToTask`, `airfogsim.task.compute.ComputeTask`, `airfogsim.task.charging.ChargeBatteryTask`, `airfogsim.task.logistics.LoadCargoTask`, `airfogsim.task.sensing.SensingTask`.

#### 3.5 工作流-代理-任务框架

*   **角色:** AirFogSim 的核心是工作流-代理-任务框架，它通过可重用组件的组合实现复杂任务场景的建模。
*   **工作流 (`airfogsim.core.workflow.Workflow`):**
    *   **角色:** 代表更高级别的目标或过程。主要作为基于仿真事件的**监视器和协调器**，由其内部 `WorkflowStatusMachine` 驱动。
    *   **源代码:** `airfogsim/core/workflow.py`
    *   **关键特性:**
        *   **整体状态:** 管理高级别状态（`WorkflowStatus` 枚举）。
        *   **属性:** 使用 `WorkflowPropertyTemplate`（由 `WorkflowMeta` 管理）定义预期参数。
        *   **状态机 (`self.status_machine`):** 包含管理工作流通过各个阶段进展的 `WorkflowStatusMachine` 实例。
        *   **转换设置 (`_setup_transitions` - 抽象):** 子类必须实现此方法，使用 `self.status_machine.add_transition()` 定义状态机逻辑。
        *   **启动/重置:** `start()` 启动工作流和状态机；`reset()` 允许重新启动。
        *   **上下文/指导:** `get_details()` 提供工作流上下文；`get_current_suggested_task()` 根据当前内部状态为代理建议下一个可能的任务。
*   **工作流状态机:**
    *   **内部状态:** 跟踪 `current_status`（字符串），代表工作流的当前阶段。
    *   **事件驱动转换:** 使用 `add_transition` 根据 `Trigger` 激活（监听代理状态、事件或时间）定义规则。
    *   **触发器管理:** 根据当前状态激活/停用相关触发器，确保只监控相关条件。
    *   **通知:** 在内部状态变化时通知父 `Workflow`（`workflow._trigger_status_changed`）。
*   **关键概念:** 状态转换不是预定义的序列，而是由仿真触发器动态驱动的。这些触发器主动监控仿真环境中的特定条件，只有在满足条件时才允许从一个状态转换到下一个状态。
*   **子类示例:** `airfogsim.workflow.inspection.InspectionWorkflow`, `airfogsim.workflow.charging.ChargingWorkflow`, `airfogsim.workflow.contract.ContractWorkflow`, `airfogsim.workflow.image_processing.ImageProcessingWorkflow`, `airfogsim.workflow.order_execution.OrderExecutionWorkflow`.

#### 3.6 触发器 (`airfogsim.core.trigger.Trigger`)

*   **角色:** 监控特定仿真条件并在满足时执行回调。驱动 `WorkflowStatusMachine` 转换。
*   **源代码:** `airfogsim/core/trigger.py`
*   **关键特性:**
    *   **条件监控:** 检查事件发生、代理状态变化或时间流逝。
    *   **激活/停用:** 必须激活（`activate()`）才能监控；可以停止（`deactivate()`）。通常由 `WorkflowStatusMachine` 管理。
    *   **回调:** 在触发时执行注册的函数（`add_callback()`），传递上下文。
*   **子类类型:**
    *   `EventTrigger`: 根据源、名称和值条件对 `EventRegistry` 事件做出反应。
    *   `StateTrigger`: 根据代理 ID、状态键和值条件对 `Agent.state_changed` 事件做出反应。
    *   `TimeTrigger`: 根据绝对时间、间隔或类似 cron 的调度（目前简化为间隔）触发。
    *   `CompositeTrigger`: 使用逻辑 AND 或 OR 组合多个触发器。

#### 3.7 资源管理

*   **角色:** AirFogSim 中的资源代表组件需要产生指标的有限共享资产。这些包括物理资源，如空域、着陆点和频谱。
*   **资源框架:**
    *   **`Resource` 基类 (`airfogsim.core.resource.Resource`):** 作为任何单个资源实例的基本表示。它维护唯一 ID、特定属性的字典、操作状态（例如，`ResourceStatus.AVAILABLE`、`ResourceStatus.FULLY_ALLOCATED`）并跟踪其当前分配。
    *   **子类示例:** `airfogsim.resource.landing.LandingResource`, `airfogsim.resource.frequency.FrequencyResource`, `airfogsim.resource.computation.ComputationResource`.
*   **资源管理:**
    *   **`ResourceManager<R>` 基类 (`airfogsim.core.resource.ResourceManager`):** 框架的核心是通用 `ResourceManager<R>` 基类，设计用于管理特定类型 R 的资源集合。其主要职责包括资源生命周期管理、分配管理和状态跟踪。
    *   **特定管理器:**
        *   **`LandingManager` (`airfogsim.manager.landing.LandingManager`):** 管理着陆点和充电站，处理着陆、起飞和充电操作的资源分配。
        *   **`FrequencyManager` (`airfogsim.manager.frequency.FrequencyManager`):** 使用符合 3GPP 的信道模型管理频谱资源，处理频率分配、干扰建模和信号质量计算。
        *   **`ComputationManager` (`airfogsim.manager.computation.ComputationManager`):** 管理计算资源，处理任务分配、处理能力分配和计算负载平衡。
    *   **资源分配过程:**
        1. 组件通过 `get_resource_requirements(task)` 识别任务的资源需求
        2. 组件从适当的管理器请求资源（例如，`env.landing_manager.request_resource(...)`）
        3. 管理器根据需求和当前可用性找到合适的资源
        4. 管理器分配资源并通知组件
        5. 组件根据分配的资源计算性能指标
        6. 任务完成后，组件将资源释放回管理器
    *   **争用处理:** 资源管理器实现处理资源争用的策略，包括排队、基于优先级的分配和抢占机制。

### 4. 典型交互流程（简化）

1.  创建 `Workflow` 并分配给 `Agent`（由 `WorkflowManager` 管理）。调用 `WorkflowManager.start_workflow()`。
2.  `Workflow` 状态变为 `RUNNING`。它启动其 `WorkflowStatusMachine`。
3.  `WorkflowStatusMachine` 进入其初始状态并激活该状态的相关 `Trigger`（例如，监控 `Agent.status` 的 `StateTrigger`，监听 `task_completed` 的 `EventTrigger`）。
4.  `Agent` 的 `live()` 循环运行。它检查其活动 `Workflow`，可能调用 `workflow.get_current_suggested_task()` 获取指导。
5.  根据其内部逻辑和工作流指导，`Agent` 决定执行 `Task`。它调用 `agent.execute_task(component_name, task_class, properties, ...)`。
6.  `Agent` 找到指定的 `Component`。
7.  `Agent` 使用 `TaskManager` 创建 `Task` 实例。
8.  `Agent` 调用 `component.execute_task(task_instance)`。
9.  `Component` 的 `_execute_task_wrapper` 开始：
    a.  触发 `ComponentName.task_started` 事件。
    b.  调用 `component.get_resource_requirements(task)`。
    c.  从相关 `Manager` 请求资源（例如，`env.landing_manager.request_resource(...)`）。如果资源不可用，这可能涉及等待。
    d.  一旦获取资源，使用 `component._calculate_performance_metrics(resources)` 计算初始 `metrics`。
    e.  触发 `ComponentName.metric_changed` 事件。
    f.  调用并 yield `task.execute(initial_metrics)`。
10. 在 `task.execute()` 内部：
    a.  任务进入其主循环，等待时间流逝或 `ComponentName.metric_changed` 事件。
    b.  如果指标变化，任务更新其内部状态。
    c.  调用 `task._update_task_state(metrics)` 更新进度。
    d.  调用 `task._get_task_specific_state_repr()` 获取代理状态更新。
    e.  调用 `agent.update_states(...)` -> `Agent.state_changed` 事件触发。
11. 由 `WorkflowStatusMachine` 激活的 `Trigger` 可能触发：
    a.  **示例 1：** 监听 `Agent.state_changed` 事件（来自步骤 10e）的 `StateTrigger` 检测到符合其标准的条件。
    b.  **示例 2：** 当任务完成时，监听 `ComponentName.task_completed` 的 `EventTrigger` 触发。
12. 激活的 `Trigger` 调用其回调，即 `WorkflowStatusMachine._on_trigger_activated`。
13. `_on_trigger_activated` 调用 `_change_status`，更新机器的 `current_status`。
14. `_change_status` 调用 `workflow._trigger_status_changed`。
15. `workflow._trigger_status_changed` 根据需要更新 `Workflow` 的整体状态（例如，将内部 'completed' 映射到 `WorkflowStatus.COMPLETED`）并触发 `sm_status_changed` 和可能的 `workflow_status_changed` 事件。
16. `WorkflowStatusMachine` 的监控进程被中断，重新启动，停用旧触发器，并激活*新*内部状态的触发器。
17. 当 `task.execute()` 完成时，它向 `Component` 返回结果。
18. `Component` 触发 `ComponentName.task_completed/failed`，将资源释放回 `Manager`，并清理。
19. `Agent` 接收 `agent.task_completed` 事件（如果已订阅）。
20. Agent 的 `live()` 循环继续，观察更新的 Agent 状态和可能的新 `Workflow` 状态/建议，以规划下一个操作。

### 5. 数据流摘要

*   **状态:** 由 `Agent` 维护，主要由 `Task` 执行更新。由 `StateTrigger`（由 `Workflow` 使用）监控。用于 `Agent` 决策。
*   **属性:** `Resource` 实例的属性。由特定 `Manager` 管理和更新（建模争用、波动）。
*   **指标:** 从资源 `attributes` 派生的性能特征。由 `Component` 计算，由 `Task` 消耗。
*   **事件:** 链接状态变化、任务生命周期、资源更新、触发器激活和工作流进展的主要通信机制。由 `EventRegistry` 管理。
*   **触发器:** 监控事件和状态以驱动 `WorkflowStatusMachine` 转换。
*   **工作流属性:** `Workflow` 的配置和目标参数。
*   **任务属性:** 特定 `Task` 实例的配置参数。

### 6. 可扩展性

新功能通常通过创建以下子类添加：

*   `Agent`: 用于不同类型的自主实体。
*   `Component`: 用于新能力。
*   `Task`: 用于新的特定动作。
*   `Workflow`: 用于新的高级目标和状态机逻辑。
*   `Trigger`: 用于自定义条件监控（不太常见）。
*   `Resource`: 用于新类型的仿真资源。
*   特定 `Manager`: 用于管理新资源类型或提供新的中央服务。
