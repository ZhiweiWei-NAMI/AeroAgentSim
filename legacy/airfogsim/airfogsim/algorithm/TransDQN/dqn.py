from .trans_network import TransformerDQN
import torch
import torch.nn as nn
import numpy as np
from ..replay_buffer import ReplayBuffer
import os

# 线程数量=4
torch.set_num_threads(4)
class DQN_Agent:
    def __init__(self, args):
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
        if args.mode == 'train':
            self.q_network = TransformerDQN(self.d_node, self.d_task, self.max_tasks, self.m1, self.m2, self.d_model, self.nhead, self.num_layers)
            self.q_network.to(self.device)
        elif args.mode == 'test':
            self.q_network = TransformerDQN(self.d_node, self.d_task, self.max_tasks, self.m1, self.m2, self.d_model, self.nhead, self.num_layers)
            self.q_network.to(self.device)
            if os.path.exists(args.model_path):
                self.q_network.load_state_dict(torch.load(args.model_path))
            else:
                print(f'Model path {args.model_path} does not exist!')

        self.target_network = TransformerDQN(self.d_node, self.d_task, self.max_tasks, self.m1, self.m2, self.d_model, self.nhead, self.num_layers)
        self.target_network.to(self.device)
        self.target_network.load_state_dict(self.q_network.state_dict())
        self.gamma = args.gamma
        self.optimizer = torch.optim.SGD(self.q_network.parameters(), lr=args.lr, momentum=0.9)
        self.criterion = nn.HuberLoss()
        self.replay_buffer = ReplayBuffer(args.replay_buffer_capacity)
        self.update_cnt = 0

    def saveModel(self):
        torch.save(self.q_network.state_dict(), os.path.join(self.args.model_dir, f'model_{self.update_cnt}.pth'))

    def select_action(self, task_node, task_data, compute_node, task_mask, compute_node_mask):
        task_node = torch.FloatTensor(task_node).to(self.device).unsqueeze(0)
        task_data = torch.FloatTensor(task_data).to(self.device).unsqueeze(0)
        compute_node = torch.FloatTensor(compute_node).to(self.device).unsqueeze(0)
        task_mask = torch.FloatTensor(task_mask).to(self.device).unsqueeze(0)
        compute_node_mask = torch.FloatTensor(compute_node_mask).to(self.device).unsqueeze(0)

        with torch.no_grad():
            q_values = self.q_network(task_node, task_data, compute_node, task_mask, compute_node_mask).squeeze(0) # [m1 * max_tasks, m2+1]
            q_values = q_values.cpu().data.numpy() # [m1 * max_tasks, m2+1]
        # add noise
        # q_values += np.random.randn(*q_values.shape) * self.args.epsilon
        return np.argmax(q_values, axis=1) # [m1 * max_tasks]
    
    def add_experience(self, task_node, task_data, compute_node, task_mask, compute_node_mask, action, reward, next_task_node, next_task_data, next_compute_node, next_task_mask, next_compute_node_mask, done):
        self.replay_buffer.add((task_node, task_data, compute_node, task_mask, compute_node_mask, action, reward, next_task_node, next_task_data, next_compute_node, next_task_mask, next_compute_node_mask, done))
    
    def update(self, writer):
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
        task_node, task_data, compute_node, task_mask, compute_node_mask, action, reward, next_task_node, next_task_data, next_compute_node, next_task_mask, next_compute_node_mask, done = zip(*experiences)
        task_node = torch.FloatTensor(np.asarray(task_node)).to(self.device)
        task_data = torch.FloatTensor(np.asarray(task_data)).to(self.device)
        compute_node = torch.FloatTensor(np.asarray(compute_node)).to(self.device)
        task_mask = torch.FloatTensor(np.asarray(task_mask)).to(self.device)
        compute_node_mask = torch.FloatTensor(np.asarray(compute_node_mask)).to(self.device)
        action = torch.LongTensor(np.asarray(action)).to(self.device) # [batch_size, m1, max_tasks]
        action = action.view(batch_size, -1) # [batch_size, m1 * max_tasks]
        reward = torch.FloatTensor(np.asarray(reward, dtype=np.float32)).to(self.device)
        next_task_node = torch.FloatTensor(np.asarray(next_task_node)).to(self.device)
        next_task_data = torch.FloatTensor(np.asarray(next_task_data)).to(self.device)
        next_compute_node = torch.FloatTensor(np.asarray(next_compute_node)).to(self.device)
        next_task_mask = torch.FloatTensor(np.asarray(next_task_mask)).to(self.device)
        next_compute_node_mask = torch.FloatTensor(np.asarray(next_compute_node_mask)).to(self.device)
        done = torch.FloatTensor(np.asarray(done, dtype=np.float32)).to(self.device)

        # 2. 计算目标 Q 值
        with torch.no_grad():
            # 使用目标网络计算下一状态的最大 Q 值
            next_q_values = self.target_network(next_task_node, next_task_data, next_compute_node, next_task_mask, next_compute_node_mask)
            max_next_q_values = torch.max(next_q_values, dim=2)[0] # [batch_size, m1 * max_tasks]
            total_max_next_q_values = torch.sum(max_next_q_values, dim=1) # [batch_size]
            # 计算目标 Q 值
            target_q_values = reward + self.gamma * total_max_next_q_values * (1 - done) # [batch_size]

        # 3. 获取当前 Q 值
        current_q_values = self.q_network(task_node, task_data, compute_node, task_mask, compute_node_mask)
        current_q_values = current_q_values.gather(2, action.unsqueeze(2)).squeeze(2) # [batch_size, m1 * max_tasks]
        total_current_q_values = torch.sum(current_q_values, dim=1) # [batch_size]

        # 4. 计算损失
        loss = self.criterion(total_current_q_values, target_q_values) # [1]

        # 5. 反向传播并更新网络
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()
        writer.add_scalar('Loss', loss.item(), self.update_cnt)

        # 6. 软更新目标网络
        self.soft_update(self.q_network, self.target_network)

    def soft_update(self, source_network, target_network):
        # 目标网络的软更新：θ_target = τ * θ_source + (1 - τ) * θ_target
        for target_param, source_param in zip(target_network.parameters(), source_network.parameters()):
            target_param.data.copy_(self.tau * source_param.data + (1.0 - self.tau) * target_param.data)