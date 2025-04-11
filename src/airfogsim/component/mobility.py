import math
from airfogsim.core.component import Component
from typing import List, Dict, Any, Optional

class MoveToComponent(Component):
    """
    移动组件，用于执行位置移动任务。
    
    性能指标：
    - speed: 移动速度（单位/秒）
    - energy_consumption: 能源消耗率（每秒瓦特）
    - direction: 移动方向
    """
    PRODUCED_METRICS = ['speed', 'energy_consumption', 'direction']  # 移除position
    MONITORED_STATES = ['battery_level', 'status', 'external_force'] # 监控的状态
    
    def __init__(self, env, agent, name: Optional[str] = None,
                 supported_events: List[str] = ['speed_changed'], properties: Optional[Dict] = None):
        """
        初始化移动组件
        
        Args:
            env: 仿真环境
            agent: 所属代理
            name: 组件名称
            supported_events: 支持的额外事件
            properties: 组件属性，包含speed_factor和energy_factor
        """
        super().__init__(env, agent, name or "MoveTo", supported_events, properties)
        self.speed_factor = self.properties.get('speed_factor', 1.0)  # 默认速度因子为1.0
        self.energy_factor = self.properties.get('energy_factor', 1.0)  # 默认能量因子为1.0

    def _calculate_performance_metrics(self) -> Dict[str, Any]:
        """计算基于当前代理状态的性能指标"""
        # 从代理状态获取关键信息
        position = self.agent.get_state('position', (0, 0, 0))
        battery_level = self.agent.get_state('battery_level', 100.0)
        agent_status = self.agent.get_state('status', 'idle')
        # Ensure external_force is a list/tuple of 3 numbers
        raw_external_force = self.agent.get_state('external_force', [0.0, 0.0, 0.0])
        if not (isinstance(raw_external_force, (list, tuple)) and len(raw_external_force) == 3):
            external_force = [0.0, 0.0, 0.0] # Default to zero vector if invalid
        else:
            external_force = list(raw_external_force) # Ensure it's a list

        # 计算速度和能量消耗
        
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
        
        # 应用速度因子
        adjusted_speed = base_speed * self.speed_factor

        # --- 考虑外部力的影响 (简化模型) ---
        # 假设外部力主要影响能量消耗，并可能轻微影响速度
        # Calculate magnitude of external force (simplified, assumes force vector components)
        force_magnitude = math.sqrt(sum(f**2 for f in external_force))

        # Example: Increase energy consumption based on force magnitude
        # Need a scaling factor based on agent's mass, drag, etc.
        force_energy_penalty = force_magnitude * 0.05 * self.energy_factor # Example penalty factor (adjust 0.05 as needed)
        energy_consumption = (adjusted_speed * self.energy_factor) + force_energy_penalty

        # Example: Slightly reduce speed if force is significant (e.g., headwind)
        # This is highly simplified. Real model needs force direction relative to movement direction.
        speed_reduction_factor = max(0.1, 1.0 - force_magnitude * 0.01) # Example reduction (ensure speed doesn't go below 10%)
        speed = adjusted_speed * speed_reduction_factor

        # Ensure non-negative values
        speed = max(0.0, speed)
        energy_consumption = max(0.0, energy_consumption)
        
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
    