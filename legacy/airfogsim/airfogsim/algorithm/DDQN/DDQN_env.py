import torch
import numpy as np
from tqdm import tqdm
import matplotlib.pyplot as plt
from DDQN_model import Double_DQN

class DDQN_Env:

    def __init__(self, n_states, n_actions):
        self.device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
        # 输入层神经元数量（状态空间大小）
        self.n_states = n_states
        # 输出层神经元数量（动作空间大小）
        self.n_actions = n_actions

        # 超参数
        self.buffer_size = 500  # 经验池容量
        self.lr = 2e-3  # 学习率
        self.gamma = 0.9  # 折扣因子
        self.epsilon = 0.9  # 探索系数
        self.eps_end = 0.01  # 最低探索系数
        self.eps_dec = 5e-7  # 探索系数衰减率
        self.target_update = 200  # 目标网络的参数的更新频率
        self.batch_size = 32  # 每次训练选取的经验数量
        self.n_hidden = 128  # 隐含层神经元个数
        self.train_min_size = 200  # 经验池超过200后再训练
        self.tau = 0.995  # 目标网络更新权重因子（策略网络权重）
        self.smooth_factor = 0.995  # 最大q值平滑因子（旧值权重）

        # self.return_list = []  # 记录每次迭代的return，即链上的reward之和
        self.max_q_value = 0  # 最大state_value
        # self.max_q_value_list = []  # 保存所有最大的state_value

        # 实例化 Double-DQN
        self.agent = Double_DQN(self.n_states, self.n_hidden, self.n_actions, self.lr, self.gamma, self.epsilon,
                                self.eps_end, self.eps_dec, self.target_update, self.buffer_size, self.train_min_size,
                                self.tau, self.device)

    def getAction(self, state):
        # 状态state时做动作选择，action为动作索引
        is_random, max_q_value, action = self.agent.take_action(state)
        # 平滑处理最大state_value
        self.max_q_value = max_q_value * (1 - self.smooth_factor) + self.max_q_value * self.smooth_factor
        # 保存每次迭代的最大state_value
        # self.max_q_value_list.append(self.max_q_value)
        return is_random,self.max_q_value,action

    def addExperience(self, state, action, reward, next_state, done):
        # 添加经验池
        self.agent.remember(state, action, reward, next_state, done)

    def train(self):
        self.agent.update()


# ------------------------------- #
# （3）绘图
# ------------------------------- #

# plt.subplot(121)
# plt.plot(return_list)
# plt.title('return')
# plt.subplot(122)
# plt.plot(max_q_value_list)
# plt.title('max_q_value')
# plt.show()
