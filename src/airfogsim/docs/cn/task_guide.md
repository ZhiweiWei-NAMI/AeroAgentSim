## AirFogSim 任务开发者文档

本文档指导开发者在 AirFogSim 框架内创建自定义 `Task` 子类。它解释了 `Task` 对象作为工作流-代理-任务框架中的原子执行单元的角色，其由 `Component` 管理的执行生命周期，与代理状态的交互，以及性能指标。

### 1. 概述

*   **`Task`：** 代表封装特定动作逻辑的原子执行单元。任务定义了工作如何执行，需要什么资源，消耗什么指标，以及产生什么代理状态。任务由 `Agent` 启动，但*由* `Component` 执行。

*   **优先级和抢占：** 任务可以有优先级级别（例如，`TaskPriority.CRITICAL`、`TaskPriority.HIGH`）和抢占标志，允许系统模拟可以中断低优先级任务的紧急任务。

**工作流-代理-任务框架中的关系：**
`Workflow`（定义更高级别的目标）-> `Agent`（决定*什么*和*何时*）-> `Component`（提供*能力*和*资源*）-> `Task`（定义*如何*完成动作）。

### 2. `Task` 基类

`Task` 类是所有特定动作的抽象基类。子类定义实际行为。

#### 2.1 核心概念

*   **初始化（`__init__`）：**
    *   分配唯一 ID、名称，链接到代理、组件和可选的工作流 ID。
    *   存储 `target_state`（期望的结果）和 `properties`（任务特定参数，如持续时间、目标位置）。
    *   初始化状态（`PENDING`）、时间、结果等。
    *   如果提供，设置优先级和抢占标志，允许任务优先级排序和中断。
    *   **关键是，验证 `PRODUCED_STATES` 和 `NECESSARY_METRICS`（见下文）。** 它检查 `PRODUCED_STATES` 是否已定义并存在于代理的状态模板中，以及 `NECESSARY_METRICS` 是否已定义。
*   **执行上下文（`execute` 方法）：**
    *   `execute` 方法包含作为 **SimPy 生成器函数**的核心任务逻辑。
    *   **重要：** 此方法由 `Component` 在其 `_execute_task_wrapper` 中调用和运行。
    *   它从组件接收 `initial_metrics`。
    *   **执行循环：**
        1.  将状态设置为 `RUNNING`，记录 `start_time`。
        2.  进入 `while self.progress < 1.0` 循环。
        3.  计算 `estimate_remaining_time(self.current_metrics)`。
        4.  **等待（`yield`）**以下*最早*的事件：
            *   `completion_timeout`：等于估计剩余时间的时间流逝。
            *   `metric_change_event`：当其性能指标变化时*由拥有的组件*触发的事件（例如，`ComponentName.metric_changed`）。
            *   `visual_update_event`：（可选）用于视觉更新的全局事件。
        5.  如果发生 `metric_change_event`，使用事件数据中的新值更新 `self.current_metrics`。
        6.  调用 `self._update_task_state(self.current_metrics)`（抽象）以根据经过的时间和当前指标更新进度和内部状态。
        7.  更新 `self.last_update_time`。
        8.  触发任务特定的 `state_changed` 事件（`self.id`，'state_changed'），包含任务的当前状态表示（`_get_current_task_state_repr`）。
        9.  使用 `self.agent.update_states(self._get_task_specific_state_repr())` 更新**代理的状态**（抽象方法提供状态字典）。
        10. 循环继续，直到 `self.progress >= 1.0`。
    *   **完成/失败：** 根据结果调用 `self.complete()` 或 `self.fail()`。处理 `simpy.Interrupt`。
    *   返回最终的 `self.result` 字典。
*   **状态管理（`complete`、`fail`、`cancel`）：** 设置最终任务状态、结束时间、结果字典和失败原因的方法。这些方法还调用相应的 `_possessing_object_on_...` 钩子。
*   **拥有对象钩子（`_possessing_object_on_complete`、`_possessing_object_on_fail`、`_possessing_object_on_cancel`）：**
    *   这些方法在任务完成、失败或取消时自动调用。
    *   子类可以重写这些方法，实现管理代理拥有的对象的逻辑（例如，在完成或失败时释放充电站资源）。基础实现不做任何事情。

#### 2.2 必须的子类定义

创建 `Task` 子类时，您**必须**定义/实现以下内容：

1.  **类属性：**
    *   `NECESSARY_METRICS: List[str]`：此任务从执行 `Component` 的 `_calculate_performance_metrics` 输出中*需要*的指标名称（字符串）列表。基础 `__init__` 检查此列表是否已定义且非空。示例：`['speed', 'processing_power']`。
    *   `PRODUCED_STATES: List[str]`：此任务预期通过 `_get_task_specific_state_repr` 修改的*代理*状态变量名称（字符串）列表。基础 `__init__` 检查此列表是否已定义、非空，以及所有列出的状态键是否存在于 `Agent` 的状态模板（`agent.get_state_templates()`）中。示例：`['position', 'battery_level', 'status']`。

2.  **抽象方法实现：**
    *   `_update_task_state(self, performance_metrics: Dict)`：
        *   **输入：** 来自组件的当前性能指标字典。
        *   **动作：** 这是核心进度逻辑。根据 `performance_metrics` 和自 `self.last_update_time` 以来经过的时间（`self.env.now - self.last_update_time`）更新任务的内部状态（例如，覆盖的距离、处理的数据）。**关键是，更新 `self.progress`（必须在 0.0 和 1.0 之间）。**
        *   **示例（`MoveToTask`）：** 使用 `speed` 指标计算移动的距离，沿路径更新 `self.current_position`，并根据已行进距离与总距离的比例更新 `self.progress`。还根据移动的距离更新内部 `self.battery_level`。
    *   `estimate_remaining_time(self, performance_metrics: Dict) -> float`：
        *   **输入：** 来自组件的当前性能指标字典。
        *   **输出：** 给定*当前*指标，从*当前进度*完成任务所需的估计*剩余*时间。如果无法用当前指标完成，返回 `float('inf')`。如果已经完成，返回 `0`。
        *   **示例（`MoveToTask`）：** 根据剩余距离（`self.total_distance - self.distance_traveled`）和当前 `speed` 计算。
    *   `_get_task_specific_state_repr(self) -> Dict`：
        *   **输出：** 包含此任务在其当前进度下产生的*代理*状态更新的字典。键**必须**是 `PRODUCED_STATES` 的子集。
        *   **动作：** 此字典在 `execute` 循环期间传递给 `self.agent.update_states()`。
        *   **示例（`MoveToTask`）：** 返回 `{'position': self.current_position, 'direction': self.direction, 'distance_traveled': self.distance_traveled, 'altitude': ..., 'battery_level': self.battery_level}`。

#### 2.3 可选的重写和属性

*   `_get_current_task_state_repr(self) -> Dict`：重写以向任务本身触发的 `state_changed` 事件添加更多任务特定的详细信息。基础实现包括基本信息，如 ID、名称、状态和进度，以及来自 `_get_task_specific_state_repr` 的内容。
*   `_possessing_object_on_complete(self)`：重写以在任务成功完成时添加逻辑（例如，释放资源）。
*   `_possessing_object_on_fail(self)`：重写以在任务失败时添加逻辑（例如，释放资源）。
*   `_possessing_object_on_cancel(self)`：重写以在任务取消时添加逻辑（例如，释放资源）。

### 3. 实现自定义任务子类（示例：`MoveToTask`）

1.  **继承：** `class MyTask(Task): ...`
2.  **定义类属性：**
    ```python
    class ComputeDataTask(Task):
        NECESSARY_METRICS = ['processing_power'] # 需要来自组件的 CPU 功率指标
        PRODUCED_STATES = ['computation_load', 'result_data', 'status'] # 更新代理的负载，存储结果，并设置状态
    ```
3.  **实现 `__init__`：**
    ```python
    def __init__(self, env, agent, component_name, task_name, ..., properties=None):
        super().__init__(...)
        # 从属性中存储任务特定参数
        self.data_size = properties.get('data_size', 100) # 例如，MB
        self.complexity = properties.get('complexity', 1.0) # 影响时间的因素
        # 初始化内部任务状态变量
        self.processed_data = 0.0
        # 其他初始设置
    ```
4.  **实现 `estimate_remaining_time`：**
    ```python
    def estimate_remaining_time(self, performance_metrics: Dict) -> float:
        power = performance_metrics.get('processing_power', 0) # 例如，MIPS
        if power <= 0:
            return float('inf') # 没有功率无法完成

        total_ops = self.data_size * self.complexity * 1e6 # 示例计算
        ops_done = self.processed_data * self.complexity * 1e6
        remaining_ops = total_ops - ops_done

        if remaining_ops <= 0:
            return 0.0

        # 剩余时间 = 剩余操作 / 功率
        return remaining_ops / power
    ```
5.  **实现 `_update_task_state`：**
    ```python
    def _update_task_state(self, performance_metrics: Dict):
        power = performance_metrics.get('processing_power', 0)
        elapsed_time = self.env.now - self.last_update_time

        # 计算此步骤中完成的工作
        ops_done_this_step = power * elapsed_time
        data_processed_this_step = ops_done_this_step / (self.complexity * 1e6) # 反向计算
        self.processed_data += data_processed_this_step

        # 计算总进度
        total_ops = self.data_size * self.complexity * 1e6
        current_total_ops_done = self.processed_data * self.complexity * 1e6

        if total_ops > 0:
             self.progress = min(1.0, current_total_ops_done / total_ops)
        else:
             self.progress = 1.0 # 没有工作要做，立即完成
    ```
6.  **实现 `_get_task_specific_state_repr`：**
    ```python
    def _get_task_specific_state_repr(self) -> Dict:
        # 根据任务是否正在运行计算当前负载
        load = self.current_metrics.get('processing_power', 0) if self.status == TaskStatus.RUNNING else 0
        # 仅在完成时提供最终结果状态
        result = {'final_result': 'some_value'} if self.progress >= 1.0 else None

        # 返回与 PRODUCED_STATES 匹配的字典
        return {
            'computation_load': load,
            'result_data': result,
            'status': 'computing' if self.status == TaskStatus.RUNNING else self.agent.get_state('status') # 更新代理状态
        }
    ```
7.  **实现拥有对象钩子（可选）：**
    ```python
    def _possessing_object_on_complete(self):
        # 示例：如果持有，释放计算资源锁
        compute_resource = self.agent.get_possessing_object('my_compute_lock')
        if compute_resource:
            print(f"时间 {self.env.now}：任务 {self.id} 已完成，释放计算锁。")
            self.agent.remove_possessing_object('my_compute_lock')

    def _possessing_object_on_fail(self):
        # 示例：失败时也释放锁
        compute_resource = self.agent.get_possessing_object('my_compute_lock')
        if compute_resource:
            print(f"时间 {self.env.now}：任务 {self.id} 失败，释放计算锁。")
            self.agent.remove_possessing_object('my_compute_lock')
    ```

### 4. 任务优先级和抢占

AirFogSim 支持任务优先级排序和抢占，允许模拟可以中断低优先级任务的紧急任务：

*   **优先级级别：** 可以使用 `TaskPriority` 枚举为任务分配优先级级别：
    *   `TaskPriority.CRITICAL`：最高优先级，用于紧急或安全关键任务
    *   `TaskPriority.HIGH`：重要任务，应该迅速执行
    *   `TaskPriority.NORMAL`：大多数任务的默认优先级
    *   `TaskPriority.LOW`：后台或非紧急任务

*   **抢占：** 任务可以被标记为抢占式，允许它们中断低优先级任务：
    *   创建任务时设置 `preemptive=True`，允许它中断其他任务
    *   当执行抢占式任务时，组件将检查是否应该中断当前正在运行的任务
    *   被中断的任务被放回队列，稍后可以恢复

*   **使用示例：**
    ```python
    # 当电池电量低时创建一个关键的、抢占式的充电任务
    charging_task = agent.execute_task(
        component_name="ChargingComponent",
        task_class="ChargeBatteryTask",
        task_name="紧急充电",
        properties={"target_level": 90.0},
        priority=TaskPriority.CRITICAL,
        preemptive=True
    )
    ```

### 5. 关键要点

*   任务代表工作流-代理-任务框架中的原子执行单元。
*   任务包含*逻辑*，组件提供*执行环境和资源*。
*   子类**必须**定义 `NECESSARY_METRICS`、`PRODUCED_STATES`，并实现 `estimate_remaining_time`、`_update_task_state` 和 `_get_task_specific_state_repr`。
*   `execute` 方法的核心循环等待时间流逝*或*组件指标更新。
*   任务通过 `_get_task_specific_state_repr` 更新拥有代理的状态。
*   任务可以具有优先级和抢占属性，允许代理根据重要性和紧急性管理任务执行。
*   使用 `_possessing_object_on_...` 钩子管理与任务生命周期相关的代理拥有的资源。
