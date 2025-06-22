"""
AirFogSim代理管理器模块

该模块定义了代理管理器，负责管理和筛选不同类型的代理。
主要功能包括：
1. 根据状态需求筛选合适的代理类
2. 为工作流推荐最合适的代理类型
3. 管理代理类的注册和查询

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from typing import Dict, List, Type, Optional, Set, Tuple, Any
import uuid
import inspect
import importlib
import pkgutil
import os
import sys
from pathlib import Path
from airfogsim.utils.logging_config import get_logger

# 获取logger
logger = get_logger(__name__)

from airfogsim.core.agent import Agent, AgentMeta


class AgentManager:
    """
    代理管理器：负责管理和筛选不同类型的代理

    主要功能：
    1. 根据状态需求筛选合适的代理类
    2. 为工作流推荐最合适的代理类型
    3. 管理代理类的注册和查询
    """

    def __init__(self, env):
        """
        初始化代理管理器

        Args:
            env: 仿真环境
        """
        self.env = env
        self.id = f"agent_manager_{uuid.uuid4().hex[:8]}"
        self.registered_agent_classes: Dict[str, Type[Agent]] = {}  # 代理类型名称 -> 代理类
        self.agent_states_map: Dict[str, Set[str]] = {}  # 代理类型名称 -> 支持的状态集合

        # 确保事件存在
        self.env.event_registry.get_event(self.id, 'agent_recommended')
        self.env.event_registry.get_event(self.id, 'agent_selected')

        # 自动发现并注册代理类
        self._discover_agent_classes()

    def get_all_agents(self):
        return self.env.agents.values()

    def _discover_agent_classes(self):
        """自动发现并注册代理类"""
        # 获取airfogsim.agent包的路径
        try:
            import airfogsim.agent as agent_pkg
            agent_path = Path(agent_pkg.__file__).parent

            # 遍历agent目录下的所有模块
            for _, module_name, is_pkg in pkgutil.iter_modules([str(agent_path)]):
                if is_pkg:
                    continue  # 跳过子包

                try:
                    # 导入模块
                    module = importlib.import_module(f"airfogsim.agent.{module_name}")

                    # 查找模块中的所有Agent子类
                    for name, obj in inspect.getmembers(module):
                        if (inspect.isclass(obj) and
                            issubclass(obj, Agent) and
                            obj != Agent and
                            hasattr(obj, '__mro__') and
                            AgentMeta in [base.__class__ for base in obj.__mro__ if hasattr(base, '__class__')]):

                            # 注册找到的代理类
                            self.register_agent_class(obj)

                except (ImportError, AttributeError) as e:
                    logger.error(f"Error: 加载模块 {module_name} 时出错: {str(e)}")

        except ImportError:
            logger.error("Error: 无法导入airfogsim.agent包")

    def register_agent_class(self, agent_class: Type[Agent]) -> None:
        """
        注册代理类

        Args:
            agent_class: 代理类
        """
        agent_name = agent_class.__name__
        self.registered_agent_classes[agent_name] = agent_class

        # 收集代理类支持的状态
        states = set()
        for cls in agent_class.__mro__:
            if hasattr(cls, '_state_templates'):
                states.update(cls._state_templates.keys())

        self.agent_states_map[agent_name] = states
        logger.info(f"时间 {self.env.now}: 代理管理器注册代理类 {agent_name}")

    def get_agent_class(self, agent_class_name: str) -> Optional[Type[Agent]]:
        """
        获取代理类

        Args:
            agent_class_name: 代理类名称

        Returns:
            代理类或None（如果未找到）
        """
        return self.registered_agent_classes.get(agent_class_name)

    def get_all_agent_classes(self) -> List[Type[Agent]]:
        """
        获取所有注册的代理类

        Returns:
            代理类列表
        """
        return list(self.registered_agent_classes.values())

    def find_compatible_agents(self, required_states: List[str]) -> List[Tuple[Type[Agent], float]]:
        """
        查找支持指定状态的代理类

        Args:
            required_states: 需要的状态列表

        Returns:
            兼容代理类及其兼容性分数的列表，按分数降序排序
        """
        required_states_set = set(required_states)
        compatible_agents: List[Tuple[Type[Agent], float]] = []

        for agent_name, agent_class in self.registered_agent_classes.items():
            # 获取代理类支持的状态
            supported_states = self.agent_states_map[agent_name]

            # 检查代理类是否支持所有需要的状态
            if required_states_set.issubset(supported_states):
                # 计算兼容性分数：支持的状态与需要的状态的比例
                # 分数越高表示代理类越专注于所需的状态
                compatibility_score = len(required_states_set) / max(1, len(supported_states))
                compatible_agents.append((agent_class, compatibility_score))

        # 按兼容性分数降序排序
        compatible_agents.sort(key=lambda x: x[1], reverse=True)
        return compatible_agents

    def recommend_agents(self, required_states: List[str], top_n: int = 3) -> List[Dict[str, Any]]:
        """
        推荐支持指定状态的代理类

        Args:
            required_states: 需要的状态列表
            top_n: 返回的推荐代理数量

        Returns:
            推荐代理的详细信息列表
        """
        compatible_agents = self.find_compatible_agents(required_states)

        # 限制返回数量
        top_agents = compatible_agents[:top_n]

        # 构建推荐结果
        recommendations = []
        for agent_class, score in top_agents:
            agent_info = {
                'agent_class': agent_class.__name__,
                'compatibility_score': score,
                'supported_states': list(self.agent_states_map[agent_class.__name__]),
                'description': agent_class.get_description() if hasattr(agent_class, 'get_description') else "无描述"
            }
            recommendations.append(agent_info)

        # 触发推荐事件
        if recommendations:
            self.env.event_registry.trigger_event(self.id, 'agent_recommended', {
                'required_states': required_states,
                'recommendations': recommendations
            })

        return recommendations

    def create_agent(self, agent_class_name: str, agent_name: str, properties: Optional[Dict] = None) -> Optional[Agent]:
        """
        创建代理实例

        Args:
            agent_class_name: 代理类名称
            agent_name: 代理名称
            properties: 代理属性

        Returns:
            创建的代理实例，如果创建失败则返回None
        """
        agent_class = self.get_agent_class(agent_class_name)
        if not agent_class:
            logger.warning(f"时间 {self.env.now}: 找不到代理类 {agent_class_name}")
            return None

        try:
            # 创建代理实例
            agent = agent_class(self.env, agent_name, properties or {})

            # 触发代理选择事件
            self.env.event_registry.trigger_event(self.id, 'agent_selected', {
                'agent_class': agent_class_name,
                'agent_name': agent_name,
                'agent_id': agent.id
            })

            return agent

        except Exception as e:
            logger.error(f"Error: 创建代理 {agent_class_name} 实例时出错: {str(e)}")
            return None

    def get_agent_state_templates(self, agent_class_name: str) -> Dict[str, Dict]:
        """
        获取代理类的状态模板

        Args:
            agent_class_name: 代理类名称

        Returns:
            状态模板字典，如果找不到代理类则返回空字典
        """
        agent_class = self.get_agent_class(agent_class_name)
        if not agent_class:
            return {}

        # 收集代理类的状态模板
        templates = {}
        for cls in agent_class.__mro__:
            if hasattr(cls, '_state_templates'):
                templates.update(cls._state_templates)

        return templates
