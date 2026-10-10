import sys
import os
import faulthandler
from torch.utils.tensorboard import SummaryWriter
from airfogsim import AirFogSimEnv, AirFogSimEvaluation
from train.MADDPG_train.MADDPG_train_algorithm import MADDPG_Train_AlgorithmModule

import yaml
from pyinstrument import Profiler

# 启动报错监控
faulthandler.enable()

# 查找并添加文件路径、项目根路径
isAirFogSim = False
file_path = os.path.abspath(__file__)
root_path = os.path.abspath(os.path.join(os.path.dirname(__file__)))
cnt = 0
while not isAirFogSim:
    cnt += 1
    if 'airfogsim' in os.listdir(root_path) or cnt > 10:
        isAirFogSim = True
    else:
        root_path = os.path.abspath(os.path.join(root_path, os.path.pardir))
sys.path.append(root_path)
sys.path.append(file_path)
dir_name = os.path.dirname(__file__)

os.environ['useCUPY'] = 'False'
print('useCUPY:', os.environ['useCUPY'])
# When n_RB < 50, numpy is better than cupy; When n_RB >= 50, cupy is better than numpy.


def load_config(path):
    with open(path, 'r') as file:
        config = yaml.safe_load(file)
        return config


last_episode = 49
max_episode = 200

# 启动全局性能监控
# global_profiler = Profiler()
# global_profiler.start()

# 定义episode性能监控
episode_profiler= Profiler()

# 启用tensorboard记录
writer = SummaryWriter("./logs")

# 1. Load the configuration file
config_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join("../", 'config.yaml')
config = load_config(config_path)

# 2. Get algorithm module
# algorithm_module = GreedyAlgorithmModule()
algorithm_module = MADDPG_Train_AlgorithmModule()
algorithm_tag = algorithm_module.getAlgorithmTag()

# 3. Create the new environment
env = AirFogSimEnv(config, interactive_mode=None)
# env = AirFogSimEnv(config, interactive_mode='graphic')

# 4. Initialize the algorithm module
algorithm_module.initialize(env,last_episode=last_episode,final=True)

# 5. Create the evaluation module
evaluation_module = AirFogSimEvaluation(algorithm_tag)

for episode in range(last_episode + 1, max_episode + 1):
    # 启动episode性能监控
    episode_profiler.start()

    actor_step_loss=[]
    critic_step_loss=[]
    while not env.isDone():
        algorithm_module.scheduleStep(env)
        a_loss,c_loss=algorithm_module.train(env) # 训练模型
        env.step()
        env.render()

        # 存储loss
        if a_loss is not None and c_loss is not None:
            for i in range(len(a_loss)):
                if len(actor_step_loss)<=i:
                    actor_step_loss.append([])
                actor_step_loss[i].append(a_loss[i])
            for i in range(len(c_loss)):
                if len(critic_step_loss)<=i:
                    critic_step_loss.append([])
                critic_step_loss[i].append(c_loss[i])
        evaluation_module.updateAndSaveStepRecords(env, algorithm_module) # 保存一步记录

        acc_reward = evaluation_module.getAccReward()
        avg_reward = evaluation_module.getAvgReward()
        succ_ratio = evaluation_module.getCompletionRatio()
        print(f"Simulation time: {env.simulation_time:.2f}, ACC_Reward: {acc_reward:.2f}, AVG_Reward: {avg_reward:.2f}, SUCC_Ratio: {succ_ratio:.2f}")
        print(f'a_loss: {a_loss}')
        print(f'c_loss: {c_loss}')
        print()

    algorithm_module.saveModel(episode,final=True) # 保存模型
    evaluation_module.updateAndSaveEpisodeRecords(episode) # 保存整个episode记录

    # 记录训练效果
    if episode > 1 and len(actor_step_loss)>0 and len(critic_step_loss)>0:
        avg_reward = evaluation_module.getAvgReward()
        succ_ratio = evaluation_module.getCompletionRatio()
        writer.add_scalar("avg_reward", avg_reward, episode)
        writer.add_scalar("succ_ratio", succ_ratio, episode)
        for i in range(len(actor_step_loss)):
            avg_loss = sum(actor_step_loss[i]) / len(actor_step_loss[i])
            writer.add_scalar(f"actor_loss_{i}", avg_loss, episode)
        for i in range(len(critic_step_loss)):
            avg_loss = sum(critic_step_loss[i]) / len(critic_step_loss[i])
            writer.add_scalar(f"critic_loss_{i}", avg_loss, episode)

    # 构建车辆数据时不能reset，且只需要一个episode
    env.reset()
    algorithm_module.reset(env)
    print(f"episode: {episode} finished")
    print()
    # 结束性能监控并打印报告
    episode_profiler.stop()
    episode_profiler.print()
    # 重置性能监控
    episode_profiler.reset()

evaluation_module.drawEpisodeRecordsByFile() # 可视化训练过程（一个episode一条数据）
env.close()
writer.close()
# 结束性能监控并打印报告
# global_profiler.stop()
# global_profiler.print()
