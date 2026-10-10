import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
dir_name = os.path.dirname(__file__)

from airfogsim import AirFogSimEnv, BaseAlgorithmModule
import numpy as np
import random
import yaml
import sys
from airfogsim.scheduler import RewardScheduler, TaskScheduler

def load_config(path):
    with open(path, 'r') as file:
        config = yaml.safe_load(file)
        return config

# 1. Load the configuration file
config_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), 'config.yaml')
config = load_config(config_path)

# 2. Create the environment
# env = AirFogSimEnv(config, interactive_mode='graphic')
env = AirFogSimEnv(config, interactive_mode=None)

# 3. Get algorithm module
algorithm_module = BaseAlgorithmModule()
algorithm_module.initialize(env)
RewardScheduler.setModel(env, 'REWARD', '1/task_delay')
accumulated_reward = 0
np.random.seed(0)
random.seed(0)
v2u_rate = [0]
v2i_rate = [0]
u2i_rate = [0]
while not env.isDone():
    algorithm_module.scheduleStep(env)
    env.step()
    accumulated_reward += algorithm_module.getRewardByTask(env)
    task_num = TaskScheduler.getDoneTaskNum(env)
    out_of_ddl_task_num = TaskScheduler.getOutOfDDLTasks(env)
    succ_ratio = task_num / max(1,task_num + out_of_ddl_task_num)
    env.render()
    v2u_rate.append(env.getChannelAvgRate('V2U'))
    v2i_rate.append(env.getChannelAvgRate('V2I'))
    u2i_rate.append(env.getChannelAvgRate('U2I'))
    print(f'Simulation time: {env.simulation_time:.2f}, Ratio: {succ_ratio:.2f}, ACC_Reward: {succ_ratio*accumulated_reward/max(1,task_num):.2f} V2U: {v2u_rate[-1]:.2f}, V2I: {v2i_rate[-1]:.2f}, U2I: {u2i_rate[-1]:.2f}', end='\r')
env.close()
# plt绘制
import matplotlib.pyplot as plt
plt.plot(v2u_rate[1:],label='V2U')
plt.plot(v2i_rate[1:],label='V2I')
plt.plot(u2i_rate[1:],label='U2I')
plt.legend()
plt.savefig('rate.png',dpi=300)
print('Simulation done!')