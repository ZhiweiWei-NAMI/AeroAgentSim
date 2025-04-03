"""
AirFogSim工具(Utils)核心模块

该模块提供了仿真系统中使用的各种实用工具函数和类，主要用于处理
空间位置、距离计算和物理量表示等通用功能。主要内容包括：
1. 距离计算函数：计算二维和三维空间中的欧几里得距离
2. Location类：表示三维空间中的位置，带有单位和距离计算功能
3. Speed类：表示速度，包含大小和方向
4. 物理单位处理：使用pint库实现带单位的物理量计算

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import math
from typing import Tuple
from pint import UnitRegistry

ureg = UnitRegistry()
Q_ = ureg.Quantity

def calculate_distance(loc1: Tuple[float, float], loc2: Tuple[float, float]) -> float:
    """计算两个位置之间的欧几里得距离"""
    x1, y1 = loc1
    x2, y2 = loc2
    return math.sqrt((x2 - x1)**2 + (y2 - y1)**2)

class Location:
    def __init__(self, x: float, y: float, z: float = 0):
        self.x = Q_(x, 'm')
        self.y = Q_(y, 'm')
        self.z = Q_(z, 'm')

    def distance_to(self, other: 'Location') -> float:
        """计算与另一个位置的欧几里得距离"""
        dx = self.x - other.x
        dy = self.y - other.y
        dz = self.z - other.z
        distance_squared = (dx**2 + dy**2 + dz**2).magnitude
        return Q_(math.sqrt(distance_squared), 'm')
            
    def __str__(self) -> str:
        return f"({self.x:.2f}, {self.y:.2f}, {self.z:.2f})"
    

class Speed:
    def __init__(self, value: float, direction: Tuple[float, float, float]):
        self.value = Q_(value, 'm/s')
        self.direction = direction
        
    def __str__(self) -> str:
        return f"{self.value:.2f} {self.direction}"
    
if __name__ == '__main__':
    loc1 = Location(0, 0)
    loc2 = Location(3, 4)
    print(loc1.distance_to(loc2))  # 5.0 m

    speed = Speed(10, (1, 0, 0))
    print(speed)  # 10.00 meter / second (1, 0, 0)