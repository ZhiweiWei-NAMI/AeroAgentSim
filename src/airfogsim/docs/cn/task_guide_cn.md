
## AirFogSim Task & TaskProof 开发者文档

本文档旨在指导开发者在 AirFogSim 框架内创建自定义 `Task` 子类。它解释了 `Task` 对象的作用、其由 `Component` 管理的执行生命周期、与 Agent 状态和性能指标的交互，以及相关的 `TaskProof` 概念。

### 1. 概述

*   **`Task` (任务):** 代表一个具体的、可执行的动作或工作单元。它包含执行该动作的*逻辑*。任务由 `Agent`（代理）发起，但由 `Component`（组件）*执行*。
*   **`TaskProof` (任务证明):** 与 `Task` 和 `Workflow`（工作流）关联的可选对象。它作为任务结果或进度的证据或记录，并可能在参与工作流的代理之间转移。

**关系:**
`Agent` (决定*做什么* & *何时做*) -> `Component` (提供*能力* & *资源*) -> `Task` (定义动作*如何完成*) -> `TaskProof` (记录*结果/证据*)。

### 2. `TaskProof` 类

`TaskProof` 类提供了一种机制，用于在工作流上下文中跟踪与任务相关的可验证结果或数据。

*   **目的:** 存储由任务生成或验证的数据 (`self.data`)，将其链接到 `Workflow` (`self.workflow_id`)，跟踪其所有权 (`self.owner_id`)，并记录其历史 (`self.updated_history`)。
*   **创建:** 通常由 `Task` 在完成时（或执行期间）通过 `task.create_proof()` 创建，这要求 `Task` 子类定义一个 `PROOF_CLASS`。
*   **更新:** 使用 `proof.update(data, agent_id)` 添加或修改数据。这会记录更新时间、数据和负责的代理。
*   **移交 (`task.handover_proof(target_task)`):**
    *   转移任务证明的所有权，通常发生在工作流进展到由不同代理管理的任务时。
    *   **内部实现:** 该方法获取目标任务的代理（`target_agent = target_task.agent`），然后调用 `proof.handover(target_agent)` 来实际执行移交。
    *   **SimPy生成器:** 这是一个 SimPy 生成器函数，需要使用 `yield env.process(task.handover_proof(target_task))` 来调用。
    *   **可选的移交工作流:** 如果设置了 `task._proof_handover_workflow_class`，移交过程会触发一个独立的、专门的工作流来管理转移过程本身（例如，用于验证步骤）。`handover_proof` 方法将等待这个工作流完成后才最终确定所有权的转移。如果未定义移交工作流，则所有权立即转移。
    *   **任务证明引用:** 移交完成后，目标任务的 `proof` 属性会更新为该证明，而源任务的 `proof` 属性会被设置为 `None`。
*   **示例 (`LocationProof`):** 一个存储位置数据的简单证明，由 `MoveToTask` 使用。

### 3. `Task` 基类

`Task` 类是所有具体动作的抽象基类。子类定义实际行为。

#### 3.1 核心概念

*   **初始化 (`__init__`):**
    *   分配唯一的 ID、名称，链接到 Agent、Component、Workflow 和 Proof ID。
    *   存储 `target_state`（期望结果）和 `properties`（任务特定参数，如持续时间、目标位置）。
    *   初始化状态 (`PENDING`)、时间、结果等。
    *   **关键：验证 `PRODUCED_STATES` 和 `NECESSARY_METRICS` (见下文)。**
*   **执行上下文 (`execute` 方法):**
    *   `execute` 方法包含核心任务逻辑，是一个 **SimPy 生成器函数**。
    *   **重要:** 此方法由 `Component` 在其 `_execute_task_wrapper` 内部调用和运行。
    *   它从组件接收 `initial_metrics` (初始指标)。
    *   **执行循环:**
        1.  设置状态为 `RUNNING`，记录 `start_time`。
        2.  进入 `while self.progress < 1.0` 循环。
        3.  计算 `estimate_remaining_time()` (估计剩余时间)。
        4.  **等待 (`yield`)** 以下事件中*最早发生*的那个：
            *   `completion_timeout`: 等于估计剩余时间的时间流逝。
            *   `metric_change_event`: 由*所属 Component* 在其性能指标变化时触发的事件。
            *   `visual_update_event`: (可选) 用于视觉更新的全局事件。
        5.  如果 `metric_change_event` 发生，更新 `self.current_metrics`。
        6.  调用 `self._update_task_state(self.current_metrics)` (抽象方法) 以根据流逝时间和当前指标更新进度和内部状态。
        7.  调用 `self._trigger_workflow_events()` (抽象方法) 与工作流交互。
        8.  更新 `self.last_update_time`。
        9.  触发任务特定的 `state_changed` 事件。
        10. 使用 `self.agent.update_states(self._get_task_specific_state_repr())` (抽象方法提供状态字典) 更新 **Agent 的状态**。
        11. 循环继续，直到 `self.progress >= 1.0`。
    *   **完成/失败:** 根据结果调用 `self.complete()` 或 `self.fail()`。处理 `simpy.Interrupt`。
    *   返回最终的 `self.result` 字典。
*   **状态管理 (`complete`, `fail`, `cancel`):** 用于设置最终任务状态、结束时间、结果字典和失败原因的方法。`complete` 还处理 `TaskProof` 的创建或更新。

#### 3.2 强制性子类定义

创建 `Task` 子类时，您**必须**定义/实现以下内容：

1.  **类属性:**
    *   `NECESSARY_METRICS: List[str]`: 一个指标名称（字符串）列表，表示此任务*需要*从执行它的 `Component` 的 `_calculate_performance_metrics` 输出中获取哪些指标。基类 `__init__` 会检查此列表是否已定义且非空。示例: `['speed', 'processing_power']`。
    *   `PRODUCED_STATES: List[str]`: 一个 *Agent* 状态变量名称（字符串）列表，表示此任务预期通过 `_get_task_specific_state_repr` 修改哪些状态。基类 `__init__` 会检查此列表是否已定义、非空，并且所有列出的状态键都存在于 `Agent` 的状态模板 (`agent.get_state_templates()`) 中。示例: `['position', 'battery_level']`。
    *   `PROOF_CLASS: Type[TaskProof] | None`: (可选) 此任务在完成时创建或更新的特定 `TaskProof` 子类（例如 `LocationProof`）。如果为 `None`，则不自动处理证明。

2.  **抽象方法实现:**
    *   `estimate_total_time(self, performance_metrics: Dict) -> float`:
        *   **输入:** 来自组件的当前性能指标字典。
        *   **输出:** 给定*当前*指标，从*开始*完成任务所需的估计*总*时间。如果当前指标无法完成任务，则返回 `float('inf')`。
        *   **示例 (`MoveToTask`):** 根据总距离和当前速度计算。
    *   `_update_task_state(self, performance_metrics: Dict)`:
        *   **输入:** 当前性能指标字典。
        *   **动作:** 这是核心进度逻辑。根据 `performance_metrics` 和自 `self.last_update_time` 以来流逝的时间 (`self.env.now - self.last_update_time`) 更新任务的内部状态（例如，覆盖的距离、处理的数据）。**关键是更新 `self.progress` (必须在 0.0 和 1.0 之间)。**
        *   **示例 (`MoveToTask`):** 使用 `speed` 指标计算移动距离，沿路径更新 `self.current_position`，并根据已行进距离与总距离的比例更新 `self.progress`。
    *   `_get_task_specific_state_repr(self) -> Dict`:
        *   **输出:** 一个字典，包含此任务在其当前进度下产生的 *Agent* 状态更新。键**必须**是 `PRODUCED_STATES` 的子集。
        *   **动作:** 这个字典在 `execute` 循环期间传递给 `self.agent.update_states()`。
        *   **示例 (`MoveToTask`):** 返回 `{'position': self.current_position, 'direction': self.direction, ...}`。
    *   `_trigger_workflow_events(self)`:
        *   **动作:** 在此实现与关联工作流交互的逻辑（如果需要）。这通常涉及更新 `TaskProof` (`self.proof.update(...)`) 并在 Agent 上触发特定事件（例如 `agent.trigger_event('proof_updated', ...)`）。
        *   **示例 (`MoveToTask`):** 使用当前位置更新 `LocationProof` 并触发 `proof_updated` 和 `position_changed` 事件。

#### 3.3 可选的覆盖与属性

*   `estimate_remaining_time(self) -> float`: 基类实现根据 `progress` 和 `estimate_total_time` 计算此值。如果需要更复杂的计算，可以覆盖此方法。
*   `_proof_handover_workflow_class: Type[Workflow] | None`: 如果您希望证明移交过程由专门的工作流管理，请设置此属性（通常在 `__init__` 中）为一个 `Workflow` 类。

### 4. 实现自定义任务子类 (示例: `MoveToTask`)

1.  **继承:** `class MyTask(Task): ...`
2.  **定义类属性:**
    ```python
    class ComputeDataTask(Task):
        NECESSARY_METRICS = ['processing_power'] # 需要 CPU 功率指标
        PRODUCED_STATES = ['computation_load', 'result_data'] # 更新代理的负载并存储结果
        PROOF_CLASS = None # 不产生标准证明
    ```
3.  **实现 `__init__`:**
    ```python
    def __init__(self, env, agent, component_name, task_name, ..., properties=None):
        super().__init__(...)
        self.data_size = properties.get('data_size', 100) # 例如 MB
        self.complexity = properties.get('complexity', 1.0) # 影响时间的因子
        self.processed_data = 0.0
        # 其他初始设置
    ```
4.  **实现 `estimate_total_time`:**
    ```python
    def estimate_total_time(self, performance_metrics: Dict) -> float:
        power = performance_metrics.get('processing_power', 0) # 例如 MIPS
        if power <= 0:
            return float('inf')
        # 总操作数 = 大小 * 复杂度
        # 时间 = 总操作数 / 功率
        total_ops = self.data_size * self.complexity * 1e6 # 示例计算
        return total_ops / power
    ```
5.  **实现 `_update_task_state`:**
    ```python
    def _update_task_state(self, performance_metrics: Dict):
        power = performance_metrics.get('processing_power', 0)
        elapsed_time = self.env.now - self.last_update_time
        ops_done_this_step = power * elapsed_time
        data_processed_this_step = ops_done_this_step / (self.complexity * 1e6) # 反向计算
        self.processed_data += data_processed_this_step

        total_ops = self.data_size * self.complexity * 1e6
        current_total_ops_done = self.processed_data * self.complexity * 1e6

        if total_ops > 0:
             self.progress = min(1.0, current_total_ops_done / total_ops)
        else:
             self.progress = 1.0
    ```
6.  **实现 `_get_task_specific_state_repr`:**
    ```python
    def _get_task_specific_state_repr(self) -> Dict:
        load = self.current_metrics.get('processing_power', 0) if self.status == TaskStatus.RUNNING else 0
        result = {'final_result': 'some_value'} if self.progress >= 1.0 else None # 仅在完成时提供最终结果状态
        return {
            'computation_load': load,
            'result_data': result
            # 确保 'result_data' 键在 PRODUCED_STATES 中
        }
    ```
7.  **实现 `_trigger_workflow_events`:**
    ```python
    def _trigger_workflow_events(self):
        # 示例：完成 50% 时触发事件
        if self.progress >= 0.5 and not hasattr(self, '_halfway_triggered'):
             self.agent.trigger_event('compute_halfway', {'task_id': self.id, 'time': self.env.now})
             self._halfway_triggered = True
        # 如果适用，更新证明
        # if self.proof: self.proof.update(...)
    ```

### 5. 关键要点

*   任务包含*逻辑*，组件提供*执行环境和资源*。
*   子类**必须**定义 `NECESSARY_METRICS`, `PRODUCED_STATES`, 并实现 `estimate_total_time`, `_update_task_state`, `_get_task_specific_state_repr`, 和 `_trigger_workflow_events`。
*   `execute` 方法的核心循环等待时间流逝*或*组件指标更新。
*   任务通过 `_get_task_specific_state_repr` 更新所属 Agent 的状态。
*   `TaskProof` 为工作流内的结果跟踪和移交提供了一种可选机制。