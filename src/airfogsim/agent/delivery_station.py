"""
AirFogSim快递站代理模块

该模块定义了快递站代理，负责生成和接收物品，以及维护可用的无人机列表。
主要功能包括：
1. 物品生成
2. 物品接收
3. 无人机注册和管理
4. 订单工作流创建和管理

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core import Agent, AgentMeta
from typing import Dict, List, Optional, Tuple, Any
import uuid
import simpy
import random

class DeliveryStationMeta(AgentMeta):
    """快递站代理元类"""
    
    def __new__(mcs, name, bases, attrs):
        cls = super().__new__(mcs, name, bases, attrs)
        
        # 注册快递站专用的状态模板
        mcs.register_template(cls, 'position', (list, tuple), True,
                          lambda pos: len(pos) == 3 and all(isinstance(c, (int, float)) for c in pos),
                          "快递站位置坐标 (x, y, z)")
        
        mcs.register_template(cls, 'storage_capacity', int, True,
                          lambda cap: cap > 0,
                          "快递站存储容量")
        
        mcs.register_template(cls, 'current_storage', int, True,
                          lambda storage: storage >= 0,
                          "当前存储的物品数量")
        
        mcs.register_template(cls, 'service_radius', float, True,
                          lambda radius: radius > 0,
                          "服务半径（米）")
        
        mcs.register_template(cls, 'registered_logistics_drones', list, False,
                          None,
                          "注册的物流无人机列表")
        
        mcs.register_template(cls, 'payload_generation_model', dict, False,
                          None,
                          "物品生成模型配置")
        
        return cls

class DeliveryStation(Agent, metaclass=DeliveryStationMeta):
    """
    快递站代理
    
    负责生成和接收物品的固定位置代理，同时维护可用的无人机列表。
    """
    
    def __init__(self, env, agent_name: str, properties: Optional[Dict] = None):
        """
        初始化快递站代理
        
        Args:
            env: 仿真环境
            agent_name: 代理名称
            properties: 代理属性，应包含position、storage_capacity、service_radius等
        """
        properties = properties or {}
        super().__init__(env, agent_name, properties)
        
        # 初始化状态
        self.initialize_states(
            position=properties.get('position', [0, 0, 0]),
            storage_capacity=properties.get('storage_capacity', 100),
            current_storage=properties.get('current_storage', 0),
            service_radius=properties.get('service_radius', 50.0),
            registered_logistics_drones=properties.get('registered_logistics_drones', []),
            payload_generation_model=properties.get('payload_generation_model', {
                'interval_mean': 300,  # 平均300秒生成一个物品
                'interval_std': 60,    # 标准差60秒
                'destinations': [],    # 目的地列表，为空时随机生成
                'properties': {}       # 物品属性模板
            })
        )
        
        # 注册事件
        self.register_event('payload_generated')
        self.register_event('payload_received')
        self.register_event('drone_registered')
        self.register_event('drone_unregistered')
        self.register_event('order_created')
        
        # 注册到空间管理器
        if hasattr(env, 'airspace_manager'):
            env.airspace_manager.register_agent(self.id, self.get_state('position'))
        
    
    @classmethod
    def get_description(cls):
        """获取代理类型的描述"""
        return "快递站代理 - 负责生成和接收物品，维护无人机列表"
    
    def register_drone(self, drone_id: str) -> bool:
        """
        注册无人机到快递站
        
        Args:
            drone_id: 无人机ID
            
        Returns:
            bool: 是否成功注册
        """
        registered_drones = self.get_state('registered_logistics_drones')
        if drone_id in registered_drones:
            print(f"时间 {self.env.now}: 无人机 {drone_id} 已注册到快递站 {self.id}")
            return False
        
        # 添加到注册列表
        registered_drones.append(drone_id)
        self.update_state('registered_logistics_drones', registered_drones)
        
        # 触发无人机注册事件
        self.trigger_event('drone_registered', {
            'drone_id': drone_id,
            'time': self.env.now
        })
        
        print(f"时间 {self.env.now}: 无人机 {drone_id} 成功注册到快递站 {self.id}")
        return True
    
    def unregister_drone(self, drone_id: str) -> bool:
        """
        从快递站注销无人机
        
        Args:
            drone_id: 无人机ID
            
        Returns:
            bool: 是否成功注销
        """
        registered_drones = self.get_state('registered_logistics_drones')
        if drone_id not in registered_drones:
            print(f"时间 {self.env.now}: 无人机 {drone_id} 未注册到快递站 {self.id}")
            return False
        
        # 从注册列表移除
        registered_drones.remove(drone_id)
        self.update_state('registered_logistics_drones', registered_drones)
        
        # 触发无人机注销事件
        self.trigger_event('drone_unregistered', {
            'drone_id': drone_id,
            'time': self.env.now
        })
        
        print(f"时间 {self.env.now}: 无人机 {drone_id} 已从快递站 {self.id} 注销")
        return True
    
    def get_available_drones(self) -> List[str]:
        """
        获取当前可用的无人机列表
        
        Returns:
            List[str]: 可用的无人机ID列表
        """
        registered_drones = self.get_state('registered_logistics_drones')
        # 这里可以添加更复杂的逻辑来筛选真正可用的无人机
        # 例如检查无人机的当前状态、电量等
        return registered_drones
    
    def select_drone_for_delivery(self) -> Optional[str]:
        """
        为配送任务选择一个无人机
        
        Returns:
            str: 选择的无人机ID，如果没有可用无人机则返回None
        """
        available_drones = self.get_available_drones()
        if not available_drones:
            return None
        
        # 简单策略：随机选择一个无人机
        # 实际应用中可以根据无人机的状态、位置等因素进行更智能的选择
        return random.choice(available_drones)
    
    def create_payload(self, payload_properties: Dict) -> Dict:
        """
        创建物品
        
        Args:
            payload_properties: 物品属性
            
        Returns:
            Dict: 创建的物品信息
        """
        # 检查快递站是否有足够的存储空间
        current_storage = self.get_state('current_storage')
        storage_capacity = self.get_state('storage_capacity')
        
        if current_storage >= storage_capacity:
            print(f"时间 {self.env.now}: 快递站 {self.id} 存储空间已满，无法创建物品")
            return None
        
        # 生成物品ID
        payload_id = f"payload_{uuid.uuid4().hex[:8]}"
        
        # 创建物品信息
        payload_info = {
            'id': payload_id,
            'properties': payload_properties,
            'creation_time': self.env.now,
            'status': 'created'
        }
        
        # 将物品添加到代理的possessing_objects中;由于物品ID是唯一的，所以可以直接使用
        self.add_possessing_object(payload_id, payload_info)
        
        # 更新快递站的当前存储量
        self.update_state('current_storage', current_storage + 1)
        
        print(f"时间 {self.env.now}: 快递站 {self.id} 创建物品 {payload_id}")
        
        # 触发物品生成事件
        self.trigger_event('payload_generated', {
            'payload_id': payload_id,
            'properties': payload_properties,
            'time': self.env.now
        })
        
        return payload_info
    
    def create_logistics_workflow(self, drone_id: str, payload_id: str, delivery_location: Tuple[float, float, float], target_agent_id: Optional[str]) -> Optional[str]:
        """
        创建物流工作流
        
        Args:
            drone_id: 无人机ID
            payload_id: 物品ID
            delivery_location: 交付位置
            target_agent_id: 目标代理ID
            
        Returns:
            str: 物流工作流ID，如果创建失败则返回None
        """
        # 获取物品信息
        payload_info = self.get_possessing_object(payload_id)
        if not payload_info or payload_info.get('id') != payload_id:
            print(f"时间 {self.env.now}: 快递站 {self.id} 找不到物品 {payload_id}")
            return None
        
        # 获取无人机代理
        drone_agent = self.env.agents.get(drone_id)
        
        if not drone_agent:
            print(f"时间 {self.env.now}: 快递站 {self.id} 找不到无人机 {drone_id}")
            return None
        
        # 获取快递站位置作为取件点
        pickup_location = self.get_state('position')
        
        # 创建物流工作流
        from airfogsim.workflow.logistics import create_logistics_workflow
        
        # 准备物品列表
        payloads = [{'id': payload_id, **payload_info.get('properties', {})}]
        
        # 创建物流工作流
        workflow = create_logistics_workflow(
            self.env,
            drone_agent,
            pickup_location,
            delivery_location,
            payloads,
            self.id,
            target_agent_id
        )
        
        if workflow:
            print(f"时间 {self.env.now}: 快递站 {self.id} 为无人机 {drone_id} 创建物流工作流 {workflow.id}")
            return workflow.id
        
        return None
    
    def create_order(self, delivery_location: Tuple[float, float, float],
                    payload_properties: Optional[Dict] = None,
                    target_agent_id: Optional[str] = None) -> Optional[str]:
        """
        创建订单并启动订单执行工作流
        
        Args:
            delivery_location: 交付位置坐标
            payload_properties: 物品属性
            target_agent_id: 目标代理ID，如果为None则尝试根据位置查找
            
        Returns:
            str: 订单工作流ID，如果创建失败则返回None
        """
        from airfogsim.workflow.order_execution import create_order_execution_workflow
        
        # 合并默认物品属性和指定的物品属性
        default_properties = self.get_state('payload_generation_model').get('properties', {})
        payload_properties = {**default_properties, **(payload_properties or {})}
        
        # 如果没有指定目标代理ID，尝试根据位置查找
        if not target_agent_id:
            nearest_agents = self.env.airspace_manager.get_k_nearest_agents(
                self.id, k=1
            )
            if nearest_agents:
                target_agent_id = nearest_agents[0].id
        
        # 创建订单执行工作流
        workflow = create_order_execution_workflow(
            self.env,
            self,
            payload_properties,
            delivery_location,
            target_agent_id
        )
        
        if workflow:
            # 触发订单创建事件
            self.trigger_event('order_created', {
                'workflow_id': workflow.id,
                'delivery_location': delivery_location,
                'payload_properties': payload_properties,
                'time': self.env.now
            })
            
            print(f"时间 {self.env.now}: 快递站 {self.id} 创建订单 {workflow.id}，交付位置: {delivery_location}")
            return workflow.id
        
        return None
    
    def _generate_random_destination(self) -> Tuple[float, float, float]:
        """
        生成随机目的地坐标
        
        Returns:
            Tuple[float, float, float]: 随机坐标
        """
        station_pos = self.get_state('position')
        service_radius = self.get_state('service_radius')
        
        # 在服务半径内随机生成坐标
        x = station_pos[0] + random.uniform(-service_radius, service_radius)
        y = station_pos[1] + random.uniform(-service_radius, service_radius)
        z = max(0, station_pos[2] + random.uniform(-10, 10))  # 高度不应过低
        
        return (x, y, z)
    
    def _handle_creating_payload_state(self, workflow):
        """
        处理创建物品状态
        
        Args:
            workflow: 订单执行工作流
        """
        # 创建物品
        payload_properties = workflow.payload_properties
        payload_info = self.create_payload(payload_properties)
        
        # 确保生成的payload_info满足order workflow的监听条件
        if payload_info:
            # 将workflow.payload_id设置为实际生成的payload_id
            workflow.payload_id = payload_info['id']
    
    def _handle_assigning_drone_state(self, workflow):
        """
        处理分配无人机状态
        
        Args:
            workflow: 订单执行工作流
        """
        # 选择无人机
        drone_id = self.select_drone_for_delivery()
        if not drone_id:
            print(f"时间 {self.env.now}: 快递站 {self.id} 没有可用的无人机")
            return
        
        # 创建物流工作流
        logistics_workflow_id = self.create_logistics_workflow(
            drone_id,
            workflow.payload_id,
            workflow.delivery_location,
            workflow.target_agent_id
        )
        
        # 更新工作流的物流工作流ID和目标代理ID
        if logistics_workflow_id:
            workflow.logistics_workflow_id = logistics_workflow_id
            workflow.assigned_drone = drone_id
    
    def live(self):
        """
        代理的主要行为循环
        
        快递站主要响应工作流的任务请求，并维护无人机列表。
        """
        while True:
            try:
                # 获取活跃的工作流
                active_workflows = self._get_active_workflows()
                
                # 处理每个活跃工作流
                for workflow in active_workflows:
                    # 只处理订单执行工作流
                    if workflow.__class__.__name__ == 'OrderExecutionWorkflow':
                        # 根据工作流状态执行对应的处理逻辑
                        current_state = workflow.status_machine.state
                        
                        if current_state == 'creating_payload':
                            self._handle_creating_payload_state(workflow)
                        elif current_state == 'assigning_drone':
                            self._handle_assigning_drone_state(workflow)
                
                # 短暂等待后再次检查
                yield self.env.timeout(5)
                
            except simpy.Interrupt:
                # 处理中断
                break
            except Exception as e:
                print(f"时间 {self.env.now}: 快递站 {self.id} 处理工作流时出错: {str(e)}")
                # 短暂等待后继续
                yield self.env.timeout(30)