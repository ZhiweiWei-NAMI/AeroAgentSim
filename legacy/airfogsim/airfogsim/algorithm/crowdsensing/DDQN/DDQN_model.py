import os

import torch
from torch import nn
from torch.nn import functional as F
import numpy as np
import collections  # 队列
import random
import logging
from .DDQN_relay_buffer import ReplayBuffer
from .DDQN_network import Net
def soft_update(target, source, t):
    for target_param, source_param in zip(target.parameters(),source.parameters()):
        target_param.data.copy_((1 - t) * target_param.data + t * source_param.data)


def hard_update(target, source):
    for target_param, source_param in zip(target.parameters(),source.parameters()):
        target_param.data.copy_(source_param.data)

# ----------------------------------- #
# 模型构建
# ----------------------------------- #
class Double_DQN:
    # （1）初始化
    def __init__(self, dim_args, train_args):
        # 维度超参数
        self.dim_states = dim_args.dim_states
        self.dim_hiddens = dim_args.dim_hiddens
        self.dim_actions = dim_args.dim_actions

        # 训练超参数
        self.lr = train_args.learning_rate
        self.gamma = train_args.gamma
        self.epsilon = train_args.epsilon
        self.eps_min = train_args.eps_end
        self.eps_dec = train_args.eps_dec
        self.target_update = train_args.target_update
        self.buffer_size = train_args.buffer_size
        self.batch_size=train_args.batch_size
        self.train_min_size = train_args.train_min_size
        self.tau = train_args.tau
        self.device = train_args.device
        # 记录迭代次数
        self.steps_done = 0

        # 实例化训练网络
        self.q_net = Net(dim_states=self.dim_states, dim_hiddens=self.dim_hiddens, dim_actions=self.dim_actions)
        self.q_net.to(self.device)
        # 实例化目标网络
        self.target_q_net = Net(dim_states=self.dim_states, dim_hiddens=self.dim_hiddens, dim_actions=self.dim_actions)
        self.target_q_net.to(self.device)

        # 优化器，更新训练网络的参数
        self.optimizer = torch.optim.Adam(params=self.q_net.parameters(), lr=self.lr)

        # 经验池
        self.memory = ReplayBuffer(buffer_size=self.buffer_size, train_min_size=self.train_min_size)

        # 更新目标网络
        self.update_network_parameters(tau=self.tau)

    # 目标网络更新
    def update_network_parameters(self, tau=None):
        if tau is None:
            tau = self.tau

        for q_target_params, q_params in zip(self.target_q_net.parameters(), self.q_net.parameters()):
            q_target_params.data.copy_(tau * q_params + (1 - tau) * q_target_params)

    def remember(self, state, mask, action, reward, next_state, next_mask, done):
        self.memory.add(state, mask, action, reward, next_state, next_mask, done)

    # 动作选择
    def take_action(self, state, mask):
        # numpy[n_states]-->[1, dim_statess]-->Tensor
        state = torch.Tensor(state[np.newaxis, :]).to(self.device)
        # numpy[n_actions]-->[1, dim_actions]-->Tensor
        mask = torch.Tensor(mask[np.newaxis, :]).bool().to(self.device)

        with torch.no_grad():
            # 获取当前状态下采取各动作的q值
            q_values = self.q_net(state)

        # 非法动作置为最小值
        # masked_q_values = torch.where(mask, q_values, torch.tensor(float('-inf')))
        q_values.copy_(torch.where(mask, q_values, torch.tensor(float('-inf'))))
        # 对每个样本找到最大 Q 值对应的动作的索引
        max_q_value, max_action_index = torch.max(q_values, dim=-1)
        # 如果小于贪婪系数就取最大值reward最大的动作
        if random.random() < self.epsilon:
            is_random = False
            # 获取reward最大值对应的动作索引
            # action = masked_q_values.argmax().item()
            if max_q_value.item() == float('-inf'):
                action = None
            else:
                action = max_action_index.item()
        # 如果大于贪婪系数就随机探索
        else:
            is_random = True
            # 选出可行动作的index并随机选择一个动作
            flatten_mask = mask.flatten()
            valid_action_indices = torch.nonzero(flatten_mask, as_tuple=False).squeeze(1)
            if valid_action_indices.numel() == 0:
                action = None
            else:
                action = valid_action_indices[torch.randint(0, len(valid_action_indices), (1,))].item()

        return is_random, max_q_value, action

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
        states, masks, actions, rewards, next_states, next_masks, dones = self.memory.sample(self.batch_size)

        # 当前状态, array_shape=[b,dim_states]
        states = torch.tensor(states, dtype=torch.float).to(self.device)
        # 当前状态的动作, array_shape=[b]==>[b,1]
        actions = torch.tensor(actions, dtype=torch.int64).view(-1, 1).to(self.device)
        # 动作掩码（布尔张量，True为有效动作，False为无效动作）, array_shape=[b,dim_actions]
        masks = torch.tensor(masks, dtype=torch.bool).to(self.device)
        # 选择当前动作的奖励, array_shape=[b]==>[b,1]
        rewards = torch.tensor(rewards, dtype=torch.float).view(-1, 1).to(self.device)
        # 下一个时刻的状态, array_shape=[b,dim_states]
        next_states = torch.tensor(next_states, dtype=torch.float).to(self.device)
        # 动作掩码（布尔张量，True为有效动作，False为无效动作）, array_shape=[b,dim_actions]
        next_masks = torch.tensor(next_masks, dtype=torch.bool).to(self.device)
        # 是否到达目标, array_shape=[b]==>[b,1]
        dones = torch.tensor(dones, dtype=torch.bool).view(-1, 1).to(self.device)

        with torch.no_grad():
            next_q_values = self.q_net.forward(next_states)
            # next_masked_q_values = torch.where(next_masks, next_q_values, torch.tensor(float('-inf')))
            next_q_values.copy_(torch.where(next_masks, next_q_values, torch.tensor(float('-inf'))))
            # [batch_size] -> [batch_size,index_size(1)]
            max_next_actions = torch.argmax(next_q_values, dim=1).unsqueeze(-1)
            next_q_targets = self.target_q_net.forward(next_states)
            td_q_targets = rewards + self.gamma * next_q_targets.gather(1, max_next_actions) * (1 - dones.int())
        q_values = self.q_net(states).gather(1, actions)

        # 预测值和目标值的均方误差损失(取一个batch的平均值)
        dqn_loss = torch.mean(F.mse_loss(q_values, td_q_targets.detach()))
        # 梯度清零
        self.optimizer.zero_grad()
        # 梯度反传
        dqn_loss.backward()
        # 更新训练网络的参数
        self.optimizer.step()

        # 更新目标网络参数
        if self.steps_done % self.target_update == 0 and self.steps_done > 0:
            soft_update(self.target_q_net, self.q_net, self.tau)
        self.decrement_epsilon()
        # 迭代计数+1
        self.steps_done += 1

        return dqn_loss.detach().item()

    def save_models(self, episode,base_dir,final):
        if final is True:
            file_dir = f"{base_dir}/final"
        else:
            file_dir=f"{base_dir}/episode_{episode}"
        model_type = "final" if final is True else "checkpoint"

        if not os.path.exists(file_dir):
            os.makedirs(file_dir)
        logging.basicConfig(
            level=logging.INFO,  # 日志级别
            format='%(asctime)s [%(levelname)s] %(message)s',  # 日志格式
            datefmt='%Y-%m-%d %H:%M:%S',  # 时间格式
            filename=f'{file_dir}/model.log',  # 日志文件名
            filemode='a'  # 追加模式写入日志文件
        )

        self.q_net.save_model(file_dir + f'/DDQN_Q_net.pth')
        print(f'Saving {model_type} episode_{episode} DDQN_Q_net network successfully!')
        logging.info(f'Saving {model_type} episode_{episode} DDQN_Q_net network successfully!')
        self.target_q_net.save_model(file_dir + f'/DDQN_Q_target.pth')
        print(f'Saving {model_type} episode_{episode} DDQN_Q_target network successfully!')
        logging.info(f'Saving {model_type} episode_{episode} DDQN_Q_target network successfully!')
        self.memory.save(file_dir+f'/DDQN_memory.pkl')
        print(f'Saving {model_type} episode_{episode} DDQN memory successfully!')
        logging.info(f'Saving {model_type} episode_{episode} DDQN memory successfully!')

    def load_models(self, episode,base_dir,final):
        if final is True:
            file_dir = f"{base_dir}/final"
        else:
            file_dir=f"{base_dir}/episode_{episode}"
        model_type = "final" if final is True else "checkpoint"

        logging.basicConfig(
            level=logging.INFO,  # 日志级别
            format='%(asctime)s [%(levelname)s] %(message)s',  # 日志格式
            datefmt='%Y-%m-%d %H:%M:%S',  # 时间格式
            filename=f'{file_dir}/model.log',  # 日志文件名
            filemode='a'  # 追加模式写入日志文件
        )

        self.q_net.load_model(file_dir + f'/DDQN_Q_net.pth')
        print(f'Loading {model_type} episode_{episode} DDQN_Q_net network successfully!')
        logging.info(f'Loading {model_type} episode_{episode} DDQN_Q_net network successfully!')
        self.target_q_net.load_model(file_dir + f'/DDQN_Q_target.pth')
        print(f'Loading {model_type} episode_{episode} DDQN_Q_target network successfully!')
        logging.info(f'Loading {model_type} episode_{episode} DDQN_Q_target network successfully!')
        self.memory.load(file_dir+f'/DDQN_memory.pkl')
        print(f'Loading {model_type} episode_{episode} DDQN memory successfully!')
        logging.info(f'Loading {model_type} episode_{episode} DDQN memory successfully!')
