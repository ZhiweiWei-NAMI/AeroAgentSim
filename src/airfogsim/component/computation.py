"""
AirFogSim计算组件模块

该模块定义了代理的计算能力，负责处理计算任务的性能指标计算。
主要功能包括：
1. 计算性能指标
2. 提供计算能力评估

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core.component import Component
from typing import List, Dict, Any, Optional, Tuple
import math

class CPUComponent(Component):
    """代表代理的计算能力，管理CPU和内存资源的组件"""
    PRODUCED_METRICS = ['processing_power']
    MONITORED_STATES = ['battery_level', 'cpu_usage', 'memory_usage']  # 监控这些状态的变化

    def __init__(self, env, agent, name: Optional[str] = None,
                 supported_events: List[str] = ['cpu_usage_changed'],
                 properties: Optional[Dict] = None):
        """
        初始化计算组件

        Args:
            env: 仿真环境
            agent: 所属代理
            name: 组件名称
            supported_events: 支持的额外事件
            properties: 组件属性，包含cpu_cores、memory_mb和processing_factor
        """
        super().__init__(env, agent, name or "CPU", supported_events, properties)

        # 从properties获取CPU和内存规格
        self.max_cpu_cores = self.properties.get('cpu_cores', 4)
        self.max_memory_mb = self.properties.get('memory_mb', 2048)
        self.processing_factor = self.properties.get('processing_factor', 1.0)

    def _calculate_performance_metrics(self) -> Dict[str, Any]:
        """计算基于当前代理状态的性能指标"""
        # 获取代理当前状态
        cpu_usage = self.agent.get_state('cpu_usage', 0.0)
        memory_usage = self.agent.get_state('memory_usage', 0.0)
        battery_level = self.agent.get_state('battery_level', 100.0)

        # 基础处理能力
        base_processing_power = self.max_cpu_cores * 100.0  # MIPS或其他单位

        # 应用环境和条件因素
        # 1. 低电量会降低处理能力以省电
        if battery_level < 20:
            base_processing_power *= 0.7
        elif battery_level < 10:
            base_processing_power *= 0.4

        # 2. 高CPU使用率会导致系统热量增加并可能降低性能
        if cpu_usage > 80:
            base_processing_power *= 0.9

        # 3. 应用处理因子
        processing_power = base_processing_power * self.processing_factor

        # 4. 如果内存使用率非常高，性能可能会进一步下降
        if memory_usage > 90:
            processing_power *= 0.8

        return {
            'processing_power': processing_power
        }


class ComputationComponent(Component):
    """代表代理的高级计算能力，提供文件处理的性能指标"""
    PRODUCED_METRICS = ['processing_power', 'computation_efficiency']
    MONITORED_STATES = ['battery_level', 'cpu_usage', 'memory_usage', 'computing_status']

    def __init__(self, env, agent, name: Optional[str] = None,
                 supported_events: List[str] = ['computation_metrics_changed'],
                 properties: Optional[Dict] = None):
        """
        初始化计算组件

        Args:
            env: 仿真环境
            agent: 所属代理
            name: 组件名称
            supported_events: 支持的额外事件
            properties: 组件属性，包含cpu_cores、memory_mb、processing_factor和computation_capabilities
        """
        super().__init__(env, agent, name or "Computation", supported_events, properties)

        # 从properties获取计算规格
        self.cpu_cores = self.properties.get('cpu_cores', 4)
        self.memory_mb = self.properties.get('memory_mb', 8192)
        self.processing_factor = self.properties.get('processing_factor', 1.0)

        # 计算能力配置，不同类型文件的处理能力
        self.computation_capabilities = self.properties.get('computation_capabilities', {
            'image': 1.0,  # 图像处理能力
            'video': 0.8,  # 视频处理能力
            'data': 1.2,   # 数据处理能力
            'text': 1.5    # 文本处理能力
        })

    def _calculate_performance_metrics(self) -> Dict[str, Any]:
        """
        计算基于当前代理状态的性能指标

        此方法会根据代理的当前状态计算计算性能指标，包括CPU使用率、内存使用率、处理能力等。
        如果代理正在执行计算任务，还会计算计算效率和估计计算时间。

        Returns:
            Dict[str, Any]: 包含性能指标的字典
        """
        # 获取代理当前状态
        battery_level = self.agent.get_state('battery_level', 100.0)
        computing_status = self.agent.get_state('computing_status', 'idle')
        is_computing = computing_status == 'computing'
        cpu_usage = self.agent.get_state('cpu_usage', 10.0)
        memory_usage = self.agent.get_state('memory_usage', 20.0)

        # 基础CPU和内存使用率调整
        # 初始化cpu_usage为字典，与computation_capabilities对应
        cpu_usage_dict = {}

        if is_computing:
            # 如果正在计算，根据文件类型和大小调整CPU和内存使用率
            for file_type, type_factor in self.computation_capabilities.items():
                # 为每种文件类型计算CPU使用率
                cpu_usage_dict[file_type] = min(100.0, 30.0 + 40.0 * type_factor)

            memory_usage_factor = 1.0
            memory_usage = min(100.0, 20.0 + 30.0 * memory_usage_factor)
        else:
            # 如果不在计算，为所有文件类型设置相同的基础CPU使用率
            for file_type in self.computation_capabilities.keys():
                cpu_usage_dict[file_type] = cpu_usage

        # 应用环境和条件因素
        # 1. 低电量会降低CPU使用率以省电
        if battery_level < 20:
            for file_type in cpu_usage_dict:
                cpu_usage_dict[file_type] *= 0.7
        elif battery_level < 10:
            for file_type in cpu_usage_dict:
                cpu_usage_dict[file_type] *= 0.5

        # 基础处理能力 (MIPS)
        base_processing_power = self.cpu_cores * 100.0

        # 应用处理因子
        processing_power = base_processing_power * self.processing_factor

        # 如果内存使用率非常高，性能可能会下降
        if memory_usage > 90:
            processing_power *= 0.8

        # 计算效率和估计时间
        computation_efficiency = {}

        if is_computing:
            # 根据文件类型调整计算效率
            for file_type, type_factor in self.computation_capabilities.items():
                type_computation_efficiency = type_factor * (1.0 - 0.2 * (cpu_usage / 100.0))
                computation_efficiency[file_type] = type_computation_efficiency


        # 返回计算指标
        return {
            'processing_power': processing_power,
            'computation_efficiency': computation_efficiency
        }