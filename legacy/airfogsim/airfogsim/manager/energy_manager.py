import random
from collections import deque

import numpy as np


class EnergyManager:
    """EnergyManager class is responsible for managing the battery energy for UAV.
    """

    def __init__(self,config_energy,UAVs_keys,simulation_interval):
        self._config_energy=config_energy
        self.simulation_interval=simulation_interval
        self._fly_unit_cost=self._config_energy['fly_unit_cost'] # energy unit / timeslot
        self._hover_unit_cost=self._config_energy['hover_unit_cost'] # energy unit / timeslot
        self._sensing_unit_cost=self._config_energy['sensing_unit_cost'] # energy unit / timeslot
        self._receive_unit_cost=self._config_energy['receive_unit_cost'] # energy unit / data unit
        self._send_unit_cost = self._config_energy['send_unit_cost']  # energy unit / data unit
        self._initial_energy_range=self._config_energy['initial_energy_range']
        self._UAVs_energy_info={} # node_id -> { energy,is_flying,is_hovering,is_sensing,is_receiving,is_sending}
        self._removed_UAVs_energy_info={} # node_id -> { energy,is_flying,is_hovering,is_sensing,is_receiving,is_sending}
        self._initUAVsEnergy(UAVs_keys)

    def reset(self, UAVs_keys):
        self._UAVs_energy_info={} # node_id -> { energy,is_flying,is_hovering,is_sensing,is_receiving,is_sending}
        self._removed_UAVs_energy_info={} # node_id -> { energy,is_flying,is_hovering,is_sensing,is_receiving,is_sending}
        self._initUAVsEnergy(UAVs_keys)
        
    def _initUAVsEnergy(self,UAVs_keys):
        for UAV_id in UAVs_keys :
            self._UAVs_energy_info[UAV_id]={}
            self._UAVs_energy_info[UAV_id]['energy']=random.randint(self._initial_energy_range[0],self._initial_energy_range[1])

    def updateEnergyPattern(self,node_id,is_flying,using_sensor_num,sending_data_size,receiving_data_size):
        UAV_energy_info=self._UAVs_energy_info[node_id]
        assert UAV_energy_info!=None
        UAV_energy_info['is_flying']=is_flying
        UAV_energy_info['is_hovering'] = not is_flying
        UAV_energy_info['using_sensor_num'] = using_sensor_num
        UAV_energy_info['receiving_data_size'] = receiving_data_size
        UAV_energy_info['sending_data_size'] = sending_data_size
        self._UAVs_energy_info[node_id]=UAV_energy_info

    def updateEnergy(self):
        to_remove_UAVs_info={} # UAV_id -> { energy,is_flying,is_hovering,is_sensing,is_receiving,is_sending}
        energy_consumption={}
        for UAV_id,UAV_energy_info in self._UAVs_energy_info.items():
            to_consume_energy=0
            to_consume_energy+=UAV_energy_info['is_flying']*self._fly_unit_cost*self.simulation_interval
            to_consume_energy+=UAV_energy_info['is_hovering']*self._hover_unit_cost*self.simulation_interval
            to_consume_energy+=UAV_energy_info['using_sensor_num']*self._sensing_unit_cost*self.simulation_interval
            to_consume_energy+=UAV_energy_info['receiving_data_size']*self._receive_unit_cost*self.simulation_interval
            to_consume_energy+=UAV_energy_info['sending_data_size']*self._send_unit_cost*self.simulation_interval
            UAV_energy_info['energy']-=to_consume_energy
            energy_consumption[UAV_id]=to_consume_energy
            if UAV_energy_info['energy']<=0:
                to_remove_UAVs_info[UAV_id]=UAV_energy_info
        for UAV_id,UAV_energy_info in to_remove_UAVs_info.items():
            self._removed_UAVs_energy_info[UAV_id] = UAV_energy_info
            del self._UAVs_energy_info[UAV_id]

        return energy_consumption

    def getAvailableUAVsId(self):
        return self._UAVs_energy_info.keys()

    def getEnergyById(self,id):
        return self._UAVs_energy_info[id]['energy']

    def getConfig(self,name):
        return self._config_energy.get(name,None)


