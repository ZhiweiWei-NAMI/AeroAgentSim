"""
AirFogSim 物理对象感知组件模块

该模块实现了一个持续运行的物理环境感知组件。该组件利用 AirspaceManager 获取周围对象的"真实"信息，
然后在组件内部模拟传感器的不确定性（如位置误差、分类错误），最终将带有模拟误差的"感知到的"对象信息
更新到 Agent 的状态中。

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import random
import simpy
from typing import List, Dict, Any, Optional, Set, Tuple
from airfogsim.core.component import Component
from airfogsim.utils.logging_config import get_logger
logger = get_logger(__name__)

class ObjectSensorComponent(Component):
    """
    物理对象感知组件，持续感知周围环境中的物理对象。

    该组件利用 AirspaceManager 获取周围对象的"真实"信息，然后模拟传感器的不确定性
    （如位置误差、分类错误），最终将带有模拟误差的"感知到的"对象信息更新到 Agent 的状态中。

    性能指标：
    - sensor_accuracy_position: 传感器位置精度指标（误差标准差）
    - sensor_accuracy_classification: 传感器分类精度指标（错误率）
    - sensor_power_consumption: 传感器基础功耗
    """
    PRODUCED_METRICS = ['sensor_accuracy_position', 'sensor_accuracy_classification', 'sensor_power_consumption']
    MONITORED_STATES = ['position', 'status']

    def __init__(self, env, agent, name: Optional[str] = None,
                 supported_events: List[str] = ['object_detected', 'object_lost'],
                 properties: Optional[Dict] = None):
        """
        初始化物理对象感知组件

        Args:
            env: 仿真环境
            agent: 所属代理
            name: 组件名称
            supported_events: 支持的额外事件
            properties: 组件属性，包含传感器配置参数
        """
        super().__init__(env, agent, name or "ObjectSensor", supported_events, properties)

        # 存储配置属性
        self.sensing_range = self.properties.get('range', 100.0)  # 最大感知距离
        self.sensing_interval = self.properties.get('sensing_interval', 5.0)  # 感知更新周期
        self.power_consumption = self.properties.get('power_consumption', 1.0)  # 基础功耗
        self.position_accuracy_stddev = self.properties.get('position_accuracy_stddev', 1.0)  # 位置测量误差的标准差
        self.classification_error_rate = self.properties.get('classification_error_rate', 0.05)  # 对象类型识别错误率
        self.detection_probability = self.properties.get('detection_probability', 0.95)  # 探测到范围内对象的概率

        # 电池消耗相关属性
        self.energy_factor = self.properties.get('energy_factor', 1.0)  # 能量消耗因子
        self.base_energy_consumption = self.properties.get('base_energy_consumption', 0.1)  # 基础能量消耗率（每秒消耗电池百分比）
        self.object_detection_energy_factor = self.properties.get('object_detection_energy_factor', 0.02)  # 每检测到一个对象的额外能量消耗

        # 初始化内部状态变量
        self._last_detected_ids = set()  # 上次检测到的对象ID集合
        self._sensing_process = None  # 持续感知进程

        # 启动后台感知进程
        self._sensing_process = self.env.process(self._persistent_object_sensing())

        # 更新组件性能指标
        self.current_metrics.update({
            'sensor_accuracy_position': self.position_accuracy_stddev,
            'sensor_accuracy_classification': self.classification_error_rate,
            'sensor_power_consumption': self.power_consumption
        })

    def _persistent_object_sensing(self):
        """
        持续对象感知的后台进程

        该进程周期性地模拟感知过程并更新Agent状态
        """
        try:
            while True:
                # 等待一个感知周期
                yield self.env.timeout(self.sensing_interval)

                # 检查组件和Agent状态是否正常
                if self.is_error or self.agent.get_state('status') == 'error':
                    continue

                # 获取自身位置
                my_position = self.agent.get_state('position')
                if not my_position:
                    continue  # 如果位置未知，跳过本次感知

                # 获取真实对象信息
                try:
                    # 调用AirspaceManager获取周围对象的真实信息
                    nearby_objects = self.env.airspace_manager.get_nearby_objects(
                        position=my_position,
                        radius=self.sensing_range
                    )
                except (AttributeError, Exception) as e:
                    logger.warning(f"时间 {self.env.now}: {self.agent_id} 的 {self.name} 组件无法获取周围对象: {str(e)}")
                    continue

                # 模拟感知过程和不准确性
                perceived_objects_list = []
                current_perceived_ids = set()

                # nearby_objects 是一个字典，键是对象ID，值是位置坐标 (x, y, z)
                for obj_id, obj_pos in nearby_objects.items():
                    # 过滤掉自身
                    if obj_id == self.agent_id:
                        continue

                    # 模拟漏检
                    if random.random() > self.detection_probability:
                        continue

                    # 获取对象的真实信息
                    true_id = obj_id
                    true_pos = obj_pos
                    true_type = self._get_object_type_from_id(true_id)

                    # 模拟位置误差
                    perceived_pos = self._add_position_error(true_pos)

                    # 模拟分类误差
                    perceived_type = self._apply_classification_error(true_type)

                    # 提取对象信息
                    object_info = self._extract_object_info(true_id, perceived_pos, perceived_type, my_position)

                    # 添加到感知对象列表
                    perceived_objects_list.append(object_info)
                    current_perceived_ids.add(true_id)

                # 更新Agent状态
                self.agent.update_state('nearby_objects', perceived_objects_list)
                self.agent.update_state('last_object_scan_time', self.env.now)

                # 计算并更新电池消耗
                detected_objects_count = len(perceived_objects_list)
                energy_consumed = self._calculate_sensing_energy_consumption(detected_objects_count)

                # 获取当前电池电量
                battery_level = self.agent.get_state('battery_level', 100.0)

                # 更新电池电量
                new_battery_level = max(0.0, battery_level - energy_consumed)
                self.agent.update_state('battery_level', new_battery_level)

                # 如果电量过低，打印警告
                if new_battery_level < 20 and battery_level >= 20:
                    logger.warning(f"时间 {self.env.now}: 警告! {self.agent_id} 感知中电量低于20%: {new_battery_level:.1f}%")
                elif new_battery_level < 10 and battery_level >= 10:
                    logger.warning(f"时间 {self.env.now}: 严重警告! {self.agent_id} 感知中电量极低: {new_battery_level:.1f}%")
                elif new_battery_level < 1e-6:
                    logger.warning(f"时间 {self.env.now}: 严重警告! {self.agent_id} 感知中电量耗尽!")
                    # 电量耗尽，将组件设置为错误状态
                    self.agent.update_state('status', 'error')
                    self.is_error = True
                    logger.warning(f"时间 {self.env.now}: {self.agent_id} 的 {self.name} 组件因电量耗尽被设置为错误状态")
                    break

                # 触发事件
                # 新检测到的对象
                for obj_id in current_perceived_ids - self._last_detected_ids:
                    obj_info = next((obj for obj in perceived_objects_list if obj['id'] == obj_id), None)
                    if obj_info:
                        self.trigger_event('object_detected', obj_info)

                # 丢失的对象
                for obj_id in self._last_detected_ids - current_perceived_ids:
                    self.trigger_event('object_lost', {'id': obj_id, 'time': self.env.now})

                # 更新上次检测到的对象ID集合
                self._last_detected_ids = current_perceived_ids

        except simpy.Interrupt:
            # 进程被中断，不需要特殊处理
            pass

    def _get_object_type_from_id(self, obj_id: str) -> str:
        """
        从对象ID推断对象类型

        Args:
            obj_id: 对象ID

        Returns:
            str: 对象类型
        """
        # 尝试从ID前缀推断类型
        if obj_id.startswith('agent_drone'):
            return 'drone'
        elif obj_id.startswith('agent_terminal'):
            return 'terminal'
        elif obj_id.startswith('agent_delivery'):
            return 'delivery'
        elif obj_id.startswith('agent_inspection'):
            return 'inspection'
        elif obj_id.startswith('agent_sensing'):
            return 'sensing_agent'
        elif obj_id.startswith('agent_'):
            return 'agent'
        elif obj_id.startswith('landing_'):
            return 'landing'
        elif obj_id.startswith('obstacle_'):
            return 'obstacle'
        else:
            return 'unknown'

    def _add_position_error(self, true_pos: Tuple[float, float, float]) -> Tuple[float, float, float]:
        """
        为真实位置添加随机误差

        Args:
            true_pos: 真实位置坐标 (x, y, z)

        Returns:
            Tuple[float, float, float]: 感知到的位置坐标
        """
        if not true_pos or len(true_pos) != 3:
            return true_pos

        # 为每个坐标添加随机误差
        x_error = random.gauss(0, self.position_accuracy_stddev)
        y_error = random.gauss(0, self.position_accuracy_stddev)
        z_error = random.gauss(0, self.position_accuracy_stddev)

        return (
            true_pos[0] + x_error,
            true_pos[1] + y_error,
            true_pos[2] + z_error
        )

    def _apply_classification_error(self, true_type: str) -> str:
        """
        应用分类误差

        Args:
            true_type: 真实对象类型

        Returns:
            str: 感知到的对象类型
        """
        # 如果随机值小于错误率，则发生分类错误
        if random.random() < self.classification_error_rate:
            # 可能的对象类型列表
            possible_types = ['drone', 'terminal', 'delivery', 'inspection', 'agent', 'unknown']

            # 从可能的类型中随机选择一个，但不能是真实类型
            possible_types = [t for t in possible_types if t != true_type]
            if possible_types:
                return random.choice(possible_types)
            else:
                return 'unknown'

        # 没有发生分类错误，返回真实类型
        return true_type

    def _extract_object_info(self, obj_id: str, perceived_pos: Tuple[float, float, float],
                            perceived_type: str, my_position: Tuple[float, float, float]) -> Dict:
        """
        提取对象信息

        Args:
            obj_id: 对象ID
            perceived_pos: 感知到的位置
            perceived_type: 感知到的类型
            my_position: 自身位置

        Returns:
            Dict: 包含感知到的对象信息的字典
        """
        # 计算相对位置
        relative_pos = (
            perceived_pos[0] - my_position[0],
            perceived_pos[1] - my_position[1],
            perceived_pos[2] - my_position[2]
        )

        # 计算距离
        distance = (relative_pos[0]**2 + relative_pos[1]**2 + relative_pos[2]**2)**0.5

        # 返回对象信息字典
        return {
            'id': obj_id,
            'type': perceived_type,
            'position': perceived_pos,
            'relative_position': relative_pos,
            'distance': distance,
            'detection_time': self.env.now
        }

    def _calculate_sensing_energy_consumption(self, detected_objects_count: int = 0) -> float:
        """
        计算感知过程的能量消耗

        Args:
            detected_objects_count: 检测到的对象数量

        Returns:
            float: 感知过程消耗的电池电量百分比
        """
        # 基础能量消耗
        energy_consumed = self.base_energy_consumption * self.energy_factor

        # 根据检测到的对象数量增加能量消耗
        energy_consumed += detected_objects_count * self.object_detection_energy_factor * self.energy_factor

        # 根据感知范围调整能量消耗（范围越大，消耗越多）
        range_factor = self.sensing_range / 100.0  # 标准化为100米范围
        energy_consumed *= range_factor

        # 获取电池电量，低电量时降低能耗以延长电池寿命
        battery_level = self.agent.get_state('battery_level', 100.0)
        if battery_level < 20.0:
            energy_consumed *= 0.8  # 低电量时降低20%能耗
        if battery_level < 10.0:
            energy_consumed *= 0.6  # 极低电量时降低40%能耗

        return energy_consumed

    def _calculate_performance_metrics(self) -> Dict[str, Any]:
        """
        计算基于当前代理状态的性能指标

        Returns:
            Dict[str, Any]: 性能指标字典
        """
        # 获取当前电池电量
        battery_level = self.agent.get_state('battery_level', 100.0)

        # 根据电池电量调整功耗
        adjusted_power_consumption = self.power_consumption
        if battery_level < 20.0:
            adjusted_power_consumption *= 0.8  # 低电量时降低功耗
        if battery_level < 10.0:
            adjusted_power_consumption *= 0.6  # 极低电量时进一步降低功耗

        # 返回性能指标
        return {
            'sensor_accuracy_position': self.position_accuracy_stddev,
            'sensor_accuracy_classification': self.classification_error_rate,
            'sensor_power_consumption': adjusted_power_consumption
        }

    def disable(self):
        """
        禁用组件，将其设置为错误状态并取消所有正在执行的任务
        同时停止持续感知进程
        """
        # 调用父类的disable方法
        super().disable()

        # 停止持续感知进程
        if self._sensing_process and self._sensing_process.is_alive:
            self._sensing_process.interrupt()
            self._sensing_process = None
            logger.info(f"时间 {self.env.now}: {self.agent_id} 的 {self.name} 组件被禁用，停止持续感知进程")

    def enable(self):
        """
        启用组件，将其从错误状态恢复
        重新启动持续感知进程
        """
        # 调用父类的enable方法
        super().enable()

        # 重新启动持续感知进程
        if not self._sensing_process or not self._sensing_process.is_alive:
            self._sensing_process = self.env.process(self._persistent_object_sensing())
            logger.info(f"时间 {self.env.now}: {self.agent_id} 的 {self.name} 组件被启用，重新启动持续感知进程")
