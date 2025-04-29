# manager/frequency.py

import functools # Added for partial
from typing import List, Dict, Optional, Tuple, Set, Any, Callable # Added Any, Callable
import numpy as np
import time
from airfogsim.utils.logging_config import get_logger
from collections import defaultdict
from airfogsim.core.resource import ResourceManager
from airfogsim.core.enums import ResourceStatus, AllocationStatus # 导入枚举
from airfogsim.resource.frequency import FrequencyResource

# Configure logging
logger = get_logger(__name__)

class FrequencyManager(ResourceManager[FrequencyResource]):
    """
    频率资源管理器

    管理预定义的、离散的、固定带宽的频率资源块
    """
    def __init__(self, env=None, total_bandwidth: float = 100.0, block_bandwidth: float = 5.0,
                 start_frequency: float = 2400.0, power_limit: float = 100.0,
                 update_interval: float = 1.0, config: Dict = None):
        """
        初始化频率资源管理器

        Args:
            env: 仿真环境
            total_bandwidth: 总带宽 (MHz)
            block_bandwidth: 每个资源块的带宽 (MHz)
            start_frequency: 起始频率 (MHz)
            power_limit: 功率限制 (mW)
            update_interval: 信道状态更新间隔（秒）
            config: 配置字典，可能包含以下字段:
                - link_quality_calculator: 链路质量计算函数，用于计算SINR和其他链路质量指标
        """
        super().__init__(env)
        self.id = "frequency_manager"

        # 配置参数
        self.config = config or {}

        # 链路质量计算器（Hook函数）
        self.link_quality_calculator = None
        calculator_func = self.config.get('link_quality_calculator')
        if callable(calculator_func):
            self.link_quality_calculator = calculator_func
            logger.info("Using external link quality calculator.")
        else:
            logger.info("Using internal SINR calculation model.")

        # 代理位置信息缓存，用于计算信道状态
        # 格式: {agent_id: (x, y, z)}
        self.agent_positions = {}

        # 发射功率信息，用于计算信道状态
        # 格式: {agent_id: power_mw}
        self.transmit_powers = {}

        # 资源分配优化
        self.available_resources_cache = set()  # 可用资源ID缓存
        self.resource_by_frequency = {}  # 频率到资源ID的映射
        self.last_update_time = 0.0  # 上次信道状态更新时间
        self.update_interval = update_interval  # 信道状态更新间隔

        # 性能指标
        self.allocation_times = []
        self.channel_update_times = []

        # 订阅环境的visual_update事件，用于更新频率资源的干扰情况
        if env and hasattr(env, 'event_registry'):
            env.event_registry.subscribe(env.id, 'visual_update', self.id, self._on_visual_update)

        # 初始化离散频率资源块
        self._initialize_resource_blocks(total_bandwidth, block_bandwidth, start_frequency, power_limit)

        # Setup subscriptions to DataProvider events
        self._setup_data_provider_subscriptions()

    def _initialize_resource_blocks(self, total_bandwidth: float, block_bandwidth: float,
                                    start_frequency: float, power_limit: float) -> None:
        """
        初始化离散频率资源块

        Args:
            total_bandwidth: 总带宽 (MHz)
            block_bandwidth: 每个资源块的带宽 (MHz)
            start_frequency: 起始频率 (MHz)
            power_limit: 功率限制 (mW)
        """
        # 计算资源块数量
        num_blocks = int(total_bandwidth / block_bandwidth)

        # 创建资源块
        for i in range(num_blocks):
            center_frequency = start_frequency + (i + 0.5) * block_bandwidth
            resource_id = f"freq_block_{i+1}"

            # 创建频率资源块
            resource = FrequencyResource(
                resource_id=resource_id,
                center_frequency=center_frequency,
                bandwidth=block_bandwidth,
                max_users=5,  # 每个资源块可以分配给多个链路
                power_limit=power_limit
            )

            # 注册资源
            self.register_resource(resource)

            # 更新缓存
            self.available_resources_cache.add(resource_id)
            self.resource_by_frequency[center_frequency] = resource_id

    def request_resource(self, source_id: str, target_id: str, power_db: float = 20.0, requirements: Dict = None) -> List[str]:
        """
        请求分配可用的频率资源块用于源和目标代理之间的通信

        Args:
            source_id: 源代理ID
            target_id: 目标代理ID
            power_db: 发射功率(dBm)
            requirements: 资源需求（可选），可能包含如下字段：
                - min_bandwidth: 最小带宽 (MHz)
                - preferred_frequency: 优先分配的频率范围 (MHz)
                - required_blocks: 需要的资源块数量 (默认为1)

        Returns:
            分配的资源块ID列表，如果没有可用资源则返回空列表
        """
        start_time = time.time()
        requirements = requirements or {}

        # 使用缓存找到所有可用的资源块
        available_resources = [self.resources[res_id] for res_id in self.available_resources_cache]

        if not available_resources:
            return None

        # 应用筛选条件
        filtered_resources = available_resources

        # 按优先频率范围排序
        if 'preferred_frequency' in requirements and filtered_resources:
            preferred_freq = requirements['preferred_frequency']
            filtered_resources.sort(
                key=lambda r: abs(r.center_frequency - preferred_freq)
            )

        # 计算所需的总带宽
        required_bandwidth = requirements.get('min_bandwidth', 0)
        required_blocks = requirements.get('required_blocks', max(1, int(required_bandwidth / filtered_resources[0].bandwidth)))

        # 如果没有可用资源，返回空列表
        if not filtered_resources:
            return []

        # 选择足够多的资源块，使总带宽满足要求
        selected_resources = []
        total_bandwidth = 0

        # 首先尝试选择足够的资源块以满足带宽要求
        for resource in filtered_resources[:required_blocks]:
            if total_bandwidth >= required_bandwidth:
                break

            selected_resources.append(resource)
            total_bandwidth += resource.bandwidth

        # 如果无法满足带宽要求，返回空列表
        if total_bandwidth < required_bandwidth:
            return []

        # 分配资源块
        allocated_resource_ids = []

        for resource in selected_resources:
            # 分配资源块
            if resource.assign_to(source_id, target_id, power_db):
                # 记录分配
                allocation_id = f"alloc_{source_id}_{target_id}_{resource.id}"

                # 更新分配记录
                allocation_info = {
                    'id': allocation_id,
                    'resource_id': resource.id,
                    'source_id': source_id,
                    'target_id': target_id,
                    'requirements': requirements,
                    'start_time': self.env.now,
                    'status': AllocationStatus.ACTIVE # 使用枚举
                }

                self.allocations[allocation_id] = allocation_info

                # 更新索引
                if resource.id not in self.resource_allocations:
                    self.resource_allocations[resource.id] = {}
                self.resource_allocations[resource.id][allocation_id] = True

                if source_id not in self.user_allocations:
                    self.user_allocations[source_id] = []
                self.user_allocations[source_id].append(allocation_id)

                # 触发频率链路活动更新事件
                if hasattr(self.env, 'event_registry'):
                    self.env.event_registry.trigger_event(
                        self.id,
                        'FrequencyLinkActivityUpdate',
                        {
                            'source_id': source_id,
                            'target_id': target_id,
                            'resource_id': resource.id,
                            'center_frequency': resource.center_frequency,
                            'bandwidth': resource.bandwidth,
                            'transmit_power_dbm': power_db,
                            'status': 'allocated'
                        }
                    )

                # 添加到已分配资源ID列表
                allocated_resource_ids.append(resource.id)

                # 更新可用资源缓存
                if resource.status != ResourceStatus.AVAILABLE:
                    self.available_resources_cache.discard(resource.id)
            else:
                # 如果分配失败，释放已分配的资源
                for res_id in allocated_resource_ids:
                    res = self.resources.get(res_id)
                    if res:
                        res.release(source_id, target_id)
                        # 更新可用资源缓存
                        if res.status == ResourceStatus.AVAILABLE:
                            self.available_resources_cache.add(res.id)
                return []

        # 记录分配时间
        self.allocation_times.append(time.time() - start_time)

        return allocated_resource_ids

    def release_resource(self, source_id: str, target_id: str, resource_ids: List[str]=None) -> bool:
        """
        释放源和目标代理之间的频率资源块

        Args:
            source_id: 源代理ID
            target_id: 目标代理ID
            resource_id: 资源块ID列表

        Returns:
            是否释放成功
        """
        start_time = time.time()

        # 如果未指定资源块ID，查找链路使用的所有资源块
        if resource_ids is None:
            resources_to_check = list(self.resources.keys())
        else:
            resources_to_check = resource_ids

        success = True

        # 遍历所有指定的资源块
        for res_id in resources_to_check:
            if res_id not in self.resources:
                continue

            resource = self.resources[res_id]

            # 检查链路是否分配了该资源块
            if (source_id, target_id) not in resource.assigned_to:
                continue

            # 释放资源
            if resource.release(source_id, target_id):
                # 更新分配记录
                for allocation_id in list(self.resource_allocations.get(res_id, {}).keys()):
                    if allocation_id in self.allocations:
                        alloc = self.allocations[allocation_id]
                        if alloc.get('source_id') == source_id and alloc.get('target_id') == target_id:
                            alloc['status'] = AllocationStatus.RELEASED # 使用枚举
                            alloc['end_time'] = self.env.now

                # 触发频率链路活动更新事件
                if hasattr(self.env, 'event_registry'):
                    self.env.event_registry.trigger_event(
                        self.id,
                        'FrequencyLinkActivityUpdate',
                        {
                            'source_id': source_id,
                            'target_id': target_id,
                            'resource_id': res_id,
                            'center_frequency': resource.center_frequency,
                            'bandwidth': resource.bandwidth,
                            'status': 'released'
                        }
                    )

                # 更新可用资源缓存
                if resource.status == ResourceStatus.AVAILABLE:
                    self.available_resources_cache.add(res_id)
            else:
                success = False

        # 记录释放时间
        self.allocation_times.append(time.time() - start_time)

        return success

    def _on_visual_update(self, event_data=None):
        """
        处理周期性更新事件，计算信道状态数据
        """
        # 只在指定间隔更新信道状态
        current_time = self.env.now
        if current_time - self.last_update_time >= self.update_interval:
            self.update_channel_conditions()
            self.last_update_time = current_time

    def update_channel_conditions(self) -> None:
        """
        更新所有活跃链路的信道状态

        计算每个活动链路的SINR，考虑同频干扰
        """
        start_time = time.time()

        # 确保环境存在
        if not self.env or not hasattr(self.env, 'airspace_manager'):
            return

        # 遍历所有资源块
        for resource in self.resources.values():
            if not resource.assigned_to:  # 跳过未分配的资源块
                continue

            # 对每个资源块计算已分配用户pair之间的干扰和接收sinr
            self._calculate_in_block_sinr(resource)

        # 记录信道更新时间
        self.channel_update_times.append(time.time() - start_time)

    def _get_nearby_agents(self, position, radius):
        """
        获取指定位置周围的代理

        Args:
            position: 位置 (x, y, z)
            radius: 半径（米）

        Returns:
            附近代理ID列表
        """
        # 确保环境存在且有空域管理器
        if not self.env or not hasattr(self.env, 'airspace_manager'):
            return []

        # 直接使用空域管理器的get_nearby_objects方法
        nearby_objects = self.env.airspace_manager.get_nearby_objects(position=position, radius=radius)

        # 返回附近代理的ID列表
        return list(nearby_objects.keys())


    def _calculate_path_loss(self, tx_position: Tuple[float, float, float],
                            rx_position: Tuple[float, float, float]) -> float:
        """
        计算路径损耗

        使用简化的自由空间路径损耗模型

        Args:
            tx_position: 发射端位置 (x, y, z)，单位为米
            rx_position: 接收端位置 (x, y, z)，单位为米

        Returns:
            路径损耗 (dB)
        """
        # 计算距离
        dx = tx_position[0] - rx_position[0]
        dy = tx_position[1] - rx_position[1]
        dz = tx_position[2] - rx_position[2]
        distance = np.sqrt(dx**2 + dy**2 + dz**2)

        # 避免距离为0
        distance = max(distance, 1.0)

        # 自由空间路径损耗模型 (dB)
        # PL = 20*log10(d) + 20*log10(f) - 27.55
        # 这里假设频率为2.4GHz
        path_loss = 20 * np.log10(distance) + 20 * np.log10(2400) - 27.55

        return path_loss

    def _calculate_fast_fading(self) -> float:
        """
        计算快速衰落

        使用简化的瑞利衰落模型

        Returns:
            快速衰落 (dB)
        """
        # 简化的瑞利衰落模型
        # 生成两个高斯随机变量
        x = np.random.normal(0, 1)
        y = np.random.normal(0, 1)

        # 计算瑞利分布的随机变量
        rayleigh = np.sqrt(x**2 + y**2)

        # 转换为dB
        fading_db = 20 * np.log10(rayleigh)

        return fading_db

    def _calculate_in_block_sinr(self, resource: FrequencyResource) -> None:
        """
        计算资源块内部各链路的SINR，使用矩阵运算提高效率

        Args:
            resource: 频率资源块
        """
        # 确保环境存在
        if not self.env or not hasattr(self.env, 'airspace_manager'):
            return

        # 获取airspace_manager
        airspace_manager = self.env.airspace_manager

        # 如果资源块未分配给任何链路，直接返回
        if not resource.assigned_to:
            return

        # 预处理：获取资源块内所有链路的位置和功率信息
        link_info = []
        link_ids = []

        for (source_id, target_id), power_db in resource.assigned_to.items():
            # 从airspace_manager获取位置信息
            source_position = airspace_manager.get_agent_position(source_id)
            target_position = airspace_manager.get_agent_position(target_id)

            # 如果没有位置信息，跳过
            if not source_position or not target_position:
                continue

            link_ids.append((source_id, target_id))
            link_info.append({
                'source_position': source_position,
                'target_position': target_position,
                'power_db': power_db
            })

        if not link_info:
            return

        num_links = len(link_info)

        # 构建位置和功率矩阵
        source_positions = np.zeros((num_links, 3))
        target_positions = np.zeros((num_links, 3))
        power_db_values = np.zeros(num_links)

        for i in range(num_links):
            source_positions[i] = link_info[i]['source_position']
            target_positions[i] = link_info[i]['target_position']
            power_db_values[i] = link_info[i]['power_db']

        # 检查是否使用外部链路质量计算器（Hook函数）
        if self.link_quality_calculator is not None:
            # 使用外部链路质量计算器计算SINR
            rx_power_dbm = np.zeros(num_links)
            interference_dbm = np.full(num_links, -float('inf'))
            sinr = np.zeros(num_links)
            noise_dbm = self._calculate_thermal_noise(resource.bandwidth)  # 默认噪声值，可能被覆盖

            for i in range(num_links):
                source_id, target_id = link_ids[i]
                source_pos = source_positions[i]
                target_pos = target_positions[i]
                power_db = power_db_values[i]

                # 准备链路上下文
                link_context = {
                    'source_id': source_id,
                    'target_id': target_id,
                    'source_position': source_pos,
                    'target_position': target_pos,
                    'transmit_power_dbm': power_db,
                    'center_frequency': resource.center_frequency,
                    'bandwidth': resource.bandwidth,
                    'resource_id': resource.id
                }

                # 调用外部链路质量计算器
                quality_info = self.link_quality_calculator(link_context, self.env)

                # 提取计算结果
                if quality_info:
                    rx_power_dbm[i] = quality_info.get('received_power_dbm', 0)
                    interference_dbm[i] = quality_info.get('interference_dbm', -float('inf'))
                    sinr[i] = quality_info.get('sinr', 0)
                    # 如果提供了噪声值，则更新
                    if 'noise_dbm' in quality_info:
                        noise_dbm = quality_info.get('noise_dbm')
                else:
                    # 如果计算器返回None或空字典，使用默认值
                    logger.warning(f"Link quality calculator returned no data for link {source_id}->{target_id}")
                    # 使用内部方法计算基本值
                    path_loss = self._calculate_path_loss(source_pos, target_pos)
                    fast_fading = self._calculate_fast_fading()
                    rx_power_dbm[i] = power_db - path_loss + fast_fading
                    sinr[i] = rx_power_dbm[i] - noise_dbm  # 假设无干扰

        else:
            # 使用内部方法计算SINR
            # 1. 计算直接链路的路径损耗矩阵 (num_links,)
            direct_path_losses = np.zeros(num_links)
            for i in range(num_links):
                direct_path_losses[i] = self._calculate_path_loss(
                    source_positions[i], target_positions[i]
                )

            # 2. 计算快速衰落
            fast_fadings = np.array([self._calculate_fast_fading() for _ in range(num_links)])

            # 3. 计算接收信号功率 (dBm)
            rx_power_dbm = power_db_values - direct_path_losses + fast_fadings

            # 4. 计算干扰矩阵 (所有发射机到所有接收机的路径损耗)
            # 创建 (num_links x num_links) 干扰矩阵
            interference_path_losses = np.zeros((num_links, num_links))

            for i in range(num_links):  # 接收机索引
                for j in range(num_links):  # 发射机索引
                    if i != j:  # 跳过自身链路
                        interference_path_losses[i, j] = self._calculate_path_loss(
                            source_positions[j], target_positions[i]
                        )

            # 5. 将功率从dBm转换为mW
            power_mw = 10 ** (power_db_values / 10)

            # 6. 计算线性域中的路径损耗因子
            path_loss_factors = 10 ** (-interference_path_losses / 10)

            # 7. 计算每个链路的干扰功率（mW）
            # 对角线设为0，不计算自干扰
            np.fill_diagonal(path_loss_factors, 0)

            # 计算干扰功率 (mW)：power_mw * path_loss_factors，矩阵乘法
            interference_mw_matrix = np.outer(power_mw, np.ones(num_links)) * path_loss_factors
            interference_mw_sum = np.sum(interference_mw_matrix, axis=1)  # 按行求和

            # 8. 计算噪声功率 (dBm)
            noise_dbm = self._calculate_thermal_noise(resource.bandwidth)
            noise_mw = 10 ** (noise_dbm / 10)

            # 9. 计算干扰功率 (dBm)并处理无干扰情况
            has_interference = interference_mw_sum > 0
            interference_dbm = np.full(num_links, -float('inf'))
            interference_dbm[has_interference] = 10 * np.log10(interference_mw_sum[has_interference])

            # 10. 计算SINR (dB)
            sinr = np.zeros(num_links)

            # 对有干扰的链路
            has_interference_mask = interference_dbm > -float('inf')
            if np.any(has_interference_mask):
                # 干扰加噪声功率 (线性域相加)
                interference_noise_mw = 10 ** (interference_dbm[has_interference_mask] / 10) + noise_mw
                interference_noise_dbm = 10 * np.log10(interference_noise_mw)
                sinr[has_interference_mask] = rx_power_dbm[has_interference_mask] - interference_noise_dbm

            # 对无干扰的链路
            no_interference_mask = ~has_interference_mask
            if np.any(no_interference_mask):
                # 无干扰，仅考虑噪声
                sinr[no_interference_mask] = rx_power_dbm[no_interference_mask] - noise_dbm

        # 11. 更新每个链路的信道状态
        for i in range(num_links):
            resource.update_channel_condition(
                noise_level=noise_dbm,
                interference=interference_dbm[i],
                sinr=sinr[i]
            )

    def _calculate_thermal_noise(self, bandwidth_mhz: float) -> float:
        """
        计算热噪声功率

        Args:
            bandwidth_mhz: 带宽 (MHz)

        Returns:
            噪声功率 (dBm)
        """
        # 玻尔兹曼常数 (J/K)
        k = 1.38e-23

        # 温度 (K)，假设室温
        T = 290

        # 带宽 (Hz)
        B = bandwidth_mhz * 1e6

        # 热噪声功率 (W)
        N = k * T * B

        # 转换为mW
        N_mw = N * 1000

        # 转换为dBm
        N_dbm = 10 * np.log10(N_mw)

        return N_dbm
    def get_resources(self, resource_ids:List[str])->List[FrequencyResource]:
        resources = []
        for resource_id in resource_ids:
            resources.append(self.resources[resource_id])
        return resources

    def get_resource_status(self) -> Dict[str, Dict]:
        """
        获取所有资源块的状态

        Returns:
            资源块状态字典，键为资源ID，值为状态信息
        """
        status = {}

        for resource_id, resource in self.resources.items():
            status[resource_id] = {
                'center_frequency': resource.center_frequency,
                'bandwidth': resource.bandwidth,
                'status': resource.status,
                'assigned_to': resource.assigned_to.copy() if resource.assigned_to else [],
                'sinr': resource.sinr,
                'noise_level': resource.noise_level,
                'interference': resource.interference
            }

        return status

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

    def update_pathloss_parameters(self, event_data: Dict[str, Any]):
        """
        Placeholder method called by WeatherDataProvider callback.
        Updates path loss model parameters based on weather conditions.

        Args:
            event_data (Dict[str, Any]): The weather event data.
        """
        # TODO: Implement logic to adjust path loss calculation based on weather
        # e.g., consider rain attenuation based on event_data['precipitation_rate']
        precipitation_rate = event_data.get('precipitation_rate', 0)
        if precipitation_rate > 0:
             logger.info(f"FrequencyManager received weather update (precipitation: {precipitation_rate} mm/hr). Path loss parameters might need adjustment (Not implemented yet).")
        # Example: self.path_loss_model.update_rain_attenuation(precipitation_rate)

    def get_performance_metrics(self) -> Dict[str, Any]:
        """
        获取频率管理器的性能指标

        Returns:
            性能指标字典
        """
        metrics = {
            'avg_allocation_time': np.mean(self.allocation_times) if self.allocation_times else 0,
            'max_allocation_time': np.max(self.allocation_times) if self.allocation_times else 0,
            'avg_channel_update_time': np.mean(self.channel_update_times) if self.channel_update_times else 0,
            'max_channel_update_time': np.max(self.channel_update_times) if self.channel_update_times else 0,
            'num_allocations': len(self.allocation_times),
            'num_channel_updates': len(self.channel_update_times),
            'available_resources': len(self.available_resources_cache),
            'total_resources': len(self.resources)
        }

        return metrics