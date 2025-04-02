from airfogsim.core.enums import TaskStatus
import math
from airfogsim.core.component import Component
from typing import List, Dict, Any, Optional, Tuple

class MoveToComponent(Component):
    """
    移动组件，用于执行位置移动任务。
    
    性能指标：
    - speed: 移动速度（单位/秒）
    - energy_consumption: 能源消耗率（每秒瓦特）
    - direction: 移动方向
    """
    PRODUCED_METRICS = ['speed', 'energy_consumption', 'direction']  # 移除position
    MONITORED_STATES = ['battery_level', 'status']  # 移除position，只监控电池电量和状态
    
    def __init__(self, env, agent, name: Optional[str] = None,
                 initial_position: tuple = (0, 0, 0),
                 speed_factor: float = 1.0,
                 energy_factor: float = 0.2,
                 supported_events: List[str] = ['position_changed']):
        """
        初始化移动组件
        
        Args:
            env: 仿真环境
            agent: 所属代理
            name: 组件名称
            initial_position: 初始位置坐标 (x,y,z)，如果只提供(x,y)则自动补充z=0
            speed_factor: 速度因子，影响移动速度
            energy_factor: 能源消耗因子
            supported_events: 支持的额外事件
        """
        super().__init__(env, agent, name or "MoveTo", supported_events)
        
        # 确保初始位置是三维的
        if len(initial_position) == 2:
            initial_position = (initial_position[0], initial_position[1], 0)
            
        # 设置代理的初始位置
        self.agent.set_state('position', initial_position)
        self.speed_factor = speed_factor
        self.energy_factor = energy_factor
    
    def get_resource_requirements(self, task) -> List[Dict]:
        """
        根据任务确定所需的空域资源
        使用AirspaceManager.get_airspace_at_coordinates方法查找空域
        """
        # 获取任务类型
        task_type = task.properties.get('movement_type', 'normal')
        
        # 获取目标位置和当前位置
        target_position = None
        if hasattr(task, 'target_position'):
            target_position = task.target_position
        elif 'target_position' in task.properties:
            target_position = task.properties['target_position']
        elif 'position' in task.target_state:
            target_position = task.target_state['position']
        
        # 获取当前位置
        current_position = self.agent.get_state('position', (0, 0, 0))
        
        # 确保位置是三维的
        if current_position and len(current_position) == 2:
            current_position = (current_position[0], current_position[1], 0)
        
        if target_position and len(target_position) == 2:
            target_position = (target_position[0], target_position[1], 0)
        
        # 如果有当前位置和目标位置，查找包含当前位置的空域
        # 这是一个简化的实现，只关注起点所在的空域
        if current_position:
            x, y, z = current_position
            containing_airspaces = self.env.airspace_manager.get_airspace_at_coordinates(x, y, z)
            
            # 如果找到空域，使用第一个
            if containing_airspaces:
                airspace = containing_airspaces[0]
                
                # 创建资源请求
                resource_spec = {
                    'type': 'airspace',
                    'airspace_id': airspace.id,
                    'purpose': task_type,
                    'priority': task.properties.get('priority', 'normal')
                }
                
                return [resource_spec]
        
        # 如果没有找到合适的空域，尝试通过资源ID直接访问
        # 这是一个备用方案，假设有一个默认空域
        resources = self.env.airspace_manager.resources
        if resources:
            # 获取第一个可用的空域资源ID
            first_airspace_id = next(iter(resources.keys()))
            
            # 创建资源请求
            resource_spec = {
                'type': 'airspace',
                'airspace_id': first_airspace_id,
                'purpose': task_type,
                'priority': task.properties.get('priority', 'normal')
            }
            
            return [resource_spec]
        
        # 如果无法找到空域，返回空列表
        return []
    
    def _allocate_task_resources(self, task) -> List[Tuple]:
        """
        为移动任务分配空域资源
        返回格式为 [(resource_type, allocation_id), ...] 的列表
        """
        resource_allocations = []
        required_resources = self.get_resource_requirements(task)
        
        for resource_req in required_resources:
            if resource_req['type'] == 'airspace':
                airspace_id = resource_req.get('airspace_id')
                if airspace_id:
                    # 分配空域资源
                    allocation_id = self.env.airspace_manager.allocate_resource(
                        airspace_id, self.agent_id, 
                    )
                        
                    if allocation_id:
                        resource_allocations.append(('airspace', allocation_id))
        
        return resource_allocations
    
    def _release_task_resources(self, task_id: str, allocations: List[Tuple]):
        """
        释放移动任务使用的空域资源
        """
        for res_type, allocation_id in allocations:
            if res_type == 'airspace':
                self.env.airspace_manager.release_allocation(allocation_id)
    
    def _calculate_performance_metrics(self) -> Dict[str, Any]:
        """计算基于当前代理状态的性能指标"""
        # 从代理状态获取关键信息
        position = self.agent.get_state('position', (0, 0, 0))
        battery_level = self.agent.get_state('battery_level', 100.0)
        agent_status = self.agent.get_state('status', 'idle')
        
        # 基础速度取决于电池电量
        base_speed = 15.0  # 默认15米/秒
        if battery_level < 20:
            # 低电量时速度降低
            base_speed = 10.0
        if battery_level < 10:
            # 极低电量时速度显著降低
            base_speed = 5.0
        if battery_level < 1e-6:
            # 电量耗尽，速度为零
            base_speed = 0.0
            
        # 查看是否有活跃的空域分配，这可能会影响性能
        active_allocations = []
        for task_id, allocations in self.task_resource_allocations.items():
            active_allocations.extend(allocations)
        
        # 检查空域限制
        max_speed_limit = None
        for res_type, alloc_id in active_allocations:
            if res_type == 'airspace':
                # 获取分配信息并查找空域速度限制
                # 注意：ResourceManager的API可能与之前不同
                if alloc_id in self.env.airspace_manager.allocations:
                    alloc_info = self.env.airspace_manager.allocations[alloc_id]
                    if alloc_info and 'resource_id' in alloc_info:
                        airspace_id = alloc_info['resource_id']
                        if airspace_id in self.env.airspace_manager.resources:
                            airspace = self.env.airspace_manager.resources[airspace_id]
                            if hasattr(airspace, 'attributes') and 'max_speed' in airspace.attributes:
                                speed_limit = airspace.attributes['max_speed']
                                if max_speed_limit is None or speed_limit < max_speed_limit:
                                    max_speed_limit = speed_limit
        
        # 应用速度限制
        speed = base_speed * self.speed_factor
        if max_speed_limit is not None:
            speed = min(speed, max_speed_limit)
            
        # 计算能量消耗
        energy_consumption = speed * self.energy_factor
        
        # 计算方向（如果可能）
        direction = 'unknown'
        target_position = None
        
        # 从活跃任务中获取目标位置
        for task_id in self.active_tasks:
            task = self.active_tasks[task_id]
            if hasattr(task, 'target_position'):
                target_position = task.target_position
                break
            elif 'target_position' in task.properties:
                target_position = task.properties['target_position']
                break
            elif 'position' in task.target_state:
                target_position = task.target_state['position']
                break
        
        # 如果有目标位置，计算方向
        if target_position and position:
            dx = target_position[0] - position[0]
            dy = target_position[1] - position[1]
            
            # 计算水平平面上的距离
            horizontal_distance = math.sqrt(dx**2 + dy**2)
            
            # 确定大致方向（水平平面上）
            if horizontal_distance > 0.1:  # 避免零距离时的方向不确定性
                if abs(dx) > abs(dy):
                    # 主要是东/西方向
                    direction = 'east' if dx > 0 else 'west'
                else:
                    # 主要是南/北方向
                    direction = 'north' if dy > 0 else 'south'
                    
                # 对角线方向处理
                if abs(dx) > 0.3 * horizontal_distance and abs(dy) > 0.3 * horizontal_distance:
                    if dx > 0 and dy > 0:
                        direction = 'northeast'
                    elif dx > 0 and dy < 0:
                        direction = 'southeast'
                    elif dx < 0 and dy > 0:
                        direction = 'northwest'
                    else:
                        direction = 'southwest'
        
        return {
            'speed': speed,
            'energy_consumption': energy_consumption,
            'direction': direction
        }