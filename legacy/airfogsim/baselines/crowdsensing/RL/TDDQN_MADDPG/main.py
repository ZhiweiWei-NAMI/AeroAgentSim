import sys
import os
import faulthandler

faulthandler.enable()

isAirFogSim = False
root_path = os.path.abspath(os.path.join(os.path.dirname(__file__)))
cnt = 0
while not isAirFogSim:
    cnt += 1
    if 'airfogsim' in os.listdir(root_path) or cnt > 10:
        isAirFogSim = True
    else:
        root_path = os.path.abspath(os.path.join(root_path, os.path.pardir))
sys.path.append(root_path)
dir_name = os.path.dirname(__file__)

os.environ['useCUPY'] = 'False'
print('useCUPY:', os.environ['useCUPY'])
# When n_RB < 50, numpy is better than cupy; When n_RB >= 50, cupy is better than numpy.

from airfogsim import AirFogSimEnv, AirFogSimEvaluation
from baselines.crowdsensing.RL.TDDQN_MADDPG.TDDQN_MADDPG_algorithm import TransDDQN_MADDPG_AlgorithmModule

import yaml
from pyinstrument import Profiler

root = os.path.abspath(__file__)


def load_config(path):
    with open(path, 'r') as file:
        config = yaml.safe_load(file)
        return config


last_episode = 30
max_episode = 200

# 启动全局性能监控
# global_profiler = Profiler()
# global_profiler.start()

# 定义episode性能监控
episode_profiler= Profiler()

# 1. Load the configuration file
config_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join("../../", 'config.yaml')
config = load_config(config_path)

# 2. Get algorithm module
# algorithm_module = GreedyAlgorithmModule()
algorithm_module = TransDDQN_MADDPG_AlgorithmModule()
algorithm_tag = algorithm_module.getAlgorithmTag()

# 3. Create the new environment
env = AirFogSimEnv(config, interactive_mode=None)
# env = AirFogSimEnv(config, interactive_mode='graphic')

# 4. Initialize the algorithm module (initialize in every episode)
algorithm_module.initialize(env,last_episode=last_episode)

# 5. Create the evaluation module
evaluation_module = AirFogSimEvaluation(algorithm_module.getAlgorithmTag())

# for episode in range(last_episode + 1, max_episode + 1):
#     # 启动episode性能监控
#     episode_profiler.start()
#
#     while not env.isDone():
#         algorithm_module.scheduleStep(env)
#         env.step()
#
#         acc_reward = evaluation_module.getAccReward()
#         avg_reward = evaluation_module.getAvgReward()
#         succ_ratio = evaluation_module.getCompletionRatio()
#         print(f"Simulation time: {env.simulation_time:.2f}, ACC_Reward: {acc_reward:.2f}, AVG_Reward: {avg_reward:.2f}, SUCC_Ratio: {succ_ratio:.2f}")
#         env.render()
#
#         algorithm_module.updateTAExperience(env)
#         algorithm_module.TransDDQN_env.train()
#         algorithm_module.updatePPExperience(env)
#         algorithm_module.MADDPG_env.train()
#
#         evaluation_module.updateEvaluationIndicators(env, algorithm_module)
#         evaluation_module.addToStepRecord()
#         # evaluation_module.printEvaluation()
#
#     algorithm_module.TransDDQN_env.saveModel(episode)
#     algorithm_module.MADDPG_env.saveModel(episode)
#
#     evaluation_module.addToEpisodeRecord()
#     evaluation_module.stepRecordToFile(episode)
#     evaluation_module.episodeRecordToFile(episode)
#     evaluation_module.drawStepRecordsByFile(episode)
#     evaluation_module.initOrResetStepRecords()
#     evaluation_module.initOrResetStepIndicators()
#
#     # 构建车辆数据时不能reset，且只需要一个episode
#     env.reset()
#     algorithm_module.reset(env)
#     print(f"episode: {episode} finished")
#     # 结束性能监控并打印报告
#     episode_profiler.stop()
#     episode_profiler.print()
#     # 重置性能监控
#     episode_profiler.reset()

evaluation_module.drawEpisodeRecordsByFile()
env.close()
# 结束性能监控并打印报告
# global_profiler.stop()
# global_profiler.print()
