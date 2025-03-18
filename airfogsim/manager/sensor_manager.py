import math
import random
from ..entities.sensor import Sensor


class SensorManager:
    """ Sensor Manager is responsible for generating and deploying sensors and managing the sensor status.
    """
    NODE_TYPE = ['vehicle', 'UAV']
    STATE = ['idle', 'busy', 'unavailable']
    ACCURACY_RANGE_VEH = [0.30, 0.40, 0.50, 0.60, 0.70, 0.80]
    ACCURACY_RANGE_UAV = [0.75, 0.80, 0.85, 0.90, 0.95, 1.00]

    def __init__(self, config_sensing, traffic_manager):
        assert set(config_sensing['node_type']).issubset(
            SensorManager.NODE_TYPE), 'The node type is not supported. Only support {}'.format(SensorManager.NODE_TYPE)
        self._config_sensing = config_sensing
        self._busy_sensors = {}  # key: node_id, value: list of busy sensors
        self._idle_sensors = {}  # key: node_id, value: list of idle sensors
        self._unavailable_sensors = {}  # key: node_id, value: list of unavailable sensors
        self._sensor_id_counter = 0
        self._sensors_per_node = config_sensing['sensors_per_node']
        self._node_type = config_sensing['node_type']
        self._sensor_type_num = config_sensing['sensor_type_num']
        self._initializeSensors(traffic_manager)

    def reset(self,traffic_manager):
        self._busy_sensors = {}  # key: node_id, value: list of busy sensors
        self._idle_sensors = {}  # key: node_id, value: list of idle sensors
        self._unavailable_sensors = {}  # key: node_id, value: list of unavailable sensors
        self._sensor_id_counter = 0
        self._initializeSensors(traffic_manager)

    def __getNewSensorId(self):
        new_id = self._sensor_id_counter
        self._sensor_id_counter += 1
        return new_id

    def _initializeSensors(self, traffic_manager):
        UAV_infos = traffic_manager.getUAVTrafficInfos()
        vehicle_infos = traffic_manager.getVehicleTrafficInfos()

        for UAV_id in UAV_infos.keys():
            self._initializeSensorsForNode(UAV_id)
        for vehicle_id in vehicle_infos.keys():
            self._initializeSensorsForNode(vehicle_id)

    def _initializeSensorsForNode(self, node_id):
        if node_id in self._idle_sensors.keys():
            return
        self._idle_sensors[node_id] = self._idle_sensors.get(node_id, [])

        node_type=self.__getNodeTypeById(node_id)
        if node_type == 'U':
            ACCURACY_RANGE=SensorManager.ACCURACY_RANGE_UAV
        elif node_type == 'V':
            ACCURACY_RANGE=SensorManager.ACCURACY_RANGE_VEH
        else:
            raise Exception('Unknown node type')
        group_num = math.floor(self._sensors_per_node/self._sensor_type_num)
        for idx in range(self._sensors_per_node):
            if idx<self._sensors_per_node*group_num:
                new_sensor_type = 'sensor_type_' + str(idx%self._sensor_type_num+1)
            else:
                new_sensor_type = 'sensor_type_' + str(random.randint(1, self._sensor_type_num))
            new_sensor_accuracy = random.choice(ACCURACY_RANGE)  # 随机生成0-1之间的离散精度
            new_sensor_id = 'sensor_' + str(self.__getNewSensorId())
            new_sensor = Sensor(new_sensor_id, new_sensor_type, new_sensor_accuracy, node_id)
            self._idle_sensors[node_id].append(new_sensor)

    def completeSensorId(self, id_num):
        return 'sensor_' + str(id_num)

    def _getIdleSensorById(self, sensor_id):
        target_sensor = None
        target_node_id = None
        for node_id, sensors in self._idle_sensors.items():
            for sensor in sensors:
                if sensor.getSensorId() == sensor_id:
                    target_sensor = sensor
                    target_node_id = node_id
        return target_node_id, target_sensor

    def _getBusySensorById(self, sensor_id):
        target_sensor = None
        target_node_id = None
        for node_id, sensors in self._busy_sensors.items():
            for sensor in sensors:
                if sensor.getSensorId() == sensor_id:
                    target_sensor = sensor
                    target_node_id = node_id
        return target_node_id, target_sensor

    def _getUnavailableSensorById(self, sensor_id):
        target_sensor = None
        target_node_id = None
        for node_id, sensors in self._unavailable_sensors.items():
            for sensor in sensors:
                if sensor.getSensorId() == sensor_id:
                    target_sensor = sensor
                    target_node_id = node_id
        return target_node_id, target_sensor

    def _addSensor(self, sensors_dict, node_id, sensor):
        sensors_dict[node_id] = sensors_dict.get(node_id, [])
        sensors_dict[node_id].append(sensor)

    def _removeSensor(self, sensors_dict, node_id, sensor_id):
        assert node_id in sensors_dict
        sensors_dict[node_id] = [sensor for sensor in sensors_dict[node_id] if sensor.getSensorId() != sensor_id]
        # # 删除多余的键
        if len(sensors_dict[node_id])==0:
            del sensors_dict[node_id]

    def initializeSensorsByNodeId(self, node_id):
        self._initializeSensorsForNode(node_id)

    def startUseById(self, sensor_id):
        """start use the sensor.

        Args:
            sensor_id (str): The sensor id.

        Returns:

        Examples:
            sensor_manager.startUseById('Sensor_1')
        """
        node_id, sensor = self._getIdleSensorById(sensor_id)
        assert sensor is not None
        sensor.startUse()
        self._removeSensor(self._idle_sensors, node_id, sensor_id)
        self._addSensor(self._busy_sensors, node_id, sensor)

    def endUseById(self, sensor_id):
        """stop use the sensor.

        Args:
            sensor_id (str): The sensor id.

        Returns:

        Examples:
            sensor_manager.stopUseById('Sensor_1')
        """
        node_id, sensor = self._getBusySensorById(sensor_id)
        assert sensor is not None
        sensor.endUse()
        self._removeSensor(self._busy_sensors, node_id, sensor_id)
        self._addSensor(self._idle_sensors, node_id, sensor)

    def _setUnavailableById(self, sensor_id):
        """make the sensor unavailable.

        Args:
            sensor_id (str): The sensor id.

        Returns:

        """

        node_id, sensor = self._getIdleSensorById(sensor_id)
        if sensor is not None:
            sensor.disable()
            self._removeSensor(self._idle_sensors, node_id, sensor_id)
            self._addSensor(self._unavailable_sensors, node_id, sensor)
            return

        node_id, sensor = self._getBusySensorById(sensor_id)
        if sensor is not None:
            sensor.disable()
            self._removeSensor(self._busy_sensors, node_id, sensor_id)
            self._addSensor(self._unavailable_sensors, node_id, sensor)
            return

        assert sensor is not None, f'{sensor_id} is not exist'

    def getUsableById(self, sensor_id):
        """check if the sensor is usable.

        Args:
            sensor_id (str): The sensor id.

        Returns:

        Examples:
            sensor_manager.getUsableById('Sensor_1')
        """
        _, sensor = self._getIdleSensorById(sensor_id)
        if sensor is not None:
            return sensor.isUsable()

        _, sensor = self._getBusySensorById(sensor_id)
        if sensor is not None:
            return sensor.isUsable()

        assert sensor is not None, f'{sensor_id} is not exist'

    def getSensorsByStateAndType(self, state, type=None):
        assert state in SensorManager.STATE, 'The state is not supported. Only support {}'.format(SensorManager.STATE)
        sensors_dict = {}
        target_sensors_dict = {}

        if state == 'idle':
            sensors_dict = self._idle_sensors
        elif state == 'busy':
            sensors_dict = self._busy_sensors
        elif state == 'unavailable':
            sensors_dict = self._unavailable_sensors

        if type is None:
            target_sensors_dict = sensors_dict
        else:
            for node_id, sensors in sensors_dict.items():

                # 用列表推导式快速筛选符合条件的传感器
                filtered_sensors = [sensor for sensor in sensors if sensor._sensor_type== type]
                # 如果有匹配的传感器，才添加到 target_sensors_dict
                if filtered_sensors:
                    target_sensors_dict[node_id] = filtered_sensors

        return target_sensors_dict

    def getUsingSensorsNumByNodeId(self, node_id):
        sensor_list = self._busy_sensors.get(node_id, [])
        for sensor in sensor_list:
            assert sensor.isUsing()
        return len(sensor_list)

    def disableByNodeId(self, node_id):
        sensor_list = self._idle_sensors.get(node_id, [])
        for sensor in sensor_list:
            self._setUnavailableById(sensor.getSensorId())

        sensor_list = self._busy_sensors.get(node_id, [])
        for sensor in sensor_list:
            self._setUnavailableById(sensor.getSensorId())

    def getBusySensorsNum(self):
        num = 0
        for node_id, sensors in self._busy_sensors.items():
            num += len(sensors)
        return num

    def getIdleSensorsNum(self):
        num = 0
        for node_id, sensors in self._idle_sensors.items():
            num += len(sensors)
        return num

    def getUnavailableSensorsNum(self):
        num = 0
        for node_id, sensors in self._unavailable_sensors.items():
            num += len(sensors)
        return num

    def getAccuracyById(self, sensor_id):
        for node_id, sensors in self._idle_sensors.items():
            for sensor in sensors:
                if sensor.getSensorId() == sensor_id:
                    return sensor.getSensorAccuracy()
        for node_id, sensors in self._busy_sensors.items():
            for sensor in sensors:
                if sensor.getSensorId() == sensor_id:
                    return sensor.getSensorAccuracy()
        for node_id, sensors in self._unavailable_sensors.items():
            for sensor in sensors:
                if sensor.getSensorId() == sensor_id:
                    return sensor.getSensorAccuracy()

    def getNodeIdById(self, sensor_id):
        for node_id, sensors in self._idle_sensors.items():
            for sensor in sensors:
                if sensor.getSensorId() == sensor_id:
                    return sensor.getDeployedNodeId()
        for node_id, sensors in self._busy_sensors.items():
            for sensor in sensors:
                if sensor.getSensorId() == sensor_id:
                    return sensor.getDeployedNodeId()
        for node_id, sensors in self._unavailable_sensors.items():
            for sensor in sensors:
                if sensor.getSensorId() == sensor_id:
                    return sensor.getDeployedNodeId()

    def getSensorById(self, sensor_id):
        for node_id, sensors in self._idle_sensors.items():
            for sensor in sensors:
                if sensor.getSensorId() == sensor_id:
                    return sensor
        for node_id, sensors in self._busy_sensors.items():
            for sensor in sensors:
                if sensor.getSensorId() == sensor_id:
                    return sensor
        for node_id, sensors in self._unavailable_sensors.items():
            for sensor in sensors:
                if sensor.getSensorId() == sensor_id:
                    return sensor

    def getConfig(self, name):
        return self._config_sensing.get(name, None)

    def __getNodeTypeById(self, node_id):
        node_id = node_id.capitalize()
        assert node_id[0] in ['V', 'R', 'U', 'C'], f'Invalid node type of {node_id}'
        if node_id[0] == 'V':
            return 'V'
        elif node_id[0] == 'R':
            return 'I'
        elif node_id[0] == 'U':
            return 'U'
        elif node_id[0] == 'C':
            return 'C'
        else:
            return None