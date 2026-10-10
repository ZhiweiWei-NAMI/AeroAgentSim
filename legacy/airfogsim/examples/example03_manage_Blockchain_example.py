import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
dir_name = os.path.dirname(__file__)

from airfogsim import AirFogSimEnv, BaseAlgorithmModule,AirFogSimScheduler
from airfogsim.enum_const import EnumerateConstants

import numpy as np
import yaml
import sys
import time

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

# 4. Get blckchain scheduler module
blockchainSched = AirFogSimScheduler.getBlockchainScheduler()
blockchainSched.setBlockchainConsensus(env, EnumerateConstants.CONSENSUS_POS)

previous_block_count=0
node_number=0
while not env.isDone():
    algorithm_module.scheduleStep(env)
    env.step()
    # 5. Output

    # 5.1 Get the size of the new block (bytes)
    # Check if a new block has been generated
    current_block_count = blockchainSched.getBlockNum(env)
    if current_block_count > previous_block_count:
        # A new block has been generated, output its size
        new_block_index = current_block_count - 1  
        block = env.blockchain_manager.getBlockchain().getBlockByIndex(new_block_index)
        block_size = block.getBlockSize()
        previous_block_count = current_block_count  
    else:
        block_size = 0  # No new block generated

    # 5.2 Get TPS (Transactions Per Second)
    tps = blockchainSched.getTransactionsPerSecond(env)

    # 5.3 Get the size of the blockchain (bytes)
    blockchain_size = blockchainSched.getBlockchainSize(env)

    sys.stdout.write(f"\rSimulation time: {env.simulation_time} | Nodes Number: {current_block_count} | TPS: {tps:.2f} | "
                     f"New Block Size: {block_size} bytes | Total Blockchain Size: {blockchain_size} bytes")
    sys.stdout.flush()

    time.sleep(0.5)  # 使输出更平滑
    env.render()
env.close()