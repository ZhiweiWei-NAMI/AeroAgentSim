"""
AirFogSim 信号数据提供者模块

该模块实现了一个信号数据提供者，用于模拟电磁环境中的信号传播。
主要功能包括：
1. 跟踪所有已知的活跃辐射源（发射机）
2. 计算信号传播和接收功率
3. 提供查询API，用于获取特定位置可检测到的信号

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import math
import numpy as np
from typing import Dict, List, Tuple, Optional, Any
from airfogsim.utils.logging_config import get_logger

from airfogsim.core.dataprovider import DataProvider
from airfogsim.manager.frequency import FrequencyManager

logger = get_logger(__name__)

class SignalSource:
    """
    信号源类，表示一个电磁辐射源

    属性:
        source_id: 信号源唯一标识符
        position: 信号源位置 (x, y, z)
        transmit_power_dbm: 发射功率 (dBm)
        center_frequency: 中心频率 (MHz)
        bandwidth: 带宽 (MHz)
        signal_type: 信号类型 (如 'communication', 'radar', 'jammer')
        is_active: 信号源是否活跃
        modulation: 调制方式 (可选)
        polarization: 极化方式 (可选)
        antenna_gain: 天线增益 (dBi) (可选)
        antenna_pattern: 天线方向图 (可选)
        target_id: 目标ID (可选，用于定向通信)
    """

    def __init__(self, source_id: str, position: Tuple[float, float, float],
                 transmit_power_dbm: float, center_frequency: float, bandwidth: float,
                 signal_type: str = 'communication', is_active: bool = True,
                 modulation: str = None, polarization: str = None,
                 antenna_gain: float = 0.0, antenna_pattern: Dict = None,
                 target_id: str = None):
        """
        初始化信号源

        Args:
            source_id: 信号源唯一标识符
            position: 信号源位置 (x, y, z)
            transmit_power_dbm: 发射功率 (dBm)
            center_frequency: 中心频率 (MHz)
            bandwidth: 带宽 (MHz)
            signal_type: 信号类型 (默认为'communication')
            is_active: 信号源是否活跃 (默认为True)
            modulation: 调制方式 (可选)
            polarization: 极化方式 (可选)
            antenna_gain: 天线增益 (dBi) (可选)
            antenna_pattern: 天线方向图 (可选)
            target_id: 目标ID (可选，用于定向通信)
        """
        self.source_id = source_id
        self.position = position
        self.transmit_power_dbm = transmit_power_dbm
        self.center_frequency = center_frequency
        self.bandwidth = bandwidth
        self.signal_type = signal_type
        self.is_active = is_active
        self.modulation = modulation
        self.polarization = polarization
        self.antenna_gain = antenna_gain
        self.antenna_pattern = antenna_pattern or {}
        self.target_id = target_id

    def to_dict(self) -> Dict:
        """
        将信号源转换为字典

        Returns:
            Dict: 包含信号源信息的字典
        """
        return {
            'source_id': self.source_id,
            'position': self.position,
            'transmit_power_dbm': self.transmit_power_dbm,
            'center_frequency': self.center_frequency,
            'bandwidth': self.bandwidth,
            'signal_type': self.signal_type,
            'is_active': self.is_active,
            'modulation': self.modulation,
            'polarization': self.polarization,
            'antenna_gain': self.antenna_gain,
            'antenna_pattern': self.antenna_pattern,
            'target_id': self.target_id
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'SignalSource':
        """
        从字典创建信号源

        Args:
            data: 包含信号源信息的字典

        Returns:
            SignalSource: 创建的信号源对象
        """
        return cls(
            source_id=data['source_id'],
            position=data['position'],
            transmit_power_dbm=data['transmit_power_dbm'],
            center_frequency=data['center_frequency'],
            bandwidth=data['bandwidth'],
            signal_type=data.get('signal_type', 'communication'),
            is_active=data.get('is_active', True),
            modulation=data.get('modulation'),
            polarization=data.get('polarization'),
            antenna_gain=data.get('antenna_gain', 0.0),
            antenna_pattern=data.get('antenna_pattern'),
            target_id=data.get('target_id')
        )


class SignalDataProvider(DataProvider):
    """
    信号数据提供者，用于模拟电磁环境中的信号传播

    该提供者维护所有已知的活跃辐射源（发射机），并提供查询API，
    用于获取特定位置可检测到的信号。
    """

    # 定义事件名称
    EVENT_SIGNAL_SOURCE_ADDED = 'SignalSourceAdded'
    EVENT_SIGNAL_SOURCE_REMOVED = 'SignalSourceRemoved'
    EVENT_SIGNAL_SOURCE_UPDATED = 'SignalSourceUpdated'

    def __init__(self, env, config=None):
        """
        初始化信号数据提供者

        Args:
            env: 仿真环境
            config: 配置字典，可能包含以下字段:
                - propagation_model: 传播模型 ('free_space', 'two_ray', 'log_distance')
                - default_noise_floor: 默认噪声底 (dBm)
                - weather_enabled: 是否启用天气影响
        """
        super().__init__(env, config)

        # 配置参数
        self.propagation_model = self.config.get('propagation_model', 'free_space')
        self.default_noise_floor = self.config.get('default_noise_floor', -100.0)  # dBm
        self.weather_enabled = self.config.get('weather_enabled', False)
        self.use_fast_fading = self.config.get('use_fast_fading', False)

        # 信号源字典，键为信号源ID，值为SignalSource对象
        self.signal_sources: Dict[str, SignalSource] = {}

        # 频率管理器引用
        self.frequency_manager = getattr(env, 'frequency_manager', None)

        # 位置更新订阅字典，键为订阅ID，值为包含agent_id和signal_source_id的字典
        self.position_subscriptions = {}

        # 注册事件处理器
        self._register_event_handlers()

        logger.info(f"SignalDataProvider initialized with model: {self.propagation_model}")

    def load_data(self):
        """
        加载外部信号源数据

        此方法可以从配置文件或数据库加载预定义的信号源。
        """
        # 从配置加载预定义信号源
        predefined_sources = self.config.get('predefined_sources', [])
        for source_data in predefined_sources:
            source = SignalSource.from_dict(source_data)
            self.add_signal_source(source)

        logger.info(f"Loaded {len(predefined_sources)} predefined signal sources")

    def start_event_triggering(self):
        """
        启动事件触发进程

        此方法可以启动定期更新信号源状态的进程。
        """
        # 目前没有需要定期触发的事件
        pass

    def _register_event_handlers(self):
        """
        注册事件处理器
        """
        # 如果有频率管理器，订阅频率链路活动更新事件
        if self.frequency_manager:
            self.env.event_registry.subscribe(
                self.frequency_manager.id,
                'FrequencyLinkActivityUpdate',
                f"{self.__class__.__name__}_frequency_link_handler",
                self._handle_frequency_link_update
            )
            logger.info("Subscribed to FrequencyLinkActivityUpdate events")

        # 订阅所有代理的位置更新事件
        self.position_subscriptions = {}  # 存储位置更新订阅的ID，用于后续取消订阅

    def _handle_frequency_link_update(self, event_data):
        """
        处理频率链路活动更新事件

        当频率管理器分配或释放频率资源时，更新信号源信息。

        Args:
            event_data: 事件数据，包含以下字段:
                - source_id: 源代理ID
                - target_id: 目标代理ID
                - resource_id: 资源ID
                - center_frequency: 中心频率
                - bandwidth: 带宽
                - transmit_power_dbm: 发射功率
                - status: 状态 ('allocated' 或 'released')
        """
        source_id = event_data.get('source_id')
        target_id = event_data.get('target_id')
        resource_id = event_data.get('resource_id')
        status = event_data.get('status')

        if not all([source_id, resource_id, status]):
            logger.warning(f"Incomplete frequency link update event: {event_data}")
            return

        # 构造信号源ID
        signal_source_id = f"{source_id}_{resource_id}"

        if status == 'allocated':
            # 获取源代理位置
            source_position = self._get_agent_position(source_id)
            if not source_position:
                logger.warning(f"Cannot get position for agent {source_id}")
                return

            # 创建或更新信号源
            center_frequency = event_data.get('center_frequency')
            bandwidth = event_data.get('bandwidth')
            transmit_power_dbm = event_data.get('transmit_power_dbm')

            if signal_source_id in self.signal_sources:
                # 更新现有信号源
                source = self.signal_sources[signal_source_id]
                source.position = source_position
                source.center_frequency = center_frequency
                source.bandwidth = bandwidth
                source.transmit_power_dbm = transmit_power_dbm
                source.is_active = True
                source.target_id = target_id

                # 触发信号源更新事件
                self.env.event_registry.trigger_event(
                    self.__class__.__name__,
                    self.EVENT_SIGNAL_SOURCE_UPDATED,
                    {'source_id': signal_source_id, 'source': source.to_dict()}
                )
            else:
                # 创建新信号源
                source = SignalSource(
                    source_id=signal_source_id,
                    position=source_position,
                    transmit_power_dbm=transmit_power_dbm,
                    center_frequency=center_frequency,
                    bandwidth=bandwidth,
                    signal_type='communication',
                    is_active=True,
                    target_id=target_id
                )

                # 添加信号源
                self.signal_sources[signal_source_id] = source

                # 触发信号源添加事件
                self.env.event_registry.trigger_event(
                    self.__class__.__name__,
                    self.EVENT_SIGNAL_SOURCE_ADDED,
                    {'source_id': signal_source_id, 'source': source.to_dict()}
                )

                # 订阅源代理的位置更新事件
                self._subscribe_to_agent_position_updates(source_id, signal_source_id)

            logger.debug(f"Signal source {signal_source_id} added/updated for frequency allocation")

        elif status == 'released':
            # 如果信号源存在，将其标记为非活跃或移除
            if signal_source_id in self.signal_sources:
                # 将信号源标记为非活跃
                self.signal_sources[signal_source_id].is_active = False

                # 触发信号源更新事件
                self.env.event_registry.trigger_event(
                    self.__class__.__name__,
                    self.EVENT_SIGNAL_SOURCE_UPDATED,
                    {'source_id': signal_source_id, 'source': self.signal_sources[signal_source_id].to_dict()}
                )

                # 取消订阅源代理的位置更新事件
                self._unsubscribe_from_agent_position_updates(source_id, signal_source_id)

                logger.debug(f"Signal source {signal_source_id} marked as inactive")

    def _subscribe_to_agent_position_updates(self, agent_id, signal_source_id):
        """
        订阅代理位置更新事件

        当代理位置发生变化时，更新对应的信号源位置。

        Args:
            agent_id: 代理ID
            signal_source_id: 信号源ID
        """
        # 创建订阅ID
        subscription_id = f"{self.__class__.__name__}_position_update_{agent_id}_{signal_source_id}"

        # 如果已经订阅，先取消订阅
        if subscription_id in self.position_subscriptions:
            self._unsubscribe_from_agent_position_updates(agent_id, signal_source_id)

        # 订阅代理的状态变化事件
        try:
            self.env.event_registry.subscribe(
                agent_id,
                'state_changed',
                subscription_id,
                lambda event_data: self._handle_agent_position_update(agent_id, signal_source_id, event_data)
            )

            # 记录订阅ID
            self.position_subscriptions[subscription_id] = {
                'agent_id': agent_id,
                'signal_source_id': signal_source_id
            }

            logger.debug(f"Subscribed to position updates for agent {agent_id} for signal source {signal_source_id}")
        except Exception as e:
            logger.warning(f"Failed to subscribe to position updates for agent {agent_id}: {e}")

    def _unsubscribe_from_agent_position_updates(self, agent_id, signal_source_id):
        """
        取消订阅代理位置更新事件

        Args:
            agent_id: 代理ID
            signal_source_id: 信号源ID
        """
        # 创建订阅ID
        subscription_id = f"{self.__class__.__name__}_position_update_{agent_id}_{signal_source_id}"

        # 如果已订阅，取消订阅
        if subscription_id in self.position_subscriptions:
            try:
                self.env.event_registry.unsubscribe(
                    agent_id,
                    'state_changed',
                    subscription_id
                )

                # 移除订阅记录
                del self.position_subscriptions[subscription_id]

                logger.debug(f"Unsubscribed from position updates for agent {agent_id} for signal source {signal_source_id}")
            except Exception as e:
                logger.warning(f"Failed to unsubscribe from position updates for agent {agent_id}: {e}")

    def _handle_agent_position_update(self, agent_id, signal_source_id, event_data):
        """
        处理代理位置更新事件

        当代理位置发生变化时，更新对应的信号源位置。

        Args:
            agent_id: 代理ID
            signal_source_id: 信号源ID
            event_data: 事件数据
        """
        # 检查是否是位置更新事件
        if event_data.get('state_name') == 'position':
            # 获取新位置
            new_position = event_data.get('new_value')

            # 检查信号源是否存在且活跃
            if signal_source_id in self.signal_sources and self.signal_sources[signal_source_id].is_active:
                # 更新信号源位置
                self.update_signal_source(signal_source_id, position=new_position)
                logger.debug(f"Updated position of signal source {signal_source_id} (from agent {agent_id}) to {new_position}")

    def _get_agent_position(self, agent_id):
        """
        获取代理的位置

        Args:
            agent_id: 代理ID

        Returns:
            Tuple[float, float, float] or None: 代理位置，如果找不到则返回None
        """
        # 尝试从环境中获取代理
        agent = self.env.agents.get(agent_id)
        if agent:
            return agent.get_state('position')

        # 如果有空域管理器，尝试从中获取位置
        if hasattr(self.env, 'airspace_manager'):
            return self.env.airspace_manager.get_object_position(agent_id)

        return None

    def add_signal_source(self, source: SignalSource) -> bool:
        """
        添加信号源

        Args:
            source: 要添加的信号源

        Returns:
            bool: 是否成功添加
        """
        if source.source_id in self.signal_sources:
            logger.warning(f"Signal source {source.source_id} already exists")
            return False

        self.signal_sources[source.source_id] = source

        # 触发信号源添加事件
        self.env.event_registry.trigger_event(
            self.__class__.__name__,
            self.EVENT_SIGNAL_SOURCE_ADDED,
            {'source_id': source.source_id, 'source': source.to_dict()}
        )

        logger.info(f"Signal source {source.source_id} added")
        return True

    def remove_signal_source(self, source_id: str) -> bool:
        """
        移除信号源

        Args:
            source_id: 要移除的信号源ID

        Returns:
            bool: 是否成功移除
        """
        if source_id not in self.signal_sources:
            logger.warning(f"Signal source {source_id} does not exist")
            return False

        source = self.signal_sources.pop(source_id)

        # 触发信号源移除事件
        self.env.event_registry.trigger_event(
            self.__class__.__name__,
            self.EVENT_SIGNAL_SOURCE_REMOVED,
            {'source_id': source_id, 'source': source.to_dict()}
        )

        # 取消所有与该信号源相关的位置更新订阅
        # 查找所有包含该信号源ID的订阅
        subscriptions_to_remove = []
        for sub_id, sub_info in self.position_subscriptions.items():
            if sub_info['signal_source_id'] == source_id:
                subscriptions_to_remove.append((sub_id, sub_info['agent_id']))

        # 取消这些订阅
        for sub_id, agent_id in subscriptions_to_remove:
            try:
                self.env.event_registry.unsubscribe(
                    agent_id,
                    'state_changed',
                    sub_id
                )
                del self.position_subscriptions[sub_id]
                logger.debug(f"Unsubscribed from position updates for agent {agent_id} for removed signal source {source_id}")
            except Exception as e:
                logger.warning(f"Failed to unsubscribe from position updates for agent {agent_id}: {e}")

        logger.info(f"Signal source {source_id} removed")
        return True

    def update_signal_source(self, source_id: str, **kwargs) -> bool:
        """
        更新信号源属性

        Args:
            source_id: 要更新的信号源ID
            **kwargs: 要更新的属性

        Returns:
            bool: 是否成功更新
        """
        if source_id not in self.signal_sources:
            logger.warning(f"Signal source {source_id} does not exist")
            return False

        source = self.signal_sources[source_id]

        # 更新属性
        for key, value in kwargs.items():
            if hasattr(source, key):
                setattr(source, key, value)

        # 触发信号源更新事件
        self.env.event_registry.trigger_event(
            self.__class__.__name__,
            self.EVENT_SIGNAL_SOURCE_UPDATED,
            {'source_id': source_id, 'source': source.to_dict()}
        )

        logger.debug(f"Signal source {source_id} updated")
        return True

    def get_signal_source(self, source_id: str) -> Optional[SignalSource]:
        """
        获取信号源

        Args:
            source_id: 信号源ID

        Returns:
            SignalSource or None: 信号源对象，如果不存在则返回None
        """
        return self.signal_sources.get(source_id)

    def get_all_signal_sources(self) -> Dict[str, SignalSource]:
        """
        获取所有信号源

        Returns:
            Dict[str, SignalSource]: 信号源字典
        """
        return self.signal_sources.copy()

    def get_active_signal_sources(self) -> Dict[str, SignalSource]:
        """
        获取所有活跃的信号源

        Returns:
            Dict[str, SignalSource]: 活跃信号源字典
        """
        return {id: source for id, source in self.signal_sources.items() if source.is_active}

    def _calculate_received_power(self, source: SignalSource, receiver_position: Tuple[float, float, float]) -> float:
        """
        计算接收功率

        使用选定的传播模型计算从信号源到接收机位置的接收功率。

        Args:
            source: 信号源
            receiver_position: 接收机位置 (x, y, z)

        Returns:
            float: 接收功率 (dBm)
        """
        # 计算距离
        distance = self._calculate_distance(source.position, receiver_position)

        # 避免距离为0导致的计算错误
        if distance < 0.1:
            distance = 0.1

        # 基础发射功率 (dBm)
        tx_power = source.transmit_power_dbm

        # 天线增益 (dBi)
        antenna_gain = source.antenna_gain

        # 根据传播模型计算路径损耗
        path_loss = self._calculate_path_loss(distance, source.center_frequency)

        # 计算接收功率 (dBm)
        rx_power = tx_power + antenna_gain - path_loss

        # 应用环境因素
        rx_power = self._apply_environmental_factors(rx_power, source, receiver_position)

        return rx_power

    def _calculate_distance(self, pos1: Tuple[float, float, float], pos2: Tuple[float, float, float]) -> float:
        """
        计算两点之间的欧几里得距离

        Args:
            pos1: 第一个位置 (x, y, z)
            pos2: 第二个位置 (x, y, z)

        Returns:
            float: 距离 (米)
        """
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(pos1, pos2)))

    def _calculate_path_loss(self, distance: float, frequency: float) -> float:
        """
        计算路径损耗

        根据选定的传播模型计算路径损耗。

        Args:
            distance: 距离 (米)
            frequency: 频率 (MHz)

        Returns:
            float: 路径损耗 (dB)
        """
        # 将频率转换为GHz
        freq_ghz = frequency / 1000.0

        if self.propagation_model == 'free_space':
            # 自由空间路径损耗模型
            # PL(dB) = 20*log10(d) + 20*log10(f) + 32.44
            # 其中d为距离(km)，f为频率(MHz)
            distance_km = distance / 1000.0
            return 20 * math.log10(distance_km) + 20 * math.log10(frequency) + 32.45

        elif self.propagation_model == 'two_ray':
            # 两射线地面反射模型
            # 对于较远距离: PL(dB) = 40*log10(d) - 10*log10(ht*hr)
            # 其中ht和hr为发射机和接收机的高度
            # 简化版本，假设高度为2米
            if distance > 100:
                return 40 * math.log10(distance / 1000.0) - 10 * math.log10(2 * 2)
            else:
                # 近距离使用自由空间模型
                distance_km = distance / 1000.0
                return 20 * math.log10(distance_km) + 20 * math.log10(frequency) + 32.45

        elif self.propagation_model == 'log_distance':
            # 对数距离路径损耗模型
            # PL(dB) = PL(d0) + 10*n*log10(d/d0)
            # 其中n为路径损耗指数，d0为参考距离
            n = 3.0  # 城市环境的典型值
            d0 = 1.0  # 参考距离1米
            pl_d0 = 20 * math.log10(4 * math.pi * d0 * freq_ghz / 0.3)  # 参考距离处的路径损耗
            return pl_d0 + 10 * n * math.log10(distance / d0)

        else:
            # 默认使用自由空间模型
            distance_km = distance / 1000.0
            return 20 * math.log10(distance_km) + 20 * math.log10(frequency) + 32.45

    def _apply_environmental_factors(self, rx_power: float, source: SignalSource,
                                    receiver_position: Tuple[float, float, float]) -> float:
        """
        应用环境因素对接收功率的影响

        考虑天气、地形等环境因素对信号传播的影响。

        Args:
            rx_power: 基础接收功率 (dBm)
            source: 信号源
            receiver_position: 接收机位置（模拟的位置坐标）

        Returns:
            float: 修正后的接收功率 (dBm)
        """
        modified_rx_power = rx_power

        weather_data_provider = self.env.get_data_provider('weather')
        # 应用天气影响
        if self.weather_enabled and weather_data_provider:
            weather_provider = weather_data_provider
            # 获取接收机位置的天气条件
            weather = weather_provider.get_weather_at(receiver_position)
            if weather:
                # 雨衰减
                if 'precipitation_rate' in weather:
                    rain_rate = weather['precipitation_rate']  # mm/h
                    if rain_rate > 0:
                        # 简化的雨衰减模型
                        # 频率越高，雨衰减越严重
                        freq_ghz = source.center_frequency / 1000.0
                        rain_attenuation = 0.01 * rain_rate * freq_ghz
                        modified_rx_power -= rain_attenuation

                # 雾衰减
                if 'visibility' in weather:
                    visibility = weather['visibility']  # km
                    if visibility < 1.0:
                        # 简化的雾衰减模型
                        fog_attenuation = 0.1 * (1.0 - visibility) * (source.center_frequency / 1000.0)
                        modified_rx_power -= fog_attenuation

        # 应用随机衰落
        # 简化的快速衰落模型
        if self.use_fast_fading:
            fast_fading = np.random.normal(0, 3.0)  # 均值0，标准差3dB的高斯分布
            modified_rx_power += fast_fading

        return modified_rx_power

    def get_detectable_signals_at_location(self, sensor_location: Tuple[float, float, float],
                                          frequency_bands_of_interest: List[Tuple[float, float]],
                                          sensor_sensitivity_threshold: float) -> List[Dict]:
        """
        获取特定位置可检测到的信号

        此方法是组件（如EMSensingComponent）获取电磁环境信息的主要API。

        Args:
            sensor_location: 传感器位置 (x, y, z)
            frequency_bands_of_interest: 感兴趣的频段列表，每个频段为 (min_freq, max_freq) (MHz)
            sensor_sensitivity_threshold: 传感器灵敏度阈值 (dBm)

        Returns:
            List[Dict]: 可检测到的信号列表，每个信号包含以下字段:
                - source_id: 信号源ID
                - position: 信号源位置
                - center_frequency: 中心频率
                - bandwidth: 带宽
                - received_power: 接收功率
                - signal_type: 信号类型
                - snr: 信噪比
        """
        # 获取所有活跃的信号源
        active_sources = self.get_active_signal_sources()

        # 过滤出感兴趣频段内的信号源
        filtered_sources = {}
        for source_id, source in active_sources.items():
            # 检查信号源频率是否在感兴趣频段内
            source_min_freq = source.center_frequency - source.bandwidth / 2
            source_max_freq = source.center_frequency + source.bandwidth / 2

            for min_freq, max_freq in frequency_bands_of_interest:
                # 检查频段是否重叠
                if not (source_max_freq < min_freq or source_min_freq > max_freq):
                    filtered_sources[source_id] = source
                    break

        # 计算每个信号源的接收功率
        detectable_signals = []
        for source_id, source in filtered_sources.items():
            # 计算接收功率
            received_power = self._calculate_received_power(source, sensor_location)

            # 检查是否超过灵敏度阈值
            if received_power >= sensor_sensitivity_threshold:
                # 计算信噪比
                snr = received_power - self.default_noise_floor

                # 添加到可检测信号列表
                detectable_signals.append({
                    'source_id': source_id,
                    'position': source.position,
                    'center_frequency': source.center_frequency,
                    'bandwidth': source.bandwidth,
                    'received_power': received_power,
                    'signal_type': source.signal_type,
                    'snr': snr,
                    'modulation': source.modulation,
                    'target_id': source.target_id
                })

        return detectable_signals
