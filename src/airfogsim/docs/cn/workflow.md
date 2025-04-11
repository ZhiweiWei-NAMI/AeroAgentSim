## AirFogSim 工作流和状态机开发者文档

本文档解释了 AirFogSim 中的 `Workflow` 和 `WorkflowStatusMachine` 类，指导开发者如何基于仿真事件（特别是 Agent 状态变化和 Task 结果）实现自定义工作流逻辑。

### 1. 概述

AirFogSim 中的工作流代表 `Agent` 可能承担的更高级别的目标、过程或动作序列。它们由一个专用的状态机 (`WorkflowStatusMachine`) 驱动，该状态机对模拟中发生的事件做出反应。

*   **`Workflow`:** 代表目标的主要对象。它持有整体状态（待定、运行中、已完成等），引用所有者 `Agent`，并包含通过 `WorkflowPropertyTemplate` 定义的特定于工作流的属性或目标。它定义了目标*是什么*并管理其整体生命周期。
*   **`WorkflowStatusMachine`:** 包含在 `Workflow` 中的引擎。它管理工作流进展的内部状态（例如 `moving_to_target`, `processing_data`）。它监听特定事件（使用各种 `Trigger` 类型）并根据预定义的规则在内部状态之间转换。它定义了工作流*如何*基于事件在内部进展。

**关系:** 一个 `Workflow` 对象*拥有一个* `WorkflowStatusMachine`。`Workflow` 的整体状态（例如 `WorkflowStatus.RUNNING`）由 `Workflow` 本身管理（通常由状态机触发），而 `WorkflowStatusMachine` 通过响应细粒度的仿真事件来管理详细的内部进展。

### 2. `WorkflowStatusMachine` 类

此类实现了工作流的事件驱动状态转换逻辑。

#### 2.1 核心概念

*   **状态:** 机器跟踪其 `current_status`（表示内部状态的字符串，例如 `'waiting_for_drone'`, `'processing_data'`）。
*   **转换 (`add_transition`):** 核心逻辑通过添加转换来定义。转换指定：
    *   `state`: 此转换有效的状态（字符串或字符串列表/元组）。`"*"` 作为通配符，从任何状态都有效。
    *   `next_status`: 如果触发器激活，机器应转换*到*的内部状态。
    *   **触发器规范:** 必须提供以下之一来定义转换*何时*发生：
        *   `agent_state` (Dict): `StateTrigger` 的配置（例如 `{'agent_id': ..., 'state_key': ..., 'operator': ..., 'target_value': ...}`）。
        *   `time_trigger` (Dict): `TimeTrigger` 的配置（例如 `{'interval': ..., 'trigger_time': ...}`）。
        *   `event_trigger` (Dict): `EventTrigger` 的配置（例如 `{'source_id': ..., 'event_name': ..., 'value_key': ..., 'operator': ..., 'target_value': ...}`）。
        *   `trigger` (Trigger): 一个预先配置的 `Trigger` 实例（最高优先级）。
    *   `callback` (Optional[Callable]): 在触发器激活*且在*状态转换之前执行的函数。回调接收触发器上下文 (`event_data`)。
    *   `description` (Optional[str]): 转换的人类可读描述。
*   **事件监控 (`_monitor_events`):** 当机器处于活动状态（调用 `start()`）时，此 SimPy 进程运行：
    1.  使用 `_get_current_transitions` 确定 `current_status` 的有效转换。
    2.  **停用**所有先前活动的触发器 (`_deactivate_all_triggers`)。
    3.  **激活**与*当前*状态的有效转换相关联的触发器。每个触发器的回调都设置为 `_on_trigger_activated`。
    4.  **无限期等待 (`yield self.env.timeout(float('inf'))`)** 直到被中断。
    5.  当触发器的回调 (`_on_trigger_activated`) 成功更改状态时，该进程被中断。
*   **状态更改 (`_on_trigger_activated`, `_change_status`):**
    *   当活动触发器触发时调用 `_on_trigger_activated`。它检查机器是否仍处于有效状态。
    *   如果有效，则调用 `_change_status`，传递 `next_status` 和有关触发事件的详细信息。
    *   `_change_status` 更新机器的内部 `current_status`。
    *   **关键：`_change_status` 通过调用 `self.workflow._trigger_status_changed(...)` 通知父 `Workflow` 对象。** 这允许 `Workflow`（和监听管理器）对内部状态进展做出反应。
    *   如果状态成功更改，`_on_trigger_activated` 会中断 `_monitor_events` 进程以处理新状态。
*   **开始状态 (`set_start_transition`):** 定义在 `Workflow` 的整体状态变为 `RUNNING` *之后*机器进入的初始内部状态。
*   **终端状态:** 像 `'completed'`, `'failed'`, `'canceled'` 这样的状态会停止监控过程。

#### 2.2 用法

开发者通常通过 `Workflow` 子类*间接*与 `WorkflowStatusMachine` 交互，主要是在 `_setup_transitions` 方法内通过调用 `self.status_machine.add_transition(...)`。

### 3. `Workflow` 基类

此类代表整体工作流目标并协调 `WorkflowStatusMachine`。它使用 `WorkflowMeta` 元类来处理属性模板继承。

#### 3.1 核心概念

*   **元类 (`WorkflowMeta`):** 处理跨类层次结构的 `WorkflowPropertyTemplate` 定义的聚合。允许在元类 `__new__` 方法中使用 `mcs.register_template` 注册模板。
*   **属性模板 (`WorkflowPropertyTemplate`, `register_property_template`, `get_property_templates`):** 定义在工作流初始化期间传递的 `properties` 字典的预期结构、类型和验证规则。确保一致性并提供文档。使用 `@classmethod register_property_template` 装饰器或 `WorkflowMeta.register_template` 进行定义。
*   **整体状态 (`self.status`):** 使用 `WorkflowStatus` 枚举 (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELED`) 跟踪高级别状态。
*   **状态机 (`self.status_machine`):** 持有驱动内部进展的 `WorkflowStatusMachine` 实例。
*   **初始化 (`__init__`):** 设置 ID、名称、所有者、可选超时。根据注册的模板验证提供的 `properties` (`_validate_properties`)。创建 `WorkflowStatusMachine`。调用抽象的 `_setup_transitions`。注册工作流级别的事件（如 `sm_status_changed`, `workflow_status_changed`）。
*   **转换设置 (`_setup_transitions`) - 抽象方法:**
    *   **这是子类必须实现的主要方法。**
    *   在此方法内部，您通过向 `self.status_machine` 添加转换来定义工作流的逻辑。
    *   转换通常使用基于 `self.owner` Agent 状态 (`agent_state`)、模拟时间 (`time_trigger`) 或特定事件 (`event_trigger`) 的触发器。
*   **启动 (`start`):**
    *   当工作流应开始执行时，由外部（通常是 `WorkflowManager`）调用。
    *   将 `Workflow.status` 从 `PENDING` 更改为 `RUNNING`。
    *   触发 `workflow_status_changed` 事件。
    *   调用 `self.status_machine.start()` 以激活事件监控过程。
*   **发出内部更改信号 (`_trigger_status_changed`):**
    *   当*其*内部状态更改时，由 `WorkflowStatusMachine` 调用的**内部**方法。
    *   此方法首先使用 `update_status_from_state_machine` 根据状态机的新状态更新工作流的整体状态 (`self.status`)。
    *   然后，它触发 `sm_status_changed` 事件以通知状态机的内部状态更改。此事件包括旧的和新的内部 SM 状态以及有关触发事件的详细信息。
*   **状态管理 (`update_status_from_state_machine`):**
    *   自动将状态机状态（如 `'completed'`, `'failed'`, `'canceled'`）映射到相应的 `WorkflowStatus` 枚举值。
    *   更新 `self.status`、`self.end_time` 和 `self.completion_reason`。
    *   当工作流的整体状态更改时，它触发 `workflow_status_changed` 事件以通知外部监听器（如 `WorkflowManager`）。
*   **重置功能 (`reset`):**
    *   将工作流重置为其初始 `PENDING` 状态。
    *   停止当前状态机进程并创建一个新的 `WorkflowStatusMachine` 实例。
    *   通过调用 `_setup_transitions` 重新建立所有转换。
    *   允许在完成或失败后重新启动工作流。
*   **超时 (`_timeout_monitor`):** 一个可选进程，如果工作流在指定持续时间内未达到终端状态机状态，则自动使工作流失败。
*   **详细信息 (`get_details`):** 子类应覆盖此方法以提供有关工作流参数或当前目标上下文的特定信息（对于 LLM 规划或 UI 显示很有用）。
*   **建议任务 (`get_current_suggested_task`):** 子类可以覆盖此方法以提供一个字典，描述 Agent 根据当前状态机状态可能应执行的任务。这有助于 Agent 决策。

#### 3.2 实现自定义 Workflow 子类

1.  **继承:** `class MyWorkflow(Workflow): ...`（如果要在元类中定义模板，则可以选择指定继承自 `WorkflowMeta` 的元类）。
2.  **注册属性模板 (可选):** 在类上使用 `@classmethod register_property_template` 装饰器或在自定义元类中使用 `WorkflowMeta.register_template` 来定义预期的 `properties`。
    ```python
    @Workflow.register_property_template('target_location', tuple, True, description="目标 3D 坐标")
    @Workflow.register_property_template('speed_limit', float, False, validator=lambda s: s > 0)
    class MyNavigationWorkflow(Workflow):
        # ...
    ```
3.  **实现 `__init__` (可选但常见):**
    *   调用 `super().__init__(...)`。
    *   存储此工作流类型所需的任何特定属性（例如，目标位置、数据 ID），通常从经过验证的 `self.properties` 字典中访问。
    ```python
    class InspectionWorkflow(Workflow): # 假设元类处理模板
        def __init__(self, env, name, owner, **kwargs):
            super().__init__(env, name, owner, **kwargs)
            # 属性由元类/基础 init 验证
            self.inspection_points = self.properties.get('inspection_points', [])
            self.current_point_index = 0
            # ... init 的其余部分
    ```
4.  **实现 `_setup_transitions` (强制):**
    *   使用 `self.status_machine.add_transition(...)` 定义状态机逻辑。
    *   使用各种触发器选项（`agent_state`, `event_trigger`, `time_trigger`）来监听相关的模拟事件。
    *   对触发器激活*且在*状态更改之前所需的操作使用回调。
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
        # 假设任务在 Agent 上触发 'charge_task_completed'
        sm.add_transition(
            state='charging',
            next_status='completed',
            event_trigger={
                'source_id': agent_id, # 由 Agent 触发的事件
                'event_name': 'charge_task_completed', # 自定义事件名称
                # 可选地检查事件数据:
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

        # 示例：失败的通配符转换（例如 Agent 状态变为 'ERROR'）
        sm.add_transition(
            state='*', # 从任何状态
            next_status='failed',
            agent_state={
                 'agent_id': agent_id,
                 'state_key': 'status',
                 'operator': TriggerOperator.EQUALS,
                 'target_value': 'ERROR'
            },
            description="Agent 进入 ERROR 状态"
        )
    ```
5.  **实现 `get_details` (可选):**
    *   返回一个包含有关工作流目标的有用上下文的字典。
    ```python
    def get_details(self):
        details = super().get_details()
        details.update({
            'goal_type': 'inspection',
            'total_points': len(self.inspection_points),
            'current_target_index': self.current_point_index, # 需要逻辑来根据 SM 状态更新
            'points': self.inspection_points
        })
        return details
    ```
6.  **实现 `get_current_suggested_task` (可选):**
    *   返回一个字典，描述 Agent 根据 `self.status_machine.state` 应执行的任务。
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
                    'target_state': {'position': station.location} # 可选的任务后预期 Agent 状态
                }
        elif current_sm_state == 'charging':
             return {
                 'component': 'ChargingComponent',
                 'task_class': 'ChargeBatteryTask',
                 'task_name': '给电池充电',
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
*   为每个转换使用适当的触发器类型（`agent_state`, `event_trigger`, `time_trigger` 或自定义 `trigger`）。
*   对特定于工作流的参数使用由 `WorkflowPropertyTemplate` 验证的 `properties`。
*   实现 `get_details` 和 `get_current_suggested_task` 以更好地与 Agent 决策（尤其是 LLM）和监控集成。
*   `WorkflowStatusMachine` 的内部状态 (`current_status`) 与 `Workflow` 的整体状态 (`status`) 不同。工作流根据状态机的进展更新其状态。
*   监听 `workflow_status_changed`（用于整体状态）或 `sm_status_changed`（用于内部 SM 状态更改）事件以进行监控。
*   `reset()` 方法允许重用或重新启动工作流。
*   保持转换逻辑清晰且集中。对与触发器激活直接相关的操作使用回调，但复杂的逻辑最好由 Agent 根据新状态或建议的任务来处理。

### 5. 图表生成

可以使用 PlantUML 或 Mermaid 图表可视化工作流：

*   `workflow.to_uml_activity_diagram()`: 生成 PlantUML 活动图字符串。
*   `workflow.to_mermaid_diagram()`: 生成 Mermaid 状态图字符串。

这些图表有助于理解和调试工作流逻辑。