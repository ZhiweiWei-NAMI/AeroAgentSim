"""
AirFogSim组件管理器模块

该模块定义了组件管理器(ComponentManager)，负责管理和筛选不同类型的组件。
主要功能包括：
1. 根据代理状态和指标需求筛选合适的组件类
2. 为代理推荐最合适的组件类型
3. 管理组件类的注册和查询

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import uuid
from typing import Dict, List, Set, Type, Tuple, Optional, Any
from pathlib import Path
import pkgutil
import importlib
import inspect
from airfogsim.core.component import Component
from airfogsim.core.agent import Agent
from airfogsim.utils.logging_config import get_logger

# 获取logger
logger = get_logger(__name__)

class ComponentManager:
    """
    组件管理器：负责管理和筛选不同类型的组件

    主要功能：
    1. 根据代理状态和指标需求筛选合适的组件类
    2. 为代理推荐最合适的组件类型
    3. 管理组件类的注册和查询
    """

    def __init__(self, env):
        """
        初始化组件管理器

        Args:
            env: 仿真环境
        """
        self.env = env
        self.id = f"component_manager_{uuid.uuid4().hex[:8]}"
        self.registered_component_classes: Dict[str, Type[Component]] = {}  # 组件类型名称 -> 组件类
        self.component_metrics_map: Dict[str, Set[str]] = {}  # 组件类型名称 -> 产生的指标集合
        self.component_states_map: Dict[str, Set[str]] = {}  # 组件类型名称 -> 监控的状态集合

        # 确保事件存在
        self.env.event_registry.get_event(self.id, 'component_recommended')
        self.env.event_registry.get_event(self.id, 'component_selected')

        # 自动发现并注册组件类
        self._discover_component_classes()

    def _discover_component_classes(self):
        """自动发现并注册组件类"""
        # 获取airfogsim.component包的路径
        try:
            import airfogsim.component as component_pkg
            component_path = Path(component_pkg.__file__).parent

            # 遍历component目录下的所有模块
            for _, module_name, is_pkg in pkgutil.iter_modules([str(component_path)]):
                if is_pkg:
                    continue  # 跳过子包

                try:
                    # 导入模块
                    module = importlib.import_module(f"airfogsim.component.{module_name}")

                    # 查找模块中的所有Component子类
                    for name, obj in inspect.getmembers(module):
                        if (inspect.isclass(obj) and
                            issubclass(obj, Component) and
                            obj != Component and
                            obj.__module__ == module.__name__):

                            # 注册找到的组件类
                            self.register_component_class(obj)

                except (ImportError, AttributeError) as e:
                    logger.error(f"Error: 加载模块 {module_name} 时出错: {str(e)}")

        except ImportError:
            logger.error("Error: 无法导入airfogsim.component包")

    def register_component_class(self, component_class: Type[Component]) -> None:
        """
        注册组件类

        Args:
            component_class: 组件类
        """
        component_name = component_class.__name__
        self.registered_component_classes[component_name] = component_class

        # 收集组件类产生的指标和监控的状态
        self.component_metrics_map[component_name] = set(getattr(component_class, 'PRODUCED_METRICS', []))
        self.component_states_map[component_name] = set(getattr(component_class, 'MONITORED_STATES', []))

        logger.info(f"时间 {self.env.now}: 组件管理器注册组件类 {component_name}")

    def get_component_class(self, component_class_name: str) -> Optional[Type[Component]]:
        """
        获取组件类

        Args:
            component_class_name: 组件类名称

        Returns:
            组件类或None（如果未找到）
        """
        return self.registered_component_classes.get(component_class_name)

    def get_all_component_classes(self) -> List[Type[Component]]:
        """
        获取所有注册的组件类

        Returns:
            组件类列表
        """
        return list(self.registered_component_classes.values())

    def find_compatible_components(self, agent: Agent, required_metrics: List[str] = None) -> List[Tuple[Type[Component], float]]:
        """
        查找与代理状态兼容的组件类

        Args:
            agent: 代理
            required_metrics: 需要的指标列表（可选）

        Returns:
            兼容组件类及其兼容性分数的列表，按分数降序排序
        """
        # 获取代理支持的状态
        agent_states = set(agent.get_state_templates().keys())

        # 如果没有指定需要的指标，则默认为空列表
        if required_metrics is None:
            required_metrics = []
        required_metrics_set = set(required_metrics)

        compatible_components: List[Tuple[Type[Component], float]] = []

        for component_name, component_class in self.registered_component_classes.items():
            # 获取组件监控的状态
            monitored_states = self.component_states_map[component_name]

            # 检查代理是否支持组件监控的所有状态
            if not monitored_states.issubset(agent_states):
                continue  # 跳过不兼容的组件

            # 获取组件产生的指标
            produced_metrics = self.component_metrics_map[component_name]

            # 计算兼容性分数
            # 1. 状态匹配度：代理支持的状态与组件监控的状态的匹配程度
            states_score = len(monitored_states) / max(1, len(agent_states))

            # 2. 指标匹配度：组件产生的指标与需要的指标的匹配程度
            metrics_overlap = produced_metrics.intersection(required_metrics_set)
            metrics_score = len(metrics_overlap) / max(1, len(required_metrics_set)) if required_metrics_set else 0.5

            # 3. 综合分数：状态匹配度和指标匹配度的加权平均
            compatibility_score = 0.4 * states_score + 0.6 * metrics_score

            # 只有当产生的指标与所需指标有交集或没有指定所需指标时才考虑该组件
            if not required_metrics_set or metrics_overlap:
                compatible_components.append((component_class, compatibility_score))

        # 按兼容性分数降序排序
        compatible_components.sort(key=lambda x: x[1], reverse=True)
        return compatible_components

    def recommend_components(self, agent: Agent, required_metrics: List[str] = None, top_n: int = 3) -> List[Dict[str, Any]]:
        """
        为代理推荐最合适的组件

        Args:
            agent: 代理
            required_metrics: 需要的指标列表（可选）
            top_n: 返回的推荐组件数量

        Returns:
            推荐组件的详细信息列表
        """
        compatible_components = self.find_compatible_components(agent, required_metrics)

        # 限制返回数量
        top_components = compatible_components[:top_n]

        # 构建推荐结果
        recommendations = []
        for component_class, score in top_components:
            component_info = {
                'component_class': component_class.__name__,
                'compatibility_score': score,
                'produced_metrics': list(getattr(component_class, 'PRODUCED_METRICS', [])),
                'monitored_states': list(getattr(component_class, 'MONITORED_STATES', [])),
                'description': component_class.__doc__ or f"{component_class.__name__} 组件"
            }
            recommendations.append(component_info)

        # 触发组件推荐事件
        self.env.event_registry.trigger_event(self.id, 'component_recommended', {
            'agent_id': agent.id,
            'recommendations': recommendations,
            'time': self.env.now
        })

        return recommendations

    def create_component(self, component_class_name: str, agent: Agent,
                        name: Optional[str] = None,
                        supported_events: Optional[List[str]] = None,
                        properties: Optional[Dict] = None) -> Optional[Component]:
        """
        创建组件实例

        Args:
            component_class_name: 组件类名称
            agent: 代理
            name: 组件名称（可选）
            supported_events: 支持的事件列表（可选）
            properties: 组件属性（可选）

        Returns:
            创建的组件实例，如果创建失败则返回None
        """
        component_class = self.get_component_class(component_class_name)
        if not component_class:
            logger.info(f"时间 {self.env.now}: 找不到组件类 {component_class_name}")
            return None

        try:
            # 创建组件实例
            component = component_class(
                env=self.env,
                agent=agent,
                name=name,
                supported_events=supported_events or [],
                properties=properties or {}
            )

            # 触发组件选择事件
            self.env.event_registry.trigger_event(self.id, 'component_selected', {
                'agent_id': agent.id,
                'component_class': component_class_name,
                'component_name': component.name,
                'time': self.env.now
            })

            return component

        except Exception as e:
            logger.info(f"时间 {self.env.now}: 创建组件 {component_class_name} 实例时出错: {str(e)}")
            import traceback
            traceback.logger.info_exc()
            return None
