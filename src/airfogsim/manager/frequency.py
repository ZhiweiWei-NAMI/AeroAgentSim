# manager/frequency.py

from typing import List, Dict, Optional, Tuple
from airfogsim.core.resource import ResourceManager
from airfogsim.resource.frequency import FrequencyResource

class FrequencyManager(ResourceManager[FrequencyResource]):
    """
    频率资源管理器
    
    管理、分配和查询无线通信频率资源
    """
    
    def __init__(self, env=None):
        super().__init__(env)
        
    def find_resources(self, requirements: Dict) -> List[FrequencyResource]:
        """
        查找符合要求的频率资源
        
        Args:
            requirements: 资源需求，可能包含如下字段：
                - min_frequency: 最小频率 (MHz)
                - max_frequency: 最大频率 (MHz)
                - min_bandwidth: 最小带宽 (MHz)
                - max_noise_level: 最大可接受噪声水平 (dB)
                - min_power: 最小需要的功率 (mW)
                
        Returns:
            符合要求的资源列表
        """
        suitable_resources = []
        
        for resource_id, resource in self.resources.items():
            # 检查资源状态
            if hasattr(resource, 'status') and resource.status != 'available':
                continue
                
            # 检查频率范围
            if 'min_frequency' in requirements:
                if resource.frequency_range[0] < requirements['min_frequency']:
                    continue
                    
            if 'max_frequency' in requirements:
                if resource.frequency_range[1] > requirements['max_frequency']:
                    continue
            
            # 检查带宽要求
            if 'min_bandwidth' in requirements:
                if resource.bandwidth < requirements['min_bandwidth']:
                    continue
            
            # 检查噪声水平
            if 'max_noise_level' in requirements:
                if resource.noise_level > requirements['max_noise_level']:
                    continue
            
            # 检查功率要求
            if 'min_power' in requirements:
                if resource.power_limit < requirements['min_power']:
                    continue
            
            # 检查是否有足够容量
            if not resource.has_capacity():
                continue
                
            suitable_resources.append(resource)
        
        return suitable_resources
    
    def check_interference(self, resource_id1: str, resource_id2: str) -> float:
        """
        检查两个频率资源之间的干扰水平
        
        Args:
            resource_id1: 第一个频率资源ID
            resource_id2: 第二个频率资源ID
            
        Returns:
            干扰水平 (0.0-1.0)，如果资源不存在则返回-1
        """
        if resource_id1 not in self.resources or resource_id2 not in self.resources:
            return -1
            
        resource1 = self.resources[resource_id1]
        resource2 = self.resources[resource_id2]
        
        # 检查是否有频率重叠
        freq1_min, freq1_max = resource1.frequency_range
        freq2_min, freq2_max = resource2.frequency_range
        
        # 如果没有重叠，干扰为0
        if freq1_max < freq2_min or freq2_max < freq1_min:
            return 0.0
            
        # 计算重叠部分
        overlap_min = max(freq1_min, freq2_min)
        overlap_max = min(freq1_max, freq2_max)
        overlap_size = overlap_max - overlap_min
        
        # 计算重叠比例
        r1_size = freq1_max - freq1_min
        r2_size = freq2_max - freq2_min
        overlap_ratio = overlap_size / min(r1_size, r2_size)
        
        return overlap_ratio
    
    def update_channel_conditions(self, noise_map: Dict[str, float] = None, 
                                 interference_map: Dict[str, float] = None) -> None:
        """
        批量更新信道状态
        
        Args:
            noise_map: 资源ID到噪声水平的映射
            interference_map: 资源ID到干扰水平的映射
        """
        noise_map = noise_map or {}
        interference_map = interference_map or {}
        
        for resource_id, resource in self.resources.items():
            noise_level = noise_map.get(resource_id, resource.noise_level)
            interference = interference_map.get(resource_id, resource.interference)
            
            resource.update_channel_condition(noise_level, interference)
            resource.update_utilization()
    
    def create_frequency(self, 
                        frequency_range: tuple,
                        bandwidth: float,
                        max_users: int = 5,
                        power_limit: float = 100.0,
                        attributes: dict = None) -> str:
        """
        创建并注册新的频率资源
        
        Args:
            frequency_range: 频率范围 (MHz)
            bandwidth: 带宽 (MHz)
            max_users: 最大用户数
            power_limit: 功率限制 (mW)
            attributes: 附加属性
            
        Returns:
            创建的资源ID，如果创建失败则返回None
        """
        # 生成资源ID
        resource_id = f"freq_{len(self.resources) + 1}"
        
        # 创建新资源
        frequency = FrequencyResource(
            resource_id=resource_id,
            frequency_range=frequency_range,
            bandwidth=bandwidth,
            max_users=max_users,
            power_limit=power_limit,
            attributes=attributes
        )
        
        # 注册资源
        if self.register_resource(frequency):
            return resource_id
        
        return None
    
    def get_frequency_utilization(self) -> Dict[str, float]:
        """
        获取所有频率资源的使用率
        
        Returns:
            资源ID到使用率的映射
        """
        utilization_map = {}
        
        for resource_id, resource in self.resources.items():
            resource.update_utilization()
            utilization_map[resource_id] = resource.utilization
            
        return utilization_map