"""
AirFogSim物流无人机代理模块

该模块定义了物流无人机代理类及其元类，扩展了基础无人机代理，实现了物流任务的行为和状态管理。
主要功能包括：
1. 物流无人机状态模板定义和管理
2. 货物携带状态管理
3. 物流任务执行逻辑
4. 物流工作流集成

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.agent.drone import DroneAgent, DroneAgentMeta
from airfogsim.workflow.logistics import LogisticsWorkflow
from typing import Dict, Any, List

class DeliveryDroneAgentMeta(DroneAgentMeta):
    """物流无人机代理元类"""
    
    def __new__(mcs, name, bases, attrs):
        cls = super().__new__(mcs, name, bases, attrs)
        
        # 注册物流无人机专用的状态模板
        mcs.register_template(cls, 'carrying_payload', bool, True, None,
                            "无人机是否携带货物")
        mcs.register_template(cls, 'payload_id', str, False, None,
                            "无人机当前携带的货物ID")
        mcs.register_template(cls, 'payload_weight', (float, int), False, None,
                            "无人机当前携带的货物重量 (kg)")
        mcs.register_template(cls, 'payload_dimensions', (tuple, list), False, 
                            lambda dims: len(dims) == 3 and all(isinstance(d, (int, float)) for d in dims),
                            "无人机当前携带的货物尺寸 (长, 宽, 高)")
        
        # 扩展状态列表，直接定义新的验证函数
        extended_states = ['idle', 'flying', 'landing', 'charging', 'error', 'waiting_to_charge',
                          'picking_up', 'delivering', 'transporting','delivery_completed','pickup_completed']
        
        # 创建新的验证函数
        def extended_status_validator(s):
            return s in extended_states
        
        # 更新状态模板
        mcs.register_template(cls, 'status', str, True,
                            extended_status_validator,
                            f"物流无人机当前状态，可选值: {', '.join(extended_states)}")
        
        return cls

class DeliveryDroneAgent(DroneAgent, metaclass=DeliveryDroneAgentMeta):
    """物流无人机代理，能够执行物流任务和工作流"""
    
    @classmethod
    def get_description(cls):
        """获取代理类型的描述"""
        return "物流无人机代理 - 能够执行物流任务，支持取件、运输和交付工作流"
    
    def __init__(self, env, agent_name: str, properties=None, agent_id=None):
        super().__init__(env, agent_name, properties, agent_id)
        self.id = agent_id or f"delivery_drone_{id(self)}"
        
        # 初始化物流相关状态
        self.update_state('carrying_payload', False)
        
        # 订阅物流相关事件
        self.env.event_registry.subscribe(
            self.id,
            'possessing_object_added',
            self.id,
            self._on_payload_added
        )
        
        self.env.event_registry.subscribe(
            self.id,
            'possessing_object_removed',
            self.id,
            self._on_payload_removed
        )
    
    def _on_payload_added(self, event_data):
        """响应货物添加事件"""
        if event_data.get('object_type') == 'payload':
            payload_id = event_data.get('object_id')
            payload = self.get_possessing_object('payload', payload_id)
            
            if payload:
                # 更新携带货物状态
                self.update_state('carrying_payload', True)
                self.update_state('payload_id', payload_id)
                
                # 如果有货物重量信息，更新重量状态
                if 'weight' in payload:
                    self.update_state('payload_weight', payload['weight'])
                
                # 如果有货物尺寸信息，更新尺寸状态
                if 'dimensions' in payload:
                    self.update_state('payload_dimensions', payload['dimensions'])
                
                # 更新状态为运输中
                self.update_state('status', 'transporting')
                
                print(f"时间 {self.env.now}: {self.id} 开始携带货物 {payload_id}")
    
    def _on_payload_removed(self, event_data):
        """响应货物移除事件"""
        if event_data.get('object_type') == 'payload':
            # 更新携带货物状态
            self.update_state('carrying_payload', False)
            self.update_state('payload_id', None)
            self.update_state('payload_weight', None)
            self.update_state('payload_dimensions', None)
            
            # 如果当前状态是delivering，更新为idle
            if self.get_state('status') == 'delivering':
                self.update_state('status', 'idle')
                
            print(f"时间 {self.env.now}: {self.id} 不再携带货物 {event_data.get('object_id')}")
    
    def _plan_and_execute_tasks(self):
        """重写任务规划和执行方法，优先处理物流工作流"""
        # 让出控制权，确保这是一个生成器函数
        yield self.env.timeout(0)
        
        # 获取当前活跃的工作流
        active_workflows = self._get_active_workflows()
        if not active_workflows:
            # 如果没有活跃的工作流，则简单地保持空闲状态
            if self.get_state('status') not in ['charging', 'transporting', 'delivering', 
                                                'picking_up', 'pickup_completed', 'delivery_completed']:
                self.update_state('status', 'idle')
            return
        
        # 优先级排序：充电 > 物流 > 其他
        charging_workflow = None
        logistics_workflow = None
        other_workflows = []
        
        for workflow in active_workflows:
            if isinstance(workflow, LogisticsWorkflow):
                logistics_workflow = workflow
            elif workflow.__class__.__name__ == 'ChargingWorkflow':
                charging_workflow = workflow
            else:
                other_workflows.append(workflow)
        
        tasks_to_execute = []
        
        # 检查是否需要优先处理充电
        charging_needed = False
        if charging_workflow and charging_workflow.status_machine.state in ['seeking_charger', 'charging']:
            charging_needed = True
        
        # 如果需要优先处理充电
        if charging_needed and charging_workflow:
            # 取消所有非充电任务
            self._cancel_non_charging_tasks()
            
            # 获取充电工作流的建议任务
            suggested_task = charging_workflow.get_current_suggested_task()
            if suggested_task:
                tasks_to_execute.append(suggested_task)
            
            # 如果当前在充电，更新无人机状态
            if charging_workflow.status_machine.state == 'charging':
                self.update_state('status', 'charging')
        
        # 如果不需要优先充电，处理物流工作流
        elif logistics_workflow:
            # 获取物流工作流的建议任务
            suggested_task = logistics_workflow.get_current_suggested_task()
            if suggested_task:
                tasks_to_execute.append(suggested_task)
            
            # 根据物流工作流状态更新无人机状态
            current_state = logistics_workflow.status_machine.state
            if current_state == 'picking_up':
                self.update_state('status', 'picking_up')
            elif current_state == 'transporting':
                self.update_state('status', 'transporting')
            elif current_state == 'delivering':
                self.update_state('status', 'delivering')
        
        # 处理其他工作流
        else:
            for workflow in other_workflows:
                suggested_task = workflow.get_current_suggested_task()
                if suggested_task:
                    tasks_to_execute.append(suggested_task)
        
        # 执行规划的任务
        if tasks_to_execute:
            self._execute_planned_tasks(tasks_to_execute)
    
    def get_details(self) -> Dict:
        """获取代理详细信息，添加物流相关信息"""
        details = super().get_details()
        
        # 添加物流相关信息
        if self.get_state('carrying_payload'):
            details['payload_info'] = {
                'id': self.get_state('payload_id'),
                'weight': self.get_state('payload_weight'),
                'dimensions': self.get_state('payload_dimensions')
            }
        
        # 添加物流工作流信息
        logistics_workflows = []
        for workflow in self._get_active_workflows():
            if isinstance(workflow, LogisticsWorkflow):
                logistics_workflows.append({
                    'id': workflow.id,
                    'name': workflow.name,
                    'state': workflow.status_machine.state,
                    'pickup_location': workflow.pickup_location,
                    'delivery_location': workflow.delivery_location
                })
        
        if logistics_workflows:
            details['logistics_workflows'] = logistics_workflows
        
        return details