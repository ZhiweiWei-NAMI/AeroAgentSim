import copy

import numpy as np
from jinja2.nodes import NodeType

from .base_sched import BaseScheduler

class MissionScheduler(BaseScheduler):
    @staticmethod
    def getToBeAssignedMissionsProfile(env,cur_time):
        return env.mission_manager.getArrivedMissionsProfile(cur_time)

    @staticmethod
    def deleteBeAssignedMissionsProfile(env,mission_profile_ids):
        env.mission_manager.deleteMissionsProfile(mission_profile_ids)

    @staticmethod
    def getAllExcutingMissionInfos(env):
        node_missions=env.mission_manager.getExecutingMissions()
        missions_dict=[]
        for node_id,missions in node_missions.items():
            for mission in missions:
                missions_dict.append(mission.to_dict())
        return missions_dict

    @staticmethod
    def getAllToAllocateMissionInfos(env):
        mission_profiles=env.mission_manager.getToGenerateMissionsProfile()
        return mission_profiles


    @staticmethod
    def generateAndAddMission(env,mission_profile):
        mission=env.mission_manager.generateMission(mission_profile)
        env.new_missions.append(mission)

    @staticmethod
    def getLastStepSuccMissionInfos(env):
        """Get the success mission infos for last timeslot.

        Args:
            env (AirFogSimEnv): The AirFogSim environment.

        Returns:
            list: The list of mission infos (mission: object).
        """
        recently_done_100_missions = env.mission_manager.getRecentlyDoneMissions()
        last_step = env.simulation_time - env.traffic_interval
        mission_info_list = []
        for mission in recently_done_100_missions:
            if mission.isFinished() and round(mission.getMissionFinishTime(),3) >= round(last_step,3):
                mission_info_list.append(mission.to_dict())
        return mission_info_list

    @staticmethod
    def getLastStepFailMissionInfos(env):
        """Get the failed mission infos for last timeslot.

        Args:
            env (AirFogSimEnv): The AirFogSim environment.

        Returns:
            list: The list of mission infos (mission: object).
        """
        recently_fail_100_missions = env.mission_manager.getRecentlyFailMissions()
        last_step = env.simulation_time - env.traffic_interval
        mission_info_list = []
        for mission in recently_fail_100_missions:
            if round(mission.getMissionFinishTime(),3) >= round(last_step,3):
                mission_info_list.append(mission.to_dict())
        return mission_info_list

    @staticmethod
    def getLastStepEarlyFailMissionInfos(env):
        """Get the failed mission infos for last timeslot.

        Args:
            env (AirFogSimEnv): The AirFogSim environment.

        Returns:
            list: The list of mission infos (mission: object).
        """
        recently_early_fail_100_missions = env.mission_manager.getRecentlyEarlyFailMissions()
        last_step = env.simulation_time - env.traffic_interval
        mission_info_list = []
        for mission in recently_early_fail_100_missions:
            if round(mission.getMissionFinishTime(),3) >= round(last_step,3):
                mission_info_list.append(mission.to_dict())
        return mission_info_list

    @staticmethod
    def getNearestMissionPosition(env,node_id,position):
        """Get the nearest position for sensing.

        Args:
            env (AirFogSimEnv): The AirFogSim environment.

        Returns:
            list: The position of mission's for sensing ([x,y]).
        """
        node_type=env._getNodeTypeById(node_id)
        if node_type == "U":
            route = env.uav_routes.get(node_id, [])
            if len(route) == 0:
                return None
            current_idx = env.getUAVNextMissionIdx(node_id)
            if current_idx is not None:
                return copy.deepcopy(route[current_idx]['position'])

            position_list = []
            for poi in route:
                position_list.append(poi['position'])

            position_array = np.array(position_list)
            position = np.array(position)
            distances = np.linalg.norm(position_array - position,axis=1)  # Calculate distances
            new_idx = np.argmin(distances)  # Find the index of the nearest position
            env.setUAVNextMissionIdx(node_id, new_idx)
            return copy.deepcopy(route[new_idx]['position'])

            # if current_idx is None:
            #     position_list = []
            #     for poi in route:
            #         position_list.append(poi['position'])
            #
            #     position_array = np.array(position_list)
            #     position = np.array(position)
            #     distances = np.linalg.norm(position_array - position,axis=1)  # Calculate distances
            #     new_idx = np.argmin(distances)  # Find the index of the nearest position
            #     env.setUAVNextMissionIdx(node_id, new_idx)
            #
            #     return copy.deepcopy(route[new_idx]['position'])
            # else:
            #     return copy.deepcopy(route[current_idx]['position'])
        else:
            executing_missions=env.mission_manager.getExecutingMissions()
            mission_list=executing_missions.get(node_id,[])
            if len(mission_list)==0:
                return None
            position_list = []
            for mission in mission_list:
                routes=mission.getRoutes()
                for route_xyz in routes:
                    position_list.append(route_xyz)

            # Find the nearest position in position_list to the given position
            position_array = np.array(position_list)
            position = np.array(position)
            distances = np.linalg.norm(position_array - position,axis=1)  # Calculate distances
            nearest_index = np.argmin(distances)  # Find the index of the nearest position

            return position_list[nearest_index]  # Return the nearest position as a list

    @staticmethod
    def getFarthestMissionPosition(env,node_id,position):
        """Get the nearest position for sensing.

        Args:
            env (AirFogSimEnv): The AirFogSim environment.

        Returns:
            list: The position of mission's for sensing ([x,y]).
        """
        node_type=env._getNodeTypeById(node_id)
        if node_type == "U":
            route = env.uav_routes.get(node_id, [])
            if len(route) == 0:
                return None
            current_idx = env.getUAVNextMissionIdx(node_id)
            if current_idx is None:
                position_list = []
                for poi in route:
                    position_list.append(poi['position'])

                position_array = np.array(position_list)
                position = np.array(position)
                distances = np.linalg.norm(position_array - position,axis=1)  # Calculate distances
                new_idx = np.argmax(distances)  # Find the index of the nearest position
                env.setUAVNextMissionIdx(node_id, new_idx)

                return copy.deepcopy(route[new_idx]['position'])
            else:
                return copy.deepcopy(route[current_idx]['position'])
        else:
            executing_missions=env.mission_manager.getExecutingMissions()
            mission_list=executing_missions.get(node_id,[])
            if len(mission_list)==0:
                return None
            position_list = []
            for mission in mission_list:
                routes=mission.getRoutes()
                for route_xyz in routes:
                    position_list.append(route_xyz)

            # Find the nearest position in position_list to the given position
            position_array = np.array(position_list)
            position = np.array(position)
            distances = np.linalg.norm(position_array - position,axis=1)  # Calculate distances
            nearest_index = np.argmax(distances)  # Find the index of the nearest position

            return position_list[nearest_index]  # Return the nearest position as a list

    @staticmethod
    def setMissionEvaluationIndicators(env,generate_num,allocate_num):
        env.setMissionEvaluationIndicators(generate_num,allocate_num)

    @staticmethod
    def getExecutingMissionProfiles(env,UAV_id):
        missions=env.mission_manager.getExecutingMissions(UAV_id)
        mission_profiles=[]
        for mission in missions:
            profile=mission.to_dict()
            mission_profiles.append(profile)
        return mission_profiles

    @staticmethod
    def getExecutingMissions(env,node_id,node_type=None):
        missions=env.mission_manager.getExecutingMissions(node_id)
        if node_type is not None:
            missions = {node_id: missions for node_id,missions in missions.items() if env._getNodeTypeById(node_id) == node_type}
        return missions



    @staticmethod
    def getConfig(env,name):
        return env.mission_manager.getConfig(name)

