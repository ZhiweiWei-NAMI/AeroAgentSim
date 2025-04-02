from airfogsim.core.component import Component
from typing import List, Dict, Any, Optional, Tuple
import math

class ChargingComponent(Component):
    """负责管理电池充电的组件"""
    PRODUCED_METRICS = ['charging_rate', 'time_to_full', 'energy_level']
    MONITORED_STATES = ['battery_level', 'battery_capacity', 'position']  # 监控这些代理状态的变化
    
    def __init__(self, env, agent, name: Optional[str] = None,
                 charging_factor: float = 1.0,
                 charging_efficiency: float = 0.85,
                 supported_events: List[str] = ['charging_started', 'charging_completed']):
        """
        初始化充电组件
        
        Args:
            env: 仿真环境
            agent: 所属代理
            name: 组件名称
            charging_factor: 充电速度因子
            charging_efficiency: 充电效率（输入能量转化为电池电量的比率）
            supported_events: 支持的额外事件
        """
        super().__init__(env, agent, name or "Charging", supported_events)
        
        self.charging_factor = charging_factor
        self.charging_efficiency = charging_efficiency
        
        # 确保agent有电池状态
        if not self.agent.has_state('battery_level'):
            self.agent.set_state('battery_level', 100.0)  # 默认满电
            
        if not self.agent.has_state('battery_capacity'):
            self.agent.set_state('battery_capacity', 5000.0)  # 默认容量，单位mAh
    
    def get_resource_requirements(self, task) -> List[Dict]:
        """
        根据任务确定所需的着陆区和充电资源
        """
        charging_task_type = task.properties.get('charging_type', 'normal')
        
        # 获取当前位置
        current_position = self.agent.get_state('position', (0, 0, 0))
        
        # 确保位置是三维的
        if current_position and len(current_position) == 2:
            current_position = (current_position[0], current_position[1], 0)
            
        # 查找当前位置附近的着陆区
        nearby_landing_spots = self.env.landing_manager.find_nearest_landing_spot(
            x=current_position[0], y=current_position[1], altitude=current_position[2],
            require_charging=True
        )
        
        if nearby_landing_spots:
            # 选择第一个有充电能力的着陆区
            landing_id = nearby_landing_spots.id
            
            # 创建资源请求
            resource_spec = {
                'type': 'landing',
                'landing_id': landing_id,
                'purpose': 'charging',
                'priority': task.properties.get('priority', 'normal')
            }
            
            return [resource_spec]
        
        # 如果找不到合适的着陆区，返回空列表
        return []
    
    def _allocate_task_resources(self, task) -> List[Tuple]:
        """
        为充电任务分配着陆区资源
        返回格式为 [(resource_type, allocation_id), ...] 的列表
        """
        resource_allocations = []
        required_resources = self.get_resource_requirements(task)
        
        for resource_req in required_resources:
            if resource_req['type'] == 'landing':
                landing_id = resource_req.get('landing_id')
                if landing_id:
                    # 分配着陆区资源 - 不传递额外属性
                    allocation_id, resource_id = self.env.landing_manager.allocate_resource(
                        self.agent_id, requirements={
                            'landing_id': landing_id
                        }
                    )
                    
                    if allocation_id:
                        resource_allocations.append(('landing', allocation_id))
                        
                        # 触发充电开始事件
                        self.trigger_event('charging_started', {
                            'landing_id': landing_id,
                            'time': self.env.now,
                            'battery_level': self.agent.get_state('battery_level', 0.0)
                        })
        
        return resource_allocations
    
    def _release_task_resources(self, task_id: str, allocations: List[Tuple]):
        """
        释放充电任务使用的着陆区资源
        """
        # 触发充电完成事件
        battery_level = self.agent.get_state('battery_level', 0.0)
        self.trigger_event('charging_completed', {
            'time': self.env.now,
            'battery_level': battery_level
        })
        
        # 释放资源
        for res_type, allocation_id in allocations:
            if res_type == 'landing':
                self.env.landing_manager.release_allocation(allocation_id)
    
    def _calculate_performance_metrics(self) -> Dict[str, Any]:
        """计算基于当前代理状态和资源分配的性能指标"""
        # 获取当前的电池电量和容量
        battery_level = self.agent.get_state('battery_level', 0.0)  # 百分比
        battery_capacity = self.agent.get_state('battery_capacity', 5000.0)  # mAh
        
        # 初始化充电率为0（未充电状态）
        charging_rate = 0.0
        time_to_full = float('inf')
        
        # 检查是否有有效的着陆区分配
        landing_allocation = None
        for task_id, allocations in self.task_resource_allocations.items():
            for res_type, alloc_id in allocations:
                if res_type == 'landing':
                    landing_allocation = self.env.landing_manager.get_allocation(alloc_id)
                    if landing_allocation:
                        break
            if landing_allocation:
                break
        
        if landing_allocation:
            # 获取着陆区资源
            landing_id = landing_allocation.get('resource_id')
            landing_spot = self.env.landing_manager.get_landing_spot(landing_id)
            
            if landing_spot and landing_spot.has_charging:
                # 获取充电功率
                charging_power = landing_spot.attributes.get('charging_power', 100.0)
                
                # 计算充电率 (% / 小时)
                # 充电功率 * 充电效率 / 电池容量 * 100%
                # 假设充电功率单位为W，电池容量为mAh，电压为标准的3.7V
                voltage = 3.7  # 锂电池标准电压
                capacity_wh = battery_capacity * voltage / 1000  # 转换mAh到Wh
                
                # 每小时充电百分比 = (充电功率 * 充电效率 / 电池容量Wh) * 100%
                charging_rate = (charging_power * self.charging_efficiency / capacity_wh) * 100.0
                
                # 应用充电因子
                charging_rate *= self.charging_factor
                
                # 计算充满所需时间（小时）
                if charging_rate > 0:
                    time_to_full = (100.0 - battery_level) / charging_rate
                else:
                    time_to_full = float('inf')
        
        return {
            'charging_rate': charging_rate,  # %/小时
            'time_to_full': time_to_full,    # 小时
            'energy_level': battery_level    # %
        }