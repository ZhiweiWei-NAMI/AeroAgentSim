"""
AirFogSim时间线可视化模块

该模块提供无人机仿真系统的事件时间线可视化功能，包括：
1. 事件数据格式化处理
2. 时间线显示优化
3. 事件分类和分组
4. 事件统计和分析
5. 时间范围过滤

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.utils.logging_config import get_logger
from typing import Dict, Any, List, Tuple, Optional
import json
from datetime import datetime

logger = get_logger(__name__)

class TimelineService:
    """
    时间线服务类，负责处理事件时间线可视化和分析
    
    主要功能：
    - 事件数据格式化 (format_events_for_timeline)
    - 事件标题和描述生成 (_get_event_title, _get_event_description)
    - 事件图标和颜色映射 (_get_event_icon_and_color)
    - 事件分组 (group_events_by_source, group_events_by_type)
    - 事件统计分析 (generate_event_statistics)
    
    使用示例：
        timeline_service = TimelineService()
        formatted_events = timeline_service.format_events_for_timeline(events)
    """
    """时间线服务，提供事件时间线相关功能"""
    
    @staticmethod
    def format_events_for_timeline(events: List[Dict[str, Any]], 
                                  start_time: Optional[float] = None,
                                  end_time: Optional[float] = None) -> List[Dict[str, Any]]:
        """将事件数据格式化为时间线显示格式"""
        try:
            # 过滤时间范围
            filtered_events = events
            if start_time is not None:
                filtered_events = [e for e in filtered_events if e.get('sim_time', 0) >= start_time]
            if end_time is not None:
                filtered_events = [e for e in filtered_events if e.get('sim_time', 0) <= end_time]
            
            # 格式化事件
            formatted_events = []
            
            for i, event in enumerate(filtered_events):
                # 处理事件数据
                event_data = event.get('event_data')
                if isinstance(event_data, str):
                    try:
                        event_data = json.loads(event_data)
                    except json.JSONDecodeError:
                        event_data = {}
                
                # 获取事件类型和源
                event_type = event.get('event_type', 'unknown')
                source_id = event.get('source_id', 'unknown')
                
                # 创建事件标题和描述
                title = TimelineService._get_event_title(event_type, source_id)
                description = TimelineService._get_event_description(event_type, event_data)
                
                # 确定事件图标和颜色
                icon, color = TimelineService._get_event_icon_and_color(event_type)
                
                formatted_events.append({
                    'id': i,
                    'time': event.get('sim_time', 0),
                    'timestamp': event.get('timestamp', ''),
                    'title': title,
                    'description': description,
                    'source_id': source_id,
                    'type': event_type,
                    'data': event_data,
                    'icon': icon,
                    'color': color
                })
            
            return formatted_events
        except Exception as e:
            logger.error(f"格式化事件时间线数据失败: {str(e)}")
            return []
    
    @staticmethod
    def _get_event_title(event_type: str, source_id: str) -> str:
        """根据事件类型和源生成事件标题"""
        # 处理常见事件类型
        if event_type == 'state_changed':
            return f"{source_id} 状态变化"
        elif event_type == 'task_started':
            return f"{source_id} 任务开始"
        elif event_type == 'task_completed':
            return f"{source_id} 任务完成"
        elif event_type == 'task_failed':
            return f"{source_id} 任务失败"
        elif 'charging' in event_type:
            return f"{source_id} 充电事件"
        elif 'moving' in event_type or 'position' in event_type:
            return f"{source_id} 移动事件"
        else:
            # 通用格式
            return f"{source_id} {event_type}"
    
    @staticmethod
    def _get_event_description(event_type: str, event_data: Dict[str, Any]) -> str:
        """根据事件类型和数据生成事件描述"""
        # 处理常见事件类型
        if event_type == 'state_changed':
            key = event_data.get('key', '未知')
            old_value = event_data.get('old_value', '未知')
            new_value = event_data.get('new_value', '未知')
            
            # 特殊处理位置
            if key == 'position':
                if isinstance(old_value, list) and len(old_value) >= 3:
                    old_value = f"[{old_value[0]:.2f}, {old_value[1]:.2f}, {old_value[2]:.2f}]"
                if isinstance(new_value, list) and len(new_value) >= 3:
                    new_value = f"[{new_value[0]:.2f}, {new_value[1]:.2f}, {new_value[2]:.2f}]"
            
            return f"状态 '{key}' 从 '{old_value}' 变为 '{new_value}'"
        
        elif event_type == 'task_started':
            task_id = event_data.get('task_id', '未知')
            task_name = event_data.get('task_name', '未知')
            return f"开始任务: {task_name} (ID: {task_id})"
        
        elif event_type == 'task_completed':
            task_id = event_data.get('task_id', '未知')
            task_name = event_data.get('task_name', '未知')
            result = event_data.get('result', {})
            return f"完成任务: {task_name} (ID: {task_id}), 结果: {result}"
        
        elif event_type == 'task_failed':
            task_id = event_data.get('task_id', '未知')
            task_name = event_data.get('task_name', '未知')
            reason = event_data.get('reason', '未知')
            return f"任务失败: {task_name} (ID: {task_id}), 原因: {reason}"
        
        else:
            # 通用格式：将事件数据转换为字符串
            return str(event_data)
    
    @staticmethod
    def _get_event_icon_and_color(event_type: str) -> Tuple[str, str]:
        """根据事件类型确定图标和颜色"""
        # 默认图标和颜色
        default_icon = "event"
        default_color = "#1890ff"
        
        # 根据事件类型映射图标和颜色
        event_mappings = {
            'state_changed': ('sync', '#52c41a'),  # 绿色
            'task_started': ('play-circle', '#1890ff'),  # 蓝色
            'task_completed': ('check-circle', '#52c41a'),  # 绿色
            'task_failed': ('close-circle', '#f5222d'),  # 红色
        }
        
        # 处理包含特定关键词的事件类型
        if 'charging' in event_type:
            return ('thunderbolt', '#faad14')  # 黄色
        elif 'moving' in event_type or 'position' in event_type:
            return ('compass', '#13c2c2')  # 青色
        elif 'error' in event_type or 'fail' in event_type:
            return ('warning', '#f5222d')  # 红色
        
        # 返回映射或默认值
        return event_mappings.get(event_type, (default_icon, default_color))
    
    @staticmethod
    def group_events_by_source(events: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """将事件按源分组"""
        grouped_events = {}
        
        for event in events:
            source_id = event.get('source_id', 'unknown')
            
            if source_id not in grouped_events:
                grouped_events[source_id] = []
            
            grouped_events[source_id].append(event)
        
        return grouped_events
    
    @staticmethod
    def group_events_by_type(events: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """将事件按类型分组"""
        grouped_events = {}
        
        for event in events:
            event_type = event.get('type', 'unknown')
            
            if event_type not in grouped_events:
                grouped_events[event_type] = []
            
            grouped_events[event_type].append(event)
        
        return grouped_events
    
    @staticmethod
    def generate_event_statistics(events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """生成事件统计信息"""
        if not events:
            return {
                'total': 0,
                'by_type': {},
                'by_source': {}
            }
        
        try:
            # 按类型统计
            type_counts = {}
            source_counts = {}
            
            for event in events:
                event_type = event.get('type', 'unknown')
                source_id = event.get('source_id', 'unknown')
                
                if event_type not in type_counts:
                    type_counts[event_type] = 0
                type_counts[event_type] += 1
                
                if source_id not in source_counts:
                    source_counts[source_id] = 0
                source_counts[source_id] += 1
            
            return {
                'total': len(events),
                'by_type': type_counts,
                'by_source': source_counts
            }
        except Exception as e:
            logger.error(f"生成事件统计信息失败: {str(e)}")
            return {
                'error': str(e),
                'total': len(events),
                'by_type': {},
                'by_source': {}
            }