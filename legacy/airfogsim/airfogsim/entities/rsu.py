
from .abstract.network_node import NetworkNode
from .abstract.fog_node import FogNode
from .abstract.task_node import TaskNode

class RSU(TaskNode, FogNode, NetworkNode):
    """The class for Road Side Units (RSUs).
    """
    def __init__(self, id, position, task_profile=None, fog_profile=None, network_profile=None):
        """The constructor of the RSU class.

        Args:
            id (str): The unique ID of the RSU.
            position (tuple): The position of the RSU.
            task_profile (dict): The task profile of the RSU.
            fog_profile (dict): The fog profile of the RSU.
            network_profile (dict): The network profile of the RSU.
        """
        position_x, position_y, position_z = position
        TaskNode.__init__(self, id, position_x, position_y, position_z, 0,0,0, task_profile)
        FogNode.__init__(self, id, position_x, position_y, position_z, 0,0,0, fog_profile)
        NetworkNode.__init__(self, id, position_x, position_y, position_z, 0,0,0, network_profile)
        self._stake = 0
        self._total_revenues = 0.1
        self._node_type = 'I'

    def getStake(self):
        """Get the stake of the RSU.

        Returns:
            float: The stake of the RSU.
        """
        return self._stake
    
    def setStake(self, stake):
        """Set the stake of the RSU.

        Args:
            stake (float): The stake of the RSU.
        """
        self._stake = stake

    def getTotalRevenues(self):
        """Get the total revenues of the RSU.

        Returns:
            float: The total revenues of the RSU.
        """
        return self._total_revenues
    
    def setTotalRevenues(self, total_revenues):
        """Set the total revenues of the RSU.

        Args:
            total_revenues (float): The total revenues of the RSU.
        """
        self._total_revenues = total_revenues

    def to_dict(self):
        """Convert the RSU to a dictionary.

        Returns:
            dict: The RSU in dictionary format.
        """
        rsu_dict = TaskNode.to_dict(self)
        rsu_dict.update(FogNode.to_dict(self))
        rsu_dict.update(NetworkNode.to_dict(self))
        infos = {}
        for key, value in self.__dict__.items():
            if key.startswith("_"):
                infos[key[1:]] = value
            else:
                infos[key] = value
        rsu_dict.update(infos)
        return rsu_dict