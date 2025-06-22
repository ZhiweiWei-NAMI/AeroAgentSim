# -*- coding: utf-8 -*-
from __future__ import annotations
from airfogsim.utils.logging_config import get_logger
import simpy
from typing import TYPE_CHECKING, Dict, Any, Optional, List, Tuple, Set
import time

from ..core.dataprovider import DataProvider

# Type hinting
if TYPE_CHECKING:
    from airfogsim.core.environment import Environment
    # from airfogsim.core.agent import Agent # No longer needed for on_traffic_update
    # from airfogsim.manager.landing import LandingManager # No longer needed for on_traffic_update
    from airfogsim.manager.airspace import AirspaceManager # Import AirspaceManager

# Configure logging
logger = get_logger(__name__)

class TrafficDataProvider(DataProvider):
    """
    提供地面车辆交通数据，触发车辆位置更新事件。
    
    支持两种数据源：
    1. SUMO 交通仿真器（通过 TraCI 接口）
    2. 轨迹文件（CSV 格式）
    """
    # EVENT_TRAFFIC_UPDATE = 'TrafficUpdate' # Removed, no longer using events

    def __init__(self, env: 'Environment', config: Optional[Dict[str, Any]] = None):
        """
        初始化 TrafficDataProvider。

        Args:
            env: 仿真环境实例。
            config (dict, optional): 配置字典，支持以下键：
                - 'source': 数据源类型，'sumo' 或 'file'。
                - 'update_interval' (float, optional): 更新间隔（秒），主要用于 SUMO 数据源。默认为 1.0 秒。
                - 'sumo_config' (dict, 当 source='sumo' 时必需):
                    - 'host': (str, 默认='localhost') SUMO TraCI 服务器主机。
                    - 'port': (int, 默认=8813) SUMO TraCI 服务器端口。
                    - 'vehicle_filter' (dict, 可选): 过滤 SUMO 车辆（例如，{'type': 'passenger'}）。
                - 'file_config' (dict, 当 source='file' 时必需):
                    - 'path': (str) 轨迹文件路径。
                    - 'format': (str, 默认='csv') 文件格式（目前支持 'csv'）。
                    - 'time_column': (str, 默认='timestamp') 时间列名。
                    - 'id_column': (str, 默认='vehicle_id') 车辆 ID 列名。
                    - 'pos_columns': (list[str], 默认=['x', 'y', 'z']) 位置列名。
                    - 'speed_column': (str, 可选) 速度列名（米/秒）。
                    - 'angle_column': (str, 可选) 方向角列名（度）。
                    - 'type_column': (str, 可选) 车辆类型列名。
                - 'center_coordinates' (dict, 可选，但推荐用于SUMO坐标转换):
                    - 'lat': (float) 地图中心点纬度。
                    - 'lon': (float) 地图中心点经度。
        """
        super().__init__(env, config)
        self.source = self.config.get('source')
        self.update_interval = float(env.visual_interval)

        if self.source not in ['sumo', 'file']:
            raise ValueError("TrafficDataProvider 'source' 必须是 'sumo' 或 'file'")

        # 内部状态
        self._traci_conn = None  # SUMO 连接
        self._traffic_schedule: Dict[float, List[Dict[str, Any]]] = {}  # 文件数据源的调度表：{sim_time: [vehicle_state_dict, ...]}
        # 添加存储车辆速度和角度的数据结构
        self._vehicle_dynamics: Dict[str, Dict[str, float]] = {} # 存储车辆的速度和角度: {vehicle_id: {'speed': speed, 'angle': angle}}
        self._known_sumo_vehicles: Set[str] = set() # Track vehicles registered in AirspaceManager from SUMO
        self._known_file_vehicles: Set[str] = set() # Track vehicles registered in AirspaceManager from file
        self._center_lat: Optional[float] = None
        self._center_lon: Optional[float] = None
        self._sumo_conv_boundary: Optional[Tuple[float, float, float, float]] = None # (min_x, min_y, max_x, max_y)

        # 验证特定配置
        if self.source == 'sumo':
            if 'sumo_config' not in self.config:
                raise ValueError("使用 'sumo' 数据源时必须提供 'sumo_config'")
            self._sumo_cfg = self.config['sumo_config']
            # 在这里导入 traci 以避免不使用 SUMO 时的依赖
            try:
                global traci
                import traci
            except ImportError:
                logger.error("未找到 Python 库 'traci'。请安装它以使用 SUMO 数据源。")
                raise
            # 获取中心点坐标
            center_coords = self.config.get('center_coordinates')
            if center_coords and 'lat' in center_coords and 'lon' in center_coords:
                self._center_lat = float(center_coords['lat'])
                self._center_lon = float(center_coords['lon'])
                logger.info(f"已配置地图中心点: Lat={self._center_lat}, Lon={self._center_lon}")
            else:
                logger.warning("未在配置中找到 'center_coordinates'。SUMO 坐标将不会相对于中心点转换。")

        elif self.source == 'file':
            if 'file_config' not in self.config:
                raise ValueError("使用 'file' 数据源时必须提供 'file_config'")
            self._file_cfg = self.config['file_config']
            # 在 load_data 中验证文件配置详情

        # 获取 AirspaceManager 实例
        try:
            self.airspace_manager: Optional['AirspaceManager'] = self.env.airspace_manager
            if not self.airspace_manager:
                 logger.warning("AirspaceManager 未在环境中找到。车辆位置将不会被注册或更新。")
        except AttributeError:
             logger.warning("环境对象没有 'get_manager' 方法或 AirspaceManager 未注册。车辆位置将不会被注册或更新。")
             self.airspace_manager = None

    def load_data(self):
        """根据配置的数据源加载交通数据。"""
        logger.info(f"从数据源加载交通数据: {self.source}")
        if self.source == 'sumo':
            self._connect_sumo()
        elif self.source == 'file':
            self._load_from_file()

    def start_event_triggering(self):
        """启动 SimPy 进程以触发 TrafficUpdate 事件。"""
        if self.source == 'sumo':
            if not self._traci_conn:
                logger.error("SUMO 连接未建立。无法启动事件触发。")
                return
            logger.info(f"启动 SUMO 更新循环，间隔: {self.update_interval}秒")
            self.env.process(self._sumo_update_loop())
        elif self.source == 'file':
            if not self._traffic_schedule:
                logger.warning("从文件加载的交通调度表为空。不会触发任何事件。")
                return
            logger.info("启动基于文件的交通更新循环。")
            self.env.process(self._file_update_loop())

    # --- SUMO 特定方法 ---
    def _connect_sumo(self):
        """建立与 SUMO TraCI 服务器的连接。"""
        host = self._sumo_cfg.get('host', 'localhost')
        port = self._sumo_cfg.get('port', 8813)
        try:
            logger.info(f"连接到 SUMO TraCI 服务器 {host}:{port}...")
            # 考虑添加重试逻辑？
            self._traci_conn = traci.connect(port=port, host=host, label=f"TrafficDataProvider_{time.time()}")
            logger.info("成功连接到 SUMO。")
            # 获取并存储边界信息
            try:
                # 返回 ((min_x, min_y), (max_x, max_y))
                conv_bound_tuple = self._traci_conn.simulation.getNetBoundary()
                self._sumo_conv_boundary = (conv_bound_tuple[0][0], conv_bound_tuple[0][1], conv_bound_tuple[1][0], conv_bound_tuple[1][1])
                
                logger.info(f"获取到 SUMO 边界: Conv={self._sumo_conv_boundary}")
            except Exception as bound_error:
                logger.warning(f"无法获取 SUMO 边界信息: {bound_error}. 坐标转换可能不准确。")
                self._sumo_conv_boundary = None

            time.sleep(1) # 减少等待时间
        except Exception as e:
            logger.error(f"无法连接到 SUMO TraCI 服务器: {e}", exc_info=True)
            self._traci_conn = None
            # 决定是否应该抛出错误或只是记录
            raise ConnectionError(f"无法连接到 SUMO {host}:{port}") from e
            
    def _cleanup_vehicles(self):
        """
        清理所有已注册的车辆，从 AirspaceManager 和内部状态中移除它们
        """
        if self.airspace_manager:
            # 清理 SUMO 车辆
            for veh_id in list(self._known_sumo_vehicles):  # 创建副本避免在迭代时修改
                vehicle_id = f'vehicle_{veh_id}'
                self.airspace_manager.remove_object(agent_id=vehicle_id)
                self._known_sumo_vehicles.remove(veh_id)
                # 从速度和角度字典中移除
                if vehicle_id in self._vehicle_dynamics:
                    del self._vehicle_dynamics[vehicle_id]
            
            # 清理文件数据车辆
            for veh_id in list(self._known_file_vehicles):  # 创建副本避免在迭代时修改
                vehicle_id = f'vehicle_{veh_id}'
                self.airspace_manager.remove_object(agent_id=vehicle_id)
                self._known_file_vehicles.remove(veh_id)
                # 从速度和角度字典中移除
                if vehicle_id in self._vehicle_dynamics:
                    del self._vehicle_dynamics[vehicle_id]

    def _sumo_update_loop(self):
        """SimPy 进程，定期查询 SUMO 并触发事件。"""
        vehicle_filter = self._sumo_cfg.get('vehicle_filter')  # 实现过滤逻辑
        
        if not self.airspace_manager:
            logger.error("AirspaceManager 不可用，无法启动 SUMO 更新循环。")
            return

        # 检查SUMO是否设置了结束时间
        try:
            sumo_end_time = self._traci_conn.simulation.getEndTime()
            logger.info(f"SUMO 配置的结束时间: {sumo_end_time} 秒")
        except Exception as e:
            logger.warning(f"无法获取 SUMO 结束时间: {e}")
            sumo_end_time = float('inf')

        while True:
            try:
                # 1. 推进 SUMO 仿真时间
                # 我们根据 SimPy 等待的时间量推进 SUMO。
                # 这使 SUMO 时间与 SimPy 时间大致保持一致。
                target_sumo_time = self.env.now  # SUMO 时间应对应的当前 SimPy 时间
                
                try:
                    current_sumo_time = self._traci_conn.simulation.getTime()
                    logger.debug(f"当前 SUMO 时间: {current_sumo_time:.2f}，目标时间: {target_sumo_time:.2f}")
                except Exception as e:
                    if "connection closed by SUMO" in str(e).lower():
                        # SUMO可能已经到达结束时间或被外部关闭
                        logger.error(f"SUMO连接已关闭: {e}，尝试重新连接...")
                        try:
                            # 尝试重新连接SUMO
                            self._connect_sumo()
                            if not self._traci_conn:
                                logger.error("无法重新连接到SUMO，退出更新循环")
                                break
                            current_sumo_time = self._traci_conn.simulation.getTime()
                            logger.info(f"已重新连接到SUMO，当前时间: {current_sumo_time:.2f}")
                        except Exception as reconnect_error:
                            logger.error(f"重新连接SUMO失败: {reconnect_error}")
                            break
                    else:
                        # 其他类型的错误
                        logger.error(f"获取SUMO时间时出错: {e}")
                        break
                
                # 检查是否接近SUMO结束时间
                if sumo_end_time != float('inf') and current_sumo_time >= sumo_end_time - 10 and sumo_end_time!=-1:
                    logger.warning(f"SUMO接近结束时间({sumo_end_time})，当前时间: {current_sumo_time}")
                
                # 只有当 SUMO 时间落后于目标 SimPy 时间时才推进 SUMO
                if current_sumo_time < target_sumo_time:
                    self._traci_conn.simulationStep(target_sumo_time)
                    current_sumo_time = self._traci_conn.simulation.getTime()  # 步进后更新 SUMO 时间

                # 2. 获取车辆数据
                current_vehicle_ids = set(self._traci_conn.vehicle.getIDList())
                current_time = self.env.now  # 使用 SimPy 时间作为时间戳

                # --- 检测车辆变化 ---
                new_vehicles = current_vehicle_ids - self._known_sumo_vehicles
                departed_vehicles = self._known_sumo_vehicles - current_vehicle_ids
                existing_vehicles = current_vehicle_ids.intersection(self._known_sumo_vehicles)

                # --- 处理离开的车辆 ---
                for veh_id in departed_vehicles:
                    logger.debug(f"车辆 {veh_id} 已离开 SUMO 仿真，从 AirspaceManager 移除。")
                    self.airspace_manager.remove_object(agent_id=f'vehicle_{veh_id}')
                    self._known_sumo_vehicles.remove(veh_id)
                    # 从速度和角度字典中移除
                    if f'vehicle_{veh_id}' in self._vehicle_dynamics:
                        del self._vehicle_dynamics[f'vehicle_{veh_id}']

                # --- 处理新车辆和现有车辆 ---
                vehicles_to_process = new_vehicles.union(existing_vehicles)
                for veh_id in vehicles_to_process:
                    # 应用 vehicle_filter（如果定义）
                    if vehicle_filter:
                        try:
                            veh_type = self._traci_conn.vehicle.getTypeID(veh_id)
                            if 'type' in vehicle_filter and veh_type != vehicle_filter['type']:
                                continue # 跳过不匹配的车辆类型
                        except traci.exceptions.TraCIException as e:
                             if "does not exist" in str(e):
                                logger.debug(f"尝试过滤时车辆 {veh_id} 已离开。")
                                if veh_id in self._known_sumo_vehicles:
                                    self.airspace_manager.remove_object(agent_id=f'vehicle_{veh_id}')
                                    self._known_sumo_vehicles.remove(veh_id)
                                    # 从速度和角度字典中移除
                                    if f'vehicle_{veh_id}' in self._vehicle_dynamics:
                                        del self._vehicle_dynamics[f'vehicle_{veh_id}']
                                continue
                             else:
                                logger.warning(f"获取车辆 {veh_id} 类型时出错: {e}")
                                continue

                    try:
                        pos = self._traci_conn.vehicle.getPosition(veh_id)  # SUMO 坐标中的 (x, y)
                        speed = self._traci_conn.vehicle.getSpeed(veh_id)  # 米/秒
                        angle = self._traci_conn.vehicle.getAngle(veh_id)  # 度（0=北，90=东）
                        veh_type = self._traci_conn.vehicle.getTypeID(veh_id)

                        # 转换坐标
                        source_pos = (pos[0], pos[1], 0.0)  # 假设 SUMO 中 Z=0
                        sim_pos = self._convert_coordinates(source_pos)

                        # --- 更新或注册车辆到 AirspaceManager 并存储速度和角度 ---
                        vehicle_id = f"vehicle_{veh_id}"
                        # 更新速度和角度信息
                        self._vehicle_dynamics[vehicle_id] = {
                            'speed': speed,
                            'angle': angle,
                            'type': veh_type
                        }
                        
                        if veh_id in new_vehicles:
                            logger.debug(f"新车辆 {veh_id} 检测到，注册到 AirspaceManager。位置: {sim_pos}")
                            self.airspace_manager.register_object(position=sim_pos, agent_id=vehicle_id)
                            self._known_sumo_vehicles.add(veh_id)
                        elif veh_id in existing_vehicles:
                            # logger.debug(f"更新车辆 {veh_id} 位置到 AirspaceManager。位置: {sim_pos}")
                            self.airspace_manager.update_object_position(position=sim_pos, agent_id=vehicle_id)
                        # else: # Should not happen due to logic above
                        #    logger.warning(f"车辆 {veh_id} 状态未知，既不是新的也不是现有的。")

                    except Exception as e:
                        # 车辆可能在 getIDList 和 getPosition 之间离开仿真
                       if "does not exist" in str(e):
                           logger.debug(f"获取车辆 {veh_id} 数据时，它已离开仿真。从 AirspaceManager 移除。")
                           if veh_id in self._known_sumo_vehicles:
                               vehicle_id = f'vehicle_{veh_id}'
                               self.airspace_manager.remove_object(agent_id=vehicle_id)
                               self._known_sumo_vehicles.remove(veh_id)
                               # 从速度和角度字典中移除
                               if vehicle_id in self._vehicle_dynamics:
                                   del self._vehicle_dynamics[vehicle_id]
                       else:
                            logger.warning(f"获取车辆 {veh_id} 数据时出错: {e}")

               # 3. 更新 known_sumo_vehicles 状态
               # self._known_sumo_vehicles = current_vehicle_ids # Done implicitly via add/remove

               # 4. 等待下一个间隔
                visual_update_event = self.env.event_registry.get_event(self.env.id, 'visual_update')
                yield visual_update_event

            except Exception as e:
                if "connection closed by SUMO" in str(e).lower():
                    logger.error(f"SUMO 关闭了连接: {e}。尝试重新连接...", exc_info=True)
                    # 尝试重新连接
                    try:
                        # 关闭旧连接（如果有）
                        try:
                            if self._traci_conn:
                                self._traci_conn.close()
                        except Exception:
                            pass
                        
                        # 等待一小段时间，让SUMO释放端口
                        time.sleep(5)
                        
                        # 尝试重新连接
                        self._connect_sumo()
                        if self._traci_conn:
                            logger.info("成功重新连接到SUMO，继续更新循环")
                            continue
                        else:
                            logger.error("无法重新连接到SUMO，停止更新循环")
                            # 清理资源
                            self._cleanup_vehicles()
                            break
                    except Exception as reconnect_error:
                        logger.error(f"重新连接SUMO时出错: {reconnect_error}", exc_info=True)
                        self._traci_conn = None
                        # 清理资源
                        self._cleanup_vehicles()
                        break
                else:
                    logger.error(f"SUMO 更新循环中出错: {e}。停止循环。", exc_info=True)
                    # 尝试关闭连接
                    try:
                        if self._traci_conn:
                            self._traci_conn.close()
                    except Exception:
                        pass
                    self._traci_conn = None
                    # 清理资源
                    self._cleanup_vehicles()
                    break

    # --- 文件特定方法 ---
    def _load_from_file(self):
        """从指定的文件加载交通数据。"""
        path = self._file_cfg.get('path')
        fmt = self._file_cfg.get('format', 'csv').lower()
        time_col = self._file_cfg.get('time_column', 'timestamp')
        id_col = self._file_cfg.get('id_column', 'vehicle_id')
        pos_cols = self._file_cfg.get('pos_columns', ['x', 'y', 'z'])
        speed_col = self._file_cfg.get('speed_column')
        angle_col = self._file_cfg.get('angle_column')
        type_col = self._file_cfg.get('type_column')

        if not path:
            raise ValueError("file_config 中未指定文件路径")

        try:
            logger.info(f"从 {fmt} 文件读取交通数据: {path}")
            if fmt == 'csv':
                import pandas as pd  # 添加 pandas 依赖
                df = pd.read_csv(path)

                # 基本验证
                required_cols = [time_col, id_col] + (pos_cols if isinstance(pos_cols, list) else [pos_cols])
                missing_cols = [col for col in required_cols if col not in df.columns]
                if missing_cols:
                    raise ValueError(f"CSV 中缺少必需的列: {missing_cols}")

                # 确保时间列是数值型
                df[time_col] = pd.to_numeric(df[time_col])

                # 处理行并填充调度表
                self._traffic_schedule = {}
                for _, row in df.iterrows():
                    sim_time = float(row[time_col])  # 假设文件中的时间是仿真时间
                    veh_id = str(row[id_col])

                    # 提取位置，处理可能缺少的 Z
                    if isinstance(pos_cols, list):
                        if len(pos_cols) >= 3:
                            source_pos = (float(row[pos_cols[0]]), float(row[pos_cols[1]]), float(row[pos_cols[2]]))
                        elif len(pos_cols) == 2:
                            source_pos = (float(row[pos_cols[0]]), float(row[pos_cols[1]]), 0.0)
                        else:
                            logger.warning(f"位置列数无效: {len(pos_cols)}。跳过行。")
                            continue
                    else:
                        # 如果 pos_cols 是单个字符串，假设它包含序列化的位置
                        try:
                            import json
                            source_pos = json.loads(row[pos_cols])
                            if len(source_pos) == 2:
                                source_pos = (source_pos[0], source_pos[1], 0.0)
                        except Exception as e:
                            logger.warning(f"解析位置列 '{pos_cols}' 时出错: {e}。跳过行。")
                            continue

                    sim_pos = self._convert_coordinates(source_pos)

                    event_data = {
                        # 'sim_time' 将在触发时添加
                        'id': veh_id,
                        'type': str(row[type_col]) if type_col and type_col in row else 'unknown',
                        'position': sim_pos,
                        'speed': float(row[speed_col]) if speed_col and speed_col in row else 0.0,
                        'angle': float(row[angle_col]) if angle_col and angle_col in row else 0.0,
                    }

                    if sim_time not in self._traffic_schedule:
                        self._traffic_schedule[sim_time] = []
                    self._traffic_schedule[sim_time].append(event_data)

                logger.info(f"成功从 {path} 加载 {len(df)} 条记录到交通调度表中。")

            else:
                raise NotImplementedError(f"文件格式 '{fmt}' 尚不支持。")

        except FileNotFoundError:
            logger.error(f"未找到交通数据文件: {path}")
            raise
        except Exception as e:
            logger.error(f"从文件 {path} 加载交通数据时出错: {e}", exc_info=True)
            self._traffic_schedule = {}  # 出错时清空调度表
            raise

    def _file_update_loop(self):
        """SimPy 进程，基于文件调度表更新 AirspaceManager。"""
        if not self.airspace_manager:
           logger.error("AirspaceManager 不可用，无法启动基于文件的更新循环。")
           return
        # 获取调度表中排序的仿真时间列表
        scheduled_times = sorted(self._traffic_schedule.keys())

        last_triggered_time = -1

        for sim_time in scheduled_times:
            # 确保我们只处理相对于当前仿真时间的未来事件
            if sim_time >= self.env.now and sim_time > last_triggered_time:
                try:
                    # 等待直到预定的事件时间
                    wait_duration = sim_time - self.env.now
                    if wait_duration > 0:
                        yield self.env.timeout(wait_duration)
                    elif wait_duration < 0:
                        logger.warning(f"尝试在过去触发文件事件？预定={sim_time}，现在={self.env.now}")
                        continue  # 跳过

                    current_sim_time = self.env.now  # 使用实际触发时间

                    # 触发此时间戳的所有车辆事件
                    if sim_time in self._traffic_schedule:
                        for event_data_template in self._traffic_schedule[sim_time]:
                            event_data = event_data_template.copy()
                            event_data['sim_time'] = current_sim_time # 添加实际触发时间
                            veh_id = event_data['id']
                            sim_pos = event_data['position']

                            # 获取速度和角度信息
                            speed = event_data.get('speed', 0.0)
                            angle = event_data.get('angle', 0.0)
                            veh_type = event_data.get('type', 'unknown')
                            vehicle_id = f"vehicle_{veh_id}"
                            
                            # 存储速度和角度信息
                            self._vehicle_dynamics[vehicle_id] = {
                                'speed': speed,
                                'angle': angle,
                                'type': veh_type
                            }
                            
                            # 更新或注册到 AirspaceManager
                            if veh_id not in self._known_file_vehicles:
                                logger.debug(f"文件数据：新车辆 {veh_id} 检测到，注册到 AirspaceManager。位置: {sim_pos}")
                                self.airspace_manager.register_object(position=sim_pos, agent_id=vehicle_id)
                                self._known_file_vehicles.add(veh_id)
                            else:
                                # logger.debug(f"文件数据：更新车辆 {veh_id} 位置到 AirspaceManager。位置: {sim_pos}")
                                self.airspace_manager.update_object_position(position=sim_pos, agent_id=vehicle_id)
                        last_triggered_time = sim_time
                    else:
                        # 如果逻辑正确，这不应该发生
                        logger.warning(f"等待后在调度表中找不到预定时间 {sim_time}。")

                except simpy.Interrupt:
                    logger.info("文件交通更新循环被中断。")
                    return  # 退出循环
                except Exception as e:
                    logger.error(f"文件交通更新循环在时间 {sim_time} 出错: {e}", exc_info=True)
                    # 决定如何处理：继续，中断？让我们尝试继续。
                    last_triggered_time = sim_time  # 标记为已处理，以避免潜在的无限循环

            elif sim_time < self.env.now:
                logger.debug(f"跳过过去的文件事件，预定时间 {sim_time:.2f}（当前时间 {self.env.now:.2f}）")

        logger.info("文件交通更新循环完成处理调度表。")

    # --- 辅助方法 ---
    def _convert_coordinates(self, source_pos: Tuple[float, float, float]) -> Tuple[float, float, float]:
        """
        将源坐标（例如 SUMO 内部坐标）转换为仿真使用的坐标系。
        对于 SUMO，如果提供了中心点和边界信息，则转换为相对于中心点的局部坐标。
        返回 (X, Y, Z)，其中 Y 是高度，Z 对应 SUMO 的 Y（南北方向）。
        """
        if self.source == 'sumo' and self._center_lat is not None and self._center_lon is not None:
            try:
                # 使用线性插值方法
                if self._sumo_conv_boundary:
                    min_x, min_y, max_x, max_y = self._sumo_conv_boundary
                    x_range = max_x - min_x
                    y_range = max_y - min_y
                    
                    # 计算中心点在 SUMO 坐标系中的位置 (线性插值)
                    center_x_sumo = min_x + x_range / 2
                    center_y_sumo = min_y + y_range / 2
                    
                    # 计算相对坐标
                    veh_x_sumo, veh_y_sumo = source_pos[0], source_pos[1]
                    relative_x = veh_x_sumo - center_x_sumo
                    relative_y = veh_y_sumo - center_y_sumo
                    
                    # 返回 (X, Y, Z)，其中 Z 对应 SUMO 的 Y（南北方向）
                    return (relative_x, relative_y, source_pos[2])
            except Exception as e:
                logger.error(f"坐标转换时出错: {e}. 返回原始 SUMO 坐标。", exc_info=True)
                
        # 对于文件源或缺少转换信息的情况，直接返回源坐标
        return source_pos
        
    def close(self):
        """清理资源，如关闭 TraCI 连接。"""
        if self.source == 'sumo' and self._traci_conn:
            try:
                logger.info("关闭 SUMO TraCI 连接。")
                self._traci_conn.close()
            except Exception as e:
                logger.error(f"关闭 SUMO 连接时出错: {e}")
            finally:
                self._traci_conn = None
                
    # --- 获取车辆动态信息的方法 ---
    def get_vehicle_dynamics(self, vehicle_id: str) -> Optional[Dict[str, float]]:
        """
        获取车辆的速度和角度信息
        
        Args:
            vehicle_id: 车辆ID（注意：需要包含'vehicle_'前缀）
            
        Returns:
            包含速度和角度的字典，如果车辆不存在则返回None
        """
        return self._vehicle_dynamics.get(vehicle_id)
        
    def get_all_vehicle_dynamics(self) -> Dict[str, Dict[str, float]]:
        """
        获取所有车辆的速度和角度信息
        
        Returns:
            所有车辆的ID到速度和角度信息的映射
        """
        return self._vehicle_dynamics.copy()
    
    # --- 结合位置和动态信息的接口 ---
    def get_vehicle_state(self, vehicle_id: str) -> Optional[Dict[str, Any]]:
        """
        获取车辆的完整状态（位置、速度和角度）
        
        Args:
            vehicle_id: 车辆ID（注意：需要包含'vehicle_'前缀）
            
        Returns:
            包含位置、速度和角度的完整状态字典，如果车辆不存在则返回None
        """
        if not self.airspace_manager:
            return None
            
        # 从 AirspaceManager 获取位置
        position = self.airspace_manager.get_agent_position(vehicle_id)
        if not position:
            return None
            
        # 获取动态信息
        dynamics = self._vehicle_dynamics.get(vehicle_id, {})
        
        # 构建完整状态
        vehicle_state = {
            'id': vehicle_id,
            'position': position,
            'sim_time': self.env.now
        }
        
        # 添加动态信息
        vehicle_state['speed'] = dynamics.get('speed', 0.0)
        vehicle_state['angle'] = dynamics.get('angle', 0.0)
        vehicle_state['type'] = dynamics.get('type', 'unknown')
        
        return vehicle_state
    
    def get_all_vehicle_states(self) -> List[Dict[str, Any]]:
        """
        获取所有车辆的完整状态（位置、速度和角度）
        
        Returns:
            所有车辆的完整状态列表
        """
        if not self.airspace_manager:
            return []
            
        # 从 AirspaceManager 获取所有车辆位置
        all_agents = self.airspace_manager.get_all_agents()
        
        # 过滤出车辆（以'vehicle_'开头的ID）
        vehicle_positions = {id: pos for id, pos in all_agents.items() if id.startswith('vehicle_')}
        
        # 构建结果列表
        vehicle_states = []
        
        for vehicle_id, position in vehicle_positions.items():
            # 获取动态信息
            dynamics = self._vehicle_dynamics.get(vehicle_id, {})
            
            # 构建完整状态
            vehicle_state = {
                'id': vehicle_id,
                'position': position,
                'sim_time': self.env.now,
                'speed': dynamics.get('speed', 0.0),
                'angle': dynamics.get('angle', 0.0),
                'type': dynamics.get('type', 'unknown')
            }
            
            vehicle_states.append(vehicle_state)
            
        return vehicle_states
