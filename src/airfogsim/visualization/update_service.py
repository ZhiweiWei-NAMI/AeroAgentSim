import queue
import time
import threading
import math
import random
from typing import Dict, List, Any, Optional, Set, TYPE_CHECKING

# Forward declaration for type hinting
if TYPE_CHECKING:
    from .simulation_manager import SimulationManager
from datetime import datetime

from .data_service import SimulationDataService
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class UpdateService:
    """处理仿真更新和前端通信的服务"""
    
    def __init__(
        self,
        data_service: SimulationDataService,
        simulation_manager: 'SimulationManager',
        run_repository=None,
    ):
        """初始化更新服务
        
        Args:
            data_service: 数据服务实例，用于更新数据库
            simulation_manager: 仿真管理器实例，用于获取状态
        """
        self.data_service = data_service
        self.simulation_manager = simulation_manager
        self.run_repository = run_repository
        
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
        self._workflow_state_cache: Dict[str, Any] = {}
        
        # 注意：simulation_time 和 simulation_status 现在从 self.simulation_manager 获取
    
    def add_update(self, update_data: Dict[str, Any]):
        """将更新数据添加到队列中
        
        Args:添加到队列的更新数据
        """
        try:
            normalized = dict(update_data)
            if self.run_repository and self.run_repository.active_run_id:
                normalized.setdefault("run_id", self.run_repository.active_run_id)
            if normalized.get("type") == "sim_event":
                normalized = {
                    **normalized,
                    "type": "log_event",
                    "source": normalized.get("source", "SimulationSystem"),
                    "message": normalized.get("message", ""),
                    "level": normalized.get("level", "info"),
                }

            self._persist_runtime_artifacts(normalized)

            # 如果队列接近最大容量，移除一些旧消息
            if self.update_queue.qsize() > self.max_queue_size * 0.9:
                # 尝试清理队列，防止内存溢出
                try:
                    while self.update_queue.qsize() > self.max_queue_size * 0.5:
                        self.update_queue.get_nowait()
                except queue.Empty:
                    pass
            
            # 添加新的更新数据到队列
            self.update_queue.put_nowait(normalized)
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
        self._update_workflow_states()

        spatial_snapshot = self._build_spatial_snapshot()
        if spatial_snapshot:
            self.add_update(spatial_snapshot)
        
        # 准备状态更新数据
        update_data = {
            "type": "sim_status", # 注意：这个状态更新现在由 SimulationManager._notify_status_change 发送
            "status": self.simulation_manager.simulation_status,
            "time": self.simulation_manager.simulation_time,
            "speed": self.simulation_manager.simulation_speed,
            "run_id": self.run_repository.active_run_id if self.run_repository else None,
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

    def _update_workflow_states(self):
        for workflow_id, workflow in self.simulation_manager.active_workflows.items():
            try:
                owner = getattr(workflow, "owner", None)
                agent_id = getattr(owner, "id", None)
                current_state = getattr(workflow.status_machine, "state", None)
                workflow_status = getattr(getattr(workflow, "status", None), "name", "unknown")
                current_task = workflow.get_current_suggested_task() if hasattr(workflow, "get_current_suggested_task") else None
                current_task_name = current_task.get("task_name") if current_task else None
                details = workflow.get_details() if hasattr(workflow, "get_details") else {}

                self.data_service.update_workflow(
                    workflow_id=workflow_id,
                    name=getattr(workflow, "name", workflow_id),
                    type_=workflow.__class__.__name__,
                    agent_id=agent_id,
                    status=workflow_status.lower(),
                    details=details,
                )

                snapshot = (workflow_status, current_state, current_task_name)
                if self._workflow_state_cache.get(workflow_id) == snapshot:
                    continue

                self._workflow_state_cache[workflow_id] = snapshot
                self.add_update(
                    {
                        "type": "workflow_state_diff",
                        "workflow_id": workflow_id,
                        "agent_id": agent_id,
                        "workflow_name": getattr(workflow, "name", workflow_id),
                        "workflow_type": workflow.__class__.__name__,
                        "status": workflow_status.lower(),
                        "state": current_state,
                        "current_task": current_task_name,
                        "details": details,
                        "time": self.simulation_manager.simulation_time,
                    }
                )
            except Exception as exc:
                logger.warning(f"更新工作流 {workflow_id} 状态时出错: {exc}")

    def _build_spatial_snapshot(self) -> Optional[Dict[str, Any]]:
        coordinate_mode = self.simulation_manager.config.get("coordinate_mode", "simulation_plane")
        traffic = self.simulation_manager.config.get("traffic", {}) or {}
        center = traffic.get("center_coordinates", {}) or {}
        workflow_by_agent = {}
        for workflow_id, workflow in self.simulation_manager.active_workflows.items():
            owner = getattr(workflow, "owner", None)
            if owner is not None:
                workflow_by_agent[getattr(owner, "id", None)] = (workflow_id, workflow)

        agents_payload = []
        xs = []
        ys = []
        for agent_id, agent in self.active_agents.items():
            try:
                position = list(agent.get_state("position") or [0.0, 0.0, 0.0])
                while len(position) < 3:
                    position.append(0.0)
                workflow_info = workflow_by_agent.get(agent_id)
                current_workflow = None
                current_task = None
                if workflow_info:
                    current_workflow = workflow_info[0]
                    workflow = workflow_info[1]
                    task = workflow.get_current_suggested_task() if hasattr(workflow, "get_current_suggested_task") else None
                    current_task = task.get("task_name") if task else None

                status = "idle"
                if hasattr(agent, "has_state") and agent.has_state("status"):
                    status = agent.get_state("status")
                elif hasattr(agent, "has_state") and agent.has_state("moving_status"):
                    status = agent.get_state("moving_status")

                display_position = self._project_position(position, coordinate_mode, center)
                xs.append(display_position[1])
                ys.append(display_position[0])

                agents_payload.append(
                    {
                        "agent_id": agent_id,
                        "agent_type": agent.__class__.__name__,
                        "position": position,
                        "display_position": display_position,
                        "altitude": position[2],
                        "status": status,
                        "current_workflow": current_workflow,
                        "current_task": current_task,
                        "color": self._agent_color(agent, status),
                        "recent_log": self.run_repository.get_recent_log_message(agent_id)
                        if self.run_repository
                        else None,
                    }
                )
            except Exception as exc:
                logger.warning(f"生成空间快照时处理智能体 {agent_id} 失败: {exc}")

        return {
            "type": "spatial_snapshot",
            "run_id": self.run_repository.active_run_id if self.run_repository else None,
            "timestamp": self.simulation_manager.simulation_time,
            "coordinate_mode": coordinate_mode,
            "bounds": {
                "min_lat": min(ys) if ys else 0.0,
                "max_lat": max(ys) if ys else 0.0,
                "min_lng": min(xs) if xs else 0.0,
                "max_lng": max(xs) if xs else 0.0,
            },
            "agents": agents_payload,
        }

    def _project_position(self, position, coordinate_mode: str, center: Dict[str, Any]):
        x, y, _z = position
        if coordinate_mode == "geo_osm":
            center_lat = float(center.get("lat", 0.0))
            center_lon = float(center.get("lon", 0.0))
            if abs(x) <= 180 and abs(y) <= 90 and center_lat == 0.0 and center_lon == 0.0:
                return [y, x]
            lat = center_lat + (y / 111320.0)
            lon_divisor = 111320.0 * max(math.cos(math.radians(lat)), 0.1)
            lon = center_lon + (x / lon_divisor)
            return [lat, lon]
        return [y, x]

    def _agent_color(self, agent: Any, status: str) -> str:
        battery = None
        try:
            if hasattr(agent, "has_state") and agent.has_state("battery_level"):
                battery = float(agent.get_state("battery_level"))
        except Exception:
            battery = None

        lowered_status = (status or "").lower()
        if "charg" in lowered_status:
            return "#27ae60"
        if battery is not None and battery < 20:
            return "#d94841"
        if battery is not None and battery < 40:
            return "#ef8b17"
        if "move" in lowered_status:
            return "#2f6fed"
        return "#4d5b7c"

    def _persist_runtime_artifacts(self, update_data: Dict[str, Any]) -> None:
        if not self.run_repository or not self.run_repository.active_run_id:
            return
        update_type = update_data.get("type")
        if update_type == "sim_status":
            self.run_repository.update_status(
                update_data.get("status", "unknown"),
                float(update_data.get("time", 0.0)),
                float(update_data.get("speed", self.simulation_manager.simulation_speed)),
            )
        elif update_type == "log_event":
            self.run_repository.append_log(update_data)
        elif update_type == "workflow_state_diff":
            self.run_repository.record_workflow_state(update_data)
        elif update_type == "spatial_snapshot":
            self.run_repository.record_spatial_snapshot(update_data)
    
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
        self._workflow_state_cache.clear()
