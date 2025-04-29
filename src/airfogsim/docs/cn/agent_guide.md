## AirFogSim 代理开发者文档

本文档提供了理解和扩展 AirFogSim 框架中 `airfogsim.core.agent.Agent` 类的指导。它解释了核心概念、如何定义自定义代理类型以及如何实现它们的行为。

### 1. `Agent` 类概述

`Agent` 类代表仿真环境（`simpy.Environment`）中的一个主动的、具有决策能力的实体。代理是与模拟世界交互的主要角色，通过以下方式：

1.  **维护状态：** 使用结构化的状态管理系统跟踪内部属性（例如，位置、状态）以及可能拥有的对象的属性（例如，充电站状态）。
2.  **拥有组件：** 利用功能单元（`Component`）如传感器、执行器（例如，移动系统）或计算资源来执行操作。
3.  **执行任务：** 创建 `Task` 对象并将其执行委托给适当的组件。
4.  **对事件做出反应：** 通过发布-订阅事件系统对环境或自身内部状态的变化做出响应。
5.  **管理拥有的对象：** 持有对其他仿真对象（如充电站、着陆点）的引用并访问它们的状态。
6.  **与合约交互：** （可选）通过 `ContractManager` 创建、接受和管理任务外包合约。
7.  **定义行为：** 通过重写钩子方法（如 `_process_custom_logic`）而不是直接重写 `live()` 方法来实现核心决策逻辑。

基础 `Agent` 类为这些功能提供了基础结构和机制。子类定义特定的代理类型（如 `DroneAgent`、`GroundStationAgent` 等），通过添加专门的状态、组件、拥有的对象和行为逻辑。

### 2. 核心概念

#### 2.1 状态管理（`StateTemplate`、`AgentMeta`）

*   **目的：** 代理使用 `self.state` 字典跟踪其属性。为确保一致性、类型安全和定义需求，框架使用 `StateTemplate`。代理还可以使用复合键（例如，`'charging_station.status'`）访问它们拥有的对象的状态。
*   **`StateTemplate`：** 定义状态变量的预期属性：
    *   `key`（str）：状态变量的名称。
    *   `value_type`（type，可选）：预期的 Python 类型（例如，`int`、`float`、`str`、`tuple`、`List[int]`）。`None` 允许任何类型。支持基本泛型如 `List`。
    *   `required`（bool）：此状态是否*必须*初始化。如果缺少则发出警告。
    *   `validator`（callable，可选）：一个接受值并返回 `True`（如果有效）或 `False`（如果无效）的函数。
    *   `description`（str，可选）：人类可读的描述。
*   **`AgentMeta`（元类）：** 这个元类自动处理类层次结构中 `StateTemplate` 定义的继承和聚合。每个代理实例根据其整个类层次结构的*组合*模板验证其状态更新。
*   **注册模板：**
    *   **推荐（通过元类）：** 定义一个继承自 `AgentMeta` 的自定义元类（如 `DroneAgentMeta`），并在其 `__new__` 方法中调用 `mcs.register_template(cls, ...)`。这对于组织特定类型的状态更清晰。
    *   **直接在类上：** 直接在代理子类上使用 `@classmethod` 装饰器 `register_state_template`。
        ```python
        # 使用类装饰器的示例
        @Agent.register_state_template('position', value_type=tuple, required=True, description="代理的 3D 位置")
        @Agent.register_state_template('status', value_type=str, required=True, default='idle')
        class MySimpleAgent(Agent):
            # ... 代理实现 ...
            pass

        # 使用元类的示例（推荐用于复杂代理）
        class DroneAgentMeta(AgentMeta):
            def __new__(mcs, name, bases, attrs):
                cls = super().__new__(mcs, name, bases, attrs)
                mcs.register_template(cls, 'battery_level', float, True, validator=lambda x: 0.0 <= x <= 100.0)
                mcs.register_template(cls, 'flight_mode', str, False, default='manual')
                # ... 其他无人机特定状态
                return cls

        class DroneAgent(Agent, metaclass=DroneAgentMeta):
            # ... 代理实现 ...
            pass
        ```
*   **使用状态：**
    *   `initialize_states(**states)`：在 `__init__` 期间设置初始状态值。检查必需的状态。
    *   `update_state(key, value)` / `set_state(key, value)`：更新单个状态值。如果值发生变化，触发验证和 `state_changed` 事件。
    *   `update_states(state_dict)`：更新多个状态。
    *   `get_state(key, default=None)`：检索状态值。支持复合键（例如，`agent.get_state('my_station.is_available')`）。
    *   `has_state(key)`：检查状态（包括复合键）是否存在。
    *   `get_current_states()`：获取代理自身状态字典的副本（不包括拥有的对象状态）。
    *   `get_state_templates()`：（类方法）获取类的所有适用模板。

#### 2.2 组件管理

*   **目的：** 组件（`airfogsim.core.Component`）代表代理的功能部分（例如，'MobilitySystem'、'CPUEngine'、'CameraSensor'）。代理将任务执行委托给其组件。
*   **用法：**
    *   `add_component(component)`：将 `Component` 实例附加到代理。组件获得对代理的引用（`component.agent`）。代理检查它是否为组件的 `MONITORED_STATES` 定义了必要的状态模板。
    *   `get_component(component_name)`：通过名称检索组件。
    *   `get_components()`：获取所有附加组件实例的列表。
    *   `get_component_names()`：列出所有附加组件的名称。
*   **示例：** 代理需要 `MobilityComponent` 来移动和 `ComputeComponent` 来处理数据。这些将在初始化或配置期间添加。

#### 2.3 事件处理

*   **目的：** 代理可以使用由 `env.event_registry` 管理的发布-订阅系统对重要事件做出反应并宣布它们。
*   **标准代理事件：** 基础 `Agent` 自动注册并触发：
    *   `state_changed`：当 `update_state` 更改值时触发（对于代理自身的状态或通过拥有的对象间接触发）。事件数据包括 `key`、`old_value`、`new_value`、`time` 以及可能的 `object_name`。
    *   `task_started`：当 `execute_task` 成功启动任务时由代理触发。
    *   `task_completed`：当代理启动的任务完成（成功、失败或取消）时由代理的内部监视器（`_monitor_task_execution`）触发。
    *   `possessing_object_added` / `possessing_object_removed`：当通过 `add_possessing_object` / `remove_possessing_object` 添加/移除对象时触发。
    *   *（注意：特定于组件的事件如 `ComponentName.task_completed` 由组件本身触发，但代理可以订阅它们）。*
*   **用法：**
    *   `register_event(event_name)`：声明此代理可能触发的事件。
    *   `trigger_event(event_name, value=None)`：触发事件，通知所有订阅者。`agent_id` 自动添加到值中。
    *   `subscribe(source_id, event_name, callback, listener_id=None)`：注册一个 `callback` 函数，当 `source_id` 触发 `event_name` 时调用。
    *   `unsubscribe(source_id, event_name, listener_id)`：停止监听事件。
    *   `unsubscribe_all()`：移除*由*此代理*做出的*所有订阅。
    *   `get_event(event_name)`：获取此代理事件的底层 `simpy.Event` 对象（对于 `yield` 有用）。
    *   `has_event(event_name)`：检查代理是否注册了特定事件。

#### 2.4 任务执行

*   **目的：** 代理决定*需要做什么*以及*哪个组件*应该做。它们创建 `Task` 对象（通过 `env.task_manager`）并要求组件执行它们。
*   **`Task` 对象：** 代表具有名称、状态、开始/结束时间、关联工作流等属性的工作单元。特定任务类型（例如，`MoveToTask`、`ComputeTask`）继承自 `airfogsim.core.Task`。
*   **`execute_task(...)` 方法：**
    *   这是代理启动工作的主要方式。
    *   它接受 `component_name`、`task_name`、`task_class`（Task 子类的字符串名称）、`target_state`、任务的 `properties`、可选的 `workflow_id` 和可选的 `task_id`。
    *   它使用 `env.task_manager.create_task` 实例化 `Task`。
    *   **关键是，它是非阻塞的。** 它触发 `task_started` 事件，要求组件开始执行（`component.execute_task(task)`），启动内部监控进程（`_monitor_task_execution`），并*立即*返回 `Task` 对象。
    *   如果找不到组件，则返回 `None`。
*   **`_monitor_task_execution(...)` （内部）：**
    *   一个 SimPy 进程，`yield` 组件的执行进程（`component_exec_proc`）。
    *   处理组件执行任务的完成、失败或中断。
    *   根据结果更新 `Task` 状态/结果（主要依赖于 Task 对象的最终状态）。
    *   触发代理的 `task_completed` 事件。
    *   管理 `self.managed_tasks` 字典（跟踪由代理启动的活动任务）。
*   **`cancel_task(task_id)`：**
    *   尝试中断与任务相关的运行进程，包括组件内（`component.task_processes`）和代理的监控器。
    *   将任务状态更新为 `CANCELED`。
    *   触发 `ComponentName.task_canceled` 事件。
*   **工作流：** 代理的 `live()` 方法通常根据其状态、目标、分配的工作流或传入事件决定*何时*和*执行哪些*任务。

#### 2.5 拥有对象管理

*   **目的：** 允许代理持有对其他仿真实体（例如，无人机拥有特定的充电站或着陆点）的引用并与其状态交互。
*   **用法：**
    *   `add_possessing_object(object_name, obj)`：在给定的 `object_name` 下存储 `obj`。自动订阅对象的 `state_changed` 事件（如果对象有 `id`），并将这些变化作为代理 `state_changed` 事件转发，使用复合键（例如，带有键 `'my_station.status'` 的 `state_changed`）。触发 `possessing_object_added`。
    *   `remove_possessing_object(object_name)`：移除对象并取消订阅其事件。触发 `possessing_object_removed`。
    *   `get_possessing_object(object_name)`：检索拥有的对象实例。
    *   `get_possessing_object_names()`：列出所有拥有的对象的名称。
    *   `get_state(f'{object_name}.attribute_name')`：使用点表示法访问拥有的对象的状态。

#### 2.6 合约管理（可选）

*   **目的：** 为代理提供使用中央 `ContractManager`（如果在环境中可用）参与任务外包的接口。
*   **用法（需要 `env.contract_manager`）：**
    *   `create_contract(task_info, target_agent_ids, reward, ...)`：创建新的任务外包合约。
    *   `accept_contract(contract_id)`：接受提供给此代理的合约。
    *   `get_available_contracts()`：查找分配给此代理的待接受合约。
    *   `get_agent_contracts(role=None, status=None)`：检索此代理作为发行者或执行者的合约，可选按状态过滤。

### 3. 实现自定义代理子类

按照以下步骤创建您自己的代理类型：

1.  **定义类：**
    *   继承自 `Agent`。
    *   （推荐）创建继承自 `AgentMeta` 的伴随元类，或使用 `@Agent.register_state_template` 装饰器注册特定类型的状态。
    ```python
    from airfogsim.core.agent import Agent, AgentMeta, StateTemplate
    from typing import Tuple, List # 等

    # 定义元类（可选但对状态是良好实践）
    class MyAgentMeta(AgentMeta):
        def __new__(mcs, name, bases, attrs):
            cls = super().__new__(mcs, name, bases, attrs)
            # 注册特定于 MyAgent 的状态
            mcs.register_template(cls, 'my_custom_state', str, True, description="一个必需的自定义状态")
            mcs.register_template(cls, 'optional_counter', int, False, validator=lambda x: x >= 0)
            return cls

    # 定义 Agent 子类
    class MyAgent(Agent, metaclass=MyAgentMeta):
        # 如果在基础 AgentMeta 中未完成，则注册所有代理通用的基础状态
        # AgentMeta.register_template(cls, 'status', str, True, default='idle') # 示例

        # ... 实现 ...
    ```

2.  **实现 `__init__`：**
    *   调用 `super().__init__(env, agent_name, properties)`。
    *   添加任何自定义实例变量。
    *   **关键：** 使用 `self.initialize_states(...)`，为其类层次结构中定义的所有 `required=True` 状态提供值，初始化代理的状态。
    *   使用 `self.add_component(...)` 添加必要的组件。组件可能被传入或在此创建。
    *   （可选）使用 `self.add_possessing_object(...)` 添加拥有的对象。
    ```python
    from airfogsim.component.mobility import MobilityComponent # 示例组件
    from airfogsim.resource import ChargingStation # 示例拥有的对象

    class MyAgent(Agent, metaclass=MyAgentMeta):
        def __init__(self, env, agent_name: str, initial_custom_state: str, properties=None, mobility_params=None, charge_station_obj=None):
            super().__init__(env, agent_name, properties)

            # 添加自定义属性
            self.my_internal_tracker = 0

            # 初始化状态（必须包括必需的状态）
            self.initialize_states(
                my_custom_state=initial_custom_state,
                optional_counter=0,
                status='initializing' # 示例基础状态
            )

            # 添加组件
            if mobility_params:
                mobility_comp = MobilityComponent(env, self, "MobilitySystem", **mobility_params) # 传递 self（代理）
                self.add_component(mobility_comp)
            # 添加其他组件...

            # 添加拥有的对象
            if charge_station_obj:
                self.add_possessing_object("my_station", charge_station_obj)

            # 设置完成后的最终状态
            self.update_state('status', 'idle')
    ```

3.  **实现钩子方法：**
    *   子类应该重写钩子方法（如 `_process_custom_logic`）而不是直接重写 `live()` 方法。
    *   `live()` 方法已在基类中实现，处理事件监听和任务调度的通用逻辑。
    *   在 `_process_custom_logic()` 内部，代理根据其当前 `self.state`、分配的工作流（`self.get_active_workflows()`）、拥有的对象状态（`self.get_state('object.state')`）或其他内部逻辑决定做什么。
    *   其他可重写的钩子方法包括 `_before_event_wait()` 和 `_check_agent_status()`。
    *   常见模式：
        *   等待一段时间：`yield self.env.timeout(duration)`
        *   等待事件：`yield self.get_event('some_event_name')` 或 `yield some_simpy_event`
        *   等待多个事件：`yield self.env.any_of([event1, event2])`
        *   执行任务：调用 `self.execute_task(...)`。记住它立即返回 `Task` 对象。
        *   监控任务完成：使用 `task_completed` 事件或检查 `self.managed_tasks`。如果需要同步等待，可以 `yield` 监控进程（`self.managed_tasks[task_id]['process']`），但通常通过事件进行异步处理更好。
        *   更新状态：调用 `self.update_state(...)`。
        *   与拥有的对象交互：`station = self.get_possessing_object('my_station')`，然后调用其方法或检查其状态。
        *   检查活动工作流：`workflows = self.get_active_workflows()` 并根据 `workflow.status_machine.current_status` 或 `workflow.get_details()` 调整行为。
    ```python
    class MyAgent(Agent, metaclass=MyAgentMeta):
        # ... __init__ ...

        def _process_custom_logic(self):
            """执行代理特定的逻辑"""
            current_status = self.get_state('status', 'idle')
            active_workflows = self.get_active_workflows() # 检查分配的目标

            if active_workflows:
                # 示例：优先处理工作流任务
                workflow = active_workflows[0] # 简单化：处理第一个活动工作流
                print(f"时间 {self.env.now}: {self.name} 正在处理工作流 {workflow.id} (状态: {workflow.status_machine.state})")
                # --- 在此添加工作流驱动的逻辑 ---
                # 例如，if workflow.status_machine.state == 'needs_movement':
                #    target = workflow.get_details().get('target_location')
                #    self.execute_task('MobilitySystem', 'Move', 'MoveToTask', properties={'target_position': target})
                #    self.update_state('status', 'moving_for_workflow')

            elif current_status == 'needs_action':
                print(f"时间 {self.env.now}: {self.name} 决定执行一个动作。")
                # 示例：使用 'MobilitySystem' 组件执行任务
                move_task = self.execute_task(
                    component_name='MobilitySystem',
                    task_name='移动到目标',
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
                # 可以在此决定是否需要采取任何行动

        def register_event_listeners(self):
            """注册事件监听器"""
            # 获取基类注册的监听器
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

            # 如果当前状态是 'moving'，重置为 'idle'
            if self.get_state('status') == 'moving':
                self.update_state('status', 'idle')
    ```

### 4. LLM 集成（示例：`DroneAgent`）

`Agent` 类本身对大型语言模型（LLM）是不可知的，但子类可以轻松集成它们以进行智能决策，特别是任务规划。`DroneAgent` 演示了这一点：

1.  **LLM 客户端：** 在 `__init__` 期间传递 LLM 客户端（如 `openai.OpenAI`）并存储在 `self.llm_client` 中。
2.  **提示工程：** 一个方法为 LLM 构建详细的提示。这个提示包括：
    *   代理的当前状态（`self.get_current_states()`）。
    *   关于当前目标的信息（例如，`workflow.get_details()`，`workflow.status_machine.current_status`）。
    *   可用的操作（组件：`self.get_component_names()`，代理已知的任务类）。
    *   所需的输出格式（例如，任务规范的 JSON 数组）。
3.  **LLM 调用：** 该方法调用 LLM API（例如，`self.llm_client.chat.completions.create(...)`）。
4.  **响应解析：** 一个方法从 LLM 的响应中提取并验证结构化任务信息（例如，JSON）。它确保任务具有所需的字段（`component_name`、`task_name`、`task_class`、`properties` 等）。
5.  **集成到 `_process_custom_logic()` 中：** `_process_custom_logic()` 方法调用 LLM 分析函数。如果 LLM 提供有效的任务，代理继续使用 `self.execute_task` 执行它们。如果 LLM 不可用或失败，代理可能会回退到更简单的、预编程的逻辑。

这种模式允许代理利用复杂的规划能力，同时保持核心仿真结构。

### 5. 关键要点和最佳实践

*   **继承自 `Agent`：** 它提供了核心结构。
*   **定义状态：** 使用 `StateTemplate`（通过元类或装饰器）以获得清晰性和验证。在 `__init__` 中初始化所有必需的状态。
*   **实现 `__init__`：** 调用 `super().__init__`，初始化状态（`initialize_states`），添加组件（`add_component`），可能还有拥有的对象（`add_possessing_object`）。
*   **实现钩子方法：** 重写 `_process_custom_logic()` 方法来实现代理特定的逻辑，而不是重写 `live()` 方法。其他可重写的钩子方法包括 `_before_event_wait()` 和 `_check_agent_status()`。
*   **使用 `execute_task`：** 通过组件使用 `task_class` 的字符串名称启动操作。记住它是非阻塞的。处理返回的 `Task` 对象或 `None`。
*   **利用事件：** 使用 `trigger_event` 和 `subscribe` 进行通信和反应。`task_completed` 事件对于对代理启动的已完成操作做出反应至关重要。根据需要订阅组件或拥有的对象事件。
*   **组件完成工作：** 代理决定*什么*和*何时*，组件定义*如何*。
*   **拥有的对象：** 使用 `add_possessing_object` 将代理链接到资源/位置，并通过复合键（`get_state`）访问它们的状态。
*   **保持代码可管理：** 将复杂逻辑分解为辅助方法。清晰地处理不同的代理状态和工作流状态。
*   **错误处理：** 检查 `execute_task` 的返回值。处理 `_process_custom_logic()` 和回调中的潜在异常。

通过遵循这些指南，您可以在 AirFogSim 框架内有效地创建多样化和能力强大的代理。参考 `Agent` 源代码和特定代理示例（如 `DroneAgent`）以获取具体的实现细节。

### 6. 日志记录

日志记录是 Agent 开发中至关重要的一部分，它帮助我们：
- 追踪 Agent 的行为和状态变化
- 调试和排查问题
- 分析性能瓶颈
- 监控系统运行状况

#### 6.1 日志级别

AirFogSim 使用 Python 的 `logging` 模块，支持以下日志级别：
- DEBUG：详细的调试信息
- INFO：一般信息，记录正常运行状态
- WARNING：警告信息，表示潜在问题
- ERROR：错误信息，表示严重问题
- CRITICAL：严重错误，可能导致系统崩溃

#### 6.2 日志格式

建议使用以下格式记录日志：
```python
import logging

logger = logging.getLogger(__name__)

# 在 __init__ 中初始化日志
def __init__(self, env, name, **kwargs):
    super().__init__(env, name, **kwargs)
    self.logger = logging.getLogger(f"{__name__}.{self.name}")
    
# 记录日志示例
self.logger.info(f"Agent {self.name} 开始执行任务 {task_id}")
self.logger.debug(f"当前状态: {self.get_current_states()}")
self.logger.warning(f"资源不足: {resource_name}")
self.logger.error(f"任务执行失败: {error_message}")
```

#### 6.3 日志记录最佳实践

1. **状态变化记录**
   - 记录重要的状态转换
   - 记录状态更新前后的值
   - 记录状态变化的原因

2. **任务执行记录**
   - 记录任务的开始和结束
   - 记录任务执行的关键步骤
   - 记录任务执行的结果和异常

3. **事件处理记录**
   - 记录接收到的事件
   - 记录事件处理的结果
   - 记录事件触发的动作

4. **性能监控记录**
   - 记录关键操作的执行时间
   - 记录资源使用情况
   - 记录性能瓶颈

5. **错误处理记录**
   - 记录异常发生的上下文
   - 记录错误恢复的过程
   - 记录错误对系统的影响

#### 6.4 日志配置

在 `config.yaml` 中配置日志：
```yaml
logging:
  level: INFO
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
  handlers:
    console:
      level: INFO
    file:
      level: DEBUG
      filename: "airfogsim.log"
```

#### 6.5 日志分析

日志分析可以帮助我们：
- 识别性能瓶颈
- 发现异常模式
- 优化系统行为
- 改进决策逻辑

建议定期分析日志，提取有价值的信息，用于系统优化和改进。
