# manager/airspace.py

from typing import List, Dict, Optional, Tuple, Set
import math
import uuid
class OctreeNode:
    """
    八叉树节点，用于高效维护和查询三维空间中的代理位置
    """
    # 八叉树的八个象限索引
    FUL = 0  # 前上左 (Front Upper Left)
    FUR = 1  # 前上右 (Front Upper Right)
    FLL = 2  # 前下左 (Front Lower Left)
    FLR = 3  # 前下右 (Front Lower Right)
    BUL = 4  # 后上左 (Back Upper Left)
    BUR = 5  # 后上右 (Back Upper Right)
    BLL = 6  # 后下左 (Back Lower Left)
    BLR = 7  # 后下右 (Back Lower Right)
    
    def __init__(self, boundary: Tuple[float, float, float, float, float, float], capacity: int = 8, depth: int = 0, max_depth: int = 8):
        """
        初始化八叉树节点
        
        Args:
            boundary: 边界 (x_min, y_min, z_min, x_max, y_max, z_max)
            capacity: 每个节点最多容纳的代理数量
            depth: 当前节点深度
            max_depth: 最大深度限制
        """
        self.boundary = boundary
        self.capacity = capacity
        self.depth = depth
        self.max_depth = max_depth
        self.agents = {}  # 存储代理ID到位置的映射
        self.children = [None, None, None, None, None, None, None, None]  # 八个子节点
        self.divided = False
    
    def contains_point(self, x: float, y: float, z: float) -> bool:
        """检查点是否在边界内"""
        x_min, y_min, z_min, x_max, y_max, z_max = self.boundary
        return (x_min <= x <= x_max and 
                y_min <= y <= y_max and 
                z_min <= z <= z_max)
    
    def divide(self):
        """将节点分为八个子节点"""
        if self.divided or self.depth >= self.max_depth:
            return
            
        x_min, y_min, z_min, x_max, y_max, z_max = self.boundary
        x_mid = (x_min + x_max) / 2
        y_mid = (y_min + y_max) / 2
        z_mid = (z_min + z_max) / 2
        
        # 创建八个子节点
        # 前上左 (Front Upper Left)
        self.children[self.FUL] = OctreeNode(
            (x_min, y_mid, z_mid, x_mid, y_max, z_max), 
            self.capacity, self.depth + 1, self.max_depth
        )
        # 前上右 (Front Upper Right)
        self.children[self.FUR] = OctreeNode(
            (x_mid, y_mid, z_mid, x_max, y_max, z_max), 
            self.capacity, self.depth + 1, self.max_depth
        )
        # 前下左 (Front Lower Left)
        self.children[self.FLL] = OctreeNode(
            (x_min, y_min, z_mid, x_mid, y_mid, z_max), 
            self.capacity, self.depth + 1, self.max_depth
        )
        # 前下右 (Front Lower Right)
        self.children[self.FLR] = OctreeNode(
            (x_mid, y_min, z_mid, x_max, y_mid, z_max), 
            self.capacity, self.depth + 1, self.max_depth
        )
        # 后上左 (Back Upper Left)
        self.children[self.BUL] = OctreeNode(
            (x_min, y_mid, z_min, x_mid, y_max, z_mid), 
            self.capacity, self.depth + 1, self.max_depth
        )
        # 后上右 (Back Upper Right)
        self.children[self.BUR] = OctreeNode(
            (x_mid, y_mid, z_min, x_max, y_max, z_mid), 
            self.capacity, self.depth + 1, self.max_depth
        )
        # 后下左 (Back Lower Left)
        self.children[self.BLL] = OctreeNode(
            (x_min, y_min, z_min, x_mid, y_mid, z_mid), 
            self.capacity, self.depth + 1, self.max_depth
        )
        # 后下右 (Back Lower Right)
        self.children[self.BLR] = OctreeNode(
            (x_mid, y_min, z_min, x_max, y_mid, z_mid), 
            self.capacity, self.depth + 1, self.max_depth
        )
        
        self.divided = True
        
        # 将当前节点中的代理重新分配到子节点
        agents_to_redistribute = list(self.agents.items())
        self.agents = {}  # 清空当前节点的代理
        
        for agent_id, (x, y, z) in agents_to_redistribute:
            self.insert(agent_id, x, y, z)
    
    def insert(self, agent_id: str, x: float, y: float, z: float) -> bool:
        """
        插入代理到八叉树
        
        Args:
            agent_id: 代理ID
            x: X坐标
            y: Y坐标
            z: Z坐标
            
        Returns:
            插入是否成功
        """
        # 如果点不在边界内，返回失败
        if not self.contains_point(x, y, z):
            return False
            
        # 如果节点未分裂且有容量，直接添加
        if not self.divided and len(self.agents) < self.capacity:
            self.agents[agent_id] = (x, y, z)
            return True
            
        # 如果节点未分裂但已满，需要分裂
        if not self.divided:
            self.divide()
            
        # 尝试将代理插入到子节点
        for child in self.children:
            if child and child.insert(agent_id, x, y, z):
                return True
                
        # 如果已经达到最大深度，强制添加到当前节点
        if self.depth >= self.max_depth:
            self.agents[agent_id] = (x, y, z)
            return True
            
        return False
    
    def update(self, agent_id: str, x: float, y: float, z: float) -> bool:
        """
        更新代理位置
        
        Args:
            agent_id: 代理ID
            x: 新的X坐标
            y: 新的Y坐标
            z: 新的Z坐标
            
        Returns:
            更新是否成功
        """
        # 先移除旧的位置
        self.remove(agent_id)
        # 然后插入新的位置
        return self.insert(agent_id, x, y, z)
    
    def remove(self, agent_id: str) -> bool:
        """
        从八叉树中移除代理
        
        Args:
            agent_id: 代理ID
            
        Returns:
            移除是否成功
        """
        # 检查当前节点
        if agent_id in self.agents:
            del self.agents[agent_id]
            return True
            
        # 如果已分裂，检查子节点
        if self.divided:
            for child in self.children:
                if child and child.remove(agent_id):
                    return True
                    
        return False
    
    def query_range(self, range_boundary: Tuple[float, float, float, float, float, float]) -> Dict[str, Tuple[float, float, float]]:
        """
        查询指定范围内的所有代理
        
        Args:
            range_boundary: 查询范围 (x_min, y_min, z_min, x_max, y_max, z_max)
            
        Returns:
            范围内的代理ID到位置的映射
        """
        x_min, y_min, z_min, x_max, y_max, z_max = range_boundary
        found_agents = {}
        
        # 如果查询范围与当前节点边界不相交，返回空
        if not self._intersects(range_boundary):
            return found_agents
            
        # 检查当前节点中的代理
        for agent_id, (x, y, z) in self.agents.items():
            if (x_min <= x <= x_max and 
                y_min <= y <= y_max and 
                z_min <= z <= z_max):
                found_agents[agent_id] = (x, y, z)
                
        # 如果已分裂，递归查询子节点
        if self.divided:
            for child in self.children:
                if child:
                    child_agents = child.query_range(range_boundary)
                    found_agents.update(child_agents)
                    
        return found_agents
    
    def query_sphere(self, center_x: float, center_y: float, center_z: float, radius: float) -> Dict[str, Tuple[float, float, float]]:
        """
        查询以指定点为中心，指定半径范围内的所有代理
        
        Args:
            center_x: 球心X坐标
            center_y: 球心Y坐标
            center_z: 球心Z坐标
            radius: 半径
            
        Returns:
            球形范围内的代理ID到位置的映射
        """
        # 创建一个包含球的立方体范围
        range_boundary = (
            center_x - radius,
            center_y - radius,
            center_z - radius,
            center_x + radius,
            center_y + radius,
            center_z + radius
        )
        
        # 先查询立方体范围内的代理
        candidate_agents = self.query_range(range_boundary)
        
        # 过滤出球形范围内的代理
        sphere_agents = {}
        for agent_id, (x, y, z) in candidate_agents.items():
            # 计算点到球心的距离
            distance = math.sqrt((x - center_x)**2 + (y - center_y)**2 + (z - center_z)**2)
            if distance <= radius:
                sphere_agents[agent_id] = (x, y, z)
                
        return sphere_agents
    
    def get_nearest_neighbors(self, x: float, y: float, z: float, k: int = 5) -> List[Tuple[str, Tuple[float, float, float], float]]:
        """
        获取距离指定点最近的k个代理
        
        Args:
            x: X坐标
            y: Y坐标
            z: Z坐标
            k: 返回的最近邻数量
            
        Returns:
            最近的k个代理，格式为 [(agent_id, position, distance), ...]
        """
        # 使用较大的初始搜索半径
        search_radius = 100.0
        neighbors = []
        
        while len(neighbors) < k:
            # 查询球形范围内的代理
            agents_in_range = self.query_sphere(x, y, z, search_radius)
            
            # 计算每个代理到查询点的距离
            for agent_id, (agent_x, agent_y, agent_z) in agents_in_range.items():
                distance = math.sqrt((agent_x - x)**2 + (agent_y - y)**2 + (agent_z - z)**2)
                neighbors.append((agent_id, (agent_x, agent_y, agent_z), distance))
            
            # 如果找到足够的邻居，按距离排序并返回前k个
            if len(neighbors) >= k:
                neighbors.sort(key=lambda x: x[2])  # 按距离排序
                return neighbors[:k]
            
            # 否则增大搜索半径
            search_radius *= 2
            
            # 防止无限循环
            if search_radius > 10000:
                break
                
        # 如果找不到足够的邻居，返回所有找到的
        neighbors.sort(key=lambda x: x[2])
        return neighbors
    
    def _intersects(self, range_boundary: Tuple[float, float, float, float, float, float]) -> bool:
        """检查两个立方体边界是否相交"""
        x_min1, y_min1, z_min1, x_max1, y_max1, z_max1 = self.boundary
        x_min2, y_min2, z_min2, x_max2, y_max2, z_max2 = range_boundary
        
        return not (x_max1 < x_min2 or x_max2 < x_min1 or
                   y_max1 < y_min2 or y_max2 < y_min1 or
                   z_max1 < z_min2 or z_max2 < z_min1)
    
    def calculate_distance_between_ids(self, id1: str, id2: str) -> Optional[float]:
        """
        计算两个代理之间的距离
        
        Args:
            id1: 第一个代理ID
            id2: 第二个代理ID
            
        Returns:
            两个代理之间的距离，如果任意一个代理不存在则返回None
        """
        if id1 not in self.agents or id2 not in self.agents:
            return None
            
        x1, y1, z1 = self.agents[id1]
        x2, y2, z2 = self.agents[id2]
        
        return math.sqrt((x1 - x2)**2 + (y1 - y2)**2 + (z1 - z2)**2)

class AirspaceManager:
    """
    空域管理器
    
    负责维护代理位置、着陆点位置、障碍物位置和进行碰撞检测
    """
    
    def __init__(self, env=None, boundary: Tuple[float, float, float, float, float, float] = (0, 0, 0, 10000, 10000, 5000)):
        """
        初始化空域管理器
        
        Args:
            env: 仿真环境
            boundary: 整体空域边界 (x_min, y_min, z_min, x_max, y_max, z_max)
        """
        self.id = f"airspace_manager_{uuid.uuid4().hex[:8]}"
        self.env = env
        self.octree = OctreeNode(boundary)
        self.agent_positions = {}  # 存储所有代理的当前位置
        self.landing_positions = {}  # 存储所有着陆点的位置
        self.obstacle_positions = {}  # 存储所有障碍物的位置
        self.collision_threshold = 5.0  # 碰撞检测阈值（米）
        self.collision_events = {}  # 记录已经触发的碰撞事件，避免重复触发
        self.event_registry = env.event_registry
        
        # 通过event_registry监听所有agent state_changed事件
        if env and hasattr(env, 'event_registry'):
            self.event_registry.subscribe(
                "*",  # 使用通配符监听所有代理
                "state_changed",
                f"{self.id}_position_monitor",
                self._on_state_changed
            )
    
    
    
    def get_distance_between_objects(self, obj_id1, obj_id2) -> Optional[float]:
        """
        获取两个代理之间的距离
        
        Args:
            agent_id1: 第一个代理ID
            agent_id2: 第二个代理ID
            
        Returns:
            两个代理之间的距离，如果任意一个代理不存在则返回None
        """
        self.octree.calculate_distance_between_ids(obj_id1, obj_id2)

    def update_object_position(self, position: Tuple[float, float, float],
                        agent_id: str=None, 
                        landing_id: str=None,
                        obstacle_id: str=None) -> None:
        """
        更新代理位置
        
        Args:
            position: 新位置 (x, y, z) 
            agent_id: 代理ID (可选)
            landing_id: 着陆点ID (可选)
            obstacle_id: 障碍物ID (可选)
        """
        x, y, z = position
        # 确保只有一个ID被提供
        if not (agent_id or landing_id or obstacle_id):
            raise ValueError("至少提供一个ID: agent_id, landing_id或obstacle_id")
        if len([i for i in [agent_id, landing_id, obstacle_id] if i]) > 1:
            raise ValueError("只能提供一个ID: agent_id, landing_id或obstacle_id")
        anyway_id = agent_id or landing_id or obstacle_id

        # 更新八叉树
        self.octree.update(anyway_id, x, y, z)
        
        anyway_positions = None
        if agent_id:
            anyway_positions = self.agent_positions
        elif landing_id:
            anyway_positions = self.landing_positions
        elif obstacle_id:
            anyway_positions = self.obstacle_positions
        anyway_positions[anyway_id] = position
        
        # 检测潜在碰撞
        self._check_potential_collisions(anyway_id, position)
    
    def get_object_position(self, agent_id: str=None, landing_id: str=None, obstacle_id: str=None) -> Optional[Tuple[float, float, float]]:
        """
        获取代理当前位置
        
        Args:
            agent_id: 代理ID (可选)
            landing_id: 着陆点ID (可选)
            obstacle_id: 障碍物ID (可选)
            
        Returns:
            代理位置 (x, y, z)，如果代理不存在则返回None
        """
        if agent_id:
            return self.agent_positions.get(agent_id)
        elif landing_id:
            return self.landing_positions.get(landing_id)
        elif obstacle_id:
            return self.obstacle_positions.get(obstacle_id)
        else:
            raise ValueError("至少提供一个ID: agent_id, landing_id或obstacle_id")

    def register_object(self, position: Tuple[float, float, float],
                      agent_id: str=None,
                      landing_id: str=None,
                      obstacle_id: str=None) -> None:
        """
        注册新对象（代理、着陆点或障碍物）
        
        Args:
            position: 初始位置 (x, y, z)
            agent_id: 代理ID (可选)
            landing_id: 着陆点ID (可选)
            obstacle_id: 障碍物ID (可选)
        """
        # 确保只有一个ID被提供
        if not (agent_id or landing_id or obstacle_id):
            raise ValueError("至少提供一个ID: agent_id, landing_id或obstacle_id")
        if len([i for i in [agent_id, landing_id, obstacle_id] if i]) > 1:
            raise ValueError("只能提供一个ID: agent_id, landing_id或obstacle_id")
        
        anyway_id = agent_id or landing_id or obstacle_id
        x, y, z = position
        
        # 添加到八叉树
        self.octree.insert(anyway_id, x, y, z)
        
        # 添加到相应的位置字典
        if agent_id:
            self.agent_positions[agent_id] = position
        elif landing_id:
            self.landing_positions[landing_id] = position
        elif obstacle_id:
            self.obstacle_positions[obstacle_id] = position
        
        # 检测潜在碰撞
        self._check_potential_collisions(anyway_id, position)
    
    def register_agent(self, agent_id: str, position: Tuple[float, float, float]) -> None:
        """
        注册新代理
        
        Args:
            agent_id: 代理ID
            position: 初始位置 (x, y, z)
        """
        self.register_object(position, agent_id=agent_id)
    
    def register_landing(self, landing_id: str, position: Tuple[float, float, float]) -> None:
        """
        注册新着陆点
        
        Args:
            landing_id: 着陆点ID
            position: 初始位置 (x, y, z)
        """
        self.register_object(position, landing_id=landing_id)
    
    def register_obstacle(self, obstacle_id: str, position: Tuple[float, float, float]) -> None:
        """
        注册新障碍物
        
        Args:
            obstacle_id: 障碍物ID
            position: 初始位置 (x, y, z)
        """
        self.register_object(position, obstacle_id=obstacle_id)
    
    def remove_object(self, agent_id: str=None, landing_id: str=None, obstacle_id: str=None) -> None:
        """
        移除对象（代理、着陆点或障碍物）
        
        Args:
            agent_id: 代理ID (可选)
            landing_id: 着陆点ID (可选)
            obstacle_id: 障碍物ID (可选)
        """
        # 确保只有一个ID被提供
        if not (agent_id or landing_id or obstacle_id):
            raise ValueError("至少提供一个ID: agent_id, landing_id或obstacle_id")
        if len([i for i in [agent_id, landing_id, obstacle_id] if i]) > 1:
            raise ValueError("只能提供一个ID: agent_id, landing_id或obstacle_id")
        
        anyway_id = agent_id or landing_id or obstacle_id
        
        # 从八叉树中移除
        self.octree.remove(anyway_id)
        
        # 从相应的位置字典中移除
        if agent_id and agent_id in self.agent_positions:
            del self.agent_positions[agent_id]
        elif landing_id and landing_id in self.landing_positions:
            del self.landing_positions[landing_id]
        elif obstacle_id and obstacle_id in self.obstacle_positions:
            del self.obstacle_positions[obstacle_id]
            
        # 清理相关的碰撞事件记录
        collision_keys_to_remove = []
        for key in self.collision_events:
            if anyway_id in key:
                collision_keys_to_remove.append(key)
                
        for key in collision_keys_to_remove:
            del self.collision_events[key]
    
    def remove_agent(self, agent_id: str) -> None:
        """
        移除代理
        
        Args:
            agent_id: 代理ID
        """
        self.remove_object(agent_id=agent_id)
    
    def remove_landing(self, landing_id: str) -> None:
        """
        移除着陆点
        
        Args:
            landing_id: 着陆点ID
        """
        self.remove_object(landing_id=landing_id)
    
    def remove_obstacle(self, obstacle_id: str) -> None:
        """
        移除障碍物
        
        Args:
            obstacle_id: 障碍物ID
        """
        self.remove_object(obstacle_id=obstacle_id)
    
    def get_agent_position(self, agent_id: str) -> Optional[Tuple[float, float, float]]:
        """
        获取代理当前位置
        
        Args:
            agent_id: 代理ID
            
        Returns:
            代理位置 (x, y, z)，如果代理不存在则返回None
        """
        return self.agent_positions.get(agent_id)
    
    def get_nearby_objects(self, position: Tuple[float, float, float], radius: float = 100.0) -> Dict[str, Tuple[float, float, float]]:
        """
        查询指定位置附近的所有对象

        Args:
            position: 查询位置 (x, y, z)
            radius: 查询半径

        Returns:
            附近对象的ID到位置的映射
        """
        x, y, z = position
        nearby_objects = self.octree.query_sphere(x, y, z, radius)
                    
        return nearby_objects
    
    def check_collision(self, agent_id1: str, agent_id2: str) -> bool:
        """
        检查两个代理之间是否有碰撞风险
        
        Args:
            agent_id1: 第一个代理ID
            agent_id2: 第二个代理ID
            
        Returns:
            是否有碰撞风险
        """
        if agent_id1 not in self.agent_positions or agent_id2 not in self.agent_positions:
            return False
            
        pos1 = self.agent_positions[agent_id1]
        pos2 = self.agent_positions[agent_id2]
        
        # 计算三维距离
        distance = math.sqrt((pos1[0] - pos2[0])**2 + (pos1[1] - pos2[1])**2 + (pos1[2] - pos2[2])**2)
        
        # 如果距离小于阈值，则认为有碰撞风险
        return distance < self.collision_threshold
    
    def _check_potential_collisions(self, anyway_id: str, position: Tuple[float, float, float]) -> None:
        """
        检查活动物体与周围其他代理的潜在碰撞
        
        Args:
            anyway_id: 任意活动的物体ID (代理/landing/障碍物)
            position: 位置
        """
        if not self.env:
            return
            
        x, y, z = position
        
        # 查询附近的代理
        nearby_objects = self.octree.query_sphere(x, y, z, self.collision_threshold * 2)
        
        # 移除查询代理自身
        if anyway_id in nearby_objects:
            del nearby_objects[anyway_id]
            
        # 检查每个附近代理是否有碰撞风险
        for other_id, (other_x, other_y, other_z) in nearby_objects.items():
            # 计算三维距离
            distance = math.sqrt((x - other_x)**2 + (y - other_y)**2 + (z - other_z)**2)
            
            # 如果距离小于阈值，触发碰撞事件
            if distance < self.collision_threshold:
                # 创建一个唯一的碰撞事件ID，确保相同的两个代理只触发一次
                collision_id = tuple(sorted([anyway_id, other_id]))
                
                # 如果这个碰撞事件已经触发过，跳过
                if collision_id in self.collision_events:
                    continue
                    
                # 记录这个碰撞事件
                self.collision_events[collision_id] = self.env.now
                
                # 触发碰撞事件
                self.event_registry.trigger_event(
                    anyway_id,
                    'collision_risk',
                    {
                        'source_id': anyway_id,
                        'other_id': other_id,
                        'distance': distance,
                        'time': self.env.now,
                        'position': position,
                        'other_position': (other_x, other_y, other_z)
                    }
                )
                
                # 同时为另一个代理触发事件
                self.event_registry.trigger_event(
                    other_id,
                    'collision_risk',
                    {
                        'source_id': other_id,
                        'other_id': anyway_id,
                        'distance': distance,
                        'time': self.env.now,
                        'position': (other_x, other_y, other_z),
                        'other_position': position
                    }
                )
    
    def clear_collision_event(self, agent_id1: str, agent_id2: str) -> None:
        """
        清除两个代理之间的碰撞事件记录
        
        Args:
            agent_id1: 第一个代理ID
            agent_id2: 第二个代理ID
        """
        collision_id = tuple(sorted([agent_id1, agent_id2]))
        if collision_id in self.collision_events:
            del self.collision_events[collision_id]
    
    def get_objects_in_volume(self, x_min: float, y_min: float, z_min: float, 
                           x_max: float, y_max: float, z_max: float) -> Dict[str, Tuple[float, float, float]]:
        """
        获取指定体积内的所有物体,种类会以id的前缀来区分
        
        Args:
            x_min: 体积最小X坐标
            y_min: 体积最小Y坐标
            z_min: 体积最小Z坐标
            x_max: 体积最大X坐标
            y_max: 体积最大Y坐标
            z_max: 体积最大Z坐标
            
        Returns:
            体积内物体的ID到位置的映射
        """
        return self.octree.query_range((x_min, y_min, z_min, x_max, y_max, z_max))
    
    def get_objects_in_sphere(self, center_x: float, center_y: float, center_z: float, radius: float) -> Dict[str, Tuple[float, float, float]]:
        """
        获取指定球形范围内的所有物体

        Args:
            center_x: 球心X坐标
            center_y: 球心Y坐标
            center_z: 球心Z坐标
            radius: 半径
            
        Returns:
            球形范围内物体的ID到位置的映射
        """
        return self.octree.query_sphere(center_x, center_y, center_z, radius)
    
    def _on_state_changed(self, event_data):
        """
        处理代理状态变化事件
        
        Args:
            event含代理ID,landing ID, obstacle ID、状态名称和新值
        """
        if not event_data or ('agent_id' not in event_data and 
                              'landing_id' not in event_data and 
                              'obstacle_id' not in event_data):
            return
            
        agent_id = event_data.get('agent_id')
        landing_id = event_data.get('landing_id')
        obstacle_id = event_data.get('obstacle_id')
        key = event_data.get('key')
        new_value = event_data.get('new_value')
        
        # 只关注position状态变化
        if key == 'position' and new_value:
            # 确保位置是有效的三维坐标
            if isinstance(new_value, (list, tuple)) and len(new_value) == 3:
                # 更新代理位置
                self.update_object_position(new_value, agent_id=agent_id,
                                     landing_id=landing_id,
                                     obstacle_id=obstacle_id)
    
    def get_all_agents(self) -> Dict[str, Tuple[float, float, float]]:
        """
        获取所有代理的位置
        
        Returns:
            所有代理的ID到位置的映射
        """
        return self.agent_positions.copy()
        
    def get_all_landings(self) -> Dict[str, Tuple[float, float, float]]:
        """
        获取所有着陆点的位置
        
        Returns:
            所有着陆点的ID到位置的映射
        """
        # 过滤出所有以landing_开头的对象
        return self.landing_positions.copy()