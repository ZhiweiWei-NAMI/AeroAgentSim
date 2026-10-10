import os

import torch
from torch import nn
from torch.nn import functional as F
import numpy as np
import collections  # 队列
import random


# ----------------------------------- #
# （1）经验回放池
# ----------------------------------- #

class ReplayBuffer:
    def __init__(self, buffer_size, train_min_size):
        # 创建一个队列，先进先出，队列长度不变
        self.buffer = collections.deque(maxlen=buffer_size)
        self.train_min_size = train_min_size

    # 填充经验池
    def add(self, state, action, reward, next_state, done):
        self.buffer.append((state, action, reward, next_state, done))

    # 随机采样batch组样本数据
    def sample(self, batch_size):
        transitions = random.sample(self.buffer, batch_size)
        # 分别取出这些数据，*获取list中的所有值
        state, action, reward, next_state, done = zip(*transitions)
        # 将state变成数组，后面方便计算
        return np.array(state), action, reward, np.array(next_state), done

    # 队列的长度
    def size(self):
        return len(self.buffer)

    def ready(self):
        return len(self.buffer) > self.train_min_size


# ----------------------------------- #
# （2）构造网络，训练网络和目标网络共用该结构
# ----------------------------------- #

class Net(nn.Module):
    def __init__(self, n_states, n_hiddens, n_actions):
        super(Net, self).__init__()
        # 有两个隐含层
        self.fc1 = nn.Linear(n_states, n_hiddens)
        self.fc2 = nn.Linear(n_hiddens, n_hiddens)
        self.fc3 = nn.Linear(n_hiddens, n_actions)

    # 前向传播
    def forward(self, x):
        x = self.fc1(x)  # [b,n_states]-->[b,n_hiddens]
        x = self.fc2(x)  # [b,n_hiddens]-->[b,n_hiddens]
        x = self.fc3(x)  # [b,n_hiddens]-->[b,n_actions]
        return x

    def save_checkpoint(self, file_dir):
        torch.save(self.state_dict(), file_dir, _use_new_zipfile_serialization=False)

    def load_checkpoint(self, file_dir):
        self.load_state_dict(torch.load(file_dir))


# ----------------------------------- #
# （3）模型构建
# ----------------------------------- #

class Double_DQN:
    # （1）初始化
    def __init__(self, n_states, n_hiddens, n_actions,
                 learning_rate, gamma, epsilon, eps_end, eps_dec,
                 target_update, buffer_size,train_min_size, tau, device):
        # 属性分配
        self.n_states = n_states
        self.n_hiddens = n_hiddens
        self.n_actions = n_actions
        self.learning_rate = learning_rate
        self.gamma = gamma
        self.epsilon = epsilon
        self.eps_min = eps_end
        self.eps_dec = eps_dec
        self.target_update = target_update
        self.buffer_size = buffer_size
        self.train_min_size=train_min_size
        self.tau = tau
        self.device = device
        # 记录迭代次数
        self.count = 0

        # 实例化训练网络
        self.q_net = Net(n_states=self.n_states, n_hiddens=self.n_hiddens, n_actions=self.n_actions)
        # 实例化目标网络
        self.target_q_net = Net(n_states=self.n_states, n_hiddens=self.n_hiddens, n_actions=self.n_actions)

        # 优化器，更新训练网络的参数
        self.optimizer = torch.optim.Adam(params=self.q_net.parameters(), lr=self.learning_rate)

        # 经验池
        self.memory = ReplayBuffer(buffer_size=self.buffer_size,train_min_size=self.tr)

        # 更新目标网络
        self.update_network_parameters(tau=self.tau)

        # 模型文件路径
        self.model_file_dir = "./model/"

    # 目标网络更新
    def update_network_parameters(self, tau=None):
        if tau is None:
            tau = self.tau

        for q_target_params, q_params in zip(self.target_q_net.parameters(), self.q_net.parameters()):
            q_target_params.data.copy_(tau * q_params + (1 - tau) * q_target_params)

    def remember(self, state, action, reward, next_state, done):
        self.memory.add(state, action, reward, next_state, done)

    # 动作选择
    def take_action(self, state):
        # numpy[n_states]-->[1, n_states]-->Tensor
        state = torch.Tensor(state[np.newaxis, :])
        # 获取当前状态下采取各动作的reward
        actions_value = self.q_net(state)
        # 对每个样本找到最大 Q 值对应的动作的索引
        max_q_value, max_action_index = torch.max(actions_value, dim=-1)
        # 如果小于贪婪系数就取最大值reward最大的动作
        if np.random.random() < self.epsilon:
            is_random = False
            # 获取reward最大值对应的动作索引
            action = actions_value.argmax().item()
        # 如果大于贪婪系数就随即探索
        else:
            is_random = True
            action = np.random.randint(self.n_actions)
        return is_random,max_q_value,action

    def decrement_epsilon(self):
        if self.epsilon > self.eps_min:
            self.epsilon = self.epsilon - self.eps_dec
        else:
            self.epsilon = self.eps_min

    # 获取每个状态对应的最大的state_value
    # def max_q_value(self, state):
    #     # list-->tensor[3]-->[1,3]
    #     state = torch.tensor(state, dtype=torch.float).view(1, -1)
    #     # 当前状态对应的每个动作的reward的最大值 [1,3]-->[1,11]-->int
    #     max_q = self.q_net.forward(state).max().item()
    #     return max_q

    # 网络训练
    def update(self):
        if not self.memory.ready():
            return
        states, actions, rewards, next_states, terminals = self.memory.sample()

        # 当前状态，array_shape=[b,4]
        states = torch.tensor(states, dtype=torch.float)
        # 当前状态的动作，tuple_shape=[b]==>[b,1]
        actions = torch.tensor(actions, dtype=torch.int64).view(-1, 1)
        # 选择当前动作的奖励, tuple_shape=[b]==>[b,1]
        rewards = torch.tensor(rewards, dtype=torch.float).view(-1, 1)
        # 下一个时刻的状态array_shape=[b,4]
        next_states = torch.tensor(next_states, dtype=torch.float)
        # 是否到达目标 tuple_shape=[b,1]
        dones = torch.tensor(terminals, dtype=torch.float).view(-1, 1)

        with torch.no_grad():
            next_q_values = self.q_net.forward(next_states)
            max_next_actions = torch.argmax(next_q_values, dim=-1)
            next_target_q_values = self.target_q_net.forward(next_states)
            q_targets = rewards + self.gamma * next_target_q_values.gather(1, max_next_actions) * (1 - dones)
        q_values = self.q_net(states).gather(1, actions)

        # 预测值和目标值的均方误差损失(取一个batch的平均值)
        dqn_loss = torch.mean(F.mse_loss(q_values, q_targets.detach()))
        # 梯度清零
        self.optimizer.zero_grad()
        # 梯度反传
        dqn_loss.backward()
        # 更新训练网络的参数
        self.optimizer.step()

        # 更新目标网络参数
        if self.count % self.target_update == 0:
            self.update_network_parameters(self.tau)
        self.decrement_epsilon()
        # 迭代计数+1
        self.count += 1

    def save_models(self, episode):
        if not os.path.exists(self.model_file_dir):
            os.makedirs(self.model_file_dir)

        self.q_net.save_model(self.model_file_dir + 'DDQN_Q_net_{}.pth'.format(episode))
        print('Saving Q_net network successfully!')
        self.target_q_net.save_model(self.model_file_dir + 'DDQN_Q_target_{}.pth'.format(episode))
        print('Saving Q_target network successfully!')

    def load_models(self, episode):
        if not os.path.exists(self.model_file_dir):
            os.makedirs(self.model_file_dir)

        self.q_net.load_model(self.model_file_dir + 'DDQN_Q_net_{}.pth'.format(episode))
        print('Loading Q_net network successfully!')
        self.target_q_net.load_model(self.model_file_dir + 'DDQN_Q_target_{}.pth'.format(episode))
        print('Loading Q_target network successfully!')
