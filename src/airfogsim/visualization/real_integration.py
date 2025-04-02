import asyncio
import logging
import json
import uuid
import threading
import time
import queue
from typing import Dict, List, Any, Optional, Callable, Set, Deque
from datetime import datetime
from collections import deque

import simpy
from simpy.events import Event, Timeout

from airfogsim.core.environment import Environment
from airfogsim.agent import DroneAgent
from airfogsim.component import MoveToComponent, ChargingComponent
from airfogsim.workflow.inspection import create_inspection_workflow
from airfogsim.workflow.charging import create_charging_workflow
from simpy.core import StopSimulation # <--- 添加这一行

from .data_service import SimulationDataService
from .config import DEFAULT_AIRSPACE, DEFAULT_FREQUENCY, DEFAULT_LANDING_SPOT

logger = logging.getLogger(__name__)

class PausableEnvironment(Environment):
    """可暂停的仿真环境，扩展自airfogsim的Environment"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pause_at = float('inf')
        self._paused = False
        self._pause_event = threading.Event()
        self._resume_event = threading.Event()
        # 初始化为已恢复状态
        self._resume_event.set()
        # 添加速度控制
        self._speed = 1.0
        self._last_step_time = None
        self._step_interval = 0.01  # 默认步进间隔，单位秒
        
    def pause_at(self, time):
        """设置在特定时间暂停仿真"""
        self._pause_at = time
    
    def pause_now(self):
        """立即暂停仿真"""
        if not self._paused:
            self._paused = True
            self._pause_event.set()
            self._resume_event.clear()
            logger.info(f"仿真暂停于时间: {self.now}")
    
    def resume(self):
        """恢复仿真"""
        if self._paused:
            self._paused = False
            self._pause_at = float('inf')
            self._pause_event.clear()
            self._resume_event.set()
            logger.info(f"仿真恢复于时间: {self.now}")

    def set_speed(self, speed):
        """设置仿真速度"""
        if speed <= 0:
            raise ValueError("仿真速度必须大于0")
        self._speed = speed
        # 根据速度调整步进间隔
        self._step_interval = 0.01 / speed  # 速度越快，间隔越小
        
    def step(self):
        """执行一步仿真，添加速度控制"""
        current_real_time = time.time()
        
        # 如果有上一步的时间记录，根据速度控制添加延时
        if self._last_step_time is not None:
            # 计算两步之间的实际时间差
            elapsed_real_time = current_real_time - self._last_step_time
            
            # 如果实际时间差小于期望的步进间隔，则等待
            wait_time = self._step_interval - elapsed_real_time
            if wait_time > 0:
                time.sleep(wait_time)
        
        # 执行原始的step逻辑
        if self._paused:
            self._pause_event.clear()
            self._resume_event.set()
            # 暂时恢复以执行一步
            super().step()
            # 恢复暂停状态
            self._paused = True
            self._pause_event.set()
            self._resume_event.clear()
        else:
            super().step()
        
        # 更新上一步的时间
        self._last_step_time = time.time()
            
    def run(self, until=None):
        """重写run方法，支持暂停和恢复"""
        if until is not None:
            self._target_time = until
        else:
            self._target_time = float('inf')
        
        try:
            # 获取下一个事件时间
            next_event_time = self.peek()
            
            # 如果没有事件或者下一个事件时间已经超过目标时间，记录日志并返回
            if next_event_time == float('inf'):
                logger.debug(f"仿真没有更多事件，当前时间: {self.now}")
                return self.now
            
            if next_event_time >= self._target_time:
                logger.debug(f"下一个事件时间 {next_event_time} 已超过目标时间 {self._target_time}")
                # 如果有事件但时间超过目标，将当前时间推进到目标时间
                if self._target_time > self.now:
                    self.now = self._target_time
                return self.now
            
            # 正常情况下执行事件循环
            while self.peek() < self._target_time:
                # 检查是否需要暂停
                if self.now >= self._pause_at and not self._paused:
                    self.pause_now()
                
                # 如果已暂停，等待恢复信号
                if self._paused:
                    logger.info("仿真已暂停，等待恢复...")
                    self._resume_event.wait()
                
                # 执行下一个事件
                self.step()
                
                # 如果没有更多事件，跳出循环
                if self.peek() == float('inf'):
                    logger.debug("执行完当前事件后没有更多事件")
                    break
                
        except StopSimulation as exc:
            return exc.args[0]
        
        return self.now


class RealSimulationIntegration:
    """实际仿真集成层，连接仿真环境和API层，并提供对SimPy环境的控制"""
    
    def __init__(self, data_service: SimulationDataService):
        self.data_service = data_service
        self.simulation_status = "STOPPED"
        self.simulation_time = 0.0
        self.simulation_speed = 1.0
        self.active_drones: Set[str] = set()
        self.active_workflows: Dict[str, Any] = {}
        self.active_agents: Dict[str, Any] = {}
        
        # 创建环境但不立即启动
        self.env = None
        self.simulation_thread = None
        
        # 用于定期更新前端的任务
        self.update_task = None
        
        # 添加消息队列，用于线程间通信
        self.update_queue = queue.Queue()
        self.max_queue_size = 1000  # 防止队列无限增长
        
        # 环境配置参数
        self.config = {
            "airspaces": [],
            "frequencies": [],
            "landing_spots": [],
            "agents": [],
            "workflows": []
        }
        
    def _event_logger(self, event_data):
        """事件日志记录器，将事件添加到更新队列"""
        event_data['message'] = f'{event_data["event"]}:{event_data["value"]}'
        event_data['level'] = 'info'
        self._add_to_update_queue(event_data)
    
    def _setup_environment(self):
        """设置仿真环境及资源"""
        # 创建可暂停环境，传入事件记录器
        self.env = PausableEnvironment(
            visual_interval=10,
            logger=self._event_logger  # 传入事件记录器
        )
        
        # 创建默认资源或从配置加载资源
        self._setup_resources()
        
        # 由于asyncio.run()不能在已有事件循环中使用，我们只记录日志，不发送事件
        logger.info("仿真环境已初始化")
        
        return self.env
    
    def _setup_resources(self):
        """设置仿真环境中的资源"""
        if not self.env:
            return
            
        # 记录日志
        self._add_to_update_queue({
            "type": "sim_event",
            "time": self.simulation_time,
            "source": "ResourceManager",
            "message": "开始配置仿真环境资源",
            "level": "info"
        })
            
        # 设置空域资源
        for idx, airspace_config in enumerate(self.config.get("airspaces", [])):
            try:
                airspace_id = self.env.airspace_manager.create_airspace(**airspace_config)
                self._add_to_update_queue({
                    "type": "sim_event",
                    "time": self.simulation_time,
                    "source": "AirspaceManager",
                    "message": f"创建空域资源 {airspace_config.get('attributes', {}).get('name', f'空域{idx}')}",
                    "level": "info"
                })
            except Exception as e:
                self._add_to_update_queue({
                    "type": "sim_event",
                    "time": self.simulation_time,
                    "source": "AirspaceManager",
                    "message": f"创建空域资源失败: {str(e)}",
                    "level": "error"
                })
        
        # 如果没有配置空域，添加默认空域
        if not self.config.get("airspaces"):
            try:
                # 使用配置文件中的默认空域设置
                self.env.airspace_manager.create_airspace(**DEFAULT_AIRSPACE)
                self._add_to_update_queue({
                    "type": "sim_event",
                    "time": self.simulation_time,
                    "source": "AirspaceManager",
                    "message": "创建默认空域资源",
                    "level": "info"
                })
            except Exception as e:
                self._add_to_update_queue({
                    "type": "sim_event",
                    "time": self.simulation_time,
                    "source": "AirspaceManager",
                    "message": f"创建默认空域资源失败: {str(e)}",
                    "level": "error"
                })
        
        # 设置频率资源
        for idx, freq_config in enumerate(self.config.get("frequencies", [])):
            try:
                freq_id = self.env.frequency_manager.create_frequency(**freq_config)
                self._add_to_update_queue({
                    "type": "sim_event",
                    "time": self.simulation_time,
                    "source": "FrequencyManager",
                    "message": f"创建频率资源 {freq_config.get('attributes', {}).get('purpose', f'频率{idx}')}",
                    "level": "info"
                })
            except Exception as e:
                self._add_to_update_queue({
                    "type": "sim_event",
                    "time": self.simulation_time,
                    "source": "FrequencyManager",
                    "message": f"创建频率资源失败: {str(e)}",
                    "level": "error"
                })
        
        # 如果没有配置频率，添加默认频率
        if not self.config.get("frequencies"):
            try:
                # 使用配置文件中的默认频率设置
                self.env.frequency_manager.create_frequency(**DEFAULT_FREQUENCY)
                self._add_to_update_queue({
                    "type": "sim_event",
                    "time": self.simulation_time,
                    "source": "FrequencyManager",
                    "message": "创建默认频率资源",
                    "level": "info"
                })
            except Exception as e:
                self._add_to_update_queue({
                    "type": "sim_event",
                    "time": self.simulation_time,
                    "source": "FrequencyManager",
                    "message": f"创建默认频率资源失败: {str(e)}",
                    "level": "error"
                })
        
        # 设置着陆点资源
        for idx, landing_config in enumerate(self.config.get("landing_spots", [])):
            try:
                landing_id = self.env.landing_manager.create_landing_spot(**landing_config)
                self._add_to_update_queue({
                    "type": "sim_event",
                    "time": self.simulation_time,
                    "source": "LandingManager",
                    "message": f"创建着陆点资源 {landing_config.get('attributes', {}).get('name', f'着陆点{idx}')}",
                    "level": "info"
                })
            except Exception as e:
                self._add_to_update_queue({
                    "type": "sim_event",
                    "time": self.simulation_time,
                    "source": "LandingManager",
                    "message": f"创建着陆点资源失败: {str(e)}",
                    "level": "error"
                })
        
        # 如果没有配置着陆点，添加默认着陆点
        if not self.config.get("landing_spots"):
            try:
                # 使用配置文件中的默认着陆点设置
                self.env.landing_manager.create_landing_spot(**DEFAULT_LANDING_SPOT)
                self._add_to_update_queue({
                    "type": "sim_event",
                    "time": self.simulation_time,
                    "source": "LandingManager",
                    "message": "创建默认着陆点资源",
                    "level": "info"
                })
            except Exception as e:
                self._add_to_update_queue({
                    "type": "sim_event",
                    "time": self.simulation_time,
                    "source": "LandingManager",
                    "message": f"创建默认着陆点资源失败: {str(e)}",
                    "level": "error"
                })
    
    def _create_agent_from_config(self, agent_config):
        """从配置创建智能体"""
        if not self.env:
            return None
            
        agent_type = agent_config.get("type", "drone")
        agent_id = agent_config.get("id") or f"agent_{uuid.uuid4().hex[:8]}"
        agent_name = agent_config.get("name", f"智能体{agent_id}")
        
        # 记录智能体创建开始
        self._add_to_update_queue({
            "type": "sim_event",
            "time": self.simulation_time,
            "source": "AgentManager",
            "message": f"开始创建{agent_type}类型智能体: {agent_name}",
            "level": "info"
        })
        
        try:
            if agent_type == "drone":
                agent = self.env.create_agent(
                    DroneAgent,
                    agent_id,
                    agent_id=agent_id,
                    initial_position=agent_config.get("position", (10, 10, 0)),
                    initial_battery=agent_config.get("battery", 100),
                    llm_client=None
                )
                
                # 添加基本组件
                move_component = MoveToComponent(self.env, agent)
                charging_component = ChargingComponent(self.env, agent)
                agent.add_component(move_component)
                agent.add_component(charging_component)
                
                # 记录到活动无人机集合
                self.active_drones.add(agent_id)
                
                # 更新数据服务中的无人机状态
                self.data_service.update_drone_state(
                    drone_id=agent_id,
                    position=agent_config.get("position", (10, 10, 0)),
                    battery_level=agent_config.get("battery", 100),
                    status="idle",
                    speed=0.0,
                    sim_time=self.simulation_time
                )
                
                # 同时更新agents表，确保在前端的智能体列表中显示
                self.data_service.update_agent(
                    agent_id=agent_id,
                    name=agent_name,
                    type_=agent_type,
                    position=agent_config.get("position", (10, 10, 0)),
                    properties={
                        "battery": agent_config.get("battery", 100),
                        "components": agent_config.get("components", []),
                        **agent_config.get("properties", {})
                    }
                )
                
                # 记录到活动智能体字典
                self.active_agents[agent_id] = agent
                
                # 记录成功创建
                self._add_to_update_queue({
                    "type": "sim_event",
                    "time": self.simulation_time,
                    "source": "AgentManager",
                    "message": f"成功创建{agent_type}类型智能体: {agent_name}，位置: {agent_config.get('position', (10, 10, 0))}",
                    "level": "info"
                })
                
                return agent
            
            # 记录不支持的智能体类型
            self._add_to_update_queue({
                "type": "sim_event",
                "time": self.simulation_time,
                "source": "AgentManager",
                "message": f"不支持的智能体类型: {agent_type}",
                "level": "error"
            })
            
        except Exception as e:
            # 记录创建失败
            self._add_to_update_queue({
                "type": "sim_event",
                "time": self.simulation_time,
                "source": "AgentManager",
                "message": f"创建智能体失败: {str(e)}",
                "level": "error"
            })
        
        return None
    
    def _create_workflow_from_config(self, workflow_config, agent):
        """从配置创建工作流"""
        if not self.env or not agent:
            return None
            
        workflow_type = workflow_config.get("type", "inspection")
        workflow_id = workflow_config.get("id") or f"workflow_{uuid.uuid4().hex[:8]}"
        workflow_name = workflow_config.get("name", f"{workflow_type}工作流")
        
        # 记录工作流创建开始
        self._add_to_update_queue({
            "type": "sim_event",
            "time": self.simulation_time,
            "source": "WorkflowManager",
            "message": f"开始为智能体 {agent.id} 创建{workflow_type}类型工作流: {workflow_name}",
            "level": "info"
        })
        
        try:
            if workflow_type == "inspection":
                # 巡检路径工作流
                waypoints = workflow_config.get("waypoints", [])
                if not waypoints:
                    # 默认路径
                    waypoints = [
                        (10, 10, 100),
                        (500, 500, 150),
                        (10, 10, 100),
                        (10, 10, 0)
                    ]
                    self._add_to_update_queue({
                        "type": "sim_event",
                        "time": self.simulation_time,
                        "source": "WorkflowManager",
                        "message": "使用默认巡检路径点",
                        "level": "info"
                    })
                
                workflow = create_inspection_workflow(self.env, agent, waypoints)
                self.active_workflows[workflow_id] = workflow
                
                # 更新数据服务中的工作流状态
                self.data_service.update_workflow(
                    workflow_id=workflow_id,
                    name=workflow_name,
                    type_=workflow_type,
                    agent_id=agent.id,
                    status="pending",
                    details={"waypoints": waypoints}
                )
                
                # 记录成功创建
                self._add_to_update_queue({
                    "type": "sim_event",
                    "time": self.simulation_time,
                    "source": "WorkflowManager",
                    "message": f"成功创建巡检工作流: {workflow_name}，路径点数量: {len(waypoints)}",
                    "level": "info"
                })
                
                return workflow
            
            elif workflow_type == "charging":
                # 充电工作流
                battery_threshold = workflow_config.get("battery_threshold", 30)
                target_charge_level = workflow_config.get("target_level", 90)
                
                # 查找最近的充电站
                position3d = agent.get_state('position')
                nearest_charging_station = self.env.landing_manager.find_nearest_landing_spot(
                    x=position3d[0],
                    y=position3d[1],
                    require_charging=True
                )
                
                if nearest_charging_station:
                    charging_station_location = nearest_charging_station.location
                    self._add_to_update_queue({
                        "type": "sim_event",
                        "time": self.simulation_time,
                        "source": "WorkflowManager",
                        "message": f"找到充电站位置: {charging_station_location}",
                        "level": "info"
                    })
                    
                    workflow = create_charging_workflow(
                        env=self.env,
                        agent=agent,
                        charging_station=charging_station_location,
                        battery_threshold=battery_threshold,
                        target_charge_level=target_charge_level
                    )
                    
                    self.active_workflows[workflow_id] = workflow
                    
                    # 更新数据服务中的工作流状态
                    self.data_service.update_workflow(
                        workflow_id=workflow_id,
                        name=workflow_name,
                        type_=workflow_type,
                        agent_id=agent.id,
                        status="pending",
                        details={
                            "battery_threshold": battery_threshold,
                            "target_charge_level": target_charge_level,
                            "charging_station": charging_station_location
                        }
                    )
                    
                    # 记录成功创建
                    self._add_to_update_queue({
                        "type": "sim_event",
                        "time": self.simulation_time,
                        "source": "WorkflowManager",
                        "message": f"成功创建充电工作流: {workflow_name}，阈值: {battery_threshold}%，目标: {target_charge_level}%",
                        "level": "info"
                    })
                    
                    return workflow
                else:
                    self._add_to_update_queue({
                        "type": "sim_event",
                        "time": self.simulation_time,
                        "source": "WorkflowManager",
                        "message": "未找到可用的充电站，无法创建充电工作流",
                        "level": "error"
                    })
            else:
                self._add_to_update_queue({
                    "type": "sim_event",
                    "time": self.simulation_time,
                    "source": "WorkflowManager",
                    "message": f"不支持的工作流类型: {workflow_type}",
                    "level": "error"
                })
                
        except Exception as e:
            # 记录创建失败
            self._add_to_update_queue({
                "type": "sim_event",
                "time": self.simulation_time,
                "source": "WorkflowManager",
                "message": f"创建工作流失败: {str(e)}",
                "level": "error"
            })
        
        return None
    
    def _add_to_update_queue(self, update_data: Dict[str, Any]):
        """将更新数据添加到队列中"""
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
        """从队列中获取更新，API层调用此方法"""
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
    
    def _update_frontend(self):
        """更新前端数据"""
        # 更新仿真时间
        if self.env:
            self.simulation_time = self.env.now
        
        # 更新无人机状态
        for agent_id, agent in self.active_agents.items():
            if agent_id in self.active_drones:
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
                    sim_time=self.simulation_time
                )
        
        # 准备状态更新数据
        update_data = {
            "type": "sim_status",
            "status": self.simulation_status,
            "time": self.simulation_time,
            "speed": self.simulation_speed
        }
        
        # 不使用asyncio.run，而是将更新放入队列
        # API层会从队列中读取并处理
        self._add_to_update_queue(update_data)
    
    def _simulation_runner(self):
        """运行仿真的线程函数"""
        try:
            logger.info("仿真线程已启动")
            
            # 启动前端更新定时器
            self._start_frontend_updater()
            
            # 不是一次性运行到结束，而是分时间片段执行
            while self.simulation_status == "RUNNING":
                # 计算当前时间片段结束时间
                # 使用simulation_speed控制每次执行的时间长度
                current_time = self.env.now
                next_time = current_time + (0.5 * self.simulation_speed)  # 每次执行0.5秒的仿真时间
                
                try:
                    # 检查是否有事件可执行
                    if self.env.peek() == float('inf'):
                        logger.info("仿真已完成所有事件")
                        self.simulation_status = "COMPLETED"
                        break
                    
                    # 获取当前事件时间
                    current_event_time = self.env.peek()
                    
                    # 如果当前事件时间已经超过了下一个时间片，调整下一个时间片
                    if current_event_time > next_time:
                        logger.debug(f"调整时间片: 当前事件时间 {current_event_time} > 下一时间片 {next_time}")
                        next_time = current_event_time + 0.1  # 稍微超过当前事件时间
                    
                    # 运行仿真到指定时间
                    result = self.env.run(until=next_time)
                    logger.debug(f"仿真执行到时间: {result}, 目标时间: {next_time}")
                    
                    # 添加适当的延时，让前端有时间更新
                    # 延时时间根据仿真速度调整
                    time.sleep(0.05)  # 固定的小延迟，确保UI有时间响应
                    
                except StopSimulation:
                    logger.info("仿真被停止")
                    self.simulation_status = "STOPPED"
                    break
            
            logger.info("仿真完成")
            if self.simulation_status != "STOPPED":
                self.simulation_status = "COMPLETED"
            
            # 最后一次更新前端
            self._update_frontend()
            
        except Exception as e:
            logger.error(f"仿真线程出错: {str(e)}")
            self.simulation_status = "ERROR"
            
            # 记录错误并添加到队列
            logger.error(f"仿真出错: {str(e)}")
            # 将错误事件放入队列
            error_event = {
                "type": "sim_event",
                "time": self.simulation_time,
                "source": "SimulationSystem",
                "message": f"仿真出错: {str(e)}",
                "level": "error"
            }
            self._add_to_update_queue(error_event)
    
    def _start_frontend_updater(self):
        """启动前端更新定时器"""
        # 检查是否已有更新线程在运行
        if hasattr(self, '_updater_running') and self._updater_running:
            logger.warning("前端更新线程已在运行")
            return
        
        self._updater_running = True
        
        def updater():
            try:
                while self.simulation_status in ["RUNNING", "PAUSED"]:
                    self._update_frontend()
                    time.sleep(0.1)  # 每0.1秒更新一次
                
                # 线程结束时清除标志
                self._updater_running = False
                logger.info("前端更新线程已结束")
            except Exception as e:
                logger.error(f"前端更新线程出错: {str(e)}")
                self._updater_running = False
        
        threading.Thread(
            target=updater,
            daemon=True,
            name=f"frontend_updater_{int(time.time())}"  # 添加时间戳以便识别
        ).start()
    
    async def configure_environment(self, config: Dict[str, Any]):
        """配置仿真环境"""
        self.config = config
        
        # 如果环境已存在且仿真正在运行，需要先重置
        if self.env and self.simulation_status != "STOPPED":
            await self.reset_simulation()
        
        # 通知配置变更
        self._add_to_update_queue({
            "type": "sim_event",
            "time": self.simulation_time,
            "source": "SimulationSystem",
            "message": f"仿真环境已配置，包含{len(config.get('agents', []))}个智能体，{len(config.get('workflows', []))}个工作流",
            "level": "info"
        })
        
        return {"status": "success", "message": "环境配置已更新"}
    
    async def add_agent(self, agent_config: Dict[str, Any]) -> str:
        """添加智能体到仿真"""
        agent_id = agent_config.get("id") or f"agent_{uuid.uuid4().hex[:8]}"
        
        if self.env and self.simulation_status != "RUNNING":
            # 如果环境已存在且仿真未运行，直接添加智能体
            agent = self._create_agent_from_config(agent_config)
            if agent:
                # 通知添加成功
                self._add_to_update_queue({
                    "type": "sim_event",
                    "time": self.simulation_time,
                    "source": "AgentManager",
                    "message": f"智能体 {agent_id} 已添加到仿真",
                    "level": "info"
                })
                return agent_id
        elif self.simulation_status == "STOPPED":
            # 如果仿真未启动，存储配置以便稍后创建
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
            self._add_to_update_queue({
                "type": "sim_event",
                "time": self.simulation_time,
                "source": "AgentManager",
                "message": f"智能体 {agent_id} 已配置等待仿真启动",
                "level": "info"
            })
            return agent_id
        
        # 通知无法添加
        self._add_to_update_queue({
            "type": "sim_event",
            "time": self.simulation_time,
            "source": "AgentManager",
            "message": f"无法添加智能体 {agent_id}：仿真正在运行",
            "level": "error"
        })
        return None
    
    async def add_workflow(self, workflow_config: Dict[str, Any]) -> str:
        """添加工作流到仿真"""
        workflow_id = workflow_config.get("id") or f"workflow_{uuid.uuid4().hex[:8]}"
        agent_id = workflow_config.get("agent_id")
        
        if not agent_id:
            self._add_to_update_queue({
                "type": "sim_event",
                "time": self.simulation_time,
                "source": "WorkflowManager",
                "message": "无法创建工作流：缺少agent_id",
                "level": "error"
            })
            return None
        
        if self.env and self.simulation_status != "RUNNING":
            # 如果环境已存在且仿真未运行，直接添加工作流
            agent = self.active_agents.get(agent_id)
            if agent:
                workflow = self._create_workflow_from_config(workflow_config, agent)
                if workflow:
                    # 通知添加成功
                    self._add_to_update_queue({
                        "type": "sim_event",
                        "time": self.simulation_time,
                        "source": "WorkflowManager",
                        "message": f"工作流 {workflow_id} 已添加到仿真",
                        "level": "info"
                    })
                    return workflow_id
        elif self.simulation_status == "STOPPED":
            # 如果仿真未启动，存储配置以便稍后创建
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
            self._add_to_update_queue({
                "type": "sim_event",
                "time": self.simulation_time,
                "source": "WorkflowManager",
                "message": f"工作流 {workflow_id} 已配置等待仿真启动",
                "level": "info"
            })
            return workflow_id
        
        # 将失败事件放入队列
        self._add_to_update_queue({
            "type": "sim_event",
            "time": self.simulation_time,
            "source": "WorkflowManager",
            "message": f"无法添加工作流 {workflow_id}：仿真正在运行或找不到关联智能体",
            "level": "error"
        })
        return None
    
    async def start_simulation(self):
        """启动仿真"""
        if self.simulation_status == "RUNNING":
            logger.warning("仿真已经在运行中")
            return
        
        logger.info("启动仿真...")
        
        if self.simulation_status == "PAUSED" and self.env:
            # 如果是暂停状态，恢复仿真
            self.env.resume()
            self.simulation_status = "RUNNING"
            
            # 将状态变更添加到队列
            self._add_to_update_queue({
                "type": "sim_status",
                "status": self.simulation_status,
                "time": self.simulation_time,
                "speed": self.simulation_speed
            })
            
            # 记录事件并添加到队列
            self._add_to_update_queue({
                "type": "sim_event",
                "time": self.simulation_time,
                "source": "SimulationSystem",
                "message": "仿真已恢复",
                "level": "info"
            })
            return
        
        # 如果是停止状态，初始化新的仿真
        self._setup_environment()
        
        # 从配置创建智能体
        for agent_config in self.config.get("agents", []):
            self._create_agent_from_config(agent_config)
        
        # 从配置创建工作流
        for workflow_config in self.config.get("workflows", []):
            agent_id = workflow_config.get("agent_id")
            if agent_id in self.active_agents:
                self._create_workflow_from_config(
                    workflow_config,
                    self.active_agents[agent_id]
                )
        
        # 启动所有配置的工作流
        for workflow in self.active_workflows.values():
            workflow.start()
        
        # 更新状态
        self.simulation_status = "RUNNING"
        
        # 记录状态变更
        logger.info(f"仿真状态更新: {self.simulation_status}, 时间: {self.simulation_time}")
        
        # 通知状态变化 - 仅直接记录，不使用异步回调
        # 启动回调在API层处理
        
        # 确保之前的线程已经终止
        if self.simulation_thread and self.simulation_thread.is_alive():
            logger.warning("检测到之前的仿真线程仍在运行，尝试终止...")
            # 尝试终止线程
            self.simulation_thread.join(timeout=2.0)
            if self.simulation_thread.is_alive():
                logger.error("无法终止之前的仿真线程，可能会导致多线程问题")
        
        # 在单独线程中运行仿真
        self.simulation_thread = threading.Thread(
            target=self._simulation_runner,
            daemon=True,
            name=f"simulation_thread_{int(time.time())}"  # 添加时间戳，便于区分
        )
        self.simulation_thread.start()
    
    async def pause_simulation(self):
        """暂停仿真"""
        if self.simulation_status != "RUNNING" or not self.env:
            logger.warning("仿真未在运行，无法暂停")
            return
        
        logger.info("暂停仿真...")
        
        # 暂停环境
        self.env.pause_now()
        self.simulation_status = "PAUSED"
        # 记录状态变更，但不直接调用异步回调
        logger.info(f"仿真已暂停，时间: {self.simulation_time}")
    
    async def resume_simulation(self):
        """恢复仿真，实际上调用start_simulation"""
        await self.start_simulation()
    
    async def reset_simulation(self):
        """重置仿真"""
        logger.info("重置仿真...")
        
        # 首先更改状态，防止线程继续执行
        self.simulation_status = "STOPPED"
        
        # 停止仿真线程
        if self.simulation_thread and self.simulation_thread.is_alive():
            logger.info(f"正在终止仿真线程: {self.simulation_thread.name}")
            
            if self.env:
                # 通过暂停环境来尝试停止线程
                self.env.pause_now()
                # 设置一个很短的目标时间，促使仿真尽快结束
                if hasattr(self.env, '_target_time'):
                    self.env._target_time = self.env.now
            
            # 等待线程结束，给予更长的超时时间
            self.simulation_thread.join(timeout=3.0)
            
            # 检查线程是否仍在运行
            if self.simulation_thread.is_alive():
                logger.warning("仿真线程未能在超时时间内终止，可能会导致资源泄漏")
        
        # 清除前端更新线程标志
        if hasattr(self, '_updater_running'):
            self._updater_running = False
            logger.info("已清除前端更新线程标志")
        
        # 清空仿真状态
        self.env = None
        self.simulation_thread = None
        self.simulation_time = 0.0
        
        # 清空活动实体
        self.active_drones.clear()
        self.active_workflows.clear()
        self.active_agents.clear()
        
        # 记录状态变更，不使用异步回调
        logger.info("仿真已重置")
    
    async def set_simulation_speed(self, speed: float):
        """设置仿真速度"""
        if speed <= 0:
            logger.warning("仿真速度必须大于0")
            return
        
        logger.info(f"设置仿真速度为 {speed}x")
        self.simulation_speed = speed
        
        # 将速度设置应用到环境中
        if self.env and hasattr(self.env, 'set_speed'):
            try:
                self.env.set_speed(speed)
                logger.info(f"已将速度 {speed}x 应用到仿真环境")
            except Exception as e:
                logger.error(f"设置仿真环境速度时出错: {str(e)}")
        
        # 通知状态变化
        self._add_to_update_queue({
            "type": "sim_status",
            "status": self.simulation_status,
            "time": self.simulation_time,
            "speed": self.simulation_speed
        })
    
    async def delete_workflow(self, workflow_id: str) -> bool:
        """删除工作流"""
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
            self._add_to_update_queue({
                "type": "sim_event",
                "time": self.simulation_time,
                "source": "WorkflowManager",
                "message": f"删除工作流 (ID: {workflow_id})",
                "level": "info"
            })
        
        return success
    
    async def delete_agent(self, agent_id: str) -> bool:
        """删除智能体"""
        # 检查是否是无人机
        is_drone = agent_id in self.active_drones
        
        if agent_id in self.active_agents:
            # 如果仿真正在运行，可能需要特殊处理
            if self.env and self.simulation_status == "RUNNING":
                # 复杂情况：正在运行的智能体可能无法安全移除
                logger.warning(f"仿真正在运行，无法安全移除智能体 {agent_id}")
                return False
            
            # 移除智能体
            del self.active_agents[agent_id]
        
        # 从相关集合中移除
        if is_drone:
            self.active_drones.discard(agent_id)
        
        # 从数据服务中删除
        success = self.data_service.delete_agent(agent_id)
        
        if success:
            # 记录事件
            self._add_to_update_queue({
                "type": "sim_event",
                "time": self.simulation_time,
                "source": "AgentManager",
                "message": f"删除智能体 (ID: {agent_id})",
                "level": "info"
            })
        
        return success
    
    async def start_heartbeat(self):
        """启动WebSocket心跳"""
        while True:
            await asyncio.sleep(30)  # 每30秒发送一次心跳
            self._add_to_update_queue({
                "type": "heartbeat",
                "time": self.simulation_time
            })