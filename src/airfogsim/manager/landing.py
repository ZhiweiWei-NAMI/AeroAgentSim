# manager/landing.py

from typing import List, Dict, Optional, Tuple
from airfogsim.core.resource import ResourceManager
from airfogsim.resource.landing import LandingResource

class LandingManager(ResourceManager[LandingResource]):
    """
    着陆区资源管理器
    
    管理、分配和查询着陆区资源
    """
    
    def __init__(self, env=None):
        super().__init__(env)
        
    def find_resources(self, requirements: Dict) -> List[LandingResource]:
        """
        查找符合要求的着陆区资源
        
        Args:
            requirements: 资源需求，可能包含如下字段：
                - x_pos: float，期望的x坐标
                - y_pos: float，期望的y坐标
                - max_distance: float，到坐标的最大距离
                - require_charging: bool，是否需要充电功能
                - require_data_transfer: bool，是否需要数据传输功能
                - min_radius: float，着陆区最小半径要求
                
        Returns:
            符合要求的资源列表
        """
        suitable_resources = []
        
        for resource_id, resource in self.resources.items():
            # 检查资源状态
            if hasattr(resource, 'status') and resource.status != 'available':
                continue
                
            # 检查容量要求
            if not resource.has_capacity():
                continue
                
            # 检查位置和距离要求
            if 'x_pos' in requirements and 'y_pos' in requirements:
                x = requirements['x_pos']
                y = requirements['y_pos']
                
                # 计算到着陆区的距离
                landing_x, landing_y = resource.location[0], resource.location[1]
                distance = ((x - landing_x) ** 2 + (y - landing_y) ** 2) ** 0.5
                
                # 如果指定了最大距离，检查是否在范围内
                if 'max_distance' in requirements:
                    max_distance = requirements['max_distance']
                    if distance > max_distance:
                        continue
            
            # 检查充电功能需求
            if requirements.get('require_charging', False):
                if not resource.has_charging:
                    continue
            
            # 检查数据传输功能需求
            if requirements.get('require_data_transfer', False):
                if not resource.has_data_transfer:
                    continue
            
            # 检查半径需求
            if 'min_radius' in requirements:
                if resource.radius < requirements['min_radius']:
                    continue
            
            # 检查着陆区状态
            if resource.condition != 'normal':
                continue
                
            suitable_resources.append(resource)
        
        return suitable_resources
    
    def get_landing_spot(self, resource_id: str) -> Optional[LandingResource]:
        """
        获取指定ID的着陆点
        
        Args:
            resource_id: 着陆点ID
            
        Returns:
            着陆点资源，如果不存在则返回None
        """
        return self.resources.get(resource_id)

    def find_nearest_landing_spot(self, x: float, y: float, altitude: float = 0,
                                 require_charging: bool = False) -> Optional[LandingResource]:
        """
        查找最近的着陆点
        
        Args:
            x: X坐标
            y: Y坐标
            altitude: 高度坐标
            require_charging: 是否需要充电功能
            
        Returns:
            最近的符合要求的着陆点，如果没有符合要求的则返回None
        """
        nearest_spot = None
        min_distance = float('inf')
        
        for resource_id, resource in self.resources.items():
            # 检查资源状态和容量
            if (hasattr(resource, 'status') and resource.status != 'available') or not resource.has_capacity():
                continue
                
            # 检查充电需求
            if require_charging and not resource.has_charging:
                continue
                
            # 检查着陆区状态
            if resource.condition != 'normal':
                continue
                
            # 计算距离
            landing_x, landing_y = resource.location[0], resource.location[1]
            distance = ((x - landing_x) ** 2 + (y - landing_y) ** 2) ** 0.5
            
            if distance < min_distance:
                min_distance = distance
                nearest_spot = resource
        
        return nearest_spot
    
    def update_landing_conditions(self, condition_map: Dict[str, str] = None) -> None:
        """
        批量更新着陆区状态
        
        Args:
            condition_map: 资源ID到状态的映射
        """
        condition_map = condition_map or {}
        
        for resource_id, condition in condition_map.items():
            if resource_id in self.resources:
                self.resources[resource_id].update_condition(condition)
    
    def get_charging_locations(self) -> List[LandingResource]:
        """
        获取所有具备充电功能的着陆区
        
        Returns:
            具备充电功能的着陆区列表
        """
        charging_spots = []
        
        for resource_id, resource in self.resources.items():
            if resource.has_charging:
                charging_spots.append(resource)
                
        return charging_spots
    
    def get_data_transfer_locations(self) -> List[LandingResource]:
        """
        获取所有具备数据传输功能的着陆区
        
        Returns:
            具备数据传输功能的着陆区列表
        """
        data_spots = []
        
        for resource_id, resource in self.resources.items():
            if resource.has_data_transfer:
                data_spots.append(resource)
                
        return data_spots
    
    def create_landing_spot(self, 
                           location: tuple,
                           radius: float = 10.0,
                           max_capacity: int = 1,
                           has_charging: bool = False,
                           has_data_transfer: bool = False,
                           attributes: dict = None) -> str:
        """
        创建并注册新的着陆区资源
        
        Args:
            location: 坐标 (x, y) 或 (x, y, z)
            radius: 半径 (米)
            max_capacity: 最大容量
            has_charging: 是否具备充电功能
            has_data_transfer: 是否具备数据传输功能
            attributes: 附加属性
            
        Returns:
            创建的资源ID，如果创建失败则返回None
        """
        # 生成资源ID
        resource_id = f"landing_{len(self.resources) + 1}"
        
        # 创建新资源
        landing_spot = LandingResource(
            resource_id=resource_id,
            location=location,
            radius=radius,
            max_capacity=max_capacity,
            has_charging=has_charging,
            has_data_transfer=has_data_transfer,
            attributes=attributes
        )
        
        # 注册资源
        if self.register_resource(landing_spot):
            return resource_id
        
        return None
