#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
使用SUMO作为交通数据源的AirFogSim示例。

此示例展示了如何将SUMO交通仿真器与AirFogSim集成，
让无人机能够监控地面车辆的移动。

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import os
from airfogsim.utils.logging_config import get_logger
import time
from typing import Dict, Any
import subprocess
import signal
import sys

from airfogsim.core.environment import Environment
from airfogsim.dataprovider.traffic import TrafficDataProvider
from airfogsim.agent.drone import DroneAgent

# 配置日志
logger = get_logger(__name__)

# 创建一个简单的无人机代理，用于监听交通事件
class TrafficMonitorDrone(DroneAgent):
    """监控地面交通的无人机。"""
    
    def __init__(self, env, agent_name, properties=None):
        super().__init__(env, agent_name, properties)
        self.nearby_vehicles = set()
        
        # 订阅交通更新事件
        self.env.event_registry.subscribe(
            'TrafficDataProvider',
            'TrafficUpdate',
            f"{self.id}_traffic_monitor",
            self._handle_traffic_update
        )
        
    def _handle_traffic_update(self, event_data):
        """处理交通更新事件。"""
        vehicle_id = event_data.get('id')
        vehicle_pos = event_data.get('position')
        vehicle_type = event_data.get('type')
        
        if not vehicle_pos:
            return
        
        # 获取无人机位置
        drone_pos = self.get_state('position')
        if not drone_pos:
            return
        
        # 计算距离
        from airfogsim.core.utils import calculate_distance
        distance = calculate_distance(
            (drone_pos[0], drone_pos[1], drone_pos[2]),
            (vehicle_pos[0], vehicle_pos[1], vehicle_pos[2])
        )
        
        # 如果车辆在监控范围内
        monitor_radius = 100.0  # 100米监控半径
        if distance <= monitor_radius:
            if vehicle_id not in self.nearby_vehicles:
                self.nearby_vehicles.add(vehicle_id)
                logger.info(f"无人机 {self.id} 发现新车辆: {vehicle_id}（类型: {vehicle_type}）在 {distance:.1f}m 处")
        elif vehicle_id in self.nearby_vehicles:
            self.nearby_vehicles.remove(vehicle_id)
            logger.info(f"车辆 {vehicle_id} 离开了无人机 {self.id} 的监控范围")

def start_sumo(sumocfg_path, port=8813, gui=True):
    """
    启动SUMO仿真器作为TraCI服务器。
    
    Args:
        sumocfg_path: SUMO配置文件路径
        port: TraCI服务器端口
        gui: 是否使用GUI版本
        
    Returns:
        subprocess.Popen: SUMO进程
    """
    sumo_binary = "sumo-gui" if gui else "sumo"
    
    # 检查SUMO是否已安装
    try:
        subprocess.run([sumo_binary, "--version"], stdout=subprocess.PIPE, check=True)
    except (subprocess.SubprocessError, FileNotFoundError):
        logger.error(f"未找到{sumo_binary}。请确保SUMO已正确安装。")
        sys.exit(1)
    
    # 启动SUMO
    cmd = [
        sumo_binary,
        "-c", sumocfg_path,
        "--remote-port", str(port),
        "--start",  # 自动开始仿真
        "--quit-on-end"  # 仿真结束时退出
    ]
    
    logger.info(f"启动SUMO: {' '.join(cmd)}")
    
    # 在Windows上，使用CREATE_NEW_CONSOLE以便在新窗口中启动SUMO-GUI
    if sys.platform == 'win32' and gui:
        CREATE_NEW_CONSOLE = 0x00000010
        return subprocess.Popen(cmd, creationflags=CREATE_NEW_CONSOLE)
    else:
        return subprocess.Popen(cmd)

def run_simulation():
    """运行集成了SUMO的AirFogSim仿真。"""
    logger.info("=== 启动基于SUMO的交通仿真 ===")
    
    # SUMO配置文件路径
    sumocfg_path = os.path.abspath("frontend/public/data/traffic/sumocfg/osm_generated.sumocfg")
    if not os.path.exists(sumocfg_path):
        logger.error(f"找不到SUMO配置文件: {sumocfg_path}")
        return
    
    # 启动SUMO
    sumo_port = 8813
    sumo_process = start_sumo(sumocfg_path, port=sumo_port, gui=False)
    
    try:
        # 给SUMO一些时间启动
        logger.info("等待SUMO启动...")
        time.sleep(5)
        
        # 创建仿真环境
        env = Environment()
        
        # 配置TrafficDataProvider
        traffic_config = {
            'source': 'sumo',
            'update_interval': 1.0,
            'sumo_config': {
                'host': 'localhost',
                'port': sumo_port
            }
        }
        
        # 创建并注册TrafficDataProvider
        traffic_provider = TrafficDataProvider(env, traffic_config)
        env.add_data_provider('traffic', traffic_provider)
                
        try:
            # 加载数据并启动事件触发
            traffic_provider.load_data()
            traffic_provider.start_event_triggering()
            
            # 运行仿真
            logger.info("开始运行仿真...")
            env.run(until=60)  # 运行60秒
            logger.info(f"仿真结束")
        except ConnectionError as e:
            logger.error(f"无法连接到SUMO: {e}")
        finally:
            # 清理资源
            if hasattr(traffic_provider, 'close'):
                traffic_provider.close()
    
    finally:
        # 终止SUMO进程
        if sumo_process:
            logger.info("终止SUMO进程...")
            sumo_process.terminate()
            try:
                sumo_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("SUMO进程未能正常终止，强制结束。")
                if sys.platform != 'win32':
                    os.killpg(os.getpgid(sumo_process.pid), signal.SIGTERM)
                else:
                    sumo_process.kill()

if __name__ == "__main__":
    run_simulation()