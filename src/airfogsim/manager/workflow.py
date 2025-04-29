from collections import defaultdict
from airfogsim.core.enums import WorkflowStatus
from airfogsim.core import Workflow
from typing import Dict, Any, Optional, Tuple, List, Union, Callable
import warnings
from airfogsim.manager.trigger import Trigger
from airfogsim.utils.logging_config import get_logger
import uuid
# 获取logger
logger = get_logger(__name__)

class WorkflowManager:
    def __init__(self, env):
        self.env = env
        self.workflows: Dict[str, 'Workflow'] = {}
        self.manager_id = f"workflow_manager_{uuid.uuid4().hex[:8]}"
        self.workflow_triggers: Dict[str, List] = defaultdict(list)
        self._register_manager_events()
        self._subscribe_to_manager_events()

    def get_all_workflows(self):
        return list(self.workflows.values())

    def _register_manager_events(self):
        self.env.event_registry.get_event(self.manager_id, 'workflow_registered')
        self.env.event_registry.get_event(self.manager_id, 'workflow_started')
        self.env.event_registry.get_event(self.manager_id, 'workflow_completed')
        self.env.event_registry.get_event(self.manager_id, 'workflow_failed')
        self.env.event_registry.get_event(self.manager_id, 'workflow_canceled')


    def _subscribe_to_manager_events(self):
        self.env.event_registry.subscribe(self.manager_id, 'workflow_registered', self.manager_id,
                                             lambda ev: logger.info(f"时间 {self.env.now}: WMgr: Registered Workflow {ev.get('workflow_id')}"))

    def register_workflow(self, workflow: 'Workflow',
                          start_trigger: Optional[Union[Tuple[str, str], Trigger]] = None,
                          max_starts: int = 1):
        """
        注册工作流，可以使用简单的事件触发器或高级触发器

        参数:
            workflow: 要注册的工作流
            start_trigger: 启动触发器，可以是(source_id, event_name)元组或Trigger对象
            max_starts: 最大启动次数，None表示无限次
        """
        if workflow.id in self.workflows:
            warnings.warn(f"Workflow {workflow.id} already registered.")
            return
        if max_starts and max_starts < 1:
            warnings.warn(f"Invalid max_starts value for workflow {workflow.id}")
            return
        self.workflows[workflow.id] = workflow
        self._subscribe_manager_to_workflow(workflow)

        # 处理触发器
        if start_trigger:
            if isinstance(start_trigger, tuple) and len(start_trigger) == 2:
                # 将简单触发器转换为高级触发器
                source_id, event_name = start_trigger
                from airfogsim.core.trigger import EventTrigger
                trigger = EventTrigger(self.env, source_id, event_name, name=f"start_trigger_{workflow.id}")
                trigger.set_max_triggers(max_starts)
                trigger.add_callback(lambda context, w_id=workflow.id: self._handle_advanced_trigger(w_id, context))
                self.workflow_triggers[workflow.id].append(trigger)
                trigger.activate()
            elif isinstance(start_trigger, Trigger):
                # 使用高级触发器
                trigger = start_trigger
                trigger.set_max_triggers(max_starts)
                trigger.add_callback(lambda context, w_id=workflow.id: self._handle_advanced_trigger(w_id, context))
                self.workflow_triggers[workflow.id].append(trigger)
                trigger.activate()
            else:
                warnings.warn(f"Invalid start_trigger format for workflow {workflow.id}")

        self.env.event_registry.trigger_event(
            self.manager_id, 'workflow_registered',
            {'workflow_id': workflow.id, 'workflow_name': workflow.name, 'workflow_class': workflow.__class__.__name__,
             'time': self.env.now}
        )
        return workflow

    def _handle_advanced_trigger(self, workflow_id: str, context: Dict[str, Any]):
        """处理高级触发器的回调"""

        logger.info(f"时间 {self.env.now}: WMgr: Advanced trigger fired for workflow {workflow_id}")
        logger.info(f"  Trigger: {context.get('trigger_name')} ({context.get('trigger_type')})")
        if not self.start_workflow(workflow_id):
            logger.error(f"WMgr: Failed to start workflow {workflow_id}")

    def start_workflow(self, workflow_id: str):
        """手动启动工作流"""
        workflow = self.workflows.get(workflow_id)
        if not workflow:
            logger.error(f"Workflow {workflow_id} not registered.")
            return False
        if workflow.status != WorkflowStatus.PENDING:
            logger.error(f"Workflow {workflow_id} is not in a startable state.")
            return False
        started_process = workflow.start()
        if not started_process:
            logger.error(f"Failed to start workflow {workflow_id}")
            return False
        return True

    def _subscribe_manager_to_workflow(self, workflow: 'Workflow'):
        try:
            self.env.event_registry.subscribe(
                workflow.id, 'workflow_status_changed', self.manager_id,
                lambda ev, w_id=workflow.id: self._handle_workflow_status_change(w_id, ev)
            )
        except Exception as e:
            logger.error(f"Error subscribing manager to workflow {workflow.id} events: {e}")

    def _handle_workflow_status_change(self, workflow_id, event_value):
        """处理工作流状态变更事件"""
        workflow = self.workflows.get(workflow_id)
        if not workflow:
            return

        new_status_str = event_value.get('new_status')
        old_status_str = event_value.get('old_status')
        sm_state = event_value.get('sm_state')
        timestamp = event_value.get('time', self.env.now)
        details = event_value.get('event_details', {})
        reason = details.get('reason', f"State machine reached '{sm_state}'") if isinstance(details, dict) else str(details) if details else None

        # 检查当前状态
        if new_status_str == WorkflowStatus.RUNNING.name:
            # 当工作流开始运行时，触发manager的工作流开始事件
            self.env.event_registry.trigger_event(
                self.manager_id, 'workflow_started',
                {'workflow_id': workflow_id, 'time': timestamp}
            )
            logger.info(f"时间 {self.env.now}: WorkflowManager: 工作流 {workflow_id} 已开始运行")

            # 更新代理的current_workflow_id状态
            if workflow.owner:
                workflow.owner.update_state('current_workflow_id', workflow_id)

        elif new_status_str in (WorkflowStatus.COMPLETED.name, WorkflowStatus.FAILED.name, WorkflowStatus.CANCELED.name):
            # 当工作流结束时，触发manager的相应事件
            event_name = f"workflow_{new_status_str.lower()}"
            event_data = {
                'workflow_id': workflow_id,
                'reason': reason,
                'time': timestamp
            }

            # 打印日志
            logger.info(f"时间 {self.env.now}: WorkflowManager: 工作流 {workflow_id} 状态变为 {new_status_str}，触发事件 {event_name}\
                        \n  - 事件数据: {event_data}")

            # 触发事件
            self.env.event_registry.trigger_event(
                self.manager_id,
                event_name,
                event_data
            )

            # 清除代理的current_workflow_id状态
            if workflow.owner:
                workflow.owner.update_state('current_workflow_id', None)

            # 判断是否仍能触发
            can_trigger_again = False
            if workflow_id in self.workflow_triggers:
                for trigger in self.workflow_triggers[workflow_id]:
                    if not trigger.has_reached_max():
                        can_trigger_again = True
                        # 重新激活触发器
                        trigger.activate()
                        break

            if can_trigger_again:
                # 如果可以再次触发，调用reset方法重置工作流
                workflow.reset()
                logger.info(f"时间 {self.env.now}: WMgr: Workflow {workflow_id} reset for re-triggering")
            else:
                # 如果达到触发上限，清理触发器
                self._cleanup_workflow_triggers(workflow_id)

            # 执行回调函数
            if workflow.callback and callable(workflow.callback):
                try:
                    workflow.callback(workflow)
                except Exception as cb_e:
                    logger.error(f"Error in workflow {workflow_id} callback: {cb_e}")

    def _cleanup_workflow_triggers(self, workflow_id: str):
        """清理工作流相关的所有触发器"""
        # 清理所有触发器
        if workflow_id in self.workflow_triggers:
            for trigger in self.workflow_triggers[workflow_id]:
                trigger.deactivate()
            del self.workflow_triggers[workflow_id]

    def cancel_workflow(self, workflow_id, reason="canceled_by_manager"):
        """取消工作流"""
        workflow = self.workflows.get(workflow_id)
        if not workflow:
            return False

        if workflow.sm_process and workflow.sm_process.is_alive:
            # 如果状态机正在运行，中断它
            try:
                workflow.sm_process.interrupt({'action': 'cancel', 'reason': reason})
                return True
            except Exception as e:
                logger.error(f"Error interrupting SM for {workflow_id}: {e}")
                return False
        elif workflow.status == WorkflowStatus.PENDING:  # 在状态机启动前取消
            # 更新工作流状态，并触发相应事件
            old_status = workflow.status
            workflow.status = WorkflowStatus.CANCELED
            workflow.completion_reason = reason
            workflow.end_time = self.env.now

            # 触发工作流状态变更事件
            self.env.event_registry.trigger_event(workflow.id, 'workflow_status_changed', {
                'workflow_id': workflow_id,
                'old_status': old_status.name,
                'new_status': workflow.status.name,
                'sm_state': workflow.status_machine.state,
                'event_details': {'reason': reason},
                'time': self.env.now
            })

            # 触发管理器取消事件
            self.env.event_registry.trigger_event(
                self.manager_id, 'workflow_canceled',
                {'workflow_id': workflow_id, 'reason': reason, 'time': self.env.now}
            )

            # 清理触发器
            self._cleanup_workflow_triggers(workflow_id)

            # 执行回调
            if workflow.callback:
                workflow.callback(workflow)
            return True
        return False

    def get_workflow(self, workflow_id):
        return self.workflows.get(workflow_id)

    def get_agent_workflows(self, agent_id):
        return [w for w in self.workflows.values() if w.owner.id == agent_id]

    def add_workflow_trigger(self, workflow_id: str, trigger):
        """为工作流添加额外的触发器（不用于启动）"""
        workflow = self.get_workflow(workflow_id)
        if not workflow:
            warnings.warn(f"Cannot add trigger: Workflow {workflow_id} not found")
            return False

        self.workflow_triggers[workflow_id].append(trigger)
        return True
