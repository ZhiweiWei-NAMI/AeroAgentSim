
## AirFogSim Agent 开发者文档


本文档旨在指导开发者理解和扩展 AirFogSim 框架内的 `airfogsim.core.agent.Agent` 类。它解释了核心概念、如何定义自定义 Agent 类型以及如何实现它们的行为。文档中将使用您提供的 `DroneAgent` 示例作为参考。
### 1. `Agent` 类概述

`Agent` 类代表仿真环境 (`simpy.Environment`) 中的一个活跃的、能做出决策的实体。Agent 是与模拟世界交互的主要参与者，通过以下方式进行：

1.  **维护状态 (Maintaining State):** 跟踪内部属性，如位置、电池电量、状态等。
2.  **持有组件 (Owning Components):** 利用功能单元 (`Component`)，如传感器、执行器（例如移动系统）或计算资源来执行动作。
3.  **执行任务 (Executing Tasks):** 定义并启动由其组件执行的动作 (`Task`)。
4.  **响应事件 (Reacting to Events):** 对环境或自身内部状态的变化做出反应。
5.  **管理工作流 (Managing Workflows):** (可选) 参与或管理更大规模的任务序列 (`Workflow`)。

基础 `Agent` 类为这些能力提供了基础结构和机制。子类通过添加专门的状态、组件和行为逻辑来定义特定的 Agent 类型（如 `DroneAgent`、`GroundStationAgent` 等）。

### 2. 核心概念

#### 2.1 状态管理 (`StateTemplate`, `AgentMeta`)

*   **目的:** Agent 使用 `self.state` 字典来跟踪其属性。为了确保一致性、类型安全并定义需求，框架使用了 `StateTemplate`。
*   **`StateTemplate`:** 定义了状态变量的预期属性：
    *   `key` (str): 状态变量的名称。
    *   `value_type` (type, 可选): 期望的 Python 类型 (例如 `int`, `float`, `str`, `tuple`)。`None` 表示允许任何类型。
    *   `required` (bool): 此状态是否*必须*被初始化。如果缺失，将发出警告。
    *   `validator` (callable, 可选): 一个函数，接收值并返回 `True` (如果有效) 或 `False` (如果无效)。
    *   `description` (str, 可选): 人类可读的描述。
*   **`AgentMeta` (元类):** 这个元类自动处理 `StateTemplate` 定义的继承。当您在基类或子类的元类（如 `DroneAgentMeta`）中定义模板时，它们会被聚合。每个 Agent 实例在更新状态时，都会根据其*整个类层级结构*的组合模板进行验证。
*   **注册模板 (Registering Templates):**
    *   **推荐方式 (通过元类):** 定义一个继承自 `AgentMeta` 的自定义元类（如 `DroneAgentMeta`），并在其 `__new__` 方法中调用 `mcs.register_template(cls, ...)`。这对于组织特定类型的状态更为清晰。
        ```python
        # DroneAgentMeta 示例
        class DroneAgentMeta(AgentMeta):
            def __new__(mcs, name, bases, attrs):
                cls = super().__new__(mcs, name, bases, attrs)
                # 注册无人机专属的状态模板
                mcs.register_template(cls, 'position', tuple, True, ...)
                mcs.register_template(cls, 'battery_level', float, True, ...)
                # ... 其他无人机特定的状态
                return cls

        class DroneAgent(Agent, metaclass=DroneAgentMeta):
            # ... Agent 实现 ...
        ```
    *   **备选方式 (类方法):** 直接在 Agent 子类上使用 `@classmethod` 装饰器 `register_state_template` (不太常见但可行)。
*   **使用状态 (Using State):**
    *   `initialize_states(**states)`: 在 `__init__` 期间设置初始状态值。检查必需的状态。
    *   `update_state(key, value)`: 更新单个状态值。如果值发生变化，会触发验证和 `state_changed` 事件。
    *   `update_states(state_dict)`: 更新多个状态。
    *   `get_state(key, default=None)`: 获取状态值。
    *   `get_current_states()`: 获取整个状态字典的副本。
    *   `get_state_templates()`: (类方法) 获取该类所有适用的模板。

#### 2.2 组件管理

*   **目的:** 组件 (`airfogsim.core.Component`) 代表 Agent 的功能部件（例如 'MobilitySystem', 'CPUEngine', 'CameraSensor'）。Agent 将任务执行委托给它们的组件。
*   **用法:**
    *   `add_component(component)`: 将一个 `Component` 实例附加到 Agent。该组件会获得一个指向 Agent 的引用 (`component.agent`)。
    *   `get_component(component_name)`: 通过名称检索组件。
    *   `get_component_names()`: 列出所有附加组件的名称。
*   **示例:** 一个 Agent 需要 `MobilityComponent` 来移动，需要 `ComputeComponent` 来处理数据。这些组件将在初始化或配置期间被添加。

#### 2.3 事件处理

*   **目的:** Agent 可以使用由 `env.event_registry` 管理的发布-订阅系统来响应和宣告重要事件。
*   **标准 Agent 事件:** 基础 `Agent` 自动注册以下事件：
    *   `state_changed`: 当 `update_state` 改变一个值时触发。
    *   `task_started`: 当 Agent 通过 `execute_task` 启动任务时触发。
    *   `task_finished`: 当 Agent 观察到它启动的任务已完成（成功、失败或取消）时触发。
    *   `proof_created`, `proof_updated`, `proof_transferred`: 与任务证明管理相关。
*   **用法:**
    *   `register_event(event_name)`: 声明此 Agent 可能触发的事件。
    *   `trigger_event(event_name, value=None)`: 触发一个事件，通知所有订阅者。
    *   `subscribe(source_id, event_name, callback, listener_id=None)`: 注册一个 `callback` 函数，当 `source_id` 触发 `event_name` 时调用该函数。
    *   `unsubscribe(source_id, event_name, listener_id)`: 停止监听某个事件。
    *   `unsubscribe_all()`: 移除*由此* Agent 创建的所有订阅。
    *   `get_event(event_name)`: 获取此 Agent 事件对应的底层 `simpy.Event` 对象 (对于 `yield` 操作很有用)。

#### 2.4 任务执行

*   **目的:** Agent 决定*需要做什么*以及*哪个组件*应该做。它们创建 `Task` 对象并请求组件执行它们。
*   **`Task` 对象:** 代表一个工作单元，具有名称、状态、开始/结束时间、关联的工作流/证明等属性。特定的任务类型（例如 `MoveToTask`, `ComputeTask`）继承自 `airfogsim.core.Task`。
*   **`execute_task(...)` 方法:**
    *   这是 Agent 发起工作的主要方式。
    *   它接收 `component_name`、`task_name`、可选的 `task_class` (默认为 `Task`)、`target_state`、任务的 `properties` 以及可选的 `workflow_id`/`proof_id`。
    *   **关键点：它是非阻塞的 (non-blocking)。** 它创建 `Task`，请求组件开始执行，触发 `task_started` 事件，并*立即*返回 `Task` 对象。
    *   它会启动一个内部监控进程 (`_monitor_task_execution`) 来等待组件执行完成。
*   **`_monitor_task_execution(...)` (内部):**
    *   一个 SimPy 进程，它 `yield` 组件的执行进程。
    *   处理组件任务执行的完成、失败或中断。
    *   根据结果更新 `Task` 的状态/结果。
    *   触发 Agent 的 `task_finished` 事件。
    *   管理 `self.managed_tasks` 字典 (跟踪由 Agent 发起的活动任务)。
*   **工作流:** Agent 的 `live()` 方法通常根据其状态、目标或分配的工作流来决定*何时*以及*执行哪些*任务。

### 3. 实现自定义 Agent 子类

请遵循以下步骤创建您自己的 Agent 类型 (以 `DroneAgent` 为指导)：

1.  **定义类 (Define the Class):**
    *   继承自 `Agent`。
    *   (推荐) 创建一个继承自 `AgentMeta` 的伴随元类，用于注册特定类型的状态。
    ```python
    from airfogsim.core.agent import Agent, AgentMeta

    # 定义元类 (可选，但对于状态管理是好实践)
    class MyAgentMeta(AgentMeta):
        def __new__(mcs, name, bases, attrs):
            cls = super().__new__(mcs, name, bases, attrs)
            # 注册 MyAgent 特定的状态
            mcs.register_template(cls, 'my_custom_state', str, True, description="一个必需的自定义状态")
            mcs.register_template(cls, 'optional_counter', int, False, validator=lambda x: x >= 0)
            return cls

    # 定义 Agent 子类
    class MyAgent(Agent, metaclass=MyAgentMeta):
        # ... 实现 ...
    ```

2.  **实现 `__init__`:**
    *   调用 `super().__init__(env, agent_name, properties)`。
    *   添加任何自定义实例变量 (例如 `DroneAgent` 中的 `llm_client` 或 `task_class_map`)。
    *   **关键:** 使用 `self.initialize_states(...)` 初始化 Agent 的状态，为在其类层级结构中定义的所有 `required=True` 的状态提供值。
    *   (可选) 使用 `self.add_component(...)` 添加必要的组件。组件可以被传入或在此处创建。
    ```python
    from airfogsim.components.mobility import MobilityComponent # 示例组件

    class MyAgent(Agent, metaclass=MyAgentMeta):
        def __init__(self, env, agent_name: str, initial_custom_state: str, properties=None, mobility_params=None):
            super().__init__(env, agent_name, properties)

            # 添加自定义属性
            self.my_internal_tracker = 0

            # 初始化状态 (必须包含必需的状态)
            self.initialize_states(
                my_custom_state=initial_custom_state,
                optional_counter=0
                # 其他继承的状态，如 'status'，也可以在这里设置
            )

            # 添加组件
            if mobility_params:
                mobility_comp = MobilityComponent(env, "MobilitySystem", **mobility_params)
                self.add_component(mobility_comp)
            # 添加其他组件...
    ```

3.  **实现 `live()`:**
    *   这个方法**必须**被实现。
    *   它定义了 Agent 的核心行为循环，作为一个 SimPy 进程 (它必须至少包含一个 `yield`)。
    *   在 `live()` 内部，Agent 根据其当前的 `self.state`、传入的事件、分配的工作流或其他逻辑来决定做什么。
    *   常见模式：
        *   等待一段时间: `yield self.env.timeout(duration)`
        *   等待一个事件: `yield self.get_event('some_event_name')` 或 `yield some_simpy_event`
        *   等待任务完成: 监控由内部处理，但*如果*你需要同步等待（对于复杂 Agent 来说不太常见），你可以 `yield execute_task` 返回的进程。通常，你会执行任务，然后让 `task_finished` 事件或其他触发器驱动下一个决策。
        *   执行任务: 调用 `self.execute_task(...)`。
        *   更新状态: 调用 `self.update_state(...)`。
    ```python
    class MyAgent(Agent, metaclass=MyAgentMeta):
        # ... __init__ ...

        def live(self):
            print(f"时间 {self.env.now}: {self.name} 开始运行.")
            yield self.env.timeout(1) # 初始延迟

            while True:
                current_status = self.get_state('status', 'idle') # 示例状态检查

                if current_status == 'needs_action':
                    print(f"时间 {self.env.now}: {self.name} 决定执行一个动作.")
                    # 示例：使用 'MobilitySystem' 组件执行任务
                    move_task = self.execute_task(
                        component_name='MobilitySystem',
                        task_name='移动到目标点',
                        task_class=MoveToTask, # 假设 MoveToTask 已导入
                        properties={'target_position': (10, 20, 5)},
                        target_state={'position': (10, 20, 5)} # 预期的最终状态
                    )

                    if move_task and move_task.id in self.managed_tasks:
                        # 选项 1: 等待这个特定任务 (同步步骤)
                        # print(f"时间 {self.env.now}: {self.name} 正在等待移动任务 {move_task.id}...")
                        # result = yield self.managed_tasks[move_task.id]['process']
                        # print(f"时间 {self.env.now}: {self.name} 移动任务完成，结果: {result}")

                        # 选项 2: 继续操作，稍后通过 'task_finished' 事件响应 (异步)
                        print(f"时间 {self.env.now}: {self.name} 已启动移动任务 {move_task.id}. 继续...")
                        self.update_state('status', 'moving')
                        # 循环将继续，或者可能等待另一个事件
                        yield self.env.timeout(0.1) # 下次检查前的短暂延迟

                    else:
                        print(f"时间 {self.env.now}: {self.name} 启动移动任务失败.")
                        self.update_state('status', 'error')
                        yield self.env.timeout(5) # 重试或停止前等待

                elif current_status == 'idle':
                    print(f"时间 {self.env.now}: {self.name} 处于空闲状态。等待事件发生。")
                    # 等待外部触发器或超时
                    # 示例: 等待特定事件 或 超时
                    event_trigger = self.get_event('start_mission') # 示例事件
                    yield event_trigger | self.env.timeout(10)

                    if event_trigger.triggered:
                        print(f"时间 {self.env.now}: {self.name} 收到 start_mission 事件!")
                        self.update_state('status', 'needs_action')
                    else:
                        print(f"时间 {self.env.now}: {self.name} 等待任务超时.")
                        # 决定超时后做什么

                else:
                     # 处理其他状态 ('moving', 'error' 等)
                     yield self.env.timeout(1) # 通用等待
    ```

### 4. LLM 集成 (示例: `DroneAgent`)

`Agent` 类本身与大语言模型 (LLM) 无关，但子类可以轻松集成它们以进行智能决策，特别是任务规划。`DroneAgent` 演示了这一点：

1.  **LLM 客户端 (LLM Client):** 在 `__init__` 期间传入一个 LLM 客户端 (例如 `openai.OpenAI`) 并存储在 `self.llm` 中。
2.  **Prompt 工程 (Prompt Engineering):** 像 `_analyze_workflow_with_llm` 这样的方法为 LLM 构建详细的 Prompt (提示)。此 Prompt 包括：
    *   Agent 的当前状态 (`self.get_current_states()`)。
    *   关于当前目标的信息 (例如 `workflow.get_details()`, `workflow.status_machine.state`)。
    *   可用的动作 (组件: `self.get_component_names()`, 任务类: `self.task_class_map`)。
    *   期望的输出格式 (例如，任务规范的 JSON 数组)。
3.  **LLM 调用 (LLM Call):** 该方法调用 LLM API (例如 `self.llm.chat.completions.create(...)`)。
4.  **响应解析 (Response Parsing):** 像 `_parse_llm_response` 这样的方法从 LLM 可能冗长的响应中提取并验证结构化的任务信息（例如 JSON）。它确保任务具有必需的字段 (`component`, `task_name`, `properties` 等)。
5.  **集成到 `live()` (Integration into `live()`):** `live()` 方法调用 LLM 分析函数 (`_analyze_workflow_with_llm`)。如果 LLM 提供了有效的任务，Agent 将继续使用 `self.execute_task` 执行它们。如果 LLM 不可用或失败，Agent 可能会回退到更简单的、预编程的逻辑 (例如 `DroneAgent` 中的 `_plan_inspection_tasks`)。

这种模式允许 Agent 利用复杂的规划能力，同时保持核心的仿真结构。

### 5. 关键要点与最佳实践

*   **继承 `Agent`:** 它提供了核心结构。
*   **定义状态:** 通过元类 (`XxxAgentMeta`) 使用 `StateTemplate` 来确保清晰度和验证。
*   **实现 `__init__`:** 调用 `super().__init__`，初始化状态 (`initialize_states`)，并添加组件 (`add_component`)。
*   **实现 `live()`:** 这是 Agent 行为的核心。使用 `yield` 来传递时间或等待事件。
*   **使用 `execute_task`:** 通过组件启动动作。记住它是非阻塞的。
*   **利用事件:** 使用 `trigger_event` 和 `subscribe` 进行通信和反应。`task_finished` 事件对于响应已完成的动作特别有用。
*   **组件执行工作:** Agent 决定*做什么*和*何时做*，Components 定义*如何做*。
*   **保持 `live()` 的可管理性:** 将复杂的逻辑分解为辅助方法 (就像 `DroneAgent` 的 `_analyze_workflow_with_llm`, `_plan_inspection_tasks`)。

遵循这些指南，您可以有效地在 AirFogSim 框架内创建多样化且功能强大的 Agent。请参阅 `Agent` 源代码和 `DroneAgent` 示例以获取具体的实现细节。
