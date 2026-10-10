
from .base_sched import BaseScheduler
import numpy as np
class EntityScheduler(BaseScheduler):
    """The entity scheduler for entities.
    """
    
    @staticmethod
    def getAllNodeIdsWithType(env):
        """Get all node ids with type.

        Args:
            env (AirFogSimEnv): The environment.

        Returns:
            list: The node ids.
            list: The type list.
        """
        vehicle_ids = env.vehicles.keys()
        uav_ids = env.UAVs.keys()
        rsu_ids = env.RSUs.keys()
        cloud_server_ids = env.cloudServers.keys()
        all_ids = vehicle_ids + uav_ids + rsu_ids + cloud_server_ids
        type_list = ['vehicle'] * len(vehicle_ids) + ['uav'] * len(uav_ids) + ['rsu'] * len(rsu_ids) + ['cloud_server'] * len(cloud_server_ids)
        return all_ids, type_list
    
    @staticmethod
    def getNodeInfoByIndexAndType(env, idx: int, type: str):
        """Get the node by the index and type.

        Args:
            env (AirFogSimEnv): The environment.
            idx (int): The index.
            type (str): The type, ['vehicle' or 'v', 'uav' or 'u', 'rsu' or 'i', 'cloud_server' or 'c']

        Returns:
            dict: The node info.
        """
        type = type.lower()
        if type in ['vehicle', 'v']:
            node = env.vehicles[env.vehicle_ids_as_index[idx]]
        elif type in ['uav', 'u']:
            node = env.UAVs[env.uav_ids_as_index[idx]]
        elif type in ['rsu', 'i']:
            node = env.RSUs[env.rsu_ids_as_index[idx]]
        elif type in ['cloud_server', 'c']:
            node = env.cloudServers[env.cloud_server_ids_as_index[idx]]
        return node.to_dict()

    
    @staticmethod
    def getAllNodeInfos(env, type_list = ['vehicle', 'uav', 'rsu', 'cloud_server']):
        """Get all node infos. type_list = ['vehicle', 'uav', 'rsu', 'cloud_server']

        Args:
            env (AirFogSimEnv): The environment.
            type_list (list): The list of the required types.

        Returns:
            list: The list of the node infos.
        """
        all_nodes = []
        for required_type in type_list:
            if required_type == 'vehicle':
                all_nodes += list(env.vehicles.values())
            elif required_type == 'uav':
                all_nodes += list(env.UAVs.values())
            elif required_type == 'rsu':
                all_nodes += list(env.RSUs.values())
            elif required_type == 'cloud_server':
                all_nodes += list(env.cloudServers.values())
        all_node_infos = []
        for node in all_nodes:
            all_node_infos.append(node.to_dict())
        return all_node_infos

    @staticmethod
    def getAllTaskNodeIds(env):
        """Get all the task node ids.

        Args:
            env (AirFogSimEnv): The AirFogSim environment.

        Returns:
            list: The list of the task node ids. (copy of the original list)
        """
        return env.task_node_ids.copy()
    
    @staticmethod
    def setTaskNodeIds(env, task_node_ids):
        """Set the task node ids.

        Args:
            env (AirFogSimEnv): The AirFogSim environment.
            task_node_ids (list): The list of the task node ids.
        """
        env.task_node_ids = task_node_ids
        
    @staticmethod
    def getNodeInfoById(env, node_id: str):
        """Get the node info by the node id.

        Args:
            env (AirFogSimEnv): The environment.
            node_id (str): The node id.

        Returns:
            dict: The node info.
        """
        node = env._getNodeById(node_id)
        return node.to_dict()

    @staticmethod
    def getNodeNumByType(env,type:str):
        """Get the node num by type.

        Args:
            env (AirFogSimEnv): The environment.
            type (str): The type, ['vehicle' or 'v', 'uav' or 'u', 'rsu' or 'i', 'cloud_server' or 'c']

        Returns:
            int: The node num.
        """
        type = type.lower()
        if type in ['vehicle', 'v']:
            num = len(env.vehicle_ids_as_index)
        elif type in ['uav', 'u']:
            num = len(env.uav_ids_as_index)
        elif type in ['rsu', 'i']:
            num = len(env.rsu_ids_as_index)
        elif type in ['cloud_server', 'c']:
            num = len(env.cloud_server_ids_as_index)
        return num

    @staticmethod
    def getNeighborNodeInfosById(env, node_id: str, sorted_by = 'distance', reverse = False, max_num = 10):
        """Get the neighbor node infos by the node id.

        Args:
            env (AirFogSimEnv): The environment.
            node_id (str): The node id.
            sorted_by (str): The attribute to sort the neighbor node infos. ['distance', 'cpu']
            reverse (bool): The flag to indicate whether the neighbor node infos are sorted in reverse

        Returns:
            list: The neighbor node infos.
        """
        assert sorted_by in ['distance', 'cpu'], "sorted_by should be 'distance' or 'cpu'"
        node = env._getNodeById(node_id)
        node_ids_map = env.traffic_manager.map_by_grid # numpy array
        row_idx, col_idx = env.traffic_manager.getMapIndexByNodeId(node_id)
        if row_idx is None or col_idx is None:
            return []
        # bfs from row_idx, col_idx，search for max_num nodes
        max_bfs_depth = max(node_ids_map.shape[0] - row_idx, node_ids_map.shape[1] - col_idx, row_idx, col_idx)
        neighbor_node_infos = []
        cpu_list = []
        distance_list = []
        for bfs_depth in range(0, max_bfs_depth):
            # for each depth, search for indexes where (row_idx, col_idx) is the center, and the distance between them is bfs_depth
            for i in range(-bfs_depth, bfs_depth + 1):
                for j in range(-bfs_depth, bfs_depth + 1):
                    if not (i == -bfs_depth or i == bfs_depth or j == -bfs_depth or j == bfs_depth): continue
                    if row_idx + i < 0 or row_idx + i >= node_ids_map.shape[0] or col_idx + j < 0 or col_idx + j >= node_ids_map.shape[1]:
                        continue
                    for neighbor_node_id in node_ids_map[row_idx + i][col_idx + j]:
                        if neighbor_node_id == node_id: continue
                        neighbor_node = env._getNodeById(neighbor_node_id)
                        neighbor_node_infos.append(neighbor_node.to_dict())
                        cpu_list.append(neighbor_node.getFogProfile()['cpu'])
                        distance_list.append(env._getDistanceBetweenNodes(node, neighbor_node))
            if len(neighbor_node_infos) >= max_num:
                break
        if sorted_by == 'distance':
            neighbor_node_infos = [x for _, x in sorted(zip(distance_list, neighbor_node_infos), key=lambda pair: pair[0], reverse=reverse)]
        elif sorted_by == 'cpu':
            neighbor_node_infos = [x for _, x in sorted(zip(cpu_list, neighbor_node_infos), key=lambda pair: pair[0], reverse=reverse)]
        return neighbor_node_infos

    @staticmethod
    def getNodeTypeById(env,node_id):
        """Get the node type by the node id.

        Args:
            env (AirFogSimEnv): The environment.
            node_id (str): The node id.

        Returns:
            str: The type of the node. 'V' for vehicle, 'U' for UAV, 'I' for RSU, 'C' for cloud server.
        """
        return env._getNodeTypeById(node_id)

    @staticmethod
    def getNodeIdxById(env,node_id):
        """Get the node index by the node id.

        Args:
            env (AirFogSimEnv): The environment.
            node_id (str): The node id.

        Returns:
            int: The index of the node.
        """
        return env._getNodeIdxById(node_id)

    @staticmethod
    def getTaskStates(env, all_tasks):
        """Get the task states numpy.

        Args:
            env (AirFogSimEnv): The environment.
            all_tasks (list): The list of the task infos.

        Returns:
            list: The list of the task states.
        """
        task_states = []
        for task in all_tasks:
            task_states.append(env.node_state_manager.transformTaskToTaskState(task, env.simulation_time))
        return task_states

    @staticmethod
    def getTaskNodeStates(env):
        """Get the task node states numpy.

        Args:
            env (AirFogSimEnv): The environment.

        Returns:
            list: The list of the task node states.
        """
        task_node_states = []
        for task_node_id in env.task_node_ids:
            task_node = env._getNodeById(task_node_id)
            task_node_states.append(env.node_state_manager.transformNodeToNodeState(task_node, env.simulation_time, 'TN'))
        return task_node_states

    @staticmethod
    def getFogNodeStates(env):
        """Get the fog node states numpy.

        Args:
            env (AirFogSimEnv): The environment.

        Returns:
            list: The list of the fog node states.
        """
        fog_node_states = []
        uav_fog_nodes = EntityScheduler.getFogNodesByType(env, 'uav')
        rsu_fog_nodes = EntityScheduler.getFogNodesByType(env, 'rsu')
        vehicle_fog_nodes = EntityScheduler.getFogNodesByType(env, 'vehicle')
        for fog_node in uav_fog_nodes + rsu_fog_nodes + vehicle_fog_nodes:
            fog_node_states.append(env.node_state_manager.transformNodeToNodeState(fog_node, env.simulation_time, 'FN'))
        return fog_node_states
    
    @staticmethod
    def getFogNodesByType(env, type):
        """Get the fog nodes by the type.

        Args:
            env (AirFogSimEnv): The environment.
            type (str): The type, ['vehicle' or 'v', 'uav' or 'u', 'rsu' or 'i']

        Returns:
            list: The list of the fog nodes.
        """
        if type in ['vehicle', 'v']:
            fog_nodes = [vehicle for vehicle in env.vehicles.values()]
        elif type in ['uav', 'u']:
            fog_nodes = [uav for uav in env.UAVs.values()]
        elif type in ['rsu', 'i']:
            fog_nodes = [rsu for rsu in env.RSUs.values()]
        # 判断fog_nodes.getId()是否在env.task_node_ids中
        fog_nodes = [fog_node for fog_node in fog_nodes if fog_node.getId() not in env.task_node_ids]
        return fog_nodes