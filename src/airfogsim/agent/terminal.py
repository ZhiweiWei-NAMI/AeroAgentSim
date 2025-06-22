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
import uuid
from airfogsim.core.agent import Agent, AgentMeta

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
        mcs.register_template(cls, 'computing_status', str, True,
                            lambda s: s in ['idle', 'computing', 'paused', 'completed'],
                            "计算状态")
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
        mcs.register_template(cls, 'transmitting_status', str, True,
                            lambda s: s in ['idle', 'transmitting', 'paused', 'completed'],
                            "传输状态")
        mcs.register_template(cls, 'transmission_progress', float, True,
                            lambda p: 0 <= p <= 1.0,
                            "传输进度 (0.0-1.0)")
        mcs.register_template(cls, 'transmission_speed', float, False, None,
                            "传输速度 (KB/s)")
        # trans_target_agent_id
        mcs.register_template(cls, 'trans_target_agent_id', str, False, None,
                            "传输目标代理ID")

        # 感知相关状态 - 通用
        mcs.register_template(cls, 'sensing_progress', float, True,
                            lambda p: 0 <= p <= 1.0,
                            "感知进度 (0.0-1.0)")
        mcs.register_template(cls, 'sensing_speed', float, False, None,
                            "感知速度 (单位/秒)")

        # 图像感知状态
        mcs.register_template(cls, 'image_sensing_status', str, True,
                            lambda s: s in ['idle', 'sensing', 'paused', 'completed'],
                            "图像感知状态")

        # 电磁感知状态
        mcs.register_template(cls, 'em_sensing_status', str, True,
                            lambda s: s in ['idle', 'sensing', 'paused', 'completed'],
                            "电磁感知状态")

        # 障碍物感知状态
        mcs.register_template(cls, 'object_sensing_status', str, True,
                            lambda s: s in ['idle', 'sensing', 'paused', 'completed'],
                            "障碍物感知状态")

        return cls

class TerminalAgent(Agent, metaclass=TerminalAgentMeta):
    """终端代理，提供计算、通信和数据存储功能"""

    @classmethod
    def get_description(cls):
        """获取代理类型的描述"""
        return "终端代理 - 提供计算、通信和数据存储功能，支持图像处理工作流"

    def __init__(self, env, agent_name: str, properties={}, agent_id=None):
        super().__init__(env, agent_name, properties)
        self.id = agent_id or f"agent_{uuid.uuid4().hex[:8]}"

        # 初始化状态
        self.initialize_states(
            level_class=TerminalAgent,
            position=properties.get('position', [0, 0, 0]),
            power_status=properties.get('power_status', 'on'),
            cpu_usage=properties.get('cpu_usage', 0.0),
            memory_usage=properties.get('memory_usage', 0.0),
            storage_usage=properties.get('storage_usage', 0.0),
            computing_status='idle',
            compute_progress=0.0,
            compute_speed=0.0,
            connection_status=properties.get('connection_status', 'connected'),
            bandwidth_usage=properties.get('bandwidth_usage', 0.0),
            transmitting_status='idle',
            transmission_progress=0.0,
            transmission_speed=0.0,
            sensing_progress=0.0,
            sensing_speed=0.0,
            image_sensing_status='idle',
            em_sensing_status='idle',
            object_sensing_status='idle',
            status='idle'
        )

    def _process_custom_logic(self):
        """执行终端特定的逻辑"""
        # 检查终端电源状态
        if self.get_state('power_status') == 'on':
            # 如果没有活跃的工作流和正在运行的任务，重置任务相关状态
            if not self.get_active_workflows() and not any(task['status'] == 'running' for task in self.managed_tasks.values()):
                self._reset_task_states()

            # 终端特定的逻辑可以在这里添加
            # 例如：监控系统资源、管理文件等
            pass

    def _reset_task_states(self):
        """重置任务相关状态"""
        if not any(task['status'] == 'running' for task in self.managed_tasks.values()):
            # 如果没有正在运行的任务，重置所有任务状态
            self.update_state('computing_status', 'idle')
            self.update_state('compute_progress', 0.0)
            self.update_state('compute_speed', 0.0)
            self.update_state('transmitting_status', 'idle')
            self.update_state('transmission_progress', 0.0)
            self.update_state('transmission_speed', 0.0)
            # 重置所有感知状态
            self.update_state('image_sensing_status', 'idle')  # 图像感知状态
            self.update_state('em_sensing_status', 'idle')  # 电磁感知状态
            self.update_state('object_sensing_status', 'idle')  # 障碍物感知状态
            self.update_state('sensing_progress', 0.0)
            self.update_state('sensing_speed', 0.0)
            self.update_state('status', 'idle')


    def register_event_listeners(self):
        """注册终端需要监听的事件"""
        # 获取基类注册的事件监听器
        listeners = super().register_event_listeners()

        # 添加终端特有的事件监听器
        # 可以根据需要添加更多事件监听器

        return listeners

    def _check_agent_status(self):
        """检查终端状态"""
        # 如果终端处于关机状态，跳过任务处理
        return self.get_state('power_status') == 'on'

    def get_details(self):
        """获取代理详细信息"""
        details = self.get_current_states()
        details.update({
            'id': self.id,
            'name': self.name,
            'type': self.__class__.__name__,
            'components': self.get_component_names(),
            'active_tasks_count': len([t for t in self.managed_tasks.values() if t['status'] == 'running']),
            'active_workflows': [w.id for w in self.get_active_workflows()],
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