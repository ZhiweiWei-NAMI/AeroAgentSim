import random

import numpy as np

from .base_sched import BaseScheduler
from airfogsim.utils import math_utils


class SensorScheduler(BaseScheduler):
    @staticmethod
    def getAccuracyById(env, sensor_id):
        env.sensor_manager.getAccuracyById(sensor_id)

    @staticmethod
    def getNodeIdById(env, sensor_id):
        env.sensor_manager.getNodeIdById(sensor_id)

    @staticmethod
    def getHighestAccurateIdleSensorOnUAV(env, sensor_type, lowest_accuracy, excluded_sensor_ids):
        candidate_sensors = env.sensor_manager.getSensorsByStateAndType('idle', sensor_type)
        appointed_node_id = None
        appointed_sensor_id = None
        appointed_sensor_accuracy = 0

        # Choose the sensor with the highest accuracy among the idle sensors
        for node_id, sensors in candidate_sensors.items():
            for sensor in sensors:
                if sensor.getSensorAccuracy() > max(lowest_accuracy,
                                                    appointed_sensor_accuracy) and sensor.getSensorId() not in excluded_sensor_ids and env._getNodeTypeById(
                        node_id) == 'U':
                    # nonlocal appointed_node_id, appointed_sensor_id, appointed_sensor_accuracy
                    appointed_sensor_accuracy = sensor.getSensorAccuracy()
                    appointed_node_id = node_id
                    appointed_sensor_id = sensor.getSensorId()
        return appointed_node_id, appointed_sensor_id, appointed_sensor_accuracy

    @staticmethod
    def getLowestAccurateIdleSensorOnUAV(env, sensor_type, lowest_accuracy, excluded_sensor_ids):
        candidate_sensors = env.sensor_manager.getSensorsByStateAndType('idle', sensor_type)
        appointed_node_id = None
        appointed_sensor_id = None
        appointed_sensor_accuracy = 0

        # Choose the sensor with the highest accuracy among the idle sensors
        for node_id, sensors in candidate_sensors.items():
            for sensor in sensors:
                if sensor.getSensorAccuracy() > lowest_accuracy and sensor.getSensorId() not in excluded_sensor_ids and env._getNodeTypeById(
                        node_id) == 'U':
                    if appointed_sensor_accuracy == 0 or sensor.getSensorAccuracy() < appointed_sensor_accuracy:
                        # nonlocal appointed_node_id, appointed_sensor_id, appointed_sensor_accuracy
                        appointed_sensor_accuracy = sensor.getSensorAccuracy()
                        appointed_node_id = node_id
                        appointed_sensor_id = sensor.getSensorId()
        return appointed_node_id, appointed_sensor_id, appointed_sensor_accuracy

    @staticmethod
    def getLowestAccurateIdleSensorInRangeOnUAV(env, sensor_type, lowest_accuracy,target_position,distance_threshold, excluded_sensor_ids):
        candidate_sensors = env.sensor_manager.getSensorsByStateAndType('idle', sensor_type)
        appointed_node_id = None
        appointed_sensor_id = None
        appointed_sensor_accuracy = 0

        # Choose the sensor with the highest accuracy among the idle sensors
        for node_id, sensors in candidate_sensors.items():
            node=env._getNodeById(node_id)
            node_type=env._getNodeTypeById(node_id)
            node_position=node.getPosition()
            distance=math_utils.calculate_distance(node_position,target_position)
            if not (distance < distance_threshold and node_type=='U'):
                continue
            for sensor in sensors:
                if sensor.getSensorAccuracy() > lowest_accuracy and sensor.getSensorId() not in excluded_sensor_ids:
                    if appointed_sensor_accuracy == 0 or sensor.getSensorAccuracy() < appointed_sensor_accuracy:
                        # nonlocal appointed_node_id, appointed_sensor_id, appointed_sensor_accuracy
                        appointed_sensor_accuracy = sensor.getSensorAccuracy()
                        appointed_node_id = node_id
                        appointed_sensor_id = sensor.getSensorId()
        return appointed_node_id, appointed_sensor_id, appointed_sensor_accuracy

    @staticmethod
    def getLowestAccurateIdleSensor(env, sensor_type, lowest_accuracy, target_position, node_infos,
                                    excluded_sensor_ids):
        candidate_sensors = env.sensor_manager.getSensorsByStateAndType('idle', sensor_type)
        appointed_node_id = None
        appointed_sensor_id = None
        appointed_sensor_accuracy = 0

        # Choose the sensor with the highest accuracy among the idle sensors
        for node_id, node_info in node_infos.items():
            sensors = candidate_sensors.get(node_id, [])
            for sensor in sensors:
                if sensor.getSensorAccuracy() > lowest_accuracy and sensor.getSensorId() not in excluded_sensor_ids:
                    if appointed_sensor_accuracy == 0 or sensor.getSensorAccuracy() < appointed_sensor_accuracy:
                        appointed_sensor_accuracy = sensor.getSensorAccuracy()
                        appointed_node_id = node_id
                        appointed_sensor_id = sensor.getSensorId()
        return appointed_node_id, appointed_sensor_id, appointed_sensor_accuracy

    @staticmethod
    def getNearestIdleSensorInNodes(env, sensor_type, lowest_accuracy, target_position, node_infos,
                                    excluded_sensor_ids):
        candidate_sensors = env.sensor_manager.getSensorsByStateAndType('idle', sensor_type)
        appointed_node_id = None
        appointed_sensor_id = None
        appointed_sensor_accuracy = 0
        min_distance = None

        # Choose the sensor with the highest accuracy among the idle sensors
        for node_id, node_info in node_infos.items():
            sensors = candidate_sensors.get(node_id, [])
            node_position = node_info['position']
            distance=math_utils.calculate_distance(node_position,target_position)
            if min_distance is None or distance < min_distance:
                for sensor in sensors:
                    if sensor.getSensorAccuracy() >= lowest_accuracy and sensor.getSensorId() not in excluded_sensor_ids:
                        appointed_sensor_accuracy = sensor.getSensorAccuracy()
                        appointed_node_id = node_id
                        appointed_sensor_id = sensor.getSensorId()
                        min_distance=distance
        return appointed_node_id, appointed_sensor_id, appointed_sensor_accuracy

    @staticmethod
    def getSuitableIdleSensorInNodes(env, sensor_type, lowest_accuracy, target_position, node_infos,
                                    excluded_sensor_ids,delay=False):
        candidate_sensors = env.sensor_manager.getSensorsByStateAndType('idle', sensor_type)
        appointed_node_id = None
        appointed_sensor_id = None
        appointed_sensor_accuracy = 0
        min_speed = None
        min_distance = None
        has_approaching = None


        # Choose the sensor with the highest accuracy among the idle sensors
        for node_id, node_info in node_infos.items():
            sensors = candidate_sensors.get(node_id, [])
            node=env._getNodeById(node_id)
            node_type=env._getNodeTypeById(node_id)
            speed=node.getSpeed()
            node_position = node_info['position']
            distance=math_utils.calculate_distance(node_position,target_position)

            old_node_info = env.old_traffic_info.get(node_id, None)
            if old_node_info is None:
                is_approaching = False
                approaching_factor=1
            else:
                old_position = old_node_info['position']
                new_position = node_position
                is_approaching = math_utils.check_approaching(old_position[:2], new_position[:2], target_position[:2])
                approaching_factor = -1 if is_approaching else 1

            if delay==True and node_type=='V' and is_approaching==False:
                continue
            distance=distance*approaching_factor

            # if ((node_type=='U' and ((min_distance is None) or (min_distance is not None and distance < min_distance))) or
            #     (node_type=='V' and ((min_speed is None) or (min_speed is not None and speed < min_speed))  )):
            if (min_distance is None) or (min_distance is not None and distance < min_distance):
                for sensor in sensors:
                    if sensor.getSensorAccuracy() >= lowest_accuracy and sensor.getSensorId() not in excluded_sensor_ids:
                        appointed_sensor_accuracy = sensor.getSensorAccuracy()
                        appointed_node_id = node_id
                        appointed_sensor_id = sensor.getSensorId()
                        min_speed=speed
                        min_distance=distance
        return appointed_node_id, appointed_sensor_id, appointed_sensor_accuracy

    @staticmethod
    def getFarthestIdleSensorInNodes(env, sensor_type, lowest_accuracy, target_position, node_infos,
                                    excluded_sensor_ids):
        candidate_sensors = env.sensor_manager.getSensorsByStateAndType('idle', sensor_type)
        appointed_node_id = None
        appointed_sensor_id = None
        appointed_sensor_accuracy = 0
        max_distance = None

        # Choose the sensor with the highest accuracy among the idle sensors
        for node_id, node_info in node_infos.items():
            sensors = candidate_sensors.get(node_id, [])
            node_position = node_info['position']
            distance=math_utils.calculate_distance(node_position,target_position)
            if max_distance is None or distance > max_distance:
                for sensor in sensors:
                    if sensor.getSensorAccuracy() >= lowest_accuracy and sensor.getSensorId() not in excluded_sensor_ids:
                        appointed_sensor_accuracy = sensor.getSensorAccuracy()
                        appointed_node_id = node_id
                        appointed_sensor_id = sensor.getSensorId()
                        max_distance=distance
        return appointed_node_id, appointed_sensor_id, appointed_sensor_accuracy

    @staticmethod
    def getRandomIdleSensorInNodes(env, sensor_type, lowest_accuracy, node_infos,excluded_sensor_ids):
        candidate_sensors = env.sensor_manager.getSensorsByStateAndType('idle', sensor_type)
        candidate_sensors_attr_list=[]

        for node_id, node_info in node_infos.items():
            sensors = candidate_sensors.get(node_id, [])
            for sensor in sensors:
                if sensor.getSensorAccuracy() >= lowest_accuracy and sensor.getSensorId() not in excluded_sensor_ids:
                    candidate_sensors_attr_list.append([node_id,sensor.getSensorId(),sensor.getSensorAccuracy()])

        if len(candidate_sensors_attr_list) == 0:
            return None, None, None
        else:
            random_idx = random.randint(0, len(candidate_sensors_attr_list)-1) # 注意randint取闭区间
            appointed_node_id, appointed_sensor_id, appointed_sensor_accuracy = candidate_sensors_attr_list[random_idx]
            return appointed_node_id, appointed_sensor_id, appointed_sensor_accuracy

    @staticmethod
    def getRandomIdleSensorInNodesWithSelect(env, sensor_type, lowest_accuracy, node_infos, excluded_sensor_ids,cur_pos,max_sensor_num):
        candidate_sensors = env.sensor_manager.getSensorsByStateAndType('idle', sensor_type)
        candidate_sensors_attr_list = []

        for node_id, node_info in node_infos.items():
            sensors = candidate_sensors.get(node_id, [])
            node=env._getNodeById(node_id)
            node_position = node.getPosition()
            distance = math_utils.calculate_distance(node_position,cur_pos)
            for sensor in sensors:
                if sensor.getSensorAccuracy() >= lowest_accuracy and sensor.getSensorId() not in excluded_sensor_ids:
                    candidate_sensors_attr_list.append([distance,node_id, sensor.getSensorId(), sensor.getSensorAccuracy()])
            candidate_sensors_attr_list = sorted(candidate_sensors_attr_list, key=lambda x: x[0])

        if len(candidate_sensors_attr_list) == 0:
            return None, None, None
        else:
            random_idx = random.randint(0, min(max_sensor_num,len(candidate_sensors_attr_list) - 1))  # 注意randint取闭区间
            distance,appointed_node_id, appointed_sensor_id, appointed_sensor_accuracy = candidate_sensors_attr_list[
                random_idx]
            return appointed_node_id, appointed_sensor_id, appointed_sensor_accuracy
