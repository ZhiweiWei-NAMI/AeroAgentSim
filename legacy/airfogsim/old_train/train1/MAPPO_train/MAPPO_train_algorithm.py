import argparse
import random

import torch
from pprint import pprint

from airfogsim.airfogsim_env import AirFogSimEnv
from airfogsim.algorithm.crowdsensing.TransDDQN.TransDDQN_env import TransDDQN_Env
from airfogsim.algorithm.crowdsensing.MAPPO.MAPPO_env import MAPPO_Env
from airfogsim.airfogsim_algorithm import BaseAlgorithmModule
from .ReplayBuffer import BaseReplayBuffer
import numpy as np

device = "cuda" if torch.cuda.is_available() else "cpu"
cuda_num = torch.cuda.device_count()
# device = "cpu"
base_dir="/home/chenjiarui/data/project/crowdsensing"


if cuda_num > 0:
    cuda_list = list(range(cuda_num))
    MAPPO_device = f"cuda:{cuda_list[cuda_num - 3]}"
else:
    MAPPO_device = "cpu"

print('device: ',MAPPO_device)
print('torch_version: ',torch.__version__)
print('cuda_num: ',cuda_num)


def parseMAPPOTrainArgs():
    parser = argparse.ArgumentParser(description='MAPPO train arguments')
    parser.add_argument('--learning_rate', type=float, default=5e-4)  # 学习率
    parser.add_argument('--gamma', type=float, default=0.96)  # 折扣因子
    parser.add_argument('--gae_lambda', type=float, default=0.98)  # GAE调整方差与偏差的系数，即GAE折扣因子，0.96-0.99
    parser.add_argument('--epsilon', type=float, default=0.2)  # 对估计优势的函数进行裁剪
    parser.add_argument('--epoch', type=int, default=10)  # episode数据训练轮数
    parser.add_argument('--device', type=str, default=MAPPO_device)  # 训练设备(GPU/CPU)
    parser.add_argument('--model_base_dir', type=str, default=f"./models")  # 模型文件路径
    args = parser.parse_args()
    return args


def parseMAPPODimArgs():
    # [x, y, z]
    dim_neighbor_UAV = 3
    m_neighbor_UAVs = 5
    # [left_sensing_time, left_return_size, x, y, z]
    dim_trans_mission = 5
    m_trans_missions = 50
    # [sensor_type, accuracy, return_size, arrival_time, TTL, duration, x, y, z, distance_threshold]
    dim_todo_mission = 10
    m_todo_missions = 6
    # [x, y, z, energy]
    dim_self_UAV = 4
    dim_observation = dim_neighbor_UAV * m_neighbor_UAVs + dim_trans_mission * m_trans_missions + dim_todo_mission * m_todo_missions + dim_self_UAV

    parser = argparse.ArgumentParser(description='MAPPO dimension arguments')
    # 分解维度
    parser.add_argument('--dim_neighbor_UAV', type=int, default=dim_neighbor_UAV)
    parser.add_argument('--m_neighbor_UAVs', type=int, default=m_neighbor_UAVs)
    parser.add_argument('--dim_trans_mission', type=int, default=dim_trans_mission)
    parser.add_argument('--m_trans_missions', type=int, default=m_trans_missions)
    parser.add_argument('--dim_todo_mission', type=int, default=dim_todo_mission)
    parser.add_argument('--m_todo_missions', type=int, default=m_todo_missions)
    parser.add_argument('--dim_self_UAV', type=int, default=dim_self_UAV)

    # 算法实际使用的维度
    parser.add_argument('--dim_observation', type=int, default=dim_observation)  # Dimension of observation
    parser.add_argument('--dim_action', type=int, default=1)  # Dimension of action [angle] (phi, speed取默认值)
    parser.add_argument('--n_agents', type=int, default=15)  # Number of agents
    parser.add_argument('--dim_hiddens', type=float, default=512)  # Dimension of hidden layer
    args = parser.parse_args()
    return args


class MAPPO_Train_AlgorithmModule(BaseAlgorithmModule):
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
        UAV: Decided by MAPPO model.
    '''

    class PathPlanReplayBuffer(BaseReplayBuffer):
        def __init__(self):
            # 创建一个字典，长度不限
            super().__init__()

        def __expToFlattenArray(self, exp):
            state = exp['state']
            action = exp['action']
            reward = exp['reward']
            next_state = exp['next_state']
            done = exp['done']
            return np.array(state), action, reward, np.array(next_state), done

        def add(self, exp_id, state, action, reward=None, next_state=None, done=None):
            self.buffer[exp_id] = {'state': state, 'action': action, 'reward': reward,
                                   'next_state': next_state, 'done': done}

        def setNextState(self, exp_id, next_state, done):
            assert exp_id in self.buffer, "State_id is invalid."
            self.buffer[exp_id]['next_state'] = next_state
            self.buffer[exp_id]['done'] = done

        def completeAndPopExperience(self, exp_id, reward):
            assert exp_id in self.buffer, "exp_id is invalid."
            self.buffer[exp_id]['reward'] = reward
            exp = self.buffer[exp_id].copy()
            # packed_exp = self.__expToFlattenArray(self.buffer[exp_id])
            del self.buffer[exp_id]
            return exp

        def size(self):
            return super().size()

        def clear(self):
            super().clear()

    def __init__(self):
        super().__init__()
        self.algorithm_module_tag = "MAPPO_Train"
        print('algorithm: ', self.algorithm_module_tag)

    def initialize(self, env: AirFogSimEnv, config={}, last_episode=None,final= False):
        """Initialize the algorithm with the environment. Including setting the task generation model, setting the reward model, etc.

        Args:
            env (AirFogSimEnv): The environment object.
        """
        self.rewardScheduler.setModel(env, 'REWARD',
                                      '5 * log(10, 1 + (_mission_deadline-_mission_duration_sum)) * (1 / (1 + exp(-(_mission_deadline-_mission_duration_sum) / (_mission_finish_time - _mission_arrival_time-_mission_duration_sum))) - 1 / (1 + exp(-1)))')
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

        self.MAPPO_dim_args = parseMAPPODimArgs()
        self.MAPPO_train_args = parseMAPPOTrainArgs()
        self.MAPPO_env = MAPPO_Env(self.MAPPO_dim_args, self.MAPPO_train_args)
        if last_episode is not None and last_episode > 0:
            self.MAPPO_env.loadModel(last_episode,final)

        self.last_mission_id = None  # Last allocated mission id,used in next state update
        self.pp_buffer = self.PathPlanReplayBuffer()
        self.last_UAV_states = {}

    def reset(self, env: AirFogSimEnv):
        self.last_mission_id = None  # Last allocated mission id,used in next state update
        self.pp_buffer.clear()
        self.last_UAV_states = {}


    def _encode_mission_states(self, mission_states, max_mission_num, dim_state):
        # [sensor_type, accuracy, return_size, arrival_time, TTL, duration, x, y, z, distance_threshold]
        # ['U',0.8,50,20,120,5,120.25,262.05,553.25,100]
        # 选取[sensor_type, accuracy, return_size, arrival_time, TTL, duration, x, y, z, distance_threshold]

        encode_states = []
        for mission_state in mission_states:
            sensor_type = mission_state[0]
            accuracy = mission_state[1]
            return_size = mission_state[2] / self.max_mission_size
            arrival_time = mission_state[3] / self.max_simulation_time
            TTL = mission_state[4] / self.max_simulation_time
            duration = mission_state[5] / self.max_simulation_time
            position_x = (mission_state[6] - self.min_position_x) / (self.max_position_x - self.min_position_x)
            position_y = (mission_state[7] - self.min_position_y) / (self.max_position_y - self.min_position_y)
            position_z = (mission_state[8] - self.min_position_z) / (self.max_position_z - self.min_position_z)
            distance_threshold = mission_state[9] / (self.max_position_x - self.min_position_x)

            state = [sensor_type, accuracy, return_size, arrival_time, TTL, duration, position_x, position_y,
                     position_z, distance_threshold]
            encode_states.append(state)

        # 补齐长度
        valid_mission_num = len(encode_states)
        if valid_mission_num < max_mission_num:
            for _ in range(max_mission_num - valid_mission_num):
                encode_states.append([0 for _ in range(dim_state)])  # 补充零

        return np.array(encode_states)


    def _encode_neighbor_UAV_states(self, UAV_states, max_UAV_num, dim_state):
        # [x, y, z]
        # [105.23, 568.15. 225.65]
        # 选取[x, y, z]

        encode_states = []
        for UAV_state in UAV_states:
            position_x = (UAV_state[0] - self.min_position_x) / (self.max_position_x - self.min_position_x)
            position_y = (UAV_state[1] - self.min_position_y) / (self.max_position_y - self.min_position_y)
            position_z = (UAV_state[2] - self.min_position_z) / (self.max_position_z - self.min_position_z)

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
            position_z = (mission_state[4] - self.min_position_z) / (self.max_position_z - self.min_position_z)

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

        position_x = (self_UAV_states[0] - self.min_position_x) / (self.max_position_x - self.min_position_x)
        position_y = (self_UAV_states[1] - self.min_position_y) / (self.max_position_y - self.min_position_y)
        position_z = (self_UAV_states[2] - self.min_position_z) / (self.max_position_z - self.min_position_z)
        energy = self_UAV_states[3] / self.max_energy
        state = [position_x, position_y, position_z, energy]

        return np.array(state)

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

    def _transformUAVMobilityPattern(self, norm_angle, norm_phi, norm_speed):
        angle = (norm_angle * 2 * np.pi) - np.pi
        phi = (norm_phi * 2 * np.pi) - np.pi

        speed_range_length = self.max_UAV_speed - self.min_UAV_speed
        speed = norm_speed * speed_range_length + self.min_UAV_speed
        return angle, phi, speed

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
        a_loss,c_loss=self.MAPPO_env.train()
        return a_loss,c_loss

    def saveModel(self, episode,final):
        self.MAPPO_env.saveModel(episode,final)

    def scheduleMission(self, env: AirFogSimEnv):
        """The mission scheduling logic.
        Mission: Missions assigned to both vehicles and UAVs, each type has a probability of sum of 1.
        Sensor: Assigned to vehicle, select the sensor closest to PoI from the idle sensors with accuracy higher than required(Distance First).
                Assigned to RSU, select the sensor with the lowest accuracy from the idle sensors with accuracy higher than required(Accuracy Lowerbound).

        Args:
            env (AirFogSimEnv): The environment object.

        """
        super().scheduleMission(env)

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
        distance_threshold = self.missionScheduler.getConfig(env, 'distance_threshold')
        traffic_interval = self.trafficScheduler.getTrafficInterval(env)
        UAV_infos = self.trafficScheduler.getUAVTrafficInfos(env)
        global_UAV_states = {}
        UAV_mobile_patterns = {}
        for UAV_id, UAV_info in UAV_infos.items():
            UAV_index = int(UAV_id.split('_')[-1])  # 转换为整数
            current_position = UAV_info['position']
            self.trafficScheduler.updateRoute(env, UAV_id, current_position, distance_threshold, traffic_interval)

            neighbor_UAV_states = self.algorithmScheduler.getNeighborUAVStates(env, current_position,
                                                                               distance_threshold,
                                                                               self.MAPPO_dim_args.m_neighbor_UAVs)
            trans_mission_states = self.algorithmScheduler.getTransMissionStates(env, current_position,
                                                                                 distance_threshold,
                                                                                 self.MAPPO_dim_args.m_trans_missions)
            todo_mission_profiles = self.missionScheduler.getExecutingMissionProfiles(env, UAV_id)
            todo_mission_states = self.algorithmScheduler.getMissionStates(env, todo_mission_profiles)
            self_UAV_state = self.algorithmScheduler.getSelfUAVStates(env, UAV_id)

            encode_neighbor_UAV_states = self._encode_neighbor_UAV_states(neighbor_UAV_states,
                                                                          self.MAPPO_dim_args.m_neighbor_UAVs,
                                                                          self.MAPPO_dim_args.dim_neighbor_UAV).flatten()
            encode_trans_mission_states = self._encode_trans_mission_states(trans_mission_states,
                                                                            self.MAPPO_dim_args.m_trans_missions,
                                                                            self.MAPPO_dim_args.dim_trans_mission).flatten()
            encode_todo_mission_states = self._encode_mission_states(todo_mission_states,
                                                                     self.MAPPO_dim_args.m_todo_missions,
                                                                     self.MAPPO_dim_args.dim_todo_mission).flatten()
            encode_self_UAV_state = self._encode_self_UAV_states(self_UAV_state).flatten()
            combined_state = np.concatenate((encode_neighbor_UAV_states, encode_trans_mission_states,
                                             encode_todo_mission_states, encode_self_UAV_state))

            global_UAV_states[UAV_index] = combined_state

        encode_global_states = self._encode_global_UAV_states(global_UAV_states,self.max_n_UAVs,self.MAPPO_dim_args.dim_observation)
        norm_actions = self.MAPPO_env.takeAction(encode_global_states) # Tensor

        for idx, norm_action in enumerate(norm_actions):
            norm_action=norm_action.to('cpu').numpy() # 转numpy数组
            # 计算 xy 平面的方位角
            norm_angle = norm_action[0]
            # z 相对于 xy 平面的仰角为0
            norm_phi=0
            # 最大速度飞行
            norm_speed=1

            angle, phi, speed = self._transformUAVMobilityPattern(norm_angle=norm_angle, norm_phi=norm_phi,
                                                                  norm_speed=norm_speed)
            mobility_pattern = {'angle': angle, 'phi': phi, 'speed': speed}
            UAV_id = self.trafficScheduler.completeStrId(env,idx, 'U')
            UAV_mobile_patterns[UAV_id] = mobility_pattern

            last_state = self.last_UAV_states.get(UAV_id, None)
            if last_state is not None:
                self.pp_buffer.add(UAV_id, last_state, norm_action)
                self.pp_buffer.setNextState(UAV_id, encode_global_states[idx], False)
            self.last_UAV_states[UAV_id] = encode_global_states[idx]

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
        if self.pp_buffer.size() == 0:
            return

        UAV_energy_consumptions, UAV_trans_datas, UAV_sensing_datas = self.algorithmScheduler.getUAVStepRecord(env)
        active_UAV_ids = UAV_energy_consumptions.keys()

        exps={}
        all_states=[]
        all_next_states = []
        for idx in range(self.max_n_UAVs):
            UAV_id = self.trafficScheduler.completeStrId(env,idx, 'U')
            if UAV_id in active_UAV_ids:
                energy_consumption = UAV_energy_consumptions[UAV_id]
                trans_data = UAV_trans_datas[UAV_id]
                sensing_data = UAV_sensing_datas[UAV_id]
                reward = trans_data + sensing_data - energy_consumption
                exp = self.pp_buffer.completeAndPopExperience(UAV_id, reward)
            else:
                reward = 0
                exp = self.pp_buffer.completeAndPopExperience(UAV_id, reward)
            exps[idx] = exp

            state = np.array(exp["state"] )  # 提取 state 并转换为 numpy 数组
            all_states.append(state)
            next_state = np.array(exp["next_state"])
            all_next_states.append(next_state)

        all_states = np.stack(all_states, axis=0)
        all_next_states = np.stack(all_next_states, axis=0)
        for idx,exp in exps.items():
            reward = np.array(exp["reward"])  # 提取 reward 并转换为 numpy 数组
            action = np.array(exp["action"] )  # 提取 action 并转换为 numpy 数组

            self.MAPPO_env.addExperience(idx,all_states,action, reward,all_next_states)

        self.pp_buffer.clear()
