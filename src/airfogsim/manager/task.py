from typing import Dict, List, Type, Optional, Set, Tuple, Any
from airfogsim.core.task import Task
from airfogsim.core.component import Component
from airfogsim.core.workflow import Workflow
from airfogsim.core.agent import Agent
from airfogsim.core.enums import TriggerType, TriggerOperator
from airfogsim.core.trigger import StateTrigger, EventTrigger
import uuid
from airfogsim.utils.logging_config import get_logger

# 获取logger
logger = get_logger(__name__)

class TaskManager:
    """
    任务管理器：负责根据代理当前的工作流和组件筛选合适的任务

    主要功能：
    1. 根据工作流需要的状态和组件提供的指标筛选合适的任务
    2. 为代理推荐最合适的任务
    3. 管理任务的注册和查询
    """

    def __init__(self, env):
        """
        初始化任务管理器

        Args:
            env: 仿真环境
        """
        self.env = env
        self.id = f"task_manager_{uuid.uuid4().hex[:8]}"
        self.registered_task_classes: Dict[str, Type[Task]] = {}  # 任务类型名称 -> 任务类
        self.task_metrics_map: Dict[str, Set[str]] = {}  # 任务类型名称 -> 所需指标集合
        self.task_states_map: Dict[str, Set[str]] = {}  # 任务类型名称 -> 产生的状态集合

        # 确保事件存在
        self.env.event_registry.get_event(self.id, 'task_recommended')
        self.env.event_registry.get_event(self.id, 'task_selected')

    def register_task_class(self, task_class: Type[Task]) -> None:
        """
        注册任务类

        Args:
            task_class: 任务类
        """
        task_name = task_class.__name__
        self.registered_task_classes[task_name] = task_class
        self.task_metrics_map[task_name] = set(task_class.NECESSARY_METRICS)
        self.task_states_map[task_name] = set(task_class.PRODUCED_STATES)
        logger.info(f"时间 {self.env.now}: 任务管理器注册任务类 {task_name}")

    def get_task_class(self, task_class_name: str) -> Optional[Type[Task]]:
        """
        获取任务类

        Args:
            task_class_name: 任务类名称

        Returns:
            任务类或None（如果未找到）
        """
        return self.registered_task_classes.get(task_class_name)

    def get_all_task_classes(self) -> List[Type[Task]]:
        """
        获取所有注册的任务类

        Returns:
            任务类列表
        """
        return list(self.registered_task_classes.values())

    def find_compatible_tasks(self,
                             agent: Agent,
                             workflow: Optional[Workflow] = None,
                             required_states: Optional[List[str]] = None) -> List[Tuple[Type[Task], float]]:
        """
        查找与代理组件和工作流兼容的任务

        Args:
            agent: 代理
            workflow: 工作流（可选）
            required_states: 需要的状态列表（可选，如果未提供则从工作流推断）

        Returns:
            兼容任务类及其兼容性分数的列表，按分数降序排序
        """
        # 获取代理的组件
        agent_components = agent.get_components()
        if not agent_components:
            logger.error(f"时间 {self.env.now}: 代理 {agent.id} 没有组件，无法执行任务")
            return []

        # 获取所有组件提供的指标
        available_metrics: Set[str] = set()
        for component in agent_components:
            available_metrics.update(component.PRODUCED_METRICS)

        # 确定所需的状态
        needed_states: Set[str] = set()
        if required_states:
            needed_states.update(required_states)
        elif workflow:
            # 从工作流状态机获取可能的下一个状态
            transitions = workflow.status_machine._get_current_transitions()
            for trigger, next_status in transitions:
                # 分析触发器，提取可能需要的状态
                if isinstance(trigger, StateTrigger):
                    # 处理状态触发器
                    if hasattr(trigger, 'agent_id') and trigger.agent_id == agent.id:
                        if hasattr(trigger, 'state_key') and trigger.state_key:
                            needed_states.add(trigger.state_key)

                elif isinstance(trigger, EventTrigger):
                    # 处理事件触发器
                    if hasattr(trigger, 'source_id') and trigger.source_id == agent.id:
                        if hasattr(trigger, 'value_key') and trigger.value_key:
                            # 处理嵌套键，如 'data.position'
                            parts = trigger.value_key.split('.')
                            if parts[0] in agent.get_state_templates():
                                needed_states.add(parts[0])

                # 处理自定义运算符
                if hasattr(trigger, 'operator') and trigger.operator == TriggerOperator.CUSTOM:
                    # 对于自定义运算符，我们无法确定具体需要哪些状态
                    # 可以考虑添加一些启发式规则，或者让任务开发者明确指定
                    pass

        # 如果没有明确的所需状态，则考虑所有可能的状态
        if not needed_states and workflow:
            needed_states = set(agent.get_state_templates().keys())

        # 评估每个任务类的兼容性
        compatible_tasks: List[Tuple[Type[Task], float]] = []

        for task_name, task_class in self.registered_task_classes.items():
            # 检查任务所需的指标是否可用
            required_metrics = self.task_metrics_map[task_name]
            if not required_metrics.issubset(available_metrics):
                continue  # 跳过不兼容的任务

            # 检查任务产生的状态是否满足需求
            produced_states = self.task_states_map[task_name]

            # 计算兼容性分数
            # 1. 指标匹配度：任务所需指标与可用指标的匹配程度
            metrics_score = len(required_metrics) / max(1, len(available_metrics))

            # 2. 状态匹配度：任务产生的状态与所需状态的匹配程度
            states_overlap = produced_states.intersection(needed_states)
            states_score = len(states_overlap) / max(1, len(needed_states)) if needed_states else 0.5

            # 3. 综合分数：指标匹配度和状态匹配度的加权平均
            compatibility_score = 0.4 * metrics_score + 0.6 * states_score

            # 只有当产生的状态与所需状态有交集时才考虑该任务
            if not needed_states or states_overlap:
                compatible_tasks.append((task_class, compatibility_score))

        # 按兼容性分数降序排序
        compatible_tasks.sort(key=lambda x: x[1], reverse=True)
        return compatible_tasks

    def recommend_tasks(self,
                       agent: Agent,
                       workflow: Optional[Workflow] = None,
                       required_states: Optional[List[str]] = None,
                       top_n: int = 3) -> List[Dict[str, Any]]:
        """
        为代理推荐最合适的任务

        Args:
            agent: 代理
            workflow: 工作流（可选）
            required_states: 需要的状态列表（可选）
            top_n: 返回的推荐任务数量

        Returns:
            推荐任务的详细信息列表
        """
        compatible_tasks = self.find_compatible_tasks(agent, workflow, required_states)

        # 限制返回数量
        top_tasks = compatible_tasks[:top_n]

        # 构建推荐结果
        recommendations = []
        for task_class, score in top_tasks:
            task_info = {
                'task_class': task_class.__name__,
                'compatibility_score': score,
                'required_metrics': list(task_class.NECESSARY_METRICS),
                'produced_states': list(task_class.PRODUCED_STATES),
                'description': task_class.__doc__ or "无描述"
            }
            recommendations.append(task_info)

        # 触发推荐事件
        if recommendations:
            self.env.event_registry.trigger_event(self.id, 'task_recommended', {
                'agent_id': agent.id,
                'workflow_id': workflow.id if workflow else None,
                'recommendations': recommendations,
                'time': self.env.now
            })

        return recommendations

    def create_task(self,
                   task_class_name: str,
                   agent: Agent,
                   component_name: str,
                   task_name: str,
                   workflow_id: Optional[str] = None,
                   target_state: Optional[Dict] = None,
                   properties: Optional[Dict] = None,
                   task_id: Optional[str] = None) -> Optional[Task]:

        """
        创建任务实例

        Args:
            task_class_name: 任务类名称
            agent: 代理
            component_name: 组件名称
            task_name: 任务名称
            workflow_id: 工作流ID（可选）
            target_state: 目标状态（可选）
            properties: 任务属性（可选）
            task_id: 任务ID（可选）

        Returns:
            创建的任务实例或None（如果创建失败）
        """
        task_class = self.get_task_class(task_class_name)
        if not task_class:
            logger.error(f"时间 {self.env.now}: 未找到任务类 {task_class_name}")
            return None

        components = agent.get_component_names()
        # 检查组件是否存在
        if component_name not in components:
            logger.error(f"时间 {self.env.now}: 代理 {agent.id} 没有组件 {component_name}")
            return None

        # 创建任务实例
        try:
            task = task_class(
                env=self.env,
                agent=agent,
                component_name=component_name,
                task_name=task_name,
                workflow_id=workflow_id,
                target_state=target_state,
                properties=properties
            )
            if task_id:
                task.id = task_id

            # 触发任务选择事件
            self.env.event_registry.trigger_event(self.id, 'task_selected', {
                'agent_id': agent.id,
                'task_id': task.id,
                'task_class': task_class_name,
                'workflow_id': workflow_id,
                'time': self.env.now
            })

            return task
        except Exception as e:
            logger.error(f"时间 {self.env.now}: 创建任务 {task_class_name} 失败: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return None
