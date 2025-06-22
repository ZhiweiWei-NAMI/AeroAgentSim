"""
AirFogSim地图可视化模块

该模块提供无人机仿真系统的地图可视化功能，包括：
1. 无人机位置格式化显示
2. 轨迹点数据处理和展示
3. 轨迹统计计算（距离、速度、高度等）
4. 热力图数据生成
5. 地图标记和弹出窗口格式化

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.utils.logging_config import get_logger
from typing import Dict, Any, List, Tuple, Optional
import json
import math

logger = get_logger(__name__)

class MapService:
    """
    地图服务类，负责处理地图可视化相关数据格式化
    
    主要功能：
    - 格式化无人机位置数据 (format_drone_positions_for_map)
    - 处理轨迹点数据 (format_trajectory_for_map)
    - 计算轨迹统计信息 (calculate_trajectory_stats)
    - 生成热力图数据 (generate_heatmap_data)
    - 格式化智能体位置数据 (format_agent_positions_for_map)
    
    使用示例：
        map_service = MapService()
        formatted_drones = map_service.format_drone_positions_for_map(drones)
    """
    """地图服务，提供地图可视化相关功能"""
    
    @staticmethod
    def format_drone_positions_for_map(drones: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将无人机数据格式化为地图显示格式"""
        try:
            formatted_drones = []
            
            for drone in drones:
                # 处理位置数据
                position = drone.get('position', [0, 0, 0])
                if isinstance(position, str):
                    try:
                        position = json.loads(position)
                    except json.JSONDecodeError:
                        position = [0, 0, 0]
                
                # 确保位置是三维坐标
                if len(position) < 3:
                    position = position + [0] * (3 - len(position))
                
                # 格式化状态为友好显示文本
                status_display = {
                    'idle': '空闲',
                    'moving': '移动中',
                    'charging': '充电中',
                    'error': '错误'
                }.get(drone.get('status', ''), drone.get('status', '未知'))
                
                # 计算电池状态
                battery_level = drone.get('battery_level', 0)
                battery_status = 'critical' if battery_level < 20 else 'normal'
                
                # 创建弹出窗口内容
                popup_content = f"""
                <div class='drone-popup'>
                    <h3>无人机 {drone.get('id', 'unknown')}</h3>
                    <p><strong>状态:</strong> {status_display}</p>
                    <p><strong>电池:</strong> {battery_level}%</p>
                    <p><strong>速度:</strong> {drone.get('speed', 0)} m/s</p>
                    <p><strong>位置:</strong> [{position[0]:.2f}, {position[1]:.2f}, {position[2]:.2f}]</p>
                </div>
                """
                
                formatted_drones.append({
                    'id': drone.get('id', 'unknown'),
                    'position': position,
                    'status': drone.get('status', 'unknown'),
                    'battery_level': battery_level,
                    'battery_status': battery_status,
                    'speed': drone.get('speed', 0),
                    'popup_content': popup_content,
                    'label': f"无人机 {drone.get('id', 'unknown')}"
                })
            
            return formatted_drones
        except Exception as e:
            logger.error(f"格式化无人机位置数据失败: {str(e)}")
            return []
    
    @staticmethod
    def format_trajectory_for_map(trajectory_points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将轨迹数据格式化为地图显示格式"""
        try:
            formatted_points = []
            
            for point in trajectory_points:
                # 处理位置数据
                position = point.get('position', [0, 0, 0])
                if isinstance(position, str):
                    try:
                        position = json.loads(position)
                    except json.JSONDecodeError:
                        position = [0, 0, 0]
                
                # 确保位置是三维坐标
                if len(position) < 3:
                    position = position + [0] * (3 - len(position))
                
                formatted_points.append({
                    'position': position,
                    'time': point.get('sim_time', 0),
                    'timestamp': point.get('timestamp', '')
                })
            
            return formatted_points
        except Exception as e:
            logger.error(f"格式化轨迹数据失败: {str(e)}")
            return []
    
    @staticmethod
    def calculate_trajectory_stats(trajectory_points: List[Dict[str, Any]]) -> Dict[str, Any]:
        """计算轨迹统计信息"""
        if not trajectory_points:
            return {
                'total_distance': 0,
                'avg_speed': 0,
                'max_altitude': 0,
                'min_altitude': 0,
                'duration': 0
            }
        
        try:
            # 计算总距离
            total_distance = 0
            for i in range(1, len(trajectory_points)):
                prev_pos = trajectory_points[i-1].get('position', [0, 0, 0])
                curr_pos = trajectory_points[i].get('position', [0, 0, 0])
                
                # 确保位置是列表而不是字符串
                if isinstance(prev_pos, str):
                    prev_pos = json.loads(prev_pos)
                if isinstance(curr_pos, str):
                    curr_pos = json.loads(curr_pos)
                
                # 计算两点之间的欧几里得距离
                distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(prev_pos, curr_pos)))
                total_distance += distance
            
            # 获取时间范围
            start_time = trajectory_points[0].get('time', 0)
            end_time = trajectory_points[-1].get('time', 0)
            duration = end_time - start_time
            
            # 计算平均速度
            avg_speed = total_distance / duration if duration > 0 else 0
            
            # 获取高度范围
            altitudes = [p.get('position', [0, 0, 0])[2] for p in trajectory_points]
            if isinstance(altitudes[0], str):
                altitudes = [json.loads(a)[2] if isinstance(a, str) else a[2] for a in altitudes]
            
            max_altitude = max(altitudes)
            min_altitude = min(altitudes)
            
            return {
                'total_distance': total_distance,
                'avg_speed': avg_speed,
                'max_altitude': max_altitude,
                'min_altitude': min_altitude,
                'duration': duration
            }
        except Exception as e:
            logger.error(f"计算轨迹统计信息失败: {str(e)}")
            return {
                'total_distance': 0,
                'avg_speed': 0,
                'max_altitude': 0,
                'min_altitude': 0,
                'duration': 0,
                'error': str(e)
            }
    
    @staticmethod
    def generate_heatmap_data(trajectory_points: List[Dict[str, Any]]) -> List[List[float]]:
        """生成热力图数据"""
        try:
            # 提取位置数据
            positions = []
            for point in trajectory_points:
                position = point.get('position', [0, 0, 0])
                if isinstance(position, str):
                    position = json.loads(position)
                
                # 热力图只需要二维坐标 [lat, lng]
                positions.append([position[1], position[0]])  # 注意：地图通常使用[lat, lng]格式
            
            return positions
        except Exception as e:
            logger.error(f"生成热力图数据失败: {str(e)}")
            return []
    
    @staticmethod
    def format_agent_positions_for_map(agents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将智能体数据格式化为地图显示格式"""
        try:
            formatted_agents = []
            
            for agent in agents:
                # 只处理有位置信息的智能体（如地面站）
                position = agent.get('position')
                if not position:
                    continue
                
                if isinstance(position, str):
                    try:
                        position = json.loads(position)
                    except json.JSONDecodeError:
                        continue
                
                # 确保位置是三维坐标
                if len(position) < 3:
                    position = position + [0] * (3 - len(position))
                
                # 处理属性数据
                properties = agent.get('properties', {})
                if isinstance(properties, str):
                    try:
                        properties = json.loads(properties)
                    except json.JSONDecodeError:
                        properties = {}
                
                # 创建弹出窗口内容
                popup_content = f"""
                <div class='agent-popup'>
                    <h3>{agent.get('name', '未知智能体')}</h3>
                    <p><strong>类型:</strong> {agent.get('type', '未知')}</p>
                    <p><strong>ID:</strong> {agent.get('id', 'unknown')}</p>
                    <p><strong>位置:</strong> [{position[0]:.2f}, {position[1]:.2f}, {position[2]:.2f}]</p>
                </div>
                """
                
                formatted_agents.append({
                    'id': agent.get('id', 'unknown'),
                    'name': agent.get('name', '未知智能体'),
                    'type': agent.get('type', '未知'),
                    'position': position,
                    'properties': properties,
                    'popup_content': popup_content,
                    'label': agent.get('name', '未知智能体')
                })
            
            return formatted_agents
        except Exception as e:
            logger.error(f"格式化智能体位置数据失败: {str(e)}")
            return []