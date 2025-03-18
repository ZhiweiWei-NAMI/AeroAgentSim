import sys
import os
import faulthandler
from torch.utils.tensorboard import SummaryWriter

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
from train2.base_random.base_random_algorithm import RandomAlgorithmModule
import yaml
from pyinstrument import Profiler


def load_config(path):
    with open(path, 'r') as file:
        config = yaml.safe_load(file)
        return config


last_episode = 0
max_episode = 200

# 启动全局性能监控
# global_profiler = Profiler()
# global_profiler.start()

# 定义episode性能监控
episode_profiler= Profiler()

# 启用tensorboard记录
writer = SummaryWriter("./logs")

# 1. Load the configuration file
config_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join("./", 'config.yaml')
config = load_config(config_path)

# 2. Get algorithm module
algorithm_module = RandomAlgorithmModule()

# 3. Create the new environment
env = AirFogSimEnv(config, interactive_mode=None)
# env = AirFogSimEnv(config, interactive_mode='graphic')

# 4. Initialize the algorithm module (initialize in every episode)
algorithm_module.initialize(env,last_episode=last_episode)

# 5. Create the evaluation module
evaluation_module = AirFogSimEvaluation(env,algorithm_module)

for episode in range(last_episode + 1, max_episode + 1):
    # 启动episode性能监控
    episode_profiler.start()

    while not env.isDone():
        algorithm_module.scheduleStep(env)
        env.step()

        acc_reward = evaluation_module.getAccReward()
        avg_reward = evaluation_module.getAvgReward()
        succ_ratio = evaluation_module.getCompletionRatio()
        print(f"Simulation time: {env.simulation_time:.2f}, ACC_Reward: {acc_reward:.2f}, AVG_Reward: {avg_reward:.2f}, SUCC_Ratio: {succ_ratio:.2f}")
        env.render()
        evaluation_module.updateAndSaveStepRecords()  # 保存一步记录

    # 记录训练效果
    acc_reward = evaluation_module.getAccReward()
    avg_reward = evaluation_module.getAvgReward()
    succ_ratio = evaluation_module.getCompletionRatio()
    writer.add_scalar("acc_reward", acc_reward, episode)
    writer.add_scalar("avg_reward", avg_reward, episode)
    writer.add_scalar("succ_ratio", succ_ratio, episode)

    evaluation_module.updateAndSaveEpisodeRecords(episode)  # 保存整个episode记录
    evaluation_module.drawEpisodeRecordsByFile()

    # 构建车辆数据时不能reset，且只需要一个episode
    env.reset()
    algorithm_module.reset(env)
    print(f"episode: {episode} finished")
    # 结束性能监控并打印报告
    episode_profiler.stop()
    episode_profiler.print()
    # 重置性能监控
    episode_profiler.reset()

# evaluation_module.drawAndResetEpisodeRecord()
# evaluation_module.drawEpisodeRecordsByFile()
env.close()
# 结束性能监控并打印报告
# global_profiler.stop()
# global_profiler.print()
