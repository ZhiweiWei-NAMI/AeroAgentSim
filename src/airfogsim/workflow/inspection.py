"""
AirFogSim巡检工作流模块

该模块定义了无人机巡检工作流及其元类，实现了无人机按顺序访问多个巡检点的过程管理。
主要功能包括：
1. 巡检点序列管理
2. 导航和路径规划
3. 巡检进度跟踪
4. 状态机转换和事件触发
5. 动态任务生成

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core import Workflow, WorkflowMeta
from airfogsim.core.enums import TriggerOperator, WorkflowStatus


class InspectionWorkflowMeta(WorkflowMeta):
    """巡检工作流元类"""

    def __new__(mcs, name, bases, attrs):
        cls = super().__new__(mcs, name, bases, attrs)

        # 注册巡检工作流专用的属性模板
        mcs.register_template(cls, 'inspection_points', list, True,
                            lambda points: all(isinstance(p, (list, tuple)) and len(p) == 3 for p in points),
                            "巡检点列表，每个点为3D坐标 (x, y, z)")

        return cls

class InspectionWorkflow(Workflow, metaclass=InspectionWorkflowMeta):
    """
    巡检工作流，检查代理是否按顺序到达指定检查点。
    """

    @classmethod
    def get_description(cls):
        """获取工作流类型的描述"""
        return "巡检工作流 - 引导无人机按顺序到达指定的巡检点"
    def __init__(self, env, name, owner, timeout=None,
                 event_names=[], initial_status='idle', callback=None, properties=None):
        # 巡检点列表
        self.inspection_points = properties.get('inspection_points', [])
        # 当前检查点索引
        self.current_point_index = 0
        # 注意，这里的event_names是workflow自身的事件，可以用于处理工作流之间的依赖关系
        event_names = ['waypoint_completed', 'inspection_completed']
        super().__init__(
            env=env,
            name=name,
            owner=owner,
            timeout=timeout,
            event_names=event_names,
            initial_status=initial_status,
            callback=callback,
            properties=properties or {}
        )

    def get_details(self):
        """获取工作流详细信息"""
        details = super().get_details()
        details['inspection_points'] = self.inspection_points
        details['description'] = f"If the current state is `Inspecting point n`, then the current task should be `Move to point n` with the (n-1)-th element in `inspection_points`"
        return details

    def get_current_suggested_task(self):
        """
        获取当前状态下建议执行的任务

        根据当前状态机状态和巡检点，动态生成任务信息。
        任务会继承工作流的优先级和抢占属性。

        返回:
            Dict: 任务信息字典
            None: 如果没有找到匹配的任务
        """
        if not self.owner or self.status != WorkflowStatus.RUNNING:
            return None

        current_state = self.status_machine.state
        task_dict = None

        # 检查是否是巡检点状态
        if current_state.startswith('inspecting_point_'):
            try:
                # 获取当前巡检点索引
                point_index = int(current_state.split('_')[-1]) - 1

                # 确保索引有效
                if 0 <= point_index < len(self.inspection_points):
                    # 获取目标坐标
                    target_position = self.inspection_points[point_index]

                    # 创建移动任务
                    task_dict = {
                        'component': 'MoveTo',
                        'task_class': 'MoveToTask',
                        'task_name': f'移动到巡检点 {point_index + 1}',
                        'workflow_id': self.id,
                        'target_state': {'position': target_position},
                        'properties': {
                            'movement_type': 'path_following',
                            'target_position': target_position
                        }
                    }
            except (ValueError, IndexError):
                pass

        # 添加优先级和抢占属性
        return self._add_priority_to_task(task_dict)

    def _setup_transitions(self):
        """设置状态机转换规则"""
        # 工作流启动时，转换到第一个检查点的检查状态
        self.status_machine.set_start_transition('inspecting_point_1')

        def on_waypoint_reached(point_index):
            # 触发检查点完成事件
            self.env.event_registry.trigger_event(
                self.id, 'waypoint_completed',
                {
                    'point_index': point_index,
                    'position': self.inspection_points[point_index],
                    'time': self.env.now
                }
            )

            # 如果是最后一个检查点，触发巡检完成事件
            if point_index == len(self.inspection_points) - 1:
                self.env.event_registry.trigger_event(
                    self.id, 'inspection_completed',
                    {
                        'workflow_id': self.id,
                        'time': self.env.now
                    }
                )
        # 为每个检查点创建转换规则
        for i, point in enumerate(self.inspection_points):
            current_state = f'inspecting_point_{i+1}'

            # 确定下一个状态
            if i+1 < len(self.inspection_points):
                next_state = f'inspecting_point_{i+2}'
            else:
                next_state = 'completed'

            # 添加转换：使用代理状态触发器监听位置变化
            transition_desc = f"当无人机到达巡检点{i+1}时，{'进入下一个巡检点' if i+1 < len(self.inspection_points) else '完成巡检任务'}"

            self.status_machine.add_transition(
                current_state,
                next_state,
                agent_state={
                    'agent_id': self.owner.id,
                    'state_key': 'position',
                    'operator': TriggerOperator.CUSTOM,
                    'target_value': lambda position, point=point:
                        all([abs(position[i] - point[i]) < 1e-6 for i in range(3)]) if position else False
                },
                callback=lambda _event_data, point_idx=i: on_waypoint_reached(point_idx),
                description=transition_desc
            )


        # 失败处理
        self.status_machine.add_transition(
            '*',
            'failed',
            event_trigger={
                'source_id': self.id,
                'event_name': 'status_changed',
                'value_key': 'new_status',
                'operator': TriggerOperator.EQUALS,
                'target_value': WorkflowStatus.FAILED
            }
        )


# 使用示例
def create_inspection_workflow(env, agent, inspection_points, task_priority=None, task_preemptive=False):
    """
    创建巡检工作流

    Args:
        env: 价格环境
        agent: 执行巡检的代理
        inspection_points: 巡检点列表
        task_priority: 任务优先级，可以是TaskPriority枚举或字符串
        task_preemptive: 任务是否可抢占

    Returns:
        创建的巡检工作流
    """
    from airfogsim.core.trigger import TimeTrigger
    from airfogsim.core.enums import TaskPriority

    # 如果没有指定优先级，使用默认值
    if task_priority is None:
        task_priority = TaskPriority.NORMAL

    workflow = env.create_workflow(
        InspectionWorkflow,
        name=f"Inspection of {agent.id}",
        owner=agent,
        properties={
            'inspection_points': inspection_points,
            'task_priority': task_priority,
            'task_preemptive': task_preemptive
        },
        start_trigger=TimeTrigger(env, trigger_time=100),
        max_starts=1
    )

    return workflow