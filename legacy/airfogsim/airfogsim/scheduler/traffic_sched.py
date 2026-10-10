import random

from shapely.geometry import LineString, Point
import copy

import numpy as np

from .base_sched import BaseScheduler
from airfogsim.utils import math_utils

class TrafficScheduler(BaseScheduler):
    @staticmethod
    def getConfig(env, name):
        return env.traffic_manager.getConfig(name)

    @staticmethod
    def getCurrentTime(env):
        return env.traffic_manager.getCurrentTime()

    @staticmethod
    def getTrafficInterval(env):
        return env.traffic_interval

    @staticmethod
    def getDistanceBetweenNodesById(env, node_id_1, node_id_2):
        return env.getDistanceBetweenNodesById(node_id_1, node_id_2)

    @staticmethod
    def getUAVTrafficInfos(env):
        return env.traffic_manager.getUAVTrafficInfos()

    @staticmethod
    def getRSUTrafficInfos(env):
        return env.traffic_manager.getRSUInfos()

    @staticmethod
    def setUAVMobilityPatterns(env, UAV_mobility_patterns):
        organized_patterns = {}
        for UAV_id, UAV_mobile_pattern in UAV_mobility_patterns.items():
            organized_patterns[UAV_id] = {}
            organized_patterns[UAV_id]['angle'] = UAV_mobile_pattern['angle']
            organized_patterns[UAV_id]['phi'] = UAV_mobile_pattern['phi']
            organized_patterns[UAV_id]['speed'] = UAV_mobile_pattern['speed']
        env.uav_mobility_patterns = organized_patterns

    @staticmethod
    def getNodeInfosInRange(env, target_position, distance_threshold,node_type):
        if node_type == 'U':
            node_infos=env.traffic_manager.getUAVTrafficInfos()
        elif node_type == 'V':
            node_infos = env.traffic_manager.getVehicleTrafficInfos()
        else:
            return None
        candidate_node_infos = {}
        node_ids_list = list(node_infos.keys())
        node_positions = [node_infos[node_id]['position'] for node_id in node_ids_list]
        if len(node_positions) == 0:
            return {}
        node_positions = np.asarray(node_positions)
        distances = np.linalg.norm(node_positions - np.asarray(target_position),axis=1)
        selected_node_ids = np.where(distances <= distance_threshold)[0]
        for idx in selected_node_ids:
            node_id = node_ids_list[idx]
            candidate_node_infos[node_id] = node_infos[node_id]

        return candidate_node_infos

    # @staticmethod
    # def setUAVSpeedAndDirectionByNodeId(env, node_id: str, speed: float, angle: float, phi: float):
    #     """Set the UAV speed and direction by the node id.
    #
    #     Args:
    #         env (AirFogSimEnv): The environment.
    #         node_id (str): The node id.
    #         speed (float): The speed.
    #         angle (float): The angle (angle in 2D plane).
    #         phi (float): The phi (angle in 3D plane).
    #     """
    #     env.traffic_manager.updateUAVMobilityPatternById(node_id, {'speed': speed, 'angle': angle, 'phi': phi})

    @staticmethod
    def getRandomTargetPositionForUAV(env, UAV_id):
        # 从env的traffic manager获取当前uav位置；随机找一个不在禁飞区的位置作为目标位置，并且保证两个位置之间的直线也不经过禁飞区
        current_position = env.traffic_manager.getNodePositionById(UAV_id)
        target_positions = env.traffic_manager.getAllJunctionPositions()
        nonfly_zones = env.traffic_manager.getNonFlyZones()  # [[[x1,y1],[x2,y2],[x3,y3]], ...]
        target_position = None
        while target_position is None and len(target_positions) > 0:
            target_position = target_positions.pop(np.random.randint(0, len(target_positions)))
            for nonfly_zone in nonfly_zones:
                if TrafficScheduler.isLineCrossNonFlyZone(current_position, target_position, nonfly_zone):
                    target_position = None
                    break
        return target_position

    @staticmethod
    def isLineCrossNonFlyZone(start_position, end_position, nonfly_zone):
        line = LineString([Point(start_position), Point(end_position)])
        for i in range(len(nonfly_zone)):
            start_point = nonfly_zone[i]
            end_point = nonfly_zone[(i + 1) % len(nonfly_zone)]
            if line.crosses(LineString([Point(start_point), Point(end_point)])):
                return True
        return False

    @staticmethod
    def getDefaultUAVMobilityPattern(env, UAV_id, current_position, target_position):
        if target_position is None:
            # 悬停
            mobility_pattern = {'angle': 0, 'phi': 0, 'speed': 0}
            target_position = current_position
        else:
            delta_x = target_position[0] - current_position[0]
            delta_y = target_position[1] - current_position[1]
            delta_z = target_position[2] - current_position[2]

            # 计算 xy 平面的方位角
            angle = np.arctan2(delta_y, delta_x)

            # 计算 z 相对于 xy 平面的仰角
            distance_xy = np.sqrt(delta_x ** 2 + delta_y ** 2)
            phi = np.arctan2(delta_z, distance_xy)

            mobility_pattern = {'angle': angle, 'phi': phi}
            UAV_speed_range = TrafficScheduler.getConfig(env, 'UAV_speed_range')
            mobility_pattern['speed'] = UAV_speed_range[1]
        mobility_pattern['target_position'] = target_position
        return mobility_pattern

    @staticmethod
    def getNextPositionOfUAV(env, UAV_id,is_random=False):
        route = env.uav_routes.get(UAV_id, [])
        if len(route) == 0:
            return None
        if is_random:
            new_idx = random.randint(0, len(route) - 1)
        else:
            new_idx = 0
        env.setUAVNextMissionIdx(UAV_id, new_idx)
        return copy.deepcopy(route[new_idx]['position'])

        # current_idx=env.getUAVNextMissionIdx(UAV_id)
        # if current_idx is None:
        #     if is_random:
        #         new_idx=random.randint(0, len(route) - 1)
        #     else:
        #         new_idx=0
        #     env.setUAVNextMissionIdx(UAV_id, new_idx)
        #     return copy.deepcopy(route[new_idx]['position'])
        # else:
        #     return copy.deepcopy(route[current_idx]['position'])

    @staticmethod
    def addUAVRoute(env,mission_id, UAV_id, position,time,ddl):
        route = env.uav_routes.get(UAV_id, [])
        route.append({ 'mission_id':mission_id,'position': position, 'to_stay_time': time,'ddl':ddl})
        env.uav_routes[UAV_id] = route

    @staticmethod
    def updateRoute(env, UAV_id, current_position, distance_threshold, stay_time):
        current_time=env.traffic_manager.getCurrentTime()
        route = env.uav_routes.get(UAV_id, [])

        route_idx=env.getUAVNextMissionIdx(UAV_id)
        if route_idx is not None:
            next_mission_id=route[route_idx]['mission_id']
        else:
            next_mission_id=None

        route = [poi for poi in route if poi['ddl'] > current_time] # 删除超过ddl的mission
        current_position_2d=current_position[:2]

        to_delete_idx = []
        for idx, poi in enumerate(route):
            target_position = poi['position']
            target_position_2d=target_position[:2]
            distance = math_utils.calculate_distance(current_position_2d,target_position_2d)
            if distance < distance_threshold:
                poi['to_stay_time'] = max(poi['to_stay_time'] - stay_time, 0)
                if poi['to_stay_time'] <= 0:
                    to_delete_idx.append(idx)
        # 删除索引处的记录（反向删除）
        for idx in sorted(to_delete_idx, reverse=True):
            del route[idx]
        env.uav_routes[UAV_id] = route

        # 更新idx
        route_idx = None
        for idx, poi in enumerate(route):
            if poi['mission_id'] == next_mission_id:
                route_idx=idx
                break
        if route_idx is None:
            env.clearUAVNextMissionIdx(UAV_id)
            return
        else:
            env.setUAVNextMissionIdx(UAV_id, route_idx)



    @staticmethod
    def getNearestRSUById(env, node_id):
        rsu_infos = env.traffic_manager.getRSUInfos()
        rsu_ids = list(rsu_infos.keys())
        rsu_positions = [rsu_infos[rsu_id]['position'] for rsu_id in rsu_ids]
        node_position = env.traffic_manager.getNodePositionById(node_id)
        if node_position is None:
            return rsu_ids[0]
        distances = np.linalg.norm(np.asarray(rsu_positions) - np.asarray(node_position),axis=1)
        nearest_idx = np.argmin(distances)
        return rsu_ids[nearest_idx]

    @staticmethod
    def getFarthestRSUById(env, node_id):
        rsu_infos = env.traffic_manager.getRSUInfos()
        rsu_ids = list(rsu_infos.keys())
        rsu_positions = [rsu_infos[rsu_id]['position'] for rsu_id in rsu_ids]
        node_position = env.traffic_manager.getNodePositionById(node_id)
        if node_position is None:
            return rsu_ids[0]
        distances = np.linalg.norm(np.asarray(rsu_positions) - np.asarray(node_position),axis=1)
        nearest_idx = np.argmax(distances)
        return rsu_ids[nearest_idx]

    @staticmethod
    def getMapRange(env, axis):
        return env.traffic_manager.getMapRange(axis)

    @staticmethod
    def completeStrId(env,node_id,type):
        return env.traffic_manager.completeStrId(node_id,type)