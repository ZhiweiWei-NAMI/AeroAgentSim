import numpy as np

from .base_sched import BaseScheduler
class CommunicationScheduler(BaseScheduler):
    """The communication scheduler for channels.
    """

    
    @staticmethod
    def getNumberOfRB(env):
        """Get the number of resource blocks.

        Args:
            env (AirFogSimEnv): The environment.

        Returns:
            int: The number of resource blocks.
        """
        return env.channel_manager.n_RB
    
    @staticmethod
    def setCommunicationWithRB(env, task_id: str, RB_nos: list):
        """Set the communication with the resource blocks.

        Args:
            env (AirFogSimEnv): The environment.
            task_id (str): The task id.
            RB_nos (list): The list of resource block numbers.
        """
        # 确保RB_nos中每个数字都在0到n_RB-1之间，n_RB = getNumberOfRB(env)
        n_RB = CommunicationScheduler.getNumberOfRB(env)
        RB_nos = [RB_no % n_RB for RB_no in RB_nos]
        env.activated_offloading_tasks_with_RB_Nos[task_id] = RB_nos

    @staticmethod
    def getSumRateByChannelType(env, transmitter_idx, receiver_idx, channel_type):
        """Get the rate by the channel type.

        Args:
            transmitter_idx (int): The index of the transmitter corresponding to its type.
            receiver_idx (int): The index of the receiver corresponding to its type.
            channel_type (str): The channel type. The channel type can be 'V2V', 'V2I', 'V2U', 'U2U', 'U2V', 'U2I', 'I2U', 'I2V', 'I2I'.

        Returns:
            float: Sum of communication blocks rate
        """
        comm_rate=env.channel_manager.getRateByChannelType( transmitter_idx, receiver_idx, channel_type)
        return np.sum(comm_rate)

    @staticmethod
    def getConfig(env,name):
        return env.channel_manager.getConfig(name)

