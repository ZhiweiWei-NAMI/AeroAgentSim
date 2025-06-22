"""
AirFogSim感知任务模块

该模块定义了文件感知任务，负责在特定位置生成包含相关内容的文件。
主要功能包括：
1. 文件感知进度跟踪
2. 感知性能评估
3. 与文件管理器集成

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core.task import Task
from typing import Dict, Optional, List, Any
import math

class FileCollectTask(Task):
    """
    文件感知任务

    在特定位置生成包含相关内容的文件的任务
    """
    NECESSARY_METRICS = ['sensing_capability', 'sensing_efficiency']
    PRODUCED_STATES = ['image_sensing_status', 'sensing_progress', 'sensing_speed']

    def __init__(self, env, agent, component_name: str,task_name: str,
                 workflow_id: Optional[str] = None,
                 target_state: Optional[str] = None,
                 properties: Optional[Dict] = None):
        """
        初始化文件感知任务

        Args:
            env: 仿真环境
            agent: 执行任务的代理
            component_name: 使用的组件名称
            workflow_id: 工作流ID
            properties: 任务属性
                file_name: 生成的文件名
                file_type: 生成的文件类型
                file_size: 生成的文件大小（KB）
                content_type: 内容类型（如'image', 'text', 'sensor_data'等）
                location: 感知位置 (x, y, z)，可选
                sensing_difficulty: 感知难度系数（默认为1.0）
        """
        super().__init__(env, agent, component_name, "file_collect" or task_name,
                         workflow_id=workflow_id,
                         target_state=target_state,
                         properties=properties or {})

        # 文件感知参数
        self.file_name = properties.get('file_name', f"sensed_data_{env.now}")
        self.file_type = properties.get('file_type', 'sensor_data')
        self.file_size = properties.get('file_size', 1024)  # 默认1MB
        self.content_type = properties.get('content_type', 'sensor_data')
        self.location = properties.get('location')
        self.sensing_difficulty = properties.get('sensing_difficulty', 1.0)

        # 获取文件管理器
        self.file_manager = getattr(env, 'file_manager', None)
        if not self.file_manager:
            raise ValueError(f"环境中没有文件管理器，无法执行文件感知任务")

        # 感知状态
        self.sensing_progress = 0.0
        self.sensing_speed = 0.0  # 感知速度 (单位时间内感知的数据量)
        self.sensed_bytes = 0.0  # 已感知的数据量
        self.estimated_time_remaining = 0.0  # 秒

        # 设置代理状态
        self.agent.set_state('image_sensing_status', 'sensing')  # 使用特定感知状态
        self.agent.set_state('sensing_progress', 0.0)
        self.agent.set_state('sensing_speed', 0.0)

        # 如果没有提供位置，尝试从空间管理器获取当前代理位置
        if not self.location and hasattr(env, 'airspace_manager'):
            self.location = env.airspace_manager.get_agent_position(agent.id)

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

        # 获取感知能力和感知效率
        sensing_capability = performance_metrics.get('sensing_capability', 100.0)
        sensing_efficiency = performance_metrics.get('sensing_efficiency', {})

        # 获取当前内容类型的感知效率
        type_efficiency = 1.0
        if isinstance(sensing_efficiency, dict) and self.content_type in sensing_efficiency:
            type_efficiency = sensing_efficiency[self.content_type]
        elif isinstance(sensing_efficiency, (int, float)):
            type_efficiency = float(sensing_efficiency)

        # 计算感知速度 (KB/s)
        # 假设每单位感知能力每秒可以感知0.1KB数据
        sensing_speed = sensing_capability * 0.1 * type_efficiency / self.sensing_difficulty

        # 计算这个时间间隔内感知的数据量
        sensed_in_interval = sensing_speed * time_delta

        # 更新已感知数据量
        self.sensed_bytes += sensed_in_interval

        # 限制已感知数据量不超过文件大小
        self.sensed_bytes = min(self.sensed_bytes, self.file_size)

        # 更新感知进度
        self.sensing_progress = self.sensed_bytes / self.file_size

        # 更新感知速度
        self.sensing_speed = sensing_speed

        # 计算剩余时间
        remaining_bytes = self.file_size - self.sensed_bytes
        if sensing_speed > 0:
            self.estimated_time_remaining = remaining_bytes / sensing_speed
        else:
            self.estimated_time_remaining = float('inf')

        # 更新代理状态
        self.agent.set_state('sensing_progress', self.sensing_progress)
        self.agent.set_state('sensing_speed', self.sensing_speed)

        # 检查是否完成感知
        if self.sensing_progress >= 1.0:
            self.progress = 1.0
        else:
            self.progress = self.sensing_progress

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

        # 获取感知能力和感知效率
        sensing_capability = performance_metrics.get('sensing_capability', 100.0)
        sensing_efficiency = performance_metrics.get('sensing_efficiency', {})

        # 获取当前内容类型的感知效率
        type_efficiency = 1.0
        if isinstance(sensing_efficiency, dict) and self.content_type in sensing_efficiency:
            type_efficiency = sensing_efficiency[self.content_type]
        elif isinstance(sensing_efficiency, (int, float)):
            type_efficiency = float(sensing_efficiency)

        # 计算感知速度 (KB/s)
        sensing_speed = sensing_capability * 0.1 * type_efficiency / self.sensing_difficulty

        # 计算剩余数据量
        remaining_bytes = self.file_size - self.sensed_bytes

        # 计算剩余时间
        if sensing_speed > 0:
            return remaining_bytes / sensing_speed
        else:
            return float('inf')

    def _get_task_specific_state_repr(self) -> Dict:
        """
        获取任务特定状态表示

        Returns:
            Dict: 任务特定状态表示
        """
        # 根据任务进度确定感知状态
        image_sensing_status = 'sensing' if self.progress < 1.0 else 'completed'

        return {
            'image_sensing_status': image_sensing_status,
            'sensing_progress': self.sensing_progress,
            'sensing_speed': self.sensing_speed
        }

    def _possessing_object_on_complete(self):
        """处理任务完成时对代理拥有对象的操作"""
        # 重置代理状态
        self.agent.set_state('image_sensing_status', 'completed')  # 使用特定感知状态
        self.agent.set_state('sensing_progress', 0.0)
        self.agent.set_state('sensing_speed', 0.0)

        # 生成感知内容
        content = self._generate_content()

        # 创建感知文件
        file_id = self.file_manager.create_file(
            owner_agent_id=self.agent.id,
            file_name=self.file_name,
            file_size=self.file_size,
            file_type=self.file_type,
            content=content,
            properties={
                'content_type': self.content_type,
                'sensing_time': self.env.now,
                'sensing_location': self.location,
                'sensed_by': self.agent.id,
                'sensing_duration': self.env.now - self.start_time
            }
        )

        # 添加感知文件到代理的possessing_objects
        self.agent.add_possessing_object(file_id, self.file_manager.get_file(file_id))

    def _possessing_object_on_fail(self):
        """处理任务失败时对代理拥有对象的操作"""
        # 重置代理状态
        self.agent.set_state('image_sensing_status', 'idle')  # 使用特定感知状态
        self.agent.set_state('sensing_progress', 0.0)
        self.agent.set_state('sensing_speed', 0.0)

    def _possessing_object_on_cancel(self):
        """处理任务取消时对代理拥有对象的操作"""
        # 重置代理状态
        self.agent.set_state('image_sensing_status', 'idle')  # 使用特定感知状态
        self.agent.set_state('sensing_progress', 0.0)
        self.agent.set_state('sensing_speed', 0.0)

    def _generate_content(self):
        """
        生成感知内容

        Returns:
            生成的内容
        """
        # 根据不同的内容类型生成不同的内容
        if self.content_type == 'image':
            return {
                'type': 'image',
                'format': 'jpeg',
                'resolution': '1920x1080',
                'data': f"模拟图像数据 - 位置: {self.location}，时间: {self.env.now}"
            }
        elif self.content_type == 'text':
            return f"在位置 {self.location} 感知到的文本数据，时间: {self.env.now}"
        elif self.content_type == 'sensor_data':
            return {
                'type': 'sensor_data',
                'timestamp': self.env.now,
                'location': self.location,
                'temperature': 25.5,
                'humidity': 60.2,
                'pressure': 1013.25,
                'light': 850,
                'sound': 45.3
            }
        else:
            return f"在位置 {self.location} 感知到的 {self.content_type} 数据，时间: {self.env.now}"