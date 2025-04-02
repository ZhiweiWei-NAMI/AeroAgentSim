
## AirFogSim Workflow & State Machine 开发者文档

本文档解释了 AirFogSim 中的 `Workflow` (工作流) 和 `WorkflowStatusMachine` (工作流状态机) 类，指导开发者如何基于仿真事件（特别是 Agent 状态变化和 Task 结果）实现自定义工作流逻辑。

### 1. 概述

AirFogSim 中的工作流代表了 `Agent` 可能承担的更高级别的目标、过程或动作序列。它们由一个专门的状态机 (`WorkflowStatusMachine`) 驱动，该状态机对仿真中发生的事件做出反应。

*   **`Workflow` (工作流):** 代表目标的主要对象。它持有整体状态（待处理、运行中、已完成等），引用所有者 `Agent`，并包含特定于工作流的属性或目标。它定义了目标*是什么*。
*   **`WorkflowStatusMachine` (工作流状态机):** 包含在 `Workflow` 内部的引擎。它管理工作流进展的内部状态。它监听特定事件（如 Agent 状态更改、任务证明更新、组件事件），并根据预定义的规则在内部状态之间转换。它定义了工作流*如何*根据事件在内部进展。

**关系:** 一个 `Workflow` 对象*拥有一个* `WorkflowStatusMachine`。`Workflow` 的整体状态（例如 `WorkflowStatus.RUNNING`）由外部管理（通常由 `WorkflowManager` 管理），而 `WorkflowStatusMachine` 则通过响应细粒度的仿真事件来管理详细的内部进展（例如 `'inspecting_point_1'`, `'moving_to_target'`, `'awaiting_confirmation'`）。

### 2. `WorkflowStatusMachine` 类

此类为工作流实现了事件驱动的状态转换逻辑。

#### 2.1 核心概念

*   **状态 (States):** 状态机跟踪其 `current_status`（表示内部状态的字符串，例如 `'waiting_for_drone'`, `'processing_data'`）。
*   **转换 (`add_transition`):** 核心逻辑通过添加转换来定义。一个转换指定：
    *   `state`: 此转换有效的起始状态（字符串或字符串列表/元组）。`"*"` 作为通配符，从任何状态都有效。
    *   `source_id`: 预期触发事件的实体的 ID（例如，`self.workflow.owner.id` 用于代理事件，特定的 `Task.id`，甚至 `env.id`）。
    *   `event_name`: 要监听的事件名称（例如 `'state_changed'`, `'proof_updated'`, `f'{component_name}.task_completed'`）。
    *   `next_status`: 如果事件发生且满足条件，状态机应转换*到*的内部状态。
    *   `condition_func` (可选): 一个函数 `Callable[[Any], bool]`，它接收 `event_value` 作为输入，如果转换应继续则返回 `True`，否则返回 `False`。这允许进行细粒度控制（例如，检查某个 Agent 状态键是否更改为特定值）。
*   **事件监控 (`_monitor_events`):** 当状态机处于活动状态时（调用了 `start()`），此 SimPy 进程运行：
    1.  使用 `_get_current_transitions` 确定从 `current_status` 出发的有效转换。
    2.  使用 `env.event_registry.subscribe` **订阅**有效转换中指定的所有必需事件。
    3.  **等待 (`yield env.any_of(...)`)** 任何已订阅的事件触发。
    4.  当事件触发时，调用回调函数 `_on_event_triggered`。
    5.  在再次等待之前（或者如果状态发生变化），使用 `_clear_subscriptions` 清除旧的订阅。
*   **状态更改 (`_on_event_triggered`, `_change_status`):**
    *   `_on_event_triggered` 检查接收到的事件是否与当前状态的有效转换匹配，以及其 `condition_func` 是否通过。
    *   如果有效，则调用 `_change_status`。它更新状态机的内部 `current_status`。
    *   **关键是，`_change_status` 通过调用 `self.workflow._trigger_status_changed(...)` 来通知父 `Workflow` 对象。** 这允许 `Workflow`（以及监听的管理器）对内部状态的进展做出反应。
*   **起始状态 (`set_start_transition`):** 定义在 `Workflow` 的整体状态变为 `RUNNING` *之后*，状态机进入的初始内部状态。
*   **终止状态 (Terminal States):** 像 `'completed'`, `'failed'`, `'canceled'` 这样的状态会停止监控过程。

#### 2.2 用法

开发者通常*间接*地通过 `Workflow` 子类与 `WorkflowStatusMachine` 交互，主要是在 `_setup_transitions` 方法内部通过调用 `self.status_machine.add_transition(...)`。

### 3. `Workflow` 基类

此类代表整体工作流目标，并协调 `WorkflowStatusMachine`。

#### 3.1 核心概念

*   **整体状态 (`self.status`):** 使用 `WorkflowStatus` 枚举 (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELED`) 跟踪高级别状态。这通常由监听工作流 `status_changed` 事件的 `WorkflowManager` 更新。
*   **状态机 (`self.status_machine`):** 持有驱动内部进展的 `WorkflowStatusMachine` 实例。
*   **初始化 (`__init__`):** 设置 ID、名称、所有者、可选超时。创建 `WorkflowStatusMachine`。调用抽象方法 `_setup_transitions`。注册工作流级别的事件（如 `status_changed`）。
*   **转换设置 (`_setup_transitions`) - 抽象方法:**
    *   **这是子类必须实现的主要方法。**
    *   在此方法内部，您通过向 `self.status_machine` 添加转换来定义工作流的逻辑。
    *   转换通常监听与 `self.owner` Agent 相关的事件（例如 `self.owner.id`, `'state_changed'`）、任务证明 (`self.owner.id`, `'proof_updated'`) 或特定的任务/组件完成事件。
    *   条件函数 (`condition_func`) 在这里至关重要，用于精确检查事件的特定细节（例如，哪个状态键发生了变化，证明数据的内容）。
*   **启动 (`start`):**
    *   当工作流应开始执行时，由外部调用（通常是 `WorkflowManager`）。
    *   将 `Workflow.status` 从 `PENDING` 更改为 `RUNNING`。
    *   触发初始的 `status_changed` 事件。
    *   调用 `self.status_machine.start()` 来激活事件监控过程。
*   **通知内部更改 (`_trigger_status_changed`):**
    *   一个**内部**方法，当*其内部状态*发生变化时由 `WorkflowStatusMachine` 调用。
    *   此方法触发 `Workflow` 自己的公共 `status_changed` 事件。此事件包含旧的和新的*内部* SM 状态以及有关触发事件的详细信息。
    *   **关键是，外部监听器（如 `WorkflowManager`）订阅*这个* `Workflow.status_changed` 事件** 来更新整体 `Workflow.status`（例如，当 SM 达到 `'completed'` 状态时将其设置为 `COMPLETED`）。
*   **超时 (`_timeout_monitor`):** 一个可选进程，如果工作流未在指定时间内达到终止状态机状态，则自动将其标记为失败。
*   **详情 (`get_details`):** 子类可以覆盖此方法以提供有关工作流参数或当前目标上下文的特定信息（对于 LLM 规划很有用）。

#### 3.2 实现自定义工作流子类

1.  **继承:** `class MyWorkflow(Workflow): ...`
2.  **实现 `__init__` (可选但常用):**
    *   调用 `super().__init__(...)`。
    *   存储此工作流类型所需的任何特定属性（例如，目标位置、数据 ID），通常通过 `properties` 字典传递。
    ```python
    class InspectionWorkflow(Workflow):
        def __init__(self, env, name, owner, inspection_points: List[Tuple], **kwargs):
            properties = kwargs.pop('properties', {})
            properties['inspection_points'] = inspection_points # 存储特定目标
            super().__init__(env, name, owner, properties=properties, **kwargs)
            self.inspection_points = inspection_points
            self.current_point_index = 0
    ```
3.  **实现 `_setup_transitions` (强制):**
    *   使用 `self.status_machine.add_transition(...)` 定义状态机逻辑。
    *   监听相关事件，通常来自 `self.owner.id`。
    *   使用条件函数精确检查事件详情。
    ```python
    def _setup_transitions(self):
        sm = self.status_machine
        agent_id = self.owner.id
        proof_id = self.properties.get('proof_id') # 假设需要 proof_id

        sm.set_start_transition('moving_to_point_1') # RUNNING 后的初始状态

        # 示例：基于 Agent 位置从移动转换到巡检
        sm.add_transition(
            state='moving_to_point_1',
            source_id=agent_id,
            event_name='state_changed', # 监听任何代理状态变化
            next_status='inspecting_point_1',
            condition_func=lambda ev: (
                ev.get('key') == 'position' and
                self._is_at_point(ev.get('new_value'), 0) # 检查新位置是否靠近点 0
            )
        )

        # 示例：基于任务证明从巡检转换到移动到下一点
        sm.add_transition(
            state='inspecting_point_1',
            source_id=agent_id, # 代理触发证明更新
            event_name='proof_updated',
            next_status='moving_to_point_2',
            condition_func=lambda ev: (
                ev.get('proof_id') == proof_id and # 检查是否是正确的证明
                ev.get('data', {}).get('inspection_status') == 'complete' # 检查证明数据
            )
        )

        # 示例：转换到完成状态
        sm.add_transition(
            state='moving_to_final_point', # 示例最终移动状态
            source_id=agent_id,
            event_name='state_changed',
            next_status='completed', # 终止状态
            condition_func=lambda ev: (
                 ev.get('key') == 'position' and
                 self._is_at_point(ev.get('new_value'), -1) # 检查是否在最后一点
            )
        )

        # 示例：失败的通配符转换（例如，低电量）
        sm.add_transition(
            state='*', # 从任何状态
            source_id=agent_id,
            event_name='state_changed',
            next_status='failed', # 终止状态
            condition_func=lambda ev: (
                 ev.get('key') == 'battery_level' and ev.get('new_value', 100) < 5
            )
        )
    # 条件函数的辅助方法
    def _is_at_point(self, current_pos, point_index):
        if not current_pos: return False
        target_index = point_index if point_index >= 0 else len(self.inspection_points) - 1
        if 0 <= target_index < len(self.inspection_points):
            target_pos = self.inspection_points[target_index]
            # 简单的距离检查（实现距离计算）
            distance = calculate_distance(current_pos, target_pos)
            return distance < 1.0 # 容差
        return False

    # (需要定义 calculate_distance 辅助函数)
    ```
4.  **实现 `get_details` (可选):**
    *   返回一个包含有关工作流目标有用上下文的字典。
    ```python
    def get_details(self):
        details = super().get_details()
        details.update({
            'goal_type': 'inspection',
            'total_points': len(self.inspection_points),
            'current_target_index': self.current_point_index, # 需要根据 SM 状态更新此索引
            'points': self.inspection_points,
            'proof_id': self.properties.get('proof_id')
        })
        return details
    ```

### 4. 关键要点

*   `Workflow` 代表目标；`WorkflowStatusMachine` 基于事件驱动内部进展。
*   子类**必须**实现 `_setup_transitions` 以使用 `add_transition` 定义状态机逻辑。
*   转换监听特定事件（通常来自所有者 `Agent`）并使用条件函数检查事件详情。
*   `WorkflowStatusMachine` 的内部状态 (`current_status`) 与 `Workflow` 的整体状态 (`status`) 不同。
*   `Workflow.status_changed` 事件标志着*内部* SM 状态的变化，并被管理器用于更新*整体*工作流状态。
*   条件函数 (`condition_func`) 对于基于事件数据精确控制状态转换至关重要。