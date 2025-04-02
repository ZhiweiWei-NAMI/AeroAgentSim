import logging
from typing import Dict, Any, List, Tuple, Optional
import json
import math
from datetime import datetime
import statistics

logger = logging.getLogger(__name__)

class StatsService:
    """统计服务，提供数据分析和统计功能"""
    
    @staticmethod
    def calculate_drone_statistics(drone_history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """计算无人机历史数据的统计信息"""
        if not drone_history:
            return {
                'battery': {'avg': 0, 'min': 0, 'max': 0, 'std_dev': 0},
                'speed': {'avg': 0, 'min': 0, 'max': 0, 'std_dev': 0},
                'altitude': {'avg': 0, 'min': 0, 'max': 0, 'std_dev': 0},
                'status_distribution': {},
                'samples': 0
            }
        
        try:
            # 提取数据
            battery_levels = []
            speeds = []
            altitudes = []
            status_counts = {}
            
            for entry in drone_history:
                # 电池电量
                battery = entry.get('battery_level')
                if battery is not None:
                    battery_levels.append(battery)
                
                # 速度
                speed = entry.get('speed')
                if speed is not None:
                    speeds.append(speed)
                
                # 高度 (从position中提取)
                position = entry.get('position')
                if position:
                    if isinstance(position, str):
                        try:
                            position = json.loads(position)
                        except json.JSONDecodeError:
                            position = [0, 0, 0]
                    
                    if len(position) >= 3:
                        altitudes.append(position[2])
                
                # 状态分布
                status = entry.get('status', 'unknown')
                if status not in status_counts:
                    status_counts[status] = 0
                status_counts[status] += 1
            
            # 计算统计数据
            battery_stats = StatsService._calculate_numeric_stats(battery_levels)
            speed_stats = StatsService._calculate_numeric_stats(speeds)
            altitude_stats = StatsService._calculate_numeric_stats(altitudes)
            
            # 计算状态分布百分比
            total_samples = len(drone_history)
            status_distribution = {
                status: (count / total_samples) * 100
                for status, count in status_counts.items()
            }
            
            return {
                'battery': battery_stats,
                'speed': speed_stats,
                'altitude': altitude_stats,
                'status_distribution': status_distribution,
                'samples': total_samples
            }
        except Exception as e:
            logger.error(f"计算无人机统计信息失败: {str(e)}")
            return {
                'error': str(e),
                'battery': {'avg': 0, 'min': 0, 'max': 0, 'std_dev': 0},
                'speed': {'avg': 0, 'min': 0, 'max': 0, 'std_dev': 0},
                'altitude': {'avg': 0, 'min': 0, 'max': 0, 'std_dev': 0},
                'status_distribution': {},
                'samples': len(drone_history)
            }
    
    @staticmethod
    def _calculate_numeric_stats(values: List[float]) -> Dict[str, float]:
        """计算数值列表的统计信息"""
        if not values:
            return {'avg': 0, 'min': 0, 'max': 0, 'std_dev': 0}
        
        try:
            avg = sum(values) / len(values)
            min_val = min(values)
            max_val = max(values)
            
            # 计算标准差
            if len(values) > 1:
                std_dev = statistics.stdev(values)
            else:
                std_dev = 0
            
            return {
                'avg': avg,
                'min': min_val,
                'max': max_val,
                'std_dev': std_dev
            }
        except Exception as e:
            logger.error(f"计算数值统计信息失败: {str(e)}")
            return {'avg': 0, 'min': 0, 'max': 0, 'std_dev': 0, 'error': str(e)}
    
    @staticmethod
    def calculate_battery_consumption_rate(drone_history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """计算电池消耗率"""
        if len(drone_history) < 2:
            return {'rate': 0, 'estimated_remaining_time': 0}
        
        try:
            # 按时间排序
            sorted_history = sorted(drone_history, key=lambda x: x.get('sim_time', 0))
            
            # 提取电池电量和时间
            battery_levels = []
            times = []
            
            for entry in sorted_history:
                battery = entry.get('battery_level')
                time = entry.get('sim_time')
                
                if battery is not None and time is not None:
                    battery_levels.append(battery)
                    times.append(time)
            
            if len(battery_levels) < 2:
                return {'rate': 0, 'estimated_remaining_time': 0}
            
            # 计算消耗率 (百分比/时间单位)
            total_consumption = battery_levels[0] - battery_levels[-1]
            total_time = times[-1] - times[0]
            
            if total_time <= 0:
                return {'rate': 0, 'estimated_remaining_time': 0}
            
            consumption_rate = total_consumption / total_time
            
            # 估计剩余时间
            current_battery = battery_levels[-1]
            if consumption_rate <= 0:
                estimated_remaining_time = float('inf')
            else:
                estimated_remaining_time = current_battery / consumption_rate
            
            return {
                'rate': consumption_rate,
                'estimated_remaining_time': estimated_remaining_time,
                'current_battery': current_battery
            }
        except Exception as e:
            logger.error(f"计算电池消耗率失败: {str(e)}")
            return {'rate': 0, 'estimated_remaining_time': 0, 'error': str(e)}
    
    @staticmethod
    def calculate_distance_traveled(trajectory_points: List[Dict[str, Any]]) -> float:
        """计算轨迹总距离"""
        if len(trajectory_points) < 2:
            return 0
        
        try:
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
            
            return total_distance
        except Exception as e:
            logger.error(f"计算轨迹距离失败: {str(e)}")
            return 0
    
    @staticmethod
    def generate_time_series_data(drone_history: List[Dict[str, Any]], 
                                 key: str = 'battery_level') -> List[Dict[str, Any]]:
        """生成时间序列数据，用于图表显示"""
        try:
            # 按时间排序
            sorted_history = sorted(drone_history, key=lambda x: x.get('sim_time', 0))
            
            # 提取数据
            series_data = []
            
            for entry in sorted_history:
                value = entry.get(key)
                time = entry.get('sim_time')
                
                if value is not None and time is not None:
                    # 如果key是'position'，需要特殊处理
                    if key == 'position':
                        if isinstance(value, str):
                            try:
                                value = json.loads(value)
                            except json.JSONDecodeError:
                                continue
                        
                        # 只使用高度 (z坐标)
                        if len(value) >= 3:
                            value = value[2]
                        else:
                            continue
                    
                    series_data.append({
                        'time': time,
                        'value': value,
                        'timestamp': entry.get('timestamp', '')
                    })
            
            return series_data
        except Exception as e:
            logger.error(f"生成时间序列数据失败: {str(e)}")
            return []
    
    @staticmethod
    def calculate_workflow_statistics(workflows: List[Dict[str, Any]]) -> Dict[str, Any]:
        """计算工作流统计信息"""
        if not workflows:
            return {
                'total': 0,
                'status_distribution': {},
                'type_distribution': {},
                'completion_rate': 0
            }
        
        try:
            # 状态分布
            status_counts = {}
            type_counts = {}
            completed_count = 0
            
            for workflow in workflows:
                # 状态分布
                status = workflow.get('status', 'unknown')
                if status not in status_counts:
                    status_counts[status] = 0
                status_counts[status] += 1
                
                if status == 'completed':
                    completed_count += 1
                
                # 类型分布
                wf_type = workflow.get('type', 'unknown')
                if wf_type not in type_counts:
                    type_counts[wf_type] = 0
                type_counts[wf_type] += 1
            
            # 计算完成率
            total = len(workflows)
            completion_rate = (completed_count / total) * 100 if total > 0 else 0
            
            # 计算分布百分比
            status_distribution = {
                status: (count / total) * 100
                for status, count in status_counts.items()
            }
            
            type_distribution = {
                wf_type: (count / total) * 100
                for wf_type, count in type_counts.items()
            }
            
            return {
                'total': total,
                'status_distribution': status_distribution,
                'type_distribution': type_distribution,
                'completion_rate': completion_rate
            }
        except Exception as e:
            logger.error(f"计算工作流统计信息失败: {str(e)}")
            return {
                'error': str(e),
                'total': len(workflows),
                'status_distribution': {},
                'type_distribution': {},
                'completion_rate': 0
            }