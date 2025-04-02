from airfogsim.core.component import Component
from typing import List, Dict, Any, Optional, Tuple
import math

class CPUComponent(Component):
    """代表代理的计算能力，管理CPU和内存资源的组件"""
    PRODUCED_METRICS = ['cpu_usage', 'memory_usage', 'processing_power']
    MONITORED_STATES = ['battery_level', 'cpu_usage', 'memory_usage']  # 监控这些状态的变化
    
    def __init__(self, env, agent, name: Optional[str] = None, 
                 cpu_cores: int = 4, 
                 memory_mb: int = 2048,
                 processing_factor: float = 1.0,
                 supported_events: List[str] = ['cpu_usage_changed']):
        """
        初始化计算组件
        
        Args:
            env: 仿真环境
            agent: 所属代理
            name: 组件名称
            cpu_cores: CPU核心数
            memory_mb: 内存大小(MB)
            processing_factor: 处理能力因子
            supported_events: 支持的额外事件
        """
        super().__init__(env, agent, name or "CPU", supported_events)
        
        # 设置CPU和内存规格
        self.max_cpu_cores = cpu_cores
        self.max_memory_mb = memory_mb
        self.processing_factor = processing_factor
        
        # 初始化代理的CPU使用状态
        self.agent.set_state('cpu_cores', cpu_cores)
        self.agent.set_state('memory_mb', memory_mb)
        self.agent.set_state('cpu_usage', 0.0)  # CPU使用率，0-100%
        self.agent.set_state('memory_usage', 0.0)  # 内存使用率，0-100%
    
    def get_resource_requirements(self, task) -> List[Dict]:
        """
        获取任务所需的CPU和内存资源需求
        这些资源由代理自身管理，不需要外部资源管理器
        """
        # 从任务属性获取CPU和内存需求
        required_cpu = task.properties.get('required_cpu_cores', 1)
        required_memory = task.properties.get('required_memory_mb', 256)
        
        # 检查这些需求是否小于最大可用资源
        if required_cpu > self.max_cpu_cores:
            required_cpu = self.max_cpu_cores
            
        if required_memory > self.max_memory_mb:
            required_memory = self.max_memory_mb
            
        return [{
            'type': 'internal_cpu',
            'required_cpu_cores': required_cpu,
            'required_memory_mb': required_memory
        }]
    
    def _allocate_task_resources(self, task) -> List[Tuple]:
        """
        为计算任务分配CPU和内存资源（内部资源）
        返回格式为 [(resource_type, allocation_id), ...] 的列表
        
        对于CPU组件，我们使用代理状态来跟踪资源使用，不需要调用外部管理器
        """
        allocations = []
        required_resources = self.get_resource_requirements(task)
        
        for res_req in required_resources:
            if res_req['type'] == 'internal_cpu':
                # 获取当前CPU和内存使用情况
                current_cpu_usage = self.agent.get_state('cpu_usage', 0.0)
                current_memory_usage = self.agent.get_state('memory_usage', 0.0)
                
                # 计算资源需求的百分比
                required_cpu = res_req.get('required_cpu_cores', 1)
                required_memory = res_req.get('required_memory_mb', 256)
                
                cpu_usage_percent = (required_cpu / self.max_cpu_cores) * 100
                memory_usage_percent = (required_memory / self.max_memory_mb) * 100
                
                # 更新代理的资源使用状态
                new_cpu_usage = min(100.0, current_cpu_usage + cpu_usage_percent)
                new_memory_usage = min(100.0, current_memory_usage + memory_usage_percent)
                
                self.agent.set_state('cpu_usage', new_cpu_usage)
                self.agent.set_state('memory_usage', new_memory_usage)
                
                # 为任务创建唯一的分配标识符
                # 对于内部资源，我们使用任务ID作为分配ID的一部分
                allocation_id = f"cpu_{task.id}"
                
                # 记录分配信息
                allocations.append(('internal_cpu', allocation_id))
                
                # 触发CPU使用率变化事件
                self.trigger_event('cpu_usage_changed', {
                    'cpu_usage': new_cpu_usage,
                    'memory_usage': new_memory_usage,
                    'task_id': task.id
                })
        
        return allocations
    
    def _release_task_resources(self, task_id: str, allocations: List[Tuple]):
        """
        释放计算任务使用的CPU和内存资源
        """
        for res_type, allocation_id in allocations:
            if res_type == 'internal_cpu':
                # 解析分配ID，获取资源使用信息
                # 由于我们没有存储确切的使用量，这里我们从代理状态中减去一个近似值
                
                # 获取当前的资源使用状态
                current_cpu_usage = self.agent.get_state('cpu_usage', 0.0)
                current_memory_usage = self.agent.get_state('memory_usage', 0.0)
                
                # 从活跃任务属性中估算资源使用
                task = None
                for t_id, t in self.active_tasks.items():
                    if t_id == task_id:
                        task = t
                        break
                
                if task:
                    # 获取任务的资源需求
                    required_cpu = task.properties.get('required_cpu_cores', 1)
                    required_memory = task.properties.get('required_memory_mb', 256)
                    
                    # 计算应该释放的资源百分比
                    cpu_usage_percent = (required_cpu / self.max_cpu_cores) * 100
                    memory_usage_percent = (required_memory / self.max_memory_mb) * 100
                    
                    # 更新代理的资源使用状态
                    new_cpu_usage = max(0.0, current_cpu_usage - cpu_usage_percent)
                    new_memory_usage = max(0.0, current_memory_usage - memory_usage_percent)
                    
                    self.agent.set_state('cpu_usage', new_cpu_usage)
                    self.agent.set_state('memory_usage', new_memory_usage)
                    
                    # 触发CPU使用率变化事件
                    self.trigger_event('cpu_usage_changed', {
                        'cpu_usage': new_cpu_usage,
                        'memory_usage': new_memory_usage,
                        'task_id': task_id
                    })
                else:
                    # 如果找不到任务，仅减少一小部分资源使用
                    # 这是一种简单的回退机制
                    new_cpu_usage = max(0.0, current_cpu_usage - 10.0)
                    new_memory_usage = max(0.0, current_memory_usage - 10.0)
                    
                    self.agent.set_state('cpu_usage', new_cpu_usage)
                    self.agent.set_state('memory_usage', new_memory_usage)
    
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