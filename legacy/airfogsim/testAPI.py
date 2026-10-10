# 测试评估airfogsim需要的api

from airfogsim import AirFogSimEnv, AirFogSimScheduler, AirFogSimEnvVisualizer
import numpy as np
import yaml
import sys
import torch.nn.functional as F
import torch

class DQN(torch.nn.Module):
    def __init__(self):
        super(DQN, self).__init__()
        self.fc1 = torch.nn.Linear(4, 128)
        self.fc2 = torch.nn.Linear(128, 128)
        self.fc3 = torch.nn.Linear(128, 2)

    def forward(self, x):
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        return self.fc3(x)
def load_config(path):
    with open(path, 'r') as file:
        return yaml.safe_load(file)

config = load_config('config.yaml')

config_path = sys.argv[1] if len(sys.argv) > 1 else 'config.yaml'
config = load_config(config_path)
env = AirFogSimEnv(config)
env2 = AirFogSimEnv(config) # 两个env，用于测试多个env的情况
env_wrapper = AirFogSimEnvVisualizer(env, config) # 可视化

# 接下来，测试AirFogSimEnv的API
# 整体框架是，算法优化层->调度层->环境层

# 算法优化层这里不涉及，直接从调度层开始。调度层的基础设置是在AirFogSimScheduler中完成的，如果不做设置，会使用默认值
# 如果sched的方法没有By，默认全局生效
# ----------------------------------------------------------------------------------------------------------

method_list = AirFogSimScheduler.getSchedulerMethodList()
print(method_list)
# 可以通过AirFogSimScheduler的静态方法，AirFogSimScheduler.addMethodsFromSchedulers()，使得所有的调度器方法都可以通过AirFogSimScheduler来调用
AirFogSimScheduler.addMethodsFromSchedulers()

# 计算资源调度器，包括初始化和运行中的调度。实际上直接通过ComputationScheduler来调用就行了
compSched = AirFogSimScheduler.getComputationScheduler()
compSched.setComputationModel(env, 'M/M/1')

# 通信资源调度器
commSched = AirFogSimScheduler.getCommunicationScheduler()
commSched.setNumberOfRB(env, 100)
commSched.setRBBandwidth(env, 1) # 1MHz
commSched.setV2VFadingModel(env, 'Rayleigh')
commSched.setV2VPathLossModel(env, 'LogDistance')
commSched.setV2VShadowingModel(env, 'LogNormal')
commSched.setV2VRange(env, 100)


# 区块链资源调度器，默认一个global链，是否需要多个链？
blockchainSched = AirFogSimScheduler.getBlockchainScheduler()
blockchainSched.setBlockchainModel(env, 'PoW')

# 任务调度器
taskSched = AirFogSimScheduler.getTaskScheduler()
taskSched.setTaskArrivalModel(env, 'Poisson') # Poisson, random, etc.
taskSched.setTaskArrivalRate(env, 0.5) # 任务到达率, per second per task node
taskSched.setTaskSizeModel(env, 'Exponential') # Exponential, uniform, etc.
taskSched.setTaskSizeRange(env, [0.5, 1.5]) # 任务大小范围, in MB
taskSched.setTaskCPUModel(env, 'Exponential') # Exponential, uniform, etc.
taskSched.setTaskCPURange(env, [0.5, 1.5]) # 任务CPU需求范围, in MIPS
taskSched.setTaskDeadlineModel(env, 'Exponential') # Exponential, uniform, etc.
taskSched.setTaskDeadlineRange(env, [0.5, 1.5]) # 任务截止时间范围, in seconds

#奖励调度器，设置每一个任务的奖励，解析数学公式来进行设置
rewardSched = AirFogSimScheduler.getRewardScheduler()
rewardSched.setRewardModel(env, '1/log(1+delay)')

# Agent调度器，为FogNode或者TaskNode设置Agent
agentSched = AirFogSimScheduler.getAgentScheduler()
agentSched.addAgentModelByNodeId(env, model_id='DQN_1', model=DQN(), node_id='Vehicle-1')

# 实体调度器，设置实体的位置，速度等信息。实体包括车辆、UAV、RSU、CloudServer, Channel等。还设置traffic信息，包括车辆生成、车辆移动等
entitySched = AirFogSimScheduler.getEntityScheduler()
entitySched.setVehicleTrafficModel(env, 'Poisson') # Poisson, random, etc. 车辆到达模型
entitySched.setVehicleArrivalRate(env, 0.5) # 车辆到达率, per second，-1表示一下子生成所有车辆
entitySched.setMaxVehicleNumber(env, 200) # 最大车辆数，是类变量，所有env共享


# Topology调度器，设置区域/道路的信息，包括最大限制速度、最大限制车辆数等
topoSched = AirFogSimScheduler.getTopologyScheduler()
topoSched.setMaxSpeedByLaneId(env, 'Lane-1', 10) # 设置Lane-1的最大速度为10m/s
topoSched.setMaxVehicleByLaneId(env, 'Lane-1', 10) # 设置Lane-1的最大车辆数为10

while True:

    # 先获取一些信息，所有Ids都可以直接通过env获取，因为不需要额外的处理；复杂的处理都在scheduler中完成
    vehicleIds = entitySched.getVehicleIds(env)
    uavIds = entitySched.getUAVIds(env)
    rsuIds = entitySched.getRSUIds(env)
    cloudServerIds = entitySched.getCloudServerIds(env)
    regionIds = entitySched.getRegionIds(env)

    V2VChannelIds = entitySched.getV2VChannelIds(env)
    for channelId in V2VChannelIds:
        commSched.getCSIByChannelId(channelId) # 获取信道状态信息
    V2UChannelIds = entitySched.getV2UChannelIds(env) # 此外还有U2R, U2U, R2R, R2U等信道
    
    entitySched.getNeighborVehiclesByNodeId(env, 'RSU-1', distance = 100) # 获取distance距离内的邻居车辆
    entitySched.getNeighborRSUsByNodeId(env, 'Vehicle-1', distance = 100) # 获取distance距离内的邻居RSU
    entitySched.getNeighborUAVsByNodeId(env, 'Vehicle-1', distance = 100) # 获取distance距离内的邻居UAV

    # 在每一步之前，都可以通过AirFogSimEnvScheduler调整策略，即state -> action
    # 1. 通信资源调度
    n_RB = commSched.getAvailableNumberOfRB(env)
    flag = commSched.setRBByChannelId(env, 'V2V-1', 5)
    if not flag:
        print('Set RB failed!')
    # 2. 任务调度
    taskSched.setTaskArrivalRateByNodeId(env, 'Vehicle-1', 0.5) # 修改车辆的任务到达率
    taskSched.setTaskArrivalRateByRegion(env, 'Region-1', 0.5) # 修改区域的任务到达率. Region是和RSU对应的

    # 3. 计算资源调度
    task_list = compSched.getComputingTaskByNodeId(env, 'Fog-V_1')
    compSched.setCPUByNodeId(env, 'Fog-V_1', [0.5, 0.5])



    # 此外的一系列调度操作，需要丰富，并且可以通过算法进行优化

    
    dqnModel = agentSched.getAgentModelByNodeId(env, 'Vehicle-1')
    rew = rewardSched.getRewardByNodeId(env, 'Vehicle-1')
    done = env.step()
    if done:
        break

env_wrapper.close()