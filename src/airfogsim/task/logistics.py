"""
AirFogSim物流任务模块

该模块定义了无人机物流任务的实现，负责模拟无人机取件和交付货物的过程。
主要功能包括：
1. 货物取件 - 在指定位置取件并将货物添加到代理的possessing_objects中
2. 货物交付 - 在指定位置交付货物并从代理的possessing_objects中移除
3. 更新代理的货物状态
4. 管理代理的货物资源

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core.task import Task
from typing import Dict, Any, Optional
import time
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class PickupTask(Task):
    """
    取件任务

    该任务负责模拟代理在指定位置取件的过程，成功后将货物添加到代理的possessing_objects中。
    """
    NECESSARY_METRICS = ['pickup_processing_time']
    PRODUCED_STATES = ['status', 'delivery_status', 'payload_ids', 'current_payload_weight', 'current_payload_volume']

    def __init__(self, env, agent, component_name, task_name,
                 workflow_id=None, target_state=None, properties=None):
        """
        初始化取件任务

        Args:
            env: 仿真环境
            agent: 代理
            component_name: 组件名称
            task_name: 任务名称
            workflow_id: 工作流ID
            target_state: 目标状态，通常包含position
            properties: 任务属性，应包含payload_id和pickup_location
        """
        # 确保属性不为空
        properties = properties or {}
        target_state = target_state or {}

        # 调用父类初始化
        super().__init__(env, agent, component_name, task_name,
                         workflow_id, target_state, properties)

        # 任务特定属性
        self.payload_id = properties.get('payload_id')
        self.pickup_location = properties.get('pickup_location', [0, 0, 0])
        self.source_agent_id = properties.get('source_agent_id')  # 添加源代理ID
        self.processing_time = 0

        # 检查代理是否已经在取件位置附近
        self.at_pickup_location = self._is_at_pickup_location()

    def _is_at_pickup_location(self):
        """检查代理是否在取件位置"""
        agent_position = self.agent.get_state('position')
        if not agent_position:
            return False

        # 检查是否在取件点附近（允许一定误差）
        tolerance = 1.0  # 1米误差范围
        return all(abs(agent_position[i] - self.pickup_location[i]) <= tolerance for i in range(3))

    def estimate_remaining_time(self, performance_metrics: Dict) -> float:
        """估计完成任务所需的剩余时间"""
        # 如果代理不在取件位置，需要先移动到取件位置
        if not self.at_pickup_location:
            # 这里简化处理，实际应该计算移动时间
            return float('inf')

        processing_time = performance_metrics.get('pickup_processing_time', float('inf'))
        return max(0, processing_time - self.processing_time)

    def _update_task_state(self, performance_metrics: Dict):
        """更新任务进度和内部状态"""
        # 更新代理是否在取件位置
        self.at_pickup_location = self._is_at_pickup_location()

        if not self.at_pickup_location:
            self.progress = 0.0
            return

        elapsed_time = self.env.now - self.last_update_time
        self.processing_time += elapsed_time

        total_processing_time = performance_metrics.get('pickup_processing_time', float('inf'))
        if total_processing_time > 0:
            self.progress = min(1.0, self.processing_time / total_processing_time)
        else:
            self.progress = 1.0

    def _get_task_specific_state_repr(self) -> Dict:
        """返回任务特定状态的表示"""
        # 获取当前payload_ids列表
        current_payload_ids = self.agent.get_state('payload_ids', [])

        # 如果任务完成，则添加当前payload_id
        if self.progress >= 1.0 and self.payload_id not in current_payload_ids:
            current_payload_ids = current_payload_ids + [self.payload_id]

        # 获取货物信息以更新重量和容积
        payload_info = None
        if hasattr(self.env, 'payload_manager') and self.payload_id:
            payload_info = self.env.payload_manager.get_payload(self.payload_id)

        # 计算新的重量和容积
        payload_weight = 0.0
        payload_volume = 0.0

        if payload_info and self.progress >= 1.0:
            payload_weight = payload_info.get('weight', 0.0)
            if 'dimensions' in payload_info:
                dimensions = payload_info['dimensions']
                if len(dimensions) == 3:
                    payload_volume = dimensions[0] * dimensions[1] * dimensions[2]

        # 当前重量和容积
        current_weight = self.agent.get_state('current_payload_weight', 0.0)
        current_volume = self.agent.get_state('current_payload_volume', 0.0)

        # 如果任务完成，则添加新的重量和容积
        if self.progress >= 1.0:
            current_weight += payload_weight
            current_volume += payload_volume

        # 基本状态更新
        return {
            'status': 'active',
            'payload_ids': current_payload_ids,
            'current_payload_weight': current_weight,
            'current_payload_volume': current_volume
        }
    def _possessing_object_on_complete(self):
        """
        任务完成时，将货物从源代理转移到当前代理的possessing_objects中
        """
        # 必须指定源代理，且源代理必须存在
        if not self.source_agent_id:
            logger.warning(f"时间 {self.env.now}: 代理 {self.agent_id} 取件失败，未指定源代理")
            self.fail("未指定源代理")
            return

        # 获取源代理
        source_agent = self.env.agents.get(self.source_agent_id)
        if not source_agent:
            logger.warning(f"时间 {self.env.now}: 代理 {self.agent_id} 无法从 {self.source_agent_id} 取件，源代理不存在")
            self.fail("源代理不存在")
            return

        # 从源代理获取货物信息
        payload_info = source_agent.get_possessing_object(self.payload_id)
        if not payload_info:
            logger.warning(f"时间 {self.env.now}: 代理 {self.agent_id} 无法从 {self.source_agent_id} 取件 {self.payload_id}，未找到货物")
            self.fail("源代理没有指定的货物")
            return

        # 从源代理移除货物
        source_agent.remove_possessing_object(self.payload_id)

        # 更新取件时间和位置
        payload_info['pickup_time'] = self.env.now
        payload_info['pickup_location'] = self.pickup_location

        # 将货物添加到当前代理的possessing_objects中
        self.agent.add_possessing_object(self.payload_id, payload_info)

        # 使用payload_manager标记货物已被取件
        if hasattr(self.env, 'payload_manager'):
            self.env.payload_manager.mark_payload_picked(self.payload_id, self.agent_id, self.agent.get_state('position'))

    def _possessing_object_on_fail(self):
        """
        任务失败时的处理
        """
        # logger.info(f"时间 {self.env.now}: 代理 {self.agent_id} 取件失败: {self.failure_reason}")
        pass

    def _possessing_object_on_cancel(self):
        """
        任务取消时的处理
        """
        logger.warning(f"时间 {self.env.now}: 代理 {self.agent_id} 取件任务被取消")


class HandoverTask(Task):
    """
    交付任务

    该任务负责模拟代理在指定位置交付货物的过程，成功后将货物从代理的possessing_objects中移除。
    """
    NECESSARY_METRICS = ['handover_processing_time']
    PRODUCED_STATES = ['status', 'payload_ids', 'current_payload_weight', 'current_payload_volume']

    def __init__(self, env, agent, component_name, task_name,
                 workflow_id=None, target_state=None, properties=None):
        """
        初始化交付任务

        Args:
            env: 仿真环境
            agent: 代理
            component_name: 组件名称
            task_name: 任务名称
            workflow_id: 工作流ID
            target_state: 目标状态
            properties: 任务属性，应包含payload_id和delivery_location
        """
        # 确保属性不为空
        properties = properties or {}
        target_state = target_state or {}

        # 调用父类初始化
        super().__init__(env, agent, component_name, task_name,
                         workflow_id, target_state, properties)

        # 任务特定属性
        self.payload_id = properties.get('payload_id')
        self.delivery_location = properties.get('delivery_location', [0, 0, 0])
        self.target_agent_id = properties.get('target_agent_id')  # 添加目标代理ID
        self.processing_time = 0

        # 检查代理是否已经在交付位置附近
        self.at_delivery_location = self._is_at_delivery_location()
        # 检查代理是否携带指定货物
        self.has_payload = self._has_payload()

    def _is_at_delivery_location(self):
        """检查代理是否在交付位置"""
        agent_position = self.agent.get_state('position')
        if not agent_position:
            return False

        # 检查是否在交付点附近（允许一定误差）
        tolerance = 1.0  # 1米误差范围
        return all(abs(agent_position[i] - self.delivery_location[i]) <= tolerance for i in range(3))

    def _has_payload(self):
        """检查代理是否携带指定货物"""
        payload = self.agent.get_possessing_object(self.payload_id)
        return payload is not None and payload.get('id') == self.payload_id

    def estimate_remaining_time(self, performance_metrics: Dict) -> float:
        """估计完成任务所需的剩余时间"""
        # 如果代理不在交付位置或没有携带货物，需要先满足这些条件
        if not self.at_delivery_location or not self.has_payload:
            return float('inf')

        processing_time = performance_metrics.get('handover_processing_time', float('inf'))
        return max(0, processing_time - self.processing_time)

    def _update_task_state(self, performance_metrics: Dict):
        """更新任务进度和内部状态"""
        # 更新代理是否在交付位置和是否携带货物
        self.at_delivery_location = self._is_at_delivery_location()
        self.has_payload = self._has_payload()

        if not self.at_delivery_location or not self.has_payload:
            self.progress = 0.0
            return

        elapsed_time = self.env.now - self.last_update_time
        self.processing_time += elapsed_time

        total_processing_time = performance_metrics.get('handover_processing_time', float('inf'))
        if total_processing_time > 0:
            self.progress = min(1.0, self.processing_time / total_processing_time)
        else:
            self.progress = 1.0

    def _get_task_specific_state_repr(self) -> Dict:
        """返回任务特定状态的表示"""
        # 获取当前payload_ids列表
        current_payload_ids = self.agent.get_state('payload_ids', [])

        # 如果任务完成，则从列表中移除当前payload_id
        if self.progress >= 1.0 and self.payload_id in current_payload_ids:
            current_payload_ids = [pid for pid in current_payload_ids if pid != self.payload_id]

        # 获取货物信息以更新重量和容积
        payload_info = None
        if self.has_payload:
            payload_info = self.agent.get_possessing_object(self.payload_id)

        # 计算需要减去的重量和容积
        payload_weight = 0.0
        payload_volume = 0.0

        if payload_info and self.progress >= 1.0:
            payload_weight = payload_info.get('weight', 0.0)
            if 'dimensions' in payload_info:
                dimensions = payload_info['dimensions']
                if len(dimensions) == 3:
                    payload_volume = dimensions[0] * dimensions[1] * dimensions[2]

        # 当前重量和容积
        current_weight = self.agent.get_state('current_payload_weight', 0.0)
        current_volume = self.agent.get_state('current_payload_volume', 0.0)

        # 如果任务完成，则减去移除的重量和容积
        if self.progress >= 1.0:
            current_weight = max(0.0, current_weight - payload_weight)
            current_volume = max(0.0, current_volume - payload_volume)

        # 基本状态更新
        return {
            'status': 'active',
            'payload_ids': current_payload_ids,
            'current_payload_weight': current_weight,
            'current_payload_volume': current_volume
        }

    def _possessing_object_on_complete(self):
        """
        任务完成时，将货物从当前代理转移到目标代理
        """
        # 必须指定目标代理，且目标代理必须存在
        if not self.target_agent_id:
            logger.warning(f"时间 {self.env.now}: 代理 {self.agent_id} 交付失败，未指定目标代理")
            self.fail("未指定目标代理")
            return

        # 获取目标代理
        target_agent = self.env.agents.get(self.target_agent_id)
        if not target_agent:
            logger.warning(f"时间 {self.env.now}: 代理 {self.agent_id} 无法交付货物给 {self.target_agent_id}，目标代理不存在")
            self.fail("目标代理不存在")
            return

        # 获取货物信息，直接使用payload_id作为key
        payload = self.agent.get_possessing_object(self.payload_id)
        if not payload:
            logger.warning(f"时间 {self.env.now}: 代理 {self.agent_id} 无法交付货物 {self.payload_id}，未找到货物信息")
            self.fail("未找到货物信息")
            return


        # 从当前代理的possessing_objects中移除货物
        self.agent.remove_possessing_object(self.payload_id)

        # 将货物转移给目标代理
        target_agent.add_possessing_object(self.payload_id, payload)
        if hasattr(self.env, 'payload_manager'):
            self.env.payload_manager.mark_payload_delivered(self.payload_id, self.agent_id, self.agent.get_state('position'))


    def _possessing_object_on_fail(self):
        """
        任务失败时的处理
        """
        logger.warning(f"时间 {self.env.now}: 代理 {self.agent_id} 交付货物失败: {self.failure_reason}")

    def _possessing_object_on_cancel(self):
        """
        任务取消时的处理
        """
        logger.warning(f"时间 {self.env.now}: 代理 {self.agent_id} 交付任务被取消")
