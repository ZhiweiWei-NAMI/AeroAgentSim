import torch
import torch.nn as nn
import torch.nn.functional as F


class Critic(nn.Module):
    def __init__(self, n_agent, dim_observation, dim_action, dim_hiddens):
        super(Critic, self).__init__()
        self.n_agent = n_agent
        self.dim_observation = dim_observation
        self.dim_action = dim_action
        obs_dim = self.dim_observation * self.n_agent
        act_dim = self.dim_action * self.n_agent

        # self.FC1 = nn.Linear(obs_dim, 1024)
        # self.FC2 = nn.Linear(1024 + act_dim, 512)
        # self.FC3 = nn.Linear(512, 300)
        # self.FC4 = nn.Linear(300, 1)
        self.value_calculator=torch.nn.Sequential(
            nn.Linear(obs_dim+act_dim, dim_hiddens),
            nn.GELU(),
            nn.Linear(dim_hiddens, dim_hiddens),
            nn.GELU(),
            nn.Linear(dim_hiddens, dim_hiddens),
            nn.GELU(),
            nn.Linear(dim_hiddens, 1)
        )

    # obs: batch_size * obs_dim
    # acts: batch_size * act_dim
    def forward(self, obs, acts):
        combined = torch.cat([obs, acts], 1)
        result = self.value_calculator(combined)
        return result

    def save_model(self, file_dir):
        torch.save(self.state_dict(), file_dir, _use_new_zipfile_serialization=False)

    def load_model(self, file_dir):
        self.load_state_dict(torch.load(file_dir))


class Actor(nn.Module):
    def __init__(self, dim_observation, dim_action, dim_hidden):
        super(Actor, self).__init__()
        # self.FC1 = nn.Linear(dim_observation, 500)
        # self.FC2 = nn.Linear(500, 128)
        # self.FC3 = nn.Linear(128, dim_action)
        self.action_selector=torch.nn.Sequential(
            nn.Linear(dim_observation, dim_hidden),
            nn.GELU(),
            nn.Linear(dim_hidden, dim_hidden),
            nn.GELU(),
            nn.Linear(dim_hidden, dim_action),
            nn.Sigmoid()
        )

    def forward(self, obs):
        result = self.action_selector(obs)
        return result

    def save_model(self, file_path):
        torch.save(self.state_dict(), file_path, _use_new_zipfile_serialization=False)

    def load_model(self, file_path):
        self.load_state_dict(torch.load(file_path))
