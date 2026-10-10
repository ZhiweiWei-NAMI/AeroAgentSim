from .abstract.fog_node import FogNode
from .abstract.task_node import TaskNode


class UAV(TaskNode, FogNode):
    """The class for Unmanned Aerial Vehicles (UAVs).
    """
    def __init__(self, id, position, speed=0, acceleration=0, angle=0, phi=0, task_profile=None, fog_profile=None):
        """The constructor of the UAV class.

        Args:
            id (str): The unique ID of the UAV.
            position (tuple): The position of the UAV.
            speed (float): The speed of the UAV.
            acceleration (float): The acceleration of the UAV.
            angle (float): The angle of the UAV. The angle is the horizontal angle of the UAV.
            phi (float): The angle of the UAV. The phi is the vertical angle of the UAV.
            task_profile (dict): The task profile of the UAV.
            fog_profile (dict): The fog profile of the UAV.
        """
        position_x, position_y, position_z = position
        TaskNode.__init__(self, id, position_x, position_y, position_z, speed, acceleration, angle, task_profile)
        FogNode.__init__(self, id, position_x, position_y, position_z, speed, acceleration, angle, fog_profile)
        self._phi = phi
        self._last_updated_time = 0
        self._node_type = 'U'        

    def update(self, uav_traffic_info, simulation_time):
        """Update the UAV.

        Args:
            uav_traffic_info (dict): The traffic information of the UAV.
            simulation_time (float): The simulation time.
        """
        self._last_updated_time = simulation_time
        self._position_x, self._position_y, self._position_z = uav_traffic_info['position']
        self._speed = uav_traffic_info['speed']
        self._acceleration = uav_traffic_info['acceleration']
        self._angle = uav_traffic_info['angle']
        self._phi = uav_traffic_info['phi']

    def isMoving(self):
        return self._speed>0

    def to_dict(self):
        """Convert the UAV to a dictionary.

        Returns:
            dict: The UAV in dictionary format.
        """
        # Need to include the information of TaskNode and FogNode
        uav_dict = TaskNode.to_dict(self)
        uav_dict.update(FogNode.to_dict(self))
        infos = {}
        for key, value in self.__dict__.items():
            if key.startswith("_"):
                infos[key[1:]] = value
            else:
                infos[key] = value
        uav_dict.update(infos)
        return uav_dict