# AirFogSim 触发器系统指南

AirFogSim 触发器系统提供了一种灵活的方式来响应模拟中的各种条件和事件。触发器是驱动 `WorkflowStatusMachine` 转换和自动化 Agent 行为的基础组件。本指南解释了如何定义和使用不同类型的触发器。

## 1. 核心概念

*   **触发器 (Trigger):** 一个监控特定条件（事件发生、状态变化、时间流逝）并在条件满足时执行已注册回调函数的对象。
*   **激活/停用 (Activation/Deactivation):** 触发器必须被 `activate()` 才能开始监控。它们可以被 `deactivate()` 以停止监控。触发器在触发一次后会自动停用，以防止在同一模拟步骤内立即重新触发，除非它们是 `CompositeTrigger` 的一部分或设计为周期性触发（如基于间隔的 `TimeTrigger`）。工作流通常在进入新状态时重新激活必要的触发器。
*   **回调 (Callbacks):** 通过 `add_callback()` 添加的函数，在触发器触发时执行。回调函数会收到一个包含触发事件详细信息的上下文词典。
*   **上下文 (Context):** 传递给回调函数的字典，包含 `trigger_id`、`trigger_name`、`trigger_type`、`time` 等信息，以及特定于类型的详细信息（例如 `event_value`、`new_state`）。

## 2. 触发器类型

AirFogSim 提供了几种内置的触发器类型：

### 2.1 `EventTrigger`

*   **目的:** 响应通过 `env.event_registry` 发布的特定命名事件。
*   **关键参数:**
    *   `source_id` (str): 预期发布事件的实体的 ID。
    *   `event_name` (str): 要监听的事件的名称。
    *   `value_key` (Optional[str]): 用于从事件数据字典中提取特定值的点分隔路径（例如 `'data.position'`）。如果为 `None`，则使用整个事件数据字典进行比较。
    *   `operator` (Optional[TriggerOperator]): 要使用的比较运算符（例如 `EQUALS`, `LESS_THAN`, `CONTAINS`, `CUSTOM`）。如果为 `None`，则只要事件发生，触发器就会触发，无论其值如何。
    *   `target_value` (Any): 用于与提取的事件数据进行比较的值。如果 `operator` 是 `CUSTOM`，则此值应为可调用函数 `(value) -> bool`。
*   **上下文键:** `source_id`, `event_name`, `event_value`, `value_key`, `operator`, `target_value`。

**示例:** 当 `drone_1` 发出 `task_completed` 事件并且事件数据中的 `result.status` 键等于 `'SUCCESS'` 时触发。

```python
from airfogsim.core.trigger import EventTrigger
from airfogsim.core.enums import TriggerOperator

task_success_trigger = EventTrigger(
    env,
    source_id="drone_1",
    event_name="task_completed",
    value_key="result.status", # 检查嵌套键
    operator=TriggerOperator.EQUALS,
    target_value="SUCCESS",
    name="drone1_task_success"
)

def on_task_success(context):
    print(f"时间 {context['time']}: 无人机 1 任务成功！事件数据: {context['event_value']}")

task_success_trigger.add_callback(on_task_success)
task_success_trigger.activate()
```

### 2.2 `StateTrigger`

*   **目的:** 监控 `Agent` 特定状态变量的变化。
*   **关键参数:**
    *   `agent_id` (str): 其状态被监控的 Agent 的 ID。
    *   `state_key` (str): 要监控的状态变量的名称（必须存在于 Agent 的状态模板中）。
    *   `operator` (TriggerOperator): 比较运算符。
    *   `target_value` (Any): 用于与状态进行比较的值。如果 `operator` 是 `CUSTOM`，则此值应为可调用函数 `(value) -> bool`。
*   **行为:** 监听 Agent 的 `state_changed` 事件。当指定的 `state_key` 发生变化时，它使用 `operator` 将*新*值与 `target_value` 进行比较。
*   **上下文键:** `agent_id`, `state_key`, `old_value`, `new_value`, `operator`, `target_value`。

**示例:** 当 `drone_1` 的 `battery_level` 低于 20 时触发。

```python
from airfogsim.core.trigger import StateTrigger
from airfogsim.core.enums import TriggerOperator

low_battery_trigger = StateTrigger(
    env,
    agent_id="drone_1",
    state_key="battery_level",
    operator=TriggerOperator.LESS_THAN,
    target_value=20,
    name="drone1_low_battery"
)

def on_low_battery(context):
    print(f"时间 {context['time']}: 无人机 1 低电量警报！电量: {context['new_value']:.1f}%")

low_battery_trigger.add_callback(on_low_battery)
low_battery_trigger.activate()
```

### 2.3 `TimeTrigger`

*   **目的:** 基于模拟时间激活。
*   **关键参数 (选择一个):**
    *   `trigger_time` (float): 在指定的绝对模拟时间激活一次。
    *   `interval` (float): 激活后按指定间隔重复激活。
    *   `cron_expr` (str): *目前简化:* 解释为重复触发的间隔（旨在将来支持完整的 cron）。
*   **上下文键:** `trigger_mode` ('one_time', 'interval', 'cron')。

**示例:** 每 60 个模拟时间单位触发一次。

```python
from airfogsim.core.trigger import TimeTrigger

periodic_trigger = TimeTrigger(
    env,
    interval=60,
    name="hourly_check"
)

def periodic_check(context):
    print(f"时间 {context['time']}: 执行周期性检查。")

periodic_trigger.add_callback(periodic_check)
periodic_trigger.activate()
```

### 2.4 `CompositeTrigger`

*   **目的:** 使用逻辑 AND 或 OR 组合多个触发器。
*   **关键参数:**
    *   `triggers` (List[Trigger]): 要组合的触发器实例列表。
    *   `operator` (TriggerOperator.AND | TriggerOperator.OR): 用于组合触发器的逻辑运算符。
*   **行为:**
    *   **AND:** 仅当自上次复合触发器激活或重置以来*所有*子触发器都已触发时才触发。
    *   **OR:** 当*任何*子触发器触发时触发。
*   **激活/停用:** 激活/停用复合触发器也会激活/停用其所有子触发器。
*   **状态重置:** 复合触发器触发后，哪些子触发器已触发的内部状态将被重置。
*   **上下文键:**
    *   **AND:** `subtriggers` (Dict[str, bool] 显示哪些子触发器已触发)。
    *   **OR:** `subtrigger_id` (触发的子触发器的 ID), `subtrigger_context` (来自子触发器的上下文)。

**示例:** 如果 `drone_1` 电池电量低 (`StateTrigger`) 并且发生了特定的 `task_failed` 事件 (`EventTrigger`)，则触发。

```python
from airfogsim.core.trigger import CompositeTrigger, StateTrigger, EventTrigger
from airfogsim.core.enums import TriggerOperator

# 假设 low_battery_trigger 已如上定义
task_fail_trigger = EventTrigger(
    env,
    source_id="drone_1",
    event_name="task_failed",
    name="drone1_task_fail"
)

critical_condition_trigger = CompositeTrigger(
    env,
    triggers=[low_battery_trigger, task_fail_trigger],
    operator=TriggerOperator.AND,
    name="drone1_critical_condition"
)

def on_critical_condition(context):
    print(f"时间 {context['time']}: 紧急情况 - 无人机 1 电池电量低且任务失败！")
    # context['subtriggers'] 将显示两个触发器都为 True

critical_condition_trigger.add_callback(on_critical_condition)
critical_condition_trigger.activate()
```

## 3. 触发器运算符

以下运算符 (`airfogsim.core.enums.TriggerOperator`) 可用于 `EventTrigger` 和 `StateTrigger`：

*   `EQUALS`: 等于 (`==`)
*   `NOT_EQUALS`: 不等于 (`!=`)
*   `GREATER_THAN`: 大于 (`>`)
*   `LESS_THAN`: 小于 (`<`)
*   `GREATER_EQUAL`: 大于或等于 (`>=`)
*   `LESS_EQUAL`: 小于或等于 (`<=`)
*   `CONTAINS`: 检查 `target_value` 是否在事件/状态值中（例如 `item in list`）。
*   `NOT_CONTAINS`: 检查 `target_value` 是否不在事件/状态值中。
*   `CUSTOM`: 使用作为 `target_value` 提供的自定义函数。该函数接收提取的值并应返回 `True` 或 `False`。
*   `AND` / `OR`: 仅用于 `CompositeTrigger`。

## 4. 在工作流中使用触发器

触发器的主要用例是在 `WorkflowStatusMachine` 内定义转换。`add_transition` 方法简化了触发器的创建：

```python
from airfogsim.core import Workflow
from airfogsim.core.enums import TriggerOperator, WorkflowStatus

class MyWorkflow(Workflow):
    # ... (init, property templates 等)

    def _setup_transitions(self):
        sm = self.status_machine
        agent_id = self.owner.id

        sm.set_start_transition('waiting_for_task')

        # 基于 Agent 状态变化的转换
        sm.add_transition(
            state='waiting_for_task',
            next_status='processing',
            agent_state={ # 内部创建 StateTrigger
                'agent_id': agent_id,
                'state_key': 'current_task_status',
                'operator': TriggerOperator.EQUALS,
                'target_value': 'RECEIVED'
            },
            description="Agent 收到了任务"
        )

        # 基于特定事件的转换
        sm.add_transition(
            state='processing',
            next_status='completed',
            event_trigger={ # 内部创建 EventTrigger
                'source_id': agent_id,
                'event_name': 'processing_finished',
                'value_key': 'result_code', # 检查事件数据
                'operator': TriggerOperator.EQUALS,
                'target_value': 0 # 成功代码
            },
            description="处理成功完成"
        )

        # 基于时间的转换 (超时)
        sm.add_transition(
            state='processing',
            next_status='failed',
            time_trigger={ # 内部创建 TimeTrigger
                'interval': 120 # 如果处理时间超过 120 秒则失败
            },
            description="处理超时"
        )

        # 使用预定义触发器实例的转换
        # custom_trigger = SomeCustomTrigger(...)
        # sm.add_transition(
        #     state='some_state',
        #     next_status='other_state',
        #     trigger=custom_trigger,
        #     description="满足自定义条件"
        # )
```

## 5. 最佳实践

1.  **描述性名称:** 为触发器提供有意义的名称，以便于调试。
2.  **具体条件:** 对 `EventTrigger` 和 `StateTrigger` 使用 `value_key` 和适当的 `operator`，以避免因不相关的更改而触发。
3.  **谨慎使用 `CUSTOM`:** 为了清晰起见，优先使用内置运算符。对于无法用其他方式表达的复杂逻辑，使用 `CUSTOM`。确保自定义函数健壮。
4.  **管理激活:** 确保在需要时激活触发器（例如，当工作流进入某个状态时），并在不再相关时停用（通常由工作流状态机自动处理）。
5.  **轻量级回调:** 保持触发器回调快速且非阻塞。复杂的操作通常应由 Agent 或工作流根据回调触发的状态更改来启动。
6.  **复合触发器:** 对于复杂的 AND/OR 条件，使用 `CompositeTrigger`，而不是在回调或自定义函数中深度嵌套逻辑。