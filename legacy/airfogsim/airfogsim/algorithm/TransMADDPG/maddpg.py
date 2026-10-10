from .trans_network import TransformerActor, TransformerCritic
import torch
import torch.nn as nn
import numpy as np
from ..replay_buffer import ReplayBuffer
import os

# 线程数量=4
torch.set_num_threads(4)


class MADDPG_Agent:
    def __init__(self, args, n_agents):  # 添加agent_id参数
        self.args = args
        self.d_node = args.d_node
        self.d_task = args.d_task
        self.max_tasks = args.max_tasks
        self.m1 = args.m1
        self.m2 = args.m2
        self.d_model = args.d_model
        self.nhead = args.nhead
        self.num_layers = args.num_layers
        self.device = args.device
        self.tau = args.tau
        self.n_agents = n_agents  # 添加n_agents参数

        # actor网络和critic网络
        self.actor_network = TransformerActor(self.d_node, self.d_task, self.max_tasks, self.m1, self.m2, self.d_model,
                                              self.nhead, self.num_layers)
        self.critic_network = TransformerCritic(self.n_agents, self.d_node,
                                                      self.d_task,
                                                      self.max_tasks,
                                                      self.m1, 
                                                      self.m2, 
                                                      self.d_model, self.nhead,
                                                      self.num_layers).to(self.device)
        self.actor_network.to(self.device)
        self.critic_network.to(self.device)

        # target actor网络和target critic网络
        self.target_actor_network = TransformerActor(self.d_node, self.d_task, self.max_tasks, self.m1, self.m2,
                                                    self.d_model, self.nhead, self.num_layers)
        self.target_critic_network = TransformerCritic(self.n_agents, self.d_node,
                                                      self.d_task,
                                                      self.max_tasks,
                                                      self.m1, 
                                                      self.m2, 
                                                      self.d_model, self.nhead,
                                                      self.num_layers).to(self.device)
        self.target_actor_network.to(self.device)
        self.target_critic_network.to(self.device)
        self.target_actor_network.load_state_dict(self.actor_network.state_dict())
        self.target_critic_network.load_state_dict(self.critic_network.state_dict())

        self.gamma = args.gamma

        # actor和critic的优化器
        self.actor_optimizer = torch.optim.Adam(self.actor_network.parameters(), lr=args.actor_lr)
        self.critic_optimizer = torch.optim.Adam(self.critic_network.parameters(), lr=args.critic_lr)
        self.criterion = nn.MSELoss()  # 使用均方误差损失函数
        self.replay_buffer = ReplayBuffer(args.replay_buffer_capacity)
        self.update_cnt = 0

    def saveModel(self):
        torch.save(self.actor_network.state_dict(),
                   os.path.join(self.args.model_dir, f'actor_model_{self.update_cnt}.pth'))
        torch.save(self.critic_network.state_dict(),
                   os.path.join(self.args.model_dir, f'critic_model_{self.update_cnt}.pth'))

    def select_action(self, task_node, task_data, compute_node, task_mask, compute_node_mask):
        task_node = torch.FloatTensor(task_node).to(self.device).unsqueeze(0)
        task_data = torch.FloatTensor(task_data).to(self.device).unsqueeze(0)
        compute_node = torch.FloatTensor(compute_node).to(self.device).unsqueeze(0)
        task_mask = torch.FloatTensor(task_mask).to(self.device).unsqueeze(0)
        compute_node_mask = torch.FloatTensor(compute_node_mask).to(self.device).unsqueeze(0)

        with torch.no_grad():
            # 使用actor网络选择动作
            actions = self.actor_network(task_node, task_data, compute_node, task_mask, compute_node_mask).squeeze(0)
            actions = actions.cpu().data.numpy()  # [max_tasks, m2]
        # 如果包含nan
        if np.isnan(actions).any():
            print('nan')
        # add noise
        # actions += np.random.randn(*actions.shape) * self.args.epsilon
        # clip 到0,1
        actions = np.clip(actions, 0, 1)
        # 全是0的行置为1
        actions[actions.sum(axis=1) == 0] = 1
        # 重采样
        actions = actions / np.sum(actions, axis=1, keepdims=True)
        # 基于概率重采样
        resampled_actions = np.array([np.random.choice(len(action), p=action) for action in actions])
        return resampled_actions

    def add_experience(self, obs, action, reward, next_obs, done):
        # 简化经验存储，直接存储obs、action、reward、next_obs和done
        self.replay_buffer.add((obs, action, reward, next_obs, done))

    def update(self, writer):  # 添加agents参数
        # if test, return
        if self.args.mode == 'test':
            return
        batch_size = self.args.batch_size
        if self.replay_buffer.size() < batch_size:
            return
        self.update_cnt += 1
        if self.update_cnt % self.args.replay_buffer_update_freq != 0:
            return
        if self.update_cnt % self.args.save_model_freq == 0:
            self.saveModel()
        experiences = self.replay_buffer.sample(batch_size)
        # 从经验中提取obs、action、reward、next_obs和done, 第一个维度是batch_size, 第二个维度是各个分量，第三个维度是n_agents
        obs_n, action_n, reward_n, next_obs_n, done_n = zip(*experiences)
        # 将obs_n转换为tensor
        task_node_n_agent = torch.stack([torch.FloatTensor(obs_n_agent[0]).to(self.device) for obs_n_agent in obs_n]).view(batch_size, -1, self.d_node)  # [batch_size, m1 * n_agents, d_node]
        task_data_n_agent = torch.stack([torch.FloatTensor(obs_n_agent[1]).to(self.device) for obs_n_agent in obs_n]).view(batch_size, -1, self.max_tasks, self.d_task)  # [batch_size, m1 * n_agents, max_tasks, d_task]
        compute_node_n_agent = torch.stack([torch.FloatTensor(obs_n_agent[2]).to(self.device) for obs_n_agent in obs_n]).view(batch_size, -1, self.d_node)  # [batch_size, m2 * n_agents, d_node]
        task_mask_n_agent = torch.stack([torch.FloatTensor(obs_n_agent[3]).to(self.device) for obs_n_agent in obs_n]).view(batch_size, -1, self.max_tasks)  # [batch_size, m1 * n_agents, max_tasks]
        compute_node_mask_n_agent = torch.stack([torch.FloatTensor(obs_n_agent[4]).to(self.device) for obs_n_agent in obs_n]).view(batch_size, -1)  # [batch_size, m2 * n_agents]
        action_n = torch.LongTensor(np.asarray(action_n)).to(self.device)  # [batch_size, n_agents, max_tasks]
        reward_n = torch.FloatTensor(reward_n).to(self.device)
        next_task_node_n_agent = torch.stack([torch.FloatTensor(next_obs_n[0]).to(self.device) for next_obs_n in next_obs_n]).view(batch_size, -1, self.d_node)  # [batch_size, m1 * n_agents, d_node]
        next_task_data_n_agent = torch.stack([torch.FloatTensor(next_obs_n[1]).to(self.device) for next_obs_n in next_obs_n]).view(batch_size, -1, self.max_tasks, self.d_task)  # [batch_size, m1 * n_agents, max_tasks, d_task]
        next_compute_node_n_agent = torch.stack([torch.FloatTensor(next_obs_n[2]).to(self.device) for next_obs_n in next_obs_n]).view(batch_size, -1, self.d_node)  # [batch_size, m2 * n_agents, d_node]
        next_task_mask_n_agent = torch.stack([torch.FloatTensor(next_obs_n[3]).to(self.device) for next_obs_n in next_obs_n]).view(batch_size, -1, self.max_tasks)  # [batch_size, m1 * n_agents, max_tasks]
        next_compute_node_mask_n_agent = torch.stack([torch.FloatTensor(next_obs_n[4]).to(self.device) for next_obs_n in next_obs_n]).view(batch_size, -1)  # [batch_size, m2 * n_agents]
        done_n = torch.FloatTensor(np.asarray(done_n)).to(self.device) # [batch_size, n_agents]


        # 2. 计算目标 Q 值
        with torch.no_grad():
            # 使用目标actor网络计算下一状态的动作
            next_action_n = [self.target_actor_network(next_task_node_n_agent[:, self.m1 * agent_id: self.m1 * (agent_id + 1), :], 
                                                        next_task_data_n_agent[:, self.m1 * agent_id: self.m1 * (agent_id + 1), :, :], 
                                                        next_compute_node_n_agent[:, self.m2 * agent_id: self.m2 * (agent_id + 1), :], 
                                                        next_task_mask_n_agent[:, self.m1 * agent_id: self.m1 * (agent_id + 1), :], 
                                                        next_compute_node_mask_n_agent[:, self.m2 * agent_id: self.m2 * (agent_id + 1)]) for agent_id in range(self.n_agents)] # [n_agents, batch_size, max_tasks, m2]
            next_action_n = torch.stack(next_action_n, dim=0).permute(1, 0, 2, 3).contiguous()  # [batch_size, n_agents, max_tasks, m2]
            # next_action_n为nan的全部置为0
            next_action_n[next_action_n != next_action_n] = 0

            # 将所有agent的下一状态动作拼接起来
            # [batch_size, m1 * n_agents, max_tasks, m2]
            next_action_n = next_action_n.view(batch_size, -1, self.m2+1)
            # 使用目标critic网络计算下一状态的Q值
            target_q_values = self.target_critic_network(next_task_node_n_agent, next_task_data_n_agent,
                                                            next_compute_node_n_agent, next_task_mask_n_agent,
                                                            next_compute_node_mask_n_agent, next_action_n)  # [batch_size, m1 * max_tasks * n_agents]
            # 计算目标Q值
            target_q_values = reward_n + self.gamma * torch.sum(target_q_values, dim=1)  # [batch_size]

        # 3. 获取当前 Q 值
        # 把action从long的index边为one-hot编码
        action_onehot = torch.zeros(batch_size, self.n_agents, self.max_tasks, self.m2+1).to(self.device)
        action_onehot.scatter_(3, action_n.unsqueeze(3), 1)
        action_onehot = action_onehot.view(batch_size, -1, self.m2+1)  # [batch_size, m1 * max_tasks * n_agents, m2]
        # 使用critic网络计算当前状态的Q值
        current_q_values = self.critic_network(task_node_n_agent, task_data_n_agent, compute_node_n_agent,
                                                task_mask_n_agent, compute_node_mask_n_agent, action_onehot)
        # 计算当前Q值
        total_current_q_values = torch.sum(current_q_values, dim=1)  # [batch_size]

        # 4. 计算critic损失
        critic_loss = self.criterion(total_current_q_values, target_q_values)  # [1]

        # 5. 反向传播并更新critic网络
        
        self.critic_optimizer.zero_grad()
        critic_loss.backward()
        self.critic_optimizer.step()
        writer.add_scalar(f'Loss/Critic_Loss', critic_loss.item(), self.update_cnt)

        # 6. 更新actor网络
        total_current_q_values = total_current_q_values.detach()
        # 使用actor网络计算当前状态的动作
        for agent_id in range(self.n_agents):
            current_action = self.actor_network(task_node_n_agent[:, self.m1 * agent_id: self.m1 * (agent_id + 1), :], 
                                                task_data_n_agent[:, self.m1 * agent_id: self.m1 * (agent_id + 1), :, :], 
                                                compute_node_n_agent[:, self.m2 * agent_id: self.m2 * (agent_id + 1), :], 
                                                task_mask_n_agent[:, self.m1 * agent_id: self.m1 * (agent_id + 1), :], 
                                                compute_node_mask_n_agent[:, self.m2 * agent_id: self.m2 * (agent_id + 1)])
            # 如果包含nan，就continue
            if np.isnan(current_action.cpu().data.numpy()).any():
                continue
            # 将所有agent的动作拼接起来，并将当前agent的动作替换为actor网络计算出的动作
            new_action_onehot = torch.cat(
                [action_onehot[:, :agent_id * self.max_tasks, :], current_action,
                action_onehot[:, (agent_id + 1) * self.max_tasks:, :]], dim=1)
            # 使用critic网络计算当前状态的Q值
            actor_loss = -(self.critic_network(task_node_n_agent, task_data_n_agent, compute_node_n_agent,
                                                task_mask_n_agent, compute_node_mask_n_agent, new_action_onehot)).mean()
            # 判断是否是nan
            if actor_loss != actor_loss:
                continue
            # 7. 反向传播并更新actor网络
            self.actor_optimizer.zero_grad()
            actor_loss.backward()
            self.actor_optimizer.step()
            writer.add_scalar(f'Loss/Agent{agent_id}_Actor_Loss', actor_loss.item(), self.update_cnt)

        # 8. 软更新目标网络
        self.soft_update(self.actor_network, self.target_actor_network)
        self.soft_update(self.critic_network, self.target_critic_network)

    def soft_update(self, source_network, target_network):
        # 目标网络的软更新：θ_target = τ * θ_source + (1 - τ) * θ_target
        for target_param, source_param in zip(target_network.parameters(), source_network.parameters()):
            target_param.data.copy_(self.tau * source_param.data + (1.0 - self.tau) * target_param.data)
