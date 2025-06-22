import math
import simpy
from airfogsim.core.component import Component
from typing import List, Dict, Any, Optional
from airfogsim.utils.logging_config import get_logger
logger = get_logger(__name__)

class MoveToComponent(Component):
    """
    移动组件，用于执行位置移动任务。

    性能指标：
    - speed: 移动速度（单位/秒）
    - energy_consumption: 能源消耗率（每秒瓦特）
    - direction: 移动方向
    """
    PRODUCED_METRICS = ['speed', 'energy_consumption', 'direction']  # 移除position
    MONITORED_STATES = ['battery_level', 'external_force', 'moving_status', 'max_allowed_speed'] # 监控的状态

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

        # 悬停能量消耗进程
        self.hovering_process = None

        # 注册状态变化监听器
        self.state_listener_id = f"{self.agent_id}_{self.name}_moving_status_listener"
        self.env.event_registry.subscribe(
            self.agent_id,
            'state_changed',
            f'{self.state_listener_id}_hovering',
            self._handle_moving_status_change
        ).add_source_filter(lambda event_data: event_data.get('key') == 'moving_status')

    def _calculate_performance_metrics(self) -> Dict[str, Any]:
        """计算基于当前代理状态的性能指标"""
        # 从代理状态获取关键信息
        position = self.agent.get_state('position', (0, 0, 0))
        battery_level = self.agent.get_state('battery_level', 100.0)
        moving_status = self.agent.get_state('moving_status', 'idle')
        max_speed = self.agent.get_state('max_allowed_speed', 15.0)

        # Ensure external_force is a list/tuple of 3 numbers
        raw_external_force = self.agent.get_state('external_force', [0.0, 0.0, 0.0])
        if not (isinstance(raw_external_force, (list, tuple)) and len(raw_external_force) == 3):
            external_force = [0.0, 0.0, 0.0] # Default to zero vector if invalid
        else:
            external_force = list(raw_external_force) # Ensure it's a list

        # 计算速度和能量消耗

        # 如果移动状态不是flying，速度为0
        if moving_status != 'flying':
            speed = 0.0
            # 悬停状态下的能量消耗
            energy_consumption = self._calculate_hovering_energy_consumption(external_force)
        else:
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
            speed = min(speed, max_speed)
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

    def _calculate_hovering_energy_consumption(self, external_force=None):
        """
        计算悬停状态下的能量消耗

        Args:
            external_force: 外部力向量，默认为None，会从代理状态获取

        Returns:
            float: 悬停状态下的能量消耗值
        """
        # 悬停基础能量消耗（比飞行状态低）
        # 假设悬停每秒消耗0.2%的电量
        energy_consumed = 0.2 * self.energy_factor

        # 获取外部力
        if external_force is None:
            raw_external_force = self.agent.get_state('external_force', [0.0, 0.0, 0.0])
            if not (isinstance(raw_external_force, (list, tuple)) and len(raw_external_force) == 3):
                external_force = [0.0, 0.0, 0.0]
            else:
                external_force = raw_external_force

        # 计算外部力对悬停能耗的影响
        force_magnitude = math.sqrt(sum(f**2 for f in external_force))
        force_energy_penalty = force_magnitude * 0.01 * self.energy_factor
        energy_consumed += force_energy_penalty

        # 确保非负值
        return max(0.0, energy_consumed)

    def _handle_moving_status_change(self, event_data):
        """处理移动状态变化事件"""
        new_status = event_data.get('new_value')
        old_status = event_data.get('old_value')

        # 如果状态变为hovering，启动悬停能量消耗进程
        if new_status == 'hovering':
            # 检查组件是否处于错误状态
            if self.is_error:
                logger.warning(f"时间 {self.env.now}: {self.agent_id} 的 {self.name} 组件处于错误状态，不启动悬停能量消耗进程")
                return

            if self.hovering_process is None or not self.hovering_process.is_alive:
                self.hovering_process = self.env.process(self._hovering_energy_consumption())
                # print(f"时间 {self.env.now}: {self.agent_id} 开始悬停，启动悬停能量消耗进程")
        # 如果状态从hovering变为其他状态，停止悬停能量消耗进程
        elif old_status == 'hovering' and self.hovering_process and self.hovering_process.is_alive:
            self.hovering_process.interrupt()
            self.hovering_process = None
            # print(f"时间 {self.env.now}: {self.agent_id} 停止悬停，关闭悬停能量消耗进程")

    def _hovering_energy_consumption(self):
        """悬停能量消耗进程"""
        try:
            # 每秒更新一次电池电量
            update_interval = 1.0  # 1秒

            while True:
                # 等待指定时间
                yield self.env.timeout(update_interval)

                # 检查组件是否处于错误状态
                if self.is_error:
                    logger.warning(f"时间 {self.env.now}: {self.agent_id} 的 {self.name} 组件处于错误状态，停止悬停能量消耗进程")
                    break

                # 检查代理是否仍在悬停状态
                current_status = self.agent.get_state('moving_status')
                if current_status != 'hovering':
                    break

                # 获取当前电池电量
                battery_level = self.agent.get_state('battery_level', 100.0)

                # 计算悬停能量消耗
                energy_consumed = self._calculate_hovering_energy_consumption()

                # 更新电池电量
                new_battery_level = max(0.0, battery_level - energy_consumed)
                self.agent.update_state('battery_level', new_battery_level)

                # 如果电量过低，打印警告
                if new_battery_level < 20 and battery_level >= 20:
                    logger.warning(f"时间 {self.env.now}: 警告! {self.agent_id} 悬停中电量低于20%: {new_battery_level:.1f}%")
                elif new_battery_level < 10 and battery_level >= 10:
                    logger.warning(f"时间 {self.env.now}: 严重警告! {self.agent_id} 悬停中电量极低: {new_battery_level:.1f}%")
                elif new_battery_level < 1e-6:
                    logger.warning(f"时间 {self.env.now}: 严重警告! {self.agent_id} 悬停中电量耗尽!")
                    # 电量耗尽，可能需要触发紧急降落或其他处理
                    self.agent.update_state('status', 'error')
                    # 将组件设置为错误状态
                    self.is_error = True
                    logger.warning(f"时间 {self.env.now}: {self.agent_id} 的 {self.name} 组件因电量耗尽被设置为错误状态")
                    break

        except simpy.Interrupt:
            # 进程被中断，不需要特殊处理
            pass

    def disable(self):
        """
        禁用组件，将其设置为错误状态并取消所有正在执行的任务
        同时停止悬停能量消耗进程
        """
        # 调用父类的disable方法
        super().disable()

        # 停止悬停能量消耗进程
        if self.hovering_process and self.hovering_process.is_alive:
            self.hovering_process.interrupt()
            self.hovering_process = None
            logger.warning(f"时间 {self.env.now}: {self.agent_id} 的 {self.name} 组件被禁用，停止悬停能量消耗进程")
