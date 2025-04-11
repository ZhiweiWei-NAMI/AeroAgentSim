## AirFogSim Task 开发者文档

本文档指导开发者在 AirFogSim 框架内创建自定义 `Task` 子类。它解释了 `Task` 对象的作用、其由 `Component` 管理的执行生命周期、与 Agent 状态的交互以及性能指标。

### 1. 概述

*   **`Task`:** 代表一个特定的、可执行的动作或工作单元。它包含执行该动作的*逻辑*。任务由 `Agent` 启动，但*由* `Component` 执行。

**关系:**
`Agent` (决定*做什么*和*何时做*) -> `Component` (提供*能力*和*资源*) -> `Task` (定义动作*如何*完成)。

### 2. `Task` 基类

`Task` 类是所有特定动作的抽象基类。子类定义实际行为。

#### 2.1 核心概念

*   **初始化 (`__init__`):**
    *   分配唯一的 ID、名称，链接到 Agent、Component 和可选的 Workflow ID。
    *   存储 `target_state`（期望的结果）和 `properties`（任务特定参数，如持续时间、目标位置）。
    *   初始化状态 (`PENDING`)、时间、结果等。
    *   **关键：验证 `PRODUCED_STATES` 和 `NECESSARY_METRICS`（见下文）。** 它检查 `PRODUCED_STATES` 是否已定义并存在于 Agent 的状态模板中，以及 `NECESSARY_METRICS` 是否已定义。
*   **执行上下文 (`execute` 方法):**
    *   `execute` 方法包含核心任务逻辑，作为一个 **SimPy 生成器函数**。
    *   **重要:** 此方法由 `Component` 在其 `_execute_task_wrapper` 内调用和运行。
    *   它从组件接收 `initial_metrics`。
    *   **执行循环:**
        1.  将状态设置为 `RUNNING`，记录 `start_time`。
        2.  进入 `while self.progress < 1.0` 循环。
        3.  计算 `estimate_remaining_time(self.current_metrics)`。
        4.  **等待 (`yield`)** 以下事件中*最早发生*的一个：
            *   `completion_timeout`: 等于估计剩余时间的时间流逝。
            *   `metric_change_event`: *由所属 Component*（例如 `ComponentName.metric_changed`）在其性能指标变化时触发的事件。
            *   `visual_update_event`: (可选) 用于可视化更新的全局事件。
        5.  如果 `metric_change_event` 发生，则使用事件数据中的新值更新 `self.current_metrics`。
        6.  调用 `self._update_task_state(self.current_metrics)` (抽象) 以根据经过的时间和当前指标更新进度和内部状态。
        7.  更新 `self.last_update_time`。
        8.  触发特定于任务的 `state_changed` 事件 (`self.id`, 'state_changed')，包含任务当前状态的表示 (`_get_current_task_state_repr`)。
        9.  使用 `self.agent.update_states(self._get_task_specific_state_repr())` 更新 **Agent 的状态**（抽象方法提供状态字典）。
        10. 循环继续，直到 `self.progress >= 1.0`。
    *   **完成/失败:** 根据结果调用 `self.complete()` 或 `self.fail()`。处理 `simpy.Interrupt`。
    *   返回最终的 `self.result` 字典。
*   **状态管理 (`complete`, `fail`, `cancel`):** 设置最终任务状态、结束时间、结果字典和失败原因的方法。这些方法也会调用相应的 `_possessing_object_on_...` 钩子。
*   **拥有对象钩子 (`_possessing_object_on_complete`, `_possessing_object_on_fail`, `_possessing_object_on_cancel`):**
    *   当任务分别完成、失败或取消时，这些方法会自动被调用。
    *   子类可以覆盖这些方法来实现管理 Agent 拥有对象（例如，在完成或失败时释放充电站资源）的逻辑。基础实现不执行任何操作。

#### 2.2 强制性子类定义

创建 `Task` 子类时，您**必须**定义/实现以下内容：

1.  **类属性:**
    *   `NECESSARY_METRICS: List[str]`: 此任务*需要*从执行 `Component` 的 `_calculate_performance_metrics` 输出中获取的指标名称（字符串）列表。基础 `__init__` 检查此列表是否已定义且非空。示例：`['speed', 'processing_power']`。
    *   `PRODUCED_STATES: List[str]`: 此任务预期通过 `_get_task_specific_state_repr` 修改的*Agent*状态变量名称（字符串）列表。基础 `__init__` 检查此列表是否已定义、非空，并且所有列出的状态键都存在于 `Agent` 的状态模板中 (`agent.get_state_templates()`)。示例：`['position', 'battery_level', 'status']`。

2.  **抽象方法实现:**
    *   `_update_task_state(self, performance_metrics: Dict)`:
        *   **输入:** 来自组件的当前性能指标字典。
        *   **动作:** 这是核心进度逻辑。根据 `performance_metrics` 和自 `self.last_update_time` 以来经过的时间 (`self.env.now - self.last_update_time`) 更新任务的内部状态（例如，覆盖的距离、处理的数据）。**关键：更新 `self.progress`（必须在 0.0 和 1.0 之间）。**
        *   **示例 (`MoveToTask`):** 使用 `speed` 指标计算移动的距离，沿路径更新 `self.current_position`，并根据行进距离与总距离更新 `self.progress`。还根据移动的距离更新内部 `self.battery_level`。
    *   `estimate_remaining_time(self, performance_metrics: Dict) -> float`:
        *   **输入:** 来自组件的当前性能指标字典。
        *   **输出:** 给定*当前*指标，完成任务*从当前进度*所需的估计*剩余*时间。如果使用当前指标无法完成，则返回 `float('inf')`。如果已完成，则返回 `0`。
        *   **示例 (`MoveToTask`):** 根据剩余距离 (`self.total_distance - self.distance_traveled`) 和当前 `speed` 计算。
    *   `_get_task_specific_state_repr(self) -> Dict`:
        *   **输出:** 一个字典，包含此任务在其当前进度下产生的*Agent*状态更新。键**必须**是 `PRODUCED_STATES` 的子集。
        *   **动作:** 此字典在 `execute` 循环期间传递给 `self.agent.update_states()`。
        *   **示例 (`MoveToTask`):** 返回 `{'position': self.current_position, 'direction': self.direction, 'distance_traveled': self.distance_traveled, 'altitude': ..., 'battery_level': self.battery_level}`。

#### 2.3 可选覆盖和属性

*   `_get_current_task_state_repr(self) -> Dict`: 覆盖以向任务本身触发的 `state_changed` 事件添加更多特定于任务的详细信息。基础实现包括 ID、名称、状态和进度等基本信息，以及来自 `_get_task_specific_state_repr` 的内容。
*   `_possessing_object_on_complete(self)`: 覆盖以在任务成功完成时添加逻辑（例如，释放资源）。
*   `_possessing_object_on_fail(self)`: 覆盖以在任务失败时添加逻辑（例如，释放资源）。
*   `_possessing_object_on_cancel(self)`: 覆盖以在任务取消时添加逻辑（例如，释放资源）。

### 3. 实现自定义 Task 子类 (示例: `MoveToTask`)

1.  **继承:** `class MyTask(Task): ...`
2.  **定义类属性:**
    ```python
    class ComputeDataTask(Task):
        NECESSARY_METRICS = ['processing_power'] # 需要来自组件的 CPU 功率指标
        PRODUCED_STATES = ['computation_load', 'result_data', 'status'] # 更新 Agent 的负载、存储结果并设置状态
    ```
3.  **实现 `__init__`:**
    ```python
    def __init__(self, env, agent, component_name, task_name, ..., properties=None):
        super().__init__(...)
        # 从 properties 存储特定于任务的参数
        self.data_size = properties.get('data_size', 100) # 例如 MB
        self.complexity = properties.get('complexity', 1.0) # 影响时间的因子
        # 初始化内部任务状态变量
        self.processed_data = 0.0
        # 其他初始设置
    ```
4.  **实现 `estimate_remaining_time`:**
    ```python
    def estimate_remaining_time(self, performance_metrics: Dict) -> float:
        power = performance_metrics.get('processing_power', 0) # 例如 MIPS
        if power <= 0:
            return float('inf') # 没有功率无法完成

        total_ops = self.data_size * self.complexity * 1e6 # 示例计算
        ops_done = self.processed_data * self.complexity * 1e6
        remaining_ops = total_ops - ops_done

        if remaining_ops <= 0:
            return 0.0

        # 剩余时间 = 剩余操作数 / 功率
        return remaining_ops / power
    ```
5.  **实现 `_update_task_state`:**
    ```python
    def _update_task_state(self, performance_metrics: Dict):
        power = performance_metrics.get('processing_power', 0)
        elapsed_time = self.env.now - self.last_update_time

        # 计算此步骤完成的工作量
        ops_done_this_step = power * elapsed_time
        data_processed_this_step = ops_done_this_step / (self.complexity * 1e6) # 反向计算
        self.processed_data += data_processed_this_step

        # 计算总进度
        total_ops = self.data_size * self.complexity * 1e6
        current_total_ops_done = self.processed_data * self.complexity * 1e6

        if total_ops > 0:
             self.progress = min(1.0, current_total_ops_done / total_ops)
        else:
             self.progress = 1.0 # 无需工作，立即完成
    ```
6.  **实现 `_get_task_specific_state_repr`:**
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
            'status': 'computing' if self.status == TaskStatus.RUNNING else self.agent.get_state('status') # 更新 Agent 状态
        }
    ```
7.  **实现拥有对象钩子 (可选):**
    ```python
    def _possessing_object_on_complete(self):
        # 示例：如果持有计算资源锁，则释放它
        compute_resource = self.agent.get_possessing_object('my_compute_lock')
        if compute_resource:
            print(f"时间 {self.env.now}: 任务 {self.id} 完成，释放计算锁。")
            self.agent.remove_possessing_object('my_compute_lock')

    def _possessing_object_on_fail(self):
        # 示例：失败时也释放锁
        compute_resource = self.agent.get_possessing_object('my_compute_lock')
        if compute_resource:
            print(f"时间 {self.env.now}: 任务 {self.id} 失败，释放计算锁。")
            self.agent.remove_possessing_object('my_compute_lock')
    ```

### 4. 关键要点

*   任务包含*逻辑*，组件提供*执行环境和资源*。
*   子类**必须**定义 `NECESSARY_METRICS`、`PRODUCED_STATES`，并实现 `estimate_remaining_time`、`_update_task_state` 和 `_get_task_specific_state_repr`。
*   `execute` 方法的核心循环等待时间流逝*或*组件指标更新。
*   任务通过 `_get_task_specific_state_repr` 更新所属 Agent 的状态。
*   使用 `_possessing_object_on_...` 钩子来管理与任务生命周期相关的 Agent 拥有资源。