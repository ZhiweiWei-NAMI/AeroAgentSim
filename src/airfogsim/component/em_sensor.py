"""
AirFogSim 电磁感知组件模块

该模块实现了一个电磁感知组件，用于感知周围环境中的电磁信号。
主要功能包括：
1. 周期性扫描特定频段
2. 检测和识别信号源
3. 更新代理状态
4. 模拟电池消耗

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import random
import simpy
from typing import List, Dict, Any, Optional, Set, Tuple
from airfogsim.utils.logging_config import get_logger

from airfogsim.core.component import Component

logger = get_logger(__name__)

class EMSensingComponent(Component):
    """
    电磁感知组件，用于感知周围环境中的电磁信号

    该组件利用SignalDataProvider获取周围信号的信息，
    模拟传感器的不确定性（如信号类型识别错误、功率测量误差等），
    并将感知到的信号更新到Agent的状态中。

    性能指标：
    - em_sensor_sensitivity: 传感器灵敏度 (dBm)
    - em_sensor_power_consumption: 传感器功耗
    - em_sensor_accuracy_identification: 信号类型识别精度 (错误率)
    - em_sensor_accuracy_power: 功率测量精度 (dB标准差)
    - em_sensor_accuracy_frequency: 频率测量精度 (MHz标准差)
    - em_sensor_accuracy_direction: 方向测量精度 (度标准差)
    """
    PRODUCED_METRICS = [
        'em_sensor_sensitivity',
        'em_sensor_power_consumption',
        'em_sensor_accuracy_identification',
        'em_sensor_accuracy_power',
        'em_sensor_accuracy_frequency',
        'em_sensor_accuracy_direction'
    ]
    MONITORED_STATES = ['position', 'status', 'battery_level']

    def __init__(self, env, agent, name: Optional[str] = None,
                 supported_events: List[str] = ['signal_detected', 'signal_lost'],
                 properties: Optional[Dict] = None):
        """
        初始化电磁感知组件

        Args:
            env: 仿真环境
            agent: 所属代理
            name: 组件名称
            supported_events: 支持的额外事件
            properties: 组件属性，包含传感器配置参数
        """
        super().__init__(env, agent, name or "EMSensor", supported_events, properties)

        # 从properties获取传感器参数
        self.sensing_interval = self.properties.get('sensing_interval', 1.0)  # 感知间隔（秒）
        self.sensitivity_threshold = self.properties.get('sensitivity_threshold', -90.0)  # 灵敏度阈值 (dBm)

        # 频率扫描范围，可以是多个频段
        self.frequency_bands = self.properties.get('frequency_bands', [(2400, 2500)])  # 默认2.4GHz频段

        # 传感器精度参数
        self.signal_identification_error_rate = self.properties.get('signal_identification_error_rate', 0.05)  # 信号类型识别错误率 (0.0-1.0)
        self.power_measurement_accuracy_stddev = self.properties.get('power_measurement_accuracy_stddev', 2.0)  # 功率测量误差标准差 (dB)
        self.frequency_measurement_accuracy_stddev = self.properties.get('frequency_measurement_accuracy_stddev', 0.5)  # 频率测量误差标准差 (MHz)
        self.direction_finding_accuracy_stddev = self.properties.get('direction_finding_accuracy_stddev', 5.0)  # 方向测量误差标准差 (度)

        # 电池消耗相关属性
        self.energy_factor = self.properties.get('energy_factor', 1.0)  # 能量消耗因子
        self.base_energy_consumption = self.properties.get('base_energy_consumption', 0.1)  # 基础能量消耗率（每秒消耗电池百分比）
        self.signal_detection_energy_factor = self.properties.get('signal_detection_energy_factor', 0.02)  # 每检测到一个信号的额外能量消耗

        # 信号数据提供者引用
        self.signal_provider = getattr(env, 'signal_data_provider', None)
        if not self.signal_provider:
            logger.warning(f"No signal_data_provider found in environment, EM sensing will not work properly")

        # 初始化内部状态变量
        self._last_detected_signals = set()  # 上次检测到的信号ID集合
        self._sensing_process = None  # 持续感知进程

        # 启动后台感知进程
        self._sensing_process = self.env.process(self._persistent_em_sensing())

        # 更新组件性能指标
        self.current_metrics.update({
            'em_sensor_sensitivity': self.sensitivity_threshold,
            'em_sensor_power_consumption': self.base_energy_consumption,
            'em_sensor_accuracy_identification': 1.0 - self.signal_identification_error_rate,  # 转换为精度值
            'em_sensor_accuracy_power': self.power_measurement_accuracy_stddev,
            'em_sensor_accuracy_frequency': self.frequency_measurement_accuracy_stddev,
            'em_sensor_accuracy_direction': self.direction_finding_accuracy_stddev
        })

    def _persistent_em_sensing(self):
        """
        持续电磁感知的后台进程

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

                # 检查信号数据提供者是否可用
                if not self.signal_provider:
                    continue

                # 获取可检测到的信号（理论上可探测的真实信号）
                true_signals = self.signal_provider.get_detectable_signals_at_location(
                    sensor_location=my_position,
                    frequency_bands_of_interest=self.frequency_bands,
                    sensor_sensitivity_threshold=self.sensitivity_threshold
                )

                # 模拟感知误差，生成最终感知到的信号列表
                final_perceived_signals = self._apply_sensing_inaccuracies(true_signals)

                # 处理感知到的信号（触发事件等）
                self._process_perceived_signals(final_perceived_signals)

                # 更新代理状态
                self.agent.update_state('detected_signals', final_perceived_signals)
                self.agent.update_state('last_em_scan_time', self.env.now)

                # 计算并更新电池消耗
                detected_signals_count = len(true_signals)  # 使用真实信号数量计算能耗
                energy_consumed = self._calculate_sensing_energy_consumption(detected_signals_count)

                # 获取当前电池电量
                battery_level = self.agent.get_state('battery_level', 100.0)

                # 更新电池电量
                new_battery_level = max(0.0, battery_level - energy_consumed)
                self.agent.update_state('battery_level', new_battery_level)

                # 如果电量过低，打印警告
                if new_battery_level < 20 and battery_level >= 20:
                    logger.warning(f"时间 {self.env.now}: 警告! {self.agent_id} 电磁感知中电量低于20%: {new_battery_level:.1f}%")
                elif new_battery_level < 10 and battery_level >= 10:
                    logger.warning(f"时间 {self.env.now}: 严重警告! {self.agent_id} 电磁感知中电量极低: {new_battery_level:.1f}%")
                elif new_battery_level < 1e-6:
                    logger.warning(f"时间 {self.env.now}: 严重警告! {self.agent_id} 电磁感知中电量耗尽!")
                    # 电量耗尽，将组件设置为错误状态
                    self.agent.update_state('status', 'error')
                    self.is_error = True
                    logger.warning(f"时间 {self.env.now}: {self.agent_id} 的 {self.name} 组件因电量耗尽被设置为错误状态")

        except Exception as e:
            logger.error(f"Error in EM sensing process: {e}")
            # 将组件状态设为错误
            self.set_error(str(e))

    def _apply_sensing_inaccuracies(self, true_signals):
        """
        模拟感知误差，为真实信号添加随机误差

        Args:
            true_signals: 理论上可探测的真实信号列表

        Returns:
            List[Dict]: 带有模拟误差的感知信号列表
        """
        final_perceived_signals = []

        for signal in true_signals:
            # 创建信号的副本，避免修改原始数据
            perceived_signal = signal.copy()

            # 为信号添加唯一ID（如果原始信号没有）
            if 'signal_id' not in perceived_signal:
                perceived_signal['signal_id'] = f"{perceived_signal['source_id']}_{perceived_signal['center_frequency']}_{id(perceived_signal)}"

            # 模拟信号类型识别错误
            if random.random() < self.signal_identification_error_rate:
                # 可能的信号类型列表
                possible_types = ['unknown', 'wifi', 'bluetooth', 'cellular', 'satellite', 'radar', 'radio']
                # 从可能的类型中随机选择一个，但不能是真实类型
                true_type = perceived_signal['signal_type']
                possible_types = [t for t in possible_types if t != true_type]
                if possible_types:
                    perceived_signal['signal_type'] = random.choice(possible_types)
                else:
                    perceived_signal['signal_type'] = 'unknown'

            # 模拟功率测量误差
            if 'received_power' in perceived_signal:
                power_error = random.gauss(0, self.power_measurement_accuracy_stddev)
                perceived_signal['received_power'] = perceived_signal['received_power'] + power_error
                # 更新SNR以保持一致性
                if 'snr' in perceived_signal:
                    perceived_signal['snr'] = perceived_signal['snr'] + power_error

            # 模拟频率测量误差
            if 'center_frequency' in perceived_signal:
                freq_error = random.gauss(0, self.frequency_measurement_accuracy_stddev)
                perceived_signal['center_frequency'] = perceived_signal['center_frequency'] + freq_error

            # 模拟带宽测量误差
            if 'bandwidth' in perceived_signal:
                bandwidth_error = random.gauss(0, self.frequency_measurement_accuracy_stddev * 0.5)  # 带宽误差通常小于频率误差
                perceived_signal['bandwidth'] = max(0.1, perceived_signal['bandwidth'] + bandwidth_error)  # 确保带宽为正

            # 模拟方向测量误差（如果有方向信息）
            if 'direction' in perceived_signal:
                direction_error = random.gauss(0, self.direction_finding_accuracy_stddev)
                perceived_signal['direction'] = (perceived_signal['direction'] + direction_error) % 360  # 确保方向在0-360度范围内

            # 添加到最终感知信号列表
            final_perceived_signals.append(perceived_signal)

        return final_perceived_signals

    def _process_perceived_signals(self, perceived_signals):
        """
        处理感知到的信号

        检测新信号和丢失的信号，并触发相应事件。

        Args:
            perceived_signals: 感知到的信号列表（已应用模拟误差）
        """
        # 获取当前检测到的信号ID集合（使用唯一信号ID）
        current_signal_ids = {signal.get('signal_id', signal['source_id']) for signal in perceived_signals}

        # 检测新信号
        new_signals = current_signal_ids - self._last_detected_signals
        for signal_id in new_signals:
            # 找到对应的信号数据
            signal_data = next((s for s in perceived_signals if s.get('signal_id', s['source_id']) == signal_id), None)
            if signal_data:
                # 触发信号检测事件
                event_data = {
                    'signal_id': signal_id,
                    'source_id': signal_data['source_id'],
                    'time': self.env.now
                }

                # 添加其他可用的信号属性
                for key in ['center_frequency', 'bandwidth', 'received_power', 'signal_type', 'snr', 'position', 'direction']:
                    if key in signal_data:
                        event_data[key] = signal_data[key]

                # 触发事件
                self.trigger_event('signal_detected', event_data)

        # 检测丢失的信号
        lost_signals = self._last_detected_signals - current_signal_ids
        for signal_id in lost_signals:
            # 触发信号丢失事件
            self.trigger_event('signal_lost', {
                'signal_id': signal_id,
                'source_id': signal_id,
                'time': self.env.now
            })

        # 更新上次检测到的信号ID集合
        self._last_detected_signals = current_signal_ids

    def _calculate_sensing_energy_consumption(self, detected_signals_count: int = 0) -> float:
        """
        计算感知过程的能量消耗

        Args:
            detected_signals_count: 检测到的信号数量

        Returns:
            float: 感知过程消耗的电池电量百分比
        """
        # 基础能量消耗
        energy_consumed = self.base_energy_consumption * self.energy_factor

        # 根据检测到的信号数量增加能量消耗
        energy_consumed += detected_signals_count * self.signal_detection_energy_factor * self.energy_factor

        # 根据频段数量调整能量消耗（频段越多，消耗越多）
        bands_factor = len(self.frequency_bands)
        energy_consumed *= (1.0 + 0.1 * (bands_factor - 1))  # 每增加一个频段，增加10%能耗

        # 根据频段宽度调整能量消耗（频段越宽，消耗越多）
        total_bandwidth = sum(max_freq - min_freq for min_freq, max_freq in self.frequency_bands)
        bandwidth_factor = total_bandwidth / 100.0  # 标准化为100MHz带宽
        energy_consumed *= max(1.0, bandwidth_factor)

        # 调整为每次感知周期的能量消耗
        energy_consumed *= self.sensing_interval

        return energy_consumed

    def scan_frequency_band(self, min_freq: float, max_freq: float) -> List[Dict]:
        """
        扫描特定频段

        此方法可以由任务调用，用于主动扫描特定频段。
        与持续感知进程不同，此方法不会触发信号检测/丢失事件，
        也不会更新代理的 detected_signals 状态。

        Args:
            min_freq: 最小频率 (MHz)
            max_freq: 最大频率 (MHz)

        Returns:
            List[Dict]: 检测到的信号列表（已应用模拟误差）
        """
        # 检查组件状态
        if self.is_error:
            logger.warning(f"无法扫描频段：组件 {self.name} 处于错误状态")
            return []

        # 获取自身位置
        my_position = self.agent.get_state('position')
        if not my_position or not self.signal_provider:
            return []

        # 获取可检测到的信号（理论上可探测的真实信号）
        true_signals = self.signal_provider.get_detectable_signals_at_location(
            sensor_location=my_position,
            frequency_bands_of_interest=[(min_freq, max_freq)],
            sensor_sensitivity_threshold=self.sensitivity_threshold
        )

        # 模拟感知误差，生成最终感知到的信号列表
        final_perceived_signals = self._apply_sensing_inaccuracies(true_signals)

        # 计算并更新电池消耗
        energy_consumed = self._calculate_sensing_energy_consumption(len(true_signals))

        # 获取当前电池电量
        battery_level = self.agent.get_state('battery_level', 100.0)

        # 更新电池电量
        new_battery_level = max(0.0, battery_level - energy_consumed)
        self.agent.update_state('battery_level', new_battery_level)

        # 更新最后扫描时间
        self.agent.update_state('last_em_scan_time', self.env.now)

        return final_perceived_signals

    def set_frequency_bands(self, frequency_bands: List[Tuple[float, float]]):
        """
        设置频率扫描范围

        Args:
            frequency_bands: 频率扫描范围列表，每个元素为 (min_freq, max_freq)
        """
        self.frequency_bands = frequency_bands

    def set_sensitivity_threshold(self, threshold: float):
        """
        设置灵敏度阈值

        Args:
            threshold: 灵敏度阈值 (dBm)
        """
        self.sensitivity_threshold = threshold

        # 更新性能指标
        self.current_metrics['em_sensor_sensitivity'] = self.sensitivity_threshold

    def set_accuracy_parameters(self, identification_error_rate=None, power_accuracy_stddev=None,
                               frequency_accuracy_stddev=None, direction_accuracy_stddev=None):
        """
        设置传感器精度参数

        Args:
            identification_error_rate: 信号类型识别错误率 (0.0-1.0)
            power_accuracy_stddev: 功率测量误差标准差 (dB)
            frequency_accuracy_stddev: 频率测量误差标准差 (MHz)
            direction_accuracy_stddev: 方向测量误差标准差 (度)
        """
        if identification_error_rate is not None:
            self.signal_identification_error_rate = identification_error_rate
            self.current_metrics['em_sensor_accuracy_identification'] = 1.0 - identification_error_rate

        if power_accuracy_stddev is not None:
            self.power_measurement_accuracy_stddev = power_accuracy_stddev
            self.current_metrics['em_sensor_accuracy_power'] = power_accuracy_stddev

        if frequency_accuracy_stddev is not None:
            self.frequency_measurement_accuracy_stddev = frequency_accuracy_stddev
            self.current_metrics['em_sensor_accuracy_frequency'] = frequency_accuracy_stddev

        if direction_accuracy_stddev is not None:
            self.direction_finding_accuracy_stddev = direction_accuracy_stddev
            self.current_metrics['em_sensor_accuracy_direction'] = direction_accuracy_stddev

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
            self._sensing_process = self.env.process(self._persistent_em_sensing())
            logger.info(f"时间 {self.env.now}: {self.agent_id} 的 {self.name} 组件被启用，重新启动持续感知进程")
