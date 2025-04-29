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
from typing import Tuple, Optional
from pint import UnitRegistry
import math
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

ureg = UnitRegistry()
Q_ = ureg.Quantity

def calculate_distance(loc1: Tuple[float, float, float], loc2: Tuple[float, float, float]) -> float:
    """计算两个位置之间的欧几里得距离"""
    x1, y1, z1 = loc1
    x2, y2, z2 = loc2
    return math.sqrt((x2 - x1)**2 + (y2 - y1)**2 + (z2 - z1)**2)

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

def convert_coordinates(source_pos: Tuple[float, float, float],
                        conversion_config: dict = None) -> Tuple[float, float, float]:
    """
    将源坐标系统中的位置转换为仿真坐标系统中的位置。
    
    Args:
        source_pos: 源坐标系统中的位置，格式为 (x, y, z)
        conversion_config: 转换配置字典，可能包含以下键：
            - 'type': 转换类型，可以是 'none'（无转换）或 'offset_scale'（偏移和缩放）
            - 'offset_x', 'offset_y', 'offset_z': 各轴的偏移量
            - 'scale': 统一缩放因子
            
    Returns:
        转换后的坐标，格式为 (x, y, z)
    """
    if not conversion_config:
        return source_pos

    conv_type = conversion_config.get('type', 'none')
    
    if conv_type == 'offset_scale':
        offset_x = float(conversion_config.get('offset_x', 0.0))
        offset_y = float(conversion_config.get('offset_y', 0.0))
        offset_z = float(conversion_config.get('offset_z', 0.0))
        scale = float(conversion_config.get('scale', 1.0))
        
        x = (source_pos[0] + offset_x) * scale
        y = (source_pos[1] + offset_y) * scale
        z = (source_pos[2] + offset_z) * scale
        
        return (x, y, z)
    elif conv_type == 'none':
        return source_pos
    else:
        # 记录警告但不抛出异常
        logger.warning(f"不支持的坐标转换类型: {conv_type}，使用 'none'")
        return source_pos
    
# 地球半径（米）
EARTH_RADIUS = 6371000.0

def latlon_to_local(lat: float, lon: float, alt: float = 0.0,
                   ref_lat: Optional[float] = None, ref_lon: Optional[float] = None,
                   ref_alt: float = 0.0) -> Tuple[float, float, float]:
    """
    将经纬度坐标转换为以参考点为原点的局部坐标系（米）。
    使用局部切平面近似（ENU - 东北上坐标系）。
    
    Args:
        lat: 纬度（度）
        lon: 经度（度）
        alt: 高度（米，相对于海平面），默认为0
        ref_lat: 参考点纬度（度），如果为None则使用lat作为参考
        ref_lon: 参考点经度（度），如果为None则使用lon作为参考
        ref_alt: 参考点高度（米），默认为0
        
    Returns:
        局部坐标 (x, y, z)，其中:
        x: 东向距离（米）
        y: 北向距离（米）
        z: 上向距离（米）
    """
    # 如果没有提供参考点，则使用输入点作为参考（结果将是原点）
    if ref_lat is None:
        ref_lat = lat
    if ref_lon is None:
        ref_lon = lon
        
    # 转换为弧度
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    ref_lat_rad = math.radians(ref_lat)
    ref_lon_rad = math.radians(ref_lon)
    
    # 计算坐标差异
    d_lat = lat_rad - ref_lat_rad
    d_lon = lon_rad - ref_lon_rad
    
    # 使用局部切平面近似计算
    # 北向距离（y轴）
    y = EARTH_RADIUS * d_lat
    
    # 东向距离（x轴）- 考虑纬度影响
    x = EARTH_RADIUS * d_lon * math.cos(ref_lat_rad)
    
    # 高度差（z轴）
    z = alt - ref_alt
    
    return (x, y, z)

def local_to_latlon(x: float, y: float, z: float = 0.0,
                   ref_lat: float = 0.0, ref_lon: float = 0.0,
                   ref_alt: float = 0.0) -> Tuple[float, float, float]:
    """
    将局部坐标系（米）转换为经纬度坐标。
    使用局部切平面近似（ENU - 东北上坐标系）的逆变换。
    
    Args:
        x: 东向距离（米）
        y: 北向距离（米）
        z: 上向距离（米），默认为0
        ref_lat: 参考点纬度（度）
        ref_lon: 参考点经度（度）
        ref_alt: 参考点高度（米），默认为0
        
    Returns:
        (纬度, 经度, 高度) 元组，其中:
        纬度: 度
        经度: 度
        高度: 米（相对于海平面）
    """
    # 转换为弧度
    ref_lat_rad = math.radians(ref_lat)
    
    # 计算纬度差异（弧度）
    d_lat = y / EARTH_RADIUS
    
    # 计算经度差异（弧度），考虑纬度影响
    d_lon = x / (EARTH_RADIUS * math.cos(ref_lat_rad))
    
    # 计算结果经纬度（弧度）
    lat_rad = ref_lat_rad + d_lat
    lon_rad = math.radians(ref_lon) + d_lon
    
    # 转换回度
    lat = math.degrees(lat_rad)
    lon = math.degrees(lon_rad)
    
    # 计算高度
    alt = z + ref_alt
    
    return (lat, lon, alt)

def utm_zone_for_lon(lon: float) -> int:
    """
    根据经度确定UTM区域编号。
    
    Args:
        lon: 经度（度）
        
    Returns:
        UTM区域编号（1-60）
    """
    # 标准UTM区域计算（经度范围从-180到180）
    # 区域1从经度-180°开始
    return int((lon + 180) / 6) + 1
        