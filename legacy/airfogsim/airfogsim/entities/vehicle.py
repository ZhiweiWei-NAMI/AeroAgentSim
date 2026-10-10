from .abstract.task_node import TaskNode
from .abstract.fog_node import FogNode
class Vehicle(TaskNode, FogNode):
    """The class for vehicles.
    """
    def __init__(self, id, position, speed=0, acceleration=0, angle=0, task_profile=None, fog_profile=None):
        """The constructor of the Vehicle class.

        Args:
            id (str): The unique ID of the vehicle.
            position (tuple): The position of the vehicle.
            speed (float): The speed of the vehicle.
            acceleration (float): The acceleration of the vehicle.
            angle (float): The angle of the vehicle.
            task_profile (dict): The task profile of the vehicle.
            fog_profile (dict): The fog profile of the vehicle.
            ip_address (str): The IP address of the vehicle.
        """
        position_x, position_y, position_z = position
        TaskNode.__init__(self, id, position_x, position_y, position_z, speed, acceleration, angle, task_profile)
        FogNode.__init__(self, id, position_x, position_y, position_z, speed, acceleration, angle, fog_profile)
        self._last_updated_time = 0
        self._node_type = 'V'

    def update(self, vehicle_traffic_info, simulation_time):
        """Update the vehicle.

        Args:
            vehicle_traffic_info (dict): The traffic information of the vehicle.
            simulation_time (float): The simulation time.
        """
        self._last_updated_time = simulation_time
        self._position_x, self._position_y, self._position_z = vehicle_traffic_info['position']
        self._speed = vehicle_traffic_info['speed']
        self._acceleration = vehicle_traffic_info['acceleration']
        self._angle = vehicle_traffic_info['angle']

    def to_dict(self):
        """Convert the vehicle to a dictionary.

        Returns:
            dict: The vehicle in dictionary format.
        """
        # 需要包含TaskNode和FogNode的信息
        vehicle_dict = TaskNode.to_dict(self)
        vehicle_dict.update(FogNode.to_dict(self))
        infos = {}
        for key, value in self.__dict__.items():
            if key.startswith("_"):
                infos[key[1:]] = value
            else:
                infos[key] = value
        vehicle_dict.update(infos)
        return vehicle_dict