import collections
import pickle
import random
import numpy as np


# ----------------------------------- #
# 经验回放池
# ----------------------------------- #
class ReplayBuffer:
    def __init__(self, buffer_size, train_min_size):
        # 创建一个队列，先进先出，队列长度不变
        self.buffer_size = buffer_size
        self.train_min_size = train_min_size
        self.buffer = collections.deque(maxlen=buffer_size)

    # 填充经验池
    def add(self, node_state, mission_state, sensor_state, sensor_mask, action, reward, next_node_state,
            next_mission_state, next_sensor_state, next_sensor_mask, done):
        self.buffer.append((node_state, mission_state, sensor_state, sensor_mask, action, reward, next_node_state,
                            next_mission_state, next_sensor_state, next_sensor_mask, done))

    # 随机采样batch组样本数据
    def sample(self, batch_size):
        transitions = random.sample(self.buffer, batch_size)
        # 分别取出这些数据，*获取list中的所有值
        node_state, mission_state, sensor_state, sensor_mask, action, reward, next_node_state, next_mission_state, next_sensor_state, next_sensor_mask, done = zip(
            *transitions)
        # 将state变成数组，后面方便计算
        return np.array(node_state), np.array(mission_state), np.array(sensor_state), np.array(sensor_mask), np.array(
            action), np.array(reward), np.array(next_node_state), np.array(next_mission_state), np.array(
            next_sensor_state), np.array(next_sensor_mask), np.array(done)

    # 队列的长度
    def size(self):
        return len(self.buffer)

    def ready(self):
        return len(self.buffer) > self.train_min_size

    def save(self, file_path):
        with open(file_path, 'wb') as f:
            pickle.dump(self.buffer, f)
        print(f"ReplayBuffer saved to {file_path}")

    def load(self, file_path):
        with open(file_path, 'rb') as f:
            self.buffer = pickle.load(f)
        print(f"ReplayBuffer loaded from {file_path}")
