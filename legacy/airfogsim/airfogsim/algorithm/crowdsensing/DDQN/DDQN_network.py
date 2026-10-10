
import torch
from torch import nn

# ----------------------------------- #
# 构造网络，训练网络和目标网络共用该结构
# ----------------------------------- #
class Net(nn.Module):
    def __init__(self, dim_states, dim_hiddens, dim_actions):
        super(Net, self).__init__()
        self.dim_states=dim_states
        self.dim_hiddens=dim_hiddens
        self.dim_actions=dim_actions
        # 有两个隐含层
        self.action_selector = nn.Sequential(
            nn.Linear(dim_states, dim_hiddens),
            nn.GELU(),
            nn.Linear(dim_hiddens, dim_hiddens),
            nn.GELU(),
            nn.Linear(dim_hiddens, dim_actions),
        )

    # 前向传播
    def forward(self, x):
        result=self.action_selector(x)
        return result

    def save_model(self, file_path):
        torch.save(self.state_dict(), file_path, _use_new_zipfile_serialization=False)

    def load_model(self, file_path):
        self.load_state_dict(torch.load(file_path))