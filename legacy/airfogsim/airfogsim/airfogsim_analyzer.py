from .airfogsim_env import AirFogSimEnv
class AirFogSimAnalyzer:
    
    def __init__(self, env:AirFogSimEnv):
        """The analyzer for AirFogSimEnv. It provides the overall analysis of the environment. For task-wise or node-wise analysis, please refer to the AirFogSimEnv class by using the getXXX() functions, and derive the APIs of task or node entities.
        
        Args:
            env (AirFogSimEnv): The AirFogSimEnv environment.
        """
        self.env = env

    def getAvgTaskCompletionRate(self):
        """Get the task completion rate of the environment.
        
        Returns:
            float: The task completion rate, ranging from 0 to 1.
        """
        return 0.0
    
    def getAvgTaskLatency(self):
        """Get the task latency of the environment.
        
        Returns:
            float: The task latency.
        """
        return 0.0
    
    def getAvgTaskUtility(self):
        """Get the task utility of the environment.
        
        Returns:
            float: The task utility.
        """
        return 0.0
    
    def getAvgFogNodeRepuation(self):
        """Get the reputation score of the fog nodes in the environment.
        
        Returns:
            float: The reputation score of the fog nodes.
        """
        return 0.0
    
    def getAvgFogNodeResourceUtilization(self):
        """Get the resource utilization of the fog nodes in the environment.
        
        Returns:
            float: The resource utilization of the fog nodes.
        """
        return 0.0
    