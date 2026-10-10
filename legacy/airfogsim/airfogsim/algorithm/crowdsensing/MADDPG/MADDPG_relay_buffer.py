import collections
import pickle
from collections import namedtuple
import random
from pprint import pprint

import numpy as np


class ReplayBuffer:
    def __init__(self, buffer_size, train_min_size):
        # 创建一个队列，先进先出，队列长度不变
        self.buffer_size=buffer_size
        self.train_min_size = train_min_size
        self.buffer = collections.deque(maxlen=buffer_size)

    def add(self, state,action,reward, next_state):
        self.buffer.append((state,action,reward, next_state))

    def sample(self, batch_size):
        transitions=random.sample(self.buffer, batch_size)
        # 分别取出这些数据，*获取list中的所有值
        state, action, reward, next_state = zip(*transitions)
        # 将state变成数组，后面方便计算
        return np.array(state), np.array(action), np.array(reward), np.array(next_state)

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