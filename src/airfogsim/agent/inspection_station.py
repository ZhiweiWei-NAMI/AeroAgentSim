"""
AirFogSim巡检站代理模块

该模块定义了巡检站代理，负责生成巡检任务并将其分配给无人机。
主要功能包括：
1. 巡检点生成
2. 无人机注册和管理
3. 巡检工作流创建和管理
4. 巡检任务分配

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core import Agent, AgentMeta
from typing import Dict, List, Optional, Tuple, Any
import math
import simpy
from airfogsim.workflow.inspection import InspectionWorkflow
from airfogsim.core.enums import TaskPriority
from airfogsim.core.trigger import TimeTrigger
import random
from airfogsim.utils.logging_config import get_logger

# 获取logger
logger = get_logger(__name__)

class InspectionStationMeta(AgentMeta):
    """巡检站代理元类"""

    def __new__(mcs, name, bases, attrs):
        cls = super().__new__(mcs, name, bases, attrs)

        # 注册巡检站专用的状态模板
        mcs.register_template(cls, 'position', (list, tuple), True,
                          lambda pos: len(pos) == 3 and all(isinstance(c, (int, float)) for c in pos),
                          "巡检站位置坐标 (x, y, z)")

        mcs.register_template(cls, 'service_radius', float, True,
                          lambda radius: radius > 0,
                          "服务半径（米）")

        mcs.register_template(cls, 'registered_inspection_drones', list, False,
                          None,
                          "注册的巡检无人机列表")

        mcs.register_template(cls, 'inspection_areas', list, False,
                          None,
                          "巡检区域列表，每个区域包含多个巡检点")

        mcs.register_template(cls, 'inspection_generation_interval', (int, float), True,
                          lambda interval: interval > 0,
                          "巡检任务生成间隔（秒）")

        return cls

class InspectionStation(Agent, metaclass=InspectionStationMeta):
    """
    巡检站代理

    负责生成巡检任务并将其分配给无人机的固定位置代理，同时维护可用的无人机列表。
    """

    def __init__(self, env, agent_name: str, properties: Optional[Dict] = None):
        """
        初始化巡检站代理

        Args:
            env: 仿真环境
            agent_name: 代理名称
            properties: 代理属性，应包含position、service_radius等
        """
        properties = properties or {}
        super().__init__(env, agent_name, properties)

        # 初始化状态
        self.initialize_states(
            position=properties.get('position', [0, 0, 0]),
            service_radius=properties.get('service_radius', 1000.0),
            registered_inspection_drones=properties.get('registered_inspection_drones', []),
            inspection_areas=properties.get('inspection_areas', []),
            inspection_generation_interval=properties.get('inspection_generation_interval', 300),
            status=properties.get('status', 'idle')
        )

        # 确保事件存在
        self.get_event('inspection_task_generated')
        self.get_event('drone_registered')
        self.get_event('drone_unregistered')
        self.get_event('inspection_workflow_created')

        # 注册到空间管理器
        if hasattr(env, 'airspace_manager'):
            env.airspace_manager.register_agent(self.id, self.get_state('position'))

    @classmethod
    def get_description(cls):
        """获取代理类型的描述"""
        return "巡检站代理 - 负责生成巡检任务并将其分配给无人机"

    def register_drone(self, drone_id: str) -> bool:
        """
        注册无人机到巡检站

        Args:
            drone_id: 无人机ID

        Returns:
            bool: 是否成功注册
        """
        registered_drones = self.get_state('registered_inspection_drones')
        if drone_id in registered_drones:
            logger.info(f"时间 {self.env.now}: 无人机 {drone_id} 已注册到巡检站 {self.id}")
            return False

        # 添加到注册列表
        registered_drones.append(drone_id)
        self.update_state('registered_inspection_drones', registered_drones)

        # 触发无人机注册事件
        self.trigger_event('drone_registered', {
            'drone_id': drone_id,
            'time': self.env.now
        })

        logger.info(f"时间 {self.env.now}: 无人机 {drone_id} 成功注册到巡检站 {self.id}")
        return True

    def unregister_drone(self, drone_id: str) -> bool:
        """
        从巡检站注销无人机

        Args:
            drone_id: 无人机ID

        Returns:
            bool: 是否成功注销
        """
        registered_drones = self.get_state('registered_inspection_drones')
        if drone_id not in registered_drones:
            logger.info(f"时间 {self.env.now}: 无人机 {drone_id} 未注册到巡检站 {self.id}")
            return False

        # 从注册列表移除
        registered_drones.remove(drone_id)
        self.update_state('registered_inspection_drones', registered_drones)

        # 触发无人机注销事件
        self.trigger_event('drone_unregistered', {
            'drone_id': drone_id,
            'time': self.env.now
        })

        logger.info(f"时间 {self.env.now}: 无人机 {drone_id} 已从巡检站 {self.id} 注销")
        return True

    def get_available_drones(self) -> List[str]:
        """
        获取当前可用的无人机列表

        Returns:
            List[str]: 可用的无人机ID列表
        """
        registered_drones = self.get_state('registered_inspection_drones')
        # 这里可以添加更复杂的逻辑来筛选真正可用的无人机
        # 例如检查无人机的当前状态、电量等
        return registered_drones

    def select_drone_for_inspection(self) -> Optional[str]:
        """
        为巡检任务选择一个无人机

        Returns:
            str: 选择的无人机ID，如果没有可用无人机则返回None
        """
        available_drones = self.get_available_drones()
        if not available_drones:
            return None

        # 简单策略：随机选择一个无人机
        # 实际应用中可以根据无人机的状态、位置等因素进行更智能的选择
        return random.choice(available_drones)

    def generate_inspection_points(self, num_points: int = 3, area_index: int = None) -> List[Tuple[float, float, float]]:
        """
        生成巡检点

        Args:
            num_points: 生成的巡检点数量
            area_index: 巡检区域索引，如果为None则随机选择一个区域

        Returns:
            List[Tuple[float, float, float]]: 巡检点列表
        """
        inspection_areas = self.get_state('inspection_areas')

        # 如果没有定义巡检区域，则在服务半径内随机生成
        if not inspection_areas:
            return self._generate_random_inspection_points(num_points)

        # 如果没有指定区域索引，则随机选择一个区域
        if area_index is None:
            area_index = random.randint(0, len(inspection_areas) - 1)

        # 确保区域索引有效
        if area_index < 0 or area_index >= len(inspection_areas):
            logger.info(f"时间 {self.env.now}: 巡检站 {self.id} 指定的区域索引 {area_index} 无效")
            return self._generate_random_inspection_points(num_points)

        # 获取指定区域
        area = inspection_areas[area_index]

        # 如果区域已经定义了巡检点，则直接使用
        if 'inspection_points' in area and area['inspection_points']:
            return area['inspection_points']

        # 否则在区域内随机生成巡检点
        center = area.get('center', self.get_state('position'))
        radius = area.get('radius', self.get_state('service_radius') / 2)
        min_altitude = area.get('min_altitude', 50)
        max_altitude = area.get('max_altitude', 150)

        inspection_points = []
        for _ in range(num_points):
            # 在区域内随机生成坐标
            angle = random.uniform(0, 2 * 3.14159)
            distance = random.uniform(0, radius)
            x = center[0] + distance * math.cos(angle)
            y = center[1] + distance * math.sin(angle)
            z = random.uniform(min_altitude, max_altitude)

            inspection_points.append((x, y, z))

        return inspection_points

    def _generate_random_inspection_points(self, num_points: int) -> List[Tuple[float, float, float]]:
        """
        在服务半径内随机生成巡检点

        Args:
            num_points: 生成的巡检点数量

        Returns:
            List[Tuple[float, float, float]]: 巡检点列表
        """
        station_pos = self.get_state('position')
        service_radius = self.get_state('service_radius')

        inspection_points = []
        for _ in range(num_points):
            # 在服务半径内随机生成坐标
            angle = random.uniform(0, 2 * 3.14159)
            distance = random.uniform(0, service_radius)
            x = station_pos[0] + distance * math.cos(angle)
            y = station_pos[1] + distance * math.sin(angle)
            z = random.uniform(50, 150)  # 高度在50-150米之间

            inspection_points.append((x, y, z))

        return inspection_points

    def create_inspection_workflow(self, drone_id: str, inspection_points: List[Tuple[float, float, float]]) -> Optional[str]:
        """
        创建巡检工作流

        Args:
            drone_id: 无人机ID
            inspection_points: 巡检点列表

        Returns:
            str: 巡检工作流ID，如果创建失败则返回None
        """
        # 获取无人机代理
        drone_agent = self.env.agents.get(drone_id)

        if not drone_agent:
            logger.warning(f"时间 {self.env.now}: 巡检站 {self.id} 找不到无人机 {drone_id}")
            return None

        # 创建巡检工作流
        workflow = self.env.create_workflow(
            InspectionWorkflow,
            name=f"Inspection of {drone_agent.id}",
            owner=drone_agent,
            properties={
                'inspection_points': inspection_points,
                'task_priority': TaskPriority.NORMAL,
                'task_preemptive': False
            },
            start_trigger=TimeTrigger(self.env, trigger_time=self.env.now + 1),
            max_starts=1
        )

        if workflow:
            logger.info(f"时间 {self.env.now}: 巡检站 {self.id} 为无人机 {drone_id} 创建巡检工作流 {workflow.id}")

            # 触发巡检工作流创建事件
            self.trigger_event('inspection_workflow_created', {
                'workflow_id': workflow.id,
                'drone_id': drone_id,
                'inspection_points': inspection_points,
                'time': self.env.now
            })

            return workflow.id

        return None

    def generate_inspection_task(self) -> Optional[str]:
        """
        生成巡检任务

        Returns:
            str: 巡检工作流ID，如果创建失败则返回None
        """
        # 选择无人机
        drone_id = self.select_drone_for_inspection()
        if not drone_id:
            logger.warning(f"时间 {self.env.now}: 巡检站 {self.id} 没有可用的无人机")
            return None

        # 生成巡检点
        inspection_points = self.generate_inspection_points()

        # 创建巡检工作流
        workflow_id = self.create_inspection_workflow(drone_id, inspection_points)

        if workflow_id:
            # 触发巡检任务生成事件
            self.trigger_event('inspection_task_generated', {
                'workflow_id': workflow_id,
                'drone_id': drone_id,
                'inspection_points': inspection_points,
                'time': self.env.now
            })

            return workflow_id

        return None

    def register_event_listeners(self):
        """注册巡检站需要监听的事件"""
        # 获取基类注册的事件监听器
        listeners = super().register_event_listeners()

        # 添加巡检站特有的事件监听器
        # 可以根据需要添加更多事件监听器

        return listeners

    def _process_custom_logic(self):
        """执行巡检站特定的逻辑"""
        # 调用父类的处理逻辑
        super()._process_custom_logic()

        # 如果还没有启动定期生成巡检任务的进程，则启动它
        if not hasattr(self, '_inspection_process_started') or not self._inspection_process_started:
            self.env.process(self._periodic_inspection_task())
            self._inspection_process_started = True

    def _periodic_inspection_task(self):
        """定期生成巡检任务"""
        # 初始等待
        yield self.env.timeout(10)

        while True:
            try:
                # 生成巡检任务
                workflow_id = self.generate_inspection_task()

                if workflow_id:
                    logger.info(f"时间 {self.env.now}: 巡检站 {self.id} 生成巡检任务 {workflow_id}")
                else:
                    logger.info(f"时间 {self.env.now}: 巡检站 {self.id} 生成巡检任务失败")

                # 等待下一次生成
                interval = self.get_state('inspection_generation_interval')
                yield self.env.timeout(interval)

            except simpy.Interrupt:
                # 处理中断
                break
            except Exception as e:
                logger.error(f"Error: 巡检站 {self.id} 生成巡检任务时出错: {str(e)}")
                # 短暂等待后继续
                yield self.env.timeout(30)
