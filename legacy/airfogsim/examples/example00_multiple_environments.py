import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
dir_name = os.path.dirname(__file__)

from airfogsim import AirFogSimEnv, BaseAlgorithmModule, AirFogSimEnvVisualizer
from airfogsim.scheduler import RewardScheduler
import time
import yaml
import multiprocessing
def process_env(env, accumulated_reward, accumulated_reward_lock, ALL_ENV_DONE):
    global algorithm_module
    if env.isDone(): 
        ALL_ENV_DONE[env.airfogsim_label] = True
        return
    algorithm_module.scheduleStep(env)
    env.step()
    reward = algorithm_module.getRewardByTask(env)
    with accumulated_reward_lock:
        accumulated_reward[env.airfogsim_label] += reward

def load_config(path):
    with open(path, 'r') as file:
        return yaml.safe_load(file)

# 1. Load the configuration file
config_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), 'config.yaml')
config = load_config(config_path)

# 2. Create the environment
envs = [AirFogSimEnv(config) for _ in range(2)]

# 3. Get algorithm module
algorithm_module = BaseAlgorithmModule()
ALL_ENV_DONE = multiprocessing.Manager().dict()  # Shared dictionary for environment states
for env in envs:
    algorithm_module.initialize(env)
    RewardScheduler.setModel(env, 'REWARD', 'task_delay')
    ALL_ENV_DONE[env.airfogsim_label] = False

# 4. Shared variables
accumulated_reward = multiprocessing.Manager().dict()  # Shared dictionary for accumulated rewards
for env in envs:
    accumulated_reward[env.airfogsim_label] = 0

# 5. Create a lock using Manager to be shared across processes
accumulated_reward_lock = multiprocessing.Manager().Lock()

def print_rewards(accumulated_reward):
    # Print rewards in main process (called periodically)
    print(f"ACC_Reward: {dict(accumulated_reward)}")

# Create a process pool with exactly 2 processes (one for each environment)
with multiprocessing.Pool(processes=2) as pool:
    cnt = 0
    while not all(ALL_ENV_DONE.values()):
        time.sleep(0.1)
        cnt += 1
        # Use pool.starmap to assign each environment to a process
        pool.starmap(process_env, [(env, accumulated_reward, accumulated_reward_lock, ALL_ENV_DONE) for env in envs])

        if cnt % 10 == 0:
            # Print rewards every 10 iterations
            print_rewards(accumulated_reward)

# Close environments after simulation
for env in envs:
    env.close()