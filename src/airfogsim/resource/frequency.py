# resource/frequency.py

from airfogsim.core.resource import Resource

class FrequencyResource(Resource):
    """
    频率资源类
    
    表示可分配的通信频率资源
    """
    
    def __init__(self, resource_id: str, 
                 frequency_range: tuple,
                 bandwidth: float,
                 max_users: int = 5,
                 power_limit: float = 100.0,
                 attributes: dict = None):
        """
        初始化频率资源
        
        Args:
            resource_id: 资源唯一标识符
            frequency_range: 频率范围 (最小值, 最大值)，单位为MHz
            bandwidth: 带宽，单位为MHz
            max_users: 可同时使用该频率资源的最大用户数
            power_limit: 发射功率限制，单位为mW
            attributes: 其他属性
        """
        super().__init__(resource_id, attributes)
        
        # 频率基本参数
        self.frequency_range = frequency_range
        self.bandwidth = bandwidth
        self.max_users = max_users
        self.power_limit = power_limit
        
        # 当前信道状态
        self.noise_level = 0.0  # 噪声水平，单位dB
        self.interference = 0.0  # 干扰水平
        self.utilization = 0.0  # 当前利用率 (0.0-1.0)
    
    def has_capacity(self) -> bool:
        """检查是否还有容量分配给新的通信请求"""
        return len(self.current_allocations) < self.max_users
    
    def update_channel_condition(self, noise_level: float, interference: float) -> None:
        """
        更新信道状态
        
        Args:
            noise_level: 新的噪声水平 (dB)
            interference: 新的干扰水平
        """
        self.noise_level = noise_level
        self.interference = interference
        
        # 根据信道状况可能影响可用性
        if noise_level > 30.0 or interference > 0.5:
            self.status = "maintenance"  # 暂时不可用
        else:
            # 如果没有分配，则设为可用
            if not self.current_allocations:
                self.status = "available"
    
    def get_snr(self, transmit_power: float) -> float:
        """
        计算信噪比 (SNR)
        
        Args:
            transmit_power: 发射功率 (mW)
            
        Returns:
            估计的信噪比 (dB)
        """
        # 简化的SNR计算
        if self.noise_level <= 0:
            return 100.0  # 避免除零错误
            
        power_db = 10 * (transmit_power / 1.0)  # 功率转换为dB
        return power_db - self.noise_level
    
    def update_utilization(self) -> None:
        """更新当前频率资源的利用率"""
        if self.max_users > 0:
            self.utilization = len(self.current_allocations) / self.max_users
        else:
            self.utilization = 0.0