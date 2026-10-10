import os
from datetime import datetime
import itertools

import numpy as np
import math
from sklearn.cluster import DBSCAN
import matplotlib.pyplot as plt

from .base_sched import BaseScheduler
from airfogsim.utils import math_utils

sensor_state_cache = {}

class AlgorithmScheduler(BaseScheduler):

    @staticmethod
    def getNodeStates(env, node_type=None, node_priority=None):
        """Get node states (shape:[node_num,dim]).

         Args:
             env (AirFogSimEnv): The AirFogSim environment.
             node_type(str): Type in ['V','I','U'].

         Returns:
             int: node num,
             list: list of node states
             node_priority:  {{node_type: priority}, ...}

         Examples:
             algoSched.getNodeState(env,'U')
         """
        # dim:[id, type, is_mission_node, is_schedulable, x, y, z]
        # [1, 'U', True, True, 105.23, 568.15. 225.65]
        if node_type is not None:
            assert node_type in ['V', 'I', 'U']
        state_dim = 5
        feature_dict = {
            'V': {
                'is_mission_node': True,
                'is_schedulable': False
            },
            'U': {
                'is_mission_node': True,
                'is_schedulable': True
            },
            'I': {
                'is_mission_node': False,
                'is_schedulable': False
            }
        }

        states = []
        candidate_nodes = {}
        if node_type == 'V':
            node_infos = env.traffic_manager.getVehicleTrafficInfos()
            candidate_nodes[node_type] = node_infos
            # node_type_enum = NodeTypeEnum.VEHICLE
        elif node_type == 'I':
            node_infos = env.traffic_manager.getRSUInfos()
            candidate_nodes[node_type] = node_infos
            # node_type_enum = NodeTypeEnum.RSU
        elif node_type == 'U':
            node_infos = env.traffic_manager.getUAVTrafficInfos()
            candidate_nodes[node_type] = node_infos
            # node_type_enum = NodeTypeEnum.UAV
        else:
            vehicle_infos = env.traffic_manager.getVehicleTrafficInfos()
            RSU_infos = env.traffic_manager.getRSUInfos()
            UAV_infos = env.traffic_manager.getUAVTrafficInfos()
            candidate_nodes['V'] = vehicle_infos
            candidate_nodes['I'] = RSU_infos
            candidate_nodes['U'] = UAV_infos

        node_num = 0
        for node_type, node_infos in candidate_nodes.items():
            node_num += len(node_infos)
            for node_id, node_info in node_infos.items():
                index = int(node_id.split('_')[-1])  # 转换为整数
                x, y, z = node_info['position']
                is_mission_node = feature_dict[node_type]['is_mission_node']
                is_schedulable = feature_dict[node_type]['is_schedulable']
                node_state = [index, node_type, is_mission_node, is_schedulable, x, y, z]
                states.append(node_state)

        # 按type,id排序
        sorted_node_states = sorted(states, key=lambda x: (node_priority[x[1]], x[0]))

        return node_num, sorted_node_states

    @staticmethod
    def getMissionStates(env, mission_profiles):
        """Get mission states (shape:[mission_num,dim]).

         Args:
             env (AirFogSimEnv): The AirFogSim environment.
             mission_profiles(dict):

         Returns:
             list: list of mission states

         Examples:
             algoSched.getMissionStates(env,mission_profiles)
         """
        # dim:[sensor_type, accuracy, return_size, arrival_time, TTL, duration, x, y, z, distance_threshold]
        # ['U',0.8,50,20,120,5,120.25,262.05,553.25,100]
        states = []
        for mission_profile in mission_profiles:
            # index = int(mission_profile['mission_id'].split('_')[-1])  # 转换为整数
            sensor_type = int(mission_profile['mission_sensor_type'].split('_')[-1])  # 转换为整数
            accuracy = mission_profile['mission_accuracy']
            return_size = mission_profile['mission_size']
            arrive_time = mission_profile['mission_arrival_time']
            TTL = mission_profile['mission_deadline']
            duration = sum(mission_profile['mission_duration'])
            x, y, z = mission_profile['mission_routes'][0]
            distance_threshold = mission_profile['distance_threshold']
            state = [sensor_type, accuracy, return_size, arrive_time, TTL, duration, x, y, z, distance_threshold]
            states.append(state)
        return states

    @staticmethod
    def getExecutingMissionStates(env, mission_profiles):
        """Get executing mission states (shape:[mission_num,dim]).

         Args:
             env (AirFogSimEnv): The AirFogSim environment.
             mission_profiles(dict):

         Returns:
             list: list of mission states

         Examples:
             algoSched.getMissionStates(env,mission_profiles)
         """
        # dim:[arrive_time, TTL, duration,left_duration, x, y, z]
        # [50,40,10,2,262.05,553.25,100]
        states = []
        for mission_profile in mission_profiles:
            # index = int(mission_profile['mission_id'].split('_')[-1])  # 转换为整数
            sensor_type = int(mission_profile['mission_sensor_type'].split('_')[-1])  # 转换为整数
            accuracy = mission_profile['mission_accuracy']
            return_size = mission_profile['mission_size']
            arrive_time = mission_profile['mission_arrival_time']
            TTL = mission_profile['mission_deadline']
            duration = sum(mission_profile['mission_duration'])
            left_duration=duration-mission_profile['mission_stayed_time_sum']
            x, y, z = mission_profile['mission_routes'][0]
            distance_threshold = mission_profile['distance_threshold']
            state = [arrive_time, TTL, duration,left_duration, x, y, z]
            states.append(state)
        return states

    @staticmethod
    def getNearest10SensorStates(env, sensor_type, lower_bound_accuracy, base_position, excluded_sensor_ids):
        """Get sensor states (shape:[10,dim]).

         Args:
             env (AirFogSimEnv): The AirFogSim environment.
             sensor_type(str): The required type of sensor
             lower_bound_accuracy(float): The lowest permitted accuracy of sensor
             base_position(list): The position of core entity
             excluded_sensor_ids(list): The sensor ids of occupied sensors in this step

         Returns:
             list: list of sensor states

         Examples:
             algoSched.getNearest10SensorStates(env,2,0.5,[100,100,200],[1,2,3,4])
         """
        # dim:[node_id,node_type,id,type, accuracy,candidate]
        # [1, 'U', 3, 0.8]
        sensor_max_num = 10

        candidate_sensors = env.sensor_manager.getSensorsByStateAndType('idle', sensor_type)
        sensor_states = []

        # Choose the sensor with the highest accuracy among the idle sensors
        for node_id, sensors in candidate_sensors.items():
            node_type = env._getNodeTypeById(node_id)
            assert node_type is not None, "Node is invalid."
            node_position = env.traffic_manager.getNodePositionById(node_id)
            # if node_type == 'V':
            #     node_position = env.traffic_manager.getVehiclePosition(node_id)
            #     node_type = NodeTypeEnum.VEHICLE
            # elif node_type == 'U':
            #     node_position = env.traffic_manager.getUAVPosition(node_id)
            #     node_type = NodeTypeEnum.UAV
            distance = math_utils.calculate_distance(base_position,node_position)
            for sensor in sensors:
                if sensor.getSensorAccuracy() > lower_bound_accuracy and sensor.getSensorId() not in excluded_sensor_ids:
                    index = int(sensor.getSensorId().split('_')[-1])  # 转换为整数
                    sensor_type = int(sensor.getSensorType().split('_')[-1])
                    node_index = int(node_id.split('_')[-1])
                    accuracy = sensor.getSensorAccuracy()
                    sensor_states.append([distance, node_index, node_type, index, sensor_type, accuracy, True])
        sensor_states_sorted = sorted(sensor_states, key=lambda x: x[0])

        top_10_sensor_states = []
        for item in sensor_states_sorted[:sensor_max_num]:
            used_state = item[1:]
            top_10_sensor_states.append(used_state)

        # mask = np.array([True] * valid_sensor_num + [False] * (sensor_max_num - valid_sensor_num)).flatten()
        # if valid_sensor_num < sensor_max_num:
        #     top_10_sensor_states.extend([[0] * sensor_dim] * (sensor_max_num - valid_sensor_num))  # 补充零
        # state_array = np.array(top_10_sensor_states).flatten()

        return top_10_sensor_states

    @staticmethod
    def getSensorStates(env, sensor_type, lower_bound_accuracy, excluded_sensor_ids,target_position,old_traffic_info,new_traffic_info,TA_distance_Veh,TA_distance_UAV,exe_distance, node_priority,grouping=True,max_available_sensors=20,use_cache=False):
        """Get sensor states (shape:[sensor_num,dim]).

         Args:
             env (AirFogSimEnv): The AirFogSim environment.
             sensor_type(str): The required type of sensor
             lower_bound_accuracy(float): The lowest permitted accuracy of sensor
             excluded_sensor_ids(list): The sensor ids of occupied sensors in this step
             node_priority(dict): {{node_type: priority}, ...}

         Returns:
             list: list of sensor states

         Examples:
             algoSched.getSensorStates(env,2,0.5,[1,2,3,4])
         """
        # dim:[node_id,node_type,id,type, accuracy,candidate,distance]
        # [1, 'U', 3, 2, 0.8, True,100.52]

        if use_cache:
            return AlgorithmScheduler._getSensorStatesByCache(env, sensor_type, lower_bound_accuracy, excluded_sensor_ids,target_position,old_traffic_info,new_traffic_info,TA_distance_Veh,TA_distance_UAV,exe_distance, node_priority,grouping,max_available_sensors)

        candidate_sensors = env.sensor_manager.getSensorsByStateAndType('idle', sensor_type)
        idle_sensors = env.sensor_manager.getSensorsByStateAndType('idle')
        busy_sensors = env.sensor_manager.getSensorsByStateAndType('busy')
        combined_sensors = itertools.chain(idle_sensors.items(), busy_sensors.items())
        sensor_states_dict = {}
        candidate_sensor_states=[]
        valid_sensor_num = 0

        candidate_sensor_ids=[sensor._sensor_id for node_id, sensors in candidate_sensors.items() for sensor in sensors]

        # Choose the sensor with the highest accuracy among the idle sensors
        for node_id, sensors in combined_sensors:
            node_sensor_states = sensor_states_dict.get(node_id, [])
            node_type = env._getNodeTypeById(node_id)
            assert node_type is not None, f"Node {node_id} is invalid."
            node=env._getNodeById(node_id)
            node_position = node.getPosition()
            node_speed=node.getSpeed()
            x,y,z=node_position
            distance = math_utils.calculate_distance(node_position,target_position)

            old_node_info=old_traffic_info.get(node_id,None)
            if old_node_info is None:
                is_approaching=False
                in_threshold_predict=False
                distance_factor=1
            else:
                old_position=old_node_info['position']
                new_position=node_position
                is_approaching =math_utils.check_approaching(old_position[:2], new_position[:2],target_position[:2])
                perpendicular_distance=math_utils.calculate_perpendicular_distance(old_position[:2], new_position[:2],target_position[:2])
                in_threshold_predict=perpendicular_distance<exe_distance
                distance_factor=-1 if is_approaching is True else 1
            for sensor in sensors:
                sensor_id=sensor.getSensorId()
                if node_type=='V' and \
                        sensor.getSensorAccuracy() > lower_bound_accuracy and \
                        sensor_id not in excluded_sensor_ids and \
                        sensor_id in candidate_sensor_ids and \
                        distance < TA_distance_Veh: # Veh可分配距离阈值
                    candidate = True
                    valid_sensor_num+=1
                    TA_distance=TA_distance_Veh
                elif node_type=='U' and \
                        sensor.getSensorAccuracy() > lower_bound_accuracy and \
                        sensor_id not in excluded_sensor_ids and \
                        sensor_id in candidate_sensor_ids and \
                        distance < TA_distance_UAV: # UAV可分配距离阈值
                    candidate = True
                    valid_sensor_num+=1
                    TA_distance=TA_distance_UAV
                else:
                    candidate = False
                    TA_distance=0
                index = int(sensor_id.split('_')[-1])  # 转换为整数
                sensor_type = int(sensor.getSensorType().split('_')[-1])
                node_index = int(node_id.split('_')[-1])
                accuracy = sensor.getSensorAccuracy()
                state = [node_index, node_type, index, sensor_type, accuracy, candidate,distance_factor*distance,x,y,z,is_approaching,in_threshold_predict,node_speed]
                node_sensor_states.append(state)
                if distance<TA_distance and sensor_id in candidate_sensor_ids:
                    candidate_sensor_states.append(state)
            sensor_states_dict[node_id] = node_sensor_states

        # 将dict转为list
        sensor_states = list(sensor_states_dict.values())

        approaching_num=0
        in_threshold_num=0
        uav_num=0
        if grouping is False:
            # 按candidate,in_threshold_predict,is_approaching,node_speed排序,true且node_speed小的在前
            sensor_states = [sensor for node in sensor_states for sensor in node]
            sensor_state_cache[env]=sensor_states
            # sorted_sensor_states=sorted(sensor_states, key=lambda x:  (not x[5],not x[11],not x[10],x[6]+5* x[12]))
            sorted_sensor_states=sorted(candidate_sensor_states, key=lambda x:  (not x[5],not x[11],not x[10],x[6]))

            # sorted_sensor_states=sorted_sensor_states[:valid_sensor_num]
            sorted_sensor_states = sorted_sensor_states[:max_available_sensors]
            for sensor_state in sorted_sensor_states:
                if sensor_state[5] is False:
                    continue

                if sensor_state[10] is True:
                    approaching_num+=1
                if sensor_state[11] is True:
                    in_threshold_num+=1
                if sensor_state[1]=='U':
                    uav_num+=1
        else:
            # 按 node_type,node_id 排序
            sorted_sensor_states = sorted(sensor_states, key=lambda x: (node_priority[x[0][2]], x[0][1]))

        other_info={
            'valid_sensor_num':valid_sensor_num,
            'approaching_num':approaching_num,
            'in_threshold_num':in_threshold_num,
            'uav_num':uav_num,
        }

        return sorted_sensor_states,other_info

    @staticmethod
    def _getSensorStatesByCache(env, sensor_type, lower_bound_accuracy, excluded_sensor_ids,target_position,old_traffic_info,new_traffic_info,TA_distance_Veh,TA_distance_UAV,exe_distance, node_priority,grouping=True,max_available_sensors=20):
        sensor_states=sensor_state_cache[env]
        candidate_sensors = env.sensor_manager.getSensorsByStateAndType('idle', sensor_type)
        candidate_sensor_ids=[sensor._sensor_id for node_id, sensors in candidate_sensors.items() for sensor in sensors]
        valid_sensor_num=0
        candidate_sensor_states =[]

        for sensor_state in sensor_states:
            node_type=sensor_state[1]
            sensor_id=f'sensor_{sensor_state[2]}'
            accuracy=sensor_state[4]
            node_position = (sensor_state[7],sensor_state[8],sensor_state[9])

            squared_sum = 0.0
            for i in range(len(node_position)):
                squared_sum += (node_position[i] - target_position[i]) ** 2
            distance= math.sqrt(squared_sum)
            # distance = math_utils.calculate_distance(node_position, target_position)

            if node_type == 'V' and \
                    accuracy > lower_bound_accuracy and \
                    sensor_id not in excluded_sensor_ids and \
                    sensor_id in candidate_sensor_ids and \
                    distance < TA_distance_Veh:  # Veh可分配距离阈值
                candidate = True
                TA_distance = TA_distance_Veh
                valid_sensor_num += 1
            elif node_type == 'U' and \
                    accuracy > lower_bound_accuracy and \
                    sensor_id not in excluded_sensor_ids and \
                    sensor_id in candidate_sensor_ids and \
                    distance < TA_distance_UAV:  # UAV可分配距离阈值
                candidate = True
                TA_distance = TA_distance_UAV
                valid_sensor_num += 1
            else:
                candidate = False
                TA_distance = 0

            if not(distance < TA_distance and sensor_id in candidate_sensor_ids):
                continue

            node_id=env.traffic_manager.completeStrId(sensor_state[0],sensor_state[1])
            old_node_info = old_traffic_info.get(node_id, None)
            if old_node_info is None:
                is_approaching = False
                in_threshold_predict = False
                distance_factor = 1
            else:
                old_position = old_node_info['position']
                new_position = node_position
                is_approaching = math_utils.check_approaching(old_position[:2], new_position[:2], target_position[:2])
                perpendicular_distance = math_utils.calculate_perpendicular_distance(old_position[:2], new_position[:2],
                                                                                     target_position[:2])
                in_threshold_predict = perpendicular_distance < exe_distance
                distance_factor = -1 if is_approaching is True else 1


            sensor_state[5]=candidate
            sensor_state[6] =distance_factor * distance
            sensor_state[10] = is_approaching
            sensor_state[11] = in_threshold_predict
            candidate_sensor_states.append(sensor_state)

        # sorted_sensor_states = sorted(available_sensor_states, key=lambda x: (not x[5], not x[11], not x[10],x[6]+5* x[12]))
        candidate_sensor_states = sorted(candidate_sensor_states, key=lambda x: (not x[5], not x[11], not x[10],x[6]))
        sorted_sensor_states=candidate_sensor_states

        approaching_num = 0
        in_threshold_num = 0
        uav_num = 0

        # sorted_sensor_states = sorted_sensor_states[:valid_sensor_num]
        sorted_sensor_states = sorted_sensor_states[:max_available_sensors]
        for sensor_state in sorted_sensor_states:
            if sensor_state[5] is False:
                continue

            if sensor_state[10] is True:
                approaching_num += 1
            if sensor_state[11] is True:
                in_threshold_num += 1
            if sensor_state[1] == 'U':
                uav_num += 1

        other_info = {
            'valid_sensor_num': valid_sensor_num,
            'approaching_num': approaching_num,
            'in_threshold_num': in_threshold_num,
            'uav_num': uav_num,
        }

        return sorted_sensor_states, other_info

    @staticmethod
    def getNeighborUAVStates(env, base_position, distance_threshold, max_num):
        """Get neighbor UAV states in (shape:[max_num,dim]).

         Args:
             env (AirFogSimEnv): The AirFogSim environment.
             base_position(str): The position of core UAV
             distance_threshold(float): The distance threshold of UAV search range
             max_num(list): The max num of searched UAVs

         Returns:
             list: list of neighbor UAV states

         Examples:
             algoSched.getNeighborUAVStates(env,[100,100,200],500,50)
         """
        # dim:[x, y, z]
        # [105.23, 568.15. 225.65]

        UAV_states = []
        UAV_infos = env.traffic_manager.getUAVTrafficInfos()
        for UAV_id, UAV_info in UAV_infos.items():
            node_index = int(UAV_id.split('_')[-1])
            UAV_position = UAV_info['position']
            x, y, z = UAV_position
            distance = math_utils.calculate_distance(base_position,UAV_position)

            if distance <= distance_threshold:
                state = [distance,node_index, x, y, z]
                UAV_states.append(state)
        states_sorted = sorted(UAV_states, key=lambda x: x[0])

        max_num_sensor_states = []
        for item in states_sorted[:max_num]:
            used_state = item[1:]
            max_num_sensor_states.append(used_state)

        return max_num_sensor_states

    @staticmethod
    def getTransMissionStates(env, base_position, distance_threshold,dim=2, max_num=None):
        """Get neighbor mission states in (shape:[max_num,dim]).

         Args:
             env (AirFogSimEnv): The AirFogSim environment.
             base_position(list): The position of core UAV
             distance_threshold(float): The distance threshold of UAV search range
             max_num(int): The max num of searched UAVs

         Returns:
             list: list of neighbor mission states

         Examples:
             algoSched.getTransMissionStates(env,[100,100,200],500,50)
         """
        # [left_sensing_time, left_return_size, x, y, z]
        # [5,30,120.25,262.05,553.25]

        mission_states = []
        executing_missions = env.mission_manager.getExecutingMissions()
        for node_id, missions in executing_missions.items():
            for mission in missions:
                current_node_id = mission.getCurrentNodeId()
                node = env._getNodeById(current_node_id)
                node_position = env.traffic_manager.getNodePositionById(node_id)
                x, y, z = node_position
                left_sensing_time = mission.getLeftSensingTime()
                left_return_size = mission.getLeftReturnSize()
                UAV_position=base_position.copy()
                if dim==2:
                    UAV_position[2]=0
                    node_position[2]=0
                distance = math_utils.calculate_distance(base_position,node_position)
                if distance < distance_threshold:
                    state = [distance, left_sensing_time, left_return_size, x, y, z]
                    mission_states.append(state)
        states_sorted = sorted(mission_states, key=lambda x: x[0])

        max_num_mission_states = []
        if max_num is not None:
            for item in states_sorted[:max_num]:
                used_state = item[1:]
                max_num_mission_states.append(used_state)
        else:
            max_num_mission_states = states_sorted

        return max_num_mission_states

    @staticmethod
    def getBeforeTransMissionStates(env, base_position, distance_threshold, dim=2, max_num=None):
        """Get neighbor mission states in (shape:[max_num,dim]).

         Args:
             env (AirFogSimEnv): The AirFogSim environment.
             base_position(list): The position of core UAV
             distance_threshold(float): The distance threshold of UAV search range
             max_num(int): The max num of searched UAVs

         Returns:
             list: list of neighbor mission states

         Examples:
             algoSched.getTransMissionStates(env,[100,100,200],500,50)
         """
        # [left_sensing_time, left_return_size, x, y, z]
        # [5,30,120.25,262.05,553.25]

        mission_states = []
        executing_missions = env.mission_manager.getExecutingMissions()
        for node_id, missions in executing_missions.items():
            for mission in missions:
                current_node_id = mission.getCurrentNodeId()
                node = env._getNodeById(current_node_id)
                node_position = env.traffic_manager.getNodePositionById(node_id)
                x, y, z = node_position
                left_sensing_time = mission.getLeftSensingTime()
                if not left_sensing_time>0:
                    continue
                left_return_size = mission.getLeftReturnSize()
                UAV_position = list(base_position.copy())
                node_position=list(node_position)
                if dim == 2:
                    UAV_position[2] = 0
                    node_position[2] = 0
                distance = math_utils.calculate_distance(base_position,node_position)
                if distance < distance_threshold:
                    state = [distance, left_sensing_time, left_return_size, x, y, z]
                    mission_states.append(state)
        states_sorted = sorted(mission_states, key=lambda x: x[0])

        max_num_mission_states = []
        if max_num is not None:
            for item in states_sorted[:max_num]:
                used_state = item[1:]
                max_num_mission_states.append(used_state)
        else:
            max_num_mission_states = states_sorted

        return max_num_mission_states

    @staticmethod
    def getSelfUAVStates(env, UAV_id):
        """Get UAV state in (shape:[dim]).

         Args:
             env (AirFogSimEnv): The AirFogSim environment.
             UAV_id(str): The id of UAV

         Returns:
             list: list of UAV state

         Examples:
             algoSched.getTransMissionStates(env,1)
         """
        # dim:[x, y, z, energy]
        # [105.23, 568.15, 225.65, 12000]
        node_position = env.traffic_manager.getNodePositionById(UAV_id)
        x, y, z = node_position
        left_energy = env.energy_manager.getEnergyById(UAV_id)
        state = [x, y, z, left_energy]
        return state

    @staticmethod
    def getSensorInfoByAction(env, action_index, sensor_states,grouping=True,allow_delay=False):
        """Get sensor info from sensor states by action index.

         Args:
             env (AirFogSimEnv): The AirFogSim environment.
             action_index(int): The index of sensor select action, corresponding to sensor
             sensor_states(list): Sensor states, [node_num, node_sensor_num, sensor_dim]
             node_dict(dict): {type_str:type_int,...}

         Returns:
             list: list of UAV state

         Examples:
             algoSched.getTransMissionStates(env, 1, [[node_id, node_type, id, type, accuracy, candidate], ..., ...], {'U':0,'V':1,'I':2,'C':3})
         """
        # [node_id,node_type,id,type, accuracy,candidate,distance,x,y,z,is_approaching,in_threshold_predict,node_speed]
        node_index_bias = 0
        node_type_bias = 1
        sensor_index_bias = 2
        accuracy_bias = 4
        is_approaching_bias=10
        in_threshold_predict_bias=11
        node_speed_bias=12


        if grouping:
            flattened_states = [sensor_state for node in sensor_states for sensor_state in node]
        else:
            flattened_states = sensor_states

        if allow_delay:
            action_index=action_index-1

        # 打印形状
        node_id_num = int(flattened_states[action_index][node_index_bias])
        sensor_id_num = int(flattened_states[action_index][sensor_index_bias])
        accuracy = flattened_states[action_index][accuracy_bias]
        node_type = flattened_states[action_index][node_type_bias]
        # node_type = [type_str for type_str, type_int in node_dict.items() if type_int == node_type]
        is_approaching=flattened_states[action_index][is_approaching_bias]
        in_threshold=flattened_states[action_index][in_threshold_predict_bias]
        node_speed=flattened_states[action_index][node_speed_bias]

        other_info={
            'is_approaching':is_approaching,
            'in_threshold':in_threshold,
            'node_speed':node_speed,
        }

        sensor_id = env.sensor_manager.completeSensorId(sensor_id_num)
        node_id = env.traffic_manager.completeStrId(node_id_num, node_type)

        return node_type, node_id, sensor_id, accuracy,other_info

    @staticmethod
    def getUAVStepRecord(env):
        UAV_energy_consumptions = env.getUAVStepEnergyConsumption()
        UAV_trans_datas = env.getUAVStepTransmissionSize()
        UAV_sensing_datas = env.getUAVStepSensingData()
        UAV_missions=env.getUAVmissions()

        return UAV_energy_consumptions, UAV_trans_datas, UAV_sensing_datas,UAV_missions

    @staticmethod
    def addUAVReward(env, UAV_id, reward):
        env.addUAVReward(UAV_id, reward)

    @staticmethod
    def getUAVRewards(env, UAV_id):
        return env.getUAVRewards(UAV_id)

    @staticmethod
    def getClusterCenter(env,mission_states):
        # [left_sensing_time, left_return_size, x, y, z]
        if len(mission_states) == 0:
            return None
        mission_states = np.array(mission_states)
        X = mission_states[:, 3:5]  # 只使用 x 和 y 坐标进行聚类

        eps=env.config['algorithm']['cluster_eps']
        dbscan = DBSCAN(eps=eps, min_samples=1)
        labels = dbscan.fit_predict(X)

        # 聚类后，计算每个聚类的加权位置中心
        unique_labels = set(labels)
        cluster_centers = {}

        for label in unique_labels:
            if label == -1:  # 忽略噪声点
                continue

            # 获取当前聚类的所有点
            cluster_points = mission_states[labels == label]

            # 计算加权位置中心
            weights = cluster_points[:, 2]  # 获取 left_return_size 作为权重
            weighted_x = np.sum(cluster_points[:, 3] * weights) / np.sum(weights)
            weighted_y = np.sum(cluster_points[:, 4] * weights) / np.sum(weights)

            cluster_centers[label] = [weighted_x, weighted_y,0]

        # 找到聚类中点最多的一个聚类
        largest_cluster_label = max(cluster_centers, key=lambda label: np.sum(labels == label))
        largest_cluster_center=cluster_centers[largest_cluster_label]


        # # 绘图
        # colorList = [
        #     (0, 1, 0),  # 绿色
        #     (0, 0, 1),  # 蓝色
        #     (1, 1, 0),  # 黄色
        #     (1, 0, 1),  # 品红色
        #     (0, 1, 1),  # 青色
        #     (0.5, 0, 0),  # 深红色
        #     (0, 0.5, 0),  # 深绿色
        #     (0, 0, 0.5),  # 深蓝色
        #     (0.5, 0.5, 0.5),  # 灰色
        #     (0, 0, 0)  # 白色
        # ]
        # plt.figure(clear=True)
        # # 绘制所有点，颜色根据聚类标签
        # for label in unique_labels:
        #     if label == -1:
        #         color = 'black'  # 噪声点为黑色
        #     else:
        #         color = colorList[label]
        #     plt.scatter(mission_states[labels == label][:, 3], mission_states[labels == label][:, 4], color=color, s=100,
        #                 label=f"Cluster {label}")
        # # 绘制最大的聚类中心，使用不同的颜色
        # plt.scatter(largest_cluster_center[0], largest_cluster_center[1], color='red', s=100,
        #             label='Largest Cluster Center')
        # # 添加标签和标题
        # plt.xlabel('X')
        # plt.ylabel('Y')
        # plt.title('DBSCAN Clustering and Largest Cluster')
        # # 显示图例
        # plt.legend()
        # plt.grid(True)
        # # 获取当前时间并格式化为文件名
        # current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        # # 创建cluster文件夹（如果不存在）
        # folder_path = "./cluster"
        # if not os.path.exists(folder_path):
        #     os.makedirs(folder_path)
        # # 保存图像到 cluster 文件夹，文件名为当前时间
        # plt.savefig(f"{folder_path}/{current_time}.png")

        # 返回该聚类的加权中心坐标
        return largest_cluster_center

    @staticmethod
    def getConfig(env,name):
        return env.config['algorithm'].get(name,None)