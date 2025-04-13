## AirFogSim Agent 开发者文档

本文档旨在指导开发者理解和扩展 AirFogSim 框架内的 `airfogsim.core.agent.Agent` 类。它解释了核心概念、如何定义自定义 Agent 类型以及如何实现它们的行为。

### 1. `Agent` 类概述

`Agent` 类代表模拟环境 (`simpy.Environment`) 中的一个活跃的、能做出决策的实体。Agent 是与模拟世界交互的主要参与者，通过以下方式：

1.  **维护状态:** 使用结构化的状态管理系统跟踪内部属性（例如，位置、状态）以及可能拥有的对象属性（例如，充电站状态）。
2.  **拥有组件:** 利用功能单元 (`Component`)，如传感器、执行器（例如，移动系统）或计算资源来执行动作。
3.  **执行任务:** 创建 `Task` 对象并将其执行委托给适当的组件。
4.  **响应事件:** 通过发布-订阅事件系统响应环境或自身内部状态的变化。
5.  **管理拥有的对象:** 持有对其他模拟对象（如充电站、着陆点）的引用并访问它们的状态。
6.  **与合约交互:** (可选) 通过 `ContractManager` 创建、接受和管理任务卸载合约。
7.  **定义行为:** 通过重写钩子方法（如 `_process_custom_logic`）来实现核心决策逻辑，而不是直接重写 `live()` 方法。

基础 `Agent` 类为这些功能提供了基础结构和机制。子类通过添加专门的状态、组件、拥有的对象和行为逻辑来定义特定的 Agent 类型（如 `DroneAgent`、`GroundStationAgent` 等）。

### 2. 核心概念

#### 2.1 状态管理 (`StateTemplate`, `AgentMeta`)

*   **目的:** Agent 使用 `self.state` 字典来跟踪其属性。为了确保一致性、类型安全并定义需求，框架使用 `StateTemplate`。Agent 还可以使用复合键（例如 `'charging_station.status'`）访问其拥有对象的状态。
*   **`StateTemplate`:** 定义状态变量的预期属性：
    *   `key` (str): 状态变量的名称。
    *   `value_type` (type, Optional): 预期的 Python 类型（例如 `int`, `float`, `str`, `tuple`, `List[int]`）。`None` 允许任何类型。支持像 `List` 这样的基本泛型。
    *   `required` (bool): 此状态是否*必须*初始化。如果缺少，则会发出警告。
    *   `validator` (callable, Optional): 一个函数，接收值并返回 `True`（如果有效）或 `False`（如果无效）。
    *   `description` (str, Optional): 人类可读的描述。
*   **`AgentMeta` (元类):** 这个元类自动处理跨类层次结构的 `StateTemplate` 定义的继承和聚合。每个 Agent 实例根据其整个类层次结构的*组合*模板来验证其状态更新。
*   **注册模板:**
    *   **推荐 (通过元类):** 定义一个继承自 `AgentMeta` 的自定义元类（如 `DroneAgentMeta`），并在其 `__new__` 方法中调用 `mcs.register_template(cls, ...)`。这对于组织特定类型的状态更清晰。
    *   **直接在类上:** 在 Agent 子类上直接使用 `@classmethod` 装饰器 `register_state_template`。
        ```python
        # 使用类装饰器的示例
        @Agent.register_state_template('position', value_type=tuple, required=True, description="Agent 的 3D 位置")
        @Agent.register_state_template('status', value_type=str, required=True, default='idle')
        class MySimpleAgent(Agent):
            # ... Agent 实现 ...
            pass

        # 使用元类的示例 (对于复杂 Agent 更推荐)
        class DroneAgentMeta(AgentMeta):
            def __new__(mcs, name, bases, attrs):
                cls = super().__new__(mcs, name, bases, attrs)
                mcs.register_template(cls, 'battery_level', float, True, validator=lambda x: 0.0 <= x <= 100.0)
                mcs.register_template(cls, 'flight_mode', str, False, default='manual')
                # ... 其他无人机特定状态
                return cls

        class DroneAgent(Agent, metaclass=DroneAgentMeta):
            # ... Agent 实现 ...
            pass
        ```
*   **使用状态:**
    *   `initialize_states(**states)`: 在 `__init__` 期间设置初始状态值。检查必需的状态。
    *   `update_state(key, value)` / `set_state(key, value)`: 更新单个状态值。如果值发生变化，则触发验证和 `state_changed` 事件。
    *   `update_states(state_dict)`: 更新多个状态。
    *   `get_state(key, default=None)`: 检索状态值。支持复合键（例如 `agent.get_state('my_station.is_available')`）。
    *   `has_state(key)`: 检查状态（包括复合键）是否存在。
    *   `get_current_states()`: 获取 Agent 自身状态字典的副本（不包括拥有对象的状态）。
    *   `get_state_templates()`: (类方法) 获取该类的所有适用模板。

#### 2.2 组件管理

*   **目的:** 组件 (`airfogsim.core.Component`) 代表 Agent 的功能部分（例如 'MobilitySystem', 'CPUEngine', 'CameraSensor'）。Agent 将任务执行委托给它们的组件。
*   **用法:**
    *   `add_component(component)`: 将 `Component` 实例附加到 Agent。该组件会获得对 Agent 的反向引用 (`component.agent`)。Agent 会检查它是否为组件的 `MONITORED_STATES` 定义了必要的状态模板。
    *   `get_component(component_name)`: 按名称检索组件。
    *   `get_components()`: 获取所有附加组件实例的列表。
    *   `get_component_names()`: 列出所有附加组件的名称。
*   **示例:** 一个 Agent 需要一个 `MobilityComponent` 来移动，一个 `ComputeComponent` 来处理数据。这些将在初始化或配置期间添加。

#### 2.3 事件处理

*   **目的:** Agent 可以使用由 `env.event_registry` 管理的发布-订阅系统来响应和宣布重要事件。
*   **标准 Agent 事件:** 基础 `Agent` 自动注册并触发：
    *   `state_changed`: 当 `update_state` 更改值时触发（对于 Agent 自身的状态或通过拥有对象间接更改）。事件数据包括 `key`, `old_value`, `new_value`, `time`，以及可能的 `object_name`。
    *   `task_started`: 当 `execute_task` 成功启动任务时由 Agent 触发。
    *   `task_completed`: 当 Agent 启动的任务完成（成功、失败或取消）时，由 Agent 的内部监视器 (`_monitor_task_execution`) 触发。
    *   `possessing_object_added` / `possessing_object_removed`: 当通过 `add_possessing_object` / `remove_possessing_object` 添加/删除对象时触发。
    *   *(注意: 组件特定的事件，如 `ComponentName.task_completed`，由组件本身触发，但 Agent 可以订阅它们。)*
*   **用法:**
    *   `register_event(event_name)`: 声明此 Agent 可能触发的事件。
    *   `trigger_event(event_name, value=None)`: 触发事件，通知所有订阅者。`agent_id` 会自动添加到值中。
    *   `subscribe(source_id, event_name, callback, listener_id=None)`: 注册一个 `callback` 函数，当 `source_id` 触发 `event_name` 时调用。
    *   `unsubscribe(source_id, event_name, listener_id)`: 停止监听事件。
    *   `unsubscribe_all()`: 移除*由此* Agent 进行的所有订阅。
    *   `get_event(event_name)`: 获取此 Agent 事件的底层 `simpy.Event` 对象（对于 `yield` 很有用）。
    *   `has_event(event_name)`: 检查 Agent 是否注册了特定事件。

#### 2.4 任务执行

*   **目的:** Agent 决定*需要做什么*以及*哪个组件*应该做。它们创建 `Task` 对象（通过 `env.task_manager`）并要求组件执行它们。
*   **`Task` 对象:** 代表具有名称、状态、开始/结束时间、关联工作流等属性的工作单元。特定任务类型（例如 `MoveToTask`, `ComputeTask`）继承自 `airfogsim.core.Task`。
*   **`execute_task(...)` 方法:**
    *   这是 Agent 启动工作的主要方式。
    *   它接受 `component_name`, `task_name`, `task_class` (Task 子类的字符串名称), `target_state`, 任务的 `properties`, 可选的 `workflow_id` 和可选的 `task_id`。
    *   它使用 `env.task_manager.create_task` 来实例化 `Task`。
    *   **关键在于，它是非阻塞的。** 它触发 `task_started` 事件，要求组件开始执行 (`component.execute_task(task)`)，启动内部监控过程 (`_monitor_task_execution`)，并*立即*返回 `Task` 对象。
    *   如果找不到组件，则返回 `None`。
*   **`_monitor_task_execution(...)` (内部):**
    *   一个 SimPy 进程，它 `yield` 组件的执行进程 (`component_exec_proc`)。
    *   处理组件任务执行的完成、失败或中断。
    *   根据结果更新 `Task` 状态/结果（主要依赖于 Task 对象的最终状态）。
    *   触发 Agent 的 `task_completed` 事件。
    *   管理 `self.managed_tasks` 字典（跟踪由 Agent 启动的活动任务）。
*   **`cancel_task(task_id)`:**
    *   尝试中断与任务关联的正在运行的进程，包括在组件内 (`component.task_processes`) 和 Agent 的监视器中。
    *   将任务状态更新为 `CANCELED`。
    *   触发 `ComponentName.task_canceled` 事件。
*   **工作流:** Agent 的 `live()` 方法通常根据其状态、目标、分配的工作流或传入事件来决定*何时*以及*执行哪些*任务。

#### 2.5 拥有对象管理

*   **目的:** 允许 Agent 持有对其他模拟实体（例如，无人机拥有特定的充电站或着陆点）的引用，并与其状态交互。
*   **用法:**
    *   `add_possessing_object(object_name, obj)`: 将 `obj` 存储在给定的 `object_name` 下。自动订阅对象的 `state_changed` 事件（如果对象有 `id`），并将这些更改作为带有复合键（例如，`state_changed` 事件，键为 `'my_station.status'`）的 Agent `state_changed` 事件转发。触发 `possessing_object_added`。
    *   `remove_possessing_object(object_name)`: 移除对象并取消订阅其事件。触发 `possessing_object_removed`。
    *   `get_possessing_object(object_name)`: 检索拥有的对象实例。
    *   `get_possessing_object_names()`: 列出所有拥有对象的名称。
    *   `get_state(f'{object_name}.attribute_name')`: 使用点表示法访问拥有对象的状态。

#### 2.6 合约管理 (可选)

*   **目的:** 为 Agent 提供一个接口，以便使用中央 `ContractManager`（如果在环境中有）参与任务卸载。
*   **用法 (需要 `env.contract_manager`):**
    *   `create_contract(task_info, target_agent_ids, reward, ...)`: 创建一个新的任务卸载合约。
    *   `accept_contract(contract_id)`: 接受提供给此 Agent 的合约。
    *   `get_available_contracts()`: 查找分配给此 Agent 且待接受的合约。
    *   `get_agent_contracts(role=None, status=None)`: 检索此 Agent 作为发布者或执行者的合约，可选择按状态过滤。

### 3. 实现自定义 Agent 子类

按照以下步骤创建您自己的 Agent 类型：

1.  **定义类:**
    *   继承自 `Agent`。
    *   (推荐) 创建一个继承自 `AgentMeta` 的伴随元类，或者使用 `@Agent.register_state_template` 装饰器来注册特定类型的状态。
    ```python
    from airfogsim.core.agent import Agent, AgentMeta, StateTemplate
    from typing import Tuple, List # 等

    # 定义元类 (可选，但对于状态是好实践)
    class MyAgentMeta(AgentMeta):
        def __new__(mcs, name, bases, attrs):
            cls = super().__new__(mcs, name, bases, attrs)
            # 注册 MyAgent 特定的状态
            mcs.register_template(cls, 'my_custom_state', str, True, description="一个必需的自定义状态")
            mcs.register_template(cls, 'optional_counter', int, False, validator=lambda x: x >= 0)
            return cls

    # 定义 Agent 子类
    class MyAgent(Agent, metaclass=MyAgentMeta):
        # 如果基础 AgentMeta 中没有完成，则注册所有 Agent 共有的基础状态
        # AgentMeta.register_template(cls, 'status', str, True, default='idle') # 示例

        # ... 实现 ...
    ```

2.  **实现 `__init__`:**
    *   调用 `super().__init__(env, agent_name, properties)`。
    *   添加任何自定义实例变量。
    *   **关键:** 使用 `self.initialize_states(...)` 初始化 Agent 的状态，为其类层次结构中定义的所有 `required=True` 状态提供值。
    *   使用 `self.add_component(...)` 添加必要的组件。组件可能在此处传入或创建。
    *   (可选) 使用 `self.add_possessing_object(...)` 添加拥有的对象。
    ```python
    from airfogsim.component.mobility import MobilityComponent # 示例组件
    from airfogsim.resource import ChargingStation # 示例拥有对象

    class MyAgent(Agent, metaclass=MyAgentMeta):
        def __init__(self, env, agent_name: str, initial_custom_state: str, properties=None, mobility_params=None, charge_station_obj=None):
            super().__init__(env, agent_name, properties)

            # 添加自定义属性
            self.my_internal_tracker = 0

            # 初始化状态 (必须包含必需的状态)
            self.initialize_states(
                my_custom_state=initial_custom_state,
                optional_counter=0,
                status='initializing' # 示例基础状态
            )

            # 添加组件
            if mobility_params:
                mobility_comp = MobilityComponent(env, self, "MobilitySystem", **mobility_params) # 传入 self (agent)
                self.add_component(mobility_comp)
            # 添加其他组件...

            # 添加拥有的对象
            if charge_station_obj:
                self.add_possessing_object("my_station", charge_station_obj)

            # 设置完成后的最终状态
            self.update_state('status', 'idle')
    ```

3.  **实现钩子方法:**
    *   子类应该重写钩子方法（如 `_process_custom_logic`）而不是直接重写 `live()` 方法。
    *   `live()` 方法已经在基类中实现，它处理事件监听和任务调度的通用逻辑。
    *   在 `_process_custom_logic()` 内部，Agent 根据其当前 `self.state`、分配的工作流 (`self._get_active_workflows()`)、拥有对象的状态 (`self.get_state('object.state')`) 或其他内部逻辑来决定做什么。
    *   其他可重写的钩子方法包括 `_before_event_wait()` 和 `_check_agent_status()`。
    *   常见模式:
        *   等待一段时间: `yield self.env.timeout(duration)`
        *   等待事件: `yield self.get_event('some_event_name')` 或 `yield some_simpy_event`
        *   等待多个事件: `yield self.env.any_of([event1, event2])`
        *   执行任务: 调用 `self.execute_task(...)`。记住它会立即返回 `Task` 对象。
        *   监控任务完成: 使用 `task_completed` 事件或检查 `self.managed_tasks`。如果需要同步等待，可以 `yield` 监视器进程 (`self.managed_tasks[task_id]['process']`)，但通常首选通过事件进行异步处理。
        *   更新状态: 调用 `self.update_state(...)`。
        *   与拥有对象交互: `station = self.get_possessing_object('my_station')`，然后调用其方法或检查其状态。
        *   检查活动工作流: `workflows = self._get_active_workflows()` 并根据 `workflow.status_machine.current_status` 或 `workflow.get_details()` 调整行为。
    ```python
    class MyAgent(Agent, metaclass=MyAgentMeta):
        # ... __init__ ...

        def _process_custom_logic(self):
            """执行代理特定的逻辑"""
            current_status = self.get_state('status', 'idle')
            active_workflows = self._get_active_workflows() # 检查分配的目标

            if active_workflows:
                # 示例：优先处理工作流任务
                workflow = active_workflows[0] # 简化：处理第一个活动工作流
                print(f"时间 {self.env.now}: {self.name} 正在处理工作流 {workflow.id} (状态: {workflow.status_machine.state})")
                # --- 在此处添加工作流驱动的逻辑 ---
                # 例如，如果 workflow.status_machine.state == 'needs_movement':
                #    target = workflow.get_details().get('target_location')
                #    self.execute_task('MobilitySystem', 'Move', 'MoveToTask', properties={'target_position': target})
                #    self.update_state('status', 'moving_for_workflow')

            elif current_status == 'needs_action':
                print(f"时间 {self.env.now}: {self.name} 决定执行一个动作。")
                # 示例：使用 'MobilitySystem' 组件执行任务
                move_task = self.execute_task(
                    component_name='MobilitySystem',
                    task_name='Move to Target',
                    task_class='MoveToTask', # 使用字符串名称
                    properties={'target_position': (10, 20, 5)},
                    target_state={'position': (10, 20, 5)} # 任务的预期最终状态
                )

                if move_task:
                    print(f"时间 {self.env.now}: {self.name} 启动了移动任务 {move_task.id}。")
                    self.update_state('status', 'moving')
                else:
                    print(f"时间 {self.env.now}: {self.name} 启动移动任务失败。")
                    self.update_state('status', 'error')

            elif current_status == 'idle':
                print(f"时间 {self.env.now}: {self.name} 处于空闲状态。")
                # 可以在这里决定是否需要执行某些操作

        def register_event_listeners(self):
            """注册事件监听器"""
            # 获取基类注册的事件监听器
            listeners = super().register_event_listeners()

            # 添加自定义事件监听器
            listeners.extend([
                {
                    'source_id': '*',  # 监听所有源
                    'event_name': 'start_mission',  # 监听特定事件
                    'callback': self._on_start_mission  # 回调函数
                },
                {
                    'source_id': self.id,
                    'event_name': 'task_completed',
                    'callback': self._on_task_completed
                }
            ])

            return listeners

        def _on_start_mission(self, event_data):
            """响应开始任务事件"""
            print(f"时间 {self.env.now}: {self.name} 收到 start_mission 事件！")
            self.update_state('status', 'needs_action')

        def _on_task_completed(self, event_data):
            """响应任务完成事件"""
            task_id = event_data.get('task_id')
            print(f"时间 {self.env.now}: {self.name} 检测到任务 {task_id} 完成。")

            # 如果当前状态是 'moving'，则重置为 'idle'
            if self.get_state('status') == 'moving':
                self.update_state('status', 'idle')
    ```

### 4. LLM 集成 (示例: `DroneAgent`)

`Agent` 类本身与大型语言模型 (LLM) 无关，但子类可以轻松集成它们以进行智能决策，特别是任务规划。`DroneAgent` 演示了这一点：

1.  **LLM 客户端:** 在 `__init__` 期间传递 LLM 客户端（如 `openai.OpenAI`）并存储在 `self.llm_client` 中。
2.  **提示工程:** 一个方法为 LLM 构建详细的提示。此提示包括：
    *   Agent 的当前状态 (`self.get_current_states()`)。
    *   有关当前目标的信息（例如 `workflow.get_details()`, `workflow.status_machine.current_status`）。
    *   可用动作（组件：`self.get_component_names()`，Agent 已知的任务类）。
    *   所需的输出格式（例如，任务规范的 JSON 数组）。
3.  **LLM 调用:** 该方法调用 LLM API（例如 `self.llm_client.chat.completions.create(...)`）。
4.  **响应解析:** 一个方法从 LLM 的响应中提取并验证结构化的任务信息（例如 JSON）。它确保任务具有必需的字段（`component_name`, `task_name`, `task_class`, `properties` 等）。
5.  **集成到 `_process_custom_logic()`:** `_process_custom_logic()` 方法调用 LLM 分析函数。如果 LLM 提供有效的任务，Agent 将继续使用 `self.execute_task` 执行它们。如果 LLM 不可用或失败，Agent 可能会回退到更简单的预编程逻辑。

这种模式允许 Agent 利用复杂的规划能力，同时保持核心的模拟结构。

### 5. 关键要点和最佳实践

*   **继承自 `Agent`:** 它提供了核心结构。
*   **定义状态:** 使用 `StateTemplate` 通过元类或装饰器来提高清晰度和验证。在 `__init__` 中初始化所有必需的状态。
*   **实现 `__init__`:** 调用 `super().__init__`，初始化状态 (`initialize_states`)，添加组件 (`add_component`)，以及可能拥有的对象 (`add_possessing_object`)。
*   **实现 `live()`:** 这是 Agent 行为的核心。使用 `yield` 来表示时间流逝或事件等待。根据状态、事件和工作流来构建逻辑。
*   **使用 `execute_task`:** 通过组件使用 `task_class` 的字符串名称来启动动作。记住它是非阻塞的。处理返回的 `Task` 对象或 `None`。
*   **利用事件:** 使用 `trigger_event` 和 `subscribe` 进行通信和反应。`task_completed` 事件对于响应由 Agent 启动的已完成动作至关重要。根据需要订阅组件或拥有对象的事件。
*   **组件完成工作:** Agent 决定*做什么*和*何时做*，组件定义*如何做*。
*   **拥有对象:** 使用 `add_possessing_object` 将 Agent 链接到资源/位置，并通过复合键 (`get_state`) 访问它们的状态。
*   **保持 `live()` 可管理:** 将复杂的逻辑分解为辅助方法。清晰地处理不同的 Agent 状态和工作流状态。
*   **错误处理:** 检查 `execute_task` 的返回值。处理 `live()` 和回调中潜在的异常。

遵循这些指南，您可以在 AirFogSim 框架内有效地创建多样化且功能强大的 Agent。有关具体的实现细节，请参阅 `Agent` 源代码和特定的 Agent 示例（如 `DroneAgent`）。
