## AirFogSim 组件开发者文档

本文档为开发人员提供指导，用于在 AirFogSim 框架内创建自定义 `Component` 子类。它解释了组件的角色、与 Agent 和 Task 的交互、资源管理以及实现所需的步骤。

### 1. `Component` 类概述

`Component` 表示附加到 `Agent` 的功能单元或能力。其主要职责是**执行**由其父 `Agent` 分配给它的 `Task` 对象。组件管理任务执行的生命周期，包括：

1.  **资源管理：** 确定资源需求、分配必要的仿真资源（如 CPU、内存、带宽或专用资源如空域），并在完成后释放它们。
2.  **任务逻辑执行：** 运行 `Task` 对象本身定义的核心逻辑（`task.execute(...)`）。
3.  **性能建模：** 基于所获得的资源和代理状态计算性能指标（例如，处理速度、能耗、移动速度）。
4.  **事件发送：** 在父 `Agent` 上触发带命名空间的事件，以表示任务进度（已开始、已完成、失败、取消）和性能指标的变化。
5.  **状态监控：** 跟踪指定的代理状态，并在这些状态变化时重新计算指标。

组件是被动的，因为它们不决定*执行什么*任务；它们只是执行由 `Agent` 给予它们的任务。

### 2. 核心概念

#### 2.1 组件声明与配置

组件定义了两个重要的类属性：
- **`PRODUCED_METRICS`**：此组件计算和报告的指标名称列表。
- **`MONITORED_STATES`**：组件监控变化的代理状态变量列表。

```python
class MovementComponent(Component):
    PRODUCED_METRICS = ['speed', 'energy_consumption', 'eta'] 
    MONITORED_STATES = ['position', 'battery_level', 'status']
    
    # 实现的其余部分...
```

#### 2.2 任务执行生命周期（`execute_task`, `_execute_task_wrapper`）

*   **启动：** `Agent` 调用组件的 `execute_task(task)` 方法。
*   **验证：** `execute_task` 首先检查它是否 `can_execute(task)`（通常通过匹配 `task.component_name` 和 `self.name`）。如果不能，它返回一个立即使任务失败的 SimPy 进程。
*   **包装进程：** 如果有效，`execute_task` 启动并返回一个由 `_execute_task_wrapper(task)` 方法管理的 SimPy 进程。这个包装器管理整个执行流程。
*   **包装器步骤：** `_execute_task_wrapper` 在 `try...finally` 块内执行以下序列：
    1.  触发 Agent 上的 `ComponentName.task_started` 事件。
    2.  通过调用 `self._allocate_task_resources(task)` 分配资源，该方法返回格式为 `[(resource_type, allocation_id), ...]` 的资源分配列表。
    3.  通过调用 `self._calculate_performance_metrics()` 计算初始性能指标。
    4.  注册为 agent 的 `state_changed` 事件的监听器，以监控相关状态变化。
    5.  使用初始指标触发 `ComponentName.metric_changed` 事件。
    6.  通过 yield `task.execute(self.env, initial_metrics)` 执行任务的特定逻辑。`Task` 对象负责使用提供的指标执行自己的行为。
    7.  一旦 `task.execute` 完成，确定最终的 `task.status`。
    8.  在 Agent 上触发适当的最终事件（`ComponentName.task_completed`、`ComponentName.task_failed` 或 `ComponentName.task_canceled`），包括来自 `task.execute` 的结果。
    9.  **清理（在 `finally` 中）：** 取消订阅状态变化事件，通过 `self._release_task_resources()` 释放资源，并清理内部组件状态（`active_tasks`、`task_processes`、`task_resource_allocations`）。
*   **错误处理：** 包装器捕获异常和 `simpy.Interrupt`，相应地更新任务状态（失败/取消），触发相应的事件，并确保资源被释放。

#### 2.3 资源管理（`get_resource_requirements`, `_allocate_task_resources`, `_release_task_resources`）

组件通过三阶段方法处理资源：

1. **资源需求定义：** `get_resource_requirements(task)` 方法根据任务属性和代理状态定义需要哪些资源。它返回资源需求字典的列表。

2. **资源分配：** `_allocate_task_resources(task)` 方法采用这些需求并与适当的资源管理器交互以获取资源。它返回格式为 `[(resource_type, allocation_id), ...]` 的元组列表。

3. **资源释放：** 当任务完成（或失败）时，`_release_task_resources(task_id, allocations)` 方法释放先前分配的资源。

#### 2.4 性能指标计算（`_calculate_performance_metrics`）

*   **目的：** 此方法基于代理的状态和分配的资源计算性能指标。
*   **调用时机：** 
    1. 在任务执行开始时建立基准性能。
    2. 每当被监控的代理状态发生变化时（通过 `_on_agent_state_changed` 回调）。
*   **输出：** 返回一个字典，其中键匹配 `PRODUCED_METRICS` 中声明的指标，值表示当前的性能值。

#### 2.5 事件系统

组件通过代理的事件系统发送事件，但使用带命名空间的事件名称：

*   **标准事件：** `task_started`、`task_completed`、`task_failed`、`task_canceled`、`metric_changed`
*   **自定义事件：** 由组件的 `supported_events` 参数定义（例如，`charging_started`、`charging_completed`）
*   **事件触发：** 使用 `self.trigger_event(event_name, event_value)`，它会自动在事件名前加上组件名称（例如，`"Charging.charging_started"`）

### 3. 实现自定义组件

按照以下步骤创建您自己的 `Component` 子类（以 `ChargingComponent` 为指导）：

1.  **继承 `Component`：**
    ```python
    from airfogsim.core.component import Component
    
    class MyComponent(Component):
        PRODUCED_METRICS = ['processing_rate', 'efficiency']
        MONITORED_STATES = ['status', 'custom_property']
        
        def __init__(self, env, agent, name=None, processing_factor=1.0, 
                     supported_events=None):
            events = ['process_started', 'process_completed']
            if supported_events:
                events.extend(supported_events)
            super().__init__(env, agent, name or "MyProcessor", events)
            
            self.processing_factor = processing_factor
            
            # 检查代理是否有所需的状态
            if not self.agent.has_state('status'):
                self.agent.set_state('status', 'idle')
    ```

2.  **实现 `get_resource_requirements`：**
    ```python
    def get_resource_requirements(self, task) -> List[Dict]:
        """确定处理任务所需的资源"""
        # 从任务属性中读取CPU需求
        required_cpu = task.properties.get('cpu_cores', 1)
        priority = task.properties.get('priority', 'normal')
        
        # 查找可用的CPU资源
        return [{
            'type': 'cpu',
            'cores': required_cpu,
            'priority': priority,
            'purpose': 'data_processing'
        }]
    ```

3.  **实现 `_allocate_task_resources`：**
    ```python
    def _allocate_task_resources(self, task) -> List[Tuple]:
        """为任务分配所需的CPU资源"""
        resource_allocations = []
        required_resources = self.get_resource_requirements(task)
        
        for resource_req in required_resources:
            if resource_req['type'] == 'cpu':
                cores = resource_req.get('cores', 1)
                priority = resource_req.get('priority', 'normal')
                
                # 分配CPU资源
                allocation_id, resource_id = self.env.cpu_manager.allocate_resource(
                    self.agent_id, requirements={
                        'cores': cores,
                        'priority': priority
                    }
                )
                
                if allocation_id:
                    resource_allocations.append(('cpu', allocation_id))
                    
                    # 触发处理开始事件
                    self.trigger_event('process_started', {
                        'time': self.env.now,
                        'cores': cores
                    })
                    
        return resource_allocations
    ```

4.  **实现 `_release_task_resources`：**
    ```python
    def _release_task_resources(self, task_id: str, allocations: List[Tuple]):
        """任务完成时释放所有资源"""
        # 触发自定义完成事件
        self.trigger_event('process_completed', {
            'time': self.env.now,
            'task_id': task_id
        })
        
        # 释放每个分配的资源
        for res_type, allocation_id in allocations:
            if res_type == 'cpu':
                self.env.cpu_manager.release_allocation(allocation_id)
    ```

5.  **实现 `_calculate_performance_metrics`：**
    ```python
    def _calculate_performance_metrics(self) -> Dict[str, Any]:
        """基于当前代理状态和分配的资源计算指标"""
        total_cpu_power = 0
        
        # 检查所有活动任务分配
        for task_id, allocations in self.task_resource_allocations.items():
            for res_type, alloc_id in allocations:
                if res_type == 'cpu':
                    # 从资源管理器获取分配详情
                    allocation = self.env.cpu_manager.get_allocation(alloc_id)
                    if allocation:
                        # 提取相关属性
                        cores = allocation.get('cores', 1)
                        total_cpu_power += cores * 100  # 示例计算
        
        # 如果需要，考虑代理状态
        agent_status = self.agent.get_state('status', 'idle')
        efficiency_factor = 1.0 if agent_status == 'active' else 0.5
        
        # 计算最终指标
        processing_rate = total_cpu_power * self.processing_factor * efficiency_factor
        
        return {
            'processing_rate': processing_rate,
            'efficiency': efficiency_factor
        }
    ```

### 4. 关键要点和最佳实践

*   **重点：** 组件使用资源执行任务。它们不做规划。
*   **必须实现的方法：** `get_resource_requirements`、`_allocate_task_resources`、`_release_task_resources` 和 `_calculate_performance_metrics` 定义了组件的行为。
*   **包装器模式：** `_execute_task_wrapper` 处理复杂的生命周期（资源分配/释放、事件、错误处理）。除非您有非常特殊的需求，否则不需要修改它。
*   **状态集成：** 使用 `MONITORED_STATES` 在相关代理状态变化时自动重新计算指标。
*   **事件命名空间：** 组件发出的事件以其名称为前缀（例如，`"Charging.charging_started"`）。
*   **资源结构：** 将资源管理组织为三步过程：定义需求、分配资源、释放资源。
*   **任务交互：** 组件通过 `task.execute()` 向 `Task` 提供性能指标。`Task` 对象包含实际的执行逻辑。

通过正确实现这些方法，您的自定义组件将无缝集成到 AirFogSim 的 agent-component-task 执行模型中。