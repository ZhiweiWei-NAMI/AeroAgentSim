"""
AirFogSim工具模块

该模块提供了各种实用工具函数和类，用于辅助AirFogSim系统的开发和运行。
主要功能包括：
1. 日志配置和管理
2. 其他实用工具函数

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from .logging_config import get_logger

__all__ = ['get_logger']
