import numpy as np
from airfogsim.utils import math_utils



class Mission:
    """Mission class to represent a mission in the simulation. The mission is a series of waypoints that the UAV/vehicle needs to visit and stay for a while. It also contains a set of tasks that need to be executed at each waypoint.
    """

    def __init__(self, mission_profile):
        """The constructor of the Mission class.

        Args:
            mission_profile (dict):
                mission_id (str): The unique ID of the mission.
                appointed_node_id (str): The appointed node ID.
                appointed_sensor_id (str): The appointed sensor ID.
                mission_routes (list): The list of routes.
                mission_duration (list): The duration of the mission (each value represent the time need to stay at each point).
                mission_duration_original (list): The original duration of the mission .
                mission_size (float): The required return size of the mission.
                mission_sensor_type (str): The sensor type of the mission.
                mission_accuracy (float): The sensor accuracy needed for the mission.
                mission_start_time (float): The start time of the mission.
                mission_deadline (float): The deadline of the mission (time to live, TTL).
                mission_task_sets (list): The list of tasks.
                mission_arrival_time (float): The arrival time of the mission.
                distance_threshold (float): The distance threshold for the location comparison.
        """
        self._mission_id = mission_profile['mission_id']
        self._mission_sensor_type = mission_profile['mission_sensor_type']
        self._mission_accuracy = mission_profile['mission_accuracy']
        self._appointed_node_id = mission_profile['appointed_node_id']
        self._appointed_sensor_id = mission_profile['appointed_sensor_id']
        self._appointed_sensor_accuracy = mission_profile['appointed_sensor_accuracy']
        self._mission_routes = mission_profile['mission_routes']
        self._mission_task_sets = mission_profile['mission_task_sets']
        self._mission_duration = mission_profile['mission_duration']
        self._mission_duration_sum = sum(self._mission_duration)  # The sum duration time of each point of the mission
        self._mission_duration_original=mission_profile.get('mission_duration_original',mission_profile['mission_duration'])
        self._mission_duration_original_sum=sum(self._mission_duration_original)
        self._mission_size = mission_profile['mission_size']
        self._mission_deadline = mission_profile['mission_deadline']
        self._distance_threshold = mission_profile.get('distance_threshold', 100)

        self._mission_arrival_time = mission_profile['mission_arrival_time']
        self._mission_start_time = mission_profile['mission_start_time']
        self._mission_stayed_time = np.zeros(len(mission_profile['mission_routes']))
        self._mission_stayed_time_sum=0
        self._last_stayed_time = -np.ones(len(mission_profile['mission_routes']))
        self._sensing_finish_time = 0
        self._mission_finish_time = 0  # The finish time of the mission (to be updated at the finish time, success or fail)

        self._mission_final_state_code = None

        if self._appointed_node_id is not None:
            assert len(mission_profile['mission_routes']) == len(mission_profile['mission_task_sets']) == len(mission_profile['mission_duration']), "The length of mission_routes, mission_task_sets, and mission_duration should be the same."
            for taskset in mission_profile['mission_task_sets']:
                for task in taskset:
                    task.setFartherMission(self)

    def outOfDeadline(self, current_time):
        """Check if the mission is out of deadline.

        Args:
            current_time (float): The current time.

        Returns:
            bool: True if the mission is out of deadline, False otherwise.
        """
        return self._mission_arrival_time + self._mission_deadline < current_time
    
    def updateMission(self, time_step, current_time, node):
        """Check the position of each waypoint and update the mission duration.

        Args:
            time_step (float): The time step.
            current_time (float): The current time.
            node (Node): The node object.
            sensor_usable (bool): If the sensor is usable
        """
        step_duration=0
        xyz = node.getPosition()
        for i in range(len(self._mission_routes)):
            if self._mission_stayed_time[i] < self._mission_duration[i]:
                xyz_2d = xyz[:2]
                route_2d = self._mission_routes[i][:2]
                if math_utils.calculate_distance(xyz_2d,route_2d) < self._distance_threshold:
                    self._mission_stayed_time[i] += time_step
                    self._last_stayed_time[i] = current_time
                    step_duration+=time_step
        self._mission_stayed_time_sum=sum(self._mission_stayed_time)

        return step_duration

    def isFinished(self):
        """Check if the mission is finished.

        Returns:
            bool: True if the mission is finished, False otherwise.
        """
        task_flag = True
        for taskset in self._mission_task_sets:
            for task in taskset:
                if not task.isFinished():
                    task_flag = False
        return all(self._mission_stayed_time >= self._mission_duration) and task_flag

    def setMissionFinalStateCode(self, mission_final_state_code):
        self._mission_final_state_code = mission_final_state_code

    def isSensingFinished(self, index=None):
        if index is None:
            return all(self._mission_stayed_time >= self._mission_duration)
        else:
            assert index < len(self._mission_routes)
            return self._mission_stayed_time[index] >= self._mission_duration[index]

    def isRelatedToNode(self, node_id):
        """Check if the mission is related to the node. (The mission is related to the node if the mission is
         assigned to node, inner task is offloaded to the node, inner task is assigned to the node,
          or the node is in the to_offload_route of inner task.)

        Args:
            node_id (str): The ID of the node.

        Returns:
            bool: True if the task is related to the node, False otherwise.
        """
        flag = False
        if self._appointed_node_id == node_id and not self.isSensingFinished():
            flag = True
        for taskset in self._mission_task_sets:
            for task in taskset:
                if task.isRelatedToNodeForMission(node_id):
                    flag = True
        return flag

    def getMissionId(self):
        return self._mission_id
    def getAppointedSensorId(self):
        return self._appointed_sensor_id

    def getAppointedNodeId(self):
        return self._appointed_node_id

    def getMissionTaskSets(self):
        return self._mission_task_sets

    def getMissionTasks(self, set_index):
        return self._mission_task_sets[set_index]

    def getRoutesLength(self):
        return len(self._mission_routes)

    def getRoutes(self):
        return self._mission_routes

    def getMissionFinishTime(self):
        return self._mission_finish_time

    def setMissionFinishTime(self, current_time):
        self._mission_finish_time = current_time

    def getMissionSensorType(self):
        return self._mission_sensor_type

    def getRequiredAccuracy(self):
        return self._mission_accuracy

    def getActualAccuracy(self):
        return self._appointed_sensor_accuracy

    def getCurrentNodeId(self):
        if self.isSensingFinished():
            for taskset in self._mission_task_sets:
                for task in taskset:
                    node_id=task.getCurrentNodeId()
        else:
            node_id=self._appointed_node_id

        return node_id

    def getLeftSensingTime(self):
        sum_left_time=sum(self._mission_duration-self._mission_stayed_time)
        return sum_left_time

    def getSensingFinishTime(self):
        return self._sensing_finish_time

    def setSensingFinishTime(self, current_time):
        self._sensing_finish_time=current_time

    def sensingFinishTimeNotSet(self):
        return self._sensing_finish_time==0

    def getLeftReturnSize(self):
        left_size=0
        for taskset in self._mission_task_sets:
            for task in taskset:
                required_return_size=task.getRequiredReturnSize()
                already_returned_size=task.getAlreadyReturnedSize()
                left_size+=required_return_size-already_returned_size
        return left_size

    def getLastSensingTime(self):
        return max(self._last_stayed_time)

    def getLastReturnTime(self):
        last_return_time=0
        for taskset in self._mission_task_sets:
            for task in taskset:
                last_return_time=max(task.getLastReturnTime(), last_return_time)
        return last_return_time



    def to_dict(self):
        """Convert the mission to dictionary.

        Returns:
            dict: The dictionary of the mission.
        """
        # 遍历所有属性，将其转化为字典
        mission_dict = {}
        for key, value in self.__dict__.items():
            key = key[1:]
            mission_dict[key] = value
        return mission_dict
