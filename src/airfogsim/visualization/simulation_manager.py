import threading
import time
import queue
import subprocess # Added
import sys # Added
import os # Added
import signal # Added
from typing import Dict, Any, Callable, Optional, Set

from simpy.core import StopSimulation

# Assuming PausableEnvironment is in environment.py
from .environment import PausableEnvironment
# Assuming setup functions are in setup.py
from .setup import setup_environment_resources, create_agent_from_config, create_workflow_from_config
# Assuming UpdateService handles queueing
from .update_service import UpdateService
# Import TrafficDataProvider
from airfogsim.dataprovider.traffic import TrafficDataProvider # Added
from .sumo_utils import start_sumo, terminate_sumo
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)


class SimulationManager:
    """管理仿真生命周期（启动、暂停、恢复、重置）和执行线程"""

    def __init__(self, update_service: Optional[UpdateService], config: Dict[str, Any]):
        self.update_service = update_service
        self.config = config # Initial configuration

        self.env: Optional[PausableEnvironment] = None
        self.simulation_thread: Optional[threading.Thread] = None
        self.simulation_status = "STOPPED" # STOPPED, RUNNING, PAUSED, COMPLETED, ERROR
        self.simulation_time = 0.0
        self.simulation_speed = 1.0

        # Store references to active entities if needed for control
        self.active_agents: Dict[str, Any] = {}
        self.active_workflows: Dict[str, Any] = {}
        # Keep track of drone IDs specifically if needed elsewhere
        self.active_drones: Set[str] = set()

        # Add references for SUMO process and TrafficDataProvider
        self.sumo_process: Optional[subprocess.Popen] = None # Added
        self.traffic_provider: Optional[TrafficDataProvider] = None # Added

        # Callback for event logging (passed to environment)
        self._event_logger_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    def set_event_logger(self, callback: Callable[[Dict[str, Any]], None]):
        """设置用于记录环境事件的回调函数"""
        self._event_logger_callback = callback

    def _log_sim_event(self, source: str, message: str, level: str = "info", extra_data: Optional[Dict] = None):
        """Helper to log simulation events via the update service"""
        event_data = {
            "type": "sim_event",
            "time": self.simulation_time, # Use manager's time for consistency before env exists
            "source": source,
            "message": message,
            "level": level,
            **(extra_data or {})
        }
        # Use the current env time if available and status is running/paused
        if self.env and self.simulation_status in ["RUNNING", "PAUSED"]:
            event_data["time"] = self.env.now

        if self._event_logger_callback:
             # If an environment logger is set, use it (it likely queues the event)
             self._event_logger_callback(event_data)
        else:
             # Otherwise, queue directly via update_service
             self.update_service.add_update(event_data)


    def _setup_simulation(self) -> bool:
        """初始化仿真环境、资源、智能体和工作流。返回 True 表示成功，False 表示失败。"""
        if self.env:
            logger.warning("Setup called but environment already exists. Resetting first.")
            self._reset_internal_state() # Reset state without stopping threads yet

        self._log_sim_event("SimulationManager", "开始设置仿真环境...")
        self.simulation_time = 0.0 # Reset time on setup

        # 1. 创建环境
        try:
            self.env = PausableEnvironment(
                initial_time=0, # Start time at 0
                visual_interval=self.config.get("visual_interval", 10), # Or get from config
                logger=self._event_logger_callback # Pass the logger callback
            )
            self.env.set_speed(self.simulation_speed) # Apply initial speed
            self._log_sim_event("SimulationManager", "PausableEnvironment 创建成功")
        except Exception as e:
            self._log_sim_event("SimulationManager", f"创建 PausableEnvironment 失败: {e}", "error")
            self.simulation_status = "ERROR"
            self._notify_status_change()
            return False

        # 2. Check and Start SUMO if configured in self.config
        # Example config structure:
        # self.config = {
        #     ...
        #     "traffic": {
        #         "source": "sumo",
        #         "update_interval": 1.0,
        #         "center_coordinates": { "lat": 39.9, "lon": 116.4 },
        #         "sumo_config": {
        #             "config_file": "sumo_configs/simple.sumocfg", # Relative to project root
        #             "port": 8813,
        #             "gui": False,
        #             "host": "localhost" # Host for TraCI connection
        #         }
        #     },
        #     ...
        # }
        traffic_config = self.config.get("traffic")
        sumo_enabled = traffic_config and traffic_config.get("source") == "sumo"
        self.sumo_process = None # Ensure reset before attempting start

        print(traffic_config)
        if sumo_enabled:
            sumo_cfg = traffic_config.get("sumo_config", {})
            sumocfg_path = sumo_cfg.get("config_file")
            sumo_port = sumo_cfg.get("port", 8813)
            sumo_gui = sumo_cfg.get("gui", False)

            if not sumocfg_path:
                self._log_sim_event("SimulationManager", "Traffic source is SUMO, but 'config_file' is missing in traffic.sumo_config", "error")
                self.simulation_status = "ERROR"
                self._notify_status_change()
                # No env cleanup needed yet, but return False
                return False

            # 处理路径中的static前缀，将其替换为frontend/public
            if sumocfg_path.startswith("static/"):
                sumocfg_path = sumocfg_path.replace("static/", "frontend/public/")
                self._log_sim_event("SimulationManager", f"SUMO配置文件路径调整: {sumocfg_path}", "info")

            # start_sumo now handles path resolution and validation
            self.sumo_process = start_sumo(sumocfg_path, sumo_port, sumo_gui)

            if not self.sumo_process:
                # start_sumo logs the specific error
                self._log_sim_event("SimulationManager", f"启动 SUMO 失败 (Config: {sumocfg_path}, Port: {sumo_port})", "error")
                self.simulation_status = "ERROR"
                self._notify_status_change()
                # No env cleanup needed yet, but return False
                return False
            else:
                self._log_sim_event("SimulationManager", f"SUMO 进程已启动 (PID: {self.sumo_process.pid}, Port: {sumo_port})")
                # 增加额外等待时间，确保SUMO完全初始化
                import time
                time.sleep(2)
                # Store the actual port used, in case default was used
                traffic_config.setdefault("sumo_config", {})['port'] = sumo_port
                # Store the host used for TraCI connection
                traffic_config.setdefault("sumo_config", {})['host'] = sumo_cfg.get('host', 'localhost')
                
                # 确保传递中心坐标到TrafficDataProvider（若没有则从config获取）
                if 'center_coordinates' not in traffic_config:
                    lat_lon_list = sumo_cfg['osm_center']
                    traffic_config['center_coordinates'] = {
                        "lat": lat_lon_list[0],
                        "lon": lat_lon_list[1]
                    }
        # 3. 设置 AirFogSim 内部资源 (Airspaces, Frequencies, etc.)
        try:
            setup_environment_resources(self.env, self.config, self._log_sim_event)
            self._log_sim_event("SimulationManager", "AirFogSim 环境资源设置成功")
        except Exception as e:
            self._log_sim_event("SimulationManager", f"设置 AirFogSim 环境资源失败: {e}", "error")
            self.simulation_status = "ERROR"
            self._notify_status_change()
            # Terminate SUMO if it was started before this failure
            terminate_sumo(self.sumo_process) # Safe to call even if None
            return False

        # 4. Setup TrafficDataProvider if SUMO is enabled and started successfully
        self.traffic_provider = None # Ensure it's reset
        if sumo_enabled and self.sumo_process:
            # traffic_config should be populated with host/port by now if sumo_enabled
            try:
                self.traffic_provider = TrafficDataProvider(self.env, traffic_config)
                # Register the provider with the environment so it can be accessed if needed
                # And so its events are potentially logged/handled by the environment
                self.env.add_data_provider('traffic', self.traffic_provider)
                self._log_sim_event("SimulationManager", "TrafficDataProvider (SUMO) 创建并注册成功")
            except Exception as e:
                self._log_sim_event("SimulationManager", f"创建 TrafficDataProvider 失败: {e}", "error")
                self.simulation_status = "ERROR"
                self._notify_status_change()
                terminate_sumo(self.sumo_process) # Clean up SUMO
                # No env cleanup needed yet, but return False
                return False

        # 5. 创建智能体 (Agents)
        self.active_agents.clear()
        self.active_drones.clear()
        agent_creation_failed = False
        for agent_config in self.config.get("agents", []):
            try:
                # Pass data_service if needed by agent creation (e.g., to update DB immediately)
                agent = create_agent_from_config(self.env, agent_config, self._log_sim_event, self.update_service.data_service)
                if agent:
                    self.active_agents[agent.id] = agent
                    if agent_config.get("type") == "drone":
                        self.active_drones.add(agent.id)
            except Exception as e:
                 self._log_sim_event("AgentManager", f"创建智能体 (config: {agent_config.get('id', 'N/A')}) 失败: {e}", "error")
                 agent_creation_failed = True # Mark failure but continue setup

        self._log_sim_event("SimulationManager", f"创建了 {len(self.active_agents)} 个智能体")
        if agent_creation_failed:
             self._log_sim_event("SimulationManager", "一个或多个智能体创建失败", "warning")
             # Decide if this constitutes a full setup failure
             # if agent_creation_failed:
             #    self.simulation_status = "ERROR"
             #    terminate_sumo(self.sumo_process)
             #    return False

        # 6. 创建工作流 (Workflows)
        self.active_workflows.clear()
        workflow_creation_failed = False
        for workflow_config in self.config.get("workflows", []):
            agent_id = workflow_config.get("agent_id")
            if agent_id in self.active_agents:
                try:
                    agent = self.active_agents[agent_id]
                    # Pass data_service if needed by workflow creation
                    workflow = create_workflow_from_config(self.env, workflow_config, agent, self._log_sim_event, self.update_service.data_service)
                    if workflow:
                        wf_id = workflow_config.get("id", getattr(workflow, 'workflow_id', None)) # Get ID safely
                        if wf_id:
                             self.active_workflows[wf_id] = workflow
                        else:
                             self._log_sim_event("WorkflowManager", f"创建的工作流缺少ID (config: {workflow_config.get('name', 'N/A')})", "warning")

                except Exception as e:
                    self._log_sim_event("WorkflowManager", f"为智能体 {agent_id} 创建工作流 (config: {workflow_config.get('id', 'N/A')}) 失败: {e}", "error")
                    workflow_creation_failed = True
            else:
                self._log_sim_event("WorkflowManager", f"跳过工作流创建：找不到智能体 {agent_id}", "warning")

        self._log_sim_event("SimulationManager", f"创建了 {len(self.active_workflows)} 个工作流")
        if workflow_creation_failed:
            self._log_sim_event("SimulationManager", "一个或多个工作流创建失败", "warning")
            # Decide if this constitutes a full setup failure
            # if workflow_creation_failed:
            #    self.simulation_status = "ERROR"
            #    terminate_sumo(self.sumo_process)
            #    return False

        self._log_sim_event("SimulationManager", "仿真环境设置完成")
        return True


    def _simulation_runner(self):
        """运行仿真的线程函数"""
        if not self.env:
            logger.error("Simulation runner called but environment is not initialized.")
            self.simulation_status = "ERROR"
            self._notify_status_change()
            return

        try:
            thread_name = threading.current_thread().name
            logger.info(f"仿真线程 '{thread_name}' 已启动")
            self.simulation_status = "RUNNING"
            self._notify_status_change() # Notify status change *before* starting workflows/providers

            # Start TrafficDataProvider event triggering if it exists
            if self.traffic_provider:
                try:
                    # load_data might be needed if provider fetches initial state
                    self.traffic_provider.load_data()
                    # start_event_triggering typically starts a simpy process
                    self.traffic_provider.start_event_triggering()
                    self._log_sim_event("TrafficDataProvider", "已启动交通事件触发")
                except ConnectionRefusedError as e:
                     # Specific error for TraCI connection failure
                     self._log_sim_event("TrafficDataProvider", f"无法连接到 SUMO TraCI 服务器: {e}. 确保SUMO正在运行并监听指定端口。", "error")
                     self.simulation_status = "ERROR"
                     # The main loop will check status and exit
                except Exception as e:
                    # Catch other potential errors during provider start
                    self._log_sim_event("TrafficDataProvider", f"启动 TrafficDataProvider 时发生错误: {e}", "error")
                    self.simulation_status = "ERROR"
                    # The main loop will check status and exit

            # Start configured workflows (needs env.process) only if status is still RUNNING
            if self.simulation_status == "RUNNING":
                for workflow_id, workflow in self.active_workflows.items():
                    try:
                        # Assuming workflow.start() returns a generator for simpy process
                        self.env.process(workflow.start())
                        self._log_sim_event("WorkflowManager", f"工作流 '{workflow_id}' 已启动")
                    except Exception as e:
                        self._log_sim_event("WorkflowManager", f"启动工作流 '{workflow_id}' 失败: {e}", "error")
                        # Decide if a single workflow failing to start is critical
                        # self.simulation_status = "ERROR"
                        # break # Exit workflow loop if one fails?


            # --- 主仿真循环 ---
            # Run indefinitely until stopped, completed, or error
            while self.simulation_status == "RUNNING":
                # Calculate a small time step to run for
                # This allows the simulation to yield control periodically
                current_time = self.env.now
                # Run for a small duration of simulation time
                # The actual real-time duration is controlled by env.step() and speed
                run_duration = 0.5 # Run 0.5 sim seconds at a time (adjust as needed)
                next_time_target = current_time + run_duration

                try:
                    # Check if there are any events left
                    if self.env.peek() == float('inf'):
                        logger.info("仿真已完成所有事件")
                        self.simulation_status = "COMPLETED"
                        break # Exit the while loop

                    # Run the simulation until the next target time
                    # The PausableEnvironment's run method handles pausing and speed
                    self.env.run(until=next_time_target)

                    # Update simulation time state *after* run chunk
                    self.simulation_time = self.env.now

                    # Small sleep to prevent tight loop and allow other threads (e.g., API)
                    # This is real time, not simulation time. Adjust if needed.
                    time.sleep(0.01)

                except StopSimulation:
                    logger.info("仿真被 StopSimulation 事件停止")
                    # Decide if this means COMPLETED or STOPPED based on how it's used
                    self.simulation_status = "COMPLETED" # Assume it means natural end
                    break
                except Exception as e:
                    logger.error(f"仿真循环中发生错误: {e}", exc_info=True)
                    self._log_sim_event("SimulationRunner", f"仿真循环错误: {e}", "error")
                    self.simulation_status = "ERROR"
                    break # Exit loop on error

            # --- 循环结束 ---
            # Ensure final time is updated
            if self.env:
                self.simulation_time = self.env.now
            logger.info(f"仿真线程 '{thread_name}' 结束，最终状态: {self.simulation_status}, 仿真时间: {self.simulation_time:.2f}")

        except Exception as e:
            # Catch errors during setup phase within the thread too
            logger.error(f"仿真线程 '{threading.current_thread().name}' 启动时出错: {e}", exc_info=True)
            self.simulation_status = "ERROR"
        finally:
            # --- Cleanup after simulation loop ends (normally or via error/stop) ---
            logger.info(f"Simulation loop ended with status: {self.simulation_status}")
            # Final time update
            if self.env:
                self.simulation_time = self.env.now

            # Notify final status
            self._notify_status_change()


    def _notify_status_change(self):
        """通知前端仿真状态、时间和速度的变化"""
        status_data = {
            "type": "sim_status",
            "status": self.simulation_status,
            "time": self.simulation_time,
            "speed": self.simulation_speed
        }
        self.update_service.add_update(status_data)
        logger.debug(f"Status Change Notified: {self.simulation_status} @ {self.simulation_time:.2f} (Speed: {self.simulation_speed}x)")


    def start_simulation(self):
        """启动或恢复仿真"""
        if self.simulation_status == "RUNNING":
            logger.warning("仿真已经在运行中")
            return {"status": "warning", "message": "仿真已在运行"}

        if self.simulation_status == "PAUSED" and self.env:
            logger.info("恢复仿真...")
            self.env.resume()
            self.simulation_status = "RUNNING"
            self._notify_status_change()
            self._log_sim_event("SimulationManager", "仿真已恢复")
            return {"status": "success", "message": "仿真已恢复"}

        # If stopped, completed, or error, start a new simulation
        logger.info("启动新仿真...")
        if not self._setup_simulation():
             logger.error("仿真设置失败，无法启动")
             # Status already set to ERROR in _setup_simulation
             self._notify_status_change() # Notify error status
             return {"status": "error", "message": "仿真设置失败"}

        # 确保之前的线程已经终止 (defensive check)
        if self.simulation_thread and self.simulation_thread.is_alive():
            logger.warning("检测到之前的仿真线程仍在运行，尝试强制终止...")
            # This is tricky and potentially unsafe. Best effort.
            # Consider signaling the thread to stop if possible.
            self.simulation_status = "STOPPED" # Signal thread to stop
            self.simulation_thread.join(timeout=1.0)
            if self.simulation_thread.is_alive():
                logger.error("无法终止之前的仿真线程，可能会导致问题。继续启动新线程...")

        # 在单独线程中运行仿真
        thread_name = f"simulation_thread_{int(time.time())}"
        self.simulation_thread = threading.Thread(
            target=self._simulation_runner,
            daemon=True,
            name=thread_name
        )
        self.simulation_thread.start()
        # Status will be set to RUNNING inside the thread start
        return {"status": "success", "message": "仿真启动中"}


    def pause_simulation(self):
        """暂停仿真"""
        if self.simulation_status != "RUNNING" or not self.env:
            logger.warning(f"仿真未在运行状态 ({self.simulation_status})，无法暂停")
            return {"status": "warning", "message": "仿真未在运行"}

        logger.info("请求暂停仿真...")
        try:
            self.env.pause_now()
            self.simulation_status = "PAUSED"
            self.simulation_time = self.env.now # Update time when paused
            self._notify_status_change()
            self._log_sim_event("SimulationManager", "仿真已暂停")
            logger.info(f"仿真已暂停于时间: {self.simulation_time:.2f}")
            return {"status": "success", "message": "仿真已暂停"}
        except Exception as e:
            logger.error(f"暂停仿真时出错: {e}", exc_info=True)
            return {"status": "error", "message": f"暂停仿真时出错: {e}"}


    def resume_simulation(self):
        """恢复仿真（调用 start_simulation 处理）"""
        return self.start_simulation()


    def _reset_internal_state(self):
        """重置内部状态变量，并清理外部进程/资源 (如 SUMO, DataProvider)。"""
        logger.debug("开始重置内部仿真状态和外部资源...")

        # 1. Close TrafficDataProvider if it exists and has a close method
        # Ensures TraCI connection is closed before terminating SUMO
        if self.traffic_provider and hasattr(self.traffic_provider, 'close'):
            try:
                logger.info("正在关闭 TrafficDataProvider...")
                self.traffic_provider.close()
                logger.info("TrafficDataProvider 已关闭")
            except Exception as e:
                logger.error(f"关闭 TrafficDataProvider 时出错: {e}", exc_info=True)
        self.traffic_provider = None # Clear reference

        # 2. Terminate SUMO process if it exists and is running
        terminate_sumo(self.sumo_process) # Use the helper function
        self.sumo_process = None # Clear reference

        # 3. Reset AirFogSim internal state variables
        self.env = None # Release environment reference
        # self.simulation_thread = None # Thread object is managed in reset_simulation
        self.simulation_time = 0.0
        # self.simulation_status is managed by the caller (reset_simulation)
        self.active_agents.clear()
        self.active_workflows.clear()
        self.active_drones.clear()

        logger.debug("内部仿真状态和外部资源已重置完成")


    def reset_simulation(self):
        """重置仿真环境和状态"""
        logger.info("请求重置仿真...")
        original_status = self.simulation_status

        # 1. Signal the simulation thread to stop
        if self.simulation_status in ["RUNNING", "PAUSED"]:
            self.simulation_status = "STOPPED" # Signal loop to exit
            if self.env and original_status == "PAUSED":
                 # If paused, need to resume briefly to allow thread to check status and exit
                 logger.debug("Resuming paused sim briefly to allow thread termination.")
                 self.env.resume()
                 time.sleep(0.05) # Give thread a moment to react

        # 2. Wait for the simulation thread to terminate
        if self.simulation_thread and self.simulation_thread.is_alive():
            thread_name = self.simulation_thread.name
            logger.info(f"等待仿真线程 '{thread_name}' 终止...")
            self.simulation_thread.join(timeout=3.0) # Wait for clean exit
            if self.simulation_thread.is_alive():
                logger.warning(f"仿真线程 '{thread_name}' 未能在超时时间内终止。")
                # Consider more forceful termination if necessary, though risky

        # 3. Reset internal state variables (calls the modified _reset_internal_state)
        self._reset_internal_state() # This now handles SUMO/DataProvider cleanup

        # 4. Set status to STOPPED and notify
        self.simulation_status = "STOPPED"
        self._notify_status_change()
        self._log_sim_event("SimulationManager", "仿真已重置")
        logger.info("仿真已成功重置")
        return {"status": "success", "message": "仿真已重置"}


    def set_simulation_speed(self, speed: float):
        """设置仿真速度"""
        if speed <= 0:
            logger.warning("Simulation speed must be > 0")
            return {"status": "error", "message": "仿真速度必须大于0"}

        logger.info(f"Setting simulation speed to {speed}x")
        self.simulation_speed = speed

        # Apply speed to environment if it exists and is running/paused
        if self.env and self.simulation_status in ["RUNNING", "PAUSED"]:
            try:
                self.env.set_speed(speed)
                logger.info(f"Applied speed {speed}x to PausableEnvironment")
                self._notify_status_change() # Notify speed change
                return {"status": "success", "message": f"仿真速度已设置为 {speed}x"}
            except Exception as e:
                 logger.error(f"Error setting speed in PausableEnvironment: {e}", exc_info=True)
                 return {"status": "error", "message": f"设置环境速度时出错: {e}"}
        else:
            logger.info(f"Simulation speed set to {speed}x (will be applied on next start/resume)")
            # Notify anyway so UI reflects the target speed
            self._notify_status_change()
            return {"status": "success", "message": f"仿真速度已暂存为 {speed}x"}


    # Optional: Add methods to dynamically add/remove agents/workflows if needed
    # These would need careful handling depending on simulation state

    # Example: Add agent while paused/stopped (simplified)
    # def add_agent_runtime(self, agent_config):
    #     if self.simulation_status not in ["STOPPED", "PAUSED"]:
    #         return {"status": "error", "message": "Can only add agents when stopped or paused"}
    #     if not self.env:
    #          return {"status": "error", "message": "Simulation environment not initialized"}
    #     try:
    #         agent = create_agent_from_config(self.env, agent_config, ...)
    #         if agent:
    #             self.active_agents[agent.id] = agent
    #             # ... register with update service etc. ...
    #             return {"status": "success", "agent_id": agent.id}
    #     except Exception as e:
    #          return {"status": "error", "message": f"Failed to add agent: {e}"}
    #     return {"status": "error", "message": "Failed to create agent"}
