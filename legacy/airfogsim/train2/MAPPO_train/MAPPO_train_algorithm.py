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
    MAPPO_device = f"cuda:{cuda_list[cuda_num - 5]}"
else:
    MAPPO_device = "cpu"

print('device: ',MAPPO_device)
print('torch_version: ',torch.__version__)
print('cuda_num: ',cuda_num)


def parseMAPPOTrainArgs():
    parser = argparse.ArgumentParser(description='MAPPO train arguments')
    parser.add_argument('--learning_rate', type=float, default=2e-4)  # 学习率
    parser.add_argument('--var', type=float, default=0.9)  # 探索系数
    parser.add_argument('--var_end', type=float, default=0.01)  # 最低探索系数
    parser.add_argument('--var_dec', type=float, default=1e-4)  # 探索系数衰减率
    parser.add_argument('--gamma', type=float, default=0.96)  # 折扣因子
    parser.add_argument('--gae_lambda', type=float, default=0.98)  # GAE调整方差与偏差的系数，即GAE折扣因子，0.96-0.99
    parser.add_argument('--epsilon', type=float, default=0.2)  # 对估计优势的函数进行裁剪
    parser.add_argument('--epoch', type=int, default=15)  # episode数据训练轮数
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
    # # [norm_cur_time,sensor_type, accuracy, return_size, arrival_time, TTL, duration, x, y, z]
    # dim_todo_mission = 10
    # [ x, y, z]
    dim_todo_mission = 3
    m_todo_missions = 6
    # # [x, y, z, energy]
    # dim_self_UAV = 4
    # [x, y, z]
    dim_self_UAV = 3
    # dim_observation = dim_neighbor_UAV * m_neighbor_UAVs + dim_trans_mission * m_trans_missions + dim_todo_mission * m_todo_missions + dim_self_UAV
    dim_observation = dim_todo_mission * m_todo_missions + dim_self_UAV

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
    parser.add_argument('--dim_action', type=int, default=9)  # Dimension of action
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
                                      '_mission_duration_sum * _mission_accuracy + log( _mission_deadline,2) * (1 / (0.2 + exp(-_mission_deadline / (_mission_finish_time - _mission_arrival_time))) - 1 / (0.2 + exp(-1)))')
        self.rewardScheduler.setModel(env, 'PUNISH', '- 5*_mission_duration_sum * _mission_accuracy')

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

        self.MAPPO_dim_args = parseMAPPODimArgs()
        self.MAPPO_train_args = parseMAPPOTrainArgs()
        self.MAPPO_env = MAPPO_Env(self.MAPPO_dim_args, self.MAPPO_train_args)
        if last_episode is not None and last_episode > 0:
            self.MAPPO_env.loadModel(last_episode,final)

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
        # [sensor_type, accuracy, return_size, arrival_time, TTL, duration, x, y, z, distance_threshold]
        # ['U',0.8,50,20,120,5,120.25,262.05,553.25,100]
        # 选取[sensor_type, accuracy, return_size, arrival_time, TTL, duration, x, y, z, distance_threshold]

        encode_states = []
        norm_cur_time = current_time/self.max_simulation_time
        for mission_state in mission_states:
            sensor_type = mission_state[0]
            accuracy = mission_state[1]
            return_size = mission_state[2] / self.max_mission_size
            arrival_time = mission_state[3] / self.max_simulation_time
            TTL = mission_state[4] / self.max_simulation_time
            duration = mission_state[5] / self.max_simulation_time
            position_x = (mission_state[6] - self.min_position_x) / (self.max_position_x - self.min_position_x)
            position_y = (mission_state[7] - self.min_position_y) / (self.max_position_y - self.min_position_y)
            position_z = (mission_state[8] - self.min_position_z) / (self.max_position_z - self.min_position_z)  if (self.max_position_z - self.min_position_z) > 0 else 0
            distance_threshold = mission_state[9] / (self.max_position_x - self.min_position_x)

            # state = [norm_cur_time,sensor_type, accuracy, return_size, arrival_time, TTL, duration, position_x, position_y,
            #          position_z]
            state = [position_x, position_y,position_z]
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

        position_x = (self_UAV_states[0] - self.min_position_x) / (self.max_position_x - self.min_position_x)
        position_y = (self_UAV_states[1] - self.min_position_y) / (self.max_position_y - self.min_position_y)
        position_z = (self_UAV_states[2] - self.min_position_z) / (self.max_position_z - self.min_position_z)  if (self.max_position_z - self.min_position_z) > 0 else 1
        energy = self_UAV_states[3] / self.max_energy
        # state = [position_x, position_y, position_z, energy]

        state = [position_x, position_y, position_z]

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
                                                                               self.MAPPO_dim_args.m_neighbor_UAVs)
            trans_mission_states = self.algorithmScheduler.getTransMissionStates(env, current_position,
                                                                                 observe_threshold,
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
                                                                     cur_time,
                                                                     self.MAPPO_dim_args.dim_todo_mission).flatten()
            encode_self_UAV_state = self._encode_self_UAV_states(self_UAV_state).flatten()
            # combined_state = np.concatenate((encode_neighbor_UAV_states, encode_trans_mission_states,
            #                                  encode_todo_mission_states, encode_self_UAV_state))
            combined_state = np.concatenate((encode_self_UAV_state,encode_todo_mission_states))

            global_UAV_states[UAV_index] = combined_state

        encode_global_states = self._encode_global_UAV_states(global_UAV_states,self.max_n_UAVs,self.MAPPO_dim_args.dim_observation)
        norm_actions = self.MAPPO_env.takeAction(encode_global_states) # Tensor

        for idx, norm_action in enumerate(norm_actions):
            action_idx=norm_action
            # 计算 xy 平面的方位角
            angle = self.action_angle[int(action_idx)]

            mobility_pattern = {}
            mobility_pattern['angle'] = angle
            mobility_pattern['phi'] = 0  # 强制只进行水平飞行
            UAV_speed_range = self.trafficScheduler.getConfig(env, 'UAV_speed_range')
            # action_idx为0表示悬停
            mobility_pattern['speed'] = random.uniform(UAV_speed_range[0], UAV_speed_range[1]) if action_idx>0 else 0

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
        distance_threshold = self.missionScheduler.getConfig(env, 'distance_threshold')
        if self.pp_buffer.size() == 0:
            return
        rewards=self._getUAVReward(env,distance_threshold)
        self.last_UAV_positions=env.getUAVLastPositions()

        exps={}
        all_states=[]
        all_next_states = []
        for idx in range(self.max_n_UAVs):
            UAV_id = self.trafficScheduler.completeStrId(env,idx, 'U')
            reward = float(rewards[UAV_id])
            exp = self.pp_buffer.completeAndPopExperience(UAV_id, reward)
            exps[idx] = exp

            state = np.array(exp["state"] )  # 提取 state 并转换为 numpy 数组
            all_states.append(state)
            next_state = np.array(exp["next_state"])
            all_next_states.append(next_state)

            self.algorithmScheduler.addUAVReward(env, UAV_id, reward)

        all_states = np.stack(all_states, axis=0)
        all_next_states = np.stack(all_next_states, axis=0)
        for idx,exp in exps.items():
            reward = np.array(exp["reward"])  # 提取 reward 并转换为 numpy 数组
            action = np.array(exp["action"] )  # 提取 action 并转换为 numpy 数组

            self.MAPPO_env.addExperience(idx,all_states,action, reward,all_next_states)

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
        # print(old_positions)
        # print(new_positions)


        # mission靠近奖励
        for UAV_id,missions in UAV_missions.items():
            # print(UAV_id)
            approach_rewards=0
            old_UAV_position_2d = old_positions.get(UAV_id, None)
            new_UAV_position_2d = new_positions.get(UAV_id, None)
            for mission in missions:
                # mission_TTL=mission._mission_deadline
                # mission_arrival_time=mission._mission_arrival_time
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
                approach_reward = 20 * delta_distance #if new_distance>distance_threshold or (old_distance < distance_threshold < new_distance) else 0
                approach_rewards += approach_reward
                break # 只取第一个任务

            # print('sum_approach_rewards: ', approach_rewards)
            rewards[UAV_id]+=approach_rewards



        for UAV_id in active_UAV_ids:
            trans_reward=0#10*UAV_trans_datas[UAV_id] # 辅助通信奖励
            sensing_reward=0#1000 * UAV_sensing_datas[UAV_id] # 感知奖励
            energy_punish= 0 #-2 * UAV_energy_consumptions[UAV_id] # 能耗惩罚
            rewards[UAV_id] +=trans_reward+sensing_reward+energy_punish
            # print(UAV_id)
            # print('trans_reward: ',trans_reward)
            # print('sensing_reward: ',sensing_reward)
            # print('energy_punish: ',energy_punish)

        # # 任务完成奖励
        # for mission_info in last_step_succ_mission_infos:
        #     node_id=mission_info["appointed_node_id"]
        #     node_type=env._getNodeTypeById(node_id)
        #     if node_type != 'U':
        #         continue
        #     reward = self.rewardScheduler.getRewardByMission(env, mission_info)
        #     rewards[node_id]+=reward
        #     # print(node_id)
        #     # print('mission_reward: ',reward)

        # 任务失败惩罚
        # for mission_info in last_step_fail_mission_infos:
        #     node_id=mission_info["appointed_node_id"]
        #     node_type=env._getNodeTypeById(node_id)
        #     if node_type != 'U':
        #         continue
        #     punish = self.rewardScheduler.getPunishByMission(env, mission_info)
        #     rewards[node_id]+=punish
            # print(node_id)
            # print('mission_punish: ',punish)
            # print()

        return rewards




