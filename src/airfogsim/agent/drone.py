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
import uuid
from airfogsim.agent.terminal import TerminalAgent, TerminalAgentMeta
from airfogsim.workflow.charging import ChargingWorkflow
from airfogsim.core.llm_client import LLMClient
from airfogsim.utils.logging_config import get_logger
logger = get_logger(__name__)

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
        # 无人机移动状态
        mcs.register_template(cls, 'moving_status', str, True,
                            lambda s: s in ['idle', 'flying', 'landing', 'hovering'],
                            "无人机当前移动状态")
        mcs.register_template(cls, 'direction', tuple, False,
                            lambda pos: len(pos) == 3 and all(isinstance(x, (int, float)) for x in pos),
                            "无人机的方向向量 (dx, dy, dz)")
        mcs.register_template(cls, 'distance_traveled', float, False, None,
                            "无人机已经行进的距离 (km)")
        mcs.register_template(cls, 'computation_load', float, False, None,
                            "无人机的计算负载")
        mcs.register_template(cls, 'altitude', (int, float), False, None,
                            "无人机的高度 (m)")
        # max_allowed_speed
        mcs.register_template(cls, 'max_allowed_speed', float, False, None,
                            "无人机允许的最大速度 (km/h)")
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
        self.id = agent_id or f"agent_{uuid.uuid4().hex[:8]}"
        self.initialize_states(
            level_class=DroneAgent,
            position=properties.get('position', [0, 0, 0]),
            battery_level=properties.get('battery_level', 100.0),
            status=properties.get('status', 'idle'),
            moving_status=properties.get('moving_status', 'idle'),
            direction=properties.get('direction', (0, 0, 0)),
            distance_traveled=properties.get('distance_traveled', 0.0),
            computation_load=properties.get('computation_load', 0.0),
            altitude=properties.get('altitude', 0.0),
            battery_capacity=properties.get('battery_capacity', 5000.0),
            charge_cycles=properties.get('charge_cycles', 0),
            external_force=properties.get('external_force', (0, 0, 0))
        )

        # 初始化 LLM 客户端，传入环境实例
        self.llm_client = None

    def register_event_listeners(self):
        """注册无人机需要监听的事件"""
        # 获取基类注册的事件监听器
        listeners = super().register_event_listeners()

        # 添加无人机特有的事件监听器
        # 可以根据需要添加更多事件监听器

        return listeners

    def _check_agent_status(self):
        """检查无人机状态"""
        # 检查电量并返回状态
        return self._check_battery_level()

    def _process_custom_logic(self):
        """执行无人机特定的逻辑"""
        # 获取当前活跃的工作流
        active_workflows = self.get_active_workflows()
        if not active_workflows:
            # 如果没有活跃的工作流，则简单地保持空闲状态
            self.update_state('moving_status', 'idle')
            self.update_state('status', 'idle')
            return

        # 检查是否有充电工作流
        for workflow in active_workflows:
            if isinstance(workflow, ChargingWorkflow):
                # 如果当前在充电，更新无人机状态
                if workflow.status_machine.state == 'charging':
                    self.update_state('moving_status', 'idle')
                    self.update_state('status', 'active')
                break

        # 无人机特定的逻辑，如使用LLM进行任务规划等
        # 这里可以添加更多无人机特有的功能



    def _check_battery_level(self):
        """检查电池电量并决定是否需要终止当前任务"""
        battery_level = self.get_state('battery_level')
        status = self.get_state('status')
        if status == 'error':
            return False

        # 如果电量极低(小于5%)，打印警告并取消所有任务
        if battery_level < 5.0:
            logger.warning(f"时间 {self.env.now}: 警告! {self.id} 电量极低({battery_level:.1f}%)，取消所有任务!")
            self._cancel_all_tasks()
            self.update_state('moving_status', 'idle')
            self.update_state('status', 'error')
            return False
        elif battery_level < 10.0:
            logger.warning(f"时间 {self.env.now}: 注意! {self.id} 电量低({battery_level:.1f}%)，应尽快充电!")
            return True
        return True

    def get_details(self):
        """获取代理详细信息"""
        details = self.get_current_states()
        details.update({
            'id': self.id,
            'name': self.name,
            'type': self.__class__.__name__,
            'components': self.get_component_names(),
            'active_tasks_count': len([t for t in self.managed_tasks.values() if t['status'] == 'running']),
            'active_workflows': [w.id for w in self.get_active_workflows()]
        })
        return details