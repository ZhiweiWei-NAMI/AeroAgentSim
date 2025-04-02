# resource/airspace.py

from airfogsim.core.resource import Resource

class AirspaceResource(Resource):
    """
    空域资源类
    
    表示一个可分配的空中区域
    """
    
    def __init__(self, resource_id: str, 
                 x_range: tuple = (0, 1000), 
                 y_range: tuple = (0, 1000), 
                 altitude_range: tuple = (0, 500),
                 max_capacity: int = 10,
                 attributes: dict = None):
        """
        初始化空域资源
        
        Args:
            resource_id: 资源唯一标识符
            x_range: X坐标范围 (最小值, 最大值)
            y_range: Y坐标范围 (最小值, 最大值)
            altitude_range: 高度范围 (最小值, 最大值)，单位为米
            max_capacity: 可同时容纳的飞行器最大数量
            attributes: 其他属性
        """
        super().__init__(resource_id, attributes)
        
        # 空域基本参数
        self.x_range = x_range
        self.y_range = y_range
        self.altitude_range = altitude_range
        self.max_capacity = max_capacity
        
        # 当前空域状态
        self.weather_condition = "clear"  # clear, rain, fog, storm, etc.
        self.traffic_level = "low"  # low, medium, high
        
    def is_coordinate_in_range(self, x: float, y: float, altitude: float) -> bool:
        """检查坐标是否在空域范围内"""
        in_x_range = self.x_range[0] <= x <= self.x_range[1]
        in_y_range = self.y_range[0] <= y <= self.y_range[1]
        in_altitude = self.altitude_range[0] <= altitude <= self.altitude_range[1]
        
        return in_x_range and in_y_range and in_altitude
    
    def update_weather(self, condition: str) -> None:
        """更新空域天气状况"""
        self.weather_condition = condition
        
        # 恶劣天气可能影响可用性
        if condition in ['storm', 'heavy_rain', 'hurricane']:
            self.status = "maintenance"  # 不可用
        else:
            # 如果没有分配，则设为可用
            if not self.current_allocations:
                self.status = "available"
    
    def has_capacity(self) -> bool:
        """检查是否还有容量分配给新的飞行器"""
        return len(self.current_allocations) < self.max_capacity