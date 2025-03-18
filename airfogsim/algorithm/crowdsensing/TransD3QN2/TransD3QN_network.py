import torch as T
from torch.nn import functional as F
from torch import nn


# ----------------------------------- #
# 训练网络和目标网络共用该结构
# ----------------------------------- #
class Net(nn.Module):
    def __init__(self, dim_args):
        super(Net, self).__init__()
        self.dim_node = dim_args.dim_node
        self.dim_mission = dim_args.dim_mission
        self.dim_sensor = dim_args.dim_sensor
        self.max_sensors = dim_args.max_sensors  # Maximum sensors for each nodes
        self.m_v = dim_args.m_v  # Maximum Veh nodes
        self.m_u = dim_args.m_u  # Maximum UAV nodes
        self.m_r = dim_args.m_r  # Maximum RSU nodes
        self.m_uv = dim_args.m_u + dim_args.m_v
        self.m1 = dim_args.m_v + dim_args.m_u + dim_args.m_r  # Maximum nodes
        self.m2 = dim_args.max_sensors * (dim_args.m_v + dim_args.m_u)  # Maximum sensors
        self.dim_model = dim_args.dim_model  # Embedding feature dimension
        self.nhead=dim_args.nhead
        self.num_layers=dim_args.num_layers
        self.dim_actions=dim_args.dim_actions
        self.dim_hiddens= dim_args.dim_hiddens
        self.dim_value=dim_args.dim_value
        self.dim_advantages=dim_args.dim_advantages

        # Embedding layers
        self.mission_embedding = nn.Linear(self.dim_mission, self.dim_model)
        self.sensor_embedding = nn.Linear(self.dim_sensor, self.dim_model)

        # Transformer Encoder
        self.transformer = nn.TransformerEncoder(
            nn.TransformerEncoderLayer(d_model=self.dim_model, nhead=self.nhead, activation=F.gelu,batch_first=True),
            num_layers=self.num_layers
        )

        # 有两个隐含层
        self.fc1 = nn.Linear((self.dim_actions+1) * self.dim_model, self.dim_hiddens)
        self.fc2 = nn.Linear(self.dim_hiddens, self.dim_hiddens)

        # 用于计算Q值和优势函数的子隐含层
        self.fc_value = nn.Linear(self.dim_hiddens, self.dim_value)
        self.fc_advantages = nn.Linear(self.dim_hiddens, self.dim_advantages)

        # 输出Q值与动作优势
        self.value_calculator=nn.Linear(self.dim_value,1)
        self.advantages_calculator=nn.Linear(self.dim_advantages,self.dim_actions)


    def forward(self, mission_state, sensor_state, sensor_mask):
        """
        Args:
            mission_state: [batch_size, 1, dim_mission]
            sensor_state: [batch_size, max_sensors, dim_sensor]
            sensor_mask: [batch_size, max_sensors], 1 for valid sensors, 0 for others
        """
        batch_size = mission_state.size(0)

        # Process neighbor task nodes and data
        mission_state_emb = self.mission_embedding(mission_state)  # [batch_size, 1, dim_model]
        sensor_state_emb = self.sensor_embedding(sensor_state)  # [batch_size, max_sensors, dim_model]

        # Combine all sequences
        combined_sequence = T.cat([
            mission_state_emb,  # mission [batch_size, 1, dim_model]
            sensor_state_emb,  # sensors [batch_size, max_sensors, dim_model]
        ], dim=1)  # [batch_size,  1+ max_sensors, dim_model ]

        sensor_mask_flat = sensor_mask.view(batch_size, -1)  # [batch_size,  max_sensors]

        # Apply Transformer Encoder
        # transformer_output = self.transformer(
        #     src=combined_sequence.permute(1, 0, 2),  # [seq_len, batch_size, dim_model]
        # ).permute(1, 0, 2)  # [batch_size, seq_len, dim_model]

        # batch_firsh==True就不用调整维度顺序了
        transformer_output = self.transformer(
            src=combined_sequence,  # [batch_size, seq_len, dim_model]
        ) # [batch_size, seq_len, dim_model]
        flatten_outputs = transformer_output.reshape(batch_size,-1)  # [batch_size, 1+max_sensors * dim_model ]

        # Select mission node
        x=F.gelu(self.fc1(flatten_outputs))
        x=F.gelu(self.fc2(x))

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
