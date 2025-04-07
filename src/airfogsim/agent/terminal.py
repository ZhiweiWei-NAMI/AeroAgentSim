"""
AirFogSim终端代理模块

该模块定义了终端代理类及其元类，实现了固定终端的行为和状态管理。
主要功能包括：
1. 终端状态模板定义和管理
2. 计算资源管理
3. 通信能力管理
4. 数据存储和处理
5. 工作流监控和管理

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core.agent import Agent, AgentMeta
from airfogsim.core import Workflow, WorkflowStatus
from airfogsim.core.enums import TaskStatus
from typing import Dict, Any, List, Optional

class TerminalAgentMeta(AgentMeta):
    """终端代理元类"""
    
    def __new__(mcs, name, bases, attrs):
        cls = super().__new__(mcs, name, bases, attrs)
        
        # 注册终端专用的状态模板
        mcs.register_template(cls, 'position', (tuple, list), True, 
                            lambda pos: len(pos) == 3 and all(isinstance(x, (int, float)) for x in pos),
                            "终端的3D位置坐标 (x, y, z)")
        
        # 电源状态
        mcs.register_template(cls, 'power_status', str, True,
                            lambda s: s in ['on', 'off', 'standby', 'error'],
                            "终端电源状态")
        
        # 计算相关状态
        mcs.register_template(cls, 'cpu_usage', float, True,
                            lambda u: 0 <= u <= 100,
                            "CPU使用率百分比 (0-100)")
        mcs.register_template(cls, 'memory_usage', float, True,
                            lambda u: 0 <= u <= 100,
                            "内存使用率百分比 (0-100)")
        mcs.register_template(cls, 'storage_usage', float, True,
                            lambda u: 0 <= u <= 100,
                            "存储使用率百分比 (0-100)")
        mcs.register_template(cls, 'is_computing', bool, True, None,
                            "是否正在进行计算")
        mcs.register_template(cls, 'compute_progress', float, True,
                            lambda p: 0 <= p <= 1.0,
                            "计算进度 (0.0-1.0)")
        mcs.register_template(cls, 'compute_speed', float, False, None,
                            "计算速度 (单位/秒)")
        
        # 通信相关状态
        mcs.register_template(cls, 'connection_status', str, True,
                            lambda s: s in ['connected', 'disconnected', 'limited', 'error'],
                            "网络连接状态")
        mcs.register_template(cls, 'bandwidth_usage', float, True,
                            lambda u: 0 <= u <= 100,
                            "带宽使用率百分比 (0-100)")
        mcs.register_template(cls, 'is_transmitting', bool, True, None,
                            "是否正在传输数据")
        mcs.register_template(cls, 'transmission_progress', float, True,
                            lambda p: 0 <= p <= 1.0,
                            "传输进度 (0.0-1.0)")
        mcs.register_template(cls, 'transmission_speed', float, False, None,
                            "传输速度 (KB/s)")
        
        # 感知相关状态
        mcs.register_template(cls, 'is_sensing', bool, True, None,
                            "是否正在进行感知")
        mcs.register_template(cls, 'sensing_progress', float, True,
                            lambda p: 0 <= p <= 1.0,
                            "感知进度 (0.0-1.0)")
        mcs.register_template(cls, 'sensing_speed', float, False, None,
                            "感知速度 (单位/秒)")
        
        return cls

class TerminalAgent(Agent, metaclass=TerminalAgentMeta):
    """终端代理，提供计算、通信和数据存储功能"""
    
    @classmethod
    def get_description(cls):
        """获取代理类型的描述"""
        return "终端代理 - 提供计算、通信和数据存储功能，支持图像处理工作流"
    
    def __init__(self, env, agent_name: str, properties=None, agent_id=None):
        super().__init__(env, agent_name, properties)
        self.id = agent_id or f"agent_{id(self)}"
        
        # 初始化状态
        self.initialize_states(
            level_class=TerminalAgent,
            position=properties.get('position', [0, 0, 0]),
            power_status=properties.get('power_status', 'on'),
            cpu_usage=properties.get('cpu_usage', 0.0),
            memory_usage=properties.get('memory_usage', 0.0),
            storage_usage=properties.get('storage_usage', 0.0),
            is_computing=False,
            compute_progress=0.0,
            compute_speed=0.0,
            connection_status=properties.get('connection_status', 'connected'),
            bandwidth_usage=properties.get('bandwidth_usage', 0.0),
            is_transmitting=False,
            transmission_progress=0.0,
            transmission_speed=0.0,
            is_sensing=False,
            sensing_progress=0.0,
            sensing_speed=0.0
        )
        
        # 订阅环境的视觉更新事件
        self.env.event_registry.subscribe(
            self.env.id,
            'visual_update',
            self.id,
            self._on_visual_update
        )
        
        # 存储管理的文件
        self.managed_files = {}
    
    def _on_visual_update(self, event_data):
        """响应环境的视觉更新事件"""
        # 在仿真环境的可视化更新周期中执行任务规划
        self.env.process(self._plan_and_execute_tasks())
    
    def _plan_and_execute_tasks(self):
        """规划并执行任务"""
        # 获取当前活跃的工作流
        yield self.env.timeout(0)  # 让出控制权，确保这是一个生成器函数
        active_workflows = self._get_active_workflows()
        if not active_workflows:
            # 如果没有活跃的工作流，则简单地保持空闲状态
            if self.get_state('power_status') == 'on':  # 如果终端已开机
                # 重置任务相关状态
                self._reset_task_states()
            return
        
        tasks_to_execute = []
        
        # 处理所有工作流
        for workflow in active_workflows:
            # 使用统一的方法获取建议任务
            suggested_task = workflow.get_current_suggested_task()
            if suggested_task:
                tasks_to_execute.append(suggested_task)
        
        # 执行规划的任务
        if tasks_to_execute:
            self._execute_planned_tasks(tasks_to_execute)
    
    def _reset_task_states(self):
        """重置任务相关状态"""
        if not any(task['status'] == 'running' for task in self.managed_tasks.values()):
            # 如果没有正在运行的任务，重置所有任务状态
            self.update_state('is_computing', False)
            self.update_state('compute_progress', 0.0)
            self.update_state('compute_speed', 0.0)
            self.update_state('is_transmitting', False)
            self.update_state('transmission_progress', 0.0)
            self.update_state('transmission_speed', 0.0)
            self.update_state('is_sensing', False)
            self.update_state('sensing_progress', 0.0)
            self.update_state('sensing_speed', 0.0)
    
    def _execute_planned_tasks(self, tasks_to_execute):
        """执行已规划的任务"""
        if not tasks_to_execute:
            return
        
        # 执行每个任务
        for task_info in tasks_to_execute:
            component_name = task_info['component']
            task_class = task_info['task_class']
            task_name = task_info['task_name']
            workflow_id = task_info['workflow_id']
            target_state = task_info.get('target_state', {})
            properties = task_info['properties']
            
            # 检查是否已有相同任务在执行中
            already_executing = False
            for task in self.managed_tasks.values():
                if (task['status'] == 'running' and 
                    task['component'] == component_name and 
                    task['task'].__class__.__name__ == task_class and
                    task['task'].workflow_id == workflow_id):
                    already_executing = True
                    break
            
            component = self.get_component(component_name)
            
            if not already_executing and component and component.is_available():
                self.execute_task(
                    component_name, 
                    task_name,
                    task_class=task_class,
                    workflow_id=workflow_id,
                    target_state=target_state,
                    properties=properties
                )
    
    def live(self):
        """终端的主要行为逻辑"""
        while True:
            # 使用event_registry获取事件
            workflow_event = self.env.event_registry.get_event('workflow_assigned', self.id)
            visual_update_event = self.env.event_registry.get_event(self.env.id, 'visual_update')
            
            # 等待任一事件触发
            events = yield self.env.any_of([workflow_event, visual_update_event])
            
            # 如果终端处于关机状态，跳过任务处理
            if self.get_state('power_status') != 'on':
                continue
            
    def get_details(self):
        """获取代理详细信息"""
        details = self.get_current_states()
        details.update({
            'id': self.id,
            'name': self.name,
            'type': self.__class__.__name__,
            'components': self.get_component_names(),
            'active_tasks_count': len([t for t in self.managed_tasks.values() if t['status'] == 'running']),
            'active_workflows': [w.id for w in self._get_active_workflows()],
            'managed_files_count': len(self.managed_files)
        })
        return details
        
    def power_on(self):
        """开启终端"""
        self.update_state('power_status', 'on')
    
    def power_off(self):
        """关闭终端"""
        # 取消所有正在运行的任务
        self._cancel_all_tasks()
        self.update_state('power_status', 'off')
        # 重置所有资源使用状态
        self.update_state('cpu_usage', 0.0)
        self.update_state('memory_usage', 0.0)
        self.update_state('bandwidth_usage', 0.0)
        self._reset_task_states()
    
    def standby(self):
        """终端进入待机模式"""
        self.update_state('power_status', 'standby')
        # 降低资源使用率
        self.update_state('cpu_usage', 1.0)
        self.update_state('memory_usage', 5.0)
        self.update_state('bandwidth_usage', 0.5)