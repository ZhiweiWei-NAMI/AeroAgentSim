import json
import math

import os
import sys
from collections import Counter

import matplotlib.pyplot as plt
import numpy as np

from airfogsim.enum_const import MissionFinalStateEnum


class AirFogSimEvaluation:
    def __init__(self,env, algorithm_module):
        self.initOrResetStepIndicators()
        self.initOrResetStepRecords()
        self.initOrResetEpisodeRecords()
        # Path to save image
        self.base_path = f"./evaluation/" # 和main在同一文件夹下
        self.algorithm_module=algorithm_module
        self.tag = algorithm_module.getAlgorithmTag()
        self.env=env
        if not os.path.exists(self.base_path):
            os.makedirs(self.base_path)

    def initOrResetStepIndicators(self):
        # 1.仿真时间
        self.simulation_time = None
        self.step_num = 0

        # 2.过程监控
        self.to_generate_missions_num = 0
        self.executing_missions_num = 0
        self.success_missions_num = 0
        self.failed_missions_num = 0
        self.early_failed_missions_num = 0

        self.traffic_step_generate_num = 0
        self.traffic_step_allocate_num = 0
        self.traffic_step_success_num = 0
        self.traffic_step_fail_num = 0
        self.traffic_step_early_fail_num = 0

        self.finish_mission_num = 0

        self.finish_mission_num_on_vehicle = 0
        self.fail_mission_num_on_vehicle = 0
        self.success_mission_num_on_vehicle = 0
        self.completion_ratio_on_vehicle = 0

        self.finish_mission_num_on_UAV = 0
        self.fail_mission_num_on_UAV = 0
        self.success_mission_num_on_UAV = 0
        self.completion_ratio_on_UAV = 0

        self.fail_differ_type_mission_num_on_vehicle = [0 for _ in range(len(MissionFinalStateEnum))]
        self.fail_differ_type_mission_num_on_UAV = [0 for _ in range(len(MissionFinalStateEnum))]

        self.available_sensor_num_list=[] # available_sensor_num when a mission is allocated to node
        self.UAV_positions={}
        self.UAV_mission_positions={}

        # 3.信道速率指标
        self.avg_trans_rate = 0
        self.avg_V2U_trans_rate = 0
        self.avg_V2I_trans_rate = 0
        self.avg_U2I_trans_rate = 0
        self.V2U_data_trans_list = []
        self.V2I_data_trans_list = []
        self.U2I_data_trans_list = []
        self.V2U_link_using = []
        self.V2I_link_using = []
        self.U2I_link_using = []

        # 4.reward
        self.sum_reward = 0
        self.sum_success_reward = 0
        self.avg_reward = 0
        self.avg_success_reward = 0

        self.UAV_rewards={}

        # 5.ratio
        # 5.1 completion ratio
        self.completion_ratio = 0

        # 5.2 weighted acceleration ratio
        self.sum_weight = 0
        self.sum_weighted_acceleration_ratio = 0
        self.weighted_acceleration_ratio = 0

        # 5.3 acceleration ratio
        self.sum_acceleration_ratio = 0
        self.acceleration_ratio = 0

        # 6.optimization objectives
        self.sum_executing_time = 0
        self.sum_sensing_time = 0
        self.avg_floating_time = 0
        self.avg_executing_time = 0

        # move time before arrive PoI
        self.sum_move_time = 0
        self.avg_move_time = 0
        self.sum_move_time_on_UAV = 0
        self.avg_move_time_on_UAV = 0
        self.sum_move_time_on_vehicle = 0
        self.avg_move_time_on_vehicle = 0

        # tramsmission time after sensing
        self.sum_trans_time = 0
        self.avg_trans_time = 0
        self.sum_trans_time_on_UAV = 0
        self.avg_trans_time_on_UAV = 0
        self.sum_trans_time_on_vehicle = 0
        self.avg_trans_time_on_vehicle = 0

    def initOrResetStepRecords(self):
        self.step_num = 0
        # 2.过程监控
        self.step_to_generate_missions_num = []
        self.step_executing_missions_num = []
        self.step_success_missions_num = []
        self.step_failed_missions_num = []
        self.step_early_failed_missions_num = []

        self.step_generate_num = []
        self.step_allocate_num = []
        self.step_success_num = []
        self.step_fail_num = []
        self.step_early_fail_num = []

        self.step_sum_success_num_on_UAV = []
        self.step_sum_fail_num_on_UAV = []
        self.step_sum_success_num_on_vehicle = []
        self.step_sum_fail_num_on_vehicle = []

        self.step_fail_differ_type_mission_num_on_vehicle = [[] for _ in range(len(MissionFinalStateEnum))]
        self.step_fail_differ_type_mission_num_on_UAV = [[] for _ in range(len(MissionFinalStateEnum))]

        # 3.信道速率指标
        self.step_avg_trans_rate = []
        self.step_avg_V2U_trans_rate = []
        self.step_avg_V2I_trans_rate = []
        self.step_avg_U2I_trans_rate = []

        # 4.reward
        self.step_avg_reward = []
        self.step_avg_success_reward = []

        # 5.ratio
        # 5.1 completion ratio0
        self.step_completion_ratio = []
        self.step_completion_ratio_on_vehicle = []
        self.step_completion_ratio_on_UAV = []

        # 5.2 weighted acceleration ratio
        self.step_weighted_acceleration_ratio = []

        # 5.3 acceleration ratio
        self.step_acceleration_ratio = []

        # 6.optimization objectives
        self.step_avg_floating_time = []
        self.step_avg_executing_time = []

        self.step_avg_move_time = []
        self.step_avg_move_time_on_UAV = []
        self.step_avg_move_time_on_vehicle = []

        self.step_avg_trans_time = []
        self.step_avg_trans_time_on_UAV = []
        self.step_avg_trans_time_on_vehicle = []

    def initOrResetEpisodeRecords(self):
        self.episode_num = 0
        # 2.过程监控
        self.episode_to_generate_missions_num = []
        self.episode_executing_missions_num = []
        self.episode_success_missions_num = []
        self.episode_failed_missions_num = []
        self.episode_early_failed_missions_num = []

        self.episode_sum_success_num_on_UAV = []
        self.episode_sum_fail_num_on_UAV = []
        self.episode_sum_success_num_on_vehicle = []
        self.episode_sum_fail_num_on_vehile = []

        self.episode_sum_success_num_on_UAV = []
        self.episode_sum_fail_num_on_UAV = []
        self.episode_sum_success_num_on_vehicle = []
        self.episode_sum_fail_num_on_vehicle = []

        self.episode_fail_differ_type_mission_num_on_vehicle = [[] for _ in range(len(MissionFinalStateEnum))]
        self.episode_fail_differ_type_mission_num_on_UAV = [[] for _ in range(len(MissionFinalStateEnum))]

        self.episode_available_sensor_num = []

        # 3.信道速率指标
        self.episode_avg_trans_rate = []
        self.episode_avg_V2U_trans_rate = []
        self.episode_avg_V2I_trans_rate = []
        self.episode_avg_U2I_trans_rate = []

        # 4.reward
        self.episode_avg_reward = []
        self.episode_avg_success_reward = []

        # 5.ratio
        # 5.1 completion ratio
        self.episode_completion_ratio = []
        self.episode_completion_ratio_on_vehicle = []
        self.episode_completion_ratio_on_UAV = []

        # 5.2 weighted acceleration ratio
        self.episode_weighted_acceleration_ratio = []

        # 5.3 acceleration ratio
        self.episode_acceleration_ratio = []

        # 6.optimization objectives
        self.episode_avg_floating_time = []
        self.episode_avg_executing_time = []

        self.episode_avg_move_time = []
        self.episode_avg_move_time_on_UAV = []
        self.episode_avg_move_time_on_vehicle = []

        self.episode_avg_trans_time = []
        self.episode_avg_trans_time_on_UAV = []
        self.episode_avg_trans_time_on_vehicle = []

    def updateAndSaveStepRecords(self):
        self.updateEvaluationIndicators() # 更新step信息
        self.addToStepRecord() # step record添加一条记录

    def updateAndSaveEpisodeRecords(self, episode):
        self.addToEpisodeRecord()  # episode record 添加一条记录
        self.stepRecordToFile(episode) # 将本轮episode所有step记录保存为json（一组记录）
        self.episodeRecordToFile(episode) # 将本episode记录添加到file（一条记录）
        self.drawStepRecordsByFile(episode) # 可视化本episode记录（一组记录，所有step）
        self.initOrResetStepRecords() # 重新初始化记录一个episode每step情况的数组
        self.initOrResetStepIndicators() # 重新初始化记录step实时信息的指标

    def updateEvaluationIndicators(self):
        step_reward, step_punish, step_sum_reward = self.algorithm_module.getRewardByMission(
            self.env)  # success reward, fail punish, sum of reward and punish
        to_generate_missions_num = self.env.mission_manager.getToGenerateMissionNum()
        executing_missions_num = self.env.mission_manager.getExecutingMissionNum()
        success_missions_num = self.env.mission_manager.getSuccessMissionNum()
        failed_missions_num = self.env.mission_manager.getFailedMissionNum()
        early_failed_missions_num = self.env.mission_manager.getEarlyFailedMissionNum()
        sum_over_missions = success_missions_num + failed_missions_num + early_failed_missions_num

        # print("test")
        # print("to_generate_missions_num", to_generate_missions_num)
        # print("executing_missions_num", executing_missions_num)
        # print("success_missions_num", success_missions_num)
        # print("failed_missions_num", failed_missions_num)
        # print("early_failed_missions_num", early_failed_missions_num)
        # print("sum_over_missions", sum_over_missions)

        last_step_succ_mission_infos = self.algorithm_module.missionScheduler.getLastStepSuccMissionInfos(self.env)
        last_step_fail_mission_infos = self.algorithm_module.missionScheduler.getLastStepFailMissionInfos(self.env)
        last_step_early_fail_mission_infos = self.algorithm_module.missionScheduler.getLastStepEarlyFailMissionInfos(self.env)

        # print("last_step_succ_mission_infos", len(last_step_succ_mission_infos))
        # print("last_step_fail_mission_infos", len(last_step_fail_mission_infos))
        # print("last_step_early_fail_mission_infos", len(last_step_early_fail_mission_infos))

        # if failed_missions_num>0:
        #     raise False

        # 1.仿真时间
        self.simulation_time = self.env.simulation_time

        # 2.过程监控
        self.to_generate_missions_num = to_generate_missions_num
        self.executing_missions_num = executing_missions_num
        self.success_missions_num = success_missions_num
        self.early_failed_missions_num = early_failed_missions_num
        self.failed_missions_num = failed_missions_num

        self.finish_mission_num = sum_over_missions

        self.traffic_step_generate_num, self.traffic_step_allocate_num = self.env.getMissionEvaluationIndicators()
        self.traffic_step_success_num = len(last_step_succ_mission_infos)
        self.traffic_step_fail_num = len(last_step_fail_mission_infos)
        self.traffic_step_early_fail_num = len(last_step_early_fail_mission_infos)

        for mission_info in last_step_succ_mission_infos:
            appointed_node_id = mission_info['appointed_node_id']
            node_type = self.__getNodeTypeById(appointed_node_id)
            if node_type == 'V':
                self.finish_mission_num_on_vehicle += 1
                self.success_mission_num_on_vehicle += 1
            elif node_type == 'U':
                self.finish_mission_num_on_UAV += 1
                self.success_mission_num_on_UAV += 1
            else:
                raise TypeError('Node type is invalid')

        for mission_info in last_step_fail_mission_infos:
            appointed_node_id = mission_info['appointed_node_id']
            node_type = self.__getNodeTypeById(appointed_node_id)
            if node_type == 'V':
                self.finish_mission_num_on_vehicle += 1
                self.fail_mission_num_on_vehicle += 1
                self.fail_differ_type_mission_num_on_vehicle[mission_info['mission_final_state_code'].value] += 1
            elif node_type == 'U':
                self.finish_mission_num_on_UAV += 1
                self.fail_mission_num_on_UAV += 1
                self.fail_differ_type_mission_num_on_UAV[mission_info['mission_final_state_code'].value] += 1
            else:
                raise TypeError('Node type is invalid')

        self.available_sensor_num_list = self.env.getAvailableSensorNumList()
        self.UAV_positions=self.env.getUAVPositions()
        self.UAV_mission_positions=self.env.getUAVMissionPositions()

        # 3.信道速率指标
        self.avg_trans_rate = float(self.env.getChannelAvgRate())
        self.avg_V2U_trans_rate = float(self.env.getChannelAvgRate('V2U'))
        self.avg_V2I_trans_rate = float(self.env.getChannelAvgRate('V2I'))
        self.avg_U2I_trans_rate = float(self.env.getChannelAvgRate('U2I'))
        self.V2U_data_trans_list = self.env.getChannelTransDataHistory('V2U')
        self.V2I_data_trans_list = self.env.getChannelTransDataHistory('V2I')
        self.U2I_data_trans_list = self.env.getChannelTransDataHistory('U2I')
        self.V2U_link_using = self.env.getLinkUsingHistory('V2U')
        self.V2I_link_using = self.env.getLinkUsingHistory('V2I')
        self.U2I_link_using = self.env.getLinkUsingHistory('U2I')

        # 4.reward
        self.sum_reward += step_sum_reward
        self.sum_success_reward += step_reward
        self.avg_reward = float(self.sum_reward / self.finish_mission_num) if self.finish_mission_num != 0 else 0
        self.avg_success_reward = float(self.sum_success_reward / self.success_missions_num) if self.success_missions_num != 0 else 0

        self.UAV_rewards=self.env.getUAVRewards()

        # 5.ratio
        # 5.1 completion ratio (all)
        self.completion_ratio = self.env.mission_manager.getMissionCompletionRatio()[0]  # return: (ratio, mission_num)
        for mission_info in last_step_succ_mission_infos:
            TTL = mission_info['mission_deadline']
            duration = mission_info['mission_duration_sum']
            finish_time = mission_info['mission_finish_time']
            arrival_time = mission_info['mission_arrival_time']
            duration_original_sum=mission_info['mission_duration_original_sum']
            mission_duration_sum=mission_info['mission_duration_sum']

            # weight = math.log(1 + TTL - duration,10)
            weight=1
            # ratio = (TTL - duration) / (finish_time - arrival_time - duration)
            ratio=duration_original_sum/mission_duration_sum

            self.sum_weight += weight
            self.sum_weighted_acceleration_ratio += weight * ratio
            self.sum_acceleration_ratio += ratio

        self.weighted_acceleration_ratio = float(self.sum_weighted_acceleration_ratio / self.sum_weight) if self.sum_weight > 0.0 else 0
        self.acceleration_ratio = float(self.sum_acceleration_ratio / self.success_missions_num) if self.success_missions_num > 0 else 0

        # 5.2 completion ratio (Classified by type, not consider early fail)
        self.completion_ratio_on_vehicle = float(self.success_mission_num_on_vehicle / self.finish_mission_num_on_vehicle) if self.finish_mission_num_on_vehicle > 0 else 0
        self.completion_ratio_on_UAV = float(self.success_mission_num_on_UAV / self.finish_mission_num_on_UAV) if self.finish_mission_num_on_UAV > 0 else 0

        # optimization objectives
        for mission_info in last_step_succ_mission_infos:
            appointed_node_id = mission_info['appointed_node_id']
            node_type = self.__getNodeTypeById(appointed_node_id)
            duration = mission_info['mission_duration_sum']
            mission_finish_time = mission_info['mission_finish_time']
            mission_arrival_time = mission_info['mission_arrival_time']
            mission_start_time = mission_info['mission_start_time']
            sensing_finish_time = mission_info['sensing_finish_time']
            pre_move_time = sensing_finish_time-mission_start_time - duration
            transmission_time = mission_finish_time - sensing_finish_time
            self.sum_executing_time += mission_finish_time - mission_arrival_time
            self.sum_sensing_time += duration

            self.sum_move_time+=pre_move_time
            self.sum_trans_time += transmission_time
            if node_type == 'V':
                self.sum_move_time_on_vehicle+=pre_move_time
                self.sum_trans_time_on_vehicle+=transmission_time
            elif node_type == 'U':
                self.sum_move_time_on_UAV+=pre_move_time
                self.sum_trans_time_on_UAV+=transmission_time
            else:
                raise TypeError('Node type is invalid')

        self.avg_floating_time = float((self.sum_executing_time - self.sum_sensing_time) / self.success_missions_num) if self.success_missions_num > 0 else 0
        self.avg_executing_time = float(self.sum_executing_time / self.success_missions_num) if self.success_missions_num > 0 else 0

        self.avg_move_time=float(self.sum_move_time/self.success_missions_num) if self.success_missions_num > 0 else 0
        self.avg_move_time_on_vehicle=float(self.sum_move_time_on_vehicle/self.success_mission_num_on_vehicle) if self.success_mission_num_on_vehicle > 0 else 0
        self.avg_move_time_on_UAV=float(self.sum_move_time_on_UAV/self.success_mission_num_on_UAV) if self.success_mission_num_on_UAV > 0 else 0

        self.avg_trans_time=float(self.sum_trans_time/self.success_missions_num) if self.success_missions_num > 0 else 0
        self.avg_trans_time_on_vehicle=float(self.sum_trans_time_on_vehicle/self.success_mission_num_on_vehicle) if self.success_mission_num_on_vehicle > 0 else 0
        self.avg_trans_time_on_UAV=float(self.sum_trans_time_on_UAV/self.success_mission_num_on_UAV) if self.success_mission_num_on_UAV > 0 else 0

    def printEvaluation(self):
        # 仿真时间
        print('simulation_time: ', self.simulation_time)

        # 过程监控
        print('过程监控')
        print('to_generate_missions_num: ', self.to_generate_missions_num)
        print('executing_missions_num: ', self.executing_missions_num)
        print('success_missions_num: ', self.success_missions_num)
        print('failed_missions_num: ', self.failed_missions_num)
        print('early_failed_missions_num: ', self.early_failed_missions_num)

        print('traffic_step_generate_num: ', self.traffic_step_generate_num)
        print('traffic_step_allocate_num: ', self.traffic_step_allocate_num)
        print('traffic_step_success_num: ', self.traffic_step_success_num)
        print('traffic_step_fail_num: ', self.traffic_step_fail_num)
        print('traffic_step_early_fail_num: ', self.traffic_step_early_fail_num)

        print('finish_mission_num: ', self.finish_mission_num)

        print('finish_mission_num_on_vehicle: ', self.finish_mission_num_on_vehicle)
        print('success_mission_num_on_vehicle: ', self.success_mission_num_on_vehicle)
        print('fail_mission_num_on_vehicle: ', self.fail_mission_num_on_vehicle)
        print('finish_mission_num_on_UAV: ', self.finish_mission_num_on_UAV)
        print('success_mission_num_on_UAV: ', self.success_mission_num_on_UAV)
        print('fail_mission_num_on_UAV: ', self.fail_mission_num_on_UAV)

        print('fail_differ_type_mission_num_on_vehicle: ', self.fail_differ_type_mission_num_on_vehicle)
        print('fail_differ_type_mission_num_on_UAV: ', self.fail_differ_type_mission_num_on_UAV)

        print('available_sensor_num_list: ', self.available_sensor_num_list)

        # 信道速率指标
        print('信道速率')
        print('avg_trans_rate: ', self.avg_trans_rate)
        print('avg_V2U_trans_rate: ', self.avg_V2U_trans_rate)
        print('avg_V2I_trans_rate: ', self.avg_V2I_trans_rate)
        print('avg_U2I_trans_rate: ', self.avg_U2I_trans_rate)

        # reward
        print('奖励')
        print('avg_reward: ', self.avg_reward)
        print('avg_success_reward: ', self.avg_success_reward)

        # ratio
        print('比率')

        print('completion_ratio: ', self.completion_ratio)
        print('completion_ratio_on_vehicle: ', self.completion_ratio_on_vehicle)
        print('completion_ratio_on_UAV: ', self.completion_ratio_on_UAV)

        print('sum_weighted_acceleration_ratio: ', self.sum_weighted_acceleration_ratio)
        print('weighted_acceleration_ratio: ', self.weighted_acceleration_ratio)

        print('sum_acceleration_ratio: ', self.sum_acceleration_ratio)
        print('acceleration_ratio: ', self.acceleration_ratio)

        # optimization objectives
        print('优化目标')
        print('avg_floating_time: ', self.avg_floating_time)
        print('avg_executing_time: ', self.avg_executing_time)

        print('avg_move_time: ', self.avg_move_time)
        print('avg_move_time_on_vehicle: ', self.avg_move_time_on_vehicle)
        print('avg_move_time_on_UAV: ', self.avg_move_time_on_UAV)
        print('avg_trans_time: ', self.avg_trans_time)
        print('avg_trans_time_on_vehicle: ', self.avg_trans_time_on_vehicle)
        print('avg_trans_time_on_UAV: ', self.avg_trans_time_on_UAV)

        print('\n')

    def addToStepRecord(self):
        self.step_num += 1
        # 过程监控
        self.step_to_generate_missions_num.append(self.to_generate_missions_num)
        self.step_executing_missions_num.append(self.executing_missions_num)
        self.step_success_missions_num.append(self.success_missions_num)
        self.step_failed_missions_num.append(self.failed_missions_num)
        self.step_early_failed_missions_num.append(self.early_failed_missions_num)

        self.step_generate_num.append(self.traffic_step_generate_num)
        self.step_allocate_num.append(self.traffic_step_allocate_num)
        self.step_success_num.append(self.traffic_step_success_num)
        self.step_fail_num.append(self.traffic_step_fail_num)
        self.step_early_fail_num.append(self.traffic_step_early_fail_num)

        self.step_sum_success_num_on_UAV.append(self.success_mission_num_on_UAV)
        self.step_sum_fail_num_on_UAV.append(self.fail_mission_num_on_UAV)
        self.step_sum_success_num_on_vehicle.append(self.success_mission_num_on_vehicle)
        self.step_sum_fail_num_on_vehicle.append(self.fail_mission_num_on_vehicle)

        for step_list, fail_value in zip(self.step_fail_differ_type_mission_num_on_UAV,self.fail_differ_type_mission_num_on_UAV):
            step_list.append(fail_value)
        for step_list, fail_value in zip(self.step_fail_differ_type_mission_num_on_vehicle,self.fail_differ_type_mission_num_on_vehicle):
            step_list.append(fail_value)

        # 信道速率指标
        self.step_avg_trans_rate.append(self.avg_trans_rate)
        self.step_avg_V2U_trans_rate.append(self.avg_V2U_trans_rate)
        self.step_avg_V2I_trans_rate.append(self.avg_V2I_trans_rate)
        self.step_avg_U2I_trans_rate.append(self.avg_U2I_trans_rate)

        # 4.reward
        self.step_avg_reward.append(self.avg_reward)
        self.step_avg_success_reward.append(self.avg_success_reward)

        # 5.ratio
        self.step_completion_ratio.append(self.completion_ratio)
        self.step_completion_ratio_on_vehicle.append(self.completion_ratio_on_vehicle)
        self.step_completion_ratio_on_UAV.append(self.completion_ratio_on_UAV)

        self.step_weighted_acceleration_ratio.append(self.weighted_acceleration_ratio)
        self.step_acceleration_ratio.append(self.acceleration_ratio)

        # 6.optimization objectives
        self.step_avg_floating_time.append(self.avg_floating_time)
        self.step_avg_executing_time.append(self.avg_executing_time)

        self.step_avg_move_time.append(self.avg_move_time)
        self.step_avg_move_time_on_vehicle.append(self.avg_move_time_on_vehicle)
        self.step_avg_move_time_on_UAV.append(self.avg_move_time_on_UAV)

        self.step_avg_trans_time.append(self.avg_trans_time)
        self.step_avg_trans_time_on_vehicle.append(self.avg_trans_time_on_vehicle)
        self.step_avg_trans_time_on_UAV.append(self.avg_trans_time_on_UAV)



    def drawAndResetStepRecord(self, episode):
        path_to_save = self.base_path + f'episode/episode_{episode}/'
        if not os.path.exists(path_to_save):
            os.makedirs(path_to_save)

        # 1.过程监控
        # 累计任务
        x_indices = list(range(1, self.step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, self.step_to_generate_missions_num, label='To generate', color='gray')
        plt.plot(x_indices, self.step_executing_missions_num, label='Executing', color='deepskyblue')
        plt.plot(x_indices, self.step_success_missions_num, label='Success', color='limegreen')
        plt.plot(x_indices, self.step_failed_missions_num, label='Fail', color='red')
        plt.plot(x_indices, self.step_early_failed_missions_num, label='Not Allocate', color='orange')
        plt.title('Accumulate Process Monitoring')
        plt.xlabel('Step')
        plt.ylabel('The Number of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'AccumulateProcessMonitoring.png')
        plt.close()

        # 时隙任务
        x_indices = list(range(1, self.step_num + 1))
        plt.figure(clear=True)
        fig, axes = plt.subplots(2, 3, figsize=(12, 8))  # 两行三列
        # 子图1: Generate
        axes[0, 0].plot(x_indices, self.step_generate_num, label='Generate', color='gray')
        axes[0, 0].set_title('Generate')
        axes[0, 0].set_ylabel('The Number')
        axes[0, 0].grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        axes[0, 0].legend()
        # 子图2: Allocate
        axes[0, 1].plot(x_indices, self.step_allocate_num, label='Allocate', color='deepskyblue')
        axes[0, 1].set_title('Allocate')
        axes[0, 1].grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        axes[0, 1].legend()
        # 子图3: Success
        axes[0, 2].plot(x_indices, self.step_success_num, label='Success', color='limegreen')
        axes[0, 2].set_title('Success')
        axes[0, 2].grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        axes[0, 2].legend()
        # 子图4: Fail
        axes[1, 0].plot(x_indices, self.step_fail_num, label='Fail', color='red')
        axes[1, 0].set_title('Fail')
        axes[1, 0].set_ylabel('The Number')
        axes[1, 0].set_xlabel('Step')
        axes[1, 0].grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        axes[1, 0].legend()
        # 子图5: Not Allocate
        axes[1, 1].plot(x_indices, self.step_early_fail_num, label='Not Allocate', color='orange')
        axes[1, 1].set_title('Not Allocate')
        axes[1, 1].set_xlabel('Step')
        axes[1, 1].grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        axes[1, 1].legend()
        # 子图6: 留空或补充说明
        axes[1, 2].axis('off')  # 禁用第六个子图
        # axes[1, 2].text(0.5, 0.5, "Additional Info", ha='center', va='center', fontsize=12, color='gray')
        # 设置全局标题
        fig.suptitle('Timeslot Process Monitoring', fontsize=16)
        # 保存图像
        plt.savefig(path_to_save + 'TimeslotProcessMonitoring.png')
        plt.close()

        # 分类累计任务-成功
        x_indices = list(range(1, self.step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, self.step_success_missions_num, label='Sum', color='limegreen')
        plt.plot(x_indices, self.step_sum_success_num_on_vehicle, label='Veh', color='orange')
        plt.plot(x_indices, self.step_sum_success_num_on_UAV, label='UAV', color='deepskyblue')
        plt.title('Accumulate Process Monitoring (Success)')
        plt.xlabel('Step')
        plt.ylabel('The Number of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'AccumulateProcessMonitoring_Success.png')
        plt.close()

        # 分类累计任务-失败
        x_indices = list(range(1, self.step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, self.step_failed_missions_num, label='Sum', color='red')
        plt.plot(x_indices, self.step_sum_fail_num_on_vehicle, label='Veh', color='orange')
        plt.plot(x_indices, self.step_sum_fail_num_on_UAV, label='UAV', color='deepskyblue')
        plt.title('Accumulate Process Monitoring (Fail)')
        plt.xlabel('Step')
        plt.ylabel('The Number of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'AccumulateProcessMonitoring_Fail.png')
        plt.close()

        # 失败类型分类统计-UAV
        x_indices = list(range(1, self.step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, self.step_fail_differ_type_mission_num_on_UAV[MissionFinalStateEnum.FORCE_FAIL.value],label='Force', color='red')
        plt.plot(x_indices, self.step_fail_differ_type_mission_num_on_UAV[MissionFinalStateEnum.SENSING_FAIL.value],label='Sense', color='deepskyblue')
        plt.plot(x_indices, self.step_fail_differ_type_mission_num_on_UAV[MissionFinalStateEnum.TRANSMISSION_FAIL.value],label='Trans', color='orange')
        plt.title('Fail on Different Stage UAV')
        plt.xlabel('Step')
        plt.ylabel('The Number of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'FailOnDifferentStage_UAV.png')
        plt.close()

        # 失败类型分类统计-Veh
        x_indices = list(range(1, self.step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, self.step_fail_differ_type_mission_num_on_vehicle[MissionFinalStateEnum.FORCE_FAIL.value],label='Force', color='red')
        plt.plot(x_indices, self.step_fail_differ_type_mission_num_on_vehicle[MissionFinalStateEnum.SENSING_FAIL.value],label='Sense', color='deepskyblue')
        plt.plot(x_indices, self.step_fail_differ_type_mission_num_on_vehicle[MissionFinalStateEnum.TRANSMISSION_FAIL.value],label='Trans', color='orange')
        plt.title('Fail on Different Stage Vehicle')
        plt.xlabel('Step')
        plt.ylabel('The Number of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'FailOnDifferentStage_Veh.png')
        plt.close()

        # mission分配时可用sensor数量
        count_data = Counter(self.available_sensor_num_list)
        # 获取数字和它们的出现次数
        numbers = list(count_data.keys())  # 数字
        counts = list(count_data.values())  # 每个数字出现的次数
        # 创建条形图
        plt.figure(clear=True)
        plt.bar(numbers, counts)
        plt.title('Available Sensor Num')
        plt.xlabel('Num')
        plt.ylabel('Frequency')
        plt.grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        plt.savefig(path_to_save + 'AvailableSensorNum.png')
        plt.close()

        # UAV轨迹与mission位置
        min_x = self.env.config['traffic']['x_range'][0]
        max_x = self.env.config['traffic']['x_range'][1]
        min_y = self.env.config['traffic']['y_range'][0]
        max_y = self.env.config['traffic']['y_range'][1]
        for i, (UAV_id, positions) in enumerate(self.UAV_positions.items()):
            plt.figure(clear=True)
            plt.xlim(min_x, max_x)
            plt.ylim(min_y, max_y)
            plt.gca().invert_yaxis()
            row = int(i / 5)
            col = int(i % 5)
            UAV_positions_x = [position[0] for position in positions]
            UAV_positions_y = [position[1] for position in positions]
            mission_positions = self.UAV_mission_positions[UAV_id]
            UAV_mission_positions_x = [position[0] for position in mission_positions]
            UAV_mission_positions_y = [position[1] for position in mission_positions]
            # 画出轨迹
            plt.plot(UAV_positions_x, UAV_positions_y, color='blue', linestyle='-', linewidth=0.2, zorder=1,
                     label='Trajectory')
            # 画出轨迹的各个点：
            # # 中间的点为蓝色（如果有）
            # if len(UAV_positions_x) > 2:
            #     plt.scatter(UAV_positions_x[1:-1], UAV_positions_y[1:-1], color='blue', s=2, zorder=2,
            #                 label='Trajectory Intermediate')

            alphas = np.linspace(0.5, 1, len(mission_positions), dtype=float)  # 从 0.1（更透明）到 1（更不透明）
            # 画mission位置，设置 zorder 高于轨迹，保证不会被覆盖
            for i in range(len(UAV_mission_positions_x)):
                plt.scatter(UAV_mission_positions_x[i], UAV_mission_positions_y[i],
                            color='orange', s=100, alpha=float(alphas[i]), zorder=3)
            # 第一个点为绿色
            plt.scatter(UAV_positions_x[0], UAV_positions_y[0], color='limegreen', s=20, zorder=4, label='Trajectory Start')
            # 最后一个点为红色
            plt.scatter(UAV_positions_x[-1], UAV_positions_y[-1], color='red', s=20, zorder=4, label='Trajectory End')

            # 设置横纵轴等
            plt.xlabel('X')
            plt.ylabel('Y')
            plt.grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
            # 设置全局标题
            plt.title(f'Trajectory and Mission Points On {UAV_id}')
            # 显示图例
            plt.legend()
            # 保存图像
            plt.savefig(path_to_save + f'TrajectoryAndMissionPoints_{UAV_id}.png')
            plt.close()


        # 2.信道状态
        # 信道速率指标
        x_indices = list(range(1, self.step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, self.step_avg_trans_rate, label='Avg', color='deepskyblue')
        plt.plot(x_indices, self.step_avg_V2U_trans_rate, label='V2U', color='limegreen')
        plt.plot(x_indices, self.step_avg_V2I_trans_rate, label='V2I', color='red')
        plt.plot(x_indices, self.step_avg_U2I_trans_rate, label='U2I', color='orange')
        plt.title('Channel Rate')
        plt.xlabel('Step')
        plt.ylabel('The Average Rate of Channel')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'ChannelRate.png')
        plt.close()

        # 信道使用情况
        x_indices = list(range(1, self.step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, self.V2U_link_using, label='V2U', color='limegreen')
        plt.plot(x_indices, self.V2I_link_using, label='V2I', color='red')
        plt.plot(x_indices, self.U2I_link_using, label='U2I', color='orange')
        plt.title('Link Using')
        plt.xlabel('SimulationStep')
        plt.ylabel('Link Num in Use')
        plt.grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'LinkUsing.png')
        plt.close()

        # 数据传输
        # V2U
        x_indices = list(range(1, len(self.V2U_data_trans_list) + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, self.V2U_data_trans_list, color='orange')
        plt.title('V2U Data Trans')
        plt.xlabel('Trans Step')
        plt.ylabel('Data Size')
        plt.grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        plt.savefig(path_to_save + 'V2U_data_trans.png')
        plt.close()
        # V2I
        x_indices = list(range(1, len(self.V2I_data_trans_list) + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, self.V2I_data_trans_list, color='orange')
        plt.title('V2I Data Trans')
        plt.xlabel('Trans Step')
        plt.ylabel('Data Size')
        plt.grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        plt.savefig(path_to_save + 'V2I_data_trans.png')
        plt.close()
        # U2I
        x_indices = list(range(1, len(self.U2I_data_trans_list) + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, self.U2I_data_trans_list, color='orange')
        plt.title('U2I Data Trans')
        plt.xlabel('Trans Step')
        plt.ylabel('Data Size')
        plt.grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        plt.savefig(path_to_save + 'U2I_data_trans.png')
        plt.close()

        # 3.奖励
        x_indices = list(range(1, self.step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, self.step_avg_reward, label='Avg', color='orange')
        plt.plot(x_indices, self.step_avg_success_reward, label='Suc', color='limegreen')
        plt.title('Reward')
        plt.xlabel('Step')
        plt.ylabel('The Average Reward of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'Reward.png')
        plt.close()

        # 4.完成率/加速率
        # completion ratio
        x_indices = list(range(1, self.step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, self.step_completion_ratio, label='Sum', color='limegreen')
        plt.plot(x_indices, self.step_completion_ratio_on_vehicle, label='Vehicle', color='orange')
        plt.plot(x_indices, self.step_completion_ratio_on_UAV, label='UAV', color='deepskyblue')
        plt.title('Completion Ratio')
        plt.xlabel('Step')
        plt.ylabel('The Completion Ratio of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'CompletionRatio.png')
        plt.close()

        # acceleration ratio
        x_indices = list(range(1, self.step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, self.step_weighted_acceleration_ratio, label='Weighted', color='orange')
        plt.plot(x_indices, self.step_acceleration_ratio, label='Not Weighted', color='limegreen')
        plt.title('Acceleration Ratio')
        plt.xlabel('Step')
        plt.ylabel('The Acceleration Ratio of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'AccelerationRatio.png')
        plt.close()

        # 5.时间优化指标
        x_indices = list(range(1, self.step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, self.step_avg_floating_time, label='Floating', color='orange')
        plt.plot(x_indices, self.step_avg_executing_time, label='Executing', color='limegreen')
        plt.title('Time')
        plt.xlabel('Step')
        plt.ylabel('The Related Time of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'Time.png')
        plt.close()

        # 移动时间
        x_indices = list(range(1, self.step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, self.step_avg_move_time, label='Sum', color='limegreen')
        plt.plot(x_indices, self.step_avg_move_time_on_vehicle, label='Veh', color='orange')
        plt.plot(x_indices, self.step_avg_move_time_on_UAV, label='UAV', color='deepskyblue')
        plt.title('Pre Move Time')
        plt.xlabel('Step')
        plt.ylabel('Time(s)')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'PreMoveTime.png')
        plt.close()

        # 传输时间
        x_indices = list(range(1, self.step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, self.step_avg_trans_time, label='Sum', color='limegreen')
        plt.plot(x_indices, self.step_avg_trans_time_on_vehicle, label='Veh', color='orange')
        plt.plot(x_indices, self.step_avg_trans_time_on_UAV, label='UAV', color='deepskyblue')
        plt.title('Transmission Time')
        plt.xlabel('Step')
        plt.ylabel('Time(s)')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'TransmissionTime.png')
        plt.close()

        self.initOrResetStepIndicators()
        self.initOrResetStepRecords()

    def addToEpisodeRecord(self):
        self.episode_num += 1
        # 过程监控
        self.episode_to_generate_missions_num.append(self.to_generate_missions_num)
        self.episode_executing_missions_num.append(self.executing_missions_num)
        self.episode_success_missions_num.append(self.success_missions_num)
        self.episode_failed_missions_num.append(self.failed_missions_num)
        self.episode_early_failed_missions_num.append(self.early_failed_missions_num)

        self.episode_sum_success_num_on_vehicle.append(self.success_mission_num_on_vehicle)
        self.episode_sum_fail_num_on_vehicle.append(self.fail_mission_num_on_vehicle)
        self.episode_sum_success_num_on_UAV.append(self.success_mission_num_on_UAV)
        self.episode_sum_fail_num_on_UAV.append(self.fail_mission_num_on_UAV)

        for step_list, fail_value in zip(self.episode_fail_differ_type_mission_num_on_UAV,self.fail_differ_type_mission_num_on_UAV):
            step_list.append(fail_value)
        for step_list, fail_value in zip(self.episode_fail_differ_type_mission_num_on_vehicle,self.fail_differ_type_mission_num_on_vehicle):
            step_list.append(fail_value)

        self.episode_available_sensor_num.append(sum(self.available_sensor_num_list)/len(self.available_sensor_num_list) if len(self.available_sensor_num_list) > 0 else 0)

        # 信道速率指标
        self.episode_avg_trans_rate.append(self.avg_trans_rate)
        self.episode_avg_V2U_trans_rate.append(self.avg_V2U_trans_rate)
        self.episode_avg_V2I_trans_rate.append(self.avg_V2I_trans_rate)
        self.episode_avg_U2I_trans_rate.append(self.avg_U2I_trans_rate)

        # reward
        self.episode_avg_reward.append(self.avg_reward)
        self.episode_avg_success_reward.append(self.avg_success_reward)

        # ratio
        self.episode_completion_ratio.append(self.completion_ratio)
        self.episode_completion_ratio_on_vehicle.append(self.completion_ratio_on_vehicle)
        self.episode_completion_ratio_on_UAV.append(self.completion_ratio_on_UAV)

        self.episode_weighted_acceleration_ratio.append(self.weighted_acceleration_ratio)
        self.episode_acceleration_ratio.append(self.acceleration_ratio)

        # optimization objectives
        self.episode_avg_floating_time.append(self.avg_floating_time)
        self.episode_avg_executing_time.append(self.avg_executing_time)

        self.episode_avg_move_time.append(self.avg_move_time)
        self.episode_avg_move_time_on_vehicle.append(self.avg_move_time_on_vehicle)
        self.episode_avg_move_time_on_UAV.append(self.avg_move_time_on_UAV)

        self.episode_avg_trans_time.append(self.avg_trans_time)
        self.episode_avg_trans_time_on_vehicle.append(self.avg_trans_time_on_vehicle)
        self.episode_avg_trans_time_on_UAV.append(self.avg_trans_time_on_UAV)

    def drawAndResetEpisodeRecord(self):
        path_to_save = self.base_path + 'final/'
        if not os.path.exists(path_to_save):
            os.makedirs(path_to_save)

        # 横轴为步数
        x_indices = list(range(1, self.episode_num + 1))

        # 1.过程监控
        plt.figure(clear=True)
        plt.plot(x_indices, self.episode_to_generate_missions_num, label='To generate', color='gray')
        plt.plot(x_indices, self.episode_executing_missions_num, label='Executing', color='deepskyblue')
        plt.plot(x_indices, self.episode_success_missions_num, label='Success', color='limegreen')
        plt.plot(x_indices, self.episode_failed_missions_num, label='Fail', color='red')
        plt.plot(x_indices, self.episode_early_failed_missions_num, label='Not Allocate', color='orange')
        plt.title('Process Monitoring')
        plt.xlabel('Episode')
        plt.ylabel('The Number of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'ProcessMonitoring.png')
        plt.close()

        # 分类累计任务-成功
        plt.figure(clear=True)
        plt.plot(x_indices, self.episode_success_missions_num, label='Sum', color='limegreen')
        plt.plot(x_indices, self.episode_sum_success_num_on_vehicle, label='Veh', color='orange')
        plt.plot(x_indices, self.episode_sum_success_num_on_UAV, label='UAV', color='deepskyblue')
        plt.title('Accumulate Process Monitoring (Success)')
        plt.xlabel('Episode')
        plt.ylabel('The Number of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'AccumulateProcessMonitoring_Success.png')
        plt.close()

        # 分类累计任务-失败
        plt.figure(clear=True)
        plt.plot(x_indices, self.episode_failed_missions_num, label='Sum', color='red')
        plt.plot(x_indices, self.episode_sum_fail_num_on_vehicle, label='Veh', color='orange')
        plt.plot(x_indices, self.episode_sum_fail_num_on_UAV, label='UAV', color='deepskyblue')
        plt.title('Accumulate Process Monitoring (Fail)')
        plt.xlabel('Episode')
        plt.ylabel('The Number of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'AccumulateProcessMonitoring_Fail.png')
        plt.close()

        # 失败类型分类统计-UAV
        plt.figure(clear=True)
        plt.plot(x_indices, self.episode_fail_differ_type_mission_num_on_UAV[MissionFinalStateEnum.FORCE_FAIL.value],label='Force',
                 color='red')
        plt.plot(x_indices, self.episode_fail_differ_type_mission_num_on_UAV[MissionFinalStateEnum.SENSING_FAIL.value],label='Sense',
                 color='deepskyblue')
        plt.plot(x_indices, self.episode_fail_differ_type_mission_num_on_UAV[MissionFinalStateEnum.TRANSMISSION_FAIL.value],label='Trans',
                 color='orange')
        plt.title('Fail on Different Stage UAV')
        plt.xlabel('Step')
        plt.ylabel('The Number of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'Fail_On_Different_Stage_UAV.png')
        plt.close()

        # 失败类型分类统计-Veh
        plt.figure(clear=True)
        plt.plot(x_indices, self.episode_fail_differ_type_mission_num_on_vehicle[MissionFinalStateEnum.FORCE_FAIL.value],label='Force', color='red')
        plt.plot(x_indices, self.episode_fail_differ_type_mission_num_on_vehicle[MissionFinalStateEnum.SENSING_FAIL.value],label='Sense', color='deepskyblue')
        plt.plot(x_indices, self.episode_fail_differ_type_mission_num_on_vehicle[MissionFinalStateEnum.TRANSMISSION_FAIL.value],label='Trans',
                 color='orange')
        plt.title('Fail on Different Stage Vehicle')
        plt.xlabel('Step')
        plt.ylabel('The Number of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'Fail_On_Different_Stage_Veh.png')
        plt.close()

        # mission分配时可用sensor数量
        plt.figure(clear=True)
        plt.plot(x_indices, self.episode_available_sensor_num, color='green')
        plt.title('Available Sensor Num')
        plt.xlabel('Step')
        plt.ylabel('The Number of Sensors')
        plt.grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        plt.savefig(path_to_save + 'Available_Sensor_Num.png')
        plt.close()

        # 2.信道速率指标
        plt.figure(clear=True)
        plt.plot(x_indices, self.episode_avg_trans_rate, label='Avg', color='deepskyblue')
        plt.plot(x_indices, self.episode_avg_V2U_trans_rate, label='V2U', color='limegreen')
        plt.plot(x_indices, self.episode_avg_V2I_trans_rate, label='V2I', color='red')
        plt.plot(x_indices, self.episode_avg_U2I_trans_rate, label='U2I', color='orange')
        plt.title('Channel Rate')
        plt.xlabel('Episode')
        plt.ylabel('The Average Rate of Channel')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'ChannelRate.png')
        plt.close()

        # 3.奖励
        plt.figure(clear=True)
        plt.plot(x_indices, self.episode_avg_reward, label='Avg', color='orange')
        plt.plot(x_indices, self.episode_avg_success_reward, label='Suc', color='limegreen')
        plt.title('Reward')
        plt.xlabel('Episode')
        plt.ylabel('The Average Reward of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'Reward.png')
        plt.close()

        # 4.完成率/加速比
        # completion ratio
        plt.figure(clear=True)
        plt.plot(x_indices, self.episode_completion_ratio, label='Sum', color='limegreen')
        plt.plot(x_indices, self.episode_completion_ratio_on_vehicle, label='Vehicle', color='orange')
        plt.plot(x_indices, self.episode_completion_ratio_on_UAV, label='UAV', color='deepskyblue')
        plt.title('Completion Ratio')
        plt.xlabel('Episode')
        plt.ylabel('The Completion Ratio of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'CompletionRatio.png')
        plt.close()

        # acceleration ratio
        plt.figure(clear=True)
        plt.plot(x_indices, self.episode_weighted_acceleration_ratio, label='Weighted Acc', color='orange')
        plt.plot(x_indices, self.episode_acceleration_ratio, label='Not Weighted Acc', color='limegreen')
        plt.title('Acceleration Ratio')
        plt.xlabel('Episode')
        plt.ylabel('The Acceleration Ratio of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'AccelerationRatio.png')
        plt.close()

        # 5.时间优化指标
        plt.figure(clear=True)
        plt.plot(x_indices, self.episode_avg_floating_time, label='Floating', color='orange')
        plt.plot(x_indices, self.episode_avg_executing_time, label='Executing', color='limegreen')
        plt.title('Time')
        plt.xlabel('Episode')
        plt.ylabel('The Related Time of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'Time.png')
        plt.close()

        # 移动时间
        x_indices = list(range(1, self.step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, self.episode_avg_move_time, label='Sum', color='limegreen')
        plt.plot(x_indices, self.episode_avg_move_time_on_vehicle, label='Veh', color='orange')
        plt.plot(x_indices, self.episode_avg_move_time_on_UAV, label='UAV', color='deepskyblue')
        plt.title('Pre Move Time')
        plt.xlabel('Episode')
        plt.ylabel('Time(s)')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'PreMoveTime.png')
        plt.close()

        # 传输时间
        x_indices = list(range(1, self.step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, self.episode_avg_trans_time, label='Sum', color='limegreen')
        plt.plot(x_indices, self.episode_avg_trans_time_on_vehicle, label='Veh', color='orange')
        plt.plot(x_indices, self.episode_avg_trans_time_on_UAV, label='UAV', color='deepskyblue')
        plt.title('Transmission Time')
        plt.xlabel('Episode')
        plt.ylabel('Time(s)')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'TransmissionTime.png')
        plt.close()

        self.initOrResetStepIndicators()
        self.initOrResetStepRecords()
        self.initOrResetEpisodeRecords()



    def stepRecordToFile(self, episode):
        folder_dir=self.base_path + f"episode/episode_{episode}/"
        if not os.path.exists(folder_dir):
            os.makedirs(folder_dir)
        file_dir=folder_dir+f"evaluation.json"

        data = {
            "step_num": self.step_num,

            "step_to_generate_missions_num": self.step_to_generate_missions_num,
            "step_executing_missions_num": self.step_executing_missions_num,
            "step_success_missions_num": self.step_success_missions_num,
            "step_failed_missions_num": self.step_failed_missions_num,
            "step_early_failed_missions_num": self.step_early_failed_missions_num,

            "step_generate_num": self.step_generate_num,
            "step_allocate_num": self.step_allocate_num,
            "step_success_num": self.step_success_num,
            "step_fail_num": self.step_fail_num,
            "step_early_fail_num": self.step_early_fail_num,

            "step_sum_success_num_on_vehicle": self.step_sum_success_num_on_vehicle,
            "step_sum_fail_num_on_vehicle": self.step_sum_fail_num_on_vehicle,
            "step_sum_success_num_on_UAV": self.step_sum_success_num_on_UAV,
            "step_sum_fail_num_on_UAV": self.step_sum_fail_num_on_UAV,

            "step_fail_differ_type_mission_num_on_vehicle":self.step_fail_differ_type_mission_num_on_vehicle,
            "step_fail_differ_type_mission_num_on_UAV": self.step_fail_differ_type_mission_num_on_UAV,

            "step_avg_trans_rate": self.step_avg_trans_rate,
            "step_avg_V2U_trans_rate": self.step_avg_V2U_trans_rate,
            "step_avg_V2I_trans_rate": self.step_avg_V2I_trans_rate,
            "step_avg_U2I_trans_rate": self.step_avg_U2I_trans_rate,

            "available_sensor_num_list": [float(x) for x in self.available_sensor_num_list],
            "UAV_positions" :self.UAV_positions,
            "UAV_mission_positions" : self.UAV_mission_positions,

            "V2U_link_using": [float(x) for x in self.V2U_link_using],
            "V2I_link_using": [float(x) for x in self.V2I_link_using],
            "U2I_link_using": [float(x) for x in self.U2I_link_using],

            "V2U_data_trans_list": [float(x) for x in self.V2U_data_trans_list],
            "V2I_data_trans_list": [float(x) for x in self.V2I_data_trans_list],
            "U2I_data_trans_list": [float(x) for x in self.U2I_data_trans_list],

            "step_avg_reward": self.step_avg_reward,
            "step_avg_success_reward": self.step_avg_success_reward,

            "step_completion_ratio": self.step_completion_ratio,
            "step_completion_ratio_on_vehicle": self.step_completion_ratio_on_vehicle,
            "step_completion_ratio_on_UAV": self.step_completion_ratio_on_UAV,

            "step_weighted_acceleration_ratio": self.step_weighted_acceleration_ratio,
            "step_acceleration_ratio": self.step_acceleration_ratio,

            "step_avg_floating_time": self.step_avg_floating_time,
            "step_avg_executing_time": self.step_avg_executing_time,

            "step_avg_move_time": self.step_avg_move_time,
            "step_avg_move_time_on_vehicle": self.step_avg_move_time_on_vehicle,
            "step_avg_move_time_on_UAV": self.step_avg_move_time_on_UAV,

            "step_avg_trans_time": self.step_avg_trans_time,
            "step_avg_trans_time_on_vehicle": self.step_avg_trans_time_on_vehicle,
            "step_avg_trans_time_on_UAV": self.step_avg_trans_time_on_UAV,
        }

        # 将数据写回文件
        with open(file_dir, "w") as file:
            json.dump(data, file, indent=4)  # json格式缩进4个空格

        print(f"save {self.tag} episode_{episode} step records successfully")

    def drawStepRecordsByFile(self, episode):
        # json文件路径
        file_path = self.base_path + f"episode/episode_{episode}/evaluation.json"
        # 保存路径
        path_to_save = self.base_path + f'episode/episode_{episode}/'

        if not os.path.exists(path_to_save):
            os.makedirs(path_to_save)

        # 读取 JSON 文件并累积数据
        with open(file_path, 'r') as file:
            data = json.load(file)
            step_num=data['step_num']

            step_to_generate_missions_num=data['step_to_generate_missions_num']
            step_executing_missions_num=data['step_executing_missions_num']
            step_success_missions_num=data['step_success_missions_num']
            step_failed_missions_num=data['step_failed_missions_num']
            step_early_failed_missions_num=data['step_early_failed_missions_num']

            step_generate_num=data['step_generate_num']
            step_allocate_num=data['step_allocate_num']
            step_success_num=data['step_success_num']
            step_fail_num=data['step_fail_num']
            step_early_fail_num=data['step_early_fail_num']

            step_sum_success_num_on_vehicle=data['step_sum_success_num_on_vehicle']
            step_sum_fail_num_on_vehicle = data['step_sum_fail_num_on_vehicle']
            step_sum_success_num_on_UAV=data['step_sum_success_num_on_UAV']
            step_sum_fail_num_on_UAV=data['step_sum_fail_num_on_UAV']

            step_fail_differ_type_mission_num_on_vehicle=data['step_fail_differ_type_mission_num_on_vehicle']
            step_fail_differ_type_mission_num_on_UAV=data['step_fail_differ_type_mission_num_on_UAV']

            available_sensor_num_list=data['available_sensor_num_list']
            UAV_positions=data['UAV_positions']
            UAV_mission_positions=data['UAV_mission_positions']

            step_avg_trans_rate=data['step_avg_trans_rate']
            step_avg_V2U_trans_rate=data['step_avg_V2U_trans_rate']
            step_avg_V2I_trans_rate=data['step_avg_V2I_trans_rate']
            step_avg_U2I_trans_rate=data['step_avg_U2I_trans_rate']

            V2U_link_using=data['V2U_link_using']
            V2I_link_using=data['V2I_link_using']
            U2I_link_using=data['U2I_link_using']

            V2U_data_trans_list=data['V2U_data_trans_list']
            V2I_data_trans_list=data['V2I_data_trans_list']
            U2I_data_trans_list=data['U2I_data_trans_list']

            step_avg_reward=data['step_avg_reward']
            step_avg_success_reward=data['step_avg_success_reward']

            step_completion_ratio=data['step_completion_ratio']
            step_completion_ratio_on_vehicle=data['step_completion_ratio_on_vehicle']
            step_completion_ratio_on_UAV=data['step_completion_ratio_on_UAV']

            step_weighted_acceleration_ratio=data['step_weighted_acceleration_ratio']
            step_acceleration_ratio=data['step_acceleration_ratio']

            step_avg_floating_time=data['step_avg_floating_time']
            step_avg_executing_time=data['step_avg_executing_time']

            step_avg_move_time=data['step_avg_move_time']
            step_avg_move_time_on_vehicle=data['step_avg_move_time_on_vehicle']
            step_avg_move_time_on_UAV=data['step_avg_move_time_on_UAV']

            step_avg_trans_time=data['step_avg_trans_time']
            step_avg_trans_time_on_vehicle=data['step_avg_trans_time_on_vehicle']
            step_avg_trans_time_on_UAV=data['step_avg_trans_time_on_UAV']

        # 1.过程监控
        # 累计任务
        x_indices = list(range(1, step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, step_to_generate_missions_num, label='To generate', color='gray')
        plt.plot(x_indices, step_executing_missions_num, label='Executing', color='deepskyblue')
        plt.plot(x_indices, step_success_missions_num, label='Success', color='limegreen')
        plt.plot(x_indices, step_failed_missions_num, label='Fail', color='red')
        plt.plot(x_indices, step_early_failed_missions_num, label='Not Allocate', color='orange')
        plt.title('Accumulate Process Monitoring')
        plt.xlabel('Step')
        plt.ylabel('The Number of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'AccumulateProcessMonitoring.png')
        plt.close()

        # 时隙任务
        x_indices = list(range(1, step_num + 1))
        plt.figure(clear=True)
        fig, axes = plt.subplots(2, 3, figsize=(12, 8))  # 两行三列
        # 子图1: Generate
        axes[0, 0].plot(x_indices, step_generate_num, label='Generate', color='gray')
        axes[0, 0].set_title('Generate')
        axes[0, 0].set_ylabel('The Number')
        axes[0, 0].grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        axes[0, 0].legend()
        # 子图2: Allocate
        axes[0, 1].plot(x_indices, step_allocate_num, label='Allocate', color='deepskyblue')
        axes[0, 1].set_title('Allocate')
        axes[0, 1].grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        axes[0, 1].legend()
        # 子图3: Success
        axes[0, 2].plot(x_indices, step_success_num, label='Success', color='limegreen')
        axes[0, 2].set_title('Success')
        axes[0, 2].grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        axes[0, 2].legend()
        # 子图4: Fail
        axes[1, 0].plot(x_indices, step_fail_num, label='Fail', color='red')
        axes[1, 0].set_title('Fail')
        axes[1, 0].set_ylabel('The Number')
        axes[1, 0].set_xlabel('Step')
        axes[1, 0].grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        axes[1, 0].legend()
        # 子图5: Not Allocate
        axes[1, 1].plot(x_indices, step_early_fail_num, label='Not Allocate', color='orange')
        axes[1, 1].set_title('Not Allocate')
        axes[1, 1].set_xlabel('Step')
        axes[1, 1].grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        axes[1, 1].legend()
        # 子图6: 留空或补充说明
        axes[1, 2].axis('off')  # 禁用第六个子图
        # axes[1, 2].text(0.5, 0.5, "Additional Info", ha='center', va='center', fontsize=12, color='gray')
        # 设置全局标题
        fig.suptitle('Timeslot Process Monitoring', fontsize=16)
        # 保存图像
        plt.savefig(path_to_save + 'TimeslotProcessMonitoring.png')
        plt.close()

        # 分类累计任务-成功
        x_indices = list(range(1, step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, step_success_missions_num, label='Sum', color='limegreen')
        plt.plot(x_indices, step_sum_success_num_on_vehicle, label='Veh', color='orange')
        plt.plot(x_indices, step_sum_success_num_on_UAV, label='UAV', color='deepskyblue')
        plt.title('Accumulate Process Monitoring (Success)')
        plt.xlabel('Step')
        plt.ylabel('The Number of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'AccumulateProcessMonitoring_Success.png')
        plt.close()

        # 分类累计任务-失败
        x_indices = list(range(1, step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, step_failed_missions_num, label='Sum', color='red')
        plt.plot(x_indices, step_sum_fail_num_on_vehicle, label='Veh', color='orange')
        plt.plot(x_indices, step_sum_fail_num_on_UAV, label='UAV', color='deepskyblue')
        plt.title('Accumulate Process Monitoring (Fail)')
        plt.xlabel('Step')
        plt.ylabel('The Number of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'AccumulateProcessMonitoring_Fail.png')
        plt.close()

        # 失败类型分类统计-UAV
        x_indices = list(range(1, step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, step_fail_differ_type_mission_num_on_UAV[MissionFinalStateEnum.FORCE_FAIL.value],
                 label='Force',color='red')
        plt.plot(x_indices, step_fail_differ_type_mission_num_on_UAV[MissionFinalStateEnum.SENSING_FAIL.value],
                 label='Sense',color='deepskyblue')
        plt.plot(x_indices, step_fail_differ_type_mission_num_on_UAV[MissionFinalStateEnum.TRANSMISSION_FAIL.value],
                 label='Trans',color='orange')
        plt.title('Fail on Different Stage UAV')
        plt.xlabel('Step')
        plt.ylabel('The Number of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'Fail_On_Different_Stage_UAV.png')
        plt.close()

        # 失败类型分类统计-Veh
        x_indices = list(range(1, step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, step_fail_differ_type_mission_num_on_vehicle[MissionFinalStateEnum.FORCE_FAIL.value],label='Force', color='red')
        plt.plot(x_indices, step_fail_differ_type_mission_num_on_vehicle[MissionFinalStateEnum.SENSING_FAIL.value],label='Sense', color='deepskyblue')
        plt.plot(x_indices, step_fail_differ_type_mission_num_on_vehicle[MissionFinalStateEnum.TRANSMISSION_FAIL.value],
                 label='Trans',color='orange')
        plt.title('Fail on Different Stage Vehicle')
        plt.xlabel('Step')
        plt.ylabel('The Number of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'Fail_On_Different_Stage_Veh.png')
        plt.close()

        # mission分配时可用sensor数量
        count_data = Counter(self.available_sensor_num_list)
        # 获取数字和它们的出现次数
        numbers = list(count_data.keys())  # 数字
        counts = list(count_data.values())  # 每个数字出现的次数
        # 创建条形图
        plt.figure(clear=True)
        plt.bar(numbers, counts)
        plt.title('Available Sensor Num')
        plt.xlabel('Num')
        plt.ylabel('Frequency')
        plt.grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        plt.savefig(path_to_save + 'Available_Sensor_Num.png')
        plt.close()

        # UAV轨迹与mission位置
        min_x = self.env.config['traffic']['x_range'][0]
        max_x = self.env.config['traffic']['x_range'][1]
        min_y = self.env.config['traffic']['y_range'][0]
        max_y = self.env.config['traffic']['y_range'][1]
        for i, (UAV_id, positions) in enumerate(UAV_positions.items()):
            plt.figure(clear=True)
            plt.xlim(min_x, max_x)
            plt.ylim(min_y, max_y)
            plt.gca().invert_yaxis()
            row=int(i/5)
            col=int(i%5)
            UAV_positions_x=[position[0] for position in positions]
            UAV_positions_y = [position[1] for position in positions]
            mission_positions=UAV_mission_positions[UAV_id]
            UAV_mission_positions_x=[position[0] for position in mission_positions]
            UAV_mission_positions_y = [position[1] for position in mission_positions]
            # 画出轨迹
            plt.plot(UAV_positions_x, UAV_positions_y, color='blue', linestyle='-', linewidth=0.2, zorder=1, label='Trajectory')
            # 画出轨迹的各个点：
            # 中间的点为蓝色（如果有）
            # if len(UAV_positions_x) > 2:
            #     plt.scatter(UAV_positions_x[1:-1], UAV_positions_y[1:-1], color='blue', s=2, zorder=2, label='Trajectory Intermediate')

            alphas = np.linspace(0.5, 1, len(mission_positions), dtype=float)  # 从 0.1（更透明）到 1（更不透明）
            # 画mission位置，设置 zorder 高于轨迹，保证不会被覆盖
            for i in range(len(UAV_mission_positions_x)):
                plt.scatter(UAV_mission_positions_x[i], UAV_mission_positions_y[i],
                            color='orange', s=100, alpha=float(alphas[i]), zorder=3)            # 第一个点为绿色
            plt.scatter(UAV_positions_x[0], UAV_positions_y[0], color='limegreen', s=20, zorder=4, label='Trajectory Start')
            # 最后一个点为红色
            plt.scatter(UAV_positions_x[-1], UAV_positions_y[-1], color='red', s=20, zorder=4, label='Trajectory End')

            # 设置横纵轴等
            plt.xlabel('X')
            plt.ylabel('Y')
            plt.grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
            # 设置全局标题
            plt.title(f'Trajectory and Mission Points On {UAV_id}')
            # 显示图例
            plt.legend()
            # 保存图像
            plt.savefig(path_to_save + f'TrajectoryAndMissionPoints_{UAV_id}.png')
            plt.close()

        # 2.信道状态
        # 信道速率指标
        x_indices = list(range(1, step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, step_avg_trans_rate, label='Avg', color='deepskyblue')
        plt.plot(x_indices, step_avg_V2U_trans_rate, label='V2U', color='limegreen')
        plt.plot(x_indices, step_avg_V2I_trans_rate, label='V2I', color='red')
        plt.plot(x_indices, step_avg_U2I_trans_rate, label='U2I', color='orange')
        plt.title('Channel Rate')
        plt.xlabel('Step')
        plt.ylabel('The Average Rate of Channel')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'ChannelRate.png')
        plt.close()

        # 信道使用情况
        x_indices = list(range(1, step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, V2U_link_using, label='V2U', color='limegreen')
        plt.plot(x_indices, V2I_link_using, label='V2I', color='red')
        plt.plot(x_indices, U2I_link_using, label='U2I', color='orange')
        plt.title('Link Using')
        plt.xlabel('SimulationStep')
        plt.ylabel('Link Num in Use')
        plt.grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'LinkUsing.png')
        plt.close()

        # 数据传输
        # V2U
        x_indices = list(range(1, len(V2U_data_trans_list) + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, V2U_data_trans_list, color='orange')
        plt.title('V2U Data Trans')
        plt.xlabel('Trans Step')
        plt.ylabel('Data Size')
        plt.grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        plt.savefig(path_to_save + 'V2U_data_trans.png')
        plt.close()
        # V2I
        x_indices = list(range(1, len(V2I_data_trans_list) + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, V2I_data_trans_list, color='orange')
        plt.title('V2I Data Trans')
        plt.xlabel('Trans Step')
        plt.ylabel('Data Size')
        plt.grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        plt.savefig(path_to_save + 'V2I_data_trans.png')
        plt.close()
        # U2I
        x_indices = list(range(1, len(U2I_data_trans_list) + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, U2I_data_trans_list, color='orange')
        plt.title('U2I Data Trans')
        plt.xlabel('Trans Step')
        plt.ylabel('Data Size')
        plt.grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        plt.savefig(path_to_save + 'U2I_data_trans.png')
        plt.close()

        # 3.奖励
        x_indices = list(range(1, step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, step_avg_reward, label='Avg', color='orange')
        plt.plot(x_indices, step_avg_success_reward, label='Suc', color='limegreen')
        plt.title('Reward')
        plt.xlabel('Step')
        plt.ylabel('The Average Reward of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'Reward.png')
        plt.close()

        # 4.完成率/加速率
        # completion ratio
        x_indices = list(range(1, step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, step_completion_ratio, label='Sum', color='limegreen')
        plt.plot(x_indices, step_completion_ratio_on_vehicle, label='Vehicle', color='orange')
        plt.plot(x_indices, step_completion_ratio_on_UAV, label='UAV', color='deepskyblue')
        plt.title('Completion Ratio')
        plt.xlabel('Step')
        plt.ylabel('The Completion Ratio of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'CompletionRatio.png')
        plt.close()

        # acceleration ratio
        x_indices = list(range(1, step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, step_weighted_acceleration_ratio, label='Weighted', color='orange')
        plt.plot(x_indices, step_acceleration_ratio, label='Not Weighted', color='limegreen')
        plt.title('Acceleration Ratio')
        plt.xlabel('Step')
        plt.ylabel('The Acceleration Ratio of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'AccelerationRatio.png')
        plt.close()

        # 5.时间优化指标
        x_indices = list(range(1, step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, step_avg_floating_time, label='Floating', color='orange')
        plt.plot(x_indices, step_avg_executing_time, label='Executing', color='limegreen')
        plt.title('Time')
        plt.xlabel('Step')
        plt.ylabel('The Related Time of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'Time.png')
        plt.close()

        # 移动时间
        x_indices = list(range(1, step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, step_avg_move_time, label='Sum', color='limegreen')
        plt.plot(x_indices, step_avg_move_time_on_vehicle, label='Veh', color='orange')
        plt.plot(x_indices, step_avg_move_time_on_UAV, label='UAV', color='deepskyblue')
        plt.title('Pre Move Time')
        plt.xlabel('Step')
        plt.ylabel('Time(s)')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'PreMoveTime.png')
        plt.close()

        # 传输时间
        x_indices = list(range(1, step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, step_avg_trans_time, label='Sum', color='limegreen')
        plt.plot(x_indices, step_avg_trans_time_on_vehicle, label='Veh', color='orange')
        plt.plot(x_indices, step_avg_trans_time_on_UAV, label='UAV', color='deepskyblue')
        plt.title('Transmission Time')
        plt.xlabel('Step')
        plt.ylabel('Time(s)')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(path_to_save + 'TransmissionTime.png')
        plt.close()



    def episodeRecordToFile(self, episode):
        record_name="episode"
        record_idx=episode
        folder_dir=self.base_path + f"final/"
        if not os.path.exists(folder_dir):
            os.makedirs(folder_dir)
        file_dir = folder_dir+"evaluation.json"

        data = {
            f"{record_name}": record_idx,
            "to_generate_missions_num": self.to_generate_missions_num,
            "executing_missions_num": self.executing_missions_num,
            "success_missions_num": self.success_missions_num,
            "failed_missions_num": self.failed_missions_num,
            "early_failed_missions_num": self.early_failed_missions_num,

            "success_mission_num_on_vehicle": self.success_mission_num_on_vehicle,
            "fail_mission_num_on_vehicle": self.fail_mission_num_on_vehicle,
            "success_mission_num_on_UAV": self.success_mission_num_on_UAV,
            "fail_mission_num_on_UAV": self.fail_mission_num_on_UAV,

            "fail_differ_type_mission_num_on_UAV": self.fail_differ_type_mission_num_on_UAV,
            "fail_differ_type_mission_num_on_vehicle": self.fail_differ_type_mission_num_on_vehicle,

            "episode_available_sensor_num": float(sum(self.available_sensor_num_list)/len(self.available_sensor_num_list) if len(self.available_sensor_num_list)>0 else 0),

            "avg_trans_rate": float(self.avg_trans_rate),
            "avg_V2U_trans_rate": float(self.avg_V2U_trans_rate),
            "avg_V2I_trans_rate": float(self.avg_V2I_trans_rate),
            "avg_U2I_trans_rate": float(self.avg_U2I_trans_rate),

            "avg_reward": float(self.avg_reward),
            "avg_success_reward": float(self.avg_success_reward),

            "completion_ratio": float(self.completion_ratio),
            "completion_ratio_on_vehicle": float(self.completion_ratio_on_vehicle),
            "completion_ratio_on_UAV": float(self.completion_ratio_on_UAV),

            "weighted_acceleration_ratio": float(self.weighted_acceleration_ratio),
            "acceleration_ratio": float(self.acceleration_ratio),

            "avg_floating_time": float(self.avg_floating_time),
            "avg_executing_time": float(self.avg_executing_time),

            "avg_move_time": self.avg_move_time,
            "avg_move_time_on_vehicle": self.avg_move_time_on_vehicle,
            "avg_move_time_on_UAV": self.avg_move_time_on_UAV,

            "avg_trans_time": self.avg_trans_time,
            "avg_trans_time_on_vehicle": self.avg_trans_time_on_vehicle,
            "avg_trans_time_on_UAV": self.avg_trans_time_on_UAV,

        }

        # 检查文件是否存在
        if os.path.exists(file_dir):
            # 如果文件存在，读取现有数据并追加新数据
            with open(file_dir, "r") as file:
                existing_data = json.load(file)
            existing_data.append(data)
        else:
            # 如果文件不存在，初始化为嵌套列表
            existing_data = [data]

        # 将数据写回文件
        with open(file_dir, "w") as file:
            json.dump(existing_data, file, indent=4)  # json格式缩进4个空格

        print(f"save {self.tag} episode_{episode} records successfully")

    def drawEpisodeRecordsByFile(self):
        """
        Generate plots from JSON files stored for each episode.
        """
        # json文件路径
        file_path = self.base_path + f"final/evaluation.json"
        # 保存路径
        save_dir = self.base_path + f'final/'

        if not os.path.exists(save_dir):
            os.makedirs(save_dir)

        # 读取 JSON 文件并累积数据
        with open(file_path, 'r') as file:
            data = json.load(file)
            episode_nums = [entry["episode"] for entry in data]
            step_num=len(episode_nums)
            to_generate_missions_num = [entry["to_generate_missions_num"] for entry in data]
            executing_missions_num = [entry["executing_missions_num"] for entry in data]
            success_missions_num = [entry["success_missions_num"] for entry in data]
            failed_missions_num = [entry["failed_missions_num"] for entry in data]
            early_failed_missions_num = [entry["early_failed_missions_num"] for entry in data]

            fail_differ_type_mission_num_on_UAV = [entry["fail_differ_type_mission_num_on_UAV"] for entry in data]
            fail_differ_type_mission_num_on_UAV = list(zip(*fail_differ_type_mission_num_on_UAV))
            fail_differ_type_mission_num_on_UAV = [list(arr) for arr in fail_differ_type_mission_num_on_UAV]
            fail_differ_type_mission_num_on_vehicle = [entry["fail_differ_type_mission_num_on_vehicle"] for entry in data]
            fail_differ_type_mission_num_on_vehicle = list(zip(*fail_differ_type_mission_num_on_vehicle))
            fail_differ_type_mission_num_on_vehicle = [list(arr) for arr in fail_differ_type_mission_num_on_vehicle]

            episode_available_sensor_num = [entry["episode_available_sensor_num"] for entry in data]

            avg_trans_rate = [entry["avg_trans_rate"] for entry in data]
            avg_V2U_trans_rate = [entry["avg_V2U_trans_rate"] for entry in data]
            avg_V2I_trans_rate = [entry["avg_V2I_trans_rate"] for entry in data]
            avg_U2I_trans_rate = [entry["avg_U2I_trans_rate"] for entry in data]

            success_mission_num_on_vehicle = [entry["success_mission_num_on_vehicle"] for entry in data]
            fail_mission_num_on_vehicle = [entry["fail_mission_num_on_vehicle"] for entry in data]
            success_mission_num_on_UAV = [entry["success_mission_num_on_UAV"] for entry in data]
            fail_mission_num_on_UAV = [entry["fail_mission_num_on_UAV"] for entry in data]

            avg_reward = [entry["avg_reward"] for entry in data]
            avg_success_reward = [entry["avg_success_reward"] for entry in data]

            completion_ratio = [entry["completion_ratio"] for entry in data]
            completion_ratio_on_vehicle = [entry["completion_ratio_on_vehicle"] for entry in data]
            completion_ratio_on_UAV = [entry["completion_ratio_on_UAV"] for entry in data]

            weighted_acceleration_ratio = [entry["weighted_acceleration_ratio"] for entry in data]
            acceleration_ratio = [entry["acceleration_ratio"] for entry in data]

            avg_floating_time = [entry["avg_floating_time"] for entry in data]
            avg_executing_time = [entry["avg_executing_time"] for entry in data]

            avg_move_time = [entry["avg_move_time"] for entry in data]
            avg_move_time_on_vehicle = [entry["avg_move_time_on_vehicle"] for entry in data]
            avg_move_time_on_UAV = [entry["avg_move_time_on_UAV"] for entry in data]

            avg_trans_time = [entry["avg_trans_time"] for entry in data]
            avg_trans_time_on_vehicle = [entry["avg_trans_time_on_vehicle"] for entry in data]
            avg_trans_time_on_UAV = [entry["avg_trans_time_on_UAV"] for entry in data]

        # 绘图
        # 过程监控
        plt.figure(clear=True)
        plt.plot(episode_nums, to_generate_missions_num, label='To generate', color='gray')
        plt.plot(episode_nums, executing_missions_num, label='Executing', color='deepskyblue')
        plt.plot(episode_nums, success_missions_num, label='Success', color='limegreen')
        plt.plot(episode_nums, failed_missions_num, label='Fail', color='red')
        plt.plot(episode_nums, early_failed_missions_num, label='Not Allocate', color='orange')
        plt.title('Process Monitoring')
        plt.xlabel('Episode')
        plt.ylabel('The Number of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(save_dir + 'ProcessMonitoring.png')
        plt.close()

        # 分类累计任务-成功
        plt.figure(clear=True)
        plt.plot(episode_nums, success_missions_num, label='Sum', color='limegreen')
        plt.plot(episode_nums, success_mission_num_on_vehicle, label='Veh', color='orange')
        plt.plot(episode_nums, success_mission_num_on_UAV, label='UAV', color='deepskyblue')
        plt.title('Accumulate Process Monitoring (Success)')
        plt.xlabel('Episode')
        plt.ylabel('The Number of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(save_dir + 'AccumulateProcessMonitoring_Success.png')
        plt.close()

        # 分类累计任务-失败
        plt.figure(clear=True)
        plt.plot(episode_nums, failed_missions_num, label='Sum', color='red')
        plt.plot(episode_nums, fail_mission_num_on_vehicle, label='Veh', color='orange')
        plt.plot(episode_nums, fail_mission_num_on_UAV, label='UAV', color='deepskyblue')
        plt.title('Accumulate Process Monitoring (Fail)')
        plt.xlabel('Episode')
        plt.ylabel('The Number of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(save_dir + 'AccumulateProcessMonitoring_Fail.png')
        plt.close()

        # 失败类型分类统计-UAV
        plt.figure(clear=True)
        plt.plot(episode_nums, fail_differ_type_mission_num_on_UAV[MissionFinalStateEnum.FORCE_FAIL.value],
                 label='Force',color='red')
        plt.plot(episode_nums, fail_differ_type_mission_num_on_UAV[MissionFinalStateEnum.SENSING_FAIL.value],
                 label='Sense',color='deepskyblue')
        plt.plot(episode_nums, fail_differ_type_mission_num_on_UAV[MissionFinalStateEnum.TRANSMISSION_FAIL.value],
                 label='Trans',color='orange')
        plt.title('Fail on Different Stage UAV')
        plt.xlabel('Step')
        plt.ylabel('The Number of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        plt.legend()
        plt.savefig(save_dir + 'Fail_On_Different_Stage_UAV.png')
        plt.close()

        # 失败类型分类统计-Veh
        x_indices = list(range(1, step_num + 1))
        plt.figure(clear=True)
        plt.plot(episode_nums, fail_differ_type_mission_num_on_vehicle[MissionFinalStateEnum.FORCE_FAIL.value],label='Force', color='red')
        plt.plot(episode_nums, fail_differ_type_mission_num_on_vehicle[MissionFinalStateEnum.SENSING_FAIL.value],label='Sense', color='deepskyblue')
        plt.plot(episode_nums, fail_differ_type_mission_num_on_vehicle[MissionFinalStateEnum.TRANSMISSION_FAIL.value],label='Trans',
                 color='orange')
        plt.title('Fail on Different Stage Vehicle')
        plt.xlabel('Step')
        plt.ylabel('The Number of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        plt.legend()
        plt.savefig(save_dir + 'Fail_On_Different_Stage_Veh.png')
        plt.close()

        # mission分配时可用sensor数量
        plt.figure(clear=True)
        plt.plot(episode_nums, episode_available_sensor_num, color='green')
        plt.title('Available Sensor Num')
        plt.xlabel('Step')
        plt.ylabel('The Number of Sensors')
        plt.grid(True, which='both', linestyle='--', linewidth=0.1, alpha=0.7)
        plt.savefig(save_dir + 'Available_Sensor_Num.png')
        plt.close()

        # 信道速率指标
        plt.figure(clear=True)
        plt.plot(episode_nums, avg_trans_rate, label='Avg', color='deepskyblue')
        plt.plot(episode_nums, avg_V2U_trans_rate, label='V2U', color='limegreen')
        plt.plot(episode_nums, avg_V2I_trans_rate, label='V2I', color='red')
        plt.plot(episode_nums, avg_U2I_trans_rate, label='U2I', color='orange')
        plt.title('Channel Rate')
        plt.xlabel('Episode')
        plt.ylabel('The Average Rate of Channel')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(save_dir + 'ChannelRate.png')
        plt.close()

        # reward
        plt.figure(clear=True)
        plt.plot(episode_nums, avg_reward, label='Avg', color='orange')
        plt.plot(episode_nums, avg_success_reward, label='Suc', color='limegreen')
        plt.title('Reward')
        plt.xlabel('Episode')
        plt.ylabel('The Average Reward of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(save_dir + 'Reward.png')
        plt.close()

        # completion ratio
        plt.figure(clear=True)
        plt.plot(episode_nums, completion_ratio, label='Sum', color='limegreen')
        plt.plot(episode_nums, completion_ratio_on_vehicle, label='Vehicle', color='orange')
        plt.plot(episode_nums, completion_ratio_on_UAV, label='UAV', color='deepskyblue')
        plt.title('Completion Ratio')
        plt.xlabel('Episode')
        plt.ylabel('The Completion Ratio of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(save_dir + 'CompletionRatio.png')
        plt.close()

        # acceleration ratio
        plt.figure(clear=True)
        plt.plot(episode_nums, weighted_acceleration_ratio, label='Weighted Acc', color='orange')
        plt.plot(episode_nums, acceleration_ratio, label='Not Weighted Acc', color='limegreen')
        plt.title('Acceleration Ratio')
        plt.xlabel('Episode')
        plt.ylabel('The Acceleration Ratio of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(save_dir + 'AccelerationRatio.png')
        plt.close()

        # optimization objectives
        plt.figure(clear=True)
        plt.plot(episode_nums, avg_floating_time, label='Floating', color='orange')
        plt.plot(episode_nums, avg_executing_time, label='Executing', color='limegreen')
        plt.title('Time')
        plt.xlabel('Episode')
        plt.ylabel('The Related Time of Missions')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(save_dir + 'Time.png')
        plt.close()

        # 移动时间
        x_indices = list(range(1, step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, avg_move_time, label='Sum', color='limegreen')
        plt.plot(x_indices, avg_move_time_on_vehicle, label='Veh', color='orange')
        plt.plot(x_indices, avg_move_time_on_UAV, label='UAV', color='deepskyblue')
        plt.title('Pre Move Time')
        plt.xlabel('Episode')
        plt.ylabel('Time(s)')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(save_dir + 'PreMoveTime.png')
        plt.close()

        # 传输时间
        x_indices = list(range(1, step_num + 1))
        plt.figure(clear=True)
        plt.plot(x_indices, avg_trans_time, label='Sum', color='limegreen')
        plt.plot(x_indices, avg_trans_time_on_vehicle, label='Veh', color='orange')
        plt.plot(x_indices, avg_trans_time_on_UAV, label='UAV', color='deepskyblue')
        plt.title('Transmission Time')
        plt.xlabel('Episode')
        plt.ylabel('Time(s)')
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend()
        plt.savefig(save_dir + 'TransmissionTime.png')
        plt.close()


    def __getNodeTypeById(self, node_id):
        node_id = node_id.capitalize()
        assert node_id[0] in ['V', 'R', 'U', 'C'], f'Invalid node type of {node_id}'
        if node_id[0] == 'V':
            return 'V'
        elif node_id[0] == 'R':
            return 'I'
        elif node_id[0] == 'U':
            return 'U'
        elif node_id[0] == 'C':
            return 'C'
        else:
            return None

    def getAccReward(self):
        return self.sum_reward

    def getAvgReward(self):
        return self.avg_reward

    def getCompletionRatio(self):
        return self.completion_ratio

    def getUAVAccReward(self,UAV_idx):
        UAV_id=self.env.traffic_manager.completeStrId(UAV_idx,"U")
        return sum(self.UAV_rewards[UAV_id])

    def getUAVAvgReward(self,UAV_idx):
        UAV_id=self.env.traffic_manager.completeStrId(UAV_idx,"U")
        return sum(self.UAV_rewards[UAV_id])/len(self.UAV_rewards[UAV_id])
