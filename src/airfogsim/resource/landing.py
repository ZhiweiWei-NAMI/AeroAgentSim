# resource/landing.py

from airfogsim.core.resource import Resource
from airfogsim.core.enums import ResourceStatus # 导入枚举

class LandingResource(Resource):
    """
    着陆区资源类
    
    表示无人机可以起飞和降落的物理场地
    """
    
    def __init__(self, resource_id: str, 
                 location: tuple,
                 radius: float = 10.0,
                 max_capacity: int = 1,
                 has_charging: bool = False,
                 has_data_transfer: bool = False,
                 attributes: dict = None):
        """
        初始化着陆区资源
        
        Args:
            resource_id: 资源唯一标识符
            location: 着陆区中心点坐标 (x, y) 或 (x, y, z)
            radius: 着陆区半径，单位为米
            max_capacity: 同时可容纳的无人机数量
            has_charging: 是否提供充电功能
            has_data_transfer: 是否提供数据传输功能
            attributes: 其他属性
        """
        super().__init__(resource_id, attributes)
        assert resource_id.startswith("landing_"), "资源ID必须以'landing_'开头"
        # 着陆区基本参数
        self.location = location if len(location) >= 2 else (*location, 0)
        self.radius = radius
        self.max_capacity = max_capacity
        self.has_charging = has_charging
        self.has_data_transfer = has_data_transfer
        
        # 着陆区状态
        self.occupied_slots = 0
        self.condition = "normal"  # normal, damaged, maintenance

    def allocate(self, agent_id: str) -> bool:
        """
        分配资源给无人机
        
        Args:
            agent_id: 无人机ID
            
        Returns:
            分配是否成功
        """
        if self.has_capacity() and not self.is_allocated(agent_id):
            self.current_allocations.add(agent_id)
            self.occupied_slots += 1
            return True
        return False
    
    def release(self, agent_id: str) -> bool:
        """
        释放资源
        
        Args:
            agent_id: 无人机ID
            
        Returns:
            释放是否成功
        """
        if agent_id in self.current_allocations:
            self.current_allocations.remove(agent_id)
            self.occupied_slots -= 1
            return True
        return False

    def is_allocated(self, agent_id: str) -> bool:
        """
        检查无人机是否已分配到该着陆区
        
        Args:
            agent_id: 无人机ID
            
        Returns:
            是否已分配
        """
        return agent_id in self.current_allocations
        
    def has_capacity(self) -> bool:
        """检查是否还有容量接收新的无人机"""
        return len(self.current_allocations) < self.max_capacity
    
    def is_within_range(self, x: float, y: float, altitude: float = 0) -> bool:
        """
        检查给定坐标是否在着陆区范围内
        
        Args:
            x: X坐标
            y: Y坐标
            altitude: 高度坐标 (可选)
            
        Returns:
            坐标是否在着陆区范围内
        """
        # 获取着陆区坐标
        landing_x, landing_y = self.location[0], self.location[1]
        
        # 计算平面距离
        distance_2d = ((x - landing_x) ** 2 + (y - landing_y) ** 2) ** 0.5
        
        # 检查是否在半径范围内
        return distance_2d <= self.radius
    
    def update_condition(self, condition: str) -> None:
        """
        更新着陆区状态
        
        Args:
            condition: 新状态 (normal, damaged, maintenance)
        """
        self.condition = condition
        
        # 如果状态变为不可用，则更新资源状态
        if condition in ['damaged', 'maintenance']:
            self.status = ResourceStatus.MAINTENANCE # 使用枚举
        else:
            # 如果没有分配且状态正常，则为可用
            if not self.current_allocations:
                self.status = ResourceStatus.AVAILABLE # 使用枚举
    
    def get_charging_power(self) -> float:
        """
        获取充电功率
        
        Returns:
            充电功率 (W)，如果不具备充电功能则返回0
        """
        if not self.has_charging:
            return 0.0
        
        # 从属性中获取充电功率，如果未指定则使用默认值
        return self.attributes.get('charging_power', 100.0)
    
    def get_data_transfer_rate(self) -> float:
        """
        获取数据传输速率
        
        Returns:
            数据传输速率 (Mbps)，如果不具备数据传输功能则返回0
        """
        if not self.has_data_transfer:
            return 0.0
        
        # 从属性中获取数据传输速率，如果未指定则使用默认值
        return self.attributes.get('data_transfer_rate', 10.0)
    
