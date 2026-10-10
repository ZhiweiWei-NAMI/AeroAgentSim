import collections
import pickle
from collections import namedtuple
import random
from pprint import pprint

import numpy as np


class Memory:
    def __init__(self):
        # 创建一个队列，先进先出
        self.buffer = collections.deque()

    def add(self, state,action,reward,next_state):
        self.buffer.append((state,action,reward,next_state))

    def sample(self, batch_size):
        transitions=random.sample(self.buffer, batch_size)
        # 分别取出这些数据，*获取list中的所有值
        state, action, reward, next_state = zip(*transitions)
        # 将state变成数组，后面方便计算
        return np.array(state), np.array(action), np.array(reward), np.array(next_state)

    def get_all(self):
        # 获取所有经验，返回整个 buffer 中的数据
        state, action, reward, next_state = zip(*self.buffer)
        return np.array(state), np.array(action), np.array(reward), np.array(next_state)

    def size(self):
        return len(self.buffer)

    def clear(self):
        self.buffer = collections.deque()

    # def save(self, file_path):
    #     with open(file_path, 'wb') as f:
    #         pickle.dump(self.buffer, f)
    #     print(f"Memory saved to {file_path}")
    #
    # def load(self, file_path):
    #     with open(file_path, 'rb') as f:
    #         self.buffer = pickle.load(f)
    #     print(f"Memory loaded from {file_path}")