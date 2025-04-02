from airfogsim.core import TaskProof

class ChargingProof(TaskProof):
    """证明代理完成了充电任务的证明"""
    def update_battery_level(self, battery_level, agent_id):
        """更新电池电量信息"""
        self.update({'battery_level': battery_level}, agent_id)
        return self