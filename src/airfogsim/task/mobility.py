"""
AirFogSim移动任务模块

该模块定义了无人机移动任务的实现，负责模拟无人机在三维空间中的移动过程。
主要功能包括：
1. 计算移动路径和距离
2. 模拟位置更新和方向控制
3. 生成位置证明
4. 模拟电池消耗
5. 处理位置相关的事件

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core.task import Task
from airfogsim.core.enums import TaskStatus
import math
from typing import Dict
from airfogsim.utils.logging_config import get_logger
logger = get_logger(__name__)

class MoveToTask(Task):
    """移动到指定目标位置的任务"""
    NECESSARY_METRICS = ['speed'] # 需要从属于component的PRODUCED_METRICS
    PRODUCED_STATES = ['position', 'direction', 'distance_traveled', 'altitude', 'battery_level', 'status', 'moving_status'] # 需要在agent的模板之内

    def __init__(self, env, agent, component_name, task_name,
                 workflow_id=None, target_state=None, properties=None):
        """
        初始化移动任务

        Args:
            env: 仿真环境
            agent: 代理
            component_name: 组件名称
            task_name: 任务名称
            workflow_id: 工作流ID
            target_state: 目标状态，必须包含'position'
            properties: 任务属性，必须包含'current_position'
        """
        # 确保目标状态和属性不为空
        target_state = target_state or {}
        properties = properties or {}

        # 调用父类初始化
        super().__init__(env, agent, component_name, task_name,
                         workflow_id, target_state, properties)

        # 任务特定属性
        self.target_position = target_state.get('position')

        # 确保目标位置是三维的
        if self.target_position and len(self.target_position) == 2:
            # 如果目标位置是二维的，添加z=0
            self.target_position = (self.target_position[0], self.target_position[1], 0)
            self.target_state['position'] = self.target_position

        # 所有task_specific状态必须由自身统一维护，并且不能轻易调用agent.update state
        self.distance_traveled = 0  # 行进距离
        self.direction = (0, 0, 0)  # 当前方向向量（三维）
        self.current_position = agent.get_state('position', (0, 0, 0))  # 默认为(0, 0, 0)
        self.start_position = self.current_position
        self.total_distance = 0     # 起始位置到目标的直线距离
        self.battery_level = agent.get_state('battery_level', 100)  # 默认为满电量

        # 计算初始的总距离和方向
        self._calculate_distance_and_direction()

    def _calculate_distance_and_direction(self):
        """计算总距离和方向向量"""
        current_x, current_y, current_z = self.start_position
        target_x, target_y, target_z = self.target_position

        # 计算三维总距离
        dx, dy, dz = target_x - current_x, target_y - current_y, target_z - current_z
        self.total_distance = math.sqrt(dx**2 + dy**2 + dz**2)

        # 计算三维方向向量
        if self.total_distance > 0:
            self.direction = (dx/self.total_distance, dy/self.total_distance, dz/self.total_distance)
        else:
            self.direction = (0, 0, 0)

    def estimate_remaining_time(self, performance_metrics: Dict) -> float:
        """估计任务完成所需的剩余时间"""
        # 获取当前速度
        speed = performance_metrics.get('speed', 0)

        # 避免除零错误
        if speed <= 1e-6:
            return float('inf')

        # 剩余距离/速度 = 预计时间
        remaining_distance = self.total_distance - self.distance_traveled
        estimated_time = remaining_distance / speed

        return max(0, estimated_time)

    def _update_task_state(self, performance_metrics: Dict):
        """更新任务进度和内部状态"""

        # 获取当前速度
        speed = performance_metrics.get('speed', 0)

        # 如果没有获取到当前位置，使用上次记录的位置
        if not self.current_position:
            self.current_position = self.start_position

        elapsed_time = self.env.now - self.last_update_time
        # 计算这段时间移动的距离
        distance_moved = speed * elapsed_time
        self.distance_traveled += distance_moved

        # 更新电池电量
        self._update_battery_level(distance_moved)

        # 根据经过的时间和速度计算新位置
        if self.total_distance > 0 and self.direction != (0, 0, 0):
            # 计算在每个方向上移动的距离
            dx = self.direction[0] * distance_moved
            dy = self.direction[1] * distance_moved
            dz = self.direction[2] * distance_moved

            # 更新当前位置
            current_x, current_y, current_z = self.current_position
            new_x = current_x + dx
            new_y = current_y + dy
            new_z = current_z + dz

            # 确保不会越过目标点
            target_x, target_y, target_z = self.target_position
            if ((dx > 0 and new_x > target_x) or (dx < 0 and new_x < target_x) or
                (dy > 0 and new_y > target_y) or (dy < 0 and new_y < target_y) or
                (dz > 0 and new_z > target_z) or (dz < 0 and new_z < target_z)):
                # 已到达或越过目标点，直接设置为目标位置
                self.current_position = self.target_position
                self.progress = 1.0
            else:
                # 正常更新位置
                self.current_position = (new_x, new_y, new_z)

            # 更新代理的位置状态，这将触发环境中的碰撞检测
            self.agent.set_state('position', self.current_position)


        # 更新进度
        if self.total_distance > 0:
            self.progress = min(1.0, self.distance_traveled / self.total_distance)
        else:
            self.progress = 1.0

        # 计算当前位置到目标的实际距离
        current_x, current_y, current_z = self.current_position
        target_x, target_y, target_z = self.target_position
        current_distance = math.sqrt((target_x - current_x)**2 +
                                     (target_y - current_y)**2 +
                                     (target_z - current_z)**2)

        # 如果距离目标足够近，完成任务
        if current_distance < 1.0:  # 1.0是容差值
            self.progress = 1.0
            self.current_position = self.target_position

    def _update_battery_level(self, distance_moved):
        """更新电池电量，根据移动距离消耗电量"""
        # 获取当前电池电量
        battery_level = self.agent.get_state('battery_level', 1.0)  # 默认为满电量

        # 计算能量消耗 - 根据移动距离按比例减少电量
        # 假设每单位距离消耗0.0005的电量（可根据实际情况调整）
        energy_consumed = distance_moved * 0.005

        # 更新电池电量
        new_battery_level = max(0.0, battery_level - energy_consumed)

        self.battery_level = new_battery_level

        # 如果电量过低，打印警告信息
        if new_battery_level < 20 and battery_level >= 20:
            logger.warning(f"时间 {self.env.now}: 警告! {self.agent.id} 电量低于20%: {new_battery_level:.1f}%")
        elif new_battery_level < 10:
            logger.warning(f"时间 {self.env.now}: 严重警告! {self.agent.id} 电量极低: {new_battery_level:.1f}%")

    def _get_task_specific_state_repr(self) -> Dict:
        """返回任务特定状态的表示"""

        # 获取当前速度来确定移动状态
        speed = 0
        performance_metrics = self.component.get_performance_metrics() if hasattr(self, 'component') else {}
        if performance_metrics:
            speed = performance_metrics.get('speed', 0)

        # 确定移动状态
        moving_status = 'idle'
        if self.progress < 1.0:
            moving_status = 'flying'
        else:
            moving_status = 'hovering'  # 任务完成后设置为悬停状态

        # 基本状态更新
        return {
            'position': self.current_position,
            'direction': self.direction,
            'distance_traveled': self.distance_traveled,
            'altitude': self.current_position[2] if self.current_position else 0,
            'battery_level': self.battery_level,
            'status': 'active' if self.progress < 1.0 else 'idle',
            'moving_status': moving_status
        }
