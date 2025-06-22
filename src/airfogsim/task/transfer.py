"""
AirFogSim传输任务模块

该模块定义了文件传输任务，负责将文件从一个代理传输到另一个代理。
主要功能包括：
1. 文件传输进度跟踪
2. 传输性能评估
3. 与文件管理器集成

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core.task import Task
from airfogsim.core.enums import TaskStatus
from typing import Dict, Optional, List, Any
import math

class FileTransferTask(Task):
    """
    文件传输任务

    将文件从源代理传输到目标代理的任务
    """
    NECESSARY_METRICS = ['transmission_rate', 'latency', 'communication_quality']
    PRODUCED_STATES = ['trans_target_agent_id', 'transmission_progress',
                       'transmission_speed', 'transmitting_status']

    def __init__(self, env, agent, component_name: str, task_name: str,
                 workflow_id: Optional[str] = None,
                 target_state: Optional[str] = None,
                 properties: Optional[Dict] = None):
        """
        初始化文件传输任务

        Args:
            env: 仿真环境
            agent: 执行任务的代理
            component_name: 使用的组件名称
            file_id: 要传输的文件ID
            properties: 任务属性
                trans_target_agent_id: 目标代理ID
                workflow_id: 工作流ID
        """
        super().__init__(env, agent, component_name, "file_transfer" or task_name,
                         workflow_id=workflow_id,
                         target_state=target_state,
                         properties=properties or {})

        # 文件传输参数
        self.file_id = properties.get('file_id')
        self.trans_target_agent_id = properties.get('trans_target_agent_id')

        # 获取文件管理器
        self.file_manager = getattr(env, 'file_manager', None)
        if not self.file_manager:
            raise ValueError(f"环境中没有文件管理器，无法执行文件传输任务")

        # 获取文件信息
        self.file_info = self.file_manager.get_file(self.file_id)
        if not self.file_info:
            raise ValueError(f"文件 {self.file_id} 不存在")
        if self.file_manager.get_file_owner(self.file_id) != agent.id:
            raise ValueError(f"文件 {self.file_id} 不在 agent {agent.id} 上")

        # 文件大小 (KB)
        self.file_size = self.file_info.get('size', 0)
        if self.file_size <= 0:
            raise ValueError(f"文件 {self.file_id} 大小无效: {self.file_size}")

        if self.trans_target_agent_id == agent.id:
            raise ValueError(f"目的agent和当前agent一致")

        self.target_agent = env.agents[self.trans_target_agent_id]

        # 传输状态
        self.transfer_progress = 0.0
        self.transfer_speed = 0.0  # KB/s
        self.transferred_bytes = 0.0  # KB
        self.estimated_time_remaining = 0.0  # 秒

        # 设置代理状态
        self.agent.set_state('trans_target_agent_id', self.trans_target_agent_id)
        self.agent.set_state('transmitting_status', 'transmitting')
        self.agent.set_state('transmission_progress', 0.0)
        self.agent.set_state('transmission_speed', 0.0)

        # 通知文件管理器开始传输
        if self.file_manager:
            self.file_manager.mark_file_transfer_started(self.file_id,
                                                         self.agent.id, self.trans_target_agent_id)

    def _update_task_state(self, performance_metrics: Dict):
        """
        更新任务状态

        Args:
            performance_metrics: 性能指标
        """
        current_time = self.env.now

        # 如果是第一次更新，记录开始时间
        if self.last_update_time is None:
            self.last_update_time = current_time
            return

        # 计算时间间隔
        time_delta = current_time - self.last_update_time
        if time_delta <= 0:
            return

        # 获取传输速率 (Mbps)
        transmission_rate = performance_metrics.get('transmission_rate', 1.0)  # Mbps

        # 计算这个时间间隔内传输的数据量 (KB)
        # 传输速率 Mbps 转换为 KB/s: 1 Mbps = 125 KB/s
        transfer_speed = transmission_rate * 125.0  # KB/s

        # 计算传输的字节数
        transferred_in_interval = transfer_speed * time_delta  # KB

        # 更新已传输字节数
        self.transferred_bytes += transferred_in_interval

        # 限制已传输字节数不超过文件大小
        self.transferred_bytes = min(self.transferred_bytes, self.file_size)

        # 更新传输进度
        self.transfer_progress = self.transferred_bytes / self.file_size

        # 更新传输速度
        self.transfer_speed = transfer_speed

        # 计算剩余时间
        remaining_bytes = self.file_size - self.transferred_bytes
        if transfer_speed > 0:
            self.estimated_time_remaining = remaining_bytes / transfer_speed
        else:
            self.estimated_time_remaining = float('inf')

        # 更新代理状态
        self.agent.set_state('transmission_progress', self.transfer_progress)
        self.agent.set_state('transmission_speed', self.transfer_speed)

        # 检查是否完成传输
        if self.transfer_progress >= 1.0:
            self.progress = 1.0
        else:
            self.progress = self.transfer_progress

    def estimate_remaining_time(self, performance_metrics) -> float:
        """
        估计剩余时间

        Args:
            performance_metrics: 性能指标

        Returns:
            float: 估计的剩余时间 (秒)
        """
        # 如果已经完成，返回0
        if self.progress >= 1.0:
            return 0.0

        # 获取传输速率 (Mbps)
        transmission_rate = performance_metrics.get('transmission_rate', 1.0)  # Mbps

        # 传输速率 Mbps 转换为 KB/s: 1 Mbps = 125 KB/s
        transfer_speed = transmission_rate * 125.0  # KB/s

        # 计算剩余字节数
        remaining_bytes = self.file_size - self.transferred_bytes

        # 计算剩余时间
        if transfer_speed > 0:
            return remaining_bytes / transfer_speed
        else:
            return float('inf')

    def _get_task_specific_state_repr(self) -> Dict:
        """
        获取任务特定状态表示

        Returns:
            Dict: 任务特定状态表示
        """
        # 根据任务进度确定传输状态
        transmitting_status = 'transmitting' if self.progress < 1.0 else 'completed'

        return {
            'transmission_progress': self.transfer_progress,
            'transmission_speed': self.transfer_speed,
            'transmitting_status': transmitting_status,
            'trans_target_agent_id': self.trans_target_agent_id
        }

    def _possessing_object_on_complete(self):
        """处理任务完成时对代理拥有对象的操作"""
        # 将文件从当前代理移除
        self.agent.remove_possessing_object(self.file_id)

        # 重置代理状态
        self.agent.set_state('transmitting_status', 'completed')
        self.agent.set_state('transmission_progress', 0.0)
        self.agent.set_state('transmission_speed', 0.0)

        # 通知文件管理器传输完成
        if self.file_manager:
            # 获取目标代理位置
            target_location = None
            if hasattr(self.env, 'airspace_manager'):
                target_location = self.env.airspace_manager.get_agent_position(self.trans_target_agent_id)

            self.file_manager.mark_file_transferred(self.file_id, self.trans_target_agent_id, target_location)
        self.target_agent.add_possessing_object(self.file_id, self.file_manager.get_file(self.file_id))

    def _possessing_object_on_fail(self):
        """处理任务失败时对代理拥有对象的操作"""
        # 重置代理状态
        self.agent.set_state('transmitting_status', 'idle')
        self.agent.set_state('transmission_progress', 0.0)
        self.agent.set_state('transmission_speed', 0.0)

    def _possessing_object_on_cancel(self):
        """处理任务取消时对代理拥有对象的操作"""
        # 重置代理状态
        self.agent.set_state('transmitting_status', 'idle')
        self.agent.set_state('transmission_progress', 0.0)
        self.agent.set_state('transmission_speed', 0.0)