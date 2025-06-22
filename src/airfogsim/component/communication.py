"""
AirFogSim通信组件模块

该模块定义了代理的通信能力，负责处理代理之间的数据传输。
主要功能包括：
1. 频谱资源管理和分配
2. 通信质量和性能计算
3. 与频谱管理器集成

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core.component import Component
from typing import List, Dict, Any, Optional, Tuple
import math
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class CommunicationComponent(Component):
    """代表代理的通信能力，管理无线通信资源的组件"""
    PRODUCED_METRICS = ['signal_strength', 'bandwidth', 'latency', 'transmission_rate', 'communication_quality']
    MONITORED_STATES = ['battery_level', 'position', 'trans_target_agent_id', 'status', 'transmitting_status']

    def __init__(self, env, agent, name: Optional[str] = None,
                 supported_events: List[str] = ['communication_status_changed'],
                 properties: Optional[Dict] = None):
        """
        初始化通信组件

        Args:
            env: 仿真环境
            agent: 所属代理
            name: 组件名称
            supported_events: 支持的额外事件
            properties: 组件属性，包含max_power、default_frequency等
        """
        super().__init__(env, agent, name or "Communication", supported_events, properties)

        # 从properties获取通信参数
        self.max_power = self.properties.get('max_power', 100.0)  # 最大发射功率(mW)
        self.current_power = self.properties.get('current_power', self.max_power)
        self.default_frequency = self.properties.get('default_frequency', (2400, 2500))  # 默认频率范围(MHz)
        self.antenna_gain = self.properties.get('antenna_gain', 1.0)  # 天线增益

        # 获取频谱管理器
        self.frequency_manager = getattr(env, 'frequency_manager', None)
        if not self.frequency_manager:
            logger.warning(f"警告: 环境中没有频谱管理器，通信组件将使用模拟数据")
        self.current_frequency_ids = []


    def _calculate_performance_metrics(self) -> Dict[str, Any]:
        """
        计算基于当前代理状态的性能指标

        此方法会根据代理的当前状态计算通信性能指标，包括信号强度、带宽、延迟和传输速率。
        如果代理正在传输数据，还会计算与目标代理的通信质量。

        Returns:
            Dict[str, Any]: 包含性能指标的字典
        """
        # 获取代理当前状态
        battery_level = self.agent.get_state('battery_level', 100.0)
        position = self.agent.get_state('position', (0, 0, 0))
        transmitting_status = self.agent.get_state('transmitting_status', 'idle')
        is_transmitting = transmitting_status == 'transmitting'
        trans_target_agent_id = self.agent.get_state('trans_target_agent_id')
        current_frequency_ids = self.current_frequency_ids

        # 基础信号强度 (dBm)
        base_signal_strength = 10 * math.log10(self.current_power * self.antenna_gain)

        # 应用环境和条件因素
        # 1. 低电量会降低发射功率
        if battery_level < 20:
            base_signal_strength -= 3  # 降低3dBm
        elif battery_level < 10:
            base_signal_strength -= 6  # 降低6dBm

        # 2. 如果有频率资源，考虑频率资源的影响
        bandwidth = 0.0  # 默认带宽 (MHz)，将累加所有资源块的带宽
        latency_sum = 0.0  # 用于计算平均延迟
        latency_count = 0  # 用于计算平均延迟的资源块数量
        transmission_rate = 0.0  # 默认传输速率 (Mbps)，将累加所有资源块的速率
        avg_snr = 0.0  # 平均信噪比

        # 获取当前频率资源
        current_frequency_resources = []
        if current_frequency_ids and self.frequency_manager:
            current_frequency_resources = self.frequency_manager.get_resources(current_frequency_ids)

        # 遍历所有频率资源块，累加带宽和传输速率
        for current_frequency_resource in current_frequency_resources:
            # 获取频率资源的干扰水平
            interference = current_frequency_resource.interference

            # 获取信噪比
            snr = current_frequency_resource.sinr
            avg_snr += snr

            # 根据信噪比估算当前资源块的带宽和延迟
            resource_bandwidth = 0.0
            resource_latency = 0.0
            resource_transmission_rate = 0.0

            if snr > 20:  # 很好的信号
                resource_bandwidth = current_frequency_resource.bandwidth
                resource_latency = 20.0
                resource_transmission_rate = 10.0
            elif snr > 10:  # 良好的信号
                resource_bandwidth = current_frequency_resource.bandwidth * 0.8
                resource_latency = 50.0
                resource_transmission_rate = 5.0
            else:  # 较差的信号
                resource_bandwidth = current_frequency_resource.bandwidth * 0.5
                resource_latency = 100.0
                resource_transmission_rate = 1.0

            # 考虑干扰的影响
            if interference > 0.3:
                resource_bandwidth *= (1 - interference)
                resource_latency *= (1 + interference)
                resource_transmission_rate *= (1 - interference)

            # 累加带宽和传输速率
            bandwidth += resource_bandwidth
            latency_sum += resource_latency
            latency_count += 1
            transmission_rate += resource_transmission_rate

        # 计算平均延迟
        latency = latency_sum / latency_count if latency_count > 0 else 100.0

        # 计算平均信噪比
        avg_snr = avg_snr / len(current_frequency_resources) if current_frequency_resources else 0.0

        # 计算通信质量
        communication_quality = 0.0
        if is_transmitting and trans_target_agent_id:
            # 使用平均信噪比计算信号强度
            signal_strength = base_signal_strength + avg_snr

            # 计算综合通信质量 (0-100)
            normalized_signal = min(100, max(0, (signal_strength + 100) * 1.4))
            normalized_latency = min(100, max(0, 100 - latency / 2))
            normalized_bandwidth = min(100, max(0, bandwidth * 5))

            communication_quality = (normalized_signal * 0.4 + normalized_latency * 0.3 + normalized_bandwidth * 0.3)

            # 如果正在传输但没有频率资源，尝试分配
            if not current_frequency_ids and self.frequency_manager:
                self._request_frequency_resource()
        else:
            # 如果不在传输但有频率资源，释放资源
            if current_frequency_ids and self.frequency_manager:
                self._release_frequency_resource()

        return {
            'signal_strength': base_signal_strength,
            'bandwidth': bandwidth,
            'latency': latency,
            'transmission_rate': transmission_rate,
            'communication_quality': communication_quality
        }

    def _request_frequency_resource(self) -> bool:
        """
        内部方法：请求分配频率资源

        Returns:
            bool: 是否成功分配资源
        """
        if not self.frequency_manager:
            return False

        # 获取目标代理ID
        trans_target_agent_id = self.agent.get_state('trans_target_agent_id')
        if not trans_target_agent_id:
            return False

        # 设置默认需求
        requirements = {
            'preferred_frequency': (self.default_frequency[0] + self.default_frequency[1]) / 2,
            'min_bandwidth': 20.0
        }

        # 转换功率从mW到dBm
        power_db = 10 * math.log10(self.current_power)

        # 请求分配资源
        resource_ids = self.frequency_manager.request_resource(
            source_id=self.agent.id,
            target_id=trans_target_agent_id,
            power_db=power_db,
            requirements=requirements
        )

        if resource_ids:
            # 保存所有分配的资源ID
            self.current_frequency_ids=resource_ids

            logger.info(f"时间 {self.env.now}: 代理 {self.agent.id} 成功分配频率资源 {resource_ids}")

            return True

        return False

    def _release_frequency_resource(self) -> bool:
        """
        内部方法：释放当前分配的频率资源

        Returns:
            bool: 是否成功释放资源
        """
        current_frequency_ids = self.current_frequency_ids
        if not self.frequency_manager or not current_frequency_ids:
            return False


        if self.frequency_manager.release_resource(self.agent.id, self.agent.get_state('trans_target_agent_id')):
            # 清空当前频率ID列表
            self.current_frequency_ids = []

            logger.info(f"时间 {self.env.now}: 代理 {self.agent.id} 释放频率资源 {current_frequency_ids}")

            return True

        return False