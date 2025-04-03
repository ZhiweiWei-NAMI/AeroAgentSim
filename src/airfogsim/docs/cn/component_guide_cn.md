
## AirFogSim 组件开发者文档

本文档为开发人员提供指导，用于在 AirFogSim 框架内创建自定义 `Component` 子类。它解释了组件的角色、与 Agent 和 Task 的交互、性能指标计算、事件处理以及实现所需的步骤。

### 1. `Component` 类概述

`Component` 表示附加到 `Agent` 的功能单元或能力。其主要职责是**执行**由其父 `Agent` 分配给它的 `Task` 对象。组件管理任务执行的生命周期，包括：

1.  **任务执行启动与监控：** 启动 `Task` 对象的执行，并监控其完成、失败或取消。
2.  **性能建模与报告：** 基于**代理的状态**计算性能指标（例如，处理速度、能耗、移动速度），并在任务开始或相关代理状态变化时报告这些指标。
3.  **事件发送：** 在父 `Agent` 上触发带命名空间的事件，以表示任务进度（已开始、已完成、失败、取消）和性能指标的变化。
4.  **状态监控：** 跟踪指定的代理状态，并在这些状态变化时重新计算性能指标。
5.  **(子类责任) 资源管理：** 如果任务执行需要特定的仿真资源（如 CPU、带宽、特定设备），**组件子类**需要负责实现资源的请求、分配和释放逻辑。基类不提供标准的资源管理框架。

组件是被动的，因为它们不决定*执行什么*任务；它们只是执行由 `Agent` 给予它们的任务。

### 2. 核心概念

#### 2.1 组件声明与配置

组件定义了两个重要的类属性：
- **`PRODUCED_METRICS`** (`List[str]`): 此组件计算和报告的性能指标名称列表。必须提供且格式正确。
- **`MONITORED_STATES`** (`List[str]`): 组件监控其变化的**代理状态**变量名称列表。如果为空列表，则不主动监控状态变化；如果提供了状态名称，则当这些状态在 `Agent` 上发生变化时，会触发组件重新计算性能指标。

```python
class MovementComponent(Component):
    PRODUCED_METRICS = ['speed', 'energy_consumption', 'eta']
    MONITORED_STATES = ['position', 'battery_level', 'status'] # 当这些状态变化时，会重新计算指标

    # 实现的其余部分...
```

#### 2.2 任务执行生命周期（`execute_task`, `_execute_task_wrapper`）

*   **启动：** `Agent` 调用组件的 `execute_task(task)` 方法。
*   **验证：** `execute_task` 首先检查它是否 `can_execute(task)`（通过匹配 `task.component_name` 和 `self.name`）。如果不能，它返回一个立即使任务失败 (`_fail_immediately`) 的 SimPy 进程。
*   **包装进程：** 如果有效，`execute_task` 启动并返回一个由 `_execute_task_wrapper(task)` 方法管理的 SimPy 进程。这个包装器管理整个执行流程。
*   **包装器步骤 (`_execute_task_wrapper`)：** 该方法在 `try...finally` 块内按顺序执行以下操作：
    1.  触发 Agent 上的 `ComponentName.task_started` 事件。
    2.  调用 `self._calculate_performance_metrics()` 计算**初始**性能指标。
    3.  验证 (`_validate_metrics`) 计算出的指标是否包含所有 `PRODUCED_METRICS` 中声明的键。
    4.  更新组件内部的 `self.current_metrics`。
    5.  使用初始指标触发 `ComponentName.metric_changed` 事件。
    6.  通过 `self.env.event_registry.subscribe` **注册**为父 `Agent` 的 `state_changed` 事件的监听器，以便在 `MONITORED_STATES` 中的状态发生变化时得到通知。
    7.  启动并等待 `task.execute(self.env, initial_metrics)` 完成。**任务对象 (`Task`) 负责执行其核心逻辑**，可以使用传递给它的初始性能指标。
    8.  `task.execute` 完成后，根据 `task.status` 确定任务的最终结果。
    9.  在 Agent 上触发适当的最终事件（`ComponentName.task_completed`、`ComponentName.task_failed` 或 `ComponentName.task_canceled`），将任务结果包含在事件数据中。
    10. 返回 `task.execute` 的结果。
*   **清理（在 `finally` 块中）：** 无论任务成功、失败还是被取消，都会执行以下操作：
    *   通过 `self.env.event_registry.unsubscribe` **取消**对 `Agent` 状态变化的监听。
    *   从组件的 `active_tasks` 和 `task_processes` 字典中移除该任务的引用。
*   **错误处理：** 包装器捕获 `simpy.Interrupt`（通常表示任务被外部取消）和一般 `Exception`。
    *   对于 `Interrupt`，它会尝试中断任务逻辑，将任务状态设置为 `CANCELED`，并触发 `task_canceled` 事件。
    *   对于其他异常，它将任务状态设置为 `FAILED`，并触发 `task_failed` 事件。
    *   在这两种情况下，都会确保执行清理步骤。

#### 2.3 性能指标计算（`_calculate_performance_metrics`）

*   **目的：** 此方法是**必须由子类实现**的抽象方法。它负责根据**当前代理的状态**（以及子类可能管理的任何资源信息）计算组件的性能指标。
*   **调用时机：**
    1.  在任务执行开始时（`_execute_task_wrapper` 内部），用于建立基准性能并传递给 `task.execute`。
    2.  每当被 `MONITORED_STATES` 中指定的代理状态发生变化时（由 `_on_agent_state_changed` 回调函数触发）。
*   **输出：** 必须返回一个字典 (`Dict[str, Any]`)，其键必须包含在类属性 `PRODUCED_METRICS` 中声明的所有指标名称。其值代表了这些指标的当前计算值。
*   **实现注意：** 子类实现此方法时，应使用 `self.agent.get_state(key)` 来获取所需的代理状态。如果涉及资源，子类需要自行管理如何获取资源状态信息（例如，直接查询资源管理器或维护内部状态）。

#### 2.4 事件系统

组件通过其父代理的事件系统发送事件，但使用带**命名空间**的事件名称，以组件名称作为前缀：

*   **标准事件（由 `_execute_task_wrapper` 触发）：**
    *   `task_started`: 任务执行开始。
    *   `task_completed`: 任务成功完成。
    *   `task_failed`: 任务执行失败。
    *   `task_canceled`: 任务被取消。
    *   `metric_changed`: 组件计算的性能指标发生变化（在任务开始时和监控的状态变化时触发）。
*   **自定义事件：** 可以在组件的 `__init__` 中通过 `supported_events` 参数定义，并在组件或其关联的任务逻辑中通过 `self.trigger_event` 触发。
*   **事件触发：** 使用 `self.trigger_event(event_name, event_value)` 方法。该方法会自动在 `event_name` 前加上组件名称和点号（例如，调用 `self.trigger_event('charging_started', ...)` 会在代理上触发 `"ChargingComponent.charging_started"` 事件）。

### 3. 实现自定义组件

按照以下步骤创建您自己的 `Component` 子类（以一个假设的 `ProcessingComponent` 为例）：

1.  **继承 `Component` 并定义属性：**
    ```python
    from airfogsim.core.component import Component
    from airfogsim.core.enums import TaskStatus
    from typing import List, Dict, Any

    class ProcessingComponent(Component):
        # 定义此组件产生的指标
        PRODUCED_METRICS = ['processing_speed', 'queue_length']
        # 定义此组件关心的代理状态
        MONITORED_STATES = ['cpu_load', 'status']

        def __init__(self, env, agent, name: Optional[str] = None,
                     base_speed: float = 100.0, supported_events: List[str] = None):
            # 定义组件支持的自定义事件
            custom_events = ['processing_batch_start', 'processing_batch_end']
            if supported_events:
                custom_events.extend(supported_events)

            super().__init__(env, agent, name or "Processor", supported_events=custom_events)

            self.base_speed = base_speed
            self.current_queue_length = 0 # 组件内部状态示例

            # 确保代理有所需的状态 (如果需要默认值)
            if not self.agent.has_state('cpu_load'):
                self.agent.set_state('cpu_load', 0.0)
            if not self.agent.has_state('status'):
                self.agent.set_state('status', 'idle')
            print(f"ProcessingComponent initialized for agent {self.agent_id}")
    ```

2.  **实现 `_calculate_performance_metrics`：**
    ```python
    def _calculate_performance_metrics(self) -> Dict[str, Any]:
        """基于代理状态计算处理速度和队列长度。"""
        cpu_load = self.agent.get_state('cpu_load', 0.0)
        agent_status = self.agent.get_state('status', 'idle')

        # 示例逻辑：CPU负载越高，速度越慢；非活动状态速度减半
        speed_multiplier = max(0, 1.0 - cpu_load) # 0.0 to 1.0
        if agent_status != 'active':
            speed_multiplier *= 0.5

        current_speed = self.base_speed * speed_multiplier

        # 队列长度是组件内部维护的状态
        queue_len = self.current_queue_length

        # 必须返回包含所有 PRODUCED_METRICS 的字典
        metrics = {
            'processing_speed': current_speed,
            'queue_length': queue_len
        }
        # print(f"Time {self.env.now}: Calculating metrics for {self.name}: {metrics}")
        return metrics

    ```
    *注意：在这个例子中，我们没有显式地进行资源分配/释放。如果需要，比如要向 CPU 管理器请求资源，那么相关的逻辑需要开发者添加到组件或任务中。*

3.  **(可选) 添加特定于组件的方法：** 你可以添加其他方法来支持组件的功能，例如，更新内部状态（如队列长度）。这些方法可能会在关联的 `Task` 对象的 `execute` 方法中被调用。
    ```python
    def update_queue_length(self, change: int):
        """更新内部队列长度并可能触发指标重新计算（如果需要立即反映）"""
        self.current_queue_length += change
        self.current_queue_length = max(0, self.current_queue_length) # 确保非负
        # 注意：如果队列长度变化应立即反映在指标中，
        # 但没有相应的Agent状态变化触发它，
        # 可能需要手动触发一次指标更新。
        # current_metrics = self._calculate_performance_metrics()
        # self._validate_metrics(current_metrics)
        # self.current_metrics.update(current_metrics)
        # self.trigger_event('metric_changed', current_metrics)
    ```

### 4. 关键要点和最佳实践

*   **核心职责：** 组件的核心职责是执行任务、计算性能指标（基于代理状态）并通过事件进行通信。
*   **子类实现：** `_calculate_performance_metrics` 是**必须**由子类实现的抽象方法。
*   **资源管理：** **资源管理不是基类的职责**。如果组件需要与仿真资源（如 CPU、网络、传感器）交互，开发者必须在**子类**中实现资源的请求、分配、使用和释放逻辑。这可能涉及直接与环境中的资源管理器交互。
*   **包装器模式：** `_execute_task_wrapper` 处理任务生命周期、事件触发、状态监听和基本错误处理。通常**不需要**重写此方法，除非有非常高级的定制需求。
*   **状态驱动的指标：** 使用 `MONITORED_STATES` 可以让组件对代理状态的变化做出反应，自动更新性能指标。
*   **事件命名空间：** 组件发出的所有事件都以其名称为前缀（例如，`"Processor.task_started"`），有助于区分事件来源。
*   **任务交互：** 组件通过 `task.execute(env, initial_metrics)` 启动任务，并将初始计算的性能指标传递给任务。`Task` 对象包含实际执行工作的代码。

通过遵循这些指导原则并正确实现所需的方法（尤其是 `_calculate_performance_metrics` 和任何必要的资源管理逻辑），您的自定义组件将能有效地集成到 AirFogSim 的 agent-component-task 执行模型中。

---