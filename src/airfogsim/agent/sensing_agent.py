"""
AirFogSim感知代理模块

该模块定义了感知代理类，实现了对周围环境的感知能力。
主要功能包括：
1. 物理对象感知
2. 环境状态监测
3. 感知数据处理

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.agent.terminal import TerminalAgent, TerminalAgentMeta
from airfogsim.component.object_sensor import ObjectSensorComponent
from typing import Dict, Optional
import uuid
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class SensingAgentMeta(TerminalAgentMeta):
    """感知代理元类"""

    def __new__(mcs, name, bases, attrs):
        cls = super().__new__(mcs, name, bases, attrs)
        
        # 注册感知代理专用的状态模板
        mcs.register_template(cls, 'nearby_objects', list, False, None,
                            "代理感知到的附近对象列表")
        mcs.register_template(cls, 'last_object_scan_time', float, False, None,
                            "上次扫描时间")
        mcs.register_template(cls, 'sensor_range', float, False, None,
                            "感知范围 (米)")
        mcs.register_template(cls, 'sensor_accuracy', float, False, None,
                            "感知精度 (0.0-1.0)")
        
        return cls


class SensingAgent(TerminalAgent, metaclass=SensingAgentMeta):
    """
    感知代理，能够感知周围环境中的物理对象
    
    该代理继承自终端代理，添加了物理对象感知能力。
    可以检测周围的物理对象，并模拟传感器的不确定性。
    """

    @classmethod
    def get_description(cls):
        """获取代理类型的描述"""
        return "感知代理 - 能够感知周围环境中的物理对象，支持位置感知和对象分类"

    def __init__(self, env, agent_name: str, properties=None, agent_id=None):
        # 确保properties是一个字典
        properties = properties or {}
        
        # 设置默认位置
        if 'position' not in properties:
            properties['position'] = (0, 0, 0)
            
        # 设置感知相关属性
        self.sensor_range = properties.get('sensor_range', 50.0)
        self.position_accuracy = properties.get('position_accuracy', 2.0)
        self.classification_error_rate = properties.get('classification_error_rate', 0.1)
        self.detection_probability = properties.get('detection_probability', 0.95)
        
        # 调用父类初始化
        super().__init__(env, agent_name, properties, agent_id)
        
        # 设置自定义ID
        if agent_id is None:
            self.id = f"agent_sensing_{uuid.uuid4().hex[:8]}"
            
        # 初始化感知状态
        self.update_state('nearby_objects', [])
        self.update_state('last_object_scan_time', 0.0)
        self.update_state('sensor_range', self.sensor_range)
        self.update_state('sensor_accuracy', 1.0 - self.classification_error_rate)
        
        # 添加物理对象感知组件
        self._add_sensing_components()
        
        # 注册事件处理器
        self._register_event_handlers()
        
    def _add_sensing_components(self):
        """添加感知相关组件"""
        # 添加物理对象感知组件
        self.add_component(ObjectSensorComponent(
            env=self.env,
            agent=self,
            properties={
                'range': self.sensor_range,
                'sensing_interval': 1.0,  # 每秒感知一次
                'position_accuracy_stddev': self.position_accuracy,
                'classification_error_rate': self.classification_error_rate,
                'detection_probability': self.detection_probability
            }
        ))
        
    def _register_event_handlers(self):
        """注册事件处理器"""
        # 注册对象检测事件处理器
        self.env.event_registry.subscribe(
            self.id, 
            'ObjectSensor.object_detected', 
            f"{self.id}_object_detected_handler",
            self._on_object_detected
        )
        
        # 注册对象丢失事件处理器
        self.env.event_registry.subscribe(
            self.id, 
            'ObjectSensor.object_lost', 
            f"{self.id}_object_lost_handler",
            self._on_object_lost
        )
        
    def _on_object_detected(self, event_data):
        """处理对象检测事件"""
        obj_id = event_data.get('id')
        obj_type = event_data.get('type')
        obj_distance = event_data.get('distance')
        logger.info(f"时间 {self.env.now:.1f}: {self.id} 检测到新对象 {obj_id}，类型: {obj_type}，距离: {obj_distance:.2f}米")
        
    def _on_object_lost(self, event_data):
        """处理对象丢失事件"""
        obj_id = event_data.get('id')
        logger.info(f"时间 {self.env.now:.1f}: {self.id} 丢失对象 {obj_id}")
        
    def get_nearby_objects(self):
        """获取附近的对象"""
        return self.get_state('nearby_objects', [])
    
    def get_objects_by_type(self, object_type):
        """获取指定类型的附近对象"""
        nearby_objects = self.get_nearby_objects()
        return [obj for obj in nearby_objects if obj.get('type') == object_type]
    
    def get_nearest_object(self, object_type=None):
        """获取最近的对象"""
        nearby_objects = self.get_nearby_objects()
        
        if object_type:
            # 过滤指定类型的对象
            filtered_objects = [obj for obj in nearby_objects if obj.get('type') == object_type]
        else:
            filtered_objects = nearby_objects
            
        if not filtered_objects:
            return None
            
        # 按距离排序
        sorted_objects = sorted(filtered_objects, key=lambda obj: obj.get('distance', float('inf')))
        return sorted_objects[0] if sorted_objects else None
    
    def get_details(self) -> Dict:
        """获取代理详细信息"""
        details = super().get_details()
        
        # 添加感知相关信息
        nearby_objects = self.get_nearby_objects()
        
        details.update({
            'sensing_info': {
                'sensor_range': self.get_state('sensor_range'),
                'sensor_accuracy': self.get_state('sensor_accuracy'),
                'last_scan_time': self.get_state('last_object_scan_time'),
                'detected_objects_count': len(nearby_objects),
                'detected_object_types': list(set(obj.get('type') for obj in nearby_objects))
            }
        })
        
        return details
