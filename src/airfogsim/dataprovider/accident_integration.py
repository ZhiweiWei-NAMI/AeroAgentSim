# -*- coding: utf-8 -*-
"""
AirFogSim意外事件集成模块

该模块提供了意外事件数据提供者的集成功能，用于在仿真中生成和处理意外事件。
"""

import os
import logging
from typing import Dict, Any, Optional

from ..core.dataprovider import DataIntegration
from .accident import AccidentDataProvider

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class AccidentIntegration(DataIntegration):
    """
    意外事件集成类
    
    负责集成意外事件数据到仿真系统，并处理意外事件变化事件
    """
    
    def __init__(self, env, config=None):
        """
        初始化意外事件集成
        
        Args:
            env: 仿真环境
            config: 意外事件配置
        """
        # 默认配置
        self.default_config = {
            'check_interval': 300,  # 检查间隔（秒）
            'base_probability': 0.01,  # 基础概率
            'min_duration': 600,  # 最小持续时间（秒）
            'max_duration': 3600,  # 最大持续时间（秒）
            'bayesian_params': {
                'weather_influence': {
                    'THUNDERSTORM': 0.8,
                    'SNOW': 0.6,
                    'RAIN': 0.4,
                    'FOG': 0.5,
                    'CLOUDY': 0.2,
                    'CLEAR': 0.1
                },
                'traffic_influence': {
                    'congestion_threshold': 20,  # 区域内车辆数量
                    'congestion_factor': 0.7,
                    'speed_threshold': 10,  # m/s
                    'speed_factor': 0.5
                }
            }
        }
        
        # 调用父类初始化方法
        super().__init__(env, config)
    
    def _initialize_provider(self):
        """初始化意外事件数据提供者"""
        # 创建意外事件数据提供者配置
        accident_config = {
            'check_interval': self.config.get('check_interval'),
            'base_probability': self.config.get('base_probability'),
            'min_duration': self.config.get('min_duration'),
            'max_duration': self.config.get('max_duration'),
            'bayesian_params': self.config.get('bayesian_params')
        }
        
        # 创建意外事件数据提供者
        self.accident_provider = AccidentDataProvider(self.env, config=accident_config)
        
        # 加载数据
        self.accident_provider.load_data()
        
        # 启动意外事件触发
        self.accident_provider.start_event_triggering()
        
        # 在env注册意外事件数据提供者
        self.env.add_data_provider('accident', self.accident_provider)
        
        logger.info(f"意外事件数据提供者初始化完成")
    
    def _register_event_listeners(self):
        """注册意外事件监听器"""
        # 注册意外事件报告事件监听器
        self.env.event_registry.subscribe(
            source_id=self.accident_provider.__class__.__name__, 
            listener_id='accident_integration',
            event_name=self.accident_provider.EVENT_ACCIDENT_REPORTED,
            callback=self._on_accident_reported
        )
        
        # 注册意外事件清除事件监听器
        self.env.event_registry.subscribe(
            source_id=self.accident_provider.__class__.__name__, 
            listener_id='accident_integration',
            event_name=self.accident_provider.EVENT_ACCIDENT_CLEARED,
            callback=self._on_accident_cleared
        )
    
    def _on_accident_reported(self, event_data):
        """
        处理意外事件报告事件
        
        Args:
            event_data: 事件数据
        """
        # 打印意外事件信息
        sim_time = event_data.get('sim_timestamp', 'N/A')
        accident_type = event_data.get('type', 'N/A')
        severity = event_data.get('severity', 'N/A')
        location = event_data.get('location', {})
        duration = event_data.get('duration', 'N/A')
        
        logger.info(f"时间 {sim_time}: 意外事件报告 - 类型: {accident_type}, 严重程度: {severity}, "
                   f"位置: ({location.get('x', 'N/A')}, {location.get('y', 'N/A')}), 持续时间: {duration}秒")
        
        # 更新所有代理的状态
        self._update_agents_for_accident(event_data)
    
    def _on_accident_cleared(self, event_data):
        """
        处理意外事件清除事件
        
        Args:
            event_data: 事件数据
        """
        # 打印意外事件清除信息
        sim_time = event_data.get('cleared_time', 'N/A')
        accident_id = event_data.get('id', 'N/A')
        
        logger.info(f"时间 {sim_time}: 意外事件 {accident_id} 已清除")
        
        # 通知所有代理意外事件已清除
        self._notify_agents_accident_cleared(event_data)
    
    def _update_agents_for_accident(self, event_data):
        """
        更新所有代理的状态以响应意外事件
        
        Args:
            event_data: 事件数据
        """
        # 获取所有代理
        agents = getattr(self.env, 'agents', {})
        
        # 获取意外事件信息
        location = event_data.get('location', {})
        affected_radius = event_data.get('affected_radius', 100)
        
        # 更新受影响区域内的代理
        for agent_id, agent in agents.items():
            # 获取代理位置
            position = agent.get_state('position')
            if not position:
                continue
            
            # 检查代理是否在受影响区域内
            x1, y1, z1 = position
            x2 = location.get('x', 0)
            y2 = location.get('y', 0)
            z2 = location.get('z', 0)
            
            # 计算距离
            distance = ((x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2) ** 0.5
            
            # 如果在受影响区域内，更新代理状态
            if distance <= affected_radius:
                logger.info(f"代理 {agent_id} 在意外事件影响范围内，距离: {distance:.2f}米")
                
                # 调用意外事件处理函数
                handler_func_name = event_data.get('handler_function')
                if handler_func_name and hasattr(self.accident_provider, handler_func_name):
                    handler_func = getattr(self.accident_provider, handler_func_name)
                    handler_func(agent, event_data)
    
    def _notify_agents_accident_cleared(self, event_data):
        """
        通知所有代理意外事件已清除
        
        Args:
            event_data: 事件数据
        """
        # 获取所有代理
        agents = getattr(self.env, 'agents', {})
        
        # 获取意外事件信息
        accident_id = event_data.get('id', 'N/A')
        
        # 通知所有代理
        for agent_id, agent in agents.items():
            # 如果代理有处理意外事件清除的方法，调用它
            if hasattr(agent, 'on_accident_cleared'):
                agent.on_accident_cleared(accident_id, event_data)
