#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AirFogSim天气集成模块

该模块提供了天气数据集成功能，包括：
1. 天气数据提供者的配置和初始化
2. 天气数据从真实时间到仿真时间的转换
3. 天气变化事件的处理

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import os
import random
import math
from typing import Dict, Any, Optional, List

from airfogsim.core.dataprovider import DataIntegration
from airfogsim.dataprovider.weather import WeatherDataProvider
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class WeatherIntegration(DataIntegration):
    """
    天气集成类
    
    负责集成天气数据到仿真系统，并处理天气变化事件
    """
    
    def __init__(self, env, config=None):
        """
        初始化天气集成
        
        Args:
            env: 仿真环境
            config: 天气配置
        """
        # 默认配置
        self.default_config = {
            'location': {'lat': 31.2304, 'lon': 121.4737},  # 上海
            'api_refresh_interval': 1800,  # 30分钟刷新一次
            'simulation_start_time': None,  # 仿真开始时间（真实时间）
            'time_scale': 1.0,  # 时间缩放比例（仿真时间/真实时间）
            'use_mock_data': True,  # 是否使用模拟数据
        }

        config = config or {}
        self.default_config.update(config)
        
        # 调用父类初始化方法
        super().__init__(env, self.default_config)
    
    def _initialize_provider(self):
        """初始化天气数据提供者"""
        # 获取API密钥
        api_key = os.environ.get("OPENWEATHERMAP_API_KEY", "")
        
        # 如果没有API密钥且不使用模拟数据，打印警告
        if not api_key and not self.config['use_mock_data']:
            logger.warning("警告: 没有设置OpenWeatherMap API密钥，将使用模拟数据")
            self.config['use_mock_data'] = True
        
        # 如果使用模拟数据，创建模拟天气数据
        if self.config['use_mock_data']:
            self.weather_provider = MockWeatherProvider(self.env, self.config)
            logger.info(f"使用模拟天气数据，位置: {self.config['location']}")
        else:
            # 创建天气数据提供者配置
            weather_config = {
                'api_key': api_key,
                'location': self.config['location'],
                'api_refresh_interval': self.config['api_refresh_interval']
            }
            
            # 创建天气数据提供者
            self.weather_provider = WeatherDataProvider(self.env, config=weather_config)
            logger.info(f"使用OpenWeatherMap API获取天气数据，位置: {self.config['location']}")
        
        # 启动天气事件触发
        self.weather_provider.start_event_triggering()
        
        # 在env注册天气数据提供者
        self.env.add_data_provider('weather', self.weather_provider)
    
    def _register_event_listeners(self):
        """注册天气变化事件监听器"""
        # 注册天气变化事件监听器
        self.env.event_registry.subscribe(
            source_id=self.weather_provider.__class__.__name__, 
            listener_id='weather_integration',
            event_name=self.weather_provider.EVENT_WEATHER_CHANGED,
            callback=self._on_weather_changed
        )
    
    def _on_weather_changed(self, event_data):
        """
        处理天气变化事件
        
        Args:
            event_data: 事件数据
        """
        # 打印天气变化信息
        sim_time = event_data.get('sim_timestamp', 'N/A')
        severity = event_data.get('severity', 'N/A')
        condition = event_data.get('condition', 'N/A')
        temp = event_data.get('temperature', 'N/A')
        wind_speed = event_data.get('wind_speed', 'N/A')
        
        logger.info(f"时间 {sim_time}: 天气变化 - 严重程度: {severity}, 状况: {condition}, 温度: {temp}°C, 风速: {wind_speed}m/s")
        
        # 更新所有代理的外部力
        self._update_agents_external_force(event_data)
    
    def _update_agents_external_force(self, weather_data):
        """
        更新所有代理的外部力
        
        Args:
            weather_data: 天气数据
        """
        # 计算外部力
        external_force = self._calculate_external_force(weather_data)
        
        # 更新所有代理的外部力
        for agent_id, agent in self.env.agents.items():
            if hasattr(agent, 'update_state'):
                agent.update_state('external_force', external_force)
    
    def _calculate_external_force(self, weather_data):
        """
        计算外部力
        
        Args:
            weather_data: 天气数据
            
        Returns:
            外部力向量 [fx, fy, fz]
        """
        # 获取风速和风向
        wind_speed = weather_data.get('wind_speed', 0)
        wind_direction = weather_data.get('wind_direction', 0)
        
        # 将风向转换为弧度
        wind_direction_rad = math.radians(wind_direction)
        
        # 计算风力分量
        force_x = wind_speed * math.cos(wind_direction_rad) * 0.1
        force_y = wind_speed * math.sin(wind_direction_rad) * 0.1
        force_z = 0.0
        
        return [force_x, force_y, force_z]


class MockWeatherProvider:
    """
    模拟天气数据提供者
    
    用于在没有API密钥的情况下提供模拟天气数据
    """
    
    # 天气变化事件名称
    EVENT_WEATHER_CHANGED = 'weather_changed'
    
    def __init__(self, env, config=None):
        """
        初始化模拟天气数据提供者
        
        Args:
            env: 仿真环境
            config: 配置
        """
        self.env = env
        self.config = config or {}
        
        # 天气状态
        self.weather_state = {
            'temperature': 25.0,
            'wind_speed': 5.0,
            'wind_direction': 0,
            'condition': 'CLEAR',
            'severity': 'NORMAL',
            'precipitation_rate': 0.0,
            'humidity': 50.0,
            'pressure': 1013.0,
            'visibility': 10.0,
            'cloud_cover': 0.0,
            'region': {
                'lat': self.config.get('location', {}).get('lat', 31.2304),
                'lon': self.config.get('location', {}).get('lon', 121.4737),
                'radius': 50.0
            }
        }
        
        # 天气变化间隔（仿真时间）
        self.weather_change_interval = 60  # 1分钟
    
    def start_event_triggering(self):
        """启动天气事件触发"""
        # 启动天气变化进程
        self.env.process(self._weather_change_process())
    
    def _weather_change_process(self):
        """天气变化进程"""
        while True:
            # 等待指定时间
            yield self.env.timeout(self.weather_change_interval)
            
            # 更新天气状态
            self._update_weather_state()
            
            # 触发天气变化事件
            self._trigger_weather_changed_event()
    
    def _update_weather_state(self):
        """更新天气状态"""
        # 随机更新天气状态
        self.weather_state['temperature'] = max(0, min(40, self.weather_state['temperature'] + random.uniform(-5, 5)))
        self.weather_state['wind_speed'] = max(0, min(20, self.weather_state['wind_speed'] + random.uniform(-2, 2)))
        self.weather_state['wind_direction'] = (self.weather_state['wind_direction'] + random.uniform(-45, 45)) % 360
        
        # 随机更新天气状况
        conditions = ['CLEAR', 'CLOUDY', 'RAIN', 'SNOW', 'FOG', 'THUNDERSTORM']
        weights = [0.4, 0.3, 0.15, 0.05, 0.05, 0.05]
        self.weather_state['condition'] = random.choices(conditions, weights=weights)[0]
        
        # 根据天气状况设置严重程度
        if self.weather_state['condition'] == 'CLEAR':
            self.weather_state['severity'] = 'NORMAL'
        elif self.weather_state['condition'] == 'CLOUDY':
            self.weather_state['severity'] = 'NORMAL'
        elif self.weather_state['condition'] == 'RAIN':
            self.weather_state['severity'] = 'MODERATE'
            if self.weather_state['wind_speed'] > 10:
                self.weather_state['severity'] = 'HEAVY_RAIN'
        elif self.weather_state['condition'] == 'SNOW':
            self.weather_state['severity'] = 'MODERATE'
            if self.weather_state['wind_speed'] > 10:
                self.weather_state['severity'] = 'HEAVY_SNOW'
        elif self.weather_state['condition'] == 'FOG':
            self.weather_state['severity'] = 'MODERATE'
        elif self.weather_state['condition'] == 'THUNDERSTORM':
            self.weather_state['severity'] = 'STORM'
        
        # 根据风速设置严重程度
        if self.weather_state['wind_speed'] > 15:
            self.weather_state['severity'] = 'HIGH_WINDS'
        
        # 更新其他天气参数
        if self.weather_state['condition'] == 'RAIN':
            self.weather_state['precipitation_rate'] = random.uniform(1, 10)
        elif self.weather_state['condition'] == 'SNOW':
            self.weather_state['precipitation_rate'] = random.uniform(1, 5)
        else:
            self.weather_state['precipitation_rate'] = 0.0
        
        self.weather_state['humidity'] = random.uniform(30, 90)
        self.weather_state['pressure'] = random.uniform(990, 1030)
        self.weather_state['visibility'] = random.uniform(1, 10)
        self.weather_state['cloud_cover'] = random.uniform(0, 100)
    
    def _trigger_weather_changed_event(self):
        """触发天气变化事件"""
        # 创建事件数据
        event_data = self.weather_state.copy()
        event_data['sim_timestamp'] = self.env.now
        
        # 触发事件
        self.env.event_registry.trigger_event(
            source_id=self.__class__.__name__,
            event_name=self.EVENT_WEATHER_CHANGED,
            event_value=event_data
        )
