"""
AirFogSim仪表盘可视化模块

该模块提供仿真系统的仪表盘数据聚合和可视化功能，包括：
1. 无人机状态统计和电池电量分析
2. 工作流执行情况统计
3. 智能体组件分布统计
4. 系统事件监控
5. 各类数据聚合和图表生成

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.utils.logging_config import get_logger
from typing import Dict, Any, List
from .data_service import SimulationDataService

logger = get_logger(__name__)

class DashboardService:
    """
    仪表盘服务类，负责聚合和分析仿真数据，为前端仪表盘提供可视化数据
    
    主要功能：
    - 获取系统概览数据 (get_dashboard_summary)
    - 无人机状态和电池分析 (get_drone_statistics)
    - 工作流执行统计 (get_workflow_statistics)
    - 智能体组件分布 (get_agent_statistics)
    - 最近事件监控 (get_recent_events)
    - 各类数据分布统计
    
    使用示例：
        data_service = SimulationDataService(env)
        dashboard = DashboardService(data_service)
        summary = dashboard.get_dashboard_summary()
    """
    """仪表盘服务，提供数据聚合和分析功能"""
    
    def __init__(self, data_service: SimulationDataService):
        self.data_service = data_service
    
    def get_dashboard_summary(self) -> Dict[str, Any]:
        """获取仪表盘概览数据"""
        try:
            # 获取无人机统计信息
            drone_stats = self._get_drone_statistics()
            
            # 获取工作流统计信息
            workflow_stats = self._get_workflow_statistics()
            
            # 获取智能体统计信息
            agent_stats = self._get_agent_statistics()
            
            # 获取最近事件
            recent_events = self._get_recent_events(limit=5)
            
            return {
                'drone_stats': drone_stats,
                'workflow_stats': workflow_stats,
                'agent_stats': agent_stats,
                'recent_events': recent_events
            }
        except Exception as e:
            logger.error(f"获取仪表盘概览数据失败: {str(e)}")
            return {
                'error': str(e),
                'drone_stats': {},
                'workflow_stats': {},
                'agent_stats': {},
                'recent_events': []
            }
    
    def _get_drone_statistics(self) -> Dict[str, Any]:
        """获取无人机统计信息"""
        drones = self.data_service.get_all_drones()
        
        # 按状态统计
        status_counts = {
            'total': len(drones),
            'active': sum(1 for d in drones if d.get('status') == 'moving'),
            'idle': sum(1 for d in drones if d.get('status') == 'idle'),
            'charging': sum(1 for d in drones if d.get('status') == 'charging'),
            'error': sum(1 for d in drones if d.get('status') == 'error'),
        }
        
        # 电池电量分布
        battery_levels = [d.get('battery_level', 0) for d in drones]
        battery_stats = {
            'average': sum(battery_levels) / len(battery_levels) if battery_levels else 0,
            'min': min(battery_levels) if battery_levels else 0,
            'max': max(battery_levels) if battery_levels else 0,
            'critical': sum(1 for b in battery_levels if b < 20),
        }
        
        return {
            'status': status_counts,
            'battery': battery_stats
        }
    
    def _get_workflow_statistics(self) -> Dict[str, Any]:
        """获取工作流统计信息"""
        workflows = self.data_service.get_all_workflows()
        
        # 按状态统计
        status_counts = {
            'total': len(workflows),
            'pending': sum(1 for w in workflows if w.get('status') == 'pending'),
            'running': sum(1 for w in workflows if w.get('status') == 'running'),
            'completed': sum(1 for w in workflows if w.get('status') == 'completed'),
            'failed': sum(1 for w in workflows if w.get('status') == 'failed'),
        }
        
        # 按类型统计
        type_counts = {}
        for workflow in workflows:
            w_type = workflow.get('type', 'unknown')
            if w_type not in type_counts:
                type_counts[w_type] = 0
            type_counts[w_type] += 1
        
        return {
            'status': status_counts,
            'types': type_counts
        }
    
    def _get_agent_statistics(self) -> Dict[str, Any]:
        """获取智能体统计信息"""
        agents = self.data_service.get_all_agents()
        
        # 按类型统计
        type_counts = {}
        for agent in agents:
            a_type = agent.get('type', 'unknown')
            if a_type not in type_counts:
                type_counts[a_type] = 0
            type_counts[a_type] += 1
        
        # 按组件统计
        component_counts = {}
        for agent in agents:
            properties = agent.get('properties', {})
            if isinstance(properties, str):
                try:
                    import json
                    properties = json.loads(properties)
                except:
                    properties = {}
            
            components = properties.get('components', [])
            for component in components:
                if component not in component_counts:
                    component_counts[component] = 0
                component_counts[component] += 1
        
        return {
            'total': len(agents),
            'types': type_counts,
            'components': component_counts
        }
    
    def _get_recent_events(self, limit: int = 5) -> List[Dict[str, Any]]:
        """获取最近事件"""
        # 注意：这里假设data_service有一个get_events方法
        # 如果没有，需要在data_service中实现该方法
        try:
            # 这里使用一个假设的方法，实际实现可能需要调整
            events = self.data_service.get_events(limit=limit)
            return events
        except Exception as e:
            logger.warning(f"获取最近事件失败: {str(e)}")
            return []
    
    def get_drone_status_distribution(self) -> Dict[str, int]:
        """获取无人机状态分布"""
        drones = self.data_service.get_all_drones()
        
        status_counts = {}
        for drone in drones:
            status = drone.get('status', 'unknown')
            if status not in status_counts:
                status_counts[status] = 0
            status_counts[status] += 1
        
        return status_counts
    
    def get_battery_level_distribution(self) -> Dict[str, int]:
        """获取电池电量分布"""
        drones = self.data_service.get_all_drones()
        
        # 将电池电量分为几个区间
        ranges = {
            '0-20%': 0,
            '21-40%': 0,
            '41-60%': 0,
            '61-80%': 0,
            '81-100%': 0
        }
        
        for drone in drones:
            battery = drone.get('battery_level', 0)
            if 0 <= battery <= 20:
                ranges['0-20%'] += 1
            elif 21 <= battery <= 40:
                ranges['21-40%'] += 1
            elif 41 <= battery <= 60:
                ranges['41-60%'] += 1
            elif 61 <= battery <= 80:
                ranges['61-80%'] += 1
            elif 81 <= battery <= 100:
                ranges['81-100%'] += 1
        
        return ranges
    
    def get_workflow_completion_rate(self) -> Dict[str, Any]:
        """获取工作流完成率"""
        workflows = self.data_service.get_all_workflows()
        
        total = len(workflows)
        if total == 0:
            return {
                'completion_rate': 0,
                'success_rate': 0,
                'failure_rate': 0
            }
        
        completed = sum(1 for w in workflows if w.get('status') == 'completed')
        failed = sum(1 for w in workflows if w.get('status') == 'failed')
        
        return {
            'completion_rate': (completed / total) * 100 if total > 0 else 0,
            'success_rate': (completed / total) * 100 if total > 0 else 0,
            'failure_rate': (failed / total) * 100 if total > 0 else 0
        }