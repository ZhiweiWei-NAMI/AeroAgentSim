import os

import torch
import numpy as np
from tqdm import tqdm
import matplotlib.pyplot as plt
from .D3QN_model import D3QN


class D3QN_Env:

    def __init__(self, dim_args, train_args):
        # self.return_list = []  # 记录每次迭代的return，即链上的reward之和
        self.smooth_factor=train_args.smooth_factor
        self.max_q_value = 0  # 最大state_value
        self.max_q_value_list = []  # 保存所有最大的state_value

        # 模型文件路径
        # current_dir = os.path.dirname(os.path.abspath(__file__))
        # self.model_base_dir = os.path.join(current_dir, "model")
        self.model_base_dir=train_args.model_base_dir

        # 实例化 Double-DQN
        self.agent = D3QN(dim_args, train_args)

    def takeAction(self, state, mask):
        # 状态state时做动作选择，action为动作索引
        is_random, max_q_value, action = self.agent.take_action(state,mask)
        # 平滑处理最大state_value
        self.max_q_value = max_q_value * (1 - self.smooth_factor) + self.max_q_value * self.smooth_factor
        # 保存每次迭代的最大state_value
        # self.max_q_value_list.append(self.max_q_value)
        return is_random,self.max_q_value,action

    def addExperience(self, state,mask, action, reward, next_state,next_mask, done):
        # 添加经验池
        self.agent.remember(state,mask, action, reward, next_state,next_mask, done)

    def train(self):
        loss=self.agent.update()
        return loss

    def saveModel(self,episode,final=False,succ_ratio=0):
        self.agent.save_models(episode,self.model_base_dir,final,succ_ratio)

    def loadModel(self,episode,final=False):
        self.agent.load_models(episode,self.model_base_dir,final)


