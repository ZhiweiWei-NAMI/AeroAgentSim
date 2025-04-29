# resource/frequency.py

from airfogsim.core.resource import Resource
from airfogsim.core.enums import ResourceStatus # 导入枚举
from airfogsim.utils.logging_config import get_logger

# 获取logger
logger = get_logger(__name__)
class FrequencyResource(Resource):
    """
    频率资源块类

    表示预定义的、离散的、固定带宽的频率资源块
    """

    def __init__(self, resource_id: str,
                 center_frequency: float,
                 bandwidth: float,
                 max_users: int = 1,
                 power_limit: float = 100.0,
                 attributes: dict = None):
        """
        初始化频率资源块

        Args:
            resource_id: 资源唯一标识符
            center_frequency: 中心频率，单位为MHz
            bandwidth: 带宽，单位为MHz
            max_users: 可同时使用该频率资源的最大用户数（通常为1，表示正交资源块）
            power_limit: 发射功率限制，单位为mW
            attributes: 其他属性
        """
        super().__init__(resource_id, attributes)

        # 频率基本参数
        self.center_frequency = center_frequency
        self.bandwidth = bandwidth
        self.max_users = max_users
        self.power_limit = power_limit

        # 当前信道状态
        self.noise_level = -100.0  # 噪声水平，单位dBm
        self.interference = 0.0  # 干扰水平，单位dBm
        self.sinr = 0.0  # 信噪干扰比，单位dB
        self.utilization = 0.0  # 当前利用率 (0.0-1.0)

        # 分配信息
        self.assigned_to = {}  # 分配给的用户/设备ID

    def assign_to(self, source_id: str, target_id:str, power_db) -> bool:
        """
        将资源块分配给用户

        Args:
            source_id
            target_id

        Returns:
            是否分配成功
        """
        if self.assigned_to.get((source_id, target_id)):
            logger.info('已分配频谱资源')
            return False
        self.assigned_to[(source_id, target_id)] = power_db
        self._check_available()
        return True

    def _check_available(self):
        if len(self.assigned_to)<self.max_users:
            self.status = ResourceStatus.AVAILABLE # 使用枚举
        else:
            self.status = ResourceStatus.FULLY_ALLOCATED # 使用枚举

    def release(self, source_id: str, target_id:str) -> bool:
        """
        释放资源块

        Returns:
            是否释放成功
        """
        if self.assigned_to is None:
            return False

        del self.assigned_to[(source_id, target_id)]
        self._check_available()
        return True

    def update_channel_condition(self, noise_level: float = None, interference: float = None, sinr: float = None) -> None:
        """
        更新信道状态

        Args:
            noise_level: 新的噪声水平 (dBm)
            interference: 新的干扰水平 (dBm)
            sinr: 新的信噪干扰比 (dB)
        """
        if noise_level is not None:
            self.noise_level = noise_level

        if interference is not None:
            self.interference = interference

        if sinr is not None:
            self.sinr = sinr

        # 根据信道状况可能影响可用性
        if self.sinr < 0 or self.noise_level > -70.0:
            self.status = ResourceStatus.MAINTENANCE # 使用枚举
        else:
            # 如果没有分配，则设为可用
            if self.status == ResourceStatus.MAINTENANCE: # 使用枚举
                self.status = ResourceStatus.AVAILABLE # 使用枚举