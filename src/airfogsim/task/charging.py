"""
AirFogSim充电任务模块

该模块定义了无人机充电任务的实现，负责模拟无人机在充电站充电的过程以及充电站资源的请求。
主要功能包括：
1. 充电站资源请求 - 为代理分配充电站资源
2. 模拟电池充电过程 - 计算充电速率和效率
3. 更新代理的电池状态
4. 管理充电站资源的添加和移除

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core.task import Task
from typing import Dict, Any, Optional
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class RequestChargingStationTask(Task):
    """
    请求充电站资源的任务

    该任务负责为代理分配充电站资源，成功完成后会将充电站对象添加到代理的possessing_objects中。
    """
    NECESSARY_METRICS = ['request_processing_time']
    PRODUCED_STATES = ['status']

    def __init__(self, env, agent, component_name, task_name,
                 workflow_id=None, target_state=None, properties=None):
        """
        初始化充电站请求任务

        Args:
            env: 仿真环境
            agent: 代理
            component_name: 组件名称
            task_name: 任务名称
            workflow_id: 工作流ID
            target_state: 目标状态
            properties: 任务属性，应包含充电站位置信息
        """
        # 确保属性不为空
        properties = properties or {}
        target_state = target_state or {}

        # 调用父类初始化
        super().__init__(env, agent, component_name, task_name,
                         workflow_id, target_state, properties)

        # 任务特定属性
        self.charging_station_id = properties.get('charging_station_id')
        self.charging_station = self.env.landing_manager.find_resource_by_id(self.charging_station_id)
        # 将充电站添加到代理的possessing_objects中
        self.agent.add_possessing_object('charging_station', self.charging_station)
        self.waiting_time = 0

    def estimate_remaining_time(self, performance_metrics: Dict) -> float:
        """估计完成任务所需的剩余时间"""
        processing_time = performance_metrics.get('request_processing_time', float('inf'))
        return processing_time

    def _update_task_state(self, performance_metrics: Dict):
        """更新任务进度和内部状态"""
        elapsed_time = self.env.now - self.last_update_time
        remain_time = performance_metrics.get('request_processing_time', float('inf'))
        self.waiting_time += elapsed_time
        self.progress = min(1.0, self.waiting_time / (remain_time + self.waiting_time + 1e-9))
        # 对self.progress进行浮点数比较时，使用1e-6作为epsilon值
        if self.progress >= 1.0 - 1e-6:
            self.progress = 1.0

    def _get_task_specific_state_repr(self) -> Dict:
        """返回任务特定状态的表示"""
        return {
            'status': 'active' if self.progress < 1.0 else 'idle'
        }

    def _possessing_object_on_complete(self):
        """
        任务完成时，将充电站对象添加到代理的possessing_objects中
        """
        assert self.charging_station.is_allocated(self.agent_id), \
            f"充电站 {self.charging_station.id} 未分配给代理 {self.agent_id}"
        logger.info(f"时间 {self.env.now}: 代理 {self.agent_id} 成功获取充电站资源 {self.charging_station.id}")

    def _possessing_object_on_fail(self):
        """
        任务失败时的处理
        """
        logger.warning(f"时间 {self.env.now}: 代理 {self.agent_id} 请求充电站失败: {self.failure_reason}")

    def _possessing_object_on_cancel(self):
        """
        任务取消时的处理
        """
        logger.warning(f"时间 {self.env.now}: 代理 {self.agent_id} 请求充电站被取消")


class ChargingTask(Task):
    """
    执行电池充电任务

    该任务负责模拟电池充电过程，并在充电完成后释放充电站资源。
    """
    NECESSARY_METRICS = ['charging_rate']
    # 明确定义此任务产生的状态
    PRODUCED_STATES = ['battery_level', 'status', 'charge_cycles']

    def __init__(self, env, agent, component_name, task_name,
                 workflow_id=None, target_state=None, properties=None):
        """
        初始化充电任务

        Args:
            env: 仿真环境
            agent: 代理
            component_name: 组件名称
            task_name: 任务名称
            workflow_id: 工作流ID
            target_state: 目标状态，必须包含'battery_level'
            properties: 任务属性
        """
        # 确保目标状态和属性不为空
        target_state = target_state or {}
        properties = properties or {}

        # 调用父类初始化
        super().__init__(env, agent, component_name, task_name,
                         workflow_id, target_state, properties)

        # 任务特定属性
        self.target_battery_level = target_state.get('battery_level', 100.0)
        self.start_battery_level = agent.get_state('battery_level', 100.0)
        self.current_battery_level = self.start_battery_level
        self.charging_efficiency = properties.get('charging_efficiency', 0.95)  # 充电效率

    def estimate_remaining_time(self, performance_metrics: Dict) -> float:
        """估计完成任务所需的总时间"""
        charging_rate = performance_metrics.get('charging_rate', 0)
        if charging_rate <= 0:
            return float('inf')

        remaining_charge = self.target_battery_level - self.current_battery_level
        if remaining_charge <= 0:
            return 0

        # 将充电率从%/小时转换为%/秒
        charging_rate_per_second = charging_rate / 3600.0

        # 返回剩余时间（秒）
        return remaining_charge / (charging_rate_per_second * self.charging_efficiency)

    def _update_task_state(self, performance_metrics: Dict):
        """更新任务进度和内部状态"""
        charging_rate = performance_metrics.get('charging_rate', 0)  # %/小时
        elapsed_time = self.env.now - self.last_update_time  # 秒

        # 将充电率从%/小时转换为%/秒
        charging_rate_per_second = charging_rate / 3600.0

        # 计算充电增量
        charge_added = charging_rate_per_second * elapsed_time * self.charging_efficiency
        self.current_battery_level = min(100.0, self.current_battery_level + charge_added)

        total_charge_needed = self.target_battery_level - self.start_battery_level
        if total_charge_needed > 0:
            charge_completed = self.current_battery_level - self.start_battery_level
            self.progress = min(1.0, charge_completed / total_charge_needed)
        else:
            self.progress = 1.0

    def _get_task_specific_state_repr(self) -> Dict:
        """返回任务特定状态的表示"""
        # 返回所有在 PRODUCED_STATES 中定义的状态
        return {
            'battery_level': self.current_battery_level,
            'status': 'active' if self.progress < 1.0 else 'idle',
            'charge_cycles': self.properties.get('charge_cycles', 0) + (1 if self.progress >= 1.0 else 0)
        }

    def _possessing_object_on_complete(self):
        """
        充电任务完成时，释放充电站资源
        """
        # 从代理的possessing_objects中移除充电站
        if self.agent.get_possessing_object('charging_station'):
            self.agent.remove_possessing_object('charging_station')
            logger.info(f"时间 {self.env.now}: 代理 {self.agent_id} 充电完成，释放充电站资源")

    def _possessing_object_on_fail(self):
        """
        充电任务失败时，释放充电站资源
        """
        # 从代理的possessing_objects中移除充电站
        if self.agent.get_possessing_object('charging_station'):
            self.agent.remove_possessing_object('charging_station')
            logger.warning(f"时间 {self.env.now}: 代理 {self.agent_id} 充电失败，释放充电站资源: {self.failure_reason}")

    def _possessing_object_on_cancel(self):
        """
        充电任务取消时，释放充电站资源
        """
        # 从代理的possessing_objects中移除充电站
        if self.agent.get_possessing_object('charging_station'):
            self.agent.remove_possessing_object('charging_station')
            logger.warning(f"时间 {self.env.now}: 代理 {self.agent_id} 充电被取消，释放充电站资源")