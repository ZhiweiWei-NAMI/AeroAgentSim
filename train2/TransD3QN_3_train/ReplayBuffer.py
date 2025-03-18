import numpy as np


class BaseReplayBuffer:
    def __init__(self):
        # 创建一个字典，长度不限
        self.buffer = {}

    # def __expToFlattenArray(self, exp):
    #     state = exp['state']
    #     action = exp['action']
    #     reward = exp['reward']
    #     next_state = exp['next_state']
    #     done = exp['done']
    #     return np.array(state), action, reward, np.array(next_state), done
    #
    # def add(self, exp_id, state, action, reward=None, next_state=None,  done=None):
    #     self.buffer[exp_id] = {'state': state, 'action': action,  'reward': reward,
    #                            'next_state': next_state,  'done': done}
    #
    # def setNextState(self, exp_id, next_state, done):
    #     assert exp_id in self.buffer, "State_id is invalid."
    #     self.buffer[exp_id]['next_state'] = next_state
    #     self.buffer[exp_id]['done'] = done
    #
    # def completeAndPopExperience(self, exp_id, reward):
    #     assert exp_id in self.buffer, "exp_id is invalid."
    #     self.buffer[exp_id]['reward'] = reward
    #     packed_exp = self.__expToFlattenArray(self.buffer[exp_id])
    #     del self.buffer[exp_id]
    #     return packed_exp

    def size(self):
        return len(self.buffer)

    def clear(self):
        self.buffer.clear()