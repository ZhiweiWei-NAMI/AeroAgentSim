import asyncio
import json
import uuid
import threading
import time
from typing import Dict, List, Any, Optional, Set

from .data_service import SimulationDataService
from .simulation_manager import SimulationManager
from .update_service import UpdateService
from .setup import create_agent_from_config, create_workflow_from_config
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class RealSimulationIntegration:
    """实际仿真集成层，连接仿真环境和API层，并协调各个模块"""
    
    def __init__(self, data_service: SimulationDataService):
        """初始化仿真集成层
        
        Args:
            data_service: 数据服务实例
        """
        self.data_service = data_service
        
        # 初始化配置
        self.config = {
            "airspaces": [],
            "frequencies": [],
            "landing_spots": [],
            "agents": [],
            "workflows": []
        }
        
        # 先创建仿真管理器，以便更新服务可以引用它
        self.simulation_manager = SimulationManager(None, self.config)  # 暂时传递None，后面会设置
        
        # 创建更新服务，并传入仿真管理器
        self.update_service = UpdateService(data_service, self.simulation_manager)
        
        # 设置更新服务到仿真管理器
        self.simulation_manager.update_service = self.update_service
        
        # 设置事件记录回调
        self.simulation_manager.set_event_logger(self._event_logger)
        
        # 活动实体集合（仅用于API层访问）
        self.active_drones = self.update_service.active_drones
        self.active_vehicles = self.update_service.active_vehicles
        self.active_workflows = {}
        self.active_agents = self.update_service.active_agents
        
    @property
    def simulation_status(self) -> str:
        """获取仿真状态"""
        return self.simulation_manager.simulation_status
        
    @property
    def simulation_time(self) -> float:
        """获取仿真时间"""
        return self.simulation_manager.simulation_time
        
    @property
    def simulation_speed(self) -> float:
        """获取仿真速度"""
        return self.simulation_manager.simulation_speed
    
    def _event_logger(self, event_data):
        """事件日志记录器，将事件添加到更新队列"""
        # 确保消息字段存在
        if 'message' not in event_data:
            event_data['message'] = f'{event_data.get("event", "unknown")}:{event_data.get("value", "")}'
        
        # 确保级别字段存在
        if 'level' not in event_data:
            event_data['level'] = 'info'
        
        # 添加到更新队列
        self.update_service.add_update(event_data)
    
    def get_updates(self, max_items: int = 10) -> List[Dict[str, Any]]:
        """从队列中获取更新，API层调用此方法
        
        Args:
            max_items: 最大获取项目数
            
        Returns:
            List[Dict[str, Any]]: 更新数据列表
        """
        return self.update_service.get_updates(max_items)
    
    async def configure_environment(self, config: Dict[str, Any]):
        """配置仿真环境
        
        Args:
            config: 环境配置
            
        Returns:
            Dict: 配置结果
        """
        self.config = config
        
        # 如果环境已存在且仿真正在运行，需要先重置
        if self.simulation_status != "STOPPED":
            await self.reset_simulation()
        
        # 通知配置变更
        self.update_service.add_update({
            "type": "sim_event",
            "time": self.simulation_time,
            "source": "SimulationSystem",
            "message": f"仿真环境已配置，包含{len(config.get('agents', []))}个智能体，{len(config.get('workflows', []))}个工作流",
            "level": "info"
        })
        
        # 更新仿真管理器的配置
        self.simulation_manager.config = config
        
        return {"status": "success", "message": "环境配置已更新"}
    
    async def add_agent(self, agent_config: Dict[str, Any]) -> str:
        """添加智能体到仿真
        
        Args:
            agent_config: 智能体配置
            
        Returns:
            str: 智能体ID，失败则返回None
        """
        agent_id = agent_config.get("id") or f"agent_{uuid.uuid4().hex[:8]}"
        
        # 如果仿真未启动，存储配置以便稍后创建
        if self.simulation_status == "STOPPED":
            self.config.setdefault("agents", []).append(agent_config)
            
            # 更新agents表，确保在前端的智能体列表中显示
            self.data_service.update_agent(
                agent_id=agent_id,
                name=agent_config.get("name", f"智能体{agent_id}"),
                type_=agent_config.get("type", "drone"),
                position=agent_config.get("position", (10, 10, 0)),
                properties={
                    "battery": agent_config.get("battery", 100),
                    "components": agent_config.get("components", []),
                    **agent_config.get("properties", {})
                }
            )
            
            # 如果是drone类型，还需更新drone_states表
            if agent_config.get("type") == "drone":
                self.data_service.update_drone_state(
                    drone_id=agent_id,
                    position=agent_config.get("position", (10, 10, 0)),
                    battery_level=agent_config.get("battery", 100),
                    status="idle",
                    speed=0.0,
                    sim_time=self.simulation_time
                )
                self.active_drones.add(agent_id)
            
            # 通知添加成功
            self.update_service.add_update({
                "type": "sim_event",
                "time": self.simulation_time,
                "source": "AgentManager",
                "message": f"智能体 {agent_id} 已配置等待仿真启动",
                "level": "info"
            })
            return agent_id
        
        # 如果环境已存在且仿真未运行，直接添加智能体
        env = getattr(self.simulation_manager, 'env', None)
        if env and self.simulation_status != "RUNNING":
            agent = create_agent_from_config(
                env, 
                agent_config, 
                self._event_logger, 
                self.data_service
            )
            if agent:
                # 注册到更新服务
                is_drone = agent_config.get("type") == "drone"
                self.update_service.register_agent(agent_id, agent, is_drone)
                
                # 通知添加成功
                self.update_service.add_update({
                    "type": "sim_event",
                    "time": self.simulation_time,
                    "source": "AgentManager",
                    "message": f"智能体 {agent_id} 已添加到仿真",
                    "level": "info"
                })
                return agent_id
        
        # 通知无法添加
        self.update_service.add_update({
            "type": "sim_event",
            "time": self.simulation_time,
            "source": "AgentManager",
            "message": f"无法添加智能体 {agent_id}：仿真正在运行",
            "level": "error"
        })
        return None
    
    async def add_workflow(self, workflow_config: Dict[str, Any]) -> str:
        """添加工作流到仿真
        
        Args:
            workflow_config: 工作流配置
            
        Returns:
            str: 工作流ID，失败则返回None
        """
        workflow_id = workflow_config.get("id") or f"workflow_{uuid.uuid4().hex[:8]}"
        agent_id = workflow_config.get("agent_id")
        
        if not agent_id:
            self.update_service.add_update({
                "type": "sim_event",
                "time": self.simulation_time,
                "source": "WorkflowManager",
                "message": "无法创建工作流：缺少agent_id",
                "level": "error"
            })
            return None
        
        # 如果仿真未启动，存储配置以便稍后创建
        if self.simulation_status == "STOPPED":
            self.config.setdefault("workflows", []).append(workflow_config)
            
            # 更新数据服务，用于前端显示
            self.data_service.update_workflow(
                workflow_id=workflow_id,
                name=workflow_config.get("name", "未命名工作流"),
                type_=workflow_config.get("type", "unknown"),
                agent_id=agent_id,
                status="pending",
                details=workflow_config.get("details", {})
            )
            
            # 将事件放入队列
            self.update_service.add_update({
                "type": "sim_event",
                "time": self.simulation_time,
                "source": "WorkflowManager",
                "message": f"工作流 {workflow_id} 已配置等待仿真启动",
                "level": "info"
            })
            return workflow_id
        
        # 如果环境已存在且仿真未运行，直接添加工作流
        env = getattr(self.simulation_manager, 'env', None)
        if env and self.simulation_status != "RUNNING":
            agent = self.active_agents.get(agent_id)
            if agent:
                workflow = create_workflow_from_config(
                    env, 
                    workflow_config, 
                    agent, 
                    self._event_logger, 
                    self.data_service
                )
                if workflow:
                    # 记录工作流
                    self.active_workflows[workflow_id] = workflow
                    
                    # 通知添加成功
                    self.update_service.add_update({
                        "type": "sim_event",
                        "time": self.simulation_time,
                        "source": "WorkflowManager",
                        "message": f"工作流 {workflow_id} 已添加到仿真",
                        "level": "info"
                    })
                    return workflow_id
        
        # 将失败事件放入队列
        self.update_service.add_update({
            "type": "sim_event",
            "time": self.simulation_time,
            "source": "WorkflowManager",
            "message": f"无法添加工作流 {workflow_id}：仿真正在运行或找不到关联智能体",
            "level": "error"
        })
        return None
    
    async def start_simulation(self):
        """启动仿真"""
        # 直接调用仿真管理器方法，状态通过属性自动更新
        result = self.simulation_manager.start_simulation()
        
        # 如果状态为RUNNING，启动前端更新器
        if self.simulation_status == "RUNNING":
            self.update_service.start_frontend_updater()
        
        return result
    
    async def pause_simulation(self):
        """暂停仿真"""
        # 直接调用仿真管理器方法，状态通过属性自动更新
        result = self.simulation_manager.pause_simulation()
        
        return result
    
    async def resume_simulation(self):
        """恢复仿真，实际上调用start_simulation"""
        return await self.start_simulation()
    
    async def reset_simulation(self):
        """重置仿真"""
        # 停止前端更新器
        self.update_service.stop_frontend_updater()
        
        # 重置仿真管理器
        self.simulation_manager.reset_simulation()
        
        # 仿真管理器已经更新状态为STOPPED，无需再次设置
        
        # 清空活动实体
        self.active_workflows.clear()
        
        # 记录状态变更
        logger.info("仿真已重置")
        
        return {"status": "success", "message": "仿真已重置"}
    
    async def set_simulation_speed(self, speed: float):
        """设置仿真速度
        
        Args:
            speed: 仿真速度
        """
        if speed <= 0:
            logger.warning("仿真速度必须大于0")
            return {"status": "error", "message": "仿真速度必须大于0"}
        logger.info(f"设置仿真速度为 {speed}x")
        # 不再需要更新本地speed状态
        self.simulation_speed = speed
        
        # 将速度设置应用到环境中
        env = getattr(self.simulation_manager, 'env', None)
        if env and hasattr(env, 'set_speed'):
            try:
                env.set_speed(speed)
                logger.info(f"已将速度 {speed}x 应用到仿真环境")
            except Exception as e:
                logger.error(f"设置仿真环境速度时出错: {str(e)}")
        
        # 状态变化通知已在SimulationManager中处理，不需要在这里重复
        # 如果需要额外通知，可以使用以下代码
        self.update_service.add_update({
            "type": "sim_status",
            "status": self.simulation_manager.simulation_status,
            "time": self.simulation_manager.simulation_time,
            "speed": self.simulation_manager.simulation_speed
        })
        
        return {"status": "success", "message": f"仿真速度已设置为 {speed}x"}
    
    async def delete_workflow(self, workflow_id: str) -> bool:
        """删除工作流
        
        Args:
            workflow_id: 工作流ID
            
        Returns:
            bool: 是否删除成功
        """
        if workflow_id in self.active_workflows:
            # 如果仿真正在运行，尝试停止工作流
            workflow = self.active_workflows[workflow_id]
            # 这里需要工作流支持停止操作
            
            # 从活动工作流中移除
            del self.active_workflows[workflow_id]
        
        # 从数据服务中删除
        success = self.data_service.delete_workflow(workflow_id)
        
        if success:
            # 记录事件
            self.update_service.add_update({
                "type": "sim_event",
                "time": self.simulation_manager.simulation_time,
                "source": "WorkflowManager",
                "message": f"删除工作流 (ID: {workflow_id})",
                "level": "info"
            })
        
        return success
    
    async def delete_agent(self, agent_id: str) -> bool:
        """删除智能体
        
        Args:
            agent_id: 智能体ID
            
        Returns:
            bool: 是否删除成功
        """
        # 检查是否是无人机
        is_drone = agent_id in self.active_drones
        
        if agent_id in self.active_agents:
            # 如果仿真正在运行，可能需要特殊处理
            if self.simulation_status == "RUNNING":
                # 复杂情况：正在运行的智能体可能无法安全移除
                logger.warning(f"仿真正在运行，无法安全移除智能体 {agent_id}")
                return False
            
            # 从更新服务注销智能体
            self.update_service.unregister_agent(agent_id)
        
        # 从数据服务中删除
        success = self.data_service.delete_agent(agent_id)
        
        if success:
            # 记录事件
            self.update_service.add_update({
                "type": "sim_event",
                "time": self.simulation_manager.simulation_time,
                "source": "AgentManager",
                "message": f"删除智能体 (ID: {agent_id})",
                "level": "info"
            })
        
        return success
    
    async def start_heartbeat(self):
        """启动WebSocket心跳"""
        while True:
            await asyncio.sleep(30)  # 每30秒发送一次心跳
            self.update_service.add_update({
                "type": "heartbeat",
                "time": self.simulation_manager.simulation_time
            })