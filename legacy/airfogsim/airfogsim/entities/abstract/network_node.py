from .simple_node import SimpleNode
class NetworkNode(SimpleNode):
    """The abstract class for network nodes.
    """
    def __init__(self, id, position_x=0, position_y=0, position_z=0, speed=0, acceleration=0, angle=0, network_profile=None):
        super(NetworkNode, self).__init__(id, position_x, position_y, position_z, speed, acceleration, angle)
        self._network_profile = network_profile

    def to_dict(self):
        """Convert the network node to a dictionary.
        """
        node_dict = super(NetworkNode, self).to_dict()
        node_dict.update({'network_profile': self._network_profile})
        return node_dict