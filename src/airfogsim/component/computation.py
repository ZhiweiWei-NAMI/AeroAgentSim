from airfogsim.core.component import Component
from typing import List, Dict, Any, Optional, Tuple
import math

class CPUComponent(Component):
    """代表代理的计算能力，管理CPU和内存资源的组件"""
    PRODUCED_METRICS = ['cpu_usage', 'memory_usage', 'processing_power']
    MONITORED_STATES = ['battery_level', 'cpu_cores', 'memory_mb']  # 监控这些状态的变化
    
    def __init__(self, env, agent, name: Optional[str] = None,
                 supported_events: List[str] = ['cpu_usage_changed'],
                 properties: Optional[Dict] = None):
        """
        初始化计算组件
        
        Args:
            env: 仿真环境
            agent: 所属代理
            name: 组件名称
            supported_events: 支持的额外事件
            properties: 组件属性，包含cpu_cores、memory_mb和processing_factor
        """
        super().__init__(env, agent, name or "CPU", supported_events, properties)
        
        # 从properties获取CPU和内存规格
        self.cpu_usage = self.properties.get('cpu_usage', 4)
        self.memory_usage = self.properties.get('memory_usage', 2048)
        self.processing_factor = self.properties.get('processing_factor', 1.0)
    
    def _calculate_performance_metrics(self) -> Dict[str, Any]:
        """计算基于当前代理状态的性能指标"""
        # 获取代理当前状态
        cpu_usage = self.agent.get_state('cpu_usage', 0.0)
        memory_usage = self.agent.get_state('memory_usage', 0.0)
        battery_level = self.agent.get_state('battery_level', 100.0)
        
        # 基础处理能力
        base_processing_power = self.max_cpu_cores * 100.0  # MIPS或其他单位
        
        # 应用环境和条件因素
        # 1. 低电量会降低处理能力以省电
        if battery_level < 20:
            base_processing_power *= 0.7
        elif battery_level < 10:
            base_processing_power *= 0.4
            
        # 2. 高CPU使用率会导致系统热量增加并可能降低性能
        if cpu_usage > 80:
            base_processing_power *= 0.9
            
        # 3. 应用处理因子
        processing_power = base_processing_power * self.processing_factor
        
        # 4. 如果内存使用率非常高，性能可能会进一步下降
        if memory_usage > 90:
            processing_power *= 0.8
            
        return {
            'cpu_usage': cpu_usage,
            'memory_usage': memory_usage,
            'processing_power': processing_power
        }