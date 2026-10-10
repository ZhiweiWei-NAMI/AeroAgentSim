from .base_sched import BaseScheduler

class TopologyScheduler(BaseScheduler):
    """The topology scheduler for the fog nodes. Provide static methods to schedule the topology of the fog nodes.
    """

    @staticmethod
    def setTopologyByNodeName(env, node_name: str, topology: dict):
        """Schedule the topology of the fog node (e.g., fog vehicle, edge server, cloud server) by the fog node name.

        Args:
            env (AirFogSimEnv): The environment.
            fog_node_name (str): The fog node name.
            topology (dict): The topology of the fog node. The topology includes the connection information of the fog node.

        Returns:
            bool: The flag to indicate whether the topology is scheduled successfully.
        """
        return True