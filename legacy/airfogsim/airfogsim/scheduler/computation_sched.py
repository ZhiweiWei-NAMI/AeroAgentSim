from .base_sched import BaseScheduler
class ComputationScheduler(BaseScheduler):
    """The computation scheduler for the fog nodes. Provide static methods to schedule the computation tasks for the fog nodes.
    """

    # @staticmethod
    # def setComputingWithNodeCPU(env, task_id: str, allocated_cpu: float):
    #     """Set the computing with the node CPU.

    #     Args:
    #         env (AirFogSimEnv): The environment.
    #         task_id (str): The task id.
    #         allocated_cpu (float): The allocated CPU.
    #     """
    #     env.compute_tasks_with_cpu[task_id] = allocated_cpu

    @staticmethod
    def setComputingCallBack(env, callback):
        """Set the computing callback.

        Args:
            env (AirFogSimEnv): The environment.
            callback (function): The callback function.
        """
        env.alloc_cpu_callback = callback

    @staticmethod
    def getRequiredComputingResourceByNodeId(env, node_id: str):
        """Get the required computing resource by the node id.

        Args:
            env (AirFogSimEnv): The environment.
            node_id (str): The node id.

        Returns:
            float: The required computing resource.
        """
        to_compute_tasks = env.task_manager.getToComputeTasks(node_id)
        required_cpu = 0
        current_time = env.simulation_time
        for task in to_compute_tasks:
            computed_cpu = task.getComputedSize()
            task_cpu = task.getTaskCPU()
            remain_cpu = task_cpu - computed_cpu
            arrival_time = task.getTaskArrivalTime()
            deadline = task.getTaskDeadline()
            remain_time = deadline + arrival_time - current_time
            if remain_time > 0.1:
                required_cpu += remain_cpu / remain_time
        return required_cpu
