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

class PickupTask(Task):
    """
    取件任务
    
    该任务负责模拟代理在指定位置取件的过程，成功后将货物添加到代理的possessing_objects中。
    """
    NECESSARY_METRICS = ['pickup_processing_time']
    PRODUCED_STATES = ['status', 'carrying_payload']
    
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
        return {
            'status': 'picking_up' if self.progress < 1.0 else 'pickup_completed',
            'carrying_payload': self.progress >= 1.0
        }
    
    def _possessing_object_on_complete(self):
        """
        任务完成时，将货物添加到代理的possessing_objects中
        """
        # 创建货物对象
        payload_info = {
            'id': self.payload_id,
            'pickup_time': self.env.now,
            'pickup_location': self.pickup_location
        }
        
        # 将货物添加到代理的possessing_objects中
        self.agent.add_possessing_object('payload', payload_info)
        print(f"时间 {self.env.now}: 代理 {self.agent_id} 成功取件 {self.payload_id} 于位置 {self.pickup_location}")
    
    def _possessing_object_on_fail(self):
        """
        任务失败时的处理
        """
        print(f"时间 {self.env.now}: 代理 {self.agent_id} 取件失败: {self.failure_reason}")
    
    def _possessing_object_on_cancel(self):
        """
        任务取消时的处理
        """
        print(f"时间 {self.env.now}: 代理 {self.agent_id} 取件任务被取消")


class HandoverTask(Task):
    """
    交付任务
    
    该任务负责模拟代理在指定位置交付货物的过程，成功后将货物从代理的possessing_objects中移除。
    """
    NECESSARY_METRICS = ['handover_processing_time']
    PRODUCED_STATES = ['status', 'carrying_payload']
    
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
        payload = self.agent.get_possessing_object('payload')
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
        return {
            'status': 'delivering' if self.progress < 1.0 else 'delivery_completed',
            'carrying_payload': self.progress < 1.0 and self.has_payload
        }
    
    def _possessing_object_on_complete(self):
        """
        任务完成时，从代理的possessing_objects中移除货物
        """
        # 获取货物信息
        payload = self.agent.get_possessing_object('payload')
        if payload and payload.get('id') == self.payload_id:
            # 记录交付信息
            delivery_info = {
                'id': self.payload_id,
                'pickup_time': payload.get('pickup_time'),
                'pickup_location': payload.get('pickup_location'),
                'delivery_time': self.env.now,
                'delivery_location': self.delivery_location
            }
            
            # 从代理的possessing_objects中移除货物
            self.agent.remove_possessing_object('payload')
            print(f"时间 {self.env.now}: 代理 {self.agent_id} 成功交付货物 {self.payload_id} 于位置 {self.delivery_location}")
            
            # 可以在这里添加交付记录到环境或其他地方
            if hasattr(self.env, 'delivery_records'):
                self.env.delivery_records.append(delivery_info)
        else:
            print(f"时间 {self.env.now}: 代理 {self.agent_id} 无法交付货物 {self.payload_id}，未找到货物信息")
    
    def _possessing_object_on_fail(self):
        """
        任务失败时的处理
        """
        print(f"时间 {self.env.now}: 代理 {self.agent_id} 交付货物失败: {self.failure_reason}")
    
    def _possessing_object_on_cancel(self):
        """
        任务取消时的处理
        """
        print(f"时间 {self.env.now}: 代理 {self.agent_id} 交付任务被取消")
