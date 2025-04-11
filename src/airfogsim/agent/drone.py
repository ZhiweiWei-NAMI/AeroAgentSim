"""
AirFogSim无人机代理模块

该模块定义了无人机代理类及其元类，实现了智能无人机的行为和状态管理。
主要功能包括：
1. 无人机状态模板定义和管理
2. 智能任务规划和执行
3. 电池管理和充电逻辑
4. LLM集成的决策支持
5. 工作流监控和管理

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.agent.terminal import TerminalAgent, TerminalAgentMeta
from airfogsim.core import Workflow, WorkflowStatus
from airfogsim.workflow.inspection import InspectionWorkflow
from airfogsim.workflow.charging import ChargingWorkflow
from airfogsim.task.mobility import MoveToTask
from airfogsim.task.compute import FileComputeTask
from airfogsim.task.charging import ChargingTask
from airfogsim.core.enums import TaskStatus

class DroneAgentMeta(TerminalAgentMeta):
    """无人机代理元类"""
    
    def __new__(mcs, name, bases, attrs):
        cls = super().__new__(mcs, name, bases, attrs)
        
        # 注册无人机专用的状态模板
        mcs.register_template(cls, 'position', (tuple, list), True, 
                            lambda pos: len(pos) == 3 and all(isinstance(x, (int, float)) for x in pos),
                            "无人机的3D位置坐标 (x, y, z)")
        mcs.register_template(cls, 'speed', float, False, None,
                            "无人机的速度 (km/h)")
        mcs.register_template(cls, 'battery_level', (float, int), True,
                            lambda lvl: 0 <= lvl <= 100,
                            "无人机电池电量百分比 (0-100)")
        mcs.register_template(cls, 'status', str, True,
                            lambda s: s in ['idle', 'flying', 'landing', 'charging', 'error', 'waiting_to_charge'],
                            "无人机当前状态")
        mcs.register_template(cls, 'direction', tuple, False,
                            lambda pos: len(pos) == 3 and all(isinstance(x, (int, float)) for x in pos),
                            "无人机的方向向量 (dx, dy, dz)")
        mcs.register_template(cls, 'distance_traveled', float, False, None,
                            "无人机已经行进的距离 (km)")
        mcs.register_template(cls, 'computation_load', float, False, None,
                            "无人机的计算负载")
        mcs.register_template(cls, 'altitude', (int, float), False, None,
                            "无人机的高度 (m)")
        # battery_capacity
        mcs.register_template(cls, 'battery_capacity', float, False, None,
                            "无人机电池容量 (mAh)")
        # charge_cycles
        mcs.register_template(cls, 'charge_cycles', int, False, None,
                            "无人机充电周期")
        # external_force
        mcs.register_template(cls, 'external_force', (tuple, list), False, 
                              lambda v: len(v) == 3 and all(isinstance(i, (int, float)) for i in v),
                            "无人机受外力影响的3D向量 (fx, fy, fz)")
        
        return cls

class DroneAgent(TerminalAgent, metaclass=DroneAgentMeta):
    """无人机代理，能够执行智能任务规划"""
    
    @classmethod
    def get_description(cls):
        """获取代理类型的描述"""
        return "无人机代理 - 能够执行智能任务规划，支持巡检和充电工作流"
    
    def __init__(self, env, agent_name: str, properties=None, agent_id=None):
        super().__init__(env, agent_name, properties)
        self.id = agent_id or f"agent_{id(self)}"
        self.initialize_states(
            level_class=DroneAgent,
            position=properties.get('position', [0, 0, 0]),
            battery_level=properties.get('battery_level', 100.0),
            status='idle',
            speed=0.0,
        )

    def _on_visual_update(self, event_data):
        """响应环境的视觉更新事件"""
        # 在仿真环境的可视化更新周期中执行任务规划
        self.env.process(self._plan_and_execute_tasks())

    def _parse_llm_response(self, response):
        """解析LLM响应并转换为任务列表"""
        tasks = []
        # 策略1: 假设LLM返回JSON格式的任务列表
        # print(response)
        try:
            import json
            import re
            
            # 提取可能的JSON部分
            json_match = re.search(r'```json\n(.*?)\n```', response, re.DOTALL)
            if json_match:
                json_content = json_match.group(1)
            else:
                json_content = response
                
            task_data = json.loads(json_content)
            if isinstance(task_data, list):
                for task in task_data:
                    if self._validate_task_format(task):
                        tasks.append(task)
        except Exception as e:
            print(f"解析LLM响应失败: {str(e)}")
        return tasks
    
    def _validate_task_format(self, task):
        """验证任务格式是否正确"""
        required_fields = ['component', 'task_name', 'workflow_id', 'target_state','task_class','properties']
        return all(field in task for field in required_fields)

    def _analyze_workflow_with_llm(self, workflow):
        """使用OpenAI分析工作流并生成任务"""
        if not self.llm_client:
            return None
        
        # 构建提示，包含工作流状态机信息
        prompt = f"""
            Suggest tasks directly without analysis:

            Workflow Name: {workflow.name}
            Current State: {self.get_current_states()}
            Current Workflow State: {workflow.status_machine.state}
            Current Workflow Details: {workflow.get_details()}
            Possible Next States: {[t[3] for t in workflow.status_machine._get_current_transitions()]}
            Available Task Class: 'MoveToTask' with init doc: {MoveToTask.__init__.__doc__},
            Available Components: {self.get_component_names()}

            Return a JSON array of tasks following this format:
            [
            {{
                "component": "ComponentName",
                "task_class": "TaskClassName", 
                "task_name": "Human readable task name",
                "workflow_id": "{workflow.id}",
                "target_state": {{"position": [x, y, z]}},  # Target drone state
                "properties": {{
                "key1": "value1", 
                "key2": "value2"
                }}
            }}
            ]
            """
        
        try:
            # print(prompt)
            # 使用OpenAI新版客户端API
            response = self.llm_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a drone task planner assistant."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=1000
            )
            # 提取回复内容 - 新API的响应格式与旧版不同
            response_text = response.choices[0].message.content
            tasks = self._parse_llm_response(response_text)
            for task in tasks:
                task['workflow_id'] = workflow.id
            return tasks
        except Exception as e:
            print(f"LLM分析失败: {str(e)}")
            return None

    def live(self):
        """无人机的主要行为逻辑，集成LLM进行智能任务规划"""
        while True:
            # 使用event_registry获取事件而不是直接创建
            workflow_event = self.env.event_registry.get_event('workflow_assigned', self.id)
            visual_update_event = self.env.event_registry.get_event(self.env.id, 'visual_update')
            
            # 等待任一事件触发
            events = yield self.env.any_of([workflow_event, visual_update_event])
            
            # 检查电量并根据需要更新状态
            self._check_battery_level()
            
            # 如果没有活跃任务，将状态设为空闲
            if not any(task['status'] == 'running' for task in self.managed_tasks.values()):
                self.update_state('status', 'idle')

    def _plan_and_execute_tasks(self):
        """规划并执行任务"""
        # 获取当前活跃的工作流
        yield self.env.timeout(0)  # 让出控制权，确保这是一个生成器函数
        active_workflows = self._get_active_workflows()
        if not active_workflows:
            # 如果没有活跃的工作流，则简单地保持空闲状态
            if self.get_state('status') != 'charging':  # 如果不在充电，则设置为空闲
                self.update_state('status', 'idle')
            return

        charging_workflow = None
        charging_needed = False
        
        # 首先检查是否有充电工作流, 且是否需要优先处理
        for workflow in active_workflows:
            if isinstance(workflow, ChargingWorkflow):
                charging_workflow = workflow
                # 如果是在seeking_charger或charging状态，则优先处理充电
                if workflow.status_machine.state in ['seeking_charger', 'charging']:
                    charging_needed = True
                break
        
        tasks_to_execute = []
        
        # 如果需要优先处理充电
        if charging_needed and charging_workflow:
            # 取消所有非充电任务
            self._cancel_non_charging_tasks()
            
            # 获取充电工作流的建议任务
            suggested_task = charging_workflow.get_current_suggested_task()
            if suggested_task:
                # 如果有建议任务，补充当前位置等动态属性
                tasks_to_execute.append(suggested_task)
            
            # 如果当前在充电，更新无人机状态
            if charging_workflow.status_machine.state == 'charging':
                self.update_state('status', 'charging')
        else:
            # 正常处理所有工作流
            for workflow in active_workflows:
                # 使用统一的方法获取建议任务
                suggested_task = workflow.get_current_suggested_task()
                if suggested_task:
                    tasks_to_execute.append(suggested_task)
        
        # 执行规划的任务
        if tasks_to_execute:
            self._execute_planned_tasks(tasks_to_execute)

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
            target_state = task_info['target_state']
            properties = task_info['properties']
            task_id = task_info.get('id', None)
            
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

            if not already_executing and component.is_available():
                self.execute_task(
                    component_name, 
                    task_name,
                    task_class=task_class,
                    workflow_id=workflow_id,
                    target_state=target_state,
                    properties=properties,
                    task_id = task_id
                )

    def _cancel_non_charging_tasks(self):
        """取消所有非充电相关的任务"""
        tasks_to_cancel = []
        for task_id, task_info in self.managed_tasks.items():
            # 只取消正在运行的非充电任务
            if (task_info['status'] == 'running' and 
                not (task_info['component'] == 'Charging' or 
                    (task_info['component'] == 'MoveTo' and '充电' in task_info['task_name'].lower()))):
                tasks_to_cancel.append(task_id)
                
        for task_id in tasks_to_cancel:
            print(f"时间 {self.env.now}: {self.id} 因充电需要取消任务: {self.managed_tasks[task_id]['task_name']}")
            self.cancel_task(task_id)
            
        return len(tasks_to_cancel)

    def _check_battery_level(self):
        """检查电池电量并决定是否需要终止当前任务"""
        battery_level = self.get_state('battery_level')
        
        # 如果电量极低(小于5%)，打印警告并取消所有任务
        if battery_level < 5.0:
            print(f"时间 {self.env.now}: 警告! {self.id} 电量极低({battery_level:.1f}%)，取消所有任务!")
            self._cancel_all_tasks()
            self.update_state('status', 'error')
        elif battery_level < 10.0:
            print(f"时间 {self.env.now}: 注意! {self.id} 电量低({battery_level:.1f}%)，应尽快充电!")

    def get_details(self):
        """获取代理详细信息"""
        details = self.get_current_states()
        details.update({
            'id': self.id,
            'name': self.name,
            'type': self.__class__.__name__,
            'components': self.get_component_names(),
            'active_tasks_count': len([t for t in self.managed_tasks.values() if t['status'] == 'running']),
            'active_workflows': [w.id for w in self._get_active_workflows()]
        })
        return details