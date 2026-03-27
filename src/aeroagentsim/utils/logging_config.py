"""
AeroAgentSim 日志配置模块。

该模块提供统一的日志配置，并同时兼容新的 ``AEROAGENTSIM_LOG_LEVEL``
和历史 ``AIRFOGSIM_LOG_LEVEL`` 环境变量。
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
    
    # 优先使用新的公共命名，同时保留历史别名。
    log_level = (
        os.environ.get('AEROAGENTSIM_LOG_LEVEL')
        or os.environ.get('AIRFOGSIM_LOG_LEVEL')
        or 'INFO'
    ).upper()
    if log_level in ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'):
        logger.setLevel(getattr(logging, log_level))
    
    return logger
