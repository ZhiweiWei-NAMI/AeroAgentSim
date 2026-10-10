from .simple_node import SimpleNode

class FogNode(SimpleNode):
    """The fog node class. This class represents the basic fog node entity in the airfogsim environment.
    """
    def __init__(self, id, position_x=0, position_y=0, position_z=0, speed=0, acceleration=0, angle=0, fog_profile=None):
        """The constructor of the FogNode class.

        Args:
            id (str): The unique ID of the fog node.
            position_x (float): The x-coordinate of the fog node.
            position_y (float): The y-coordinate of the fog node.
            position_z (float): The z-coordinate of the fog node.
            speed (float): The speed of the fog node.
            acceleration (float): The acceleration of the fog node.
            angle (float): The angle of the fog node.
            fog_profile (dict): The fog profile of the fog node.
        """
        super(FogNode, self).__init__(id, position_x, position_y, position_z, speed, acceleration, angle)
        self._fog_profile = fog_profile

    def getFogProfile(self):
        """Get the fog profile of the fog node.

        Returns:
            dict: The fog profile of the fog node.
        """
        return self._fog_profile

    def setFogProfile(self, fog_profile):
        """Set the fog profile of the fog node.

        Args:
            fog_profile (dict): The fog profile of the fog node.
        """
        self._fog_profile = fog_profile

    def to_dict(self):
        """Convert the fog node to a dictionary.

        Returns:
            dict: The fog node in dictionary format.
        """
        fog_node_dict = super(FogNode, self).to_dict()
        fog_node_dict.update({'fog_profile': self._fog_profile})
        return fog_node_dict