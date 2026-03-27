# -*- coding: utf-8 -*-
"""
AirFogSim数据提供者模块

该模块包含各种数据提供者和数据集成类，用于将外部数据集成到仿真系统中。
主要内容包括：
1. 天气数据提供者和集成
2. 事故数据集成
3. 信号数据提供者和集成
"""

from .weather import WeatherDataProvider
from .signal import SignalDataProvider, SignalSource
from .signal_integration import ExternalSignalSourceIntegration

__all__ = [
    'WeatherDataProvider',
    'SignalDataProvider',
    'SignalSource',
    'ExternalSignalSourceIntegration'
]
