class SimpleNode:
    def __init__(self, id, position_x, position_y, position_z, speed=0, acceleration=0, angle=0):
        self._id = id
        self._position_x = position_x
        self._position_y = position_y
        self._position_z = position_z
        self._speed = speed
        self._angle = angle
        self._acceleration = acceleration
        self._is_transmitting = False
        self._is_receiving = False
        self._revenue = 0
        self._ai_model_dict = {}
        self._token = None # Token for the node to access the vehicular network

    def to_dict(self):
        infos = {}
        for key, value in self.__dict__.items():
            if key.startswith("_"):
                infos[key[1:]] = value
            else:
                infos[key] = value
        return infos

    def setToken(self, token):
        self._token = token

    def getToken(self):
        return self._token

    def updateAIModel(self, model_name, model):
        self._ai_model_dict[model_name] = model

    def getAIModel(self, model_name):
        return self._ai_model_dict[model_name]

    def getRevenue(self):
        return self._revenue
    
    def setRevenue(self, revenue):
        self._revenue = revenue

    def setTransmitting(self, is_transmitting):
        self._is_transmitting = is_transmitting

    def setReceiving(self, is_receiving):
        self._is_receiving = is_receiving

    def getId(self):
        return self._id
    
    def getPosition(self):
        return (self._position_x, self._position_y, self._position_z)
    
    def getSpeed(self):
        return self._speed
    
    def getAcceleration(self):
        return self._acceleration
    
    def setPosition(self, position_x, position_y, position_z):
        self._position_x = position_x
        self._position_y = position_y
        self._position_z = position_z

    def setSpeed(self, speed):
        self._speed = speed

    def setAcceleration(self, acceleration):
        self._acceleration = acceleration

    def __str__(self):
        return "SimpleNode: id={}, position=({}, {}, {}), speed={}, acceleration={}".format(self._id, self._position_x, self._position_y, self._position_z, self._speed, self._acceleration)