import queue
import time
import threading
import math
import random
from typing import Dict, List, Any, Optional, Set, Deque, TYPE_CHECKING

# Forward declaration for type hinting
if TYPE_CHECKING:
    from .simulation_manager import SimulationManager
from datetime import datetime

from .data_service import SimulationDataService
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class UpdateService:
    """处理仿真更新和前端通信的服务"""
    
    def __init__(self, data_service: SimulationDataService, simulation_manager: 'SimulationManager'):
        """初始化更新服务
        
        Args:
            data_service: 数据服务实例，用于更新数据库
            simulation_manager: 仿真管理器实例，用于获取状态
        """
        self.data_service = data_service
        self.simulation_manager = simulation_manager
        
        # 添加消息队列，用于线程间通信
        self.update_queue = queue.Queue()
        self.max_queue_size = 1000  # 防止队列无限增长
        
        # 前端更新线程控制
        self._updater_running = False
        self._updater_thread = None
        
        # 活动实体集合（仅用于更新）
        self.active_drones: Set[str] = set()
        self.active_vehicles: Set[str] = set()
        self.active_agents: Dict[str, Any] = {}
        
        # 注意：simulation_time 和 simulation_status 现在从 self.simulation_manager 获取
    
    def add_update(self, update_data: Dict[str, Any]):
        """将更新数据添加到队列中
        
        Args:添加到队列的更新数据
        """
        try:
            # 如果队列接近最大容量，移除一些旧消息
            if self.update_queue.qsize() > self.max_queue_size * 0.9:
                # 尝试清理队列，防止内存溢出
                try:
                    while self.update_queue.qsize() > self.max_queue_size * 0.5:
                        self.update_queue.get_nowait()
                except queue.Empty:
                    pass
            
            # 添加新的更新数据到队列
            self.update_queue.put_nowait(update_data)
        except Exception as e:
            logger.error(f"添加更新到队列时出错: {str(e)}")
    
    def get_updates(self, max_items: int = 10) -> List[Dict[str, Any]]:
        """从队列中获取更新，API层调用此方法
        
        Args:
            max_items: 最大获取项目数
            
        Returns:
            List[Dict[str, Any]]: 更新数据列表
        """
        updates = []
        try:
            # 非阻塞获取队列中的更新
            for _ in range(min(max_items, self.update_queue.qsize())):
                if not self.update_queue.empty():
                    updates.append(self.update_queue.get_nowait())
        except queue.Empty:
            pass
        except Exception as e:
            logger.error(f"从队列获取更新时出错: {str(e)}")
        
        return updates
    
    # 移除 update_simulation_state 方法，状态更新由 SimulationManager 通过 _notify_status_change 推送
    # def update_simulation_state(self, status: str, time: float, speed: float):
    #     ...
    def start_frontend_updater(self):
        """启动前端更新线程"""
        # 检查是否已有更新线程在运行
        if self._updater_running:
            logger.warning("前端更新线程已在运行")
            return
        
        self._updater_running = True
        
        def updater():
            try:
                # 从 simulation_manager 获取状态
                logger.info(f"前端更新线程已启动, 状态: {self.simulation_manager.simulation_status}")
                while self._updater_running and self.simulation_manager.simulation_status in ["RUNNING", "PAUSED"]:
                    self.update_frontend()
                    time.sleep(0.1)  # 每0.1秒更新一次
                
                # 线程结束时清除标志
                self._updater_running = False
                logger.info(f"前端更新线程已结束, 状态: {self.simulation_manager.simulation_status}")
            except Exception as e:
                logger.error(f"前端更新线程出错: {str(e)}")
                self._updater_running = False
        
        self._updater_thread = threading.Thread(
            target=updater,
            daemon=True,
            name=f"frontend_updater_{int(time.time())}"  # 添加时间戳以便识别
        )
        self._updater_thread.start()
    
    def stop_frontend_updater(self):
        """停止前端更新线程"""
        if self._updater_running:
            logger.info("停止前端更新线程...")
            self._updater_running = False
            
            # 等待线程结束
            if self._updater_thread and self._updater_thread.is_alive():
                self._updater_thread.join(timeout=1.0)
                if self._updater_thread.is_alive():
                    logger.warning("前端更新线程未能在超时时间内终止")
            
            self._updater_thread = None
    
    def update_frontend(self):
        """更新前端数据"""
        # 更新无人机状态
        self._update_drone_states()
        
        # 准备状态更新数据
        update_data = {
            "type": "sim_status", # 注意：这个状态更新现在由 SimulationManager._notify_status_change 发送
            "status": self.simulation_manager.simulation_status,
            "time": self.simulation_manager.simulation_time
        }
        
        # 将更新放入队列
        self.add_update(update_data)
    
    def _update_drone_states(self):
        """更新无人机状态"""
        for agent_id, agent in self.active_agents.items():
            if agent_id in self.active_drones:
                try:
                    position = agent.get_state('position')
                    battery = agent.get_state('battery_level')
                    status = agent.get_state('status') if agent.has_state('status') else "idle"
                    speed = agent.get_state('speed') if agent.has_state('speed') else 0.0
                    
                    # 更新数据服务
                    self.data_service.update_drone_state(
                        drone_id=agent_id,
                        position=position,
                        battery_level=battery,
                        status=status,
                        speed=speed,
                        sim_time=self.simulation_manager.simulation_time # 使用 manager 的时间
                    )
                except Exception as e:
                    logger.warning(f"更新无人机 {agent_id} 状态时出错: {str(e)}")
        
    def register_agent(self, agent_id: str, agent: Any, is_drone: bool = False):
        """注册智能体到更新服务
        
        Args:
            agent_id: 智能体ID
            agent: 智能体对象
            is_drone: 是否为无人机
        """
        self.active_agents[agent_id] = agent
        if is_drone:
            self.active_drones.add(agent_id)
    
    def unregister_agent(self, agent_id: str):
        """从更新服务注销智能体
        
        Args:
            agent_id: 智能体ID
        """
        if agent_id in self.active_agents:
            del self.active_agents[agent_id]
        
        if agent_id in self.active_drones:
            self.active_drones.remove(agent_id)
    
    def clear_all_agents(self):
        """清除所有注册的智能体"""
        self.active_agents.clear()
        self.active_drones.clear()
        self.active_vehicles.clear()