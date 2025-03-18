import argparse
import json
import os
import pickle
import random

from .MAPPO_network import Critic, Actor
import torch
from copy import deepcopy
from .MAPPO_memory import Memory
from torch.optim import Adam
from torch.optim import SGD
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

def compute_advantage(gamma, gae_lambda, td_error,device):
    # 用通用优势估计（Generalized Advantage Estimator, GAE）构建时序优势
    td_error = td_error.detach().cpu().numpy()
    advantage_list = []
    advantage = 0.0
    for delta in td_error[::-1]:
        advantage = gamma * gae_lambda * advantage + delta
        advantage_list.append(advantage)
    advantage_list.reverse()
    advantage_array = np.array(advantage_list)
    return torch.tensor(advantage_array, dtype=torch.float,device=device)

def normalize_advantage(adv: torch.Tensor) -> torch.Tensor:
    """
    对批量优势进行归一化（减去均值，除以标准差）
    Args:
        adv: 原始优势值，形状 [batch_size]
    Returns:
        adv_normalized: 归一化后的优势值，已阻断梯度
    """
    adv_mean = adv.mean()                # 计算均值
    adv_std = adv.std()                  # 计算标准差
    adv_normalized = (adv - adv_mean) / (adv_std + 1e-8)  # 标准化（防止除零）
    return adv_normalized.detach()       # 分离计算图

class MinLRScheduler(torch.optim.lr_scheduler.LRScheduler):
    def __init__(self, optimizer, min_lr=1e-5, step_size=10, gamma=0.99, last_epoch=-1):
        self.min_lr = min_lr
        self.step_size = step_size
        self.gamma = gamma
        super(MinLRScheduler, self).__init__(optimizer, last_epoch)

    def get_lr(self):
        # 获取当前学习率
        lr = self.base_lrs[0]

        # 计算当前调度器下的学习率
        new_lr = lr * (self.gamma ** (self.last_epoch // self.step_size))

        # 限制最小学习率
        return [max(new_lr, self.min_lr)] # 返回的是一个列表

class MAPPO:
    def __init__(self, dim_args, train_args):
        # 维度超参数
        self.n_agents = dim_args.n_agents
        self.dim_hiddens = dim_args.dim_hiddens
        self.dim_obs = dim_args.dim_observation
        self.dim_act = dim_args.dim_action


        # 训练超参数
        self.lr = train_args.learning_rate
        self.lr_min=train_args.learning_rate_min
        self.lr_gamma = train_args.learning_rate_gamma
        self.step_size = train_args.step_size
        self.var = [train_args.var for i in range(self.n_agents)]
        self.var_min = train_args.var_end
        self.var_dec = train_args.var_dec
        self.gamma = train_args.gamma
        self.gae_lambda = train_args.gae_lambda
        self.epsilon = train_args.epsilon
        self.epoch = train_args.epoch
        self.device = train_args.device

        # 实例化策略网络*n
        self.actors = Actor(self.dim_obs,self.dim_act, self.dim_hiddens)
        self.old_actors = deepcopy(self.actors) # 旧行为策略
        # 实例化价值网络*n
        self.critics = Critic(self.n_agents, self.dim_obs, self.dim_act, self.dim_hiddens)
        self.old_critics = deepcopy(self.critics) # 旧评价网络
        # 策略训练网络优化器
        self.actor_optimizer = Adam(self.actors.parameters(), lr=self.lr,eps=1e-5)
        self.actor_scheduler = MinLRScheduler(optimizer=self.actor_optimizer,min_lr=self.lr_min, step_size=self.step_size, gamma=self.lr_gamma)
        # 目标训练网络优化器
        self.critic_optimizer = Adam(self.critics.parameters(), lr=self.lr,eps=1e-5)
        self.critic_scheduler = MinLRScheduler(optimizer=self.critic_optimizer,min_lr=self.lr_min, step_size=self.step_size, gamma=self.lr_gamma)

        # 经验池
        self.memory = [Memory() for i in range(self.n_agents)] # 每个agent构建独立经验池


        self.actors.to(self.device)
        self.old_actors.to(self.device)
        self.critics.to(self.device)
        self.old_critics.to(self.device)

        # 记录迭代次数
        self.steps_done = 0
        # 记录最大完成率
        self.best_succ_ratio=0

    def remember(self,agent_id, state, action, reward, next_state):
        self.memory[agent_id].add(state, action, reward, next_state)

    def take_action(self,agents_state):
        # agents_state: [n_agents, state_dim]
        agents_state=torch.tensor(agents_state,dtype=torch.float).to(self.device)
        actions=[]
        with torch.no_grad():
            for i in range(self.n_agents):
                state = agents_state[i, :].detach()
                action_probabilities = self.old_actors(state.unsqueeze(0))

                distribution = torch.distributions.Categorical(action_probabilities)
                action = distribution.sample().item()
                # if random.random() > self.var[i]:
                #     is_random=False
                #     distribution = torch.distributions.Categorical(action_probabilities)
                #     action = distribution.sample().item()
                #     # max_probability, max_action_index = torch.max(action_probabilities, dim=-1)
                #     # action = max_action_index.item()
                # else:
                #     # # 使用 torch.multinomial 随机选择一个动作的索引
                #     # action_idx = torch.multinomial(action_probabilities, 1)
                #     # # 获取选中的动作的概率值
                #     # probability = action_probabilities[action_idx]
                #
                #     is_random = True
                #     # 获取动作数量
                #     num_actions = action_probabilities.size(-1)
                #     # 使用 torch.randint 随机选取一个动作的索引
                #     action_idx = torch.randint(0, num_actions, (1,))
                #     action = action_idx.item()


                print("action_probabilities")
                print(action_probabilities)
                # print('is_random')
                # print(is_random)
                print('action')
                print(action)

                actions.append(action)
                # self.decrement_var(i)
        self.steps_done += 1


        return actions

    def decrement_var(self,idx):
        if self.var[idx] > self.var_min:
            self.var[idx] = self.var[idx] - self.var_dec
        else:
            self.var[idx] = self.var_min

    def update(self):
        c_loss = [[] for _ in range(self.n_agents)]
        a_loss = [[] for _ in range(self.n_agents)]

        for agent_idx in range(self.n_agents):
            # 同一时间的全局state,action,next_state,reward
            states, actions, rewards, next_states= self.memory[agent_idx].get_all()
            # 转换为 PyTorch 张量
            # numpy[batch_size,n_agents, state_dim]-->Tensor[batch_size,n_agents, state_dim]
            states = torch.tensor(states, dtype=torch.float).to(self.device)
            # numpy[batch_size, action_dim]-->Tensor[batch_size, action_dim]
            actions = torch.tensor(actions, dtype=torch.long).to(self.device)
            # numpy[batch_size]-->Tensor[batch_size,1]
            rewards = torch.tensor(rewards, dtype=torch.float).unsqueeze(1).to(self.device)
            # numpy[batch_size, n_agents, state_dim]-->Tensor[batch_size, n_agents, state_dim]
            next_states = torch.tensor(next_states, dtype=torch.float).to(self.device)

            whole_states = states.view(states.shape[0], -1)
            whole_next_states = states.view(next_states.shape[0], -1)
            with torch.no_grad():
                td_targets = rewards +  self.gamma*self.old_critics(whole_next_states)
                td_errors = td_targets - self.old_critics(whole_states)
                adv = compute_advantage(self.gamma, self.gae_lambda, td_errors, self.device)
                adv = normalize_advantage(adv)
                old_action_probabilities = self.old_actors(states[:, agent_idx, :])  # softmax 输出的概率
                old_dis = torch.distributions.Categorical(old_action_probabilities)  # Categorical分布
                log_prob_old = old_dis.log_prob(actions)  # 获取旧策略下的动作log概率

            for _ in range(self.epoch):
                # 更新actor
                new_action_probabilities = self.actors(states[:, agent_idx, :])  # softmax 输出的概率
                new_dis = torch.distributions.Categorical(new_action_probabilities)  # Categorical分布
                log_prob_new = new_dis.log_prob(actions)  # 获取新策略下的动作log概率
                ratio = torch.exp(log_prob_new - log_prob_old)  # 计算新旧策略的概率比
                L1 = ratio * adv
                L2 = torch.clamp(ratio, 1 - self.epsilon, 1 + self.epsilon) * adv
                loss_actor = -torch.min(L1, L2).mean()
                self.actor_optimizer.zero_grad()
                loss_actor.backward()
                self.actor_optimizer.step()
                self.actor_scheduler.step()

                # 2.更新critic，next_state估值使用旧价值网络
                q_values = self.critics(whole_states)
                loss_critic = F.mse_loss(q_values,td_targets.detach()).mean()
                self.critic_optimizer.zero_grad()
                loss_critic.backward()
                self.critic_optimizer.step()
                self.critic_scheduler.step()

                a_loss[agent_idx].append(loss_actor.detach().item())
                c_loss[agent_idx].append(loss_critic.detach().item())

        hard_update( self.old_critics,self.critics)
        hard_update(self.old_actors,self.actors)
        for i in range(self.n_agents):
            self.memory[i].clear()

        return a_loss,c_loss


    def save_models(self, episode, base_dir,final,succ_ratio):
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

        self.critics.save_model(file_dir + f'/MAPPO_critics.pth')
        print(f'Saving {model_type} episode_{episode} MAPPO_critics network successfully!')
        logging.info(f'Saving {model_type} episode_{episode} MAPPO_critics network successfully!')
        self.actors.save_model(file_dir + f'/MAPPO_actors.pth')
        print(f'Saving {model_type} episode_{episode} MAPPO_actors network successfully!')
        logging.info(f'Saving {model_type} episode_{episode} MAPPO_actors network successfully!')
        self.old_critics.save_model(file_dir + f'/MAPPO_old_critics.pth')
        print(f'Saving {model_type} episode_{episode} MAPPO_old_critics network successfully!')
        logging.info(f'Saving {model_type} episode_{episode} MAPPO_old_critics network successfully!')
        self.old_actors.save_model(file_dir + f'/MAPPO_old_actors.pth')
        print(f'Saving {model_type} episode_{episode} MAPPO_old_actors network successfully!')
        logging.info(f'Saving {model_type} episode_{episode} MAPPO_old_actors network successfully!')

        if succ_ratio > self.best_succ_ratio:
            self.best_succ_ratio = succ_ratio
            best_dir = f"{base_dir}/best"
            if not os.path.exists(best_dir):
                os.makedirs(best_dir)
            self.critics.save_model(base_dir + f'/MAPPO_critics.pth')
            self.actors.save_model(base_dir + f'/MAPPO_actors.pth')
            data = {
                'episode': episode,
                'best_succ_ratio': self.best_succ_ratio,
            }
            with open(best_dir + f'/params.json', 'w') as f:
                json.dump(data, f, indent=4)  # indent=4 美化 JSON 格式

        params = {
            'var': self.var,
            'actor_lr_last_epoch': self.actor_scheduler.last_epoch,
            'critic_lr_last_epoch': self.critic_scheduler.last_epoch,
            'steps_done': self.steps_done,
        }
        print(f'params: {params}')
        with open(file_dir + f'/params.json', 'w') as f:
            json.dump(params, f, indent=4)  # indent=4 美化 JSON 格式
        print(f'Saving {model_type} episode_{episode} params successfully!')
        logging.info(f'Saving {model_type} episode_{episode} params successfully!')


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


        self.critics.load_model(file_dir + f'/MAPPO_critics.pth')
        print(f'Loading {model_type} episode_{episode} MAPPO_critics network successfully!')
        logging.info(f'Loading {model_type} episode_{episode} MAPPO_critics network successfully!')
        self.actors.load_model(file_dir + f'/MAPPO_actors.pth')
        print(f'Loading {model_type} episode_{episode} MAPPO_actors network successfully!')
        logging.info(f'Loading {model_type} episode_{episode} MAPPO_actors network successfully!')
        self.old_critics.load_model(file_dir + f'/MAPPO_old_critics.pth')
        print(f'Loading {model_type} episode_{episode} MAPPO_old_critics network successfully!')
        logging.info(f'Loading {model_type} episode_{episode} MAPPO_old_critics network successfully!')
        self.old_actors.load_model(file_dir + f'/MAPPO_old_actors.pth')
        print(f'Loading {model_type} episode_{episode} MAPPO_old_actors network successfully!')
        logging.info(f'Loading {model_type} episode_{episode} MAPPO_old_actors network successfully!')

        best_dir = f"{base_dir}/best"
        with open(best_dir + f'/params.json', "r") as f:
            data = json.load(f)
            self.best_succ_ratio = data['best_succ_ratio']

        with open(file_dir+f'/params.json', 'r') as f:
            params=json.load(f)
            print(f'params: {params}')
            var=params['var']
            actor_lr_last_epoch=params['actor_lr_last_epoch']
            critic_lr_last_epoch = params['critic_lr_last_epoch']
            steps_done=params['steps_done']
            self.var=var
            self.steps_done=steps_done
            self.actor_scheduler=MinLRScheduler(optimizer=self.actor_optimizer,min_lr=self.lr_min, step_size=self.step_size, gamma=self.lr_gamma,last_epoch=actor_lr_last_epoch)
            self.critic_scheduler=MinLRScheduler(optimizer=self.critic_optimizer,min_lr=self.lr_min, step_size=self.step_size, gamma=self.lr_gamma,last_epoch=critic_lr_last_epoch)

        print(f'Loading {model_type} episode_{episode} params successfully!')
        logging.info(f'Loading {model_type} episode_{episode} params successfully!')