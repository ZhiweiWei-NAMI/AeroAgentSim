class Sensor:
    ''' Sensor class to represent a sensor on the UAV/vehicle/RSU. '''
    def __init__(self,sensor_id,sensor_type,sensor_accuracy,deployed_node_id):
        """The constructor of the Sensor class.

        Args:
            sensor_type (str): The type of sensor.
            sensor_accuracy (float): The accuracy of sensor in [0,1].
            deployed_node_id: The node that sensor deployed.
        """
        self._sensor_id=sensor_id
        self._sensor_type=sensor_type
        self._sensor_accuracy=sensor_accuracy
        self._deployed_node_id=deployed_node_id
        self._is_using=False #Is it being used
        self._is_available=True #Is it within the map range and functioning properly

    def getSensorId(self):
        """get sensor id.

        Args:

        Returns:
            str: The sensor id.
        """
        return self._sensor_id

    def getSensorType(self):
        """get sensor type.

        Args:

        Returns:
            str: The type of sensor.
        """
        return self._sensor_type

    def getSensorAccuracy(self):
        """get sensor type.

        Args:

        Returns:
            float: The accuracy of sensor.
        """
        return self._sensor_accuracy

    def getDeployedNodeId(self):
        """get deployed node id.

        Args:

        Returns:
            str: The node id
        """
        return self._deployed_node_id

    def isUsing(self):
        """Check if the sensor is being used.

        Args:

        Returns:
            bool: The state of sensor (True: if the sensor is being used)
        """
        return self._is_using

    def isAvailable(self):
        """Check if the sensor is available.

        Args:

        Returns:
            bool: The state of sensor (True: if the sensor is available)
        """
        return self._is_available

    def isUsable(self):
        """Check if the sensor is usable.

        Args:

        Returns:
            bool: The state of sensor (True: if the sensor is available and not using)
        """
        return self._is_available and not self._is_using

    def startUse(self):
        """Change state of sensor to using.

        Args:

        Returns:

        """
        assert self.isUsable(),'The sensor cannot be used' # Not within the map range or is occupied or is damaged
        self._is_using=True

    def endUse(self):
        """Change state of sensor to idle.

        Args:

        Returns:

        """
        self._is_using = False

    def disable(self):
        """Set state of sensor(unavailable).

        Args:

        Returns:

        """
        self._is_available = False
        self._is_using=False

    def activate(self):
        """Set state of sensor(available).

        Args:

        Returns:

        """
        self._is_available = True
        self._is_using=False



