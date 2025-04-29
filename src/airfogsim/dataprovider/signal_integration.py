"""
AirFogSim 信号数据集成模块

该模块实现了信号数据集成，用于加载外部信号源定义并注册到信号数据提供者。
主要功能包括：
1. 从配置文件加载信号源定义
2. 将信号源注册到信号数据提供者
3. 管理信号源的激活和停用

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import os
import json
import yaml
from airfogsim.utils.logging_config import get_logger
import numpy as np
from typing import Dict, List, Any, Optional, Tuple, Callable

from airfogsim.core.dataprovider import DataIntegration
from airfogsim.dataprovider.signal import SignalDataProvider, SignalSource
from airfogsim.core.trigger import TimeTrigger

logger = get_logger(__name__)

def calculate_link_quality(link_context: Dict[str, Any], env) -> Dict[str, float]:
    """
    计算链路质量指标，包括SINR、接收功率和干扰功率

    该函数作为FrequencyManager的链路质量计算器Hook函数，用于计算链路质量指标。
    它使用SignalDataProvider提供的信号传播和干扰计算功能，但不直接依赖于SignalDataProvider的内部实现。

    Args:
        link_context: 链路上下文，包含以下字段:
            - source_id: 源代理ID
            - target_id: 目标代理ID
            - source_position: 源代理位置 (x, y, z)
            - target_position: 目标代理位置 (x, y, z)
            - transmit_power_dbm: 发射功率 (dBm)
            - center_frequency: 中心频率 (MHz)
            - bandwidth: 带宽 (MHz)
            - resource_id: 资源ID
        env: 仿真环境

    Returns:
        包含链路质量指标的字典，包括:
            - received_power_dbm: 接收功率 (dBm)
            - interference_dbm: 干扰功率 (dBm)
            - noise_dbm: 噪声功率 (dBm)
            - sinr: 信噪干扰比 (dB)
    """
    # 获取SignalDataProvider
    signal_provider = getattr(env, 'signal_data_provider', None)
    if not signal_provider:
        logger.warning("No SignalDataProvider found in environment. Using default values.")
        return None

    # 提取链路上下文信息
    source_id = link_context.get('source_id')
    target_id = link_context.get('target_id')
    source_position = link_context.get('source_position')
    target_position = link_context.get('target_position')
    transmit_power_dbm = link_context.get('transmit_power_dbm')
    center_frequency = link_context.get('center_frequency')
    bandwidth = link_context.get('bandwidth')

    # 创建临时信号源
    temp_source = SignalSource(
        source_id=f"temp_{source_id}",
        position=source_position,
        transmit_power_dbm=transmit_power_dbm,
        center_frequency=center_frequency,
        bandwidth=bandwidth,
        signal_type='communication',
        target_id=target_id
    )

    # 计算接收功率
    rx_power_dbm = signal_provider._calculate_received_power(temp_source, target_position)

    # 获取干扰信号
    interference_signals = signal_provider.get_detectable_signals_at_location(
        sensor_location=target_position,
        frequency_bands_of_interest=[(center_frequency - bandwidth/2, center_frequency + bandwidth/2)],
        sensor_sensitivity_threshold=-120.0  # 非常低的阈值
    )

    # 计算干扰功率（排除目标信号）
    interference_power_mw = 0.0
    for signal in interference_signals:
        # 排除目标信号
        if signal['source_id'] != f"temp_{source_id}" and signal['source_id'] != source_id:
            # 将dBm转换为mW
            interference_power_mw += 10 ** (signal['received_power'] / 10)

    # 计算干扰功率(dBm)
    interference_dbm = -float('inf')
    if interference_power_mw > 0:
        interference_dbm = 10 * np.log10(interference_power_mw)

    # 获取噪声功率
    noise_dbm = signal_provider.default_noise_floor
    noise_mw = 10 ** (noise_dbm / 10)

    # 计算SINR
    if interference_power_mw > 0:
        # 干扰加噪声功率 (线性域相加)
        interference_noise_mw = interference_power_mw + noise_mw
        interference_noise_dbm = 10 * np.log10(interference_noise_mw)
        sinr = rx_power_dbm - interference_noise_dbm
    else:
        # 无干扰，仅考虑噪声
        sinr = rx_power_dbm - noise_dbm

    # 返回链路质量指标
    return {
        'received_power_dbm': rx_power_dbm,
        'interference_dbm': interference_dbm,
        'noise_dbm': noise_dbm,
        'sinr': sinr
    }


def register_link_quality_calculator(env):
    """
    注册链路质量计算函数到频率管理器

    这是一个便捷函数，用于将链路质量计算函数注册到频率管理器。
    它可以在不创建SignalIntegration实例的情况下使用。

    Args:
        env: 仿真环境

    Returns:
        bool: 是否成功注册
    """
    # 获取频率管理器
    frequency_manager = getattr(env, 'frequency_manager', None)
    if not frequency_manager:
        logger.warning("No FrequencyManager found in environment. Cannot register link quality calculator.")
        return False

    # 注册链路质量计算函数
    frequency_manager.config['link_quality_calculator'] = calculate_link_quality
    if hasattr(frequency_manager, 'link_quality_calculator'):
        frequency_manager.link_quality_calculator = calculate_link_quality
    logger.info("Registered link quality calculator with FrequencyManager")
    return True

class ExternalSignalSourceIntegration(DataIntegration):
    """
    外部信号源集成，用于加载外部信号源定义并注册到信号数据提供者
    """

    def __init__(self, env, config=None):
        """
        初始化外部信号源集成

        Args:
            env: 仿真环境
            config: 配置字典，可能包含以下字段:
                - sources_file: 信号源定义文件路径
                - sources: 直接定义的信号源列表
                - auto_load: 是否自动加载信号源 (默认为True)
                - signal_provider_id: 信号数据提供者ID (默认为'signal_data_provider')
        """
        # 合并默认配置和用户配置
        default_config = {
            'sources_file': None,
            'sources': [],
            'auto_load': True,
            'signal_provider_id': 'signal_data_provider'
        }

        # 如果config为None，使用空字典
        user_config = config or {}

        # 合并配置
        merged_config = default_config.copy()
        merged_config.update(user_config)

        # 设置signal_provider_id，这样_initialize_provider可以使用它
        self.signal_provider_id = merged_config.get('signal_provider_id')

        super().__init__(env, merged_config)

        # 信号源定义
        self.sources_file = self.config.get('sources_file')
        self.sources = self.config.get('sources', [])
        self.auto_load = self.config.get('auto_load', True)

        # 已加载的信号源
        self.loaded_sources = {}

        # 信号源激活/停用触发器
        self.activation_triggers = {}

        # 如果配置了自动加载，则立即加载信号源
        if self.auto_load:
            self.load_signal_sources()

        # 如果配置了与频率管理器集成，则注册链路质量计算函数
        if self.config.get('integrate_with_frequency_manager', False):
            self.integrate_with_frequency_manager()

    def _initialize_provider(self):
        """
        初始化信号数据提供者
        """
        # 检查环境中是否已有信号数据提供者
        if not hasattr(self.env, self.signal_provider_id):
            # 创建信号数据提供者
            signal_provider = SignalDataProvider(self.env, self.config.get('provider_config', {}))

            # 注册到环境
            setattr(self.env, self.signal_provider_id, signal_provider)

            logger.info(f"Created SignalDataProvider with ID: {self.signal_provider_id}")

        # 获取信号数据提供者引用
        self.signal_provider = getattr(self.env, self.signal_provider_id)

    def _register_event_listeners(self):
        """
        注册事件监听器
        """
        # 订阅信号源事件
        self.env.event_registry.subscribe(
            self.signal_provider.__class__.__name__,
            SignalDataProvider.EVENT_SIGNAL_SOURCE_ADDED,
            f"{self.__class__.__name__}_source_added_handler",
            self._on_signal_source_added
        )

        self.env.event_registry.subscribe(
            self.signal_provider.__class__.__name__,
            SignalDataProvider.EVENT_SIGNAL_SOURCE_REMOVED,
            f"{self.__class__.__name__}_source_removed_handler",
            self._on_signal_source_removed
        )

    def _on_signal_source_added(self, event_data):
        """
        处理信号源添加事件

        Args:
            event_data: 事件数据
        """
        source_id = event_data.get('source_id')
        logger.debug(f"Signal source added: {source_id}")

    def _on_signal_source_removed(self, event_data):
        """
        处理信号源移除事件

        Args:
            event_data: 事件数据
        """
        source_id = event_data.get('source_id')
        logger.debug(f"Signal source removed: {source_id}")

        # 如果有对应的激活触发器，也移除它
        if source_id in self.activation_triggers:
            trigger = self.activation_triggers.pop(source_id)
            trigger.deactivate()
            logger.debug(f"Removed activation trigger for source: {source_id}")

    def load_signal_sources(self):
        """
        加载信号源定义

        从配置文件和直接定义的信号源列表加载信号源。
        """
        # 从文件加载
        if self.sources_file:
            self._load_sources_from_file(self.sources_file)

        # 从配置加载
        if self.sources:
            self._load_sources_from_config(self.sources)

    def _load_sources_from_file(self, file_path):
        """
        从文件加载信号源定义

        支持JSON和YAML格式。

        Args:
            file_path: 文件路径
        """
        if not os.path.exists(file_path):
            logger.warning(f"Signal sources file not found: {file_path}")
            return

        try:
            # 根据文件扩展名选择解析器
            ext = os.path.splitext(file_path)[1].lower()

            if ext in ['.json']:
                with open(file_path, 'r') as f:
                    sources_data = json.load(f)
            elif ext in ['.yaml', '.yml']:
                with open(file_path, 'r') as f:
                    sources_data = yaml.safe_load(f)
            else:
                logger.warning(f"Unsupported file format: {ext}")
                return

            # 加载信号源
            self._load_sources_from_config(sources_data)

            logger.info(f"Loaded signal sources from file: {file_path}")

        except Exception as e:
            logger.error(f"Error loading signal sources from file: {e}")

    def _load_sources_from_config(self, sources_data):
        """
        从配置加载信号源定义

        Args:
            sources_data: 信号源定义列表或字典
        """
        # 确保sources_data是列表
        if isinstance(sources_data, dict):
            sources_list = sources_data.get('sources', [])
        else:
            sources_list = sources_data

        # 遍历信号源定义
        for source_def in sources_list:
            try:
                # 创建信号源
                source = self._create_signal_source(source_def)

                if source:
                    # 注册到信号数据提供者
                    if self.signal_provider.add_signal_source(source):
                        # 记录已加载的信号源
                        self.loaded_sources[source.source_id] = source

                        # 如果定义了激活时间，创建激活触发器
                        if 'activation_time' in source_def:
                            self._create_activation_trigger(source.source_id, source_def)

                        # 如果定义了停用时间，创建停用触发器
                        if 'deactivation_time' in source_def:
                            self._create_deactivation_trigger(source.source_id, source_def)

            except Exception as e:
                logger.error(f"Error creating signal source: {e}")

        logger.info(f"Loaded {len(self.loaded_sources)} signal sources from config")

    def _create_signal_source(self, source_def):
        """
        从定义创建信号源

        Args:
            source_def: 信号源定义字典

        Returns:
            SignalSource or None: 创建的信号源，如果创建失败则返回None
        """
        # 检查必要字段
        required_fields = ['source_id', 'position', 'transmit_power_dbm', 'center_frequency', 'bandwidth']
        for field in required_fields:
            if field not in source_def:
                logger.warning(f"Missing required field '{field}' in signal source definition")
                return None

        # 创建信号源
        try:
            source = SignalSource(
                source_id=source_def['source_id'],
                position=source_def['position'],
                transmit_power_dbm=source_def['transmit_power_dbm'],
                center_frequency=source_def['center_frequency'],
                bandwidth=source_def['bandwidth'],
                signal_type=source_def.get('signal_type', 'communication'),
                is_active=source_def.get('is_active', True),
                modulation=source_def.get('modulation'),
                polarization=source_def.get('polarization'),
                antenna_gain=source_def.get('antenna_gain', 0.0),
                antenna_pattern=source_def.get('antenna_pattern'),
                target_id=source_def.get('target_id')
            )
            return source

        except Exception as e:
            logger.error(f"Error creating signal source: {e}")
            return None

    def _create_activation_trigger(self, source_id, source_def):
        """
        创建信号源激活触发器

        Args:
            source_id: 信号源ID
            source_def: 信号源定义
        """
        activation_time = source_def.get('activation_time')
        if activation_time is None:
            return

        # 创建时间触发器
        trigger_id = f"activate_{source_id}"
        trigger = TimeTrigger(
            env=self.env,
            trigger_id=trigger_id,
            activation_time=activation_time,
            callback=lambda: self._activate_signal_source(source_id)
        )

        # 激活触发器
        trigger.activate()

        # 记录触发器
        self.activation_triggers[source_id] = trigger

        logger.debug(f"Created activation trigger for source {source_id} at time {activation_time}")

    def _create_deactivation_trigger(self, source_id, source_def):
        """
        创建信号源停用触发器

        Args:
            source_id: 信号源ID
            source_def: 信号源定义
        """
        deactivation_time = source_def.get('deactivation_time')
        if deactivation_time is None:
            return

        # 创建时间触发器
        trigger_id = f"deactivate_{source_id}"
        trigger = TimeTrigger(
            env=self.env,
            trigger_id=trigger_id,
            activation_time=deactivation_time,
            callback=lambda: self._deactivate_signal_source(source_id)
        )

        # 激活触发器
        trigger.activate()

        # 记录触发器
        self.activation_triggers[f"deactivate_{source_id}"] = trigger

        logger.debug(f"Created deactivation trigger for source {source_id} at time {deactivation_time}")

    def _activate_signal_source(self, source_id):
        """
        激活信号源

        Args:
            source_id: 信号源ID
        """
        # 更新信号源状态
        self.signal_provider.update_signal_source(source_id, is_active=True)
        logger.info(f"Activated signal source: {source_id} at time {self.env.now}")

    def _deactivate_signal_source(self, source_id):
        """
        停用信号源

        Args:
            source_id: 信号源ID
        """
        # 更新信号源状态
        self.signal_provider.update_signal_source(source_id, is_active=False)
        logger.info(f"Deactivated signal source: {source_id} at time {self.env.now}")

    def add_signal_source(self, source_def):
        """
        添加信号源

        Args:
            source_def: 信号源定义

        Returns:
            bool: 是否成功添加
        """
        # 创建信号源
        source = self._create_signal_source(source_def)

        if not source:
            return False

        # 注册到信号数据提供者
        if self.signal_provider.add_signal_source(source):
            # 记录已加载的信号源
            self.loaded_sources[source.source_id] = source

            # 如果定义了激活时间，创建激活触发器
            if 'activation_time' in source_def:
                self._create_activation_trigger(source.source_id, source_def)

            # 如果定义了停用时间，创建停用触发器
            if 'deactivation_time' in source_def:
                self._create_deactivation_trigger(source.source_id, source_def)

            return True

        return False

    def remove_signal_source(self, source_id):
        """
        移除信号源

        Args:
            source_id: 信号源ID

        Returns:
            bool: 是否成功移除
        """
        # 从信号数据提供者移除
        if self.signal_provider.remove_signal_source(source_id):
            # 从已加载信号源中移除
            if source_id in self.loaded_sources:
                del self.loaded_sources[source_id]

            return True

        return False

    def integrate_with_frequency_manager(self):
        """
        与频率管理器集成

        将链路质量计算函数注册到频率管理器
        """
        return register_link_quality_calculator(self.env)
