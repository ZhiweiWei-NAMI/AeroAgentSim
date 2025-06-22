"""
AirFogSim物流组件模块

该模块定义了物流组件，负责处理无人机的物流任务执行。
主要功能包括：
1. 货物取件处理
2. 货物交付处理
3. 物流任务性能计算
4. 物流状态管理

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core.component import Component
from airfogsim.task.logistics import PickupTask, HandoverTask
from typing import Dict, Any, List, Tuple
import warnings

class LogisticsComponent(Component):
    """物流组件，负责处理无人机的物流任务"""

    # 组件产生的性能指标
    PRODUCED_METRICS = [
        'pickup_processing_time',  # 取件处理时间
        'handover_processing_time'  # 交付处理时间
    ]

    # 组件关心的代理状态
    MONITORED_STATES = [
        'position',              # 位置
        'battery_level',         # 电池电量
        'payload_ids',           # 无人机当前携带的货物ID
        'current_payload_weight', # 无人机当前负载重量 (kg)
        'max_payload_weight',    # 无人机最大负载重量 (kg)
        'current_payload_volume', # 无人机当前负载容积 (m³)
        'max_payload_volume',    # 无人机最大负载容积 (m³)
    ]

    def __init__(self, env, agent, name="Logistics",
                 supported_events=None, properties=None):
        """
        初始化物流组件

        Args:
            env: 仿真环境
            agent: 所属代理
            name: 组件名称，默认为"Logistics"
            supported_events: 支持的事件列表
            properties: 组件属性，可包含以下内容：
                - pickup_speed: 取件速度（默认为1.0，单位：件/分钟）
                - handover_speed: 交付速度（默认为1.0，单位：件/分钟）
                - max_payload_weight: 最大载重（默认为5.0，单位：kg）
                - max_payload_dimensions: 最大货物尺寸（默认为[0.5, 0.5, 0.5]，单位：m）
        """
        # 设置默认支持的事件
        if supported_events is None:
            supported_events = [
                'pickup_started', 'pickup_completed',
                'handover_started', 'handover_completed'
            ]

        # 设置默认属性
        properties = properties or {}

        # 调用父类初始化
        super().__init__(env, agent, name, supported_events, properties)

        # 组件特定属性
        self.pickup_speed = properties.get('pickup_speed', 1.0)  # 取件速度（件/分钟）
        self.handover_speed = properties.get('handover_speed', 1.0)  # 交付速度（件/分钟）
        self.max_payload_weight = properties.get('max_payload_weight', 5.0)  # 最大载重（kg）
        self.max_payload_dimensions = properties.get('max_payload_dimensions', [0.5, 0.5, 0.5])  # 最大货物尺寸（m）

        # 当前处理的货物信息
        self.current_payload = None

    def _calculate_performance_metrics(self) -> Dict[str, Any]:
        """
        基于当前代理状态计算组件性能指标

        Returns:
            Dict: 包含性能指标的字典
        """
        metrics = {}

        # 计算取件处理时间（分钟）
        base_pickup_time = 60.0 / self.pickup_speed

        # 计算交付处理时间（分钟）
        base_handover_time = 60.0 / self.handover_speed

        # 考虑电池电量对性能的影响
        battery_level = self.agent.get_state('battery_level', 100.0)
        battery_factor = 1.0
        if battery_level < 20.0:
            # 低电量时性能降低
            battery_factor = max(0.5, battery_level / 20.0)

        # 考虑货物重量对性能的影响
        weight_factor = 1.0
        payload_ids = self.agent.get_state('payload_ids', [])
        if payload_ids:  # 检查是否携带货物
            current_payload_weight = self.agent.get_state('current_payload_weight', 0.0)
            max_payload_weight = self.agent.get_state('max_payload_weight', self.max_payload_weight)
            if current_payload_weight > 0:
                # 货物越重，处理时间越长
                weight_factor = 1.0 + (current_payload_weight / max_payload_weight) * 0.5

        # 计算最终性能指标
        metrics['pickup_processing_time'] = base_pickup_time / battery_factor
        metrics['handover_processing_time'] = base_handover_time / battery_factor * weight_factor

        return metrics

    def can_execute(self, task) -> bool:
        """
        检查组件是否可以执行指定任务

        Args:
            task: 要执行的任务

        Returns:
            bool: 是否可以执行
        """
        # 首先检查组件名称是否匹配
        if not super().can_execute(task):
            return False

        # 检查任务类型是否支持
        if task.__class__.__name__ not in ['PickupTask', 'HandoverTask']:
            return False

        # 对于取件任务，检查代理是否有足够的负载能力
        if task.__class__.__name__ == 'PickupTask':
            # 获取当前负载重量和最大负载重量
            current_weight = self.agent.get_state('current_payload_weight', 0.0)
            max_weight = self.agent.get_state('max_payload_weight', self.max_payload_weight)

            # 获取当前负载容积和最大负载容积
            current_volume = self.agent.get_state('current_payload_volume', 0.0)
            max_volume = self.agent.get_state('max_payload_volume', 0.125)  # 默认0.125立方米

            # 获取任务中的货物信息
            payload_id = task.properties.get('payload_id')
            payload_weight = 0.0
            payload_volume = 0.0

            # 尝试从环境的payload_manager获取货物信息
            if hasattr(self.env, 'payload_manager') and payload_id:
                payload = self.env.payload_manager.get_payload(payload_id)
                if payload:
                    payload_weight = payload.get('weight', 0.0)

                    # 计算货物体积
                    if 'dimensions' in payload:
                        dimensions = payload['dimensions']
                        if len(dimensions) == 3:
                            payload_volume = dimensions[0] * dimensions[1] * dimensions[2]

            # 检查是否超过最大负载重量
            if current_weight + payload_weight > max_weight:
                task.fail(f"代理负载重量不足，当前:{current_weight}kg，需要:{payload_weight}kg，最大:{max_weight}kg")
                return False

            # 检查是否超过最大负载容积
            if current_volume + payload_volume > max_volume:
                task.fail(f"代理负载容积不足，当前:{current_volume}m³，需要:{payload_volume}m³，最大:{max_volume}m³")
                return False

        # 对于交付任务，检查代理是否携带货物
        if task.__class__.__name__ == 'HandoverTask':
            payload_id = task.properties.get('payload_id')
            payload_ids = self.agent.get_state('payload_ids', [])

            if not payload_ids or (payload_id and payload_id not in payload_ids):
                task.fail("代理没有携带指定货物，无法执行交付任务")
                return False

        return True

    def get_details(self) -> Dict:
        """获取组件详细信息"""
        details = {
            'name': self.name,
            'type': self.__class__.__name__,
            'pickup_speed': self.pickup_speed,
            'handover_speed': self.handover_speed,
            'max_payload_weight': self.max_payload_weight,
            'max_payload_dimensions': self.max_payload_dimensions,
            'current_payload': self.current_payload,
            'active_tasks': len(self.active_tasks),
            'metrics': self.current_metrics
        }
        return details