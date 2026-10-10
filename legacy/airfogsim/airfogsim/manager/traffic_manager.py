import traci
import numpy as np
import random
import pandas as pd
import sumolib
class TrafficManager():
    """The traffic manager class. It manages both vehicle traffic and UAV traffic. It also manipulates the positions of the vehicles, UAVs, RSUs, and cloud servers.
    """

    def __init__(self, config_traffic, traci_connection:traci.connection, sumo_network_xml:str=None):
        """Initialize the traffic manager.

        Args:
            config_traffic (dict): The traffic configuration part of the environment configuration.
        """
        self._config_traffic = config_traffic
        self._net = sumolib.net.readNet(sumo_network_xml)
        self._start_traffic_time = config_traffic.get("start_traffic_time", 0)
        self._max_n_vehicles = config_traffic.get("max_n_vehicles", 100)
        self._x_range = config_traffic.get("x_range", [0, 1000]) # set in airfogsim_env.py according to used area map
        self._y_range = config_traffic.get("y_range", [0, 1000]) # set in airfogsim_env.py according to used area map
        self._nonfly_zone_coordinates = config_traffic.get("nonfly_zone_coordinates", [])
        self._vehicle_speed_range=config_traffic.get("vehicle_speed_range", [[5.55,9.72],[9.72,13.88],[13.88,18.05],[18.05,22.22]])
        self._vehicle_speed_probability=config_traffic.get("vehicle_speed_probability", [0.75,0.1,0.1,0.05])
        self._UAV_boundary_threshold=config_traffic.get("UAV_boundary_threshold", [1000,500,1000,1000])
        self._UAV_z_range = config_traffic.get("UAV_z_range", [100, 200])
        self._UAV_speed_range = config_traffic.get("UAV_speed_range", [10, 30])
        self._max_n_UAVs = config_traffic.get("max_n_UAVs", 10)
        self._max_n_RSUs = config_traffic.get("max_n_RSUs", 4)
        self._RSU_positions = config_traffic.get("RSU_positions", [[0, 0, 0]])
        self._max_n_cloudServers = config_traffic.get("max_n_cloudServers", 1)
        self._arrival_lambda = config_traffic.get("arrival_lambda", 1)
        self._distance_threshold = config_traffic.get("distance_threshold", 1)

        self._traci_connection = traci_connection
        self._current_time = self._start_traffic_time

        self._vehicle_infos = {} # vehicle_id -> {position, speed, routeId}
        self._UAV_infos = {} # uav_id -> {position, speed, acceleration, angle, phi}
        self._RSU_infos = {} # rsu_id -> {position, id}
        self._cloudServer_infos = {}
        self._new_added_vehicle_ids=[] # The latest(this time slot) added vehicle's id

        self._sumo_route_ids = [] # all route ids in SUMO, further information can be gained by traci_connection.route.getEdges(route_id)
        self._sumo_edges = {} # each edge is a series of lanes in SUMO, edgeId -> [laneId1, laneId2, ...]
        self._sumo_laneIds = [] # all lane ids in SUMO

        self._traffic_interval = config_traffic.get("traffic_interval", 1)
        self._tripinfo = None
        if traci_connection is not None:
            assert traci_connection.simulation.getDeltaT() == self._traffic_interval, "The traffic interval should be the same as the simulation interval."
        else:
            # 从config_traffic的tripinfo中获取tripinfo.csv的路径，读取作为pandas的DataFrame
            tripinfo_path = config_traffic.get("tripinfo", None)
            if tripinfo_path is not None:
                # 只读取columns=['vehicle_id', 'data_timestep', 'vehicle_x', 'vehicle_y', 'vehicle_speed', 'vehicle_angle', 'vehicle_route']
                self._tripinfo = pd.read_csv(tripinfo_path, sep=";", usecols=['vehicle_id', 'data_timestep', 'vehicle_x', 'vehicle_y', 'vehicle_speed', 'vehicle_angle', 'vehicle_route'])
                # dropnan
                self._tripinfo = self._tripinfo.dropna()
            else:
                raise ValueError("The tripinfo path is not set in the config_traffic.")
        
        self._vehicle_id_counter = 0
        self._UAV_id_counter = 0
        self._RSU_id_counter = 0
        self._cloudServer_id_counter = 0
        self._route_id_counter = 0

        self._grid_width = 50
        self._traffic_mode = config_traffic['traffic_mode']

        self._initialize_map_by_grid()
        self._initialize_edges_and_lanes()
        self._update_route_ids()
        self._initialize_RSUs()
        self._initialize_cloudServers()
        self._initialize_UAVs()

        self.veh_speed = {}

    def reset(self,new_traci_connection):
        """Reset the traffic manager.
        """
        self._traci_connection=new_traci_connection
        self._current_time = self._start_traffic_time
        self._vehicle_infos = {}
        self._UAV_infos = {}
        self._RSU_infos = {}
        self._cloudServer_infos = {}
        self._new_added_vehicle_ids = []
        self._vehicle_id_counter = 0
        self._UAV_id_counter = 0
        self._RSU_id_counter = 0
        self._cloudServer_id_counter = 0
        self._route_id_counter = 0
        self._initialize_map_by_grid()
        self._initialize_edges_and_lanes()
        self._update_route_ids()
        self._initialize_RSUs()
        self._initialize_cloudServers()
        self._initialize_UAVs()

    def _getSpeedTypeIdx(self,num):
        max_idx=len(self._vehicle_speed_probability)-1
        idx=0
        sum_p=0
        for p in self._vehicle_speed_probability:
            sum_p += p
            if num<=sum_p:
                return idx
            idx+=1
        return min(idx,max_idx)

    def getMapIndexByNodeId(self, node_id):
        # row_idx, col_idx = np.where(self._map_by_grid == node_id)
        for row in range(self._map_by_grid.shape[0]):
            for col in range(self._map_by_grid.shape[1]):
                if node_id in self._map_by_grid[row, col]:
                    return row, col
        return None, None
    
    def getVehicleTrafficInfosByMapIndex(self, row, col):
        row = min(max(0, row), self._map_by_grid.shape[0] - 1)
        col = min(max(0, col), self._map_by_grid.shape[1] - 1)
        vehicle_ids = self._map_by_grid[row, col]
        vehicle_infos = self.getVehicleInfoByIds(vehicle_ids)
        return vehicle_infos
    
    def getMapIndexesByTargetPositionAndRange(self, target_position, range):
        row = int((target_position[1] - self._y_range[0]) / self._grid_width)
        col = int((target_position[0] - self._x_range[0]) / self._grid_width)
        row_range = int(range / self._grid_width)
        col_range = int(range / self._grid_width)
        row_start = max(0, row - row_range)
        row_end = min(self._map_by_grid.shape[0], row + row_range + 1)
        col_start = max(0, col - col_range)
        col_end = min(self._map_by_grid.shape[1], col + col_range + 1)
        return row_start, row_end, col_start, col_end

    @property
    def map_by_grid(self):
        return self._map_by_grid.copy()
    
    @property
    def grid_width(self):
        return self._grid_width

    def _initialize_map_by_grid(self):
        """Initialize the map_by_grid matrix. The matrix is used to store the node ids (as list) in each grid. The grid is defined by the grid width. The matrix is by: row1, col1 = y1, x1; row2, col2 = y2, x2 of position (x, y). 
        """
        row_num = int((self._y_range[1] - self._y_range[0]) / self._grid_width)
        col_num = int((self._x_range[1] - self._x_range[0]) / self._grid_width)
        self._map_by_grid = np.empty((row_num, col_num), dtype=object)
        for i in range(row_num):
            for j in range(col_num):
                self._map_by_grid[i, j] = []

    def getRSUPositions(self):
        """Get the RSU positions.

        Returns:
            list: The RSU positions.
        """
        return self._RSU_positions

    def getRSUPosition(self, RSU_id):
        RSU_info = self._RSU_infos.get(RSU_id, None)
        assert RSU_info is not None
        return RSU_info['position']

    def getVehiclePosition(self, vehicle_id):
        vehicle_info = self._vehicle_infos.get(vehicle_id, None)
        assert vehicle_info is not None
        return vehicle_info['position']

    def getUAVPosition(self, UAV_id):
        UAV_info = self._UAV_infos.get(UAV_id, None)
        assert UAV_info is not None
        return UAV_info['position']

    def getNumberOfRSUs(self):
        """Get the number of RSUs.

        Returns:
            int: The number of RSUs.
        """
        return len(self._RSU_infos)
    
    def getNumberOfCloudServers(self):
        """Get the number of cloud servers.

        Returns:
            int: The number of cloud servers.
        """
        return len(self._cloudServer_infos)
    
    def getNumberOfUAVs(self):
        """Get the number of UAVs.

        Returns:
            int: The number of UAVs.
        """
        return len(self._UAV_infos)
    
    def getNumberOfVehicles(self):
        """Get the number of vehicles.

        Returns:
            int: The number of vehicles.
        """
        return len(self._vehicle_infos)

    def _initialize_RSUs(self):
        """Initialize the RSU information.
        """
        for RSU_position in self._RSU_positions:
            RSU_id = "RSU_" + str(self._RSU_id_counter)
            self._RSU_id_counter += 1
            self._RSU_infos[RSU_id] = {"position": RSU_position, "id": RSU_id}
            row = int((RSU_position[1] - self._y_range[0]) / self._grid_width)
            col = int((RSU_position[0] - self._x_range[0]) / self._grid_width)
            if row >= 0 and row < self._map_by_grid.shape[0] and col >= 0 and col < self._map_by_grid.shape[1]:
                self._map_by_grid[row, col].append(RSU_id)

    def _initialize_cloudServers(self):
        """Initialize the cloud server information.
        """
        for _ in range(self._max_n_cloudServers):
            cloudServer_id = "cloudServer_" + str(self._RSU_id_counter)
            self._cloudServer_id_counter += 1
            position = (0, 0, 0)
            self._cloudServer_infos[cloudServer_id] = {"position": position, "id": cloudServer_id}

    def _initialize_UAVs(self):
        """Initialize the UAV information with random positions in the given range.
        """
        for _ in range(self._max_n_UAVs):
            UAV_id = "UAV_" + str(self._UAV_id_counter)
            self._UAV_id_counter += 1
            left_boundary=self._x_range[0]+self._UAV_boundary_threshold[2]
            right_boundary=self._x_range[1]-self._UAV_boundary_threshold[3]
            up_boundary=self._y_range[0]+self._UAV_boundary_threshold[0]
            down_boundary=self._y_range[1]-self._UAV_boundary_threshold[1]
            position = (random.uniform(left_boundary, right_boundary), random.uniform(up_boundary, down_boundary), random.uniform(self._UAV_z_range[0], self._UAV_z_range[1]))
            self._UAV_infos[UAV_id] = {"position": position}
            row = int((position[1] - self._y_range[0]) / self._grid_width)
            col = int((position[0] - self._x_range[0]) / self._grid_width)
            if row >= 0 and row < self._map_by_grid.shape[0] and col >= 0 and col < self._map_by_grid.shape[1]:
                self._map_by_grid[row, col].append(UAV_id)

    def completeStrId(self, id_num, node_type):
        assert node_type in ['I', 'V', 'U','C']
        if node_type == 'I':
            str_id = "RSU_" + str(id_num)
        elif node_type == 'V':
            str_id = "vehicle_" + str(id_num)
        elif node_type == 'U':
            str_id = "UAV_" + str(id_num)
        elif node_type == 'C':
            str_id = "cloudServer_" + str(id_num)
        return str_id



    def _initialize_edges_and_lanes(self):
        """Initialize the edges and lanes information."""
        if self._net:
            self._sumo_edges = {}
            self._sumo_laneIds = []
            nodes = self._net.getNodes()
            self._sumo_junction_positions = {}
            for node in nodes:
                node_id = node.getID()
                position = (node.getCoord()[0], node.getCoord()[1], 0)
                self._sumo_junction_positions[node_id] = position
            
            # 获取所有的 lane IDs
            for edge in self._net.getEdges():
                for lane in edge.getLanes():
                    lane_id = lane.getID()
                    edge_id = edge.getID()
                    if edge_id not in self._sumo_edges:
                        self._sumo_edges[edge_id] = []
                    self._sumo_edges[edge_id].append(lane_id)
                    self._sumo_laneIds.append(lane_id)

            valid_edges = []
            edges = list(self._sumo_edges.keys())
            self.all_allowed_classes = set()

            for edge in edges:
                lanes = self._sumo_edges[edge]
                for lane_id in lanes:
                    lane = self._net.getLane(lane_id)
                    allowed_classes = lane.getPermissions()
                    self.all_allowed_classes.update(allowed_classes)
                    if len(allowed_classes) == 0 or 'passenger' in allowed_classes:
                        valid_edges.append(edge)
                        break

            self.valid_edges = valid_edges
        

    def _update_route_ids(self):
        """Update the route information generated by SUMO.
        """
        if self._traffic_mode == 'SUMO':
            route_ids = self._traci_connection.route.getIDList()
            self._sumo_route_ids = route_ids

    def _generateRandomRoute(self):
        """Generate a random route id.

        Returns:
            str: The route id.
        """
        route_id = "gen_veh_route_" + str(self._route_id_counter)
        valid_edges = self.valid_edges
        while True:
            try:
                from_edge, to_edge = random.sample(valid_edges, 2) 
                route = traci.simulation.findRoute(from_edge, to_edge)
                while len(route.edges) == 0:
                    from_edge, to_edge = random.sample(valid_edges, 2)
                    route = traci.simulation.findRoute(from_edge, to_edge)
                break
            except traci.exceptions.TraCIException as e:
                pass
            
        self._traci_connection.route.add(route_id, route.edges)
        self._route_id_counter += 1
        return route_id
    
    def updateVehicleMobilityPatterns(self, vehicle_mobility_patterns):
        """Update the vehicle mobility patterns.

        Args:
            vehicle_mobility_patterns (dict): The vehicle mobility patterns. The key is vehicle id, and the value is the mobility pattern={angle, speed}
        """
        for vehicle_id, mobility_pattern in vehicle_mobility_patterns.items():
            self._traci_connection.vehicle.setSpeed(vehicle_id, mobility_pattern["speed"])

    def _updateUAVMobilityPatternById(self, UAV_id, mobility_pattern):
        """Update the UAV mobility pattern by the UAV id.

        Args:
            UAV_id (str): The UAV id.
            mobility_pattern (dict): The mobility pattern={angle, phi, speed}
        """
        assert UAV_id in self._UAV_infos, "The UAV id should be in the UAV information."
        self._UAV_infos[UAV_id]["speed"] = mobility_pattern["speed"]
        self._UAV_infos[UAV_id]["angle"] = mobility_pattern["angle"]
        self._UAV_infos[UAV_id]["phi"] = mobility_pattern["phi"]

    def updateUAVMobilityPatterns(self, UAV_mobility_patterns):
        """Update the UAV mobility patterns.

        Args:
            UAV_mobility_patterns (dict): The UAV mobility patterns. The key is UAV id, and the value is the mobility pattern={angle, phi, speed}
        """
        for UAV_id, mobility_pattern in UAV_mobility_patterns.items():
            self._updateUAVMobilityPatternById(UAV_id, mobility_pattern)

    def updateCurrentTime(self):
        """Update the current time.

        Returns:
            float: The updated current time.
        """
        if self._traffic_mode == 'SUMO':
            return self._traci_connection.simulation.getTime()
        else:
            # 把self._tripinfo中current_time之前的数据删除
            # self._tripinfo = self._tripinfo[self._tripinfo['data_timestep']>=self._current_time]
            tmp_time = self._current_time + self._traffic_interval
            # 保证tmp_time mod self._traffic_interval == 0
            tmp_time = round(tmp_time / self._traffic_interval) 
            tmp_time = tmp_time * self._traffic_interval
            return tmp_time

    def getVehicleIDsList(self):
        """Get the vehicle ids list.

        Returns:
            list: The vehicle ids list.
        """
        if self._traffic_mode == 'SUMO':
            return self._traci_connection.vehicle.getIDList()
        else:
            # 根据当前的时隙，从tripinfo中获取当前时隙的车辆信息
            current_time = self._current_time
            # tripinfo是pd.DataFrame，可以直接使用pandas的查询功能,date_timestep在current_time-traffic_interval到current_time之间的车辆
            vehicle_ids = self._tripinfo[(self._tripinfo['data_timestep']>round(current_time-self._traffic_interval,3)) & (self._tripinfo['data_timestep']<=round(current_time,3))]['vehicle_id'].tolist()
            return vehicle_ids
        
    def getVehicleInfoByIds(self, vehicle_ids):
        # {"position": position3d, "speed": speed, "acceleration": acceleration, "angle": angle, "routeId": route_id, 'id': vehicle_id}
        if self._traffic_mode == 'SUMO':
            vehicle_infos = {}
            for vehicle_id in vehicle_ids:
                position = self._traci_connection.vehicle.getPosition(vehicle_id)
                speed = self._traci_connection.vehicle.getSpeed(vehicle_id)
                acceleration = self._traci_connection.vehicle.getAcceleration(vehicle_id)
                angle = self._traci_connection.vehicle.getAngle(vehicle_id)
                route_id = self._traci_connection.vehicle.getRouteID(vehicle_id)
                position3d = (position[0], position[1], 0)
                vehicle_infos[vehicle_id] = {"position": position3d, "speed": speed, "acceleration": acceleration, "angle": angle, "routeId": route_id, 'id': vehicle_id}
            return vehicle_infos
        else:
            # 从pd中批量获取车辆信息
            cur_time_trip_info = self._tripinfo[(self._tripinfo['data_timestep']>round(self._current_time-self._traffic_interval,3)) & (self._tripinfo['data_timestep']<=round(self._current_time,3))]
            pd_vehicle_infos = cur_time_trip_info[cur_time_trip_info['vehicle_id'].isin(vehicle_ids)]
            vehicle_infos = {}
            for idx, vehicle_info in pd_vehicle_infos.iterrows():
                position = (vehicle_info['vehicle_x'], vehicle_info['vehicle_y'], 0)
                speed = vehicle_info['vehicle_speed']
                acceleration = 0
                angle = vehicle_info['vehicle_angle']
                route_id = vehicle_info['vehicle_route']
                vehicle_id = vehicle_info['vehicle_id']
                vehicle_infos[vehicle_id] = {"position": position, "speed": speed, "acceleration": acceleration, "angle": angle, "routeId": route_id, 'id': vehicle_id}
            return vehicle_infos

    def stepSimulation(self):
        """Step the simulation for one step. Generate vehicles according to Poisson distribution, limit the number of vehicles, and update the route ids.
        """
        if self._traffic_mode == 'SUMO':
            to_generate_vehicles = int(np.random.poisson(self._arrival_lambda*self._traffic_interval))
            current_n_vehicles = self._traci_connection.vehicle.getIDCount()
            to_generate_vehicles = min(to_generate_vehicles, self._max_n_vehicles - current_n_vehicles)
            self._new_added_vehicle_ids = []  # Clear the list in each step.
            if to_generate_vehicles > 0 :
                for _ in range(to_generate_vehicles):
                    vehicle_id = "vehicle_" + str(self._vehicle_id_counter)
                    self._new_added_vehicle_ids.append(vehicle_id)
                    self._vehicle_id_counter += 1
                    route_id = self._generateRandomRoute()
                    self._traci_connection.vehicle.add(vehicle_id, route_id)
                    speed_range_type=self._getSpeedTypeIdx(random.random())
                    max_speed_min,max_speed_max=self._vehicle_speed_range[speed_range_type]
                    max_speed=random.uniform(max_speed_min,max_speed_max)
                    self._traci_connection.vehicle.setSpeed(vehicle_id, max_speed)
                    traci.vehicle.setSpeedMode(vehicle_id, 00000)

            self._traci_connection.simulationStep()
            # vehicles will be updated by sumo. (Vehicles which are out of map will be cleared automatically by sumo)
            vehicle_ids = self.getVehicleIDsList()
        else:
            old_vehicle_ids=self._vehicle_infos.keys() if len(self._vehicle_infos)>0 else []
            # 从tripinfo中获取当前时间的车辆信息
            vehicle_ids = self.getVehicleIDsList()
            self._new_added_vehicle_ids=set(vehicle_ids)-set(old_vehicle_ids)

        self._vehicle_infos = self.getVehicleInfoByIds(vehicle_ids)
        self._current_time = self.updateCurrentTime()

        for UAV_id in self._UAV_infos:
            org_position = self._UAV_infos[UAV_id]["position"]
            speed = self._UAV_infos[UAV_id].get("speed", 0)
            last_speed = self._UAV_infos[UAV_id].get("last_speed", 0)
            acceleration = (last_speed - speed) / self._traffic_interval
            self._UAV_infos[UAV_id]["acceleration"] = acceleration
            self._UAV_infos[UAV_id]["last_speed"] = self._UAV_infos[UAV_id].get("speed", 0)
            angle = self._UAV_infos[UAV_id].get("angle", 0)
            phi = self._UAV_infos[UAV_id].get("phi", 0)
            # new position of UAV need to be uodated by hand
            new_position = (org_position[0] + speed * np.cos(angle) * np.cos(phi) * self._traffic_interval, org_position[1] + speed * np.sin(angle) * np.cos(phi) * self._traffic_interval, org_position[2] + speed * np.sin(phi) * self._traffic_interval)
            new_position = [float(i) for i in new_position]
            self._UAV_infos[UAV_id] = {"position": new_position, "speed": speed, "last_speed": speed, "angle": angle, "phi": phi, "acceleration": acceleration}
        self._update_route_ids()
        self._update_map_by_grid()

    def _update_map_by_grid(self):
        self._map_by_grid = np.empty((self._map_by_grid.shape[0], self._map_by_grid.shape[1]), dtype=object)
        for i in range(self._map_by_grid.shape[0]):
            for j in range(self._map_by_grid.shape[1]):
                self._map_by_grid[i, j] = []
        for vehicle_id, vehicle_info in self._vehicle_infos.items():
            position = vehicle_info["position"]
            row = int((position[1] - self._y_range[0]) / self._grid_width)
            col = int((position[0] - self._x_range[0]) / self._grid_width)
            if row >= 0 and row < self._map_by_grid.shape[0] and col >= 0 and col < self._map_by_grid.shape[1]:
                self._map_by_grid[row, col].append(vehicle_id)
        for UAV_id, UAV_info in self._UAV_infos.items():
            position = UAV_info["position"]
            row = int((position[1] - self._y_range[0]) / self._grid_width)
            col = int((position[0] - self._x_range[0]) / self._grid_width)
            if row >= 0 and row < self._map_by_grid.shape[0] and col >= 0 and col < self._map_by_grid.shape[1]:
                self._map_by_grid[row, col].append(UAV_id)
        for RSU_id, RSU_info in self._RSU_infos.items():
            position = RSU_info["position"]
            row = int((position[1] - self._y_range[0]) / self._grid_width)
            col = int((position[0] - self._x_range[0]) / self._grid_width)
            if row >= 0 and row < self._map_by_grid.shape[0] and col >= 0 and col < self._map_by_grid.shape[1]:
                self._map_by_grid[row, col].append(RSU_id)

    def getMapRange(self,axis):
        assert axis in ['X', 'Y', 'Z']
        if axis=='X':
            return self._x_range
        if axis=='Y':
            return self._y_range
        if axis=='Z':
            return self._UAV_z_range

    def getVehicleTrafficInfos(self):
        """Get the vehicle traffics at the given simulation time.

        Returns:
            dict: The vehicle traffics, including the vehicle id, position, speed, angle, acceleration, and current routeId.
        """
        return self._vehicle_infos
    
    def getUAVTrafficInfos(self):
        """Get the UAV traffics at the given simulation time. The trajectory of the UAVs is controlled by their missions

        Returns:
            dict: The UAV traffics, including the UAV id, position, acceleration, speed, angle, and phi.
        """
        return self._UAV_infos
    
    def getRSUInfos(self):
        """Get the RSU information.

        Returns:
            dict: The RSU information, including the RSU id and position
        """
        return self._RSU_infos
    
    def getCloudServerInfos(self):
        """Get the cloud server information.

        Returns:
            dict: The cloud server information, including the cloud server id and position.
        """
        return self._cloudServer_infos

    def getNewVehicleIds(self):
        """Get vehicle ids which is added in latest timeslot.

        Returns:
            list: The Id list of vehicles.
        """
        return self._new_added_vehicle_ids
    
    def getCurrentTime(self):
        """Get the current simulation time.

        Returns:
            float: The current simulation time (in seconds).
        """
        # return self._traci_connection.simulation.getTime()
        return self._current_time

    def removeUAV(self, UAV_id):
        assert UAV_id in self._UAV_infos.keys(), 'UAV_id not in _UAV_infos'
        del self._UAV_infos[UAV_id]

    def checkIsMovingByUAVId(self, UAV_id):
        UAV_info = self._UAV_infos[UAV_id]
        assert UAV_id in self._UAV_infos.keys(), 'UAV_id not in _UAV_infos'
        return UAV_info['speed'] > 0

    def getConfig(self,name):
        return self._config_traffic.get(name,None)
    
    def getNodePositionById(self, id):
        if id in self._vehicle_infos:
            return self._vehicle_infos[id]["position"]
        elif id in self._UAV_infos:
            return self._UAV_infos[id]["position"]
        elif id in self._RSU_infos:
            return self._RSU_infos[id]["position"]
        elif id in self._cloudServer_infos:
            return self._cloudServer_infos[id]["position"]
        return None
    
    def getAllJunctionPositions(self):
        return list(self._sumo_junction_positions.values())
    
    def getNonFlyZones(self):
        return self._nonfly_zone_coordinates.copy()
