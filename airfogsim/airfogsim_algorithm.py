import math
import random

from .airfogsim_scheduler import AirFogSimScheduler
from .airfogsim_env import AirFogSimEnv
import numpy as np


class BaseAlgorithmModule:
    """Use different schedulers to interact with the environment before calling env.step(). Manipulate different environments with the same algorithm design at the same time for learning sampling efficiency.\n
    Any implementation of the algorithm should inherit this class and implement the algorithm logic in the `scheduleStep()` method.
    """

    '''
    scheduleOffloading: Not used.
    scheduleComputing: Not used.
    scheduleCommunication: Randomly allocate three RBs
    scheduleMission: 
        Mission: Missions assigned to only UAVs.
        Sensor: Use the sensor with the lowest accuracy among sensors with accuracy higher than the required accuracy.
    scheduleReturning: Select the nearest RSU.
    scheduleTraffic: UAV flys to the sensing position closest to the current location among all sensing missions assigned to oneself
    '''

    def __init__(self):
        self.algorithm_module_tag = "Base"
        self.compScheduler = AirFogSimScheduler.getComputationScheduler()
        self.commScheduler = AirFogSimScheduler.getCommunicationScheduler()
        self.entityScheduler = AirFogSimScheduler.getEntityScheduler()
        self.rewardScheduler = AirFogSimScheduler.getRewardScheduler()
        self.taskScheduler = AirFogSimScheduler.getTaskScheduler()
        self.blockchainScheduler = AirFogSimScheduler.getBlockchainScheduler()
        self.missionScheduler = AirFogSimScheduler.getMissionScheduler()
        self.sensorScheduler = AirFogSimScheduler.getSensorScheduler()
        self.trafficScheduler = AirFogSimScheduler.getTrafficScheduler()
        self.algorithmScheduler = AirFogSimScheduler.getAlgorithmScheduler()

    def initialize(self, env: AirFogSimEnv, config={}):
        """Initialize the algorithm with the environment. Should be implemented by the subclass. Including setting the reward model, etc.

        Args:
            env (AirFogSimEnv): The environment object.
        """
        self.rewardScheduler.setModel(env, 'REWARD', '-task_delay')
        self.rewardScheduler.setModel(env, 'PUNISH', '-1')

    def reset(self,env:AirFogSimEnv):
        pass

    def scheduleStep(self, env: AirFogSimEnv):
        """The algorithm logic. Should be implemented by the subclass.
        
        Args:
            env (AirFogSimEnv): The environment object.
        """
        self.scheduleReturning(env)
        self.scheduleOffloading(env)
        self.scheduleCommunication(env)
        self.scheduleComputing(env)
        self.scheduleMission(env)
        self.scheduleTraffic(env)

    def scheduleReturning(self, env: AirFogSimEnv):
        """The returning logic. Should be implemented by the subclass.

        Args:
            env (AirFogSimEnv): The environment object.
        """
        V2U_distance_threshold = self.commScheduler.getConfig(env, 'V2U_distance')
        V2I_distance_threshold = self.commScheduler.getConfig(env, 'V2I_distance')
        U2I_distance_threshold = self.commScheduler.getConfig(env, 'U2I_distance')
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
                        nearest_u_distance = np.min(V2U_distance)
                        nearest_u_idx = np.unravel_index(np.argmin(V2U_distance), V2U_distance.shape)
                        nearest_u_id = self.entityScheduler.getNodeInfoByIndexAndType(env, int(nearest_u_idx[0]), 'U')[
                            'id']

                    if RSU_num > 0:
                        V2R_distance = np.zeros(RSU_num)
                        for r_idx in range(RSU_num):
                            r_id = self.entityScheduler.getNodeInfoByIndexAndType(env, r_idx, 'I')['id']
                            distance = self.trafficScheduler.getDistanceBetweenNodesById(env, current_node_id, r_id)
                            V2R_distance[r_idx] = distance
                        nearest_r_distance = np.min(V2R_distance)
                        nearest_r_idx = np.unravel_index(np.argmin(V2R_distance), V2R_distance.shape)
                        nearest_r_id = self.entityScheduler.getNodeInfoByIndexAndType(env, int(nearest_r_idx[0]), 'I')[
                            'id']

                    relay_probability = env.mission_manager.getConfig("relay_probability")
                    if random.random() < relay_probability and UAV_num > 0 and nearest_u_distance < V2U_distance_threshold:
                        return_route = [nearest_u_id, nearest_r_id]
                    elif nearest_r_distance < V2I_distance_threshold:
                        return_route = [nearest_r_id]
                    else:
                        continue

                elif current_node_type == 'U':
                    U2R_distance = np.zeros((RSU_num))
                    for r_idx in range(RSU_num):
                        r_id = self.entityScheduler.getNodeInfoByIndexAndType(env, r_idx, 'I')['id']
                        distance = self.trafficScheduler.getDistanceBetweenNodesById(env, current_node_id, r_id)
                        U2R_distance[r_idx] = distance
                    nearest_r_distance = np.min(U2R_distance)
                    nearest_r_idx = np.unravel_index(np.argmin(U2R_distance), U2R_distance.shape)
                    nearest_r_id = self.entityScheduler.getNodeInfoByIndexAndType(env, int(nearest_r_idx[0]), 'I')['id']
                    if nearest_r_distance < U2I_distance_threshold:
                        return_route = [nearest_r_id]
                    else:
                        continue
                else:
                    raise TypeError('Node type is invalid')

                self.taskScheduler.setTaskReturnRoute(env, task.getTaskId(), return_route)

    def scheduleMission(self, env: AirFogSimEnv):
        """The mission scheduling logic. Should be implemented by the subclass. Default is selecting the idle sensor
        with lowest(but higher than mission_accuracy) accuracy (Only assigned to UAV).
        
        Args:
            env (AirFogSimEnv): The environment object.

        """
        UAV_probability = self.missionScheduler.getConfig(env, 'UAV_execution_probability')
        cur_time = self.trafficScheduler.getCurrentTime(env)
        traffic_interval = self.trafficScheduler.getTrafficInterval(env)
        new_missions_profile = self.missionScheduler.getToBeAssignedMissionsProfile(env, cur_time)
        delete_mission_profile_ids = []
        excluded_sensor_ids = []

        generate_num = 0
        allocate_num = 0
        for mission_profile in new_missions_profile:
            if mission_profile['mission_arrival_time'] > cur_time - traffic_interval:
                generate_num += 1
            mission_sensor_type = mission_profile['mission_sensor_type']
            mission_accuracy = mission_profile['mission_accuracy']
            sensing_position = mission_profile['mission_routes'][0]
            TA_distance_Veh = self.missionScheduler.getConfig(env, 'TA_distance_Veh')
            TA_distance_UAV = self.missionScheduler.getConfig(env, 'TA_distance_UAV')

            fixed_node_type=mission_profile.get('fixed_node_type',None)
            if fixed_node_type is None:
                if random.random() < UAV_probability:
                    node_type='U'
                    TA_distance=TA_distance_UAV
                else:
                    node_type = 'V'
                    TA_distance=TA_distance_Veh
                mission_profile['fixed_node_type']=node_type
                mission_profile['fixed_TA_distance']=TA_distance
            else:
                node_type = mission_profile['fixed_node_type']
                TA_distance = mission_profile['fixed_TA_distance']
            node_infos = self.trafficScheduler.getNodeInfosInRange(env, sensing_position, TA_distance,node_type)
            appointed_node_id, appointed_sensor_id, appointed_sensor_accuracy = self.sensorScheduler.getNearestIdleSensorInNodes(
                env, mission_sensor_type, mission_accuracy, sensing_position, node_infos, excluded_sensor_ids)

            if appointed_node_id != None and appointed_sensor_id != None:
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
                if node_type == 'U':
                    self.trafficScheduler.addUAVRoute(env, mission_profile['mission_id'],appointed_node_id, mission_profile['mission_routes'][0],mission_profile['mission_duration'][0],mission_profile['mission_arrival_time']+ mission_profile['mission_deadline'])
                self.missionScheduler.generateAndAddMission(env, mission_profile)
                allocate_num += 1

                delete_mission_profile_ids.append(mission_profile['mission_id'])
                excluded_sensor_ids.append(appointed_sensor_id)

        self.missionScheduler.setMissionEvaluationIndicators(env, generate_num, allocate_num)
        self.missionScheduler.deleteBeAssignedMissionsProfile(env, delete_mission_profile_ids)

    def scheduleTraffic(self, env: AirFogSimEnv):
        """The UAV traffic scheduling logic. Should be implemented by the subclass. Default is move to the next
         mission sensing or task position. If there is no mission allocated to UAV, movement is random.

        Args:
            env (AirFogSimEnv): The environment object.
        """
        distance_threshold = self.missionScheduler.getConfig(env, 'distance_threshold')
        observe_threshold = self.algorithmScheduler.getConfig(env, 'observe_threshold')
        traffic_interval = self.trafficScheduler.getTrafficInterval(env)
        UAVs_info = self.trafficScheduler.getUAVTrafficInfos(env)
        UAVs_mobile_pattern = {}
        for UAV_id, UAV_info in UAVs_info.items():
            current_position = UAV_info['position']
            self.trafficScheduler.updateRoute(env, UAV_id, current_position, distance_threshold, traffic_interval)
            next_mission_position = self.missionScheduler.getNearestMissionPosition(env, UAV_id, current_position)
            if next_mission_position is None:
                mission_states = self.algorithmScheduler.getBeforeTransMissionStates(env, current_position,
                                                                                     observe_threshold)
                cluster_center = self.algorithmScheduler.getClusterCenter(env, mission_states)

            target_position = next_mission_position if next_mission_position is not None else cluster_center
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
                mobility_pattern['phi'] = 0  # 强制只进行水平飞行
                UAV_speed_range = self.trafficScheduler.getConfig(env, 'UAV_speed_range')
                mobility_pattern['speed'] = random.uniform(UAV_speed_range[0], UAV_speed_range[1])
                UAVs_mobile_pattern[UAV_id] = mobility_pattern
        self.trafficScheduler.setUAVMobilityPatterns(env, UAVs_mobile_pattern)

    def scheduleOffloading(self, env: AirFogSimEnv):
        """The offloading scheduling logic. Should be implemented by the subclass. Default is to offload the task to the nearest node.
        
        Args:
            env (AirFogSimEnv): The environment object.
        """
        all_task_infos = self.taskScheduler.getAllToOffloadTaskInfos(env)
        for task_dict in all_task_infos:
            task_node_id = task_dict['task_node_id']
            task_id = task_dict['task_id']
            neighbor_infos = self.entityScheduler.getNeighborNodeInfosById(env, task_node_id, sorted_by='distance', max_num=5)
            if len(neighbor_infos) > 0:
                nearest_node_id = neighbor_infos[0]['id']
                furthest_node_id = neighbor_infos[-1]['id']
                flag = self.taskScheduler.setTaskOffloading(env, task_node_id, task_id, nearest_node_id)
                assert flag

    def scheduleCommunication(self, env: AirFogSimEnv):
        """The communication scheduling logic. Should be implemented by the subclass. Default is random.
        
        Args:
            env (AirFogSimEnv): The environment object.
        """
        n_RB = self.commScheduler.getNumberOfRB(env)
        all_offloading_task_infos = self.taskScheduler.getAllOffloadingTaskInfos(env)
        for task_dict in all_offloading_task_infos:
            allocated_RB_nos = np.random.choice(n_RB, 3, replace=False)
            self.commScheduler.setCommunicationWithRB(env, task_dict['task_id'], allocated_RB_nos)

    def scheduleComputing(self, env: AirFogSimEnv):
        """The computing scheduling logic. Should be implemented by the subclass. Default is evenly distributing the computing resources to the tasks.
        
        Args:
            env (AirFogSimEnv): The environment object.
        """
        def alloc_cpu_callback(computing_tasks, **kwargs):
            # _computing_tasks: {task_id: task_dict}
            # simulation_interval: float
            # current_time: float
            # 返回值是一个字典，key是task_id，value是分配的cpu
            # 本函数的目的是将所有的cpu分配给task
            appointed_fog_node_dict = {}
            task_list = []
            for tasks in computing_tasks.values():
                for task in tasks:
                    task_dict = task.to_dict()
                    assigned_node_id = task_dict['assigned_to']
                    assigned_node_info = self.entityScheduler.getNodeInfoById(env, assigned_node_id)
                    task_num = appointed_fog_node_dict.get(assigned_node_id, 0)
                    if assigned_node_info is None or task_num>=3:
                        continue
                    appointed_fog_node_dict[assigned_node_id] = task_num + 1
                    task_list.append(task_dict)
            # 所有cpu分配给task
            alloc_cpu_dict = {}
            for task_dict in task_list:
                task_id = task_dict['task_id']
                assigned_node_id = task_dict['assigned_to']
                alloc_cpu = assigned_node_info.get('fog_profile', {}).get('cpu', 0) / max(1, appointed_fog_node_dict[assigned_node_id])
                alloc_cpu_dict[task_id] = alloc_cpu
            return alloc_cpu_dict
        self.compScheduler.setComputingCallBack(env, alloc_cpu_callback) 

    def getRewardByTask(self, env: AirFogSimEnv):
        """The reward calculation logic. Should be implemented by the subclass. Default is calculating reward of done tasks in last time.
        
        Args:
            env (AirFogSimEnv): The environment object.

        Returns:
            float: The reward value.
        """
        last_step_succ_task_infos = self.taskScheduler.getLastStepSuccTaskInfos(env)
        last_step_fail_task_infos = self.taskScheduler.getLastStepFailTaskInfos(env)
        reward = 0
        for task_info in last_step_succ_task_infos+last_step_fail_task_infos:
            reward += self.rewardScheduler.getRewardByTask(env, task_info)
        return reward

    def getRewardByMission(self, env: AirFogSimEnv):
        """The reward calculation logic. Should be implemented by the subclass. Default is calculating reward of done missions in last time.

        Args:
            env (AirFogSimEnv): The environment object.

        Returns:
            float: The reward value.
        """
        last_step_succ_mission_infos = self.missionScheduler.getLastStepSuccMissionInfos(env)
        last_step_fail_mission_infos = self.missionScheduler.getLastStepFailMissionInfos(env)
        last_step_early_fail_mission_infos= self.missionScheduler.getLastStepEarlyFailMissionInfos(env)
        sum_reward = 0
        reward = 0
        punish = 0
        for mission_info in last_step_succ_mission_infos:
            # print("finish reward")
            # finish_reward=mission_info['mission_duration_sum'] * mission_info['mission_accuracy']
            # print(finish_reward)
            # print("time reward")
            # time_reward=(math.log(mission_info['mission_deadline'],2) *
            #              (1 / (0.2 + math.exp(-mission_info['mission_deadline'] / (mission_info['mission_finish_time'] - mission_info['mission_arrival_time']))) - 1 / (
            #                 0.2 + math.exp(-1))))
            # print(time_reward)

            mission_reward = self.rewardScheduler.getRewardByMission(env, mission_info)

            reward += mission_reward
            sum_reward += mission_reward
        for mission_info in last_step_fail_mission_infos:
            mission_punish = self.rewardScheduler.getPunishByMission(env, mission_info)
            punish += mission_punish
            sum_reward += mission_punish
        for mission_info in last_step_early_fail_mission_infos:
            mission_punish = self.rewardScheduler.getPunishByMission(env, mission_info)
            punish += mission_punish
            sum_reward += mission_punish
        return reward, punish, sum_reward

    def getAlgorithmTag(self):
        return self.algorithm_module_tag





