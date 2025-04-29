import uuid
from typing import Dict, Any, Callable, Optional, List, Tuple

from airfogsim.agent import DroneAgent
from airfogsim.component import MoveToComponent, ChargingComponent
from airfogsim.workflow.inspection import create_inspection_workflow
from airfogsim.workflow.charging import create_charging_workflow

from .environment import PausableEnvironment
from .config import DEFAULT_AIRSPACE, DEFAULT_FREQUENCY, DEFAULT_LANDING_SPOT
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

def setup_environment_resources(env: PausableEnvironment, config: Dict[str, Any], log_event: Callable = None) -> bool:
    """设置仿真环境中的资源
    
    Args:
        env: 仿真环境
        config: 环境配置
        log_event: 事件记录回调函数
    
    Returns:
        bool: 设置是否成功
    """
    if not env:
        logger.error("无法设置资源：环境未初始化")
        return False
    
    # 记录日志
    if log_event:
        log_event("ResourceManager", "开始配置仿真环境资源")
    
    # 设置空域资源
    setup_airspaces(env, config.get("airspaces", []), log_event)
    
    # 设置频率资源
    setup_frequencies(env, config.get("frequencies", []), log_event)
    
    # 设置着陆点资源
    setup_landing_spots(env, config.get("landing_spots", []), log_event)
    
    return True

def setup_airspaces(env: PausableEnvironment, airspaces: List[Dict[str, Any]], log_event: Callable = None) -> None:
    """设置空域资源"""
    for idx, airspace_config in enumerate(airspaces):
        try:
            airspace_id = env.airspace_manager.create_airspace(**airspace_config)
            if log_event:
                log_event(
                    "AirspaceManager", 
                    f"创建空域资源 {airspace_config.get('attributes', {}).get('name', f'空域{idx}')}"
                )
        except Exception as e:
            if log_event:
                log_event(
                    "AirspaceManager", 
                    f"创建空域资源失败: {str(e)}", 
                    "error"
                )
    
    # 如果没有配置空域，添加默认空域
    if not airspaces:
        try:
            # 使用配置文件中的默认空域设置
            env.airspace_manager.create_airspace(**DEFAULT_AIRSPACE)
            if log_event:
                log_event("AirspaceManager", "创建默认空域资源")
        except Exception as e:
            if log_event:
                log_event(
                    "AirspaceManager", 
                    f"创建默认空域资源失败: {str(e)}", 
                    "error"
                )

def setup_frequencies(env: PausableEnvironment, frequencies: List[Dict[str, Any]], log_event: Callable = None) -> None:
    """设置频率资源"""
    for idx, freq_config in enumerate(frequencies):
        try:
            freq_id = env.frequency_manager.create_frequency(**freq_config)
            if log_event:
                log_event(
                    "FrequencyManager", 
                    f"创建频率资源 {freq_config.get('attributes', {}).get('purpose', f'频率{idx}')}"
                )
        except Exception as e:
            if log_event:
                log_event(
                    "FrequencyManager", 
                    f"创建频率资源失败: {str(e)}", 
                    "error"
                )
    
    # 如果没有配置频率，添加默认频率
    if not frequencies:
        try:
            # 使用配置文件中的默认频率设置
            env.frequency_manager.create_frequency(**DEFAULT_FREQUENCY)
            if log_event:
                log_event("FrequencyManager", "创建默认频率资源")
        except Exception as e:
            if log_event:
                log_event(
                    "FrequencyManager", 
                    f"创建默认频率资源失败: {str(e)}", 
                    "error"
                )

def setup_landing_spots(env: PausableEnvironment, landing_spots: List[Dict[str, Any]], log_event: Callable = None) -> None:
    """设置着陆点资源"""
    for idx, landing_config in enumerate(landing_spots):
        try:
            landing_id = env.landing_manager.create_landing_spot(**landing_config)
            if log_event:
                log_event(
                    "LandingManager", 
                    f"创建着陆点资源 {landing_config.get('attributes', {}).get('name', f'着陆点{idx}')}"
                )
        except Exception as e:
            if log_event:
                log_event(
                    "LandingManager", 
                    f"创建着陆点资源失败: {str(e)}", 
                    "error"
                )
    
    # 如果没有配置着陆点，添加默认着陆点
    if not landing_spots:
        try:
            # 使用配置文件中的默认着陆点设置
            env.landing_manager.create_landing_spot(**DEFAULT_LANDING_SPOT)
            if log_event:
                log_event("LandingManager", "创建默认着陆点资源")
        except Exception as e:
            if log_event:
                log_event(
                    "LandingManager", 
                    f"创建默认着陆点资源失败: {str(e)}", 
                    "error"
                )

def create_agent_from_config(env: PausableEnvironment, agent_config: Dict[str, Any], 
                             log_event: Callable = None, data_service = None) -> Any:
    """从配置创建智能体
    
    Args:
        env: 仿真环境
        agent_config: 智能体配置
        log_event: 事件记录回调函数
        data_service: 数据服务，用于更新数据库
    
    Returns:
        Any: 创建的智能体对象，失败则返回None
    """
    if not env:
        logger.error("无法创建智能体：环境未初始化")
        return None
    
    agent_type = agent_config.get("type", "drone")
    agent_id = agent_config.get("id") or f"agent_{uuid.uuid4().hex[:8]}"
    agent_name = agent_config.get("name", f"智能体{agent_id}")
    
    # 记录智能体创建开始
    if log_event:
        log_event("AgentManager", f"开始创建{agent_type}类型智能体: {agent_name}")
    
    try:
        if agent_type == "drone":
            agent = env.create_agent(
                DroneAgent,
                agent_id,
                agent_id=agent_id,
                initial_position=agent_config.get("position", (10, 10, 0)),
                initial_battery=agent_config.get("battery", 100),
                llm_client=None
            )
            
            # 添加基本组件
            move_component = MoveToComponent(env, agent)
            charging_component = ChargingComponent(env, agent)
            agent.add_component(move_component)
            agent.add_component(charging_component)
            
            # 如果提供了数据服务，更新数据库
            if data_service:
                # 更新无人机状态
                data_service.update_drone_state(
                    drone_id=agent_id,
                    position=agent_config.get("position", (10, 10, 0)),
                    battery_level=agent_config.get("battery", 100),
                    status="idle",
                    speed=0.0,
                    sim_time=env.now
                )
                
                # 同时更新agents表，确保在前端的智能体列表中显示
                data_service.update_agent(
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
            
            # 记录成功创建
            if log_event:
                log_event(
                    "AgentManager", 
                    f"成功创建{agent_type}类型智能体: {agent_name}，位置: {agent_config.get('position', (10, 10, 0))}"
                )
            
            return agent
        
        # 记录不支持的智能体类型
        if log_event:
            log_event(
                "AgentManager", 
                f"不支持的智能体类型: {agent_type}", 
                "error"
            )
        
    except Exception as e:
        # 记录创建失败
        if log_event:
            log_event(
                "AgentManager", 
                f"创建智能体失败: {str(e)}", 
                "error"
            )
    
    return None

def create_workflow_from_config(env: PausableEnvironment, workflow_config: Dict[str, Any], 
                                agent: Any, log_event: Callable = None, data_service = None) -> Any:
    """从配置创建工作流
    
    Args:
        env: 仿真环境
        workflow_config: 工作流配置
        agent: 关联的智能体
        log_event: 事件记录回调函数
        data_service: 数据服务，用于更新数据库
    
    Returns:
        Any: 创建的工作流对象，失败则返回None
    """
    if not env or not agent:
        logger.error("无法创建工作流：环境或智能体未初始化")
        return None
    
    workflow_type = workflow_config.get("type", "inspection")
    workflow_id = workflow_config.get("id") or f"workflow_{uuid.uuid4().hex[:8]}"
    workflow_name = workflow_config.get("name", f"{workflow_type}工作流")
    
    # 记录工作流创建开始
    if log_event:
        log_event(
            "WorkflowManager", 
            f"开始为智能体 {agent.id} 创建{workflow_type}类型工作流: {workflow_name}"
        )
    
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
                if log_event:
                    log_event("WorkflowManager", "使用默认巡检路径点")
            
            workflow = create_inspection_workflow(env, agent, waypoints)
            
            # 如果提供了数据服务，更新数据库
            if data_service:
                data_service.update_workflow(
                    workflow_id=workflow_id,
                    name=workflow_name,
                    type_=workflow_type,
                    agent_id=agent.id,
                    status="pending",
                    details={"waypoints": waypoints}
                )
            
            # 记录成功创建
            if log_event:
                log_event(
                    "WorkflowManager", 
                    f"成功创建巡检工作流: {workflow_name}，路径点数量: {len(waypoints)}"
                )
            
            return workflow
        
        elif workflow_type == "charging":
            # 充电工作流
            battery_threshold = workflow_config.get("battery_threshold", 30)
            target_charge_level = workflow_config.get("target_level", 90)
            
            # 查找最近的充电站
            position3d = agent.get_state('position')
            nearest_charging_station = env.landing_manager.find_nearest_landing_spot(
                x=position3d[0],
                y=position3d[1],
                require_charging=True
            )
            
            if nearest_charging_station:
                charging_station_location = nearest_charging_station.location
                if log_event:
                    log_event(
                        "WorkflowManager", 
                        f"找到充电站位置: {charging_station_location}"
                    )
                
                workflow = create_charging_workflow(
                    env=env,
                    agent=agent,
                    charging_station=charging_station_location,
                    battery_threshold=battery_threshold,
                    target_charge_level=target_charge_level
                )
                
                # 如果提供了数据服务，更新数据库
                if data_service:
                    data_service.update_workflow(
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
                if log_event:
                    log_event(
                        "WorkflowManager", 
                        f"成功创建充电工作流: {workflow_name}，阈值: {battery_threshold}%，目标: {target_charge_level}%"
                    )
                
                return workflow
            else:
                if log_event:
                    log_event(
                        "WorkflowManager", 
                        "未找到可用的充电站，无法创建充电工作流", 
                        "error"
                    )
        else:
            if log_event:
                log_event(
                    "WorkflowManager", 
                    f"不支持的工作流类型: {workflow_type}", 
                    "error"
                )
            
    except Exception as e:
        # 记录创建失败
        if log_event:
            log_event(
                "WorkflowManager", 
                f"创建工作流失败: {str(e)}", 
                "error"
            )
    
    return None