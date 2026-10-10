from .abstract.fog_node import FogNode
from .abstract.task_node import TaskNode
from .abstract.network_node import NetworkNode

class CloudServer(FogNode, TaskNode, NetworkNode):
    """The class for cloud servers.
    """
    def __init__(self, id, position, task_profile=None, fog_profile=None, network_profile=None):
        """The constructor of the CloudServer class.

        Args:
            id (str): The unique ID of the cloud server.
            position (tuple): The position of the cloud server.
            task_profile (dict): The task profile of the cloud server.
            fog_profile (dict): The fog profile of the cloud server.
            network_profile (dict): The network profile of the cloud server.
        """
        position_x, position_y, position_z = position
        TaskNode.__init__(self, id, position_x, position_y, position_z, 0, 0, 0, task_profile)
        FogNode.__init__(self, id, position_x, position_y, position_z, 0, 0, 0, fog_profile)
        NetworkNode.__init__(self, id, position_x, position_y, position_z, 0, 0, 0, network_profile)
        self._node_type = 'C'

    def to_dict(self):
        """Convert the cloud server to a dictionary.

        Returns:
            dict: The cloud server in dictionary format.
        """
        cloud_server_dict = TaskNode.to_dict(self)
        cloud_server_dict.update(FogNode.to_dict(self))
        cloud_server_dict.update(NetworkNode.to_dict(self))
        infos = {}
        for key, value in self.__dict__.items():
            if key.startswith("_"):
                infos[key[1:]] = value
            else:
                infos[key] = value
        cloud_server_dict.update(infos)
        return cloud_server_dict