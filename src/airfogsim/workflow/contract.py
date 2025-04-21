"""
AirFogSim合约工作流模块

该模块定义了合约执行工作流及其元类，实现了代理执行合约中多个任务的过程管理。
主要功能包括：
1. 合约任务序列管理
2. 任务状态跟踪
3. 合约完成状态监控
4. 状态机转换和事件触发

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core import Workflow, WorkflowMeta
from airfogsim.core.enums import TriggerOperator, WorkflowStatus
import uuid
from typing import List, Dict, Any, Optional

class ContractWorkflowMeta(WorkflowMeta):
    """合约工作流元类"""

    def __new__(mcs, name, bases, attrs):
        cls = super().__new__(mcs, name, bases, attrs)

        # 注册合约工作流专用的属性模板
        mcs.register_template(cls, 'contract_id', str, True, None, "关联的合约ID")
        mcs.register_template(cls, 'tasks', list, True,
                          lambda tasks: all(isinstance(t, dict) and 'id' in t and 'component' in t for t in tasks),
                          "合约包含的任务列表")

        return cls

class ContractWorkflow(Workflow, metaclass=ContractWorkflowMeta):
    """
    合约工作流，管理合约中包含的多个任务的执行流程。
    支持顺序执行多个任务，并在所有任务完成后标记合约为完成状态。
    """

    @classmethod
    def get_description(cls):
        """获取工作流类型的描述"""
        return "合约工作流 - 管理合约中多个任务的执行流程"

    def __init__(self, env, name, owner, executor_agent_id, timeout=None,
                 event_names=[], initial_status='idle', callback=None, properties=None):
        # 添加工作流特定事件
        event_names = list(event_names) + ['task_completed', 'all_tasks_completed']

        # 确保 properties 不为 None
        properties = properties or {}

        # 初始化任务列表 - 在调用父类初始化之前
        self.tasks = properties.get('tasks', [])

        # 合约ID
        self.contract_id = properties.get('contract_id')
        # 执行者ID
        self.executor_agent_id = executor_agent_id
        # 当前任务索引
        self.current_task_index = 0

        # 任务完成状态
        self.task_status = {task['id']: False for task in self.tasks}

        # 调用父类初始化
        super().__init__(
            env=env,
            name=name,
            owner=owner,
            timeout=timeout,
            event_names=event_names,
            initial_status=initial_status,
            callback=callback,
            properties=properties
        )

        # 为每个任务添加工作流ID
        for task in self.tasks:
            task['workflow_id'] = self.id


    def get_details(self):
        """获取工作流详细信息"""
        details = super().get_details()
        details['contract_id'] = self.contract_id
        details['tasks'] = self.tasks
        details['executor_agent_id'] = self.executor_agent_id
        details['current_task_index'] = self.current_task_index
        details['task_status'] = self.task_status
        return details

    def get_current_suggested_task(self):
        """
        获取当前状态下建议执行的任务

        根据当前状态机状态，返回当前应该执行的任务信息。

        返回:
            Dict: 任务信息字典
            None: 如果没有找到匹配的任务或所有任务已完成
        """
        if not self.owner or self.status != WorkflowStatus.RUNNING:
            return None

        current_state = self.status_machine.state

        # 检查是否是等待任务状态
        if current_state.startswith('waiting_for_task_'):
            try:
                # 获取当前任务索引
                task_index = int(current_state.split('_')[-1]) - 1

                # 确保索引有效
                if 0 <= task_index < len(self.tasks):
                    # 获取当前任务信息
                    task_info = self.tasks[task_index]

                    # 添加工作流ID
                    task_info['workflow_id'] = self.id

                    return task_info
            except (ValueError, IndexError):
                pass

        return None

    def _setup_transitions(self):
        """设置状态机转换规则"""
        # 工作流启动时，转换到第一个任务的等待状态
        self.status_machine.set_start_transition('waiting_for_task_1')

        def on_task_completed(task_index, event_data):
            # 更新任务状态
            task_id = self.tasks[task_index]['id']
            self.task_status[task_id] = True

            # 触发任务完成事件
            self.env.event_registry.trigger_event(
                self.id, 'task_completed',
                {
                    'task_index': task_index,
                    'task_id': task_id,
                    'task_name': self.tasks[task_index].get('name', f'Task {task_index+1}'),
                    'time': self.env.now,
                    'result': event_data
                }
            )

            # 如果是最后一个任务，触发所有任务完成事件
            if task_index == len(self.tasks) - 1:
                self.env.event_registry.trigger_event(
                    self.id, 'all_tasks_completed',
                    {
                        'workflow_id': self.id,
                        'contract_id': self.contract_id,
                        'time': self.env.now
                    }
                )

        # 为每个任务创建转换规则
        for i, task in enumerate(self.tasks):
            current_state = f'waiting_for_task_{i+1}'

            # 确定下一个状态
            if i+1 < len(self.tasks):
                next_state = f'waiting_for_task_{i+2}'
            else:
                next_state = 'completed'

            # 添加转换：使用事件触发器监听任务完成事件
            transition_desc = f"当任务 {task.get('name', f'Task {i+1}')} 完成时，{'进入下一个任务' if i+1 < len(self.tasks) else '完成合约'}"

            self.status_machine.add_transition(
                current_state,
                next_state,
                event_trigger={
                    'source_id': self.executor_agent_id,
                    'event_name': f"{task['component']}.task_completed",
                    'value_key': 'task_id',
                    'operator': TriggerOperator.EQUALS,
                    'target_value': task['id']
                },
                callback=lambda event_data, idx=i: on_task_completed(idx, event_data),
                description=transition_desc
            )

            # 添加任务失败时的转换
            self.status_machine.add_transition(
                current_state,
                'failed',
                event_trigger={
                    'source_id': self.executor_agent_id,
                    'event_name': f"{task['component']}.task_failed",
                    'value_key': 'task_id',
                    'operator': TriggerOperator.EQUALS,
                    'target_value': task['id']
                },
                description=f"任务 {task.get('name', f'Task {i+1}')} 失败"
            )

            # 添加任务取消时的转换
            self.status_machine.add_transition(
                current_state,
                'canceled',
                event_trigger={
                    'source_id': self.executor_agent_id,
                    'event_name': f"{task['component']}.task_canceled",
                    'value_key': 'task_id',
                    'operator': TriggerOperator.EQUALS,
                    'target_value': task['id']
                },
                description=f"任务 {task.get('name', f'Task {i+1}')} 取消"
            )

        # 通用失败处理
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


def create_contract_workflow(env, contract_id, tasks, owner, executor_agent_id, timeout=None):
    """
    创建合约工作流

    Args:
        env: 仿真环境
        contract_id: 合约ID
        tasks: 任务列表
        owner: 工作流所有者（通常是合约发起者）
        executor_agent_id: 执行者ID
        timeout: 超时时间（可选）

    Returns:
        ContractWorkflow: 创建的合约工作流实例
    """
    workflow = ContractWorkflow(
        env=env,
        name=f"Contract_{contract_id}_Execution",
        owner=owner,
        executor_agent_id=executor_agent_id,
        timeout=timeout,
        properties={
            'contract_id': contract_id,
            'tasks': tasks
        }
    )

    return workflow