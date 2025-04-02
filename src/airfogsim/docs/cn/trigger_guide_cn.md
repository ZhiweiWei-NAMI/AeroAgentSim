# 触发器系统指南

AirFogSim触发器系统提供了一种灵活的方式来响应模拟中的事件和状态变化。本指南将解释如何有效地使用触发器系统。

## 触发器类型

AirFogSim支持几种类型的触发器：

1. **事件触发器（Event Triggers）**：响应模拟实体发出的特定事件
2. **状态触发器（State Triggers）**：监控代理状态变化
3. **时间触发器（Time Triggers）**：在特定时间或间隔激活
4. **组合触发器（Composite Triggers）**：使用逻辑运算符组合多个触发器

## 基本用法

### 创建简单的事件触发器

```python
from airfogsim.core.trigger import EventTrigger
from airfogsim.core.enums import TriggerOperator

# 创建一个在无人机电池电量低于20%时激活的触发器
trigger = EventTrigger(
    env,                          # 模拟环境
    source_id="drone_1",          # 发出事件的实体
    event_name="state_changed",   # 要监控的事件
    value_key="battery_level",    # 事件数据中要检查的键
    operator=TriggerOperator.LESS_THAN,  # 比较运算符
    target_value=20,              # 比较的目标值
    name="low_battery_trigger"    # 触发器名称（可选）
)

# 添加触发器激活时执行的回调函数
trigger.add_callback(lambda ctx: print(f"电池电量警告: {ctx['event_value']}%"))

# 激活触发器
trigger.activate()
```

### 创建时间触发器

```python
from airfogsim.core.trigger import TimeTrigger

# 创建一个每10个模拟时间单位激活一次的触发器
trigger = TimeTrigger(
    env,
    interval=10,
    name="periodic_trigger"
)

# 添加回调
trigger.add_callback(lambda ctx: print(f"时间: {env.now} - 周期性事件触发"))

# 激活触发器
trigger.activate()
```

### 创建状态触发器

```python
from airfogsim.core.trigger import StateTrigger
from airfogsim.core.enums import TriggerOperator

# 创建一个在代理位置变化时激活的触发器
trigger = StateTrigger(
    env,
    agent_id="drone_1",
    state_key="position",
    operator=TriggerOperator.NOT_EQUALS,
    target_value=None,  # 任何位置变化都会触发
    name="position_change_trigger"
)

# 添加回调
trigger.add_callback(lambda ctx: print(f"位置变更为: {ctx['new_value']}"))

# 激活触发器
trigger.activate()
```

### 创建组合触发器

```python
from airfogsim.core.trigger import CompositeTrigger, EventTrigger, StateTrigger
from airfogsim.core.enums import TriggerOperator

# 创建单独的触发器
battery_trigger = EventTrigger(
    env, "drone_1", "state_changed", "battery_level", 
    TriggerOperator.LESS_THAN, 10
)

position_trigger = StateTrigger(
    env, "drone_1", "position", 
    TriggerOperator.EQUALS, [0, 0, 0]
)

# 使用AND运算符组合触发器（两个条件都必须满足）
composite = CompositeTrigger(
    env,
    triggers=[battery_trigger, position_trigger],
    operator=TriggerOperator.AND,
    name="low_battery_at_home_trigger"
)

# 添加回调
composite.add_callback(lambda ctx: print("无人机在家且电池电量低"))

# 激活组合触发器
composite.activate()
```

## 触发器运算符

以下运算符可用于比较值：

- `TriggerOperator.EQUALS`：等于
- `TriggerOperator.NOT_EQUALS`：不等于
- `TriggerOperator.GREATER_THAN`：大于
- `TriggerOperator.LESS_THAN`：小于
- `TriggerOperator.GREATER_EQUAL`：大于等于
- `TriggerOperator.LESS_EQUAL`：小于等于
- `TriggerOperator.CONTAINS`：包含（用于集合）
- `TriggerOperator.NOT_CONTAINS`：不包含（用于集合）
- `TriggerOperator.AND`：逻辑与（用于组合触发器）
- `TriggerOperator.OR`：逻辑或（用于组合触发器）
- `TriggerOperator.CUSTOM`：自定义函数（高级用法）

## 在工作流中使用触发器

AirFogSim中的工作流使用触发器来管理状态转换。以下是使用触发器设置工作流的示例：

```python
from airfogsim.core import Workflow
from airfogsim.core.enums import TriggerOperator

class MyWorkflow(Workflow):
    def _setup_transitions(self):
        # 设置初始状态
        self.status_machine.set_start_transition('waiting')
        
        # 基于代理状态添加转换
        self.status_machine.add_transition(
            'waiting',           # 当前状态
            'moving',            # 下一个状态
            agent_state={        # 代理状态触发器配置
                'agent_id': self.owner.id,
                'state_key': 'is_moving',
                'operator': TriggerOperator.EQUALS,
                'target_value': True
            }
        )
        
        # 基于时间添加转换
        self.status_machine.add_transition(
            'moving',
            'timeout',
            time_trigger={
                'interval': 60  # 60个时间单位后转换
            }
        )
        
        # 基于事件添加转换
        self.status_machine.add_transition(
            'moving',
            'completed',
            event_trigger={
                'source_id': self.owner.id,
                'event_name': 'arrived',
                'value_key': 'location',
                'operator': TriggerOperator.EQUALS,
                'target_value': 'destination'
            }
        )
```

## 高级用法

### 自定义运算符

对于复杂条件，可以使用`CUSTOM`运算符和自定义函数：

```python
from airfogsim.core.trigger import EventTrigger
from airfogsim.core.enums import TriggerOperator

# 创建带有自定义条件函数的触发器
trigger = EventTrigger(
    env,
    source_id="drone_1",
    event_name="position_updated",
    value_key="position",
    operator=TriggerOperator.CUSTOM,
    target_value=lambda pos: pos[0]**2 + pos[1]**2 < 100  # 当位置在半径为10的圆内时激活
)
```

### 工作流可卸载性分析

触发器系统使工作流能够分析它们是否可以卸载到其他代理：

```python
# 检查工作流是否可以卸载
offload_analysis = workflow.analyze_offloadability()

if offload_analysis['offloadable']:
    print(f"工作流可以卸载。可卸载状态: {offload_analysis['states']}")
else:
    print("工作流不能卸载")
```

## 最佳实践

1. **使用描述性名称**：给触发器有意义的名称，使调试更容易
2. **停用未使用的触发器**：当不再需要触发器时，调用`trigger.deactivate()`
3. **保持回调轻量级**：触发器回调应该快速且避免阻塞操作
4. **使用组合触发器**：对于复杂条件，使用组合触发器而不是复杂的自定义函数
5. **在回调中处理异常**：在回调代码中使用try-except块以防止崩溃

## 示例：巡检工作流

以下是使用触发器系统的完整巡检工作流示例：

```python
from airfogsim.core import Workflow
from airfogsim.core.enums import TriggerOperator

class InspectionWorkflow(Workflow):
    def _setup_transitions(self):
        # 设置初始状态
        self.status_machine.set_start_transition('inspecting_point_1')
        
        # 对于每个巡检点
        for i, point in enumerate(self.inspection_points):
            current_state = f'inspecting_point_{i+1}'
            
            # 确定下一个状态
            if i+1 < len(self.inspection_points):
                next_state = f'inspecting_point_{i+2}'
            else:
                next_state = 'completed'
            
            # 使用事件触发器添加转换
            self.status_machine.add_transition(
                current_state, 
                next_state,
                event_trigger={
                    'source_id': self.proof_id,
                    'event_name': 'proof_updated',
                    'value_key': 'data.position',
                    'operator': TriggerOperator.CUSTOM,
                    'target_value': lambda position, point=point: 
                        all([abs(position[i] - point[i]) < 1e-6 for i in range(3)])
                }
            )
