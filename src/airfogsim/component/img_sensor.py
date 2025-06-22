"""
AirFogSim感知组件模块

该模块定义了代理的感知能力，负责处理感知任务的性能指标计算。
主要功能包括：
1. 感知性能指标
2. 提供感知能力评估

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core.component import Component
from typing import List, Dict, Any, Optional, Tuple
import math

class ImageSensingComponent(Component):
    """代表代理的感知能力，提供环境感知的性能指标"""
    PRODUCED_METRICS = ['sensing_capability', 'sensing_efficiency', 'sensing_range']
    MONITORED_STATES = ['battery_level', 'image_sensing_status', 'position']

    def __init__(self, env, agent, name: Optional[str] = None,
                 supported_events: List[str] = ['sensing_metrics_changed'],
                 properties: Optional[Dict] = None):
        """
        初始化感知组件

        Args:
            env: 仿真环境
            agent: 所属代理
            name: 组件名称
            supported_events: 支持的额外事件
            properties: 组件属性，包含sensing_capability、sensing_range和sensing_types
        """
        super().__init__(env, agent, name or "Sensing", supported_events, properties)

        # 从properties获取感知规格
        self.base_sensing_capability = self.properties.get('sensing_capability', 500)
        self.sensing_range = self.properties.get('sensing_range', 100.0)  # 感知范围（米）
        self.sensing_factor = self.properties.get('sensing_factor', 1.0)

        # 感知能力配置，不同类型内容的感知能力
        self.sensing_efficiency = self.properties.get('sensing_efficiency', {
            'image': 1.0,  # 图像感知能力
            'video': 0.8,  # 视频感知能力
            'audio': 1.2,  # 音频感知能力
            'sensor_data': 1.5,  # 传感器数据感知能力
            'text': 1.8    # 文本感知能力
        })

    def _calculate_performance_metrics(self) -> Dict[str, Any]:
        """
        计算基于当前代理状态的性能指标

        此方法会根据代理的当前状态计算感知性能指标，包括感知能力、感知效率和感知范围。
        如果代理正在执行感知任务，还会计算感知效率和估计感知时间。

        Returns:
            Dict[str, Any]: 包含性能指标的字典
        """
        # 获取代理当前状态
        battery_level = self.agent.get_state('battery_level', 100.0)
        # 优先使用特定感知状态，如果不存在则使用通用感知状态
        position = self.agent.get_state('position')

        # 基础感知能力
        sensing_capability = self.base_sensing_capability * self.sensing_factor

        # 应用环境和条件因素
        # 1. 低电量会降低感知能力以省电
        if battery_level < 20:
            sensing_capability *= 0.8
        elif battery_level < 10:
            sensing_capability *= 0.5

        # 2. 如果代理正在移动，感知能力可能会降低
        moving_status = self.agent.get_state('moving_status', 'idle')
        if moving_status == 'flying':
            sensing_capability *= 0.9

        # 3. 环境因素影响（如果有环境管理器）
        if hasattr(self.env, 'environment_manager'):
            # 获取当前位置的环境条件
            if position:
                env_conditions = self.env.environment_manager.get_conditions_at(position)
                if env_conditions:
                    # 例如，低光照条件下图像感知能力降低
                    if 'light_level' in env_conditions and env_conditions['light_level'] < 0.3:
                        if 'image' in self.sensing_efficiency:
                            self.sensing_efficiency['image'] *= 0.7

                    # 高噪声环境下音频感知能力降低
                    if 'noise_level' in env_conditions and env_conditions['noise_level'] > 0.7:
                        if 'audio' in self.sensing_efficiency:
                            self.sensing_efficiency['audio'] *= 0.6

        # 计算感知范围（可能受电量和其他因素影响）
        effective_sensing_range = self.sensing_range
        if battery_level < 30:
            effective_sensing_range *= 0.8

        # 返回感知指标
        return {
            'sensing_capability': sensing_capability,
            'sensing_efficiency': self.sensing_efficiency.copy(),
            'sensing_range': effective_sensing_range
        }