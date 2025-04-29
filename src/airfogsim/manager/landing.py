# manager/landing.py

import functools # Added for partial
from typing import List, Dict, Optional, Any
from airfogsim.core.resource import ResourceManager
from airfogsim.resource.landing import LandingResource
# from airfogsim.manager.airspace import AirspaceManager  # 不需要导入，通过env获取
from airfogsim.core.enums import ResourceStatus # Added
from queue import PriorityQueue
import math
from airfogsim.utils.logging_config import get_logger

# Configure logging
logger = get_logger(__name__)
class LandingManager(ResourceManager[LandingResource]):
    """
    着陆区资源管理器

    管理、分配和查询着陆区资源
    """

    def __init__(self, env):
        super().__init__(env)
        # 存储资源分配信息：{resource_id: {agent_id: (agent, listener_id)}}
        # 存储资源请求队列
        self.request_queues = PriorityQueue()

        # 空域管理器引用，用于高效的空间查询
        self.airspace_manager = env.airspace_manager

        # Setup subscriptions to DataProvider events
        self._setup_data_provider_subscriptions()

    def find_resource_by_id(self, resource_id: str) -> Optional[LandingResource]:
        """
        根据资源ID查找资源

        Args:
            resource_id: 资源ID

        Returns:
            LandingResource对象，如果不存在则返回None
        """
        return self.resources.get(resource_id)

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

        # 如果有空域管理器且有位置要求，使用空间查询
        if 'x_pos' in requirements and 'y_pos' in requirements:
            x = requirements['x_pos']
            y = requirements['y_pos']
            z = requirements.get('z_pos', 0)  # 默认高度为0

            # 查询范围
            max_distance = requirements.get('max_distance', 1000.0)  # 默认搜索半径

            # 使用八叉树查询指定范围内的所有对象
            position = (x, y, z)
            nearby_objects = self.airspace_manager.get_nearby_objects(position=position, radius=max_distance)

            # 过滤出着陆点（ID以landing_开头）
            landing_ids = [
                obj_id
                for obj_id in nearby_objects.keys()
                if obj_id.startswith('landing_')
            ]

            # 检查每个着陆点是否符合其他要求
            for landing_id in landing_ids:
                if landing_id in self.resources:
                    resource = self.resources[landing_id]

                    # 检查资源状态
                    if hasattr(resource, 'status') and resource.status != ResourceStatus.AVAILABLE: # 使用枚举
                        continue

                    # 检查容量要求
                    if not resource.has_capacity():
                        continue

                    # 检查充电功能需求
                    if requirements.get('require_charging', False) and not resource.has_charging:
                        continue

                    # 检查数据传输功能需求
                    if requirements.get('require_data_transfer', False) and not resource.has_data_transfer:
                        continue

                    # 检查半径需求
                    if 'min_radius' in requirements and resource.radius < requirements['min_radius']:
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

    def find_nearest_landing_spot(self, x: float, y: float, altitude: float = 0, max_distance=1000,
                                 require_charging: bool = False) -> Optional[LandingResource]:
        """
        查找最近的着陆点

        Args:
            x: X坐标
            y: Y坐标
            altitude: 高度坐标
            require_charging: 是否需要充电功能

        Returns:
            最近的符合要求的available着陆点，如果没有符合要求的则返回None
        """
        # 过滤出着陆点
        landing_candidates = []
        for obj_id, resource in self.resources.items():
            resource_id = obj_id
            resource = self.resources[resource_id]

            # 检查资源状态和容量
            if (hasattr(resource, 'status') and resource.status != ResourceStatus.AVAILABLE) or not resource.has_capacity(): # 使用枚举
                continue

            # 检查充电需求
            if require_charging and not resource.has_charging:
                continue

            # 检查着陆区状态
            if resource.condition != 'normal':
                continue
            position = self.env.airspace_manager.get_object_position(landing_id=resource_id)
            distance = math.sqrt(
                (position[0] - x) ** 2 +
                (position[1] - y) ** 2 +
                (position[2] - altitude) ** 2
            )
            # 添加到候选列表
            landing_candidates.append((resource, distance))

        # 按距离排序并返回最近的
        if landing_candidates:
            landing_candidates.sort(key=lambda x: x[1])
            return landing_candidates[0][0]

        return None


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
        # 获取所有着陆点
        charging_spots = []
        for resource_id, resource in self.resources.items():
            resource = self.resources[resource_id]
            if resource.has_charging:
                charging_spots.append(resource)

        return charging_spots

    def get_data_transfer_locations(self) -> List[LandingResource]:
        """
        获取所有具备数据传输功能的着陆区

        Returns:
            具备数据传输功能的着陆区列表
        """
        # 获取所有着陆点
        data_spots = []
        for _, resource in self.resources.items():
            if resource.has_data_transfer:
                data_spots.append(resource)

        return data_spots

    def request_resource(self, resource_id: str, agent, priority: int = 0) -> bool:
        """
        请求资源，如果资源可用则立即分配，否则加入请求队列

        Args:
            resource_id: 资源ID
            agent: 请求的代理
            priority: 优先级（数字越小优先级越高）

        Returns:
            bool: 是否成功分配资源
        """
        # 检查资源是否存在
        resource = self.find_resource_by_id(resource_id)
        if not resource:
            logger.warning(f"时间 {self.env.now}: 资源 {resource_id} 不存在")
            return False

        # 检查代理是否已经分配了此资源
        if self.is_allocated_to(resource_id, agent.id):
            logger.warning(f"时间 {self.env.now}: 代理 {agent.id} 已经分配了资源 {resource_id}")
            return True

        # 检查资源是否有可用容量
        if resource.has_capacity():
            # 立即分配资源
            logger.info(f"时间 {self.env.now}: 代理 {agent.id} 请求资源 {resource_id}，立即分配")
            return self.allocate_resource(resource_id, agent)
        elif not self.is_requesting(resource_id, agent.id):
            # 加入请求队列
            request_time = self.env.now
            self.request_queues.put((priority, request_time, resource_id, agent.id, agent))
            logger.warning(f"时间 {self.env.now}: 代理 {agent.id} 请求资源 {resource_id} 已加入队列，优先级 {priority}")
            return False
        else:            
            return False
        
    def is_allocated_to(self, resource_id: str, agent_id: str) -> bool:
        """
        检查资源是否已分配给指定代理

        Args:
            resource_id: 资源ID
            agent_id: 代理ID

        Returns:
            bool: 是否已分配
        """
        return resource_id in self.resource_allocations and agent_id in self.resource_allocations[resource_id]
        
    def is_requesting(self, resource_id: str, agent_id: str) -> bool:
        """
        检查代理是否正在请求资源

        Args:
            resource_id: 资源ID
            agent_id: 代理ID

        Returns:
            bool: 是否正在请求
        """
        return any(agent_id == agent_id for _, _, _, agent_id, _ in self.request_queues.queue)

    def allocate_resource(self, resource_id: str, agent) -> bool:
        """
        分配资源给代理

        Args:
            resource_id: 资源ID
            agent: 代理

        Returns:
            bool: 是否成功分配
        """
        resource = self.find_resource_by_id(resource_id)
        if not resource:
            return False

        # 检查资源是否有可用容量
        if not resource.has_capacity():
            return False

        # 初始化资源分配字典
        if resource_id not in self.resource_allocations:
            self.resource_allocations[resource_id] = {}

        # 分配资源
        agent_id = agent.id

        # 注册监听器，监听代理移除对象事件
        listener_id = f"resource_{resource_id}_agent_{agent_id}"

        def on_agent_object_removed(event_data):
            if isinstance(event_data, dict) and event_data.get('object_id') == resource_id:
                self.release_resource(resource_id, agent_id)

        self.env.event_registry.subscribe(
            agent_id, 'possessing_object_removed', listener_id, on_agent_object_removed
        )

        # 记录分配信息
        self.resource_allocations[resource_id][agent_id] = (agent, listener_id)

        # 更新资源状态
        resource.allocate(agent_id)

        logger.info(f"时间 {self.env.now}: 资源 {resource_id} 已分配给代理 {agent_id}")
        return True

    def release_resource(self, resource_id: str, agent_id: str) -> bool:
        """
        释放资源

        Args:
            resource_id: 资源ID
            agent_id: 代理ID

        Returns:
            bool: 是否成功释放
        """
        # 检查资源是否存在
        if resource_id not in self.resources:
            return False

        # 检查资源是否已分配给该代理
        if (resource_id not in self.resource_allocations or
            agent_id not in self.resource_allocations[resource_id]):
            return False

        # 获取代理和监听器ID
        _, listener_id = self.resource_allocations[resource_id][agent_id]

        # 取消监听
        self.env.event_registry.unsubscribe(agent_id, 'possessing_object_removed', listener_id)

        # 移除分配记录
        del self.resource_allocations[resource_id][agent_id]

        # 更新资源状态
        resource = self.resources[resource_id]
        resource.release(agent_id)

        logger.info(f"时间 {self.env.now}: 代理 {agent_id} 释放了资源 {resource_id}")

        # 处理请求队列
        self._process_request_queue()

        return True

    def _process_request_queue(self):
        """处理请求队列，尝试分配可用资源"""
        # 检查队列是否为空
        if self.request_queues.empty():
            return

        # 创建一个临时列表存储无法处理的请求
        pending_requests = []

        # 处理队列中的所有请求
        while not self.request_queues.empty():
            priority, request_time, resource_id, agent_id, agent = self.request_queues.get()

            # 检查资源是否存在
            resource = self.find_resource_by_id(resource_id)
            if not resource:
                continue

            # 检查资源是否有可用容量
            if resource.has_capacity():
                # 分配资源
                self.allocate_resource(resource_id, agent)
            else:
                # 将请求放回临时列表
                pending_requests.append((priority, request_time, resource_id, agent_id, agent))

        # 将未处理的请求重新加入队列
        for request in pending_requests:
            self.request_queues.put(request)

    def register_resource(self, resource: LandingResource) -> bool:
        """
        注册着陆区资源到管理器，并在空域管理器中注册位置

        Args:
            resource: 着陆区资源对象

        Returns:
            注册是否成功
        """
        resource.env = self.env
        # 调用父类的注册方法
        if not super().register_resource(resource):
            return False

        # 如果有空域管理器，则在空域中注册着陆点位置
        if self.airspace_manager:
            # 确保位置是三维坐标
            location = resource.location
            if len(location) == 2:
                location = (location[0], location[1], 0)

            # 在空域管理器中注册着陆点位置，使用landing_前缀区分代理
            self.airspace_manager.register_landing(resource.id, location)

        return True

    def unregister_resource(self, resource_id: str) -> bool:
        """
        从管理器移除资源，并从空域管理器中移除位置

        Args:
            resource_id: 资源ID

        Returns:
            移除是否成功
        """
        # 如果有空域管理器，先从空域中移除着陆点位置
        if self.airspace_manager and resource_id in self.resources:
            self.airspace_manager.remove_landing(resource_id)

        # 调用父类的移除方法
        return super().unregister_resource(resource_id)

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
            attributes=attributes,
            env=self.env
        )

        # 注册资源
        if self.register_resource(landing_spot):
            return resource_id

        return None

    def create_charging_spot(self, location: tuple, radius: float = 15.0, max_capacity: int = 3,
                            charging_power: float = 300.0, has_data_transfer: bool = False,
                            name: str = None, attributes: dict = None) -> str:
        """
        创建并注册充电站着陆点

        Args:
            location: 坐标 (x, y) 或 (x, y, z)
            radius: 半径 (米)
            max_capacity: 最大容量
            charging_power: 充电功率 (W)
            has_data_transfer: 是否具备数据传输功能
            name: 名称
            attributes: 附加属性

        Returns:
            创建的资源ID，如果创建失败则返回None
        """
        # 准备属性
        attrs = attributes or {}
        if name:
            attrs['name'] = name
        else:
            attrs['name'] = f'充电站_{len(self.resources) + 1}'

        attrs['charging_power'] = charging_power

        if has_data_transfer:
            attrs['data_transfer_rate'] = attrs.get('data_transfer_rate', 20.0)

        # 创建着陆点
        return self.create_landing_spot(
            location=location,
            radius=radius,
            max_capacity=max_capacity,
            has_charging=True,
            has_data_transfer=has_data_transfer,
            attributes=attrs
        )

    def create_data_station(self, location: tuple, radius: float = 12.0, max_capacity: int = 2,
                           data_transfer_rate: float = 100.0, has_charging: bool = False,
                           name: str = None, attributes: dict = None) -> str:
        """
        创建并注册数据站着陆点

        Args:
            location: 坐标 (x, y) 或 (x, y, z)
            radius: 半径 (米)
            max_capacity: 最大容量
            data_transfer_rate: 数据传输速率 (Mbps)
            has_charging: 是否具备充电功能
            name: 名称
            attributes: 附加属性

        Returns:
            创建的资源ID，如果创建失败则返回None
        """
        # 准备属性
        attrs = attributes or {}
        if name:
            attrs['name'] = name
        else:
            attrs['name'] = f'数据站_{len(self.resources) + 1}'

        attrs['data_transfer_rate'] = data_transfer_rate

        if has_charging:
            attrs['charging_power'] = attrs.get('charging_power', 100.0)

        # 创建着陆点
        return self.create_landing_spot(
            location=location,
            radius=radius,
            max_capacity=max_capacity,
            has_charging=has_charging,
            has_data_transfer=True,
            attributes=attrs
        )

    def create_base_station(self, location: tuple, radius: float = 50.0, max_capacity: int = 10,
                           charging_power: float = 500.0, data_transfer_rate: float = 100.0,
                           name: str = None, attributes: dict = None) -> str:
        """
        创建并注册基地站着陆点（同时具备充电和数据传输功能）

        Args:
            location: 坐标 (x, y) 或 (x, y, z)
            radius: 半径 (米)
            max_capacity: 最大容量
            charging_power: 充电功率 (W)
            data_transfer_rate: 数据传输速率 (Mbps)
            name: 名称
            attributes: 附加属性

        Returns:
            创建的资源ID，如果创建失败则返回None
        """
        # 准备属性
        attrs = attributes or {}
        if name:
            attrs['name'] = name
        else:
            attrs['name'] = f'基地站_{len(self.resources) + 1}'

        attrs['charging_power'] = charging_power
        attrs['data_transfer_rate'] = data_transfer_rate

        # 创建着陆点
        return self.create_landing_spot(
            location=location,
            radius=radius,
            max_capacity=max_capacity,
            has_charging=True,
            has_data_transfer=True,
            attributes=attrs
        )

    def create_emergency_landing_spot(self, location: tuple, radius: float = 8.0, max_capacity: int = 1,
                                     name: str = None, attributes: dict = None) -> str:
        """
        创建并注册紧急着陆点（无充电和数据传输功能）

        Args:
            location: 坐标 (x, y) 或 (x, y, z)
            radius: 半径 (米)
            max_capacity: 最大容量
            name: 名称
            attributes: 附加属性

        Returns:
            创建的资源ID，如果创建失败则返回None
        """
        # 准备属性
        attrs = attributes or {}
        if name:
            attrs['name'] = name
        else:
            attrs['name'] = f'紧急着陆点_{len(self.resources) + 1}'

        attrs['emergency'] = True

        # 创建着陆点
        return self.create_landing_spot(
            location=location,
            radius=radius,
            max_capacity=max_capacity,
            has_charging=False,
            has_data_transfer=False,
            attributes=attrs
        )

    def create_landing_grid(self, center: tuple, rows: int = 3, cols: int = 3, spacing: float = 100.0,
                           spot_radius: float = 10.0, max_capacity: int = 1,
                           has_charging: bool = False, has_data_transfer: bool = False,
                           name_prefix: str = "网格着陆点", attributes: dict = None) -> list:
        """
        创建网格状的多个着陆点

        Args:
            center: 网格中心坐标 (x, y) 或 (x, y, z)
            rows: 行数
            cols: 列数
            spacing: 着陆点间距 (米)
            spot_radius: 每个着陆点的半径 (米)
            max_capacity: 每个着陆点的最大容量
            has_charging: 是否具备充电功能
            has_data_transfer: 是否具备数据传输功能
            name_prefix: 名称前缀
            attributes: 附加属性

        Returns:
            创建的资源ID列表
        """
        # 确保中心点是三维坐标
        if len(center) == 2:
            center = (center[0], center[1], 0)

        # 计算网格的起始点（左上角）
        start_x = center[0] - (cols - 1) * spacing / 2
        start_y = center[1] - (rows - 1) * spacing / 2
        z = center[2]

        # 创建网格中的每个着陆点
        landing_ids = []
        for row in range(rows):
            for col in range(cols):
                # 计算当前着陆点的位置
                x = start_x + col * spacing
                y = start_y + row * spacing

                # 准备属性
                attrs = attributes.copy() if attributes else {}
                attrs['name'] = f"{name_prefix}_{row+1}_{col+1}"
                attrs['grid_row'] = row
                attrs['grid_col'] = col

                # 创建着陆点
                landing_id = self.create_landing_spot(
                    location=(x, y, z),
                    radius=spot_radius,
                    max_capacity=max_capacity,
                    has_charging=has_charging,
                    has_data_transfer=has_data_transfer,
                    attributes=attrs
                )

                if landing_id:
                    landing_ids.append(landing_id)

        return landing_ids

    def _setup_data_provider_subscriptions(self):
        """Sets up subscriptions to events from registered DataProviders."""
        try:
            # Import locally if needed, or ensure it's imported at the top
            from airfogsim.dataprovider.weather import WeatherDataProvider

            weather_provider = self.env.get_data_provider('weather')
            if weather_provider and isinstance(weather_provider, WeatherDataProvider):
                # Use functools.partial to bind 'self' (the manager instance) to the callback
                bound_callback = functools.partial(weather_provider.on_weather_changed, self)
                listener_id = f"{self.__class__.__name__}_weather_listener" # Unique listener ID
                self.env.event_registry.subscribe(
                    event_name=WeatherDataProvider.EVENT_WEATHER_CHANGED,
                    callback=bound_callback,
                    listener_id=listener_id,
                )
                logger.info(f"{self.__class__.__name__} subscribed to {WeatherDataProvider.EVENT_WEATHER_CHANGED}")

            # Add subscriptions for other relevant data providers here
        except ImportError:
            logger.warning("WeatherDataProvider not found, cannot subscribe to weather events.")
        except Exception as e:
            logger.error(f"Error setting up DataProvider subscriptions for {self.__class__.__name__}: {e}")


    def update_resource_status_by_region(self, region_data: Any, new_status: ResourceStatus):
        """
        Updates the status of landing resources within a specified region based on external conditions (e.g., weather).

        Args:
            region_data: Data defining the affected region (e.g., polygon, zone ID).
                         Interpretation depends on implementation of _is_resource_in_region.
            new_status (ResourceStatus): The new status to potentially set (e.g., UNAVAILABLE_WEATHER, AVAILABLE).
        """
        updated_count = 0
        for resource_id, resource in self.resources.items():
            if self._is_resource_in_region(resource, region_data):
                current_status = resource.status
                status_changed = False

                if new_status == ResourceStatus.UNAVAILABLE_WEATHER:
                    # Directly set to unavailable due to weather
                    if current_status != ResourceStatus.UNAVAILABLE_WEATHER.value:
                        resource.set_status(ResourceStatus.UNAVAILABLE_WEATHER.value)
                        status_changed = True
                        logger.info(f"Landing resource {resource_id} status set to UNAVAILABLE_WEATHER at time {self.env.now}")

                elif new_status == ResourceStatus.AVAILABLE:
                    # Try to set back to available only if previously unavailable due to weather
                    # and other conditions are met (normal condition, no allocations).
                    if current_status == ResourceStatus.UNAVAILABLE_WEATHER.value:
                        if resource.condition == "normal" and not resource.current_allocations:
                            resource.set_status(ResourceStatus.AVAILABLE.value)
                            status_changed = True
                            logger.info(f"Landing resource {resource_id} status set back to AVAILABLE from weather at time {self.env.now}")
                        # else:
                            # logger.debug(f"Landing resource {resource_id} weather cleared, but condition ({resource.condition}) or allocations ({len(resource.current_allocations)}) prevent setting AVAILABLE.")
                # else: Handle other potential statuses if needed

                if status_changed:
                    updated_count += 1

        if updated_count > 0:
            logger.info(f"Updated status for {updated_count} landing resources in region due to external condition at time {self.env.now}")


    def _is_resource_in_region(self, landing_resource: LandingResource, region_data: Any) -> bool:
        """
        Placeholder: Checks if a landing resource is within the specified region.
        Needs actual implementation based on how regions are defined (polygons, zones, etc.).
        """
        # TODO: Implement actual region checking logic.
        # Example using simple bounding box if region_data is like {'min_x': ..., 'max_x': ...}
        if isinstance(region_data, dict) and 'min_x' in region_data:
            loc = landing_resource.location
            return (region_data['min_x'] <= loc[0] <= region_data['max_x'] and
                    region_data['min_y'] <= loc[1] <= region_data['max_y'])

        # Example using AirspaceManager if region_data is a center point and radius
        if isinstance(region_data, dict) and 'center' in region_data and 'radius' in region_data:
            loc = landing_resource.location
            dist_sq = (loc[0] - region_data['center'][0])**2 + \
                      (loc[1] - region_data['center'][1])**2 + \
                      (loc[2] - region_data['center'][2])**2
            return dist_sq <= region_data['radius']**2

        # Simplified default: Assume affects all resources if region_data is not None
        return region_data is not None
