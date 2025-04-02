# manager/airspace.py

from typing import List, Dict, Optional, Tuple
from airfogsim.core.resource import ResourceManager
from airfogsim.resource.airspace import AirspaceResource

class AirspaceManager(ResourceManager[AirspaceResource]):
    """
    空域资源管理器
    
    管理、分配和查询空域资源
    """
    
    def __init__(self, env=None):
        super().__init__(env)
        
    def find_resources(self, requirements: Dict) -> List[AirspaceResource]:
        """
        查找符合要求的空域资源
        
        Args:
            requirements: 资源需求，可能包含如下字段：
                - x_pos: float，期望的x坐标
                - y_pos: float，期望的y坐标
                - altitude: float，期望的高度
                - min_capacity: int，最小容量需求
                
        Returns:
            符合要求的资源列表
        """
        suitable_resources = []
        
        for resource_id, resource in self.resources.items():
            # 检查资源状态
            if hasattr(resource, 'status') and resource.status != 'available':
                continue
                
            # 检查位置要求
            if 'x_pos' in requirements and 'y_pos' in requirements:
                x = requirements['x_pos']
                y = requirements['y_pos']
                altitude = requirements.get('altitude', 0)
                
                if not resource.is_coordinate_in_range(x, y, altitude):
                    continue
            
            # 检查容量要求
            if 'min_capacity' in requirements:
                min_capacity = requirements['min_capacity']
                if len(resource.current_allocations) + min_capacity > resource.max_capacity:
                    continue
            
            # 检查天气要求
            if 'weather_condition' in requirements:
                if resource.weather_condition != requirements['weather_condition']:
                    continue
            
            suitable_resources.append(resource)
        
        return suitable_resources
    
    def get_airspace_at_coordinates(self, x: float, y: float, altitude: float) -> List[AirspaceResource]:
        """
        获取包含指定坐标的所有空域
        
        Args:
            x: X坐标
            y: Y坐标
            altitude: 高度
            
        Returns:
            包含该坐标的空域列表
        """
        containing_airspaces = []
        
        for resource_id, resource in self.resources.items():
            if resource.is_coordinate_in_range(x, y, altitude):
                containing_airspaces.append(resource)
                
        return containing_airspaces
    
    def update_all_weather(self, weather_condition: str) -> None:
        """
        更新所有空域的天气状况
        
        Args:
            weather_condition: 新的天气状况
        """
        for resource_id, resource in self.resources.items():
            resource.update_weather(weather_condition)
    
    def get_available_capacity(self, resource_id: str) -> int:
        """
        获取指定空域的可用容量
        
        Args:
            resource_id: 空域资源ID
            
        Returns:
            可用容量，如果资源不存在则返回0
        """
        if resource_id not in self.resources:
            return 0
            
        resource = self.resources[resource_id]
        return max(0, resource.max_capacity - len(resource.current_allocations))
    
    def create_airspace(self, 
                        x_range: tuple = (0, 1000), 
                        y_range: tuple = (0, 1000), 
                        altitude_range: tuple = (0, 500),
                        max_capacity: int = 10,
                        attributes: dict = None) -> str:
        """
        创建并注册新的空域资源
        
        Args:
            x_range: X坐标范围
            y_range: Y坐标范围
            altitude_range: 高度范围
            max_capacity: 最大容量
            attributes: 附加属性
            
        Returns:
            创建的资源ID，如果创建失败则返回None
        """
        # 生成资源ID
        resource_id = f"airspace_{len(self.resources) + 1}"
        
        # 创建新资源
        airspace = AirspaceResource(
            resource_id=resource_id,
            x_range=x_range,
            y_range=y_range,
            altitude_range=altitude_range,
            max_capacity=max_capacity,
            attributes=attributes
        )
        
        # 注册资源
        if self.register_resource(airspace):
            return resource_id
        
        return None