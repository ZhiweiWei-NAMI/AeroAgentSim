import asyncio
import logging
import json
import uuid
from typing import Dict, List, Any, Optional, Callable, Set
from datetime import datetime

from .data_service import SimulationDataService

logger = logging.getLogger(__name__)

class SimulationIntegration:
    """仿真集成层，连接仿真环境和API层"""
    
    def __init__(self, data_service: SimulationDataService):
        self.data_service = data_service
        self.callbacks: List[Callable] = []
        self.simulation_status = "STOPPED"
        self.simulation_time = 0.0
        self.simulation_speed = 1.0
        self.active_drones: Set[str] = set()
        self.active_workflows: Set[str] = set()
        self.active_agents: Set[str] = set()
        
        # 模拟环境引用，在实际集成中会连接到真实的仿真环境
        self.simulation_env = None
        
        # 创建一个事件循环用于模拟仿真更新
        self.event_loop_task = None
    
    def register_callback(self, callback: Callable):
        """注册回调函数，用于接收仿真更新"""
        if callback not in self.callbacks:
            self.callbacks.append(callback)
            logger.info(f"已注册新的回调函数，当前回调数量: {len(self.callbacks)}")
    
    def unregister_callback(self, callback: Callable):
        """取消注册回调函数"""
        if callback in self.callbacks:
            self.callbacks.remove(callback)
            logger.info(f"已取消注册回调函数，当前回调数量: {len(self.callbacks)}")
    
    async def _notify_callbacks(self, update_data: Dict[str, Any]):
        """通知所有回调函数"""
        for callback in self.callbacks:
            try:
                await callback(update_data)
            except Exception as e:
                logger.error(f"执行回调函数时出错: {str(e)}")
    
    async def start_simulation(self):
        """启动仿真"""
        if self.simulation_status == "RUNNING":
            logger.warning("仿真已经在运行中")
            return
        
        logger.info("启动仿真...")
        self.simulation_status = "RUNNING"
        
        # 通知状态变化
        await self._notify_callbacks({
            "type": "sim_status",
            "status": self.simulation_status,
            "time": self.simulation_time,
            "speed": self.simulation_speed
        })
        
        # 记录事件
        await self._notify_callbacks({
            "type": "sim_event",
            "time": self.simulation_time,
            "source": "SimulationSystem",
            "message": "仿真已启动",
            "level": "success"
        })
        
        # 启动事件循环
        if self.event_loop_task is None or self.event_loop_task.done():
            self.event_loop_task = asyncio.create_task(self._simulation_event_loop())
    
    async def pause_simulation(self):
        """暂停仿真"""
        if self.simulation_status != "RUNNING":
            logger.warning("仿真未在运行，无法暂停")
            return
        
        logger.info("暂停仿真...")
        self.simulation_status = "PAUSED"
        
        # 通知状态变化
        await self._notify_callbacks({
            "type": "sim_status",
            "status": self.simulation_status,
            "time": self.simulation_time,
            "speed": self.simulation_speed
        })
        
        # 记录事件
        await self._notify_callbacks({
            "type": "sim_event",
            "time": self.simulation_time,
            "source": "SimulationSystem",
            "message": "仿真已暂停",
            "level": "info"
        })
    
    async def resume_simulation(self):
        """恢复仿真"""
        if self.simulation_status != "PAUSED":
            logger.warning("仿真未暂停，无法恢复")
            return
        
        logger.info("恢复仿真...")
        self.simulation_status = "RUNNING"
        
        # 通知状态变化
        await self._notify_callbacks({
            "type": "sim_status",
            "status": self.simulation_status,
            "time": self.simulation_time,
            "speed": self.simulation_speed
        })
        
        # 记录事件
        await self._notify_callbacks({
            "type": "sim_event",
            "time": self.simulation_time,
            "source": "SimulationSystem",
            "message": "仿真已恢复",
            "level": "info"
        })
    
    async def reset_simulation(self):
        """重置仿真"""
        logger.info("重置仿真...")
        
        # 停止事件循环
        if self.event_loop_task and not self.event_loop_task.done():
            self.event_loop_task.cancel()
            try:
                await self.event_loop_task
            except asyncio.CancelledError:
                pass
        
        self.simulation_status = "STOPPED"
        self.simulation_time = 0.0
        
        # 清空活动实体
        self.active_drones.clear()
        self.active_workflows.clear()
        self.active_agents.clear()
        
        # 通知状态变化
        await self._notify_callbacks({
            "type": "sim_status",
            "status": self.simulation_status,
            "time": self.simulation_time,
            "speed": self.simulation_speed
        })
        
        # 记录事件
        await self._notify_callbacks({
            "type": "sim_event",
            "time": self.simulation_time,
            "source": "SimulationSystem",
            "message": "仿真已重置",
            "level": "info"
        })
    
    async def set_simulation_speed(self, speed: float):
        """设置仿真速度"""
        if speed <= 0:
            logger.warning("仿真速度必须大于0")
            return
        
        logger.info(f"设置仿真速度为 {speed}x")
        self.simulation_speed = speed
        
        # 通知状态变化
        await self._notify_callbacks({
            "type": "sim_status",
            "status": self.simulation_status,
            "time": self.simulation_time,
            "speed": self.simulation_speed
        })
    
    async def _simulation_event_loop(self):
        """模拟仿真事件循环"""
        logger.info("启动仿真事件循环")
        
        # 初始化一些模拟无人机
        if not self.active_drones:
            await self._initialize_demo_entities()
        
        try:
            while self.simulation_status == "RUNNING":
                # 更新仿真时间
                self.simulation_time += 0.1 * self.simulation_speed
                
                # 更新无人机状态
                await self._update_drone_states()
                
                # 检查工作流状态
                await self._check_workflow_states()
                
                # 通知状态变化
                await self._notify_callbacks({
                    "type": "sim_status",
                    "status": self.simulation_status,
                    "time": self.simulation_time,
                    "speed": self.simulation_speed
                })
                
                # 随机生成事件
                if self.simulation_time % 10 < 0.1 * self.simulation_speed:
                    await self._generate_random_event()
                
                # 等待一小段时间
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            logger.info("仿真事件循环已取消")
            raise
        except Exception as e:
            logger.error(f"仿真事件循环出错: {str(e)}")
            # 记录错误事件
            await self._notify_callbacks({
                "type": "sim_event",
                "time": self.simulation_time,
                "source": "SimulationSystem",
                "message": f"仿真出错: {str(e)}",
                "level": "error"
            })
            self.simulation_status = "PAUSED"
            await self._notify_callbacks({
                "type": "sim_status",
                "status": self.simulation_status,
                "time": self.simulation_time,
                "speed": self.simulation_speed
            })
    
    async def _initialize_demo_entities(self):
        """初始化演示实体"""
        # 创建几个演示无人机
        drone_ids = ["drone_1", "drone_2", "drone_3"]
        
        for i, drone_id in enumerate(drone_ids):
            self.active_drones.add(drone_id)
            
            # 初始位置
            position = [100 * (i + 1), 100, 50]
            
            # 更新无人机状态
            self.data_service.update_drone_state(
                drone_id=drone_id,
                position=position,
                battery_level=100.0,
                status="idle",
                speed=0.0,
                sim_time=self.simulation_time
            )
            
            # 记录事件
            await self._notify_callbacks({
                "type": "sim_event",
                "time": self.simulation_time,
                "source": f"Drone-{drone_id}",
                "message": f"无人机 {drone_id} 已初始化",
                "level": "info"
            })
        
        # 创建几个演示智能体
        agent_ids = ["agent_1", "agent_2"]
        agent_types = ["ground_station", "edge_server"]
        
        for i, (agent_id, agent_type) in enumerate(zip(agent_ids, agent_types)):
            self.active_agents.add(agent_id)
            
            # 更新智能体状态
            self.data_service.update_agent(
                agent_id=agent_id,
                name=f"{agent_type.capitalize()} {i+1}",
                type_=agent_type,
                position=[200 * (i + 1), 0, 0] if agent_type == "ground_station" else None,
                properties={"status": "active", "capacity": 100}
            )
            
            # 记录事件
            await self._notify_callbacks({
                "type": "sim_event",
                "time": self.simulation_time,
                "source": f"Agent-{agent_id}",
                "message": f"智能体 {agent_id} ({agent_type}) 已初始化",
                "level": "info"
            })
    
    async def _update_drone_states(self):
        """更新无人机状态"""
        for drone_id in self.active_drones:
            # 获取当前状态
            current_drone = self.data_service.get_drone(drone_id)
            if not current_drone:
                continue
            
            # 模拟状态变化
            position = json.loads(current_drone['position']) if isinstance(current_drone['position'], str) else current_drone['position']
            battery_level = current_drone['battery_level']
            status = current_drone['status']
            
            # 根据状态更新位置
            if status == "moving":
                # 简单的移动逻辑
                position[0] += (5 * self.simulation_speed) * (0.5 - (self.simulation_time % 10) / 10)
                position[1] += (3 * self.simulation_speed) * ((self.simulation_time % 15) / 15 - 0.5)
                position[2] = max(10, position[2] + (2 * self.simulation_speed) * ((self.simulation_time % 20) / 20 - 0.5))
                
                # 消耗电池
                battery_level = max(0, battery_level - 0.05 * self.simulation_speed)
            elif status == "idle":
                # 小幅度随机移动
                position[0] += (0.5 * self.simulation_speed) * ((self.simulation_time % 5) / 5 - 0.5)
                position[1] += (0.5 * self.simulation_speed) * ((self.simulation_time % 7) / 7 - 0.5)
                
                # 缓慢消耗电池
                battery_level = max(0, battery_level - 0.01 * self.simulation_speed)
            elif status == "charging":
                # 充电
                battery_level = min(100, battery_level + 0.5 * self.simulation_speed)
                
                # 如果充满电，切换到空闲状态
                if battery_level >= 99.9:
                    status = "idle"
                    
                    # 记录事件
                    await self._notify_callbacks({
                        "type": "sim_event",
                        "time": self.simulation_time,
                        "source": f"Drone-{drone_id}",
                        "message": "充电完成，电池已充满",
                        "level": "success"
                    })
            
            # 如果电池电量低，切换到充电状态
            if battery_level < 20 and status != "charging":
                status = "charging"
                
                # 记录事件
                await self._notify_callbacks({
                    "type": "sim_event",
                    "time": self.simulation_time,
                    "source": f"Drone-{drone_id}",
                    "message": "电池电量低，开始充电",
                    "level": "warning"
                })
            
            # 随机切换状态
            if self.simulation_time % 30 < 0.1 * self.simulation_speed and status == "idle":
                status = "moving"
                
                # 记录事件
                await self._notify_callbacks({
                    "type": "sim_event",
                    "time": self.simulation_time,
                    "source": f"Drone-{drone_id}",
                    "message": "开始移动任务",
                    "level": "info"
                })
            elif self.simulation_time % 20 < 0.1 * self.simulation_speed and status == "moving":
                status = "idle"
                
                # 记录事件
                await self._notify_callbacks({
                    "type": "sim_event",
                    "time": self.simulation_time,
                    "source": f"Drone-{drone_id}",
                    "message": "移动任务完成，切换到空闲状态",
                    "level": "success"
                })
            
            # 更新无人机状态
            self.data_service.update_drone_state(
                drone_id=drone_id,
                position=position,
                battery_level=battery_level,
                status=status,
                speed=5.0 if status == "moving" else 0.0,
                sim_time=self.simulation_time
            )
    
    async def _check_workflow_states(self):
        """检查工作流状态"""
        # 这里可以添加工作流状态检查逻辑
        pass
    
    async def _generate_random_event(self):
        """生成随机事件"""
        if not self.active_drones:
            return
        
        # 随机选择一个无人机
        import random
        drone_id = random.choice(list(self.active_drones))
        
        # 随机事件类型
        event_types = [
            {"message": "检测到障碍物，调整路径", "level": "warning"},
            {"message": "完成区域扫描", "level": "success"},
            {"message": "数据传输完成", "level": "info"},
            {"message": "信号强度波动", "level": "info"},
            {"message": "执行自检程序", "level": "info"}
        ]
        
        event = random.choice(event_types)
        
        # 记录事件
        await self._notify_callbacks({
            "type": "sim_event",
            "time": self.simulation_time,
            "source": f"Drone-{drone_id}",
            "message": event["message"],
            "level": event["level"]
        })
    
    async def create_workflow(self, name: str, type_: str, parameters: Dict[str, Any], agent_id: Optional[str] = None) -> str:
        """创建新工作流"""
        workflow_id = f"workflow_{uuid.uuid4().hex[:8]}"
        
        # 更新工作流状态
        self.data_service.update_workflow(
            workflow_id=workflow_id,
            name=name,
            type_=type_,
            agent_id=agent_id,
            status="pending",
            details=parameters
        )
        
        # 记录事件
        await self._notify_callbacks({
            "type": "sim_event",
            "time": self.simulation_time,
            "source": "WorkflowManager",
            "message": f"创建新工作流: {name} (ID: {workflow_id})",
            "level": "info"
        })
        
        return workflow_id
    
    async def delete_workflow(self, workflow_id: str) -> bool:
        """删除工作流"""
        success = self.data_service.delete_workflow(workflow_id)
        
        if success:
            if workflow_id in self.active_workflows:
                self.active_workflows.remove(workflow_id)
            
            # 记录事件
            await self._notify_callbacks({
                "type": "sim_event",
                "time": self.simulation_time,
                "source": "WorkflowManager",
                "message": f"删除工作流 (ID: {workflow_id})",
                "level": "info"
            })
        
        return success
    
    async def create_agent(self, name: str, type_: str, initial_position: List[float] = None,
                          initial_battery: float = 100.0, components: List[str] = None,
                          properties: Dict[str, Any] = None) -> str:
        """创建新智能体"""
        agent_id = f"agent_{uuid.uuid4().hex[:8]}"
        
        # 更新智能体状态
        self.data_service.update_agent(
            agent_id=agent_id,
            name=name,
            type_=type_,
            position=initial_position,
            properties={
                "battery_level": initial_battery,
                "components": components or [],
                **(properties or {})
            }
        )
        
        self.active_agents.add(agent_id)
        
        # 如果是无人机类型，添加到活动无人机列表
        if type_ == "drone":
            self.active_drones.add(agent_id)
            
            # 更新无人机状态
            self.data_service.update_drone_state(
                drone_id=agent_id,
                position=initial_position or [0, 0, 0],
                battery_level=initial_battery,
                status="idle",
                speed=0.0,
                sim_time=self.simulation_time
            )
        
        # 记录事件
        await self._notify_callbacks({
            "type": "sim_event",
            "time": self.simulation_time,
            "source": "AgentManager",
            "message": f"创建新智能体: {name} (ID: {agent_id}, 类型: {type_})",
            "level": "info"
        })
        
        return agent_id
    
    async def start_heartbeat(self):
        """启动WebSocket心跳"""
        while True:
            await asyncio.sleep(30)  # 每30秒发送一次心跳
            if self.callbacks:
                await self._notify_callbacks({
                    "type": "heartbeat",
                    "time": self.simulation_time
                })

    async def delete_agent(self, agent_id: str) -> bool:
        """删除智能体"""
        # 检查是否是无人机
        is_drone = agent_id in self.active_drones
        
        # 删除智能体
        success = self.data_service.delete_agent(agent_id)
        
        if success:
            if agent_id in self.active_agents:
                self.active_agents.remove(agent_id)
            
            if is_drone and agent_id in self.active_drones:
                self.active_drones.remove(agent_id)
            
            # 记录事件
            await self._notify_callbacks({
                "type": "sim_event",
                "time": self.simulation_time,
                "source": "AgentManager",
                "message": f"删除智能体 (ID: {agent_id})",
                "level": "info"
            })
        
        return success