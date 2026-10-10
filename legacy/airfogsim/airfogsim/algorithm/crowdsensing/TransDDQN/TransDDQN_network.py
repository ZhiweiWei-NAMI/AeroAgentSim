import torch
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
        self.dim_hiddens= dim_args.dim_hiddens


        # Embedding layers
        self.node_embedding = nn.Linear(self.dim_node, self.dim_model)
        self.mission_embedding = nn.Linear(self.dim_mission, self.dim_model)
        self.sensor_embedding = nn.Linear(self.dim_sensor, self.dim_model)
        self.sensor_mask_embedding = nn.Linear(self.dim_sensor, self.dim_model)

        # Transformer Encoder
        self.transformer = nn.TransformerEncoder(
            nn.TransformerEncoderLayer(d_model=self.dim_model, nhead=self.nhead, activation=F.gelu,batch_first=True),
            num_layers=self.num_layers
        )

        # Sensing node selection head sigmoid for possible values [0, 1]
        self.mission_node_selector = nn.Sequential(
            nn.Linear((self.m1 + 1 + self.m_uv * self.max_sensors) * self.dim_model, self.dim_hiddens),
            nn.GELU(),
            nn.Linear(self.dim_hiddens, self.dim_hiddens),
            nn.GELU(),
            nn.Linear(self.dim_hiddens, self.m2),
        )

    def forward(self, node_state, mission_state, sensor_state, sensor_mask):
        """
        Args:
            node_state: [batch_size, m1, dim_node]
            mission_state: [batch_size, 1, dim_mission]
            sensor_state: [batch_size, m_uv, max_sensors, dim_sensor]
            sensor_mask: [batch_size, m_uv, max_sensors], 1 for valid sensors, 0 for others
        """
        batch_size = node_state.size(0)

        # Process neighbor task nodes and data
        node_state_emb = self.node_embedding(node_state)  # [batch_size, m1, dim_model]
        mission_state_emb = self.mission_embedding(mission_state)  # [batch_size, 1, dim_model]
        sensor_state_emb = self.sensor_embedding(sensor_state)  # [batch_size, m_uv, max_sensors, dim_model]

        # Add node embedding to each task [batch_size, m_uv, max_sensors, dim_model]
        sensor_state_combined = node_state_emb[:, :self.m_uv].unsqueeze(2) + sensor_state_emb
        # Flatten to [batch_size, m_uv * max_sensors, dim_model]
        sensor_state_sequence = sensor_state_combined.view(batch_size, -1, self.dim_model)

        # Combine all sequences
        combined_sequence = torch.cat([
            node_state_emb,  # nodes(Veh+UAV+RSU) [batch_size, m1, dim_model]
            mission_state_emb,  # mission [batch_size, 1, dim_model]
            sensor_state_sequence,  # sensors [batch_size, m_uv * max_sensors, dim_model]
        ], dim=1)  # [batch_size,  m1+1+m_uv * max_sensors, dim_model ]

        sensor_mask_flat = sensor_mask.view(batch_size, -1)  # [batch_size, m_uv * max_sensors]=[batch_size, m2]

        # Apply Transformer Encoder
        # transformer_output = self.transformer(
        #     src=combined_sequence.permute(1, 0, 2),  # [seq_len, batch_size, dim_model]
        # ).permute(1, 0, 2)  # [batch_size, seq_len, dim_model]

        # batch_firsh==True就不用调整维度顺序了
        transformer_output = self.transformer(
            src=combined_sequence,  # [batch_size, seq_len, dim_model]
        ) # [batch_size, seq_len, dim_model]
        flatten_outputs = transformer_output.reshape(batch_size,
                                                  -1)  # [batch_size, (m1+1+m_uv * max_sensors) * dim_model ]

        # Select mission node
        q_value = self.mission_node_selector(flatten_outputs)  # [batch_size, m2]

        return q_value

    def save_model(self, file_path):
        torch.save(self.state_dict(), file_path, _use_new_zipfile_serialization=False)

    def load_model(self, file_path):
        self.load_state_dict(torch.load(file_path))
