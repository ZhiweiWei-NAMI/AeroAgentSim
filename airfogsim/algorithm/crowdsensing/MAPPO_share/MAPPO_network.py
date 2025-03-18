import torch
import torch.nn as nn
import torch.nn.functional as F


class Critic(nn.Module):
    def __init__(self, n_agent, dim_observation, dim_action, dim_hidden):
        super(Critic, self).__init__()
        self.n_agent = n_agent
        self.dim_observation = dim_observation
        self.dim_action = dim_action
        obs_dim = self.dim_observation * self.n_agent
        act_dim = self.dim_action * self.n_agent

        self.fc1 = nn.Linear(obs_dim, dim_hidden)
        self.fc2 = nn.Linear(dim_hidden, dim_hidden)
        self.fc3 = nn.Linear(dim_hidden, 1)

    # obs: batch_size * obs_dim
    # acts: batch_size * act_dim
    def forward(self, obs):
        x = F.tanh(self.fc1(obs))
        x = F.tanh(self.fc2(x))
        x = self.fc3(x)
        return x

    def save_model(self, file_dir):
        torch.save(self.state_dict(), file_dir, _use_new_zipfile_serialization=False)

    def load_model(self, file_dir):
        self.load_state_dict(torch.load(file_dir))


class Actor(nn.Module):
    def __init__(self, dim_observation, dim_action,dim_hiddens):
        super(Actor, self).__init__()
        self.fc1 = nn.Linear(dim_observation, dim_hiddens)
        self.fc2 = nn.Linear(dim_hiddens, dim_hiddens)
        self.fc3 = nn.Linear(dim_hiddens, dim_action)


    def forward(self, obs):
        x = F.tanh(self.fc1(obs))
        x = F.tanh(self.fc2(x))
        x = F.softmax(self.fc3(x), dim=-1)
        return x

    def save_model(self, file_path):
        torch.save(self.state_dict(), file_path, _use_new_zipfile_serialization=False)

    def load_model(self, file_path):
        self.load_state_dict(torch.load(file_path))
