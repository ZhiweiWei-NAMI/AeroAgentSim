"""
AirFogSim环境(Environment)核心模块

该模块定义了仿真系统的核心环境类，继承自SimPy的Environment类。
环境是整个仿真系统的容器，负责协调各种管理器、代理和资源的运行。
主要功能包括：
1. 管理仿真时间和事件流
2. 管理各种资源（空域、频率、着陆点等）
3. 注册和管理代理(Agent)
4. 创建和管理工作流(Workflow)
5. 创建和管理触发器(Trigger)
6. 提供数据存储和可视化更新

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

# -*- coding: utf-8 -*-
from __future__ import annotations # For forward type hints like 'DataProvider'
import uuid
from .agent import Agent
from .workflow import Workflow
import simpy
from airfogsim.manager.file import FileManager
from airfogsim.manager.airspace import AirspaceManager
from airfogsim.manager.frequency import FrequencyManager
from airfogsim.manager.landing import LandingManager
from airfogsim.manager.workflow import WorkflowManager
from airfogsim.manager.trigger import TriggerManager
from airfogsim.manager.payload import PayloadManager
from airfogsim.manager.task import TaskManager
from airfogsim.manager.contract import ContractManager
from airfogsim.manager.component import ComponentManager
from airfogsim.manager.agent import AgentManager
from airfogsim.core.enums import TriggerOperator
from .event import EventRegistry
from typing import Dict, Optional, Type, Tuple, Union, List, Any
from airfogsim.core.dataprovider import DataProvider # Import DataProvider
import airfogsim.task as airfogsim_task
import airfogsim.agent as airfogsim_agent
import airfogsim.component as airfogsim_component
import airfogsim.workflow as airfogsim_workflow
from airfogsim.utils.logging_config import get_logger

# 获取logger
logger = get_logger(__name__)

class Environment(simpy.Environment):
    def __init__(self, initial_time=0, visual_interval=5, logger=None, **kwargs):
        super().__init__(initial_time=initial_time)
        self.id = f"env_{uuid.uuid4().hex[:8]}"
        self.event_registry = EventRegistry(self, logger)

        # 初始化数据提供者存储
        self.data_providers: Dict[str, 'DataProvider'] = {} # Define before managers
        self.data = {} # Keep existing generic data store if needed

        # 初始化各种资源管理器
        self.airspace_manager = AirspaceManager(self)
        self.frequency_manager = FrequencyManager(self)
        self.landing_manager = LandingManager(self)
        self.file_manager = FileManager(self)

        self.workflow_manager = WorkflowManager(self)
        self.trigger_manager = TriggerManager(self)
        self.task_manager = TaskManager(self)
        self.component_manager = ComponentManager(self)
        self.agent_manager = AgentManager(self)
        self.payload_manager = PayloadManager(self)
        self.contract_manager = ContractManager(self)
        self.agents: Dict[str, 'Agent'] = {}

        self.visual_interval = float(visual_interval) if visual_interval is not None else 0 # s
        if self.visual_interval > 0:
             self.event_registry.get_event(self.id, 'visual_update')
             # Run loop only if interval > 0
             self.process(self._visual_update_loop(self.visual_interval))

        # 注册所有类
        self._register_tasks(kwargs.get('task_classes', []))
        self._register_all_classes()

    @property
    def now(self):
        # 精度为:.3
        return round(super().now, 3)

    def _register_tasks(self, task_classes):
        if not task_classes:
            airfogsim_task.register_all_tasks(self.task_manager)
            return
        for task_class in task_classes:
            self.task_manager.register_task_class(task_class)

    def _register_all_classes(self):
        """注册所有任务、代理、组件和工作流类"""
        # 注册任务类
        task_count = airfogsim_task.register_all_tasks(self.task_manager)
        logger.info(f"时间 {self.now}: 注册了 {task_count} 个任务类")

        # 注册代理类
        agent_count = airfogsim_agent.register_all_agents(self.agent_manager)
        logger.info(f"时间 {self.now}: 注册了 {agent_count} 个代理类")

        # 注册组件类
        component_count = airfogsim_component.register_all_components(self.component_manager)
        logger.info(f"时间 {self.now}: 注册了 {component_count} 个组件类")

        # 注册工作流类（仅记录可用的工作流类，不实际注册实例）
        workflow_count = airfogsim_workflow.register_all_workflows(self.workflow_manager)
        logger.info(f"时间 {self.now}: 发现了 {workflow_count} 个工作流类")

    def _visual_update_loop(self, interval_sec):
        while True:
            yield self.timeout(interval_sec)
            self.event_registry.trigger_event(self.id, 'visual_update', {'time': self.now})

    def _get_agent_by_id(self, agent_id):
        return self.agents.get(agent_id)

    def register_agent(self, agent: 'Agent'):
        self.agents[agent.id] = agent

        # 如果代理有位置信息，注
        if hasattr(self, 'airspace_manager') and hasattr(agent, 'state'):
            position = agent.get_state('position')
            if position:
                # 确保位置是三维的
                if len(position) == 2:
                    position = (position[0], position[1], 0)
                self.airspace_manager.register_agent(agent.id, position)

                # 订阅代理的状态变化事件，以便在位置变化时更新空域管理器
                self.event_registry.subscribe(
                    agent.id,
                    'state_changed',
                    f"{agent.id}_position_tracker",
                    self._handle_agent_position_change
                ).add_source_filter(lambda event_data: 'position'==event_data.get('key', {}))

        return self

    def _handle_agent_position_change(self, event_data):
        """
        处理代理位置变化事件，更新空域管理器中的代理位置事件数据，包含key, old_value, new_value和time
        """
        if not event_data or 'key' not in event_data or event_data['key'] != 'position':
            return

        agent_id = event_data.get('source_id')
        if not agent_id:
            return

        position = event_data.get('new_value')
        if not position:
            return

        # 确保位置是三维的
        if len(position) == 2:
            position = (position[0], position[1], 0)

        # 更新空域管理器中的代理位置
        if hasattr(self, 'airspace_manager'):
            self.airspace_manager.update_object_position(position, agent_id=agent_id)

    def get_agent(self, agent_id):
        return self.agents.get(agent_id)

    def get_all_agents(self):
        return list(self.agents.values()) # Return list of agent objects

    def add_data_provider(self, key: str, provider: 'DataProvider'):
        """
        Registers a DataProvider instance with the environment.

        Args:
            key (str): A unique key to identify the provider (e.g., 'weather', 'traffic').
            provider (DataProvider): The DataProvider instance to register.
        """
        if key in self.data_providers:
            logger.warning(f"DataProvider with key '{key}' already exists. Overwriting.")
        self.data_providers[key] = provider
        logger.info(f"DataProvider '{key}' ({provider.__class__.__name__}) registered.")

    def get_data_provider(self, key: str) -> Optional['DataProvider']:
        """
        Retrieves a registered DataProvider instance by its key.

        Args:
            key (str): The key of the DataProvider to retrieve.

        Returns:
            Optional[DataProvider]: The DataProvider instance, or None if not found.
        """
        return self.data_providers.get(key)

    def store_data(self, key, value):
        self.data[key] = value

    def get_data(self, key, default=None):
        return self.data.get(key, default)

    from .trigger import Trigger
    def create_workflow(self, workflow_class: Type['Workflow'], name: str, owner: Optional['Agent'] = None,
                          start_trigger: Optional[Union[Tuple[str, str], 'Trigger']] = None, max_starts: int = 1,
                          **kwargs) -> 'Workflow':
        kwargs['env'] = self
        kwargs['name'] = name
        kwargs['owner'] = owner
        workflow = workflow_class(**kwargs)
        self.workflow_manager.register_workflow(workflow, start_trigger, max_starts)
        return workflow

    def create_agent(self, agent_class: Type['Agent'], agent_name: str, **kwargs) -> 'Agent':
        #  直接把kwards变成properties
        properties = kwargs.pop('properties', {})
        agent = agent_class(env=self, agent_name=agent_name, properties=properties)
        self.register_agent(agent)
        return agent

    # 触发器相关方法
    def create_event_trigger(self, source_id: str, event_name: str,
                            value_key: Optional[str] = None,
                            operator: Optional[TriggerOperator] = None,
                            target_value: Any = None,
                            name: Optional[str] = None):
        return self.trigger_manager.create_event_trigger(source_id, event_name, value_key, operator, target_value, name)

    def create_state_trigger(self, agent_id: str, state_key: str,
                            operator: TriggerOperator = TriggerOperator.EQUALS,
                            target_value: Any = None,
                            name: Optional[str] = None):
        return self.trigger_manager.create_state_trigger(agent_id, state_key, operator, target_value, name)

    def create_time_trigger(self, trigger_time: Optional[float] = None,
                          interval: Optional[float] = None,
                          cron_expr: Optional[str] = None,
                          name: Optional[str] = None):
        return self.trigger_manager.create_time_trigger(trigger_time, interval, cron_expr, name)

    def create_composite_trigger(self, triggers: List[Trigger],
                               operator_type: str = "and",
                               name: Optional[str] = None):
        from airfogsim.manager.trigger import TriggerOperator
        operator = TriggerOperator.AND if operator_type.lower() == "and" else TriggerOperator.OR
        return self.trigger_manager.create_composite_trigger(triggers, operator, name)