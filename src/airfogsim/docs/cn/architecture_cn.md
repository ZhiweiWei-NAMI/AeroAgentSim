## AirFogSim: 系统架构文档

### 1. 引言

AirFogSim 是一个基于 SimPy 构建的离散事件仿真框架，旨在模拟涉及自治代理、动态资源、任务执行和面向目标的工作流的复杂系统。本文档概述了核心架构组件及其交互，提供了对系统设计的高层理解。

### 2. 核心理念

*   **以代理为中心的自治 (Agent-Centric Autonomy):** `Agent` (代理) 是主要的行动者，拥有内部状态，并根据其状态、目标 (Workflows) 和环境感知来自主决定行动 (Tasks)。
*   **事件驱动的交互 (Event-Driven Interaction):** 组件之间的通信和同步主要通过集中的 `EventRegistry` (事件注册中心) 进行。状态变化、任务完成、资源更新和工作流进展会触发事件，其他组件可以订阅并对此做出反应。
*   **关注点分离 (Separation of Concerns):** 每个核心组件都有明确的职责：
    *   **Agent:** 状态维护、决策制定、任务发起。
    *   **Component:** 能力抽象、任务执行环境、资源交互、指标计算。
    *   **Task:** 行动逻辑的封装、状态产出、指标消耗。
    *   **Workflow:** 目标表示、通过事件监控代理/任务进度。
    *   **Resource Layer:** 建模资源可用性、属性、动态性（竞争、波动）。
*   **模块化与可扩展性 (Modularity and Extensibility):** 该架构设计旨在通过子类化核心组件（Agents, Components, Tasks, Workflows, Resource Pools）来进行扩展。

### 3. 关键组件与交互

1.  **仿真环境 (`env`)**
    *   **核心:** 基于 `simpy.Environment` 实现离散事件调度。
    *   **事件注册中心 (`env.event_registry`):** 用于在所有仿真实体之间发布和订阅命名事件的中央总线。实现解耦通信。
    *   **资源管理器 (`env.resource_manager`):** 请求和释放资源的中心点。它将请求委托给相应的 `SpecificResourcePool`。
    *   **工作流管理器 (`env.workflow_manager`):** 管理 `Workflow` 实例的生命周期（创建、启动、跟踪状态）。监听工作流状态变化事件。
    *   **任务管理器 (`env.task_manager`):** 管理 `Task` 实例的生命周期，通过任务兼容性评估和推荐系统指导代理的任务规划和执行。
    *   **(概念性) MCP 服务器 (`env.mcp_server`):** 一个可选的集中式服务，协助代理进行复杂的任务规划，特别是涉及 LLM 的规划。收集上下文，与 LLM 交互，将结果解析为任务计划。

2.  **代理 (`airfogsim.core.Agent`)**
    *   **角色:** 自治的决策者和状态维护实体。代表模拟实体，如无人机、地面站、用户。
    *   **状态:** 维护由 `StateTemplate` 定义和验证的内部状态 (`self.state`)。状态表示代理的当前状况（位置、电池、状态等）。
    *   **决策逻辑 (`live` 方法):** 定义代理行为循环的 SimPy 进程。它感知自身状态、分配的 Workflows，并可能咨询 `MCPServer` 或使用内部逻辑来决定执行哪些 Tasks。
    *   **组件所有权:** 拥有 `Component` 实例，代表其能力。
    *   **任务发起:** 调用 `Component.execute_task(task)` 来启动动作。管理已发起任务的生命周期 (`self.managed_tasks`)。
    *   **任务管理:** 使用 `TaskManager` 提供的功能来评估、推荐和规划任务。
    *   **事件源/汇:** 触发关于自身状态变化 (`state_changed`) 和任务生命周期 (`task_started`, `task_finished`) 的事件。订阅来自其组件、工作流或其他实体的事件。

3.  **组件 (`airfogsim.core.Component`)**
    *   **角色:** 抽象特定能力（例如，移动性、计算、传感）。为 Tasks 提供执行环境。充当 Agent/Task 与底层 Resources 之间的接口。
    *   **任务执行:** 实现 `execute_task(task)`，该方法管理资源获取（通过 `ResourceManager`）、运行 `Task.execute()` 逻辑、处理指标并管理清理/资源释放。
    *   **资源交互:** 根据 Task 需求 (`get_resource_requirements`) 通过 `ResourceManager` 请求资源。接收 `ResourceWrapper` 实例。
    *   **指标计算:** **关键地，将资源*属性* (来自 `ResourceWrapper`) 转换为任务*指标* (`_calculate_performance_metrics`)。** 这些指标（例如，有效速度、处理速率）基于当前资源状态和竞争情况量化了能力。
    *   **事件源:** 在 Agent 上触发带命名空间的事件（例如 `ComponentName.task_started`, `ComponentName.metric_changed`），以通知任务进度和性能更新。

4.  **任务 (`airfogsim.core.Task`)**
    *   **角色:** 封装特定动作的*逻辑*。定义工作*如何*完成。代表代理规划的结果。
    *   **执行逻辑 (`execute` 方法):** 一个由 `Component` 运行的 SimPy 生成器。消耗由 Component 提供的性能 `metrics` (指标)。其核心循环通常等待时间流逝或指标更新。
    *   **状态产出:** 基于其逻辑和消耗的指标更新其进度并计算*Agent 状态变化*。通过 `_get_task_specific_state_repr()` 将这些变化传回给 Agent。子类声明 `PRODUCED_STATES`。
    *   **指标消耗:** 声明需要从 Component 获取的 `NECESSARY_METRICS`。
    *   **证明处理:** 可选地创建/更新 `TaskProof` 对象以记录可验证的结果。触发与证明相关的事件 (`_trigger_workflow_events`)。

5.  **工作流 (`airfogsim.core.Workflow`)**
    *   **角色:** 代表更高级别的目标或过程。主要充当基于事件的**监控器和协调器**。
    *   **状态机 (`WorkflowStatusMachine`):** 包含由仿真事件驱动的内部状态转换逻辑。
    *   **事件监控:** 状态机订阅在 `_setup_transitions` 中定义的特定事件（通常是 `Agent.state_changed`, `Agent.proof_updated`, `Component.task_completed`）。条件函数检查事件详情。
    *   **被动性质:** 工作流本身不直接*执行*任务。它们*监控*由代理取得的进展（通过任务执行产生的状态变化和证明），并相应地转换其内部状态。当前的内部状态可能会影响代理的*下一个*规划决策。
    *   **事件源:** 当其内部状态机转换时，触发 `status_changed` 事件，允许 `WorkflowManager` 或其他组件跟踪其进度。

6.  **资源层 (`airfogsim.core.resource`)**
    *   **`SpecificResourcePool` (ABC):** 管理特定类型的所有资源（例如 `CpuPool`, `AirspacePool`）。处理创建/删除、竞争建模 (`_adjust_attributes_for_contention`) 和波动模拟 (`_generate_attribute_fluctuations`)。定义请求模板。
    *   **`ResourceWrapper`:** 代表具有动态 `attributes` (属性，例如容量、速度、延迟、状态) 的单个资源实例。通过回调通知订阅者（Components）属性变化。
    *   **动态性:** 模拟由于池范围使用情况（竞争）和固有随机性（波动）引起的资源性能变化。可以与外部数据源集成以驱动这些动态。
    *   **属性来源:** 提供基础*属性*，供 Components 用于计算*指标*。

### 4. 典型交互流程 (简化)

1.  创建 `Workflow` 并将其分配给 `Agent`（由 `WorkflowManager` 管理）。
2.  `Agent` 的 `live()` 循环运行。它识别活动的 `Workflow` 目标。
3.  Agent 收集上下文（其状态、工作流详情、组件能力）并请求 `Task` 计划（可能通过 `MCPServer` -> LLM）。
4.  Agent 收到计划（`Task` 规范列表）。
5.  对于一个 Task，Agent 调用 `component.execute_task(task_instance)`。
6.  `Component` 从 `ResourceManager` -> `SpecificResourcePool` 请求必要的 `Resources`。
7.  `Pool` 创建/分配一个 `ResourceWrapper` 实例并返回它。Pool 可能根据当前竞争情况更新属性。
8.  `Component` 根据 `ResourceWrapper` 的 `attributes` 计算初始 `metrics`。
9.  `Component` 调用 `task.execute(metrics)`，运行 Task 逻辑。
10. 在 `task.execute()` 内部：
    *   Task 等待时间或指标更新（如果 `ResourceWrapper` 属性因池竞争/波动而改变，包装器通过回调通知 Component，Component 触发 `ComponentName.metric_changed`，唤醒 Task）。
    *   Task 更新其进度。
    *   Task 计算所需的 Agent `state` 变化 (`_get_task_specific_state_repr`)。
    *   Task 调用 `agent.update_states(...)` -> Agent 的 `state_changed` 事件触发。
    *   Task 可选地更新其 `TaskProof` -> 触发 `proof_updated` 事件。
11. `WorkflowStatusMachine`（监听带有特定条件的 `state_changed` 或 `proof_updated` 事件）可能会转换其内部状态。它触发 `Workflow.status_changed` 事件。
12. `WorkflowManager`（监听 `Workflow.status_changed`）可能会更新整体 `Workflow.status`（例如，更新为 COMPLETED）。
13. 当 `task.execute()` 完成时，它向 `Component` 返回结果。
14. `Component` 触发 `ComponentName.task_completed/failed`，将 `ResourceWrapper` 释放回 `Pool`。
15. `Agent` 收到 `agent.task_finished` 事件。
16. `Agent` 的 `live()` 循环继续，可能基于更新后的 Agent 状态和 Workflow 状态规划下一个 Task。

### 5. 数据流总结

*   **状态 (State):** 由 `Agent` 维护，主要由 `Task` 执行更新。由 `Workflow` 监控。用于 `Agent` 决策。
*   **属性 (Attributes):** `ResourceWrapper` 实例的基本属性。由 `SpecificResourcePool` 管理和更新（竞争、波动）。
*   **指标 (Metrics):** 从资源 `attributes` 派生出的性能特征。由 `Component` 计算，由 `Task` 消耗。
*   **事件 (Events):** 连接状态变化、任务生命周期、资源更新和工作流进展的主要通信机制。
*   **证明 (Proofs):** 由 `Task` 生成/更新的数据工件，可能由 `Workflow` 监控。

### 6. 可扩展性

通过创建以下核心组件的子类来添加新功能：
*   `Agent`: 用于不同类型的自治实体。
*   `Component`: 用于新能力。
*   `Task`: 用于新的具体行动。
*   `Workflow`: 用于新的高级目标和监控逻辑。
*   `SpecificResourcePool`: 用于新型动态资源。