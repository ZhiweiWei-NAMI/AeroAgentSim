import random

from airfogsim.airfogsim_scheduler import AirFogSimScheduler
from airfogsim.airfogsim_env import AirFogSimEnv
from airfogsim.airfogsim_algorithm import BaseAlgorithmModule
import numpy as np

class GreedyAlgorithmModule(BaseAlgorithmModule):
    """Greedy Algorithm Module
    Use different schedulers to interact with the environment before calling env.step(). Manipulate different environments with the same algorithm design at the same time for learning sampling efficiency.\n
    Any implementation of the algorithm should inherit this class and implement the algorithm logic in the `scheduleStep()` method.
    """

    '''
    scheduleOffloading: Pass.
    scheduleComputing: BaseAlgorithm.
    scheduleCommunication: BaseAlgorithm.
    scheduleMission: 
        Mission: Missions assigned to both vehicles and UAVs, each type has a probability of sum of 1.
        Sensor: Select the sensor randomly from the idle sensors with accuracy higher than required, and in distance threshold.
    scheduleReturning: Relay(only for task assigned to vehicle), select nearest UAV and nearest RSU, return_route=[UAV,RSU]
                       Direct, select nearest RSU, return_route=[RSU]
                       Relay or direct is controlled by probability.
    scheduleTraffic: UAV: Move to the nearest mission position. If there is no mission allocated to UAV, movement is random.
    '''

    def __init__(self):
        super().__init__()
        self.algorithm_module_tag="Greedy"
        print('algorithm: ',self.algorithm_module_tag)

    def initialize(self, env: AirFogSimEnv, config={}, last_episode=None):
        """Initialize the algorithm with the environment. Including setting the task generation model, setting the reward model, etc.

        Args:
            env (AirFogSimEnv): The environment object.
            config (dict): The configuration dictionary.
            last_episode (int): The last episode number.
        """
        # self.rewardScheduler.setModel(env, 'REWARD',
        #                               '5 * log(10, 1 + (_mission_deadline-_mission_duration_sum)) * (1 / (1 + exp(-(_mission_deadline-_mission_duration_sum) / (_mission_finish_time - _mission_arrival_time-_mission_duration_sum))) - 1 / (1 + exp(-1)))')
        # self.rewardScheduler.setModel(env, 'PUNISH', '-1')
        self.rewardScheduler.setModel(env, 'REWARD',
                                      '_mission_duration_sum * _mission_accuracy + log( _mission_deadline,2) * (1 / (0.2 + exp(-_mission_deadline / (_mission_finish_time - _mission_arrival_time))) - 1 / (0.2 + exp(-1)))')
        self.rewardScheduler.setModel(env, 'PUNISH', '- 5*_mission_duration_sum * _mission_accuracy')

    def reset(self,env: AirFogSimEnv):
        super().reset(env)

    def scheduleStep(self, env: AirFogSimEnv):
        """The algorithm logic.

        Args:
            env (AirFogSimEnv): The environment object.
        """
        self.scheduleReturning(env)
        self.scheduleOffloading(env)
        self.scheduleCommunication(env)
        self.scheduleComputing(env)
        self.scheduleMission(env)
        self.scheduleTraffic(env)

    def scheduleMission(self, env: AirFogSimEnv):
        """The mission scheduling logic.

        Args:
            env (AirFogSimEnv): The environment object.

        """
        cur_time = self.trafficScheduler.getCurrentTime(env)
        traffic_interval=self.trafficScheduler.getTrafficInterval(env)
        new_missions_profile = self.missionScheduler.getToBeAssignedMissionsProfile(env, cur_time)
        delete_mission_profile_ids = []
        excluded_sensor_ids = []

        generate_num=0
        allocate_num=0
        for mission_profile in new_missions_profile:
            if mission_profile['mission_arrival_time']>cur_time-traffic_interval:
                generate_num+=1
            mission_sensor_type = mission_profile['mission_sensor_type']
            mission_accuracy = mission_profile['mission_accuracy']
            sensing_position = mission_profile['mission_routes'][0]
            TA_distance_Veh = self.missionScheduler.getConfig(env, 'TA_distance_Veh')
            TA_distance_UAV = self.missionScheduler.getConfig(env, 'TA_distance_UAV')

            UAV_infos = self.trafficScheduler.getNodeInfosInRange(env, sensing_position, TA_distance_UAV,"U")
            vehicle_infos = self.trafficScheduler.getNodeInfosInRange(env, sensing_position, TA_distance_Veh,"V")
            node_infos={}
            node_infos.update(vehicle_infos)
            node_infos.update(UAV_infos)

            appointed_node_id, appointed_sensor_id, appointed_sensor_accuracy = self.sensorScheduler.getRandomIdleSensorInNodesWithSelect(
                env, mission_sensor_type, mission_accuracy, node_infos, excluded_sensor_ids,sensing_position,20)

            if appointed_node_id != None and appointed_sensor_id != None:
                node_type=env._getNodeTypeById(appointed_node_id)
                mission_profile['appointed_node_id'] = appointed_node_id
                mission_profile['appointed_sensor_id'] = appointed_sensor_id
                mission_profile['appointed_sensor_accuracy'] = appointed_sensor_accuracy
                mission_profile['mission_start_time'] = cur_time
                for _ in mission_profile['mission_routes']:
                    task_set = []
                    mission_task_profile = {
                        'task_node_id': appointed_node_id,
                        'task_deadline': mission_profile['mission_deadline'],
                        'arrival_time': mission_profile['mission_arrival_time'],
                        'return_size': mission_profile['mission_size'],
                    }
                    new_task = self.taskScheduler.generateTaskOfMission(env, mission_task_profile)
                    task_set.append(new_task)
                    mission_profile['mission_task_sets'].append(task_set)
                if node_type=='U':
                    self.trafficScheduler.addUAVRoute(env, mission_profile['mission_id'],appointed_node_id, mission_profile['mission_routes'][0],mission_profile['mission_duration'][0],mission_profile['mission_arrival_time']+ mission_profile['mission_deadline'])
                self.missionScheduler.generateAndAddMission(env, mission_profile)
                allocate_num+=1

                delete_mission_profile_ids.append(mission_profile['mission_id'])
                excluded_sensor_ids.append(appointed_sensor_id)

        self.missionScheduler.setMissionEvaluationIndicators(env,generate_num,allocate_num)
        self.missionScheduler.deleteBeAssignedMissionsProfile(env, delete_mission_profile_ids)


    def scheduleReturning(self, env: AirFogSimEnv):
        """The returning logic. Relay or direct is controlled by probability.

        Args:
            env (AirFogSimEnv): The environment object.
        """
        waiting_to_return_tasks = self.taskScheduler.getWaitingToReturnTaskInfos(env)
        for task_node_id, tasks in waiting_to_return_tasks.items():
            for task in tasks:
                current_node_id = task.getCurrentNodeId()
                current_node_type = self.entityScheduler.getNodeTypeById(env, current_node_id)
                assert current_node_type is not None
                vehicle_num = self.entityScheduler.getNodeNumByType(env, 'V')
                UAV_num = self.entityScheduler.getNodeNumByType(env, 'U')
                RSU_num = self.entityScheduler.getNodeNumByType(env, 'I')

                if current_node_type == 'V':
                    if UAV_num > 0:
                        V2U_distance = np.zeros((UAV_num))
                        for u_idx in range(UAV_num):
                            u_id = self.entityScheduler.getNodeInfoByIndexAndType(env, u_idx, 'U')['id']
                            distance = self.trafficScheduler.getDistanceBetweenNodesById(env, current_node_id, u_id)
                            V2U_distance[u_idx] = distance
                        nearest_u_distance = np.max(V2U_distance)
                        nearest_u_idx = np.unravel_index(np.argmax(V2U_distance), V2U_distance.shape)
                        nearest_u_id = self.entityScheduler.getNodeInfoByIndexAndType(env, int(nearest_u_idx[0]), 'U')[
                            'id']

                    if RSU_num > 0:
                        V2R_distance = np.zeros(RSU_num)
                        for r_idx in range(RSU_num):
                            r_id = self.entityScheduler.getNodeInfoByIndexAndType(env, r_idx, 'I')['id']
                            distance = self.trafficScheduler.getDistanceBetweenNodesById(env, current_node_id, r_id)
                            V2R_distance[r_idx] = distance
                        nearest_r_distance = np.max(V2R_distance)
                        nearest_r_idx = np.unravel_index(np.argmax(V2R_distance), V2R_distance.shape)
                        nearest_r_id = self.entityScheduler.getNodeInfoByIndexAndType(env, int(nearest_r_idx[0]), 'I')[
                            'id']

                    relay_probability=env.mission_manager.getConfig("relay_probability")
                    if random.random() < relay_probability and UAV_num > 0:
                        return_route = [nearest_u_id,nearest_r_id]
                    else:
                        return_route = [nearest_r_id]

                elif current_node_type == 'U':
                    U2R_distance = np.zeros((RSU_num))
                    for r_idx in range(RSU_num):
                        r_id = self.entityScheduler.getNodeInfoByIndexAndType(env, r_idx, 'I')['id']
                        distance = self.trafficScheduler.getDistanceBetweenNodesById(env, current_node_id, r_id)
                        U2R_distance[r_idx] = distance
                    nearest_r_distance = np.max(U2R_distance)
                    nearest_r_idx = np.unravel_index(np.argmax(U2R_distance), U2R_distance.shape)
                    nearest_r_id = self.entityScheduler.getNodeInfoByIndexAndType(env, int(nearest_r_idx[0]), 'I')['id']
                    return_route = [nearest_r_id]
                else:
                    raise TypeError('Node type is invalid')

                self.taskScheduler.setTaskReturnRoute(env, task.getTaskId(), return_route)

    def scheduleTraffic(self, env: AirFogSimEnv):
        """The UAV traffic scheduling logic.

        Args:
            env (AirFogSimEnv): The environment object.
        """
        distance_threshold = self.missionScheduler.getConfig(env, 'distance_threshold')
        traffic_interval = self.trafficScheduler.getTrafficInterval(env)
        UAVs_info = self.trafficScheduler.getUAVTrafficInfos(env)
        UAVs_mobile_pattern = {}
        for UAV_id, UAV_info in UAVs_info.items():
            current_position = UAV_info['position']
            self.trafficScheduler.updateRoute(env, UAV_id, current_position, distance_threshold, traffic_interval)
            target_position = self.missionScheduler.getNearestMissionPosition(env, UAV_id, current_position)

            if target_position is None:
                # 在 [0, 2π) 范围内生成一个随机角度（弧度）
                random_angle = np.random.uniform(0, 2 * np.pi)
                mobility_pattern = {}
                mobility_pattern['angle'] = random_angle
                mobility_pattern['phi'] = 0
                UAV_speed_range = self.trafficScheduler.getConfig(env, 'UAV_speed_range')
                mobility_pattern['speed'] = random.uniform(UAV_speed_range[0], UAV_speed_range[1])
                UAVs_mobile_pattern[UAV_id] = mobility_pattern
            else:
                delta_x = target_position[0] - current_position[0]
                delta_y = target_position[1] - current_position[1]
                delta_z = target_position[2] - current_position[2]

                # 计算 xy 平面的方位角
                angle = np.arctan2(delta_y, delta_x)

                # 计算 z 相对于 xy 平面的仰角
                distance_xy = np.sqrt(delta_x ** 2 + delta_y ** 2)
                phi = np.arctan2(delta_z, distance_xy)

                mobility_pattern = {}
                mobility_pattern['angle'] = angle
                mobility_pattern['phi'] = 0 # 强制只进行水平飞行
                UAV_speed_range = self.trafficScheduler.getConfig(env, 'UAV_speed_range')
                mobility_pattern['speed'] = random.uniform(UAV_speed_range[0], UAV_speed_range[1])
                UAVs_mobile_pattern[UAV_id] = mobility_pattern
        self.trafficScheduler.setUAVMobilityPatterns(env, UAVs_mobile_pattern)

    def scheduleOffloading(self, env: AirFogSimEnv):
        # super().scheduleOffloading(env)
        pass
    def scheduleCommunication(self, env: AirFogSimEnv):
        super().scheduleCommunication(env)

    def scheduleComputing(self, env: AirFogSimEnv):
        super().scheduleComputing(env)

    def getRewardByTask(self, env: AirFogSimEnv):
        return super().getRewardByTask(env)

    def getRewardByMission(self, env: AirFogSimEnv):
        return super().getRewardByMission(env)

    def getAlgorithmTag(self):
        return self.algorithm_module_tag