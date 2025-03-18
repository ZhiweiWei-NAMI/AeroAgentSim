
import torch as T
import torch.nn.functional as F
from torch import nn

# ----------------------------------- #
# 构造网络，训练网络和目标网络共用该结构
# ----------------------------------- #
class Net(nn.Module):
    def __init__(self, dim_args):
        super(Net, self).__init__()
        self.dim_states=dim_args.dim_states
        self.dim_hiddens= dim_args.dim_hiddens
        self.dim_value=dim_args.dim_value
        self.dim_advantages=dim_args.dim_advantages
        self.dim_actions = dim_args.dim_actions


        # 有3个隐含层
        self.fc1 = nn.Linear(self.dim_states, self.dim_hiddens)
        self.fc2 = nn.Linear(self.dim_hiddens, self.dim_hiddens)
        self.fc3 = nn.Linear(self.dim_hiddens, self.dim_hiddens)

        self.fc_value = nn.Linear(self.dim_hiddens, self.dim_value)
        self.fc_advantages = nn.Linear(self.dim_hiddens, self.dim_advantages)

        self.value_calculator=nn.Linear(self.dim_value,1)
        self.advantages_calculator=nn.Linear(self.dim_advantages,self.dim_actions)


    # 前向传播
    def forward(self, x):
        x=F.gelu(self.fc1(x))
        x=F.gelu(self.fc2(x))
        x = F.gelu(self.fc3(x))

        V=F.gelu(self.fc_value(x))
        A=F.gelu(self.fc_advantages(x))

        V=self.value_calculator(V)
        A=self.advantages_calculator(A)

        Q = V + A - T.mean(A, dim=-1, keepdim=True)
        return Q

    def save_model(self, file_path):
        T.save(self.state_dict(), file_path, _use_new_zipfile_serialization=False)

    def load_model(self, file_path):
        self.load_state_dict(T.load(file_path))