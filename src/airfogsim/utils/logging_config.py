"""
AirFogSim日志配置模块

该模块提供了统一的日志配置，用于整个AirFogSim系统。
主要功能包括：
1. 配置日志格式和级别
2. 提供获取logger的函数
3. 提供日志级别的常量

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import logging
import os
from typing import Optional

# 默认日志格式
DEFAULT_LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# 配置根日志记录器
logging.basicConfig(
    level=logging.INFO,
    format=DEFAULT_LOG_FORMAT
)

def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    获取配置好的logger实例
    
    Args:
        name: 日志记录器名称，通常为模块名称
        
    Returns:
        logging.Logger: 配置好的日志记录器
    """
    logger = logging.getLogger(name)
    
    # 检查环境变量以允许覆盖日志级别
    log_level = os.environ.get('AIRFOGSIM_LOG_LEVEL', 'INFO').upper()
    if log_level in ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'):
        logger.setLevel(getattr(logging, log_level))
    
    return logger
