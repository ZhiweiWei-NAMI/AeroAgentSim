import math
from airfogsim.core.component import Component
from typing import List, Dict, Any, Optional

class ChargingComponent(Component):
    """
    充电组件，用于管理电池充电过程和充电站资源请求。

    性能指标：
    - charging_rate: 充电速率（%/小时）
    - time_to_full: 充满所需时间（小时）
    - energy_level: 当前电量水平（%）
    - request_processing_time: 充电站资源请求处理时间（秒）
    """
    PRODUCED_METRICS = ['charging_rate', 'request_processing_time']
    MONITORED_STATES = ['battery_capacity', 'position', 'charging_station.current_allocations',
                        'charging_station.power_level']  # 监控这些代理状态的变化

    def __init__(self, env, agent, name: Optional[str] = None,
                 supported_events: List[str] = ['charging_started', 'charging_completed'],
                 properties: Optional[Dict] = None):
        """
        初始化充电组件

        Args:
            env: 仿真环境
            agent: 所属代理
            name: 组件名称
            supported_events: 支持的额外事件
            properties: 组件属性，包含charging_factor和charging_efficiency
        """
        super().__init__(env, agent, name or "Charging", supported_events, properties)

        self.charging_factor = self.properties.get('charging_factor', 1.0)  # 默认充电速度因子为1.0
        self.charging_efficiency = self.properties.get('charging_efficiency', 0.85)  # 默认充电效率为0.85


    def _calculate_performance_metrics(self) -> Dict[str, Any]:
        """计算基于当前代理状态的性能指标"""
        # 获取当前的电池容量和代理状态
        battery_capacity = self.agent.get_state('battery_capacity', 5000.0)  # mAh
        agent_status = self.agent.get_state('status', 'idle')
        position = self.agent.get_state('position', (0, 0, 0))
        # 检查是否有moving_status属性
        moving_status = self.agent.get_state('moving_status', None) if hasattr(self.agent, 'moving_status') else None
        # 初始化指标
        charging_rate = 0.0
        request_processing_time = float('inf')  # 默认值表示无法处理

        # 检查代理是否有充电站对象
        charging_station = self.agent.get_possessing_object('charging_station')
        if charging_station:
            # 获取充电站的请求处理时间，如果不在申请中，则会添加一个申请到队列，并且返回默认的等待时间,确保在申请中;
            # 如果申请成功已经分配,则返回0
            if not self.env.landing_manager.is_allocated_to(charging_station.id, self.agent.id):
                self.env.landing_manager.request_resource(charging_station.id, self.agent)
            else:
                request_processing_time = 0.0  # 申请成功，处理时间为0
                # 检查状态，如果有moving_status，则需要是idle状态才能充电
                if (agent_status == 'active' and
                    charging_station.is_within_range(*position) and
                    (moving_status == 'idle' if moving_status is not None else True)):
                    # 基础充电功率 (W)，根据充电站电源水平调整
                    base_charging_power = charging_station.get_attribute('charging_power', 100)

                    # 计算充电率 (% / 小时)
                    # 充电功率 * 充电效率 / 电池容量 * 100%
                    # 假设充电功率单位为W，电池容量为mAh，电压为标准的3.7V
                    voltage = 3.7  # 锂电池标准电压
                    capacity_wh = battery_capacity * voltage / 1000  # 转换mAh到Wh

                    # 每小时充电百分比 = (充电功率 * 充电效率 / 电池容量Wh) * 100%
                    charging_rate = (base_charging_power * self.charging_efficiency / capacity_wh) * 100.0

                    # 应用充电因子
                    charging_rate *= self.charging_factor

        return {
            'charging_rate': charging_rate,  # %/小时
            'request_processing_time': request_processing_time  # 秒
        }