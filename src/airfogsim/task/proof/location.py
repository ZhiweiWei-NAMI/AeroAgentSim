from airfogsim.core import TaskProof

class LocationProof(TaskProof):
    """位置证明类，记录代理在特定位置的证明"""    
    def update_position(self, position, agent_id):
        """更新位置信息"""
        self.update({'position': position}, agent_id)
        return self
