## AirFogSim 组件开发者文档

本文档为希望在 AirFogSim 框架内创建自定义 `Component` 子类的开发者提供指导。它解释了组件的角色、它们与代理和任务的交互、基于代理状态的性能指标计算以及实现所需的步骤。

### 1. `Component` 类概述

`Component` 代表附加到 `Agent` 的功能单元或能力。其主要职责是**执行**由其父 `Agent` 分配给它的 **`Task` 对象**。组件管理任务执行的生命周期，包括：

1.  **任务逻辑执行：** 运行 `Task` 对象本身定义的核心逻辑（`task.execute(...)`）。
2.  **性能建模：** 根据**父代理的当前状态**和组件属性计算性能指标（例如，处理速度、能源消耗、移动速度）。
3.  **代理状态监控：** 通过重新计算性能指标来响应相关代理状态（`MONITORED_STATES`）的变化。
4.  **事件发射：** 在父 `Agent` 上触发命名空间事件，以表示任务进度（开始、完成、失败、取消）和性能指标的变化（`metric_changed`）。
5.  **任务生命周期管理：** 协调任务的设置、执行和清理阶段。

组件在某种意义上是被动的，因为它们不决定*执行什么*任务；它们只是执行由 `Agent` 给予它们的任务，根据代理的状态调整其性能。资源获取和管理**不是**由基础 `Component` 包装器直接处理的；这个逻辑应该在特定的 `Component` 子类中实现，或者可能在 `Task` 逻辑本身中实现，通常受代理的状态或拥有的对象的影响。

### 2. 核心概念

#### 2.1 任务执行生命周期（`execute_task`，`_execute_task_wrapper`）

*   **启动：** `Agent` 调用组件的 `execute_task(task)` 方法。
*   **验证：** `execute_task` 首先检查它是否 `can_execute(task)`（通常通过匹配 `task.component_name` 到 `self.name`）。如果不能，它返回一个立即使任务失败的 SimPy 进程。它还检查任务是否已经在运行。
*   **包装器进程：** 如果有效，`execute_task` 启动并返回一个由 `_execute_task_wrapper(task)` 方法管理的 SimPy 进程。这个包装器协调整个执行流程。
*   **包装器步骤：** `_execute_task_wrapper` 在 `try...finally` 块内执行以下序列：
    1.  在代理上触发 `ComponentName.task_started` 事件。
    2.  调用 `self._calculate_performance_metrics()`（抽象方法）以根据当前代理状态获取初始性能值。根据 `PRODUCED_METRICS` 验证这些指标。更新 `self.current_metrics`。
    3.  触发带有初始指标的 `ComponentName.metric_changed` 事件。
    4.  **注册代理状态监听器：** 使用 `self.env.event_registry.subscribe` 订阅父代理的 `state_changed` 事件，将其链接到 `_on_agent_state_changed` 回调。
    5.  通过 yield `task.execute(self.env, initial_metrics)` 执行任务的特定逻辑。`Task` 对象使用这些指标来执行其工作。
    6.  一旦 `task.execute` 完成，确定最终的 `task.status`。
    7.  在代理上触发适当的最终事件（`ComponentName.task_completed`、`ComponentName.task_failed` 或 `ComponentName.task_canceled`），包括来自 `task.execute` 的结果。
    8.  **清理（在 `finally` 中）：**
        *   使用 `self.env.event_registry.unsubscribe` 取消订阅代理状态监听器。
        *   从组件的内部跟踪（`active_tasks`、`task_processes`）中移除任务。
*   **错误处理：** 包装器捕获 `simpy.Interrupt`（取消任务并触发 `task_canceled`）和其他 `Exception`（使任务失败并触发 `task_failed`），确保进行清理。

#### 2.2 性能指标（`PRODUCED_METRICS`，`_calculate_performance_metrics`，`current_metrics`）

*   **`PRODUCED_METRICS`（类变量）：** 一个**必需的**类级别字符串列表，定义了这个组件类型计算并提供给 `Task` 的性能指标的名称（例如，`['speed', 'energy_consumption']`）。用于验证和初始化 `current_metrics`。
*   **`_calculate_performance_metrics()`（抽象方法）：** 这个**抽象方法必须由子类实现**。
    *   **输入：** 隐式使用 `self.agent.get_state()` 或 `MONITORED_STATES` 中定义的特定代理状态，以及可能的组件 `self.properties`。
    *   **动作：** 根据相关代理状态和组件配置计算当前性能指标。
    *   **输出：** 返回一个字典，其中键是指标名称（必须存在于 `PRODUCED_METRICS` 中），值是计算的性能值。
*   **`current_metrics`（实例变量）：** 一个字典，保存 `PRODUCED_METRICS` 中定义的指标的最后计算值。
*   **`metric_changed` 事件：** 最初由 `_execute_task_wrapper` 触发，然后在计算的指标发生变化时由 `_on_agent_state_changed` 触发，向运行的 `Task` 和其他监听器提供更新的指标字典。
*   **`_validate_metrics(metrics)`：** 内部辅助函数，确保计算的指标与 `PRODUCED_METRICS` 匹配。

#### 2.3 代理状态监控（`MONITORED_STATES`，`_on_agent_state_changed`）

*   **`MONITORED_STATES`（类变量）：** 一个**可选的**类级别代理状态键（字符串）列表，这个组件特别关心。如果这里列出的状态发生变化，组件将重新计算其指标。如果为空或未定义，组件可能会对*任何*代理状态变化做出反应（取决于 `_on_agent_state_changed` 的实现）。基础实现检查变化的状态 `key` 是否在 `MONITORED_STATES` 中（如果 `MONITORED_STATES` 不为空）。
*   **`_validate_agent_states()`：** 在 `__init__` 期间调用，以确保 `MONITORED_STATES` 中列出的状态（不包括像 'object.state' 这样的复合键）实际上在代理的状态模板中定义。
*   **`_on_agent_state_changed(event_data)`：** 当父代理的 `state_changed` 事件发生时触发的回调函数。
    *   检查变化的状态键（`event_data['key']`）是否相关（即，如果列表已定义且非空，则在 `MONITORED_STATES` 中）。
    *   如果相关，它调用 `_calculate_performance_metrics()` 获取更新的指标。
    *   它将新指标与 `self.current_metrics` 进行比较。**如果任何指标值发生变化**，它更新 `self.current_metrics` 并触发带有新指标字典的 `ComponentName.metric_changed` 事件。这允许运行的 `Task` 根据代理的更新状态调整其行为。

#### 2.4 事件处理（`_register_component_events`，`trigger_event`）

*   **命名空间事件：** 组件在其父 `Agent` 上触发事件，但事件名称自动以组件的名称为命名空间（例如，`MobilityComponent.task_started`，`ComputeComponent.metric_changed`）。这由 `trigger_event` 处理。
*   **标准事件：** 基类通过 `_register_component_events` 自动注册常见事件：`task_started`，`task_completed`，`task_failed`，`task_canceled`，`state_changed`（通常*由* Task 触发，但组件*可以*触发它），`metric_changed`。
*   **自定义事件：** 子类可以在其 `__init__` 中定义额外的 `supported_events`，并使用 `self.trigger_event('my_custom_event', value)` 触发它们。
*   **触发：** 在组件内使用 `self.trigger_event('event_name', value)` 在代理上触发命名空间事件。

### 3. 实现自定义组件子类

按照以下步骤创建您自己的组件类型：

1.  **定义类：**
    *   继承自 `airfogsim.core.component.Component`。
    ```python
    from airfogsim.core.component import Component
    from typing import List, Dict, Any, Optional

    class MyCustomComponent(Component):
        # ... 实现 ...
    ```

2.  **定义 `PRODUCED_METRICS`（必需）：**
    *   将此组件计算的指标声明为类变量。
    ```python
    class MyCustomComponent(Component):
        PRODUCED_METRICS = ['processing_rate', 'latency']
        # ...
    ```

3.  **定义 `MONITORED_STATES`（可选但推荐）：**
    *   声明影响此组件指标的代理状态。
    ```python
    class MyComputeComponent(Component):
        PRODUCED_METRICS = ['processing_power']
        MONITORED_STATES = ['cpu_load', 'power_mode'] # 仅对这些状态变化做出反应
        # ...
    ```

4.  **实现 `__init__`：**
    *   调用 `super().__init__(env, agent, name, supported_events, properties)`。如果需要，提供默认名称。列出任何自定义事件。
    *   从 `properties` 或参数中存储任何组件特定的配置。
    *   初始化组件的任何内部状态。
    ```python
    def __init__(self, env, agent, name: Optional[str] = None, processing_factor: float = 1.0, custom_events: List[str] = None, properties=None):
        supported = ['custom_event_1'] + (custom_events or [])
        super().__init__(env, agent, name or "MyCustom", supported_events=supported, properties=properties)
        self.processing_factor = processing_factor
        # 如果需要，初始化其他自定义状态
    ```

5.  **实现 `_calculate_performance_metrics`（必需）：**
    *   使用 `self.agent.get_state('state_key', default_value)` 访问相关代理状态。
    *   使用组件属性（`self.properties`，`self.processing_factor` 等）。
    *   计算 `PRODUCED_METRICS` 中定义的性能指标。
    *   将结果作为字典返回。
    ```python
    def _calculate_performance_metrics(self) -> Dict[str, Any]:
        # 示例：根据代理的电源模式和组件因子计算
        power_mode = self.agent.get_state('power_mode', 'normal')
        base_rate = self.properties.get('base_processing_rate', 100)

        if power_mode == 'low':
            rate = base_rate * 0.5
            latency = 20
        elif power_mode == 'high':
            rate = base_rate * 1.5
            latency = 5
        else: # normal
            rate = base_rate
            latency = 10

        processing_rate = rate * self.processing_factor

        # 返回与 PRODUCED_METRICS 匹配的字典
        return {
            'processing_rate': processing_rate,
            'latency': latency
        }
    ```

6.  **（可选）重写 `can_execute` 或 `is_available`：** 如果默认行为（匹配名称，检查活动任务）不足，添加自定义逻辑。

7.  **（可选）添加自定义逻辑/事件：** 根据组件的特定功能需要，包含辅助方法或触发自定义事件。

### 4. 关键要点和最佳实践

*   **焦点：** 组件执行任务，根据父代理的状态调整其性能。它们充当代理的能力接口。
*   **抽象方法：** `_calculate_performance_metrics` 是**必需的**实现，定义了组件的性能如何从代理的状态和组件配置中派生。
*   **状态驱动的指标：** 性能指标主要由代理的状态（`MONITORED_STATES`）和组件属性驱动，而不是在基类中直接由获取的资源驱动。
*   **包装器模式：** `_execute_task_wrapper` 处理复杂的生命周期（事件、状态监听、任务执行、错误处理、清理）。子类主要关注 `_calculate_performance_metrics`。
*   **任务交互：** 组件通过 `task.execute()` 和 `metric_changed` 事件向 `Task` 提供性能指标。`Task` 对象包含消耗这些指标的实际执行逻辑。
*   **事件：** 使用命名空间事件（`self.trigger_event`）进行与代理和可能的其他监听器（如运行的任务）的通信。
*   **资源管理：** 显式资源获取/释放逻辑（例如，与管理器交互）**不是**基础 `Component` 包装器的一部分，需要在子类中实现或在 `Task` 逻辑中处理，通常由代理状态或拥有的对象触发。

通过正确实现这些方法，您的自定义组件将无缝集成到 AirFogSim 代理-组件-任务执行模型中，动态响应其父代理的状态。
