## AeroAgentSim 工作流-代理-任务框架文档

本文档解释了 AeroAgentSim 中的工作流-代理-任务框架，重点关注 `Workflow` 和 `WorkflowStatusMachine` 类，以及它们如何通过可重用组件的组合来实现复杂任务场景的建模。

### 1. 概述

AeroAgentSim 的核心是工作流-代理-任务框架，它通过可重用组件的组合实现复杂任务场景的建模。任务（Tasks）代表原子执行单元，而工作流（Workflows）定义更高级别的过程、目标或操作序列。

*   **`Workflow`:** 代表更高级别目标或过程的主要对象。它持有整体状态（待处理、运行中、已完成等），引用所有者 `Agent`，并包含通过 `WorkflowPropertyTemplate` 定义的特定于工作流的属性或目标。它定义了目标*是什么*并管理其整体生命周期。

*   **`WorkflowStatusMachine`:** 每个 Workflow 实例都封装了一个专用状态机，由 WorkflowStatusMachine 类表示。该状态机管理工作流通过各个阶段（例如，'idle'、'picking_up'、'transporting'）的进展。它监听特定事件（使用各种 `Trigger` 类型）并根据预定义规则在内部状态之间转换。它定义了工作流*如何*基于事件在内部进展。

*   **`Agent`:** 自主决策实体，拥有组件，执行任务，参与工作流。代理维护内部状态，并根据其状态、分配的工作流和环境感知做出决策。

*   **`Task`:** 封装特定动作逻辑的原子执行单元。任务定义了工作如何执行，需要什么资源，消耗什么指标，以及产生什么代理状态。

**关键概念:** 这个模型的一个关键方面是状态转换不是预定义的序列，而是由仿真触发器动态驱动的。这些触发器主动监控仿真环境中的特定条件，只有在满足条件时才允许从一个状态转换到下一个状态。

**关系:** `Workflow` 对象*拥有一个* `WorkflowStatusMachine`。`Workflow` 的整体状态（例如，`WorkflowStatus.RUNNING`）由 `Workflow` 自身管理（通常由状态机触发），而 `WorkflowStatusMachine` 通过对细粒度仿真事件的反应来管理详细的内部进展。

### 2. `WorkflowStatusMachine` 类

这个类实现了工作流的事件驱动状态转换逻辑。

#### 2.1 核心概念

*   **状态:** 机器跟踪其 `current_status`（表示内部状态的字符串，例如，`'waiting_for_drone'`、`'processing_data'`）。
*   **转换 (`add_transition`):** 核心逻辑通过添加转换来定义。转换指定：
    *   `state`: 此转换有效的状态（字符串或字符串的列表/元组）。`"*"` 作为通配符，对任何状态都有效。
    *   `next_status`: 如果触发器激活，机器应该转换*到*的内部状态。
    *   **触发器规范:** 必须提供以下之一来定义转换*何时*发生：
        *   `agent_state` (Dict): `StateTrigger` 的配置（例如，`{'agent_id': ..., 'state_key': ..., 'operator': ..., 'target_value': ...}`）。
        *   `time_trigger` (Dict): `TimeTrigger` 的配置（例如，`{'interval': ..., 'trigger_time': ...}`）。
        *   `event_trigger` (Dict): `EventTrigger` 的配置（例如，`{'source_id': ..., 'event_name': ..., 'value_key': ..., 'operator': ..., 'target_value': ...}`）。
        *   `trigger` (Trigger): 预配置的 `Trigger` 实例（最高优先级）。
    *   `callback` (Optional[Callable]): 触发器激活时*在*状态转换*之前*执行的函数。回调接收触发器上下文（`event_data`）。
    *   `description` (Optional[str]): 转换的人类可读描述。
*   **事件监控 (`_monitor_events`):** 当机器处于活动状态（调用了 `start()`）时，此 SimPy 进程运行：
    1.  使用 `_get_current_transitions` 确定从 `current_status` 开始的有效转换。
    2.  **停用**所有先前活动的触发器（`_deactivate_all_triggers`）。
    3.  **激活**与*当前*状态的有效转换相关联的触发器。每个触发器的回调设置为 `_on_trigger_activated`。
    4.  **等待（`yield self.env.timeout(float('inf'))`）**无限期地直到被中断。
    5.  当触发器的回调（`_on_trigger_activated`）成功改变状态时，进程被中断。
*   **状态变更 (`_on_trigger_activated`, `_change_status`):**
    *   当活动触发器触发时，调用 `_on_trigger_activated`。它检查机器是否仍处于有效状态。
    *   如果有效，它调用 `_change_status`，传递 `next_status` 和有关触发事件的详细信息。
    *   `_change_status` 更新机器的内部 `current_status`。
    *   **至关重要的是，`_change_status` 通过调用 `self.workflow._trigger_status_changed(...)` 通知父 `Workflow` 对象。** 这允许 `Workflow`（和监听的管理器）对内部状态进展做出反应。
    *   如果状态成功变更，`_on_trigger_activated` 中断 `_monitor_events` 进程以处理新状态。
*   **起始状态 (`set_start_transition`):** 定义机器在 `Workflow` 的整体状态变为 `RUNNING` *之后*进入的初始内部状态。
*   **终止状态:** 像 `'completed'`、`'failed'`、`'canceled'` 这样的状态会停止监控进程。

#### 2.2 使用

开发者通常通过 `Workflow` 子类*间接*与 `WorkflowStatusMachine` 交互，主要在 `_setup_transitions` 方法中通过调用 `self.status_machine.add_transition(...)` 来实现。

### 3. `Workflow` 基类

这个类代表整体工作流目标并协调 `WorkflowStatusMachine`。它使用 `WorkflowMeta` 元类来处理属性模板继承。

#### 3.1 核心概念

*   **元类 (`WorkflowMeta`):** 处理类层次结构中 `WorkflowPropertyTemplate` 定义的聚合。允许在元类 `__new__` 方法中使用 `mcs.register_template` 注册模板。
*   **属性模板 (`WorkflowPropertyTemplate`, `register_property_template`, `get_property_templates`):** 定义工作流初始化期间传递的 `properties` 字典的预期结构、类型和验证规则。确保一致性并提供文档。使用 `@classmethod register_property_template` 装饰器或 `WorkflowMeta.register_template` 进行定义。
*   **整体状态 (`self.status`):** 使用 `WorkflowStatus` 枚举（`PENDING`、`RUNNING`、`COMPLETED`、`FAILED`、`CANCELED`）跟踪高级状态。
*   **状态机 (`self.status_machine`):** 持有驱动内部进展的 `WorkflowStatusMachine` 实例。
*   **初始化 (`__init__`):** 设置 ID、名称、所有者、可选超时。根据注册的模板验证提供的 `properties`（`_validate_properties`）。使用指定的 `initial_status`（默认为 'idle'）创建 `WorkflowStatusMachine`。调用抽象方法 `_setup_transitions`。自动注册工作流级别事件（'sm_status_changed'、'workflow_status_changed'）。还自动注册 'task_priority' 和 'task_preemptive' 的属性模板。
*   **转换设置 (`_setup_transitions`) - 抽象方法:**
    *   **这是子类必须实现的主要方法。**
    *   在此方法中，您通过向 `self.status_machine` 添加转换来定义工作流的逻辑。
    *   转换通常使用基于 `self.owner` Agent 状态（`agent_state`）、仿真时间（`time_trigger`）或特定事件（`event_trigger`）的触发器。
*   **启动 (`start`):**
    *   当工作流应该开始执行时，由外部（通常是 `WorkflowManager`）调用。
    *   将 `Workflow.status` 从 `PENDING` 更改为 `RUNNING`。
    *   触发 `workflow_status_changed` 事件。
    *   调用 `self.status_machine.start()` 激活事件监控进程。
*   **信号内部变更 (`_trigger_status_changed`):**
    *   当*其*内部状态变化时，由 `WorkflowStatusMachine` 调用的**内部**方法。
    *   此方法首先使用 `update_status_from_state_machine` 根据状态机的新状态更新工作流的整体状态（`self.status`）。
    *   然后它触发 `sm_status_changed` 事件，通知状态机的内部状态变化。此事件包括旧的和新的内部 SM 状态以及有关触发事件的详细信息。
*   **状态管理 (`update_status_from_state_machine`):**
    *   自动将状态机状态映射到相应的 `WorkflowStatus` 枚举值：
        * 状态机状态 `'completed'` → `WorkflowStatus.COMPLETED`
        * 状态机状态 `'failed'` → `WorkflowStatus.FAILED`
        * 状态机状态 `'canceled'` → `WorkflowStatus.CANCELED`
        * 其他状态通常保持当前工作流状态
    *   当达到终止状态时，更新 `self.status`、`self.end_time` 和 `self.completion_reason`。
    *   `completion_reason` 基于事件详细信息或基于状态机状态的默认消息设置。
    *   当工作流的整体状态变化时，它触发 `workflow_status_changed` 事件，通知外部监听器（如 `WorkflowManager`）。
    *   如果工作流状态变为 `RUNNING` 并且指定了所有者，它还会在所有者代理上触发 'workflow_assigned' 事件，通知其关于分配。
*   **重置功能 (`reset`):**
    *   将工作流重置为其初始 `PENDING` 状态。
    *   停止当前状态机进程并使用原始初始状态创建新的 `WorkflowStatusMachine` 实例。
    *   通过再次调用 `_setup_transitions` 重新建立所有转换，以设置状态机逻辑。
    *   清除 start_time、end_time 和 completion_reason。
    *   触发带有原因 'workflow_reset' 的 'workflow_status_changed' 事件。
    *   允许在完成或失败后重新启动工作流。
*   **超时 (`_timeout_monitor`):** 一个可选进程，如果工作流在指定时间内没有达到终止状态机状态，则自动将其标记为失败。
*   **详细信息 (`get_details`):** 子类应该重写此方法，提供有关工作流参数或当前目标上下文的特定信息（对 LLM 规划或 UI 显示有用）。
*   **建议任务 (`get_current_suggested_task`):** 子类可以重写此方法，提供基于当前状态机状态代理应该执行的任务的字典描述。这有助于代理决策。

#### 3.2 实现自定义工作流子类

1.  **继承:** `class MyWorkflow(Workflow): ...`（如果在元类中定义模板，可以选择指定继承自 `WorkflowMeta` 的元类）。
2.  **注册属性模板（可选）:** 使用类上的 `@classmethod register_property_template` 装饰器或自定义元类中的 `WorkflowMeta.register_template` 定义预期的 `properties`。
    ```python
    @Workflow.register_property_template('target_location', tuple, True, description="目标 3D 坐标")
    @Workflow.register_property_template('speed_limit', float, False, validator=lambda s: s > 0)
    class MyNavigationWorkflow(Workflow):
        # ...
    ```
3.  **实现 `__init__`（可选但常见）:**
    *   调用 `super().__init__(...)`。
    *   存储此工作流类型所需的任何特定属性（例如，目标位置、数据 ID），通常从经过验证的 `self.properties` 字典中访问。
    ```python
    class InspectionWorkflow(Workflow): # 假设元类处理模板
        def __init__(self, env, name, owner, initial_status='waiting', **kwargs):
            super().__init__(env, name, owner, initial_status=initial_status, **kwargs)
            # 属性由元类/基础初始化验证
            self.inspection_points = self.properties.get('inspection_points', [])
            self.current_point_index = 0
            # ... 初始化的其余部分
    ```
4.  **实现 `_setup_transitions`（必需）:**
    *   使用 `self.status_machine.add_transition(...)` 定义状态机逻辑。
    *   使用各种触发器选项（`agent_state`、`event_trigger`、`time_trigger`）监听相关的仿真事件。
    *   使用回调处理触发器激活时*在*状态变更*之前*需要的操作。
    ```python
    def _setup_transitions(self):
        sm = self.status_machine
        agent_id = self.owner.id
        battery_threshold = self.properties.get('battery_threshold', 20)

        sm.set_start_transition('monitoring') # RUNNING 后的初始状态

        # 示例：基于 Agent 状态的转换（低电量）
        sm.add_transition(
            state='monitoring',
            next_status='needs_charge',
            agent_state={
                'agent_id': agent_id,
                'state_key': 'battery_level',
                'operator': TriggerOperator.LESS_THAN,
                'target_value': battery_threshold
            },
            description="电池电量低于阈值"
        )

        # 示例：基于事件的转换（任务完成）
        # 假设任务在代理上触发 'charge_task_completed'
        sm.add_transition(
            state='charging',
            next_status='completed',
            event_trigger={
                'source_id': agent_id, # 由代理触发的事件
                'event_name': 'charge_task_completed', # 自定义事件名称
                # 可选检查事件数据：
                # 'value_key': 'result.status',
                # 'operator': TriggerOperator.EQUALS,
                # 'target_value': 'SUCCESS'
            },
            description="充电任务成功完成"
        )

        # 示例：基于时间的转换
        sm.add_transition(
            state='waiting_confirmation',
            next_status='failed',
            time_trigger={
                'interval': 60 # 60 秒后超时
            },
            description="确认超时"
        )

        # 示例：通配符转换用于失败（例如，代理状态变为 'ERROR'）
        sm.add_transition(
            state='*', # 从任何状态
            next_status='failed',
            agent_state={
                 'agent_id': agent_id,
                 'state_key': 'status',
                 'operator': TriggerOperator.EQUALS,
                 'target_value': 'ERROR'
            },
            description="代理进入 ERROR 状态"
        )
    ```
5.  **实现 `get_details`（可选）:**
    *   返回包含有关工作流目标的有用上下文的字典。
    ```python
    def get_details(self):
        details = super().get_details()
        details.update({
            'goal_type': 'inspection',
            'total_points': len(self.inspection_points),
            'current_target_index': self.current_point_index, # 需要基于 SM 状态更新的逻辑
            'points': self.inspection_points
        })
        return details
    ```
6.  **实现 `get_current_suggested_task`（可选）:**
    *   返回基于 `self.status_machine.state` 描述代理应执行的任务的字典。
    ```python
    def get_current_suggested_task(self):
        if not self.owner or self.status != WorkflowStatus.RUNNING: return None
        current_sm_state = self.status_machine.state

        if current_sm_state == 'needs_charge':
            # 查找最近的充电站（示例逻辑）
            station = self.env.resource_manager.find_nearest('charging_station', self.owner.get_state('position'))
            if station:
                return {
                    'component': 'MoveTo', # 组件名称
                    'task_class': 'MoveToTask', # 任务类名称（字符串）
                    'task_name': f'移动到充电站 {station.id}',
                    'workflow_id': self.id,
                    'properties': {'target_position': station.location},
                    'target_state': {'position': station.location} # 可选的任务后预期代理状态
                }
        elif current_sm_state == 'charging':
             return {
                 'component': 'ChargingComponent',
                 'task_class': 'ChargeBatteryTask',
                 'task_name': '充电电池',
                 'workflow_id': self.id,
                 'properties': {'target_level': self.properties.get('target_charge_level', 95)},
                 'target_state': {'battery_level': self.properties.get('target_charge_level', 95)}
             }
        # ... 其他状态
        return None # 当前状态没有建议
    ```

### 4. 关键要点和最佳实践

*   `Workflow` 代表高级目标并管理整体状态；`WorkflowStatusMachine` 基于触发器和事件驱动内部的逐步进展。
*   子类**必须**实现 `_setup_transitions` 以使用 `add_transition` 定义状态机逻辑。
*   为每个转换使用适当的触发器类型（`agent_state`、`event_trigger`、`time_trigger` 或自定义 `trigger`）。
*   对特定于工作流的参数使用由 `WorkflowPropertyTemplate` 验证的 `properties`。
*   实现 `get_details` 和 `get_current_suggested_task` 以更好地与代理决策（尤其是 LLM）和监控集成。
*   `WorkflowStatusMachine` 的内部状态（`current_status`）与 `Workflow` 的整体状态（`status`）不同。工作流根据状态机的进展更新其状态。
*   监听 `workflow_status_changed`（用于整体状态）或 `sm_status_changed`（用于内部 SM 状态变化）事件进行监控。这些事件在工作流初始化期间自动注册。
*   `reset()` 方法允许重用或重新启动工作流。
*   保持转换逻辑清晰且集中。对与触发器激活直接相关的操作使用回调，但复杂的逻辑最好由 Agent 根据新状态或建议的任务来处理。
*   工作流自动注册 'task_priority' 和 'task_preemptive' 属性模板，用于控制工作流建议的任务的执行行为。

### 5. 图表生成

可以使用 PlantUML 或 Mermaid 图表可视化工作流，以帮助理解和调试工作流逻辑：

*   `workflow.to_uml_activity_diagram()`: 生成 PlantUML 活动图字符串。
    * 图表显示工作流状态机中定义的所有状态和转换。
    * 终止状态（completed、failed、canceled）用停止节点表示。
    * 转换标有其描述和触发器类型。
    * 通配符转换（适用于任何状态的转换）单独显示，带有注释。

*   `workflow.to_mermaid_diagram()`: 生成 Mermaid 状态图字符串。
    * 与 UML 图类似，但使用 Mermaid 语法以便与 Markdown 文档集成。
    * 显示状态、转换，并包含通配符转换的注释。
    * 终止状态连接到结束节点。

使用示例：
```python
# 生成并保存 UML 图
uml_diagram = workflow.to_uml_activity_diagram()
with open('workflow_diagram.puml', 'w') as f:
    f.write(uml_diagram)

# 生成并保存 Mermaid 图
mermaid_diagram = workflow.to_mermaid_diagram()
with open('workflow_diagram.md', 'w') as f:
    f.write(mermaid_diagram)
```

这些图表对于具有许多状态和转换的复杂工作流特别有用，因为它们提供了工作流逻辑的可视化表示。
