import torch
import torch.nn as nn

class TransformerActor(nn.Module):
    def __init__(self, d_node, d_task, max_tasks, m1, m2, d_model, nhead, num_layers):
        super(TransformerActor, self).__init__()
        self.d_node = d_node
        self.d_task = d_task
        self.max_tasks = max_tasks
        self.m1 = m1  # Maximum task nodes
        self.m2 = m2  # Maximum compute nodes
        self.d_model = d_model

        # Embedding layers
        self.task_node_embedding = nn.Linear(d_node, d_model)
        self.task_data_embedding = nn.Linear(d_task, d_model)
        self.compute_node_embedding = nn.Linear(d_node, d_model)

        # Transformer Encoder
        self.transformer = nn.TransformerEncoder(
            nn.TransformerEncoderLayer(d_model=d_model, nhead=nhead, batch_first=True, activation='gelu'),
            num_layers=num_layers
        )

        # Compute node selection head, output dim = m2 (no need for local computation)
        self.compute_node_selector = nn.Sequential(
            nn.Linear(d_model, d_model // 2),
            nn.GELU(),
            nn.Linear(d_model // 2, self.m2+1)
        )

    def forward(self, task_node, task_data, compute_node, task_mask, compute_node_mask):
        """
        Args:
            task_node: [batch_size, m1, d_node]
            task_data: [batch_size, m1, max_tasks, d_task]
            compute_node: [batch_size, m2, d_node]
            task_mask: [batch_size, m1, max_tasks], 1 for valid tasks, 0 for padding
            compute_node_mask: [batch_size, m2]
        """
        batch_size = task_node.size(0)

        # Process neighbor task nodes and data
        task_node_emb = self.task_node_embedding(task_node)  # [batch_size, m1, d_model]
        task_data_emb = self.task_data_embedding(task_data)  # [batch_size, m1, max_tasks, d_model]
        task_data_combined = task_node_emb.unsqueeze(2) + task_data_emb  # Add node embedding to each task
        task_data_sequence = task_data_combined.view(batch_size, -1,
                                                     self.d_model)  # Flatten to [batch_size, m1 * max_tasks, d_model]

        # Process neighbor compute nodes
        compute_node_emb = self.compute_node_embedding(compute_node)  # [batch_size, m2, d_model]

        # Combine all sequences
        combined_sequence = torch.cat([
            task_data_sequence,  # tasks [batch_size, m1 * max_tasks, d_model]
            compute_node_emb  # Compute nodes [batch_size, m2, d_model]
        ], dim=1)  # [batch_size, m1 * max_tasks + m2, d_model]

        # Create combined mask
        task_mask_flat = task_mask.view(batch_size, -1)  # [batch_size, m1 * max_tasks]
        combined_mask = torch.cat([
            task_mask_flat,  # task mask
            compute_node_mask  # Compute node mask
        ], dim=1)  # [batch_size, m1 * max_tasks + m2]

        # Apply Transformer Encoder
        transformer_output = self.transformer(
            src=combined_sequence,  
            # src_key_padding_mask=(1 - combined_mask).bool()  # Mask padded positions
        )  # [batch_size, seq_len, d_model]

        # Extract final outputs, m1*max_tasks
        final_outputs = (transformer_output[:, :self.max_tasks])  # [batch_size, max_tasks, d_model]

        # Select compute node
        compute_score = self.compute_node_selector(final_outputs)  # [batch_size, max_tasks, m2+1]

        # Mask invalid compute nodes
        # compute_score: [batch_size, m1 * max_tasks, m2], compute_node_mask.unsqueeze(1): [batch_size, 1, m2]
        valid_compute_score = compute_score[:, :, 1:] * compute_node_mask.unsqueeze(1)  # [batch_size, max_tasks, m2]
        valid_compute_score = torch.cat([compute_score[:, :, :1], valid_compute_score], dim=2) # [batch_size, max_tasks, m2+1]
        # Softmax
        valid_compute_score = torch.softmax(valid_compute_score, dim=-1)

        return valid_compute_score


class TransformerCritic(nn.Module):
    def __init__(self, n_agent, d_node, d_task, max_tasks, m1, m2, d_model, nhead, num_layers):
        super(TransformerCritic, self).__init__()
        self.d_node = d_node
        self.d_task = d_task
        self.max_tasks = max_tasks
        self.m1 = m1  # Maximum task nodes
        self.m2 = m2  # Maximum compute nodes
        self.d_model = d_model

        # Embedding layers
        self.task_node_embedding = nn.Linear(d_node, d_model)
        self.task_data_embedding = nn.Linear(d_task, d_model)
        self.compute_node_embedding = nn.Linear(d_node, d_model)
        self.action_embedding = nn.Linear(m2+1, d_model)  # Add action embedding layer

        # Transformer Encoder
        self.transformer = nn.TransformerEncoder(
            nn.TransformerEncoderLayer(d_model=d_model, nhead=nhead, batch_first=True, activation='gelu'),
            num_layers=num_layers
        )

        # Q-value head
        self.q_value_head = nn.Sequential(
            nn.Linear(d_model, d_model//2),
            nn.GELU(),
            nn.Linear(d_model//2, 1)
        )

    def forward(self, task_node, task_data, compute_node, task_mask, compute_node_mask, action):
        """
        Args:
            task_node: [batch_size, n_agent*m1, d_node]
            task_data: [batch_size, n_agent*m1, max_tasks, d_task]
            compute_node: [batch_size, n_agent*m2, d_node]
            task_mask: [batch_size, n_agent*m1, max_tasks], 1 for valid tasks, 0 for padding
            compute_node_mask: [batch_size, n_agent*m2]
            action: [batch_size, n_agent * max_tasks, m2]
        """
        batch_size = task_node.size(0)
        if task_mask.sum() == 0 or compute_node_mask.sum() == 0:
            return torch.zeros(batch_size, self.max_tasks)

        # Process neighbor task nodes and data
        task_node_emb = self.task_node_embedding(task_node)  # [batch_size, n_agent*m1, d_model]
        task_data_emb = self.task_data_embedding(task_data)  # [batch_size, n_agent*m1, max_tasks, d_model]
        task_data_combined = task_node_emb.unsqueeze(2) + task_data_emb  # Add node embedding to each task
        task_data_sequence = task_data_combined.view(batch_size, -1,
                                                     self.d_model)  # Flatten to [batch_size, n_agent*m1 * max_tasks, d_model]

        # Process neighbor compute nodes
        compute_node_emb = self.compute_node_embedding(compute_node)  # [batch_size, n_agent*m2, d_model]

        # Process actions
        action_emb = self.action_embedding(action)  # [batch_size, n_agent * max_tasks, d_model]

        # Combine all sequences
        combined_sequence = torch.cat([
            task_data_sequence,  # tasks [batch_size, n_agent*m1 * max_tasks, d_model]
            compute_node_emb,  # Compute nodes [batch_size, n_agent*m2, d_model]
        ], dim=1)  # [batch_size, n_agent*m1 * max_tasks + n_agent*m2, d_model]

        # Create combined mask
        task_mask_flat = task_mask.view(batch_size, -1)  # [batch_size, n_agent*m1 * max_tasks]
        combined_mask = torch.cat([
            task_mask_flat,  # task mask
            compute_node_mask,  # Compute node mask
        ], dim=1)  # [batch_size, n_agent*m1 * max_tasks + n_agent*m2]

        # Apply Transformer Encoder
        transformer_output = self.transformer(
            src=combined_sequence,  # [seq_len, batch_size, d_model]
            # src_key_padding_mask=(1 - combined_mask).bool()  # Mask padded positions
        )  # [batch_size, seq_len, d_model]

        # Concatenate action embedding
        tmp_outputs = torch.cat([
            transformer_output,  # Transformer output [batch_size, seq_len, d_model]
            action_emb  # Action embedding [batch_size, n_agent * max_tasks, d_model]
        ], dim=1)
        tmp_outputs = torch.relu(tmp_outputs)
        final_outputs = tmp_outputs[:, :self.max_tasks*self.m1]  # [batch_size, n_agent * max_tasks, d_model]
        # Get Q-values
        q_values = self.q_value_head(final_outputs).squeeze(-1)  # [batch_size, seq_len]
        return q_values
