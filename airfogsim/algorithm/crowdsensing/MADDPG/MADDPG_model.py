import argparse
import os

from .MADDPG_network import Critic, Actor
import torch
from copy import deepcopy
from .MADDPG_relay_buffer import ReplayBuffer
from torch.optim import Adam
from torch.nn import functional as F
import torch.nn as nn
import numpy as np
import logging


def soft_update(target, source, tau):
    for target_param, source_param in zip(target.parameters(),source.parameters()):
        target_param.data.copy_((1 - tau) * target_param.data + tau * source_param.data)


def hard_update(target, source):
    for target_param, source_param in zip(target.parameters(),source.parameters()):
        target_param.data.copy_(source_param.data)


class MADDPG:
    def __init__(self, dim_args, train_args):
        # 维度超参数
        self.n_agents = dim_args.n_agents
        self.dim_obs = dim_args.dim_observation
        self.dim_act = dim_args.dim_action
        self.dim_hiddens = dim_args.dim_hiddens

        # 训练超参数
        self.buffer_size = train_args.buffer_size
        self.lr = train_args.learning_rate
        self.gamma = train_args.gamma
        self.var = [train_args.var for i in range(self.n_agents)]  # 动作探索随机噪声
        self.var_end = train_args.var_end
        self.var_dec = train_args.var_dec
        self.target_update = train_args.target_update
        self.batch_size = train_args.batch_size
        self.train_min_size = train_args.train_min_size
        self.tau = train_args.tau
        self.device = train_args.device

        # 实例化策略训练网络*n
        self.actors = [Actor(self.dim_obs, self.dim_act, self.dim_hiddens) for i in range(self.n_agents)]
        # 实例化价值训练网络*n
        self.critics = [Critic(self.n_agents, self.dim_obs, self.dim_act, self.dim_hiddens) for i in
                        range(self.n_agents)]
        # 实例化目标策略网络*n
        self.actors_target = deepcopy(self.actors)
        # 实例化目标价值网络*n
        self.critics_target = deepcopy(self.critics)
        # 策略训练网络优化器
        self.critic_optimizer = [Adam(x.parameters(), lr=self.lr) for x in self.critics]
        # 目标训练网络优化器
        self.actor_optimizer = [Adam(x.parameters(), lr=self.lr) for x in self.actors]

        # 经验池
        self.memory = ReplayBuffer(buffer_size=self.buffer_size, train_min_size=self.train_min_size)

        for x in self.actors:
            x.to(self.device)
        for x in self.critics:
            x.to(self.device)
        for x in self.actors_target:
            x.to(self.device)
        for x in self.critics_target:
            x.to(self.device)

        # 记录迭代次数
        self.steps_done = 0

    def remember(self, state, action, reward, next_state):
        self.memory.add(state, action, reward, next_state)

    def take_action(self, agents_state):
        # agents_state: [n_agents, state_dim]
        agents_state=torch.tensor(agents_state,dtype=torch.float).to(self.device)
        actions=[]
        with torch.no_grad():
            for i in range(self.n_agents):
                self.actors[i].eval()
                state = agents_state[i, :]
                action = self.actors[i](state.unsqueeze(0)).squeeze(0) # 只压缩batch维度
                noise=(torch.from_numpy((np.random.rand(self.dim_act) * 2 - 1) )* self.var[i]).to(self.device)
                action +=noise   # 添加[-1,1]随机噪声
                action = torch.clamp(action, 0, 1)  # 限制动作范围在[0,1]
                actions.append(action)
                if self.var[i] > self.var_end:
                    self.var[i] -= self.var_dec # 噪声衰减
        self.steps_done += 1


        return actions

    def update(self):
        if not self.memory.ready():
            return  None, None

        c_loss=[]
        a_loss=[]
        for agent in range(self.n_agents):
            # 同一时间的全局state,action,next_state,reward
            states, actions, rewards, next_states= self.memory.sample(self.batch_size)
            # 转换为 PyTorch 张量
            # numpy[batch_size, n_agents, state_dim]-->Tensor[batch_size, n_agents, state_dim]
            states = torch.tensor(states, dtype=torch.float).to(self.device)
            # numpy[batch_size, n_agents, action_dim]-->Tensor[batch_size, n_agents, action_dim]
            actions = torch.tensor(actions, dtype=torch.float).to(self.device)
            # numpy[batch_size, n_agents]-->Tensor[batch_size, n_agents]
            rewards = torch.tensor(rewards, dtype=torch.float).to(self.device)
            # numpy[batch_size, n_agents, state_dim]-->Tensor[batch_size, n_agents, state_dim]
            next_states = torch.tensor(next_states, dtype=torch.float).to(self.device)

            whole_states = states.view(self.batch_size, -1)
            whole_actions = actions.view(self.batch_size, -1)

            # 1.Critic 更新(agent_i)
            q_values_i = self.critics[agent](whole_states, whole_actions)  # 实际采取的动作
            with torch.no_grad():
                next_whole_states = next_states.view(self.batch_size, -1)
                # all agents next actions
                next_whole_actions = [
                    self.actors_target[i](next_states[:, i, :]) for i in range(self.n_agents)
                ]
                next_whole_actions = torch.cat(next_whole_actions, dim=-1)
                q_targets_i = self.critics_target[agent](next_whole_states, next_whole_actions)
                td_targets_i = rewards[:, agent].unsqueeze(-1) + self.gamma * q_targets_i


            critic_loss = F.mse_loss(q_values_i, td_targets_i.detach())
            self.critic_optimizer[agent].zero_grad()
            critic_loss.backward()
            self.critic_optimizer[agent].step()

            # 2.Actor 更新(agent_i)
            state_i = states[:, agent, :]
            online_action_i = self.actors[agent](state_i)  # 非经验里state_i对应的实际动作，是重新采样的
            online_action = actions.clone()
            online_action[:, agent, :] = online_action_i
            whole_online_actions = online_action.view(self.batch_size, -1)

            # loss采用-q，评价越好loss越小
            actor_loss = -self.critics[agent](whole_states, whole_online_actions).mean()
            self.actor_optimizer[agent].zero_grad()
            actor_loss.backward()
            self.actor_optimizer[agent].step()

            c_loss.append(critic_loss.detach().item())
            a_loss.append(actor_loss.detach().item())

        if self.steps_done % self.target_update == 0 and self.steps_done > 0:
            for i in range(self.n_agents):
                soft_update(self.critics_target[i], self.critics[i], self.tau)
                soft_update(self.actors_target[i], self.actors[i], self.tau)

        return a_loss,c_loss


    def save_models(self, episode, base_dir,final):
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

        for i in range(self.n_agents):
            self.critics_target[i].save_model(file_dir + f'/MADDPG_critics_target_{i}.pth')
            print(f'Saving {model_type} episode_{episode} MADDPG_critics_target_{i} network successfully!')
            logging.info(f'Saving {model_type} episode_{episode} MADDPG_critics_target_{i} network successfully!')
            self.actors_target[i].save_model(file_dir + f'/MADDPG_actors_target_{i}.pth')
            print(f'Saving {model_type} episode_{episode} MADDPG_actors_target_{i} network successfully!')
            logging.info(f'Saving {model_type} episode_{episode} MADDPG_actors_target_{i} network successfully!')
            self.critics[i].save_model(file_dir + f'/MADDPG_critics_{i}.pth')
            print(f'Saving {model_type} episode_{episode} MADDPG_critics_{i} network successfully!')
            logging.info(f'Saving {model_type} episode_{episode} MADDPG_critics_{i} network successfully!')
            self.actors[i].save_model(file_dir + f'/MADDPG_actors_{i}.pth')
            print(f'Saving {model_type} episode_{episode} MADDPG_actors_{i} network successfully!')
            logging.info(f'Saving {model_type} episode_{episode} MADDPG_actors_{i} network successfully!')

        self.memory.save(file_dir+f'/MADDPG_memory.pkl')
        print(f'Saving {model_type} episode_{episode} MADDPG memory successfully!')
        logging.info(f'Saving {model_type} episode_{episode} MADDPG memory successfully!')


    def load_models(self, episode, base_dir,final):
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

        for i in range(self.n_agents):
            self.critics_target[i].load_model(file_dir + f'/MADDPG_critics_target_{i}.pth')
            print(f'Loading {model_type} episode_{episode} MADDPG_critics_target_{i} network successfully!')
            logging.info(f'Loading {model_type} episode_{episode} MADDPG_critics_target_{i} network successfully!')
            self.actors_target[i].load_model(file_dir + f'/MADDPG_actors_target_{i}.pth')
            print(f'Loading {model_type} episode_{episode} MADDPG_actors_target_{i} network successfully!')
            logging.info(f'Loading {model_type} episode_{episode} MADDPG_actors_target_{i} network successfully!')
            self.critics[i].load_model(file_dir + f'/MADDPG_critics_{i}.pth')
            print(f'Loading {model_type} episode_{episode} MADDPG_critics_{i} network successfully!')
            logging.info(f'Loading {model_type} episode_{episode} MADDPG_critics_{i} network successfully!')
            self.actors[i].load_model(file_dir + f'/MADDPG_actors_{i}.pth')
            print(f'Loading {model_type} episode_{episode} MADDPG_actors_{i} network successfully!')
            logging.info(f'Loading {model_type} episode_{episode} MADDPG_actors_{i} network successfully!')

        self.memory.load(file_dir+f'/MADDPG_memory.pkl')
        print('Loading MADDPG memory successfully!')
        logging.info('Loading MADDPG memory successfully!')
