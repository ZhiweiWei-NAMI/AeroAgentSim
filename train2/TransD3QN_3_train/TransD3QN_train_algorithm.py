import argparse
import random

import torch
from pprint import pprint

from sympy.physics.units import action

from airfogsim.airfogsim_env import AirFogSimEnv
from airfogsim.algorithm.crowdsensing.TransD3QN3.TransD3QN_env import TransD3QN_Env
from airfogsim.airfogsim_algorithm import BaseAlgorithmModule
from .ReplayBuffer import BaseReplayBuffer
import numpy as np

device = "cuda" if torch.cuda.is_available() else "cpu"
cuda_num = torch.cuda.device_count()
# device = "cpu"
base_dir="/home/chenjiarui/data/project/crowdsensing"


if cuda_num > 0:
    cuda_list = list(range(cuda_num))
    TransD3QN_device = f"cuda:{cuda_list[cuda_num - 5]}"
else:
    TransD3QN_device = "cpu"

print('device: ',TransD3QN_device)
print('torch_version: ',torch.__version__)
print('cuda_num: ',cuda_num)


def parseTransD3QNTrainArgs():
    parser = argparse.ArgumentParser(description='TransD3QN UAV train arguments')
    parser.add_argument('--buffer_size', type=int, default=50000)  # 经验池容量
    parser.add_argument('--learning_rate', type=float, default=2e-4)  # 学习率
    parser.add_argument('--gamma', type=float, default=0.96)  # 折扣因子
    parser.add_argument('--epsilon', type=float, default=0.9)  # 探索系数
    parser.add_argument('--eps_end', type=float, default=0.01)  # 最低探索系数
    parser.add_argument('--eps_dec', type=float, default=0.999)  # 探索系数衰减率
    parser.add_argument('--target_update', type=int, default=200)  # 目标网络的参数的更新频率
    parser.add_argument('--batch_size', type=int, default=64)  # 每次训练选取的经验数量32
    parser.add_argument('--train_min_size', type=int, default=500)  # 经验池超过200后再训练(train_min_size>batch_size)
    parser.add_argument('--tau', type=float, default=0.01)  # 目标网络软更新平滑因子(训练策略网络权重)
    parser.add_argument('--smooth_factor', type=float, default=0.99)  # 最大q值平滑因子（旧值权重）
    parser.add_argument('--device', type=str, default=TransD3QN_device)  # 训练设备(GPU/CPU)
    parser.add_argument('--model_base_dir', type=str, default=f"./models")  # 模型文件路径

    args = parser.parse_args()
    return args


def parseTransD3QNDimArgs():
    # [x, y, z]
    dim_neighbor_UAV = 3
    m_neighbor_UAVs = 5
    # [left_sensing_time, left_return_size, x, y, z]
    dim_trans_mission = 5
    m_trans_missions = 50
    # # [norm_cur_time,sensor_type, accuracy, return_size, arrival_time, TTL, duration, x, y, z]
    # dim_todo_mission = 10
    # [left_mission_time, left_duration, x, y, z]
    dim_todo_mission = 5
    m_todo_missions = 4
    # # [x, y, z, energy]
    # dim_self_UAV = 4
    # [x, y, z]
    dim_self_UAV = 3
    # dim_observation = dim_neighbor_UAV * m_neighbor_UAVs + dim_trans_mission * m_trans_missions + dim_todo_mission * m_todo_missions + dim_self_UAV
    dim_observation = dim_todo_mission * m_todo_missions + dim_self_UAV

    max_sensors=4

    parser = argparse.ArgumentParser(description='TransD3QN dimension arguments')
    # 分解维度
    parser.add_argument('--dim_neighbor_UAV', type=int, default=dim_neighbor_UAV)
    parser.add_argument('--m_neighbor_UAVs', type=int, default=m_neighbor_UAVs)
    parser.add_argument('--dim_trans_mission', type=int, default=dim_trans_mission)
    parser.add_argument('--m_trans_missions', type=int, default=m_trans_missions)
    parser.add_argument('--dim_todo_mission', type=int, default=dim_todo_mission)
    parser.add_argument('--m_todo_missions', type=int, default=m_todo_missions)
    parser.add_argument('--dim_self_UAV', type=int, default=dim_self_UAV)
    parser.add_argument('--max_sensors', type=int, default=max_sensors)

    # 算法实际使用的维度
    parser.add_argument('--dim_model', type=int, default=256)  # Embedding feature dimension
    parser.add_argument('--nhead', type=float, default=4)  # Head num
    parser.add_argument('--num_layers', type=float, default=3)  # Transformer encoder layers num
    parser.add_argument('--dim_actions', type=float, default=9)  # Dimension of hidden layer
    parser.add_argument('--dim_hiddens', type=float, default=256)  # Dimension of hidden layer
    parser.add_argument('--dim_value', type=float, default=128)  # Dimension of value sub layer
    parser.add_argument('--dim_advantages', type=float, default=128)  # Dimension of advantages sub layer

    args = parser.parse_args()
    return args


class TransD3QN_Train_AlgorithmModule(BaseAlgorithmModule):
    """
    Use different schedulers to interact with the environment before calling env.step(). Manipulate different environments with the same algorithm design at the same time for learning sampling efficiency.\n
    Any implementation of the algorithm should inherit this class and implement the algorithm logic in the `scheduleStep()` method.
    """

    '''
    scheduleOffloading: BaseAlgorithm.
    scheduleComputing: BaseAlgorithm.
    scheduleCommunication: BaseAlgorithm.
    scheduleMission: BaseAlgorithm
    scheduleReturning: BaseAlgorithm
    scheduleTraffic: 
        UAV: Decided by TransD3QN model.
    '''

    class PathPlanReplayBuffer(BaseReplayBuffer):
        def __init__(self):
            # 创建一个字典，长度不限
            super().__init__()

        def __expToFlattenArray(self, exp):
            node_state = exp['node_state']
            mission_state = exp['mission_state']
            action = exp['action']
            reward = exp['reward']
            next_node_state = exp['next_node_state']
            next_mission_state = exp['next_mission_state']
            done = exp['done']
            return (np.array(node_state), np.array(mission_state), np.array(action), np.array(reward),
                    np.array(next_node_state), np.array(next_mission_state),  np.array(done))

        def add(self, exp_id, node_state, mission_state, action, reward=None,
                next_node_state=None, next_mission_state=None, done=None):
            self.buffer[exp_id] = {'node_state': node_state, 'mission_state': mission_state, 'action': action, 'reward': reward,
                                   'next_node_state': next_node_state, 'next_mission_state': next_mission_state,
                                   'done': done}

        def setNextState(self, exp_id, next_node_state, next_mission_state, done):
            # assert exp_id in self.buffer, "State_id is invalid."
            if exp_id not in self.buffer:
                return

            self.buffer[exp_id]['next_node_state'] = next_node_state
            self.buffer[exp_id]['next_mission_state'] = next_mission_state
            self.buffer[exp_id]['done'] = done

        def completeAndPopExperience(self, exp_id, reward):
            if exp_id not in self.buffer:
                return None
            if self.buffer[exp_id]['next_node_state'] is None:
                return None

            self.buffer[exp_id]['reward'] = reward if self.buffer[exp_id]['reward'] is None else \
            self.buffer[exp_id]['reward'] + reward
            packed_exp = self.__expToFlattenArray(self.buffer[exp_id])
            del self.buffer[exp_id]
            return packed_exp

        def size(self):
            return super().size()

        def clear(self):
            super().clear()

    def __init__(self):
        super().__init__()
        self.algorithm_module_tag = "TransD3QN UAV Train"
        print('algorithm: ', self.algorithm_module_tag)

    def initialize(self, env: AirFogSimEnv, config={}, last_episode=None,final= False):
        """Initialize the algorithm with the environment. Including setting the task generation model, setting the reward model, etc.

        Args:
            env (AirFogSimEnv): The environment object.
        """
        self.rewardScheduler.setModel(env, 'REWARD','1-(_mission_finish_time - _mission_arrival_time-_mission_duration_sum)/(_mission_deadline-_mission_duration_sum)')
        self.rewardScheduler.setModel(env, 'PUNISH', '-1')
        
        self.max_simulation_time = env.max_simulation_time
        self.min_position_x, self.max_position_x = self.trafficScheduler.getMapRange(env, 'X')
        self.min_position_y, self.max_position_y = self.trafficScheduler.getMapRange(env, 'Y')
        self.min_position_z, self.max_position_z = self.trafficScheduler.getMapRange(env, 'Z')
        self.min_UAV_speed, self.max_UAV_speed = self.trafficScheduler.getConfig(env, 'UAV_speed_range')
        self.max_n_vehicles = self.trafficScheduler.getConfig(env, 'max_n_vehicles')
        self.max_n_UAVs = self.trafficScheduler.getConfig(env, 'max_n_UAVs')
        self.max_n_RSUs = self.trafficScheduler.getConfig(env, 'max_n_RSUs')
        self.max_mission_size = self.missionScheduler.getConfig(env, 'mission_size_range')[1]
        self.max_energy = env.energy_manager.getConfig('initial_energy_range')[0]
        self.TA_distance_Veh=self.missionScheduler.getConfig(env, 'TA_distance_Veh')
        self.TA_distance_UAV = self.missionScheduler.getConfig(env, 'TA_distance_UAV')
        self.node_type_dict = {
            'U': 1,
            'V': 2,
            'I': 3,
            'C': 4,
        }
        self.node_priority = {
            'U': 1,
            'V': 2,
            'I': 3,
        }
        self.action_angle = {
            0: 0,  # 0 方向对应 0
            1: np.arctan2(0, 1),  # 右
            2: np.arctan2(1, 1),  # 右上
            3: np.arctan2(1, 0),  # 上
            4: np.arctan2(1, -1),  # 左上
            5: np.arctan2(0, -1),  # 左
            6: np.arctan2(-1, -1),  # 左下
            7: np.arctan2(-1, 0),  # 下
            8: np.arctan2(-1, 1),  # 右下
        }

        self.TransD3QN_dim_args = parseTransD3QNDimArgs()
        self.TransD3QN_train_args = parseTransD3QNTrainArgs()
        self.TransD3QN_env = TransD3QN_Env(self.TransD3QN_dim_args, self.TransD3QN_train_args)
        if last_episode is not None and last_episode > 0:
            self.TransD3QN_env.loadModel(last_episode,final)

        self.last_mission_id = None  # Last allocated mission id,used in next state update
        self.pp_buffer = self.PathPlanReplayBuffer()
        self.last_UAV_states = {}
        self.last_UAV_positions = env.getUAVPositions()

    def reset(self, env: AirFogSimEnv):
        self.last_mission_id = None  # Last allocated mission id,used in next state update
        self.pp_buffer.clear()
        self.last_UAV_states = {}
        self.last_UAV_positions = env.getUAVPositions()


    def _encode_mission_states(self, mission_states, max_mission_num,current_time, dim_state):
        # dim:[arrive_time, TTL, duration,left_duration, x, y, z]
        # [50,40,10,2,262.05,553.25,100]
        # 选取[left_mission_time, left_duration, x, y, z]

        encode_states = []
        norm_cur_time = current_time/self.max_simulation_time
        for mission_state in mission_states:
            arrival_time = mission_state[0]
            TTL = mission_state[1]
            duration=mission_state[2]
            left_duration=mission_state[3]
            left_mission_time_norm=(TTL-(current_time-arrival_time))/TTL
            left_duration_norm=left_duration/duration

            position_x = (mission_state[4] - self.min_position_x) / (self.max_position_x - self.min_position_x)
            position_y = (mission_state[5] - self.min_position_y) / (self.max_position_y - self.min_position_y)
            position_z = (mission_state[6] - self.min_position_z) / (self.max_position_z - self.min_position_z)  if (self.max_position_z - self.min_position_z) > 0 else 0

            state = [left_mission_time_norm,left_duration_norm,position_x, position_y,position_z]
            encode_states.append(state)

        # 补齐长度
        valid_mission_num = len(encode_states)
        if valid_mission_num < max_mission_num:
            for _ in range(max_mission_num - valid_mission_num):
                encode_states.append([0 for _ in range(dim_state)])  # 补充零

        return np.array(encode_states)


    def _encode_neighbor_UAV_states(self, UAV_states, max_UAV_num, dim_state):
        # [id, x, y, z]
        # [105.23, 568.15. 225.65]
        # 选取[x, y, z]

        encode_states = []
        for UAV_state in UAV_states:
            id=UAV_state[0]
            position_x = (UAV_state[1] - self.min_position_x) / (self.max_position_x - self.min_position_x)
            position_y = (UAV_state[2] - self.min_position_y) / (self.max_position_y - self.min_position_y)
            position_z = (UAV_state[3] - self.min_position_z) / (self.max_position_z - self.min_position_z)  if (self.max_position_z - self.min_position_z) > 0 else 1

            state = [position_x, position_y, position_z]
            encode_states.append(state)

        # 补齐长度
        valid_UAV_num = len(encode_states)
        if valid_UAV_num < max_UAV_num:
            for _ in range(max_UAV_num - valid_UAV_num):
                encode_states.append([0 for _ in range(dim_state)])  # 补充零

        return np.array(encode_states)

    def _encode_trans_mission_states(self, trans_mission_states, max_mission_num, dim_state):
        # [left_sensing_time, left_return_size, x, y, z]
        # [5,30,120.25,262.05,553.25]
        # 选取[left_sensing_time, left_return_size, x, y, z]

        encode_states = []
        for mission_state in trans_mission_states:
            left_sensing_time = mission_state[0] / self.max_simulation_time
            left_return_size = mission_state[1] / self.max_mission_size
            position_x = (mission_state[2] - self.min_position_x) / (self.max_position_x - self.min_position_x)
            position_y = (mission_state[3] - self.min_position_y) / (self.max_position_y - self.min_position_y)
            position_z = (mission_state[4] - self.min_position_z) / (self.max_position_z - self.min_position_z)  if (self.max_position_z - self.min_position_z) > 0 else 0

            state = [left_sensing_time, left_return_size, position_x, position_y, position_z]
            encode_states.append(state)

        # 补齐长度
        valid_mission_num = len(encode_states)
        if valid_mission_num < max_mission_num:
            for _ in range(max_mission_num - valid_mission_num):
                encode_states.append([0 for _ in range(dim_state)])  # 补充零

        return np.array(encode_states)

    def _encode_self_UAV_states(self, self_UAV_states):
        # [x, y, z, energy]
        # [105.23, 568.15, 225.65, 12000]
        # 选取[x, y, z, energy]
        states=[]

        position_x = (self_UAV_states[0] - self.min_position_x) / (self.max_position_x - self.min_position_x)
        position_y = (self_UAV_states[1] - self.min_position_y) / (self.max_position_y - self.min_position_y)
        position_z = (self_UAV_states[2] - self.min_position_z) / (self.max_position_z - self.min_position_z)  if (self.max_position_z - self.min_position_z) > 0 else 1
        energy = self_UAV_states[3] / self.max_energy
        # state = [position_x, position_y, position_z, energy]

        state = [position_x, position_y, position_z]
        states.append(state)

        return np.array(states)

    def _encode_global_UAV_states(self, states_dict, max_UAV_num, dim_state):
        encode_states = []
        for i in range(max_UAV_num):
            state = states_dict.get(i, None)
            if state is None:
                encode_states.append([0 for _ in range(dim_state)])  # 补充零
            else:
                state = state.tolist()
                encode_states.append(state)
        return np.array(encode_states)


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

    def train(self, env: AirFogSimEnv):
        loss=self.TransD3QN_env.train()
        return loss

    def saveModel(self, episode,final,succ_ratio):
        self.TransD3QN_env.saveModel(episode,final,succ_ratio)

    def scheduleMission(self, env: AirFogSimEnv):
        """The mission scheduling logic.
        Mission: Missions assigned to both vehicles and UAVs, each type has a probability of sum of 1.
        Sensor: Assigned to vehicle, select the sensor closest to PoI from the idle sensors with accuracy higher than required(Distance First).
                Assigned to RSU, select the sensor with the lowest accuracy from the idle sensors with accuracy higher than required(Accuracy Lowerbound).

        Args:
            env (AirFogSimEnv): The environment object.

        """
        cur_time = self.trafficScheduler.getCurrentTime(env)
        traffic_interval = self.trafficScheduler.getTrafficInterval(env)
        UAV_time_discount = self.missionScheduler.getConfig(env, 'UAV_time_discount')
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

            UAV_infos = self.trafficScheduler.getNodeInfosInRange(env, sensing_position, TA_distance_UAV, "U")
            vehicle_infos = self.trafficScheduler.getNodeInfosInRange(env, sensing_position, TA_distance_Veh, "V")
            node_infos = {}
            node_infos.update(vehicle_infos)
            node_infos.update(UAV_infos)

            appointed_node_id, appointed_sensor_id, appointed_sensor_accuracy = self.sensorScheduler.getSuitableIdleSensorInNodes(
                env, mission_sensor_type, mission_accuracy, sensing_position, node_infos, excluded_sensor_ids,
                delay=True)

            if appointed_node_id != None and appointed_sensor_id != None:
                node_type = env._getNodeTypeById(appointed_node_id)
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
                    self.trafficScheduler.addUAVRoute(env, mission_profile['mission_id'], appointed_node_id,
                                                      mission_profile['mission_routes'][0],
                                                      mission_profile['mission_duration'][0] * UAV_time_discount,
                                                      mission_profile['mission_arrival_time'] + mission_profile[
                                                          'mission_deadline'])
                self.missionScheduler.generateAndAddMission(env, mission_profile)
                allocate_num += 1

                delete_mission_profile_ids.append(mission_profile['mission_id'])
                excluded_sensor_ids.append(appointed_sensor_id)

        self.missionScheduler.setMissionEvaluationIndicators(env, generate_num, allocate_num)
        self.missionScheduler.deleteBeAssignedMissionsProfile(env, delete_mission_profile_ids)

    def scheduleReturning(self, env: AirFogSimEnv):
        """The returning logic. Relay or direct is controlled by probability.
        Relay(only for task assigned to vehicle), select nearest UAV and nearest RSU, return_route=[UAV,RSU]
        Direct, select nearest RSU, return_route=[RSU]

        Args:
            env (AirFogSimEnv): The environment object.
        """
        super().scheduleReturning(env)

    def scheduleTraffic(self, env: AirFogSimEnv):
        """The UAV traffic scheduling logic. Should be implemented by the subclass. Default is move to the next
         mission sensing or task position. If there is no mission allocated to UAV, movement is random.

        Args:
            env (AirFogSimEnv): The environment object.
        """
        cur_time = self.trafficScheduler.getCurrentTime(env)
        distance_threshold = self.missionScheduler.getConfig(env, 'distance_threshold')
        observe_threshold=self.algorithmScheduler.getConfig(env, 'observe_threshold')
        traffic_interval = self.trafficScheduler.getTrafficInterval(env)
        UAV_infos = self.trafficScheduler.getUAVTrafficInfos(env)
        global_UAV_states = {}
        UAV_mobile_patterns = {}
        for UAV_id, UAV_info in UAV_infos.items():
            UAV_index = int(UAV_id.split('_')[-1])  # 转换为整数
            current_position = UAV_info['position']
            self.trafficScheduler.updateRoute(env, UAV_id, current_position, distance_threshold, traffic_interval)

            neighbor_UAV_states = self.algorithmScheduler.getNeighborUAVStates(env, current_position,
                                                                               observe_threshold,
                                                                               self.TransD3QN_dim_args.m_neighbor_UAVs)
            trans_mission_states = self.algorithmScheduler.getTransMissionStates(env, current_position,
                                                                                 observe_threshold,
                                                                                 self.TransD3QN_dim_args.m_trans_missions)
            todo_mission_profiles = self.missionScheduler.getExecutingMissionProfiles(env, UAV_id)
            todo_mission_states = self.algorithmScheduler.getExecutingMissionStates(env, todo_mission_profiles)
            self_UAV_state = self.algorithmScheduler.getSelfUAVStates(env, UAV_id)

            encode_neighbor_UAV_states = self._encode_neighbor_UAV_states(neighbor_UAV_states,
                                                                          self.TransD3QN_dim_args.m_neighbor_UAVs,
                                                                          self.TransD3QN_dim_args.dim_neighbor_UAV)
            encode_trans_mission_states = self._encode_trans_mission_states(trans_mission_states,
                                                                            self.TransD3QN_dim_args.m_trans_missions,
                                                                            self.TransD3QN_dim_args.dim_trans_mission)
            encode_todo_mission_states = self._encode_mission_states(todo_mission_states,
                                                                     self.TransD3QN_dim_args.m_todo_missions,
                                                                     cur_time,
                                                                     self.TransD3QN_dim_args.dim_todo_mission)
            encode_self_UAV_state = self._encode_self_UAV_states(self_UAV_state)

            is_random,max_q_value,action_idx = self.TransD3QN_env.takeAction(encode_self_UAV_state,encode_todo_mission_states) # Tensor
            env.addMissionTaIdx(action_idx)

            # 计算 xy 平面的方位角
            angle = self.action_angle[int(action_idx)]

            mobility_pattern = {}
            mobility_pattern['angle'] = angle
            mobility_pattern['phi'] = 0  # 强制只进行水平飞行
            UAV_speed_range = self.trafficScheduler.getConfig(env, 'UAV_speed_range')
            # action_idx为0表示悬停
            mobility_pattern['speed'] = random.uniform(UAV_speed_range[0], UAV_speed_range[1]) if action_idx>0 else 0
            UAV_mobile_patterns[UAV_id] = mobility_pattern

            last_state = self.last_UAV_states.get(UAV_id, None)
            if last_state is not None:
                last_node_states = last_state['last_node_states']
                last_mission_states = last_state['last_mission_states']
                self.pp_buffer.add(UAV_id, last_node_states,last_mission_states, action_idx)
                self.pp_buffer.setNextState(UAV_id,encode_self_UAV_state,encode_todo_mission_states, False)
            self.last_UAV_states[UAV_id] = {
                'last_node_states': encode_self_UAV_state,
                'last_mission_states': encode_todo_mission_states,
            }

        self.trafficScheduler.setUAVMobilityPatterns(env, UAV_mobile_patterns)

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


    def updatePPExperience(self, env: AirFogSimEnv):
        distance_threshold = self.missionScheduler.getConfig(env, 'distance_threshold')
        if self.pp_buffer.size() == 0:
            return
        rewards=self._getUAVReward(env,distance_threshold)
        self.last_UAV_positions=env.getUAVLastPositions()


        for idx in range(self.max_n_UAVs):
            UAV_id = self.trafficScheduler.completeStrId(env,idx, 'U')
            reward = float(rewards[UAV_id])
            exp = self.pp_buffer.completeAndPopExperience(UAV_id, reward)
            node_state=exp[0]
            mission_state=exp[1]
            action=exp[2]
            reward=exp[3]
            next_node_state=exp[4]
            next_mission_state=exp[5]
            done=exp[6]

            self.algorithmScheduler.addUAVReward(env, UAV_id, reward)
            self.TransD3QN_env.addExperience(*exp)

        self.pp_buffer.clear()

    def _getUAVReward(self, env: AirFogSimEnv,distance_threshold):
        last_time=max(env.simulation_time-env.simulation_interval,0)
        last_step_succ_mission_infos = self.missionScheduler.getLastStepSuccMissionInfos(env)
        last_step_fail_mission_infos = self.missionScheduler.getLastStepFailMissionInfos(env)

        UAV_energy_consumptions, UAV_trans_datas, UAV_sensing_datas,UAV_missions = self.algorithmScheduler.getUAVStepRecord(env)
        active_UAV_ids = UAV_energy_consumptions.keys()
        rewards={UAV_id:0 for UAV_id in active_UAV_ids}

        # UAV positon is 2d
        old_positions=self.last_UAV_positions
        new_positions=env.getUAVLastPositions()


        # mission靠近奖励
        for UAV_id,missions in UAV_missions.items():
            approach_rewards=0
            old_UAV_position_2d = old_positions.get(UAV_id, None)
            new_UAV_position_2d = new_positions.get(UAV_id, None)
            for mission in missions:
                mission_TTL=mission._mission_deadline
                mission_arrival_time=mission._mission_arrival_time
                # mission_position_2d=mission.getRoutes()[0][:2]
                # old_distance=np.linalg.norm(np.asarray(old_UAV_position_2d) - np.asarray(mission_position_2d))
                # new_distance=np.linalg.norm(np.asarray(new_UAV_position_2d) - np.asarray(mission_position_2d))
                # delta_distance=old_distance-new_distance
                #
                # sensing_weight=mission.getLeftSensingTime()
                # TTL_weight=30/max(1,(mission_TTL-(last_time-mission_arrival_time)))
                # if old_distance>200:
                #     distance_weight=150*20/(old_distance-150)
                # else:
                #     distance_weight=-0.25*(max(150.0,float(old_distance))-200)+60
                # attract=sensing_weight*TTL_weight*distance_weight
                #
                # approach_reward=attract*delta_distance
                # approach_rewards+=approach_reward
                #
                # print(mission.getMissionId())
                # print('delta_distance: ',delta_distance)
                # print('sensing_weight: ',sensing_weight)
                # print('TTL_weight: ',TTL_weight)
                # print('distance_weight: ',distance_weight)
                # print('attract: ',attract)
                # print('approach_reward: ',approach_reward)

                # 简化版接近奖励
                mission_position_2d = mission.getRoutes()[0][:2]
                old_distance=np.linalg.norm(np.asarray(old_UAV_position_2d) - np.asarray(mission_position_2d))
                new_distance=np.linalg.norm(np.asarray(new_UAV_position_2d) - np.asarray(mission_position_2d))
                delta_distance=old_distance-new_distance
                # approach_reward = delta_distance #if new_distance>distance_threshold or (old_distance < distance_threshold < new_distance) else 0
                left_time=mission_TTL-(last_time-mission_arrival_time)
                attract=min(mission_TTL/left_time,10) if left_time>0 else 0
                approach_reward= delta_distance*attract/10
                approach_rewards += approach_reward
                print('attract',attract)
                print('approach_reward', approach_reward)

            print('sum_approach_rewards: ', approach_rewards)
            rewards[UAV_id]+=approach_rewards



        for UAV_id in active_UAV_ids:
            trans_reward=0#10*UAV_trans_datas[UAV_id] # 辅助通信奖励
            sensing_reward=20 * UAV_sensing_datas[UAV_id] # 感知奖励
            energy_punish= 0 #-2 * UAV_energy_consumptions[UAV_id] # 能耗惩罚
            rewards[UAV_id] +=trans_reward+sensing_reward+energy_punish
            print(UAV_id)
            print('trans_reward: ',trans_reward)
            print('sensing_reward: ',sensing_reward)
            print('energy_punish: ',energy_punish)

        # 任务完成奖励
        for mission_info in last_step_succ_mission_infos:
            node_id=mission_info["appointed_node_id"]
            node_type=env._getNodeTypeById(node_id)
            if node_type != 'U':
                continue
            reward = self.rewardScheduler.getRewardByMission(env, mission_info)
            rewards[node_id]+=reward
            print(node_id)
            print('mission_reward: ',reward)

        #任务失败惩罚
        for mission_info in last_step_fail_mission_infos:
            node_id=mission_info["appointed_node_id"]
            node_type=env._getNodeTypeById(node_id)
            if node_type != 'U':
                continue
            punish = self.rewardScheduler.getPunishByMission(env, mission_info)
            rewards[node_id]+=punish
            print(node_id)
            print('mission_punish: ',punish)
            print()

        return rewards




