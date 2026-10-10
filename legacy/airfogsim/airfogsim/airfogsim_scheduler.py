from .scheduler import *
import inspect



class AirFogSimScheduler:
    """The scheduler for AirFogSimEnv. It provides communication, computation, task generation, blockchain scheduler. Agent interacts with this class to get the state, reward, and done signal.
    """
    @staticmethod
    def getComputationScheduler():
        """Get the computation scheduler for the environment. It schedules the computation tasks for the fog nodes.

        Returns: 
            ComputationScheduler: The computation scheduler.
        """
        return ComputationScheduler()
    
    @staticmethod
    def getCommunicationScheduler():
        """Get the communication scheduler for the environment. It schedules the communication tasks for the fog nodes.

        Returns: 
            CommunicationScheduler: The communication scheduler.
        """
        return CommunicationScheduler()
    
    @staticmethod
    def getTaskScheduler():
        """Get the task scheduler for the environment. It schedules the tasks for the task nodes.

        Returns: 
            TaskScheduler: The task scheduler.
        """
        return TaskScheduler()
    
    @staticmethod
    def getBlockchainScheduler():
        """Get the blockchain scheduler for the environment. It schedules the blockchain tasks for the blockchain nodes.

        Returns: 
            BlockchainScheduler: The blockchain scheduler.
        """
        return BlockchainScheduler()
    
    @staticmethod
    def getRewardScheduler():
        """Get the reward scheduler for the environment. It calculates the reward for the agent.

        Returns: 
            RewardScheduler: The reward scheduler.
        """
        return RewardScheduler()
    
    @staticmethod
    def getSchedulerMethodList():
        """Get the list of all scheduler methods. Use inspect module to get the list of all static methods in this class. The method name should start with 'get' and end with 'Scheduler'.

        Returns: 
            list: The list of all scheduler method names.
        """
        method_list = []
        for name, method in inspect.getmembers(AirFogSimScheduler):
            if inspect.isfunction(method) and name.startswith('get') and name.endswith('Scheduler'):
                method_list.append(name)
        return method_list
    

    @staticmethod
    def addMethodsFromSchedulers():
        """Add the methods from all scheduler classes to AirFogSimScheduler. By using this method, the agent can call the methods from all scheduler classes by calling the methods from AirFogSimScheduler.

        Examples:
            AirFogSimScheduler.addMethodsFromSchedulers()
            compSched = AirFogSimScheduler.getComputationScheduler()
            compSched.setComputationModel(env, 'M/M/1')
            AirFogSimScheduler.setComputationModel(env, 'M/M/1')
        """
        scheduler_method_list = AirFogSimScheduler.getSchedulerMethodList()
        # 从scheduler_method_list中获取方法名，然后调用后获取每一个scheduler的实例，然后将实例的方法添加到AirFogSimScheduler中
        for scheduler_method in scheduler_method_list:
            scheduler_instance = getattr(AirFogSimScheduler, scheduler_method)()
            for name, method in inspect.getmembers(scheduler_instance):
                if inspect.isfunction(method):
                    setattr(AirFogSimScheduler, name, method)

    @staticmethod
    def getEntityScheduler():
        """Get the entity scheduler for the environment. It schedules the entities in the environment.

        Returns: 
            EntityScheduler: The entity scheduler.
        """
        return EntityScheduler()

    @staticmethod
    def getMissionScheduler():
        """Get the misssion scheduler for the environment. It schedules the mission for the task nodes.

        Returns:
            MissionScheduler: The mission scheduler.
        """
        return MissionScheduler()

    @staticmethod
    def getSensorScheduler():
        """Get the sensor scheduler for the environment. It schedules the sensors for the task nodes and missions.

        Returns:
            SensorScheduler: The sensor scheduler.
        """
        return SensorScheduler()

    @staticmethod
    def getTrafficScheduler():
        """Get the traffic scheduler for the environment.

        Returns:
            TrafficScheduler: The traffic scheduler.
        """
        return TrafficScheduler()

    @staticmethod
    def getAlgorithmScheduler():
        """Get the algorithm scheduler for the environment.

        Returns:
            AlgorithmScheduler: The algorithm scheduler.
        """
        return AlgorithmScheduler()
