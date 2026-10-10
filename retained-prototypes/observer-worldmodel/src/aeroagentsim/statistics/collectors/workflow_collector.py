"""
AirFogSim工作流状态收集器

该模块提供了用于收集工作流状态数据的收集器。
"""

import time
import json
import csv
import os


class WorkflowStateCollector:
    """
    工作流状态收集器

    负责收集工作流的状态数据，包括工作流的创建、状态变化等。
    """

    def __init__(self, env):
        """
        初始化工作流状态收集器

        Args:
            env: 仿真环境
        """
        self.env = env
        self.workflow_states = {}  # 存储工作流状态数据，格式：{workflow_id: [{timestamp, status, ...}, ...]}
        self.start_time = time.time()

        # 订阅事件
        self._subscribe_events()

    def _subscribe_events(self):
        """订阅事件"""
        # 订阅工作流管理器事件
        if hasattr(self.env, 'workflow_manager'):
            manager_id = self.env.workflow_manager.manager_id

            # 订阅工作流注册事件
            self.env.event_registry.subscribe(
                manager_id,
                'workflow_registered',
                f'stats_workflow_registered_collector_{self.start_time}',
                self._on_workflow_registered
            )

            # 订阅工作流开始事件
            self.env.event_registry.subscribe(
                manager_id,
                'workflow_started',
                f'stats_workflow_registered_collector_{self.start_time}',
                self._on_workflow_started
            )

            # 订阅工作流完成事件
            self.env.event_registry.subscribe(
                manager_id,
                'workflow_completed',
                f'stats_workflow_registered_collector_{self.start_time}',
                self._on_workflow_completed
            )

            # 订阅工作流失败事件
            self.env.event_registry.subscribe(
                manager_id,
                'workflow_failed',
                f'stats_workflow_registered_collector_{self.start_time}',
                self._on_workflow_failed
            )

            # 订阅工作流取消事件
            self.env.event_registry.subscribe(
                manager_id,
                'workflow_canceled',
                f'stats_workflow_registered_collector_{self.start_time}',
                self._on_workflow_canceled
            )

        # 订阅工作流状态机状态变化事件
        self.env.event_registry.subscribe(
            '*',
            'sm_status_changed',
            f'stats_workflow_registered_collector_{self.start_time}',
            self._on_sm_status_changed
        )

        # 订阅工作流创建事件
        self.env.event_registry.subscribe(
            '*',
            'workflow_created',
            f'stats_workflow_registered_collector_{self.start_time}',
            self._on_workflow_created
        )

    def _on_workflow_registered(self, event_data):
        """
        处理工作流注册事件

        Args:
            event_data: 事件数据
        """
        workflow_id = event_data.get('workflow_id')
        if not workflow_id:
            return

        # 获取工作流（注意：对于已完成的工作流，可能已经不存在）
        workflow = self.env.workflow_manager.get_workflow(workflow_id)
        workflow_type = event_data.get('workflow_class', 'Unknown')
        owner_id = None

        if workflow:
            workflow_type = workflow.__class__.__name__
            owner_id = workflow.owner.id if workflow.owner else None

        # 初始化工作流状态存储
        if workflow_id not in self.workflow_states:
            self.workflow_states[workflow_id] = []

        # 记录注册事件
        self.workflow_states[workflow_id].append({
            'timestamp': self.env.now,
            'real_time': time.time() - self.start_time,
            'workflow_id': workflow_id,
            'workflow_type': workflow_type,
            'owner_id': owner_id,
            'status': 'REGISTERED',
            'sm_state': workflow.status_machine.state if workflow and hasattr(workflow, 'status_machine') else None,
            'event': 'registered'
        })

    def _on_workflow_started(self, event_data):
        """
        处理工作流开始事件

        Args:
            event_data: 事件数据
        """
        workflow_id = event_data.get('workflow_id')
        if not workflow_id:
            return

        # 获取工作流（注意：对于已完成的工作流，可能已经不存在）
        workflow = self.env.workflow_manager.get_workflow(workflow_id)
        workflow_type = 'Unknown'
        owner_id = None

        if workflow:
            workflow_type = workflow.__class__.__name__
            owner_id = workflow.owner.id if workflow.owner else None

        # 初始化工作流状态存储
        if workflow_id not in self.workflow_states:
            self.workflow_states[workflow_id] = []

        # 获取工作流的详细信息
        details = workflow.get_details() if hasattr(workflow, 'get_details') else {}

        # 记录开始事件
        self.workflow_states[workflow_id].append({
            'timestamp': self.env.now,
            'real_time': time.time() - self.start_time,
            'workflow_id': workflow_id,
            'workflow_type': workflow_type,
            'owner_id': owner_id,
            'status': workflow.status.name if hasattr(workflow, 'status') else None,
            'sm_state': workflow.status_machine.state if workflow and hasattr(workflow, 'status_machine') else None,
            'details': details,
            'event': 'started',
            'full_state': True  # 标记为全部状态
        })

    def _on_workflow_completed(self, event_data):
        """
        处理工作流完成事件

        Args:
            event_data: 事件数据
        """
        self._handle_workflow_end_event(event_data, 'COMPLETED', 'completed')

    def _on_workflow_failed(self, event_data):
        """
        处理工作流失败事件

        Args:
            event_data: 事件数据
        """
        self._handle_workflow_end_event(event_data, 'FAILED', 'failed')

    def _on_workflow_canceled(self, event_data):
        """
        处理工作流取消事件

        Args:
            event_data: 事件数据
        """
        self._handle_workflow_end_event(event_data, 'CANCELED', 'canceled')

    def _handle_workflow_end_event(self, event_data, status, event_name):
        """
        处理工作流结束相关事件的通用方法

        Args:
            event_data: 事件数据
            status: 状态名称
            event_name: 事件名称
        """
        workflow_id = event_data.get('workflow_id')
        if not workflow_id:
            return

        # 获取工作流（注意：对于已完成的工作流，可能已经不存在）
        workflow = self.env.workflow_manager.get_workflow(workflow_id)
        workflow_type = 'Unknown'
        owner_id = None

        if workflow:
            workflow_type = workflow.__class__.__name__
            owner_id = workflow.owner.id if workflow.owner else None

        # 初始化工作流状态存储
        if workflow_id not in self.workflow_states:
            self.workflow_states[workflow_id] = []

        # 记录结束事件
        self.workflow_states[workflow_id].append({
            'timestamp': self.env.now,
            'real_time': time.time() - self.start_time,
            'workflow_id': workflow_id,
            'workflow_type': workflow_type,
            'owner_id': owner_id,
            'status': status,
            'sm_state': workflow.status_machine.state if workflow and hasattr(workflow, 'status_machine') else None,
            'reason': event_data.get('reason'),
            'event': event_name
        })

    def _on_workflow_status_changed(self, event_data):
        """
        处理工作流状态变化事件

        Args:
            event_data: 事件数据
        """
        workflow_id = event_data.get('workflow_id')
        if not workflow_id:
            return

        # 获取工作流
        workflow = self.env.workflow_manager.get_workflow(workflow_id)
        if not workflow:
            return

        # 获取状态变化信息
        old_status = event_data.get('old_status')
        new_status = event_data.get('new_status')
        sm_state = event_data.get('sm_state')

        # 检查是否已经有相同的状态记录
        if workflow_id in self.workflow_states:
            for state_record in reversed(self.workflow_states[workflow_id]):
                # 如果已经有相同的状态记录，则跳过
                if state_record.get('sm_state') == sm_state and state_record.get('status') == new_status:
                    return

        # 初始化工作流状态存储
        if workflow_id not in self.workflow_states:
            self.workflow_states[workflow_id] = []

        # 记录状态变化
        self.workflow_states[workflow_id].append({
            'timestamp': self.env.now,
            'real_time': time.time() - self.start_time,
            'workflow_id': workflow_id,
            'workflow_type': workflow.__class__.__name__,
            'owner_id': workflow.owner.id if workflow.owner else None,
            'old_status': old_status,
            'new_status': new_status,
            'sm_state': sm_state
        })

    def _on_sm_status_changed(self, event_data):
        """
        处理工作流状态机状态变化事件

        Args:
            event_data: 事件数据
        """
        workflow_id = event_data.get('workflow_id')
        if not workflow_id:
            return

        # 获取工作流
        workflow = self.env.workflow_manager.get_workflow(workflow_id)
        if not workflow:
            return

        # 获取状态变化信息
        old_sm_state = event_data.get('old_sm_status')
        new_sm_state = event_data.get('new_sm_status')

        # 如果是从 idle -> inspecting_point_1 的变化，则跳过，因为这个事件已经被 started 事件捕获
        if old_sm_state == 'idle' and new_sm_state == 'inspecting_point_1':
            return
        # 如果是->completed也跳过
        if new_sm_state == 'completed':
            return

        # 初始化工作流状态存储
        if workflow_id not in self.workflow_states:
            self.workflow_states[workflow_id] = []

        # 记录状态机状态变化
        self.workflow_states[workflow_id].append({
            'timestamp': self.env.now,
            'real_time': time.time() - self.start_time,
            'workflow_id': workflow_id,
            'workflow_type': workflow.__class__.__name__,
            'owner_id': workflow.owner.id if workflow.owner else None,
            'status': workflow.status.name if hasattr(workflow, 'status') else None,
            'old_sm_state': old_sm_state,
            'new_sm_state': new_sm_state,
            'sm_state': new_sm_state,
            'event': 'sm_state_changed'
        })

    def _on_workflow_created(self, event_data):
        """
        处理工作流创建事件

        Args:
            event_data: 事件数据
        """
        workflow_id = event_data.get('workflow_id')
        if not workflow_id:
            return

        # 获取工作流
        workflow = self.env.workflow_manager.get_workflow(workflow_id)
        if not workflow:
            return

        # 初始化工作流状态存储
        if workflow_id not in self.workflow_states:
            self.workflow_states[workflow_id] = []

        # 记录创建事件
        self.workflow_states[workflow_id].append({
            'timestamp': self.env.now,
            'real_time': time.time() - self.start_time,
            'workflow_id': workflow_id,
            'workflow_type': workflow.__class__.__name__,
            'owner_id': workflow.owner.id if workflow.owner else None,
            'status': 'CREATED',
            'sm_state': workflow.status_machine.state if hasattr(workflow, 'status_machine') else None,
            'event': 'created'
        })

    def export_data(self, output_dir):
        """
        导出数据到文件

        Args:
            output_dir: 输出目录

        Returns:
            Dict: 导出的文件路径
        """
        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)

        # 导出工作流状态数据
        workflow_states_file = os.path.join(output_dir, "workflow_states.json")
        with open(workflow_states_file, "w") as f:
            json.dump(self.workflow_states, f, indent=2)

        # 创建 CSV 格式的数据，方便分析
        self._export_csv_data(output_dir)

        return {
            "workflow_states_file": workflow_states_file
        }

    def _export_csv_data(self, output_dir):
        """
        导出 CSV 格式的数据

        Args:
            output_dir: 输出目录
        """
        # 导出工作流状态数据
        workflow_file = os.path.join(output_dir, "workflow_states.csv")
        with open(workflow_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "workflow_id", "workflow_type", "owner_id", "status", "sm_state"])

            for workflow_id, states in self.workflow_states.items():
                for state in states:
                    writer.writerow([
                        state["timestamp"],
                        workflow_id,
                        state["workflow_type"],
                        state["owner_id"],
                        state["status"] if "status" in state else state.get("new_status"),
                        state["sm_state"]
                    ])
