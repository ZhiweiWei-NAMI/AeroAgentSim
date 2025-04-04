"""
AirFogSim物品管理器模块

该模块定义了物品(Payload)管理器，负责物品的创建、注册、跟踪和查询。
主要功能包括：
1. 物品创建和注册
2. 物品位置和状态跟踪
3. 物品查询接口
4. 与空间管理器集成

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from typing import Dict, List, Optional, Tuple, Any
import uuid

class PayloadManager:
    """
    物品管理器
    
    负责管理物流系统中的物品(Payload)，包括创建、注册、跟踪和查询物品。
    """
    
    def __init__(self, env):
        """
        初始化物品管理器
        
        Args:
            env: 仿真环境
        """
        self.env = env
        self.payloads = {}  # 存储所有物品，格式：payload_id -> payload_info
        self.payload_locations = {}  # 存储物品位置，格式：payload_id -> (x, y, z)
        self.payload_carriers = {}  # 存储物品的携带者，格式：payload_id -> agent_id
        self.payload_status = {}  # 存储物品状态，格式：payload_id -> status
        self.payload_records = {} # 存储物品记录，格式：payload_id -> record
        
        # 物品状态枚举
        self.STATUS_CREATED = 'created'  # 已创建但未被取件
        self.STATUS_PICKED = 'picked'    # 已被取件，正在运输
        self.STATUS_DELIVERED = 'delivered'  # 已交付
        
        # 注册事件
        self.env.event_registry.register_event(self.id, 'payload_created')
        self.env.event_registry.register_event(self.id, 'payload_picked')
        self.env.event_registry.register_event(self.id, 'payload_delivered')
        self.env.event_registry.register_event(self.id, 'payload_location_changed')
        
        # 如果环境有空间管理器，设置引用
        self.airspace_manager = getattr(env, 'airspace_manager', None)

    def add_payload_record(self, payload_id: str, record: Dict[str, Any]) -> bool:
        """
        添加物品记录
        
        Args:
            payload_id: 物品ID
            record: 记录内容
            
        Returns:
            bool: 是否成功添加
        """
        if payload_id not in self.payloads:
            print(f"警告: 物品 {payload_id} 不存在，无法添加记录")
            return False
        
        if payload_id not in self.payload_records:
            self.payload_records[payload_id] = []
        
        self.payload_records[payload_id].append(record)
        return True
    
    def get_payload_records(self, payload_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        获取物品记录
        
        Args:
            payload_id: 物品ID
            
        Returns:
            List[Dict[str, Any]]: 物品记录列表，如果不存在则返回None
        """
        return self.payload_records.get(payload_id)
    
    @property
    def id(self):
        """获取管理器ID"""
        return 'payload_manager'
    
    def create_payload(self, properties: Dict[str, Any] = None) -> str:
        """
        创建新物品
        
        Args:
            properties: 物品属性
            
        Returns:
            str: 物品ID
        """
        properties = properties or {}
        
        # 生成唯一ID
        payload_id = properties.get('id') or f"payload_{uuid.uuid4().hex[:8]}"
        
        # 确保ID唯一
        if payload_id in self.payloads:
            raise ValueError(f"物品ID {payload_id} 已存在")
        
        # 创建物品信息
        payload_info = {
            'id': payload_id,
            'create_time': self.env.now,
            **properties
        }
        
        # 存储物品信息
        self.payloads[payload_id] = payload_info
        self.payload_status[payload_id] = self.STATUS_CREATED
        
        # 如果指定了位置，记录位置
        if 'location' in properties:
            self.update_payload_location(payload_id, properties['location'])
        
        # 触发物品创建事件
        self.env.event_registry.trigger_event(
            self.id, 'payload_created',
            {
                'payload_id': payload_id,
                'properties': payload_info,
                'time': self.env.now
            }
        )
        
        print(f"时间 {self.env.now}: 物品 {payload_id} 已创建")
        return payload_id
    
    def register_payload(self, payload_id: str, properties: Dict[str, Any]) -> bool:
        """
        注册已有物品
        
        Args:
            payload_id: 物品ID
            properties: 物品属性
            
        Returns:
            bool: 是否成功注册
        """
        if payload_id in self.payloads:
            print(f"警告: 物品 {payload_id} 已存在，将更新属性")
            self.payloads[payload_id].update(properties)
        else:
            self.payloads[payload_id] = {
                'id': payload_id,
                'create_time': self.env.now,
                **properties
            }
            self.payload_status[payload_id] = self.STATUS_CREATED
        
        # 如果指定了位置，记录位置
        if 'location' in properties:
            self.update_payload_location(payload_id, properties['location'])
        
        return True
    
    def update_payload_location(self, payload_id: str, location: Tuple[float, float, float]) -> bool:
        """
        更新物品位置
        
        Args:
            payload_id: 物品ID
            location: 新位置 (x, y, z)
            
        Returns:
            bool: 是否成功更新
        """
        if payload_id not in self.payloads:
            print(f"警告: 物品 {payload_id} 不存在，无法更新位置")
            return False
        
        old_location = self.payload_locations.get(payload_id)
        self.payload_locations[payload_id] = location
        
        # 如果有空间管理器，更新物品在空间中的位置
        if self.airspace_manager:
            # 使用特殊前缀区分物品和代理
            payload_space_id = f"payload_{payload_id}"
            self.airspace_manager.update_agent_position(payload_space_id, location)
        
        # 触发位置变更事件
        self.env.event_registry.trigger_event(
            self.id, 'payload_location_changed',
            {
                'payload_id': payload_id,
                'old_location': old_location,
                'new_location': location,
                'time': self.env.now
            }
        )
        
        return True
    
    def mark_payload_picked(self, payload_id: str, agent_id: str) -> bool:
        """
        标记物品已被取件
        
        Args:
            payload_id: 物品ID
            agent_id: 取件代理ID
            
        Returns:
            bool: 是否成功标记
        """
        if payload_id not in self.payloads:
            print(f"警告: 物品 {payload_id} 不存在，无法标记为已取件")
            return False
        
        if self.payload_status.get(payload_id) != self.STATUS_CREATED:
            print(f"警告: 物品 {payload_id} 当前状态为 {self.payload_status.get(payload_id)}，不能标记为已取件")
            return False
        
        # 更新状态和携带者
        self.payload_status[payload_id] = self.STATUS_PICKED
        self.payload_carriers[payload_id] = agent_id
        
        # 更新物品信息
        self.payloads[payload_id]['pickup_time'] = self.env.now
        self.payloads[payload_id]['pickup_agent'] = agent_id
        
        # 触发物品取件事件
        self.env.event_registry.trigger_event(
            self.id, 'payload_picked',
            {
                'payload_id': payload_id,
                'agent_id': agent_id,
                'time': self.env.now
            }
        )
        
        print(f"时间 {self.env.now}: 物品 {payload_id} 已被代理 {agent_id} 取件")
        return True
    
    def mark_payload_delivered(self, payload_id: str, location: Tuple[float, float, float]) -> bool:
        """
        标记物品已交付
        
        Args:
            payload_id: 物品ID
            location: 交付位置 (x, y, z)
            
        Returns:
            bool: 是否成功标记
        """
        if payload_id not in self.payloads:
            print(f"警告: 物品 {payload_id} 不存在，无法标记为已交付")
            return False
        
        if self.payload_status.get(payload_id) != self.STATUS_PICKED:
            print(f"警告: 物品 {payload_id} 当前状态为 {self.payload_status.get(payload_id)}，不能标记为已交付")
            return False
        
        # 获取携带者
        carrier_id = self.payload_carriers.get(payload_id)
        
        # 更新状态和位置
        self.payload_status[payload_id] = self.STATUS_DELIVERED
        self.update_payload_location(payload_id, location)
        
        # 移除携带者
        if payload_id in self.payload_carriers:
            del self.payload_carriers[payload_id]
        
        # 更新物品信息
        self.payloads[payload_id]['delivery_time'] = self.env.now
        self.payloads[payload_id]['delivery_location'] = location
        
        # 触发物品交付事件
        self.env.event_registry.trigger_event(
            self.id, 'payload_delivered',
            {
                'payload_id': payload_id,
                'carrier_id': carrier_id,
                'location': location,
                'time': self.env.now
            }
        )
        
        print(f"时间 {self.env.now}: 物品 {payload_id} 已交付到位置 {location}")
        return True
    
    def get_payload(self, payload_id: str) -> Optional[Dict]:
        """
        获取物品信息
        
        Args:
            payload_id: 物品ID
            
        Returns:
            Dict: 物品信息，如果不存在则返回None
        """
        return self.payloads.get(payload_id)
    
    def get_payload_location(self, payload_id: str) -> Optional[Tuple[float, float, float]]:
        """
        获取物品位置
        
        Args:
            payload_id: 物品ID
            
        Returns:
            Tuple[float, float, float]: 物品位置，如果不存在则返回None
        """
        return self.payload_locations.get(payload_id)
    
    def get_payload_status(self, payload_id: str) -> Optional[str]:
        """
        获取物品状态
        
        Args:
            payload_id: 物品ID
            
        Returns:
            str: 物品状态，如果不存在则返回None
        """
        return self.payload_status.get(payload_id)
    
    def get_payload_carrier(self, payload_id: str) -> Optional[str]:
        """
        获取物品携带者
        
        Args:
            payload_id: 物品ID
            
        Returns:
            str: 携带者ID，如果不存在则返回None
        """
        return self.payload_carriers.get(payload_id)
    
    def get_all_payloads(self) -> Dict[str, Dict]:
        """
        获取所有物品
        
        Returns:
            Dict[str, Dict]: 物品ID到物品信息的映射
        """
        return self.payloads.copy()
    
    def get_payloads_by_status(self, status: str) -> Dict[str, Dict]:
        """
        获取指定状态的物品
        
        Args:
            status: 物品状态
            
        Returns:
            Dict[str, Dict]: 物品ID到物品信息的映射
        """
        result = {}
        for payload_id, payload_status in self.payload_status.items():
            if payload_status == status:
                result[payload_id] = self.payloads[payload_id]
        return result
    
    def get_payloads_by_location(self, center: Tuple[float, float, float], radius: float) -> Dict[str, Dict]:
        """
        获取指定范围内的物品
        
        Args:
            center: 中心位置 (x, y, z)
            radius: 半径
            
        Returns:
            Dict[str, Dict]: 物品ID到物品信息的映射
        """
        result = {}
        
        # 如果有空间管理器，使用空间管理器的查询功能
        if self.airspace_manager:
            x, y, z = center
            payload_agents = self.airspace_manager.query_sphere(x, y, z, radius)
            
            # 过滤出物品（以"payload_"开头的ID）
            for agent_id, position in payload_agents.items():
                if agent_id.startswith("payload_"):
                    payload_id = agent_id[8:]  # 去掉"payload_"前缀
                    if payload_id in self.payloads:
                        result[payload_id] = self.payloads[payload_id]
        else:
            # 手动计算距离
            import math
            x, y, z = center
            for payload_id, location in self.payload_locations.items():
                if location:
                    px, py, pz = location
                    distance = math.sqrt((x - px) ** 2 + (y - py) ** 2 + (z - pz) ** 2)
                    if distance <= radius:
                        result[payload_id] = self.payloads[payload_id]
        
        return result
    
    def get_payloads_by_carrier(self, agent_id: str) -> Dict[str, Dict]:
        """
        获取指定代理携带的物品
        
        Args:
            agent_id: 代理ID
            
        Returns:
            Dict[str, Dict]: 物品ID到物品信息的映射
        """
        result = {}
        for payload_id, carrier_id in self.payload_carriers.items():
            if carrier_id == agent_id:
                result[payload_id] = self.payloads[payload_id]
        return result