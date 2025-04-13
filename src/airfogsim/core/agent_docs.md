# Agent 类文档

`Agent` 类是 AirFogSim 中所有代理的基类，提供了状态管理、组件管理、任务管理、工作流管理、事件管理等核心功能。

## 类定义

```python
class Agent(metaclass=AgentMeta):
    """
    Agent 类是 AirFogSim 中所有代理的基类。
    
    Agent 提供了以下核心功能：
    1. 状态管理：管理代理的状态属性
    2. 组件管理：管理代理的组件
    3. 任务管理：管理代理的任务队列和执行
    4. 工作流管理：处理分配给代理的工作流
    5. 事件管理：注册、触发和处理事件
    6. 对象持有管理：管理代理拥有的外部对象
    7. 合约管理：创建和管理代理间的合约
    
    子类可以通过重写以下钩子方法来添加自定义行为：
    - register_event_listeners(): 注册自定义事件监听器
    - _before_event_wait(): 在等待事件之前执行
    - _check_agent_status(): 检查代理状态并决定是否继续处理
    - _process_custom_logic(): 执行代理特定的逻辑
    """
```

## 初始化

```python
def __init__(self, env, agent_name: str, properties: Optional[Dict] = None):
    """
    初始化代理。
    
    Args:
        env: 仿真环境
        agent_name: 代理名称
        properties: 代理属性字典
    """
```

## 状态管理

### 初始化状态

```python
def initialize_states(self, level_class=None, **states):
    """
    初始化代理状态。
    
    Args:
        level_class: 状态初始化的级别类
        **states: 状态键值对
    """
```

### 获取状态

```python
def get_state_templates(cls):
    """
    获取代理类的状态模板。
    
    Returns:
        Dict: 状态模板字典
    """
    
def get_current_states(self):
    """
    获取代理当前的所有状态。
    
    Returns:
        Dict: 当前状态字典
    """
    
def get_state(self, key, default=None):
    """
    获取指定键的状态值。
    
    Args:
        key: 状态键
        default: 如果状态不存在，返回的默认值
        
    Returns:
        Any: 状态值或默认值
    """
    
def has_state(self, key):
    """
    检查代理是否有指定键的状态。
    
    Args:
        key: 状态键
        
    Returns:
        bool: 如果状态存在，则返回 True，否则返回 False
    """
```

### 更新状态

```python
def update_state(self, key, value):
    """
    更新指定键的状态值。
    
    Args:
        key: 状态键
        value: 新的状态值
        
    Returns:
        bool: 如果更新成功，则返回 True，否则返回 False
    """
    
def update_states(self, state_dict):
    """
    批量更新状态。
    
    Args:
        state_dict: 状态键值对字典
        
    Returns:
        bool: 如果所有更新都成功，则返回 True，否则返回 False
    """
    
def set_state(self, key, value):
    """
    设置指定键的状态值（update_state 的别名）。
    
    Args:
        key: 状态键
        value: 新的状态值
        
    Returns:
        bool: 如果设置成功，则返回 True，否则返回 False
    """
```

## 组件管理

```python
def add_component(self, component):
    """
    添加组件到代理。
    
    Args:
        component: 要添加的组件
        
    Returns:
        bool: 如果添加成功，则返回 True，否则返回 False
    """
    
def get_component(self, component_name: str) -> Optional[Component]:
    """
    获取指定名称的组件。
    
    Args:
        component_name: 组件名称
        
    Returns:
        Optional[Component]: 如果找到组件，则返回组件对象，否则返回 None
    """
    
def get_components(self):
    """
    获取代理的所有组件。
    
    Returns:
        Dict[str, Component]: 组件名称到组件对象的映射
    """
    
def get_component_names(self) -> List[str]:
    """
    获取代理的所有组件名称。
    
    Returns:
        List[str]: 组件名称列表
    """
```

## 任务管理

```python
def add_task_to_queue(self, component_name: str, task_name: str, task_class: str,
                     properties: Dict = None, workflow_id: str = None,
                     priority: int = 0, preemptive: bool = False):
    """
    添加任务到队列。
    
    Args:
        component_name: 执行任务的组件名称
        task_name: 任务名称
        task_class: 任务类名
        properties: 任务属性
        workflow_id: 工作流 ID
        priority: 任务优先级
        preemptive: 是否可抢占
        
    Returns:
        str: 任务 ID
    """
    
def execute_task(self, component_name: str, task_name: str, task_class: str,
                properties: Dict = None, workflow_id: str = None,
                priority: int = 0, preemptive: bool = False):
    """
    直接执行任务（不经过队列）。
    
    Args:
        component_name: 执行任务的组件名称
        task_name: 任务名称
        task_class: 任务类名
        properties: 任务属性
        workflow_id: 工作流 ID
        priority: 任务优先级
        preemptive: 是否可抢占
        
    Returns:
        str: 任务 ID
    """
    
def cancel_task(self, task_id):
    """
    取消任务。
    
    Args:
        task_id: 任务 ID
        
    Returns:
        bool: 如果取消成功，则返回 True，否则返回 False
    """
    
def get_component_tasks(self, component_name):
    """
    获取指定组件的所有任务。
    
    Args:
        component_name: 组件名称
        
    Returns:
        List[Dict]: 任务信息列表
    """
```

## 工作流管理

```python
def _get_active_workflows(self):
    """
    获取分配给代理的活跃工作流。
    
    Returns:
        List: 活跃工作流列表
    """
```

## 事件管理

```python
def register_event(self, event_name):
    """
    注册事件。
    
    Args:
        event_name: 事件名称
    """
    
def has_event(self, event_name):
    """
    检查是否已注册事件。
    
    Args:
        event_name: 事件名称
        
    Returns:
        bool: 如果事件已注册，则返回 True，否则返回 False
    """
    
def get_event(self, event_name):
    """
    获取事件。
    
    Args:
        event_name: 事件名称
        
    Returns:
        Event: 事件对象
    """
    
def trigger_event(self, event_name, value=None):
    """
    触发事件。
    
    Args:
        event_name: 事件名称
        value: 事件值
    """
    
def register_event_listeners(self):
    """
    注册事件监听器。
    
    子类应该重写此方法来注册自定义事件监听器。
    
    Returns:
        List[Dict]: 事件监听器信息列表
    """
```

## 生命周期管理

```python
def live(self):
    """
    代理的主要行为逻辑。
    
    该方法实现了基本的事件监听和任务处理逻辑，包括：
    1. 初始化任务调度器
    2. 注册事件监听器
    3. 监听注册的事件
    4. 执行代理特定的行为
    
    子类可以通过重写以下方法来添加自定义行为：
    - register_event_listeners(): 注册自定义事件监听器
    - _before_event_wait(): 在等待事件之前执行
    - _check_agent_status(): 检查代理状态并决定是否继续处理
    - _process_custom_logic(): 执行代理特定的逻辑
    """
    
def _before_event_wait(self):
    """
    在等待事件之前执行的逻辑。
    
    子类可以重写此方法来添加自定义行为。
    """
    
def _check_agent_status(self):
    """
    检查代理状态并决定是否继续处理。
    
    子类可以重写此方法来添加自定义行为。
    
    Returns:
        bool: 如果代理状态允许继续处理，则返回 True，否则返回 False
    """
    
def _process_custom_logic(self):
    """
    执行代理特定的逻辑。
    
    子类应该重写此方法来添加自定义行为。
    """
    
def cleanup(self):
    """
    清理代理资源。
    
    在代理被销毁前调用此方法来清理资源。
    """
```

## 对象持有管理

```python
def add_possessing_object(self, object_name: str, obj: Any) -> bool:
    """
    添加代理拥有的对象。
    
    Args:
        object_name: 对象名称
        obj: 对象
        
    Returns:
        bool: 如果添加成功，则返回 True，否则返回 False
    """
    
def remove_possessing_object(self, object_name: str) -> bool:
    """
    移除代理拥有的对象。
    
    Args:
        object_name: 对象名称
        
    Returns:
        bool: 如果移除成功，则返回 True，否则返回 False
    """
    
def get_possessing_object(self, object_name: str) -> Optional[Any]:
    """
    获取代理拥有的对象。
    
    Args:
        object_name: 对象名称
        
    Returns:
        Optional[Any]: 如果找到对象，则返回对象，否则返回 None
    """
    
def get_possessing_object_names(self) -> List[str]:
    """
    获取代理拥有的所有对象名称。
    
    Returns:
        List[str]: 对象名称列表
    """
```

## 合约管理

```python
def create_contract(self, task_info, target_agent_ids, reward, penalty=0,
                   expiration_time=None, contract_type='task'):
    """
    创建合约。
    
    Args:
        task_info: 任务信息
        target_agent_ids: 目标代理 ID 列表
        reward: 奖励
        penalty: 惩罚
        expiration_time: 过期时间
        contract_type: 合约类型
        
    Returns:
        str: 合约 ID
    """
    
def accept_contract(self, contract_id):
    """
    接受合约。
    
    Args:
        contract_id: 合约 ID
        
    Returns:
        bool: 如果接受成功，则返回 True，否则返回 False
    """
    
def get_available_contracts(self):
    """
    获取可用的合约。
    
    Returns:
        List[Dict]: 合约信息列表
    """
    
def get_agent_contracts(self, role=None, status=None):
    """
    获取代理的合约。
    
    Args:
        role: 角色（'issuer' 或 'target'）
        status: 状态
        
    Returns:
        List[Dict]: 合约信息列表
    """
```

## 辅助函数

```python
def get_details(self):
    """
    获取代理详细信息。
    
    Returns:
        Dict: 代理详细信息
    """
```

## 内部辅助函数

这些函数主要供内部使用，通常不需要直接调用：

```python
def _get_attribute(self, obj, attr, default=None):
    """获取对象属性，如果不存在则返回默认值"""
    
def _has_attribute(self, obj, attr):
    """检查对象是否有指定属性"""
    
def _validate_required_templates(self, cls):
    """验证必需的状态模板"""
    
def _cancel_all_tasks(self):
    """取消所有任务"""
    
def _sort_task_queue(self):
    """按优先级排序任务队列"""
    
def _process_task_queue(self):
    """处理任务队列"""
    
def _execute_queued_task(self, task_info):
    """执行队列中的任务"""
    
def _try_preempt_task(self, task_info):
    """尝试抢占任务"""
    
def _task_scheduler(self):
    """任务调度器"""
    
def _monitor_task_execution(self, task: Task, component_exec_proc: simpy.Process):
    """监控任务执行"""
    
def _setup_data_provider_subscriptions(self):
    """设置数据提供者订阅"""
    
def _handle_workflow_assigned(self, event_data):
    """处理工作流分配事件"""
    
def _process_workflow_tasks(self):
    """处理工作流任务"""
```

## 元类方法

这些方法由 `AgentMeta` 元类提供，用于管理状态模板：

```python
@classmethod
def register_state_template(cls, key, **kwargs):
    """
    注册状态模板。
    
    Args:
        key: 状态键
        **kwargs: 状态模板参数
        
    Returns:
        cls: 类对象，用于链式调用
    """
    
@classmethod
def get_description(cls):
    """
    获取代理类型的描述。
    
    Returns:
        str: 代理类型描述
    """
```
