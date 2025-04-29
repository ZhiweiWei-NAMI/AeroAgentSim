"""
AirFogSim计算任务模块

该模块定义了文件计算任务，负责将一个文件通过计算转换为另一个文件。
主要功能包括：
1. 文件计算进度跟踪
2. 计算性能评估
3. 与文件管理器集成

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core.task import Task
from typing import Dict, Optional, List, Any
import math

class FileComputeTask(Task):
    """
    文件计算任务

    将一个文件通过计算转换为另一个文件的任务
    """
    NECESSARY_METRICS = ['processing_power', 'computation_efficiency']
    PRODUCED_STATES = ['computing_status', 'compute_progress', 'compute_speed']

    def __init__(self, env, agent, component_name: str,task_name: str,
                 workflow_id: Optional[str] = None,
                 target_state: Optional[str] = None,
                 properties: Optional[Dict] = None):
        """
        初始化文件计算任务

        Args:
            env: 仿真环境
            agent: 执行任务的代理
            component_name: 使用的组件名称
            workflow_id: 工作流ID
            properties: 任务属性
                file_id: 要计算的文件ID
                result_file_name: 结果文件名（可选）
                result_file_type: 结果文件类型（可选，默认与源文件相同）
        """
        super().__init__(env, agent, component_name, "file_compute" or task_name,
                         workflow_id=workflow_id,
                         target_state=target_state,
                         properties=properties or {})

        # 文件计算参数
        self.file_id = properties.get('file_id')
        self.result_file_name = properties.get('result_file_name')
        self.result_file_type = properties.get('result_file_type')

        # 获取文件管理器
        self.file_manager = getattr(env, 'file_manager', None)
        if not self.file_manager:
            raise ValueError(f"环境中没有文件管理器，无法执行文件计算任务")

        # 获取文件信息
        self.file_info = self.file_manager.get_file(self.file_id)
        if not self.file_info:
            raise ValueError(f"文件 {self.file_id} 不存在")

        # 检查文件所有权
        if self.file_manager.get_file_owner(self.file_id) != agent.id:
            raise ValueError(f"文件 {self.file_id} 不在 agent {agent.id} 上")

        # 文件大小和类型
        self.file_size = self.file_info.get('size', 0)
        if self.file_size <= 0:
            raise ValueError(f"文件 {self.file_id} 大小无效: {self.file_size}")

        self.file_type = self.file_info.get('type', 'data')
        if not self.result_file_type:
            self.result_file_type = self.file_type

        # 如果没有指定结果文件名，生成默认名称
        if not self.result_file_name:
            original_name = self.file_info.get('name', 'unknown')
            self.result_file_name = f"result_{original_name}"

        # 计算状态
        self.compute_progress = 0.0
        self.compute_speed = 0.0  # 计算速度 (单位时间内处理的数据量)
        self.processed_bytes = 0.0  # 已处理的数据量
        self.estimated_time_remaining = 0.0  # 秒

        # 设置代理状态
        self.agent.set_state('computing_status', 'computing')
        self.agent.set_state('compute_progress', 0.0)
        self.agent.set_state('compute_speed', 0.0)

        # 设置CPU使用率，根据文件类型设置不同的基础占用率
        file_type_cpu_usage = {
            'image': 40.0,  # 图像处理基础CPU占用
            'video': 60.0,  # 视频处理基础CPU占用
            'data': 30.0,   # 数据处理基础CPU占用
            'text': 20.0    # 文本处理基础CPU占用
        }

        # 获取当前文件类型的CPU占用率，默认为30.0
        base_cpu_usage = file_type_cpu_usage.get(self.file_type, 30.0)

        # 根据文件大小调整CPU占用率，文件越大占用越高
        size_factor = min(1.5, max(0.5, self.file_size / 1024 / 100))  # 限制在0.5-1.5之间
        cpu_usage = base_cpu_usage * size_factor+self.agent.get_state('cpu_usage')
        if cpu_usage > 100:
            cpu_usage = 100.0

        # 设置代理的CPU使用率状态
        self.agent.set_state('cpu_usage', cpu_usage)

        # 通知文件管理器开始计算
        if self.file_manager:
            self.file_manager.mark_file_computing_started(self.file_id, self.agent.id)

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

        # 获取处理能力和计算效率
        processing_power = performance_metrics.get('processing_power', 100.0)
        computation_efficiency = performance_metrics.get('computation_efficiency', {})

        # 获取当前文件类型的计算效率
        type_efficiency = 1.0
        if isinstance(computation_efficiency, dict) and self.file_type in computation_efficiency:
            type_efficiency = computation_efficiency[self.file_type]
        elif isinstance(computation_efficiency, (int, float)):
            type_efficiency = float(computation_efficiency)

        # 计算处理速度 (KB/s)
        # 假设每单位处理能力每秒可以处理0.1KB数据
        compute_speed = processing_power * 0.1 * type_efficiency

        # 计算这个时间间隔内处理的数据量
        processed_in_interval = compute_speed * time_delta

        # 更新已处理数据量
        self.processed_bytes += processed_in_interval

        # 限制已处理数据量不超过文件大小
        self.processed_bytes = min(self.processed_bytes, self.file_size)

        # 更新计算进度
        self.compute_progress = self.processed_bytes / self.file_size

        # 更新计算速度
        self.compute_speed = compute_speed

        # 计算剩余时间
        remaining_bytes = self.file_size - self.processed_bytes
        if compute_speed > 0:
            self.estimated_time_remaining = remaining_bytes / compute_speed
        else:
            self.estimated_time_remaining = float('inf')

        # 更新代理状态
        self.agent.set_state('compute_progress', self.compute_progress)
        self.agent.set_state('compute_speed', self.compute_speed)

        # 检查是否完成计算
        if self.compute_progress >= 1.0:
            self.progress = 1.0
        else:
            self.progress = self.compute_progress

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

        # 获取处理能力和计算效率
        processing_power = performance_metrics.get('processing_power', 100.0)
        computation_efficiency = performance_metrics.get('computation_efficiency', {})

        # 获取当前文件类型的计算效率
        type_efficiency = 1.0
        if isinstance(computation_efficiency, dict) and self.file_type in computation_efficiency:
            type_efficiency = computation_efficiency[self.file_type]
        elif isinstance(computation_efficiency, (int, float)):
            type_efficiency = float(computation_efficiency)

        # 计算处理速度 (KB/s)
        compute_speed = processing_power * 0.1 * type_efficiency

        # 计算剩余数据量
        remaining_bytes = self.file_size - self.processed_bytes

        # 计算剩余时间
        if compute_speed > 0:
            return remaining_bytes / compute_speed
        else:
            return float('inf')

    def _get_task_specific_state_repr(self) -> Dict:
        """
        获取任务特定状态表示

        Returns:
            Dict: 任务特定状态表示
        """
        # 根据任务进度确定计算状态
        computing_status = 'computing' if self.progress < 1.0 else 'completed'

        return {
            'computing_status': computing_status,
            'compute_progress': self.compute_progress,
            'compute_speed': self.compute_speed
        }

    def _possessing_object_on_complete(self):
        """处理任务完成时对代理拥有对象的操作"""
        # 重置代理状态
        self.agent.set_state('computing_status', 'completed')
        self.agent.set_state('compute_progress', 0.0)
        self.agent.set_state('compute_speed', 0.0)

        # 获取当前CPU使用率并减去任务占用的部分
        current_cpu_usage = self.agent.get_state('cpu_usage', 0.0)
        file_type_cpu_usage = {
            'image': 40.0,
            'video': 60.0,
            'data': 30.0,
            'text': 20.0
        }
        base_cpu_usage = file_type_cpu_usage.get(self.file_type, 30.0)
        size_factor = min(1.5, max(0.5, self.file_size / 1024 / 100))
        task_cpu_usage = base_cpu_usage * size_factor

        # 计算新的CPU使用率，确保不低于10.0%的基础空闲状态
        new_cpu_usage = max(10.0, current_cpu_usage - task_cpu_usage)
        self.agent.set_state('cpu_usage', new_cpu_usage)

        # 创建结果文件
        original_content = self.file_info.get('content')
        result_content = self._process_content(original_content)

        # 创建结果文件
        result_file_id = self.file_manager.create_file(
            owner_agent_id=self.agent.id,
            file_name=self.result_file_name,
            file_size=self.file_size * 0.8,  # 假设结果文件比原文件小
            file_type=self.result_file_type,
            content=result_content,
            properties={
                'source_file_id': self.file_id,
                'computation_time': self.env.now - self.start_time,
                'processed_by': self.agent.id
            }
        )

        # 标记原文件为已计算
        self.file_manager.mark_file_computed(self.file_id, result_file_id)

        # 添加结果文件到代理的possessing_objects
        self.agent.add_possessing_object(result_file_id, self.file_manager.get_file(result_file_id))

    def _possessing_object_on_fail(self):
        """处理任务失败时对代理拥有对象的操作"""
        # 重置代理状态
        self.agent.set_state('computing_status', 'idle')
        self.agent.set_state('compute_progress', 0.0)
        self.agent.set_state('compute_speed', 0.0)

        # 获取当前CPU使用率并减去任务占用的部分
        current_cpu_usage = self.agent.get_state('cpu_usage', 0.0)
        file_type_cpu_usage = {
            'image': 40.0,
            'video': 60.0,
            'data': 30.0,
            'text': 20.0
        }
        base_cpu_usage = file_type_cpu_usage.get(self.file_type, 30.0)
        size_factor = min(1.5, max(0.5, self.file_size / 1024 / 100))
        task_cpu_usage = base_cpu_usage * size_factor

        # 计算新的CPU使用率，确保不低于10.0%的基础空闲状态
        new_cpu_usage = max(10.0, current_cpu_usage - task_cpu_usage)
        self.agent.set_state('cpu_usage', new_cpu_usage)

        # 获取当前CPU使用率并减去任务占用的部分
        current_cpu_usage = self.agent.get_state('cpu_usage', 0.0)
        file_type_cpu_usage = {
            'image': 40.0,
            'video': 60.0,
            'data': 30.0,
            'text': 20.0
        }
        base_cpu_usage = file_type_cpu_usage.get(self.file_type, 30.0)
        size_factor = min(1.5, max(0.5, self.file_size / 1024 / 100))
        task_cpu_usage = base_cpu_usage * size_factor

        # 计算新的CPU使用率，确保不低于10.0%的基础空闲状态
        new_cpu_usage = max(10.0, current_cpu_usage - task_cpu_usage)
        self.agent.set_state('cpu_usage', new_cpu_usage)

    def _possessing_object_on_cancel(self):
        """处理任务取消时对代理拥有对象的操作"""
        # 重置代理状态
        self.agent.set_state('computing_status', 'idle')
        self.agent.set_state('compute_progress', 0.0)
        self.agent.set_state('compute_speed', 0.0)

    def _process_content(self, content):
        """
        处理文件内容，生成结果内容

        Args:
            content: 原始内容

        Returns:
            处理后的内容
        """
        # 这里是模拟处理逻辑，实际应用中可以根据不同文件类型实现不同的处理算法
        if content is None:
            return "处理结果"

        if isinstance(content, str):
            return f"处理结果: {content[:50]}..."

        if isinstance(content, dict):
            return {"result": "processed", "original": content}

        if isinstance(content, list):
            return ["processed"] + content[:5]

        return "处理结果"