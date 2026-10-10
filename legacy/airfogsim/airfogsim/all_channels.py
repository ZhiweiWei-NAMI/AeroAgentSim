import os
if os.environ.get('useCUPY') == 'True':
    try:
        # 尝试导入 cupy
        import cupy as cp
    except ImportError:
        # 如果导入失败，回退到 numpy
        import numpy as np
        cp = np  # 使用 numpy 作为替代
        print("CuPy not available. Using NumPy instead.")
else:
    import numpy as cp

cp.seterr(all='ignore')
from .channel_callback import PathLossCallback, ShadowingCallback, FastFadingCallback

class V2IChannel: 
    # Simulator of the V2I channels
    def __init__(self, n_Veh, n_BS, frequency_ranges, BS_positions, **kwargs):
        '''V2I只存在于一个BS范围内的V2I channel'''
        self.h_bs = 25 # 基站高度25m
        self.h_ut = 1.5        
        self.BS_positions = cp.asarray(BS_positions)
        self.n_Veh = n_Veh
        self.n_BS = n_BS
        self.decorrelation_distance = 50
        self.n_RB = len(frequency_ranges)
        self.frequency_ranges = cp.asarray(frequency_ranges)
        self.shadow_std = 4
        self.fastfading_std = 1
        self.pathloss_callback = PathLossCallback(kwargs.get('pathloss_type', 'UMa_LOS_tr38901'))
        self.shadow_callback = ShadowingCallback(kwargs.get('shadowing_type', '3GPP_LogNormal'))
        self.fast_fading_callback = FastFadingCallback(kwargs.get('fastfading_type', 'Rayleigh'))
        self.update_shadow()

    
    def remove_vehicle_shadow(self, v_index):
        '''删除车辆，删除车辆的阴影'''
        self.Shadow = cp.delete(self.Shadow, v_index, axis=0)
        self.n_Veh -= 1

    def add_vehicle_shadow(self):
        '''增加车辆，增加车辆的阴影'''
        new_shadow = cp.random.normal(0, self.shadow_std, size=(1, self.n_BS))
        self.Shadow = cp.concatenate((self.Shadow, new_shadow), axis=0)
        # 更新n_Veh
        self.n_Veh += 1

    def update_positions(self, veh_positions):
        # 把字典转换成列表，通过vid_index来根据index排序
        self.positions = cp.asarray(veh_positions)
        self.n_Veh = len(veh_positions)
        
    def update_pathloss(self):
        if self.n_Veh == 0:
            self.PathLoss = cp.zeros((self.n_Veh, self.n_BS, self.n_RB))
            return
        veh_positions = self.positions
        BS_positions = self.BS_positions
        self.PathLoss = self.pathloss_callback(veh_positions, BS_positions, self.frequency_ranges)

    def update_shadow(self, delta_distance_list=None):
        # 如果shadow的任意一个shape为0，那么就重新生成shadow
        if delta_distance_list is None:
            # 1 如果过去一个时间片的车辆数量发生了变化
            self.Shadow = self.shadow_callback(cp.zeros((self.n_Veh, self.n_BS)), std=self.shadow_std)
        else:
            delta_distance_list = cp.asarray(delta_distance_list)
            delta_distance = cp.repeat(delta_distance_list[:,cp.newaxis], self.n_BS, axis=1)
            self.Shadow = self.shadow_callback(self.Shadow, delta_distance=delta_distance, std=self.shadow_std, d_correlation=self.decorrelation_distance)

    def update_fast_fading(self):
        self.FastFading = self.fast_fading_callback(self.n_Veh, self.n_BS, self.n_RB, std=self.fastfading_std)

class V2VChannel:
    def __init__(self, n_Veh, frequency_ranges, **kwargs):
        self.t = 0
        self.h_tx = 1.5
        self.h_rx = 1.5
        self.decorrelation_distance = 10
        self.n_Veh = n_Veh # 车辆数量
        self.n_RB = len(frequency_ranges) # RB数量
        self.frequency_ranges = cp.asarray(frequency_ranges)
        self.shadow_std = 3
        self.fastfading_std = 1
        self.pathloss_callback = PathLossCallback(kwargs.get('pathloss_type', 'V2V_urban_tr37885'))
        self.shadow_callback = ShadowingCallback(kwargs.get('shadowing_type', '3GPP_LogNormal'))
        self.fast_fading_callback = FastFadingCallback(kwargs.get('fastfading_type', 'Rayleigh'))
        self.update_shadow()

    def update_positions(self, positions):
        # 把字典转换成列表，通过vid_index来根据index排序
        self.positions = cp.asarray(positions)
        self.n_Veh = len(positions)

    def update_pathloss(self):
        if self.n_Veh == 0:
            self.PathLoss = cp.zeros((self.n_Veh, self.n_Veh, self.n_RB))
            return
        veh_positions = self.positions
        self.PathLoss = self.pathloss_callback(veh_positions, veh_positions, self.frequency_ranges)
        for i in range(self.PathLoss.shape[0]):
            self.PathLoss[i, i, :] = 0

    # 考虑到车辆数量变动，需要更新上一时刻的阴影，删除的车辆阴影删除，新增的车辆阴影增加
    def remove_vehicle_shadow(self, v_index):
        '''删除车辆，删除车辆的阴影'''
        self.Shadow = cp.delete(self.Shadow, v_index, axis=0)
        self.Shadow = cp.delete(self.Shadow, v_index, axis=1)
        self.n_Veh -= 1

    def add_vehicle_shadow(self):
        '''增加车辆，增加车辆的阴影'''
        new_shadow = cp.random.normal(0, self.shadow_std, size=(1, self.n_Veh))
        self.Shadow = cp.concatenate((self.Shadow, new_shadow), axis=0)
        new_shadow = cp.random.normal(0, self.shadow_std, size=(self.n_Veh + 1, 1))
        self.Shadow = cp.concatenate((self.Shadow, new_shadow), axis=1)
        self.n_Veh += 1

    def update_shadow(self, delta_distance_list=None):
        if delta_distance_list is None:
            self.Shadow = self.shadow_callback(cp.zeros((self.n_Veh, self.n_Veh)), std=self.shadow_std)
        else:
            delta_distance_list = cp.asarray(delta_distance_list)
            delta_distance = cp.add.outer(delta_distance_list, delta_distance_list)
            self.Shadow = self.shadow_callback(self.Shadow, delta_distance=delta_distance, std=self.shadow_std, d_correlation=self.decorrelation_distance)

    def update_fast_fading(self):
        self.FastFading = self.fast_fading_callback(self.n_Veh, self.n_Veh, self.n_RB, std=self.fastfading_std)

class V2UChannel:
    def __init__(self, n_Veh, n_UAV, frequency_ranges, hei_UAV, **kwargs):
        '''多个vehicle和多个UAV之间的通信信道'''
        self.t = 0
        self.h_uav = hei_UAV
        self.h_veh = 1.5 # 车作为MS的高度
        self.frequency_ranges = cp.asarray(frequency_ranges)
        self.decorrelation_distance = 50
        self.shadow_std = 4 # shadow的标准值
        self.fastfading_std = 1
        self.n_Veh = n_Veh # 车辆数量
        self.n_UAV = n_UAV # 无人机数量
        self.n_RB = len(frequency_ranges) # RB数量
        self.pathloss_callback = PathLossCallback(kwargs.get('pathloss_type', 'free_space'))
        self.shadow_callback = ShadowingCallback(kwargs.get('shadowing_type', '3GPP_LogNormal'))
        self.fast_fading_callback = FastFadingCallback(kwargs.get('fastfading_type', 'Rayleigh'))
        self.update_shadow()
    
    def remove_vehicle_shadow(self, v_index):
        '''删除车辆，删除车辆的阴影'''
        self.Shadow = cp.delete(self.Shadow, v_index, axis=0)
        self.n_Veh -= 1
        
    def add_vehicle_shadow(self):
        '''增加车辆，增加车辆的阴影'''
        new_shadow = cp.random.normal(0, self.shadow_std, size=(1, self.n_UAV))
        self.Shadow = cp.concatenate((self.Shadow, new_shadow), axis=0)
        # 更新n_Veh
        self.n_Veh += 1

    def remove_UAV_shadow(self, vid, vid_index):
        '''删除无人机，删除无人机的阴影'''
        index = vid_index[vid]
        self.Shadow = cp.delete(self.Shadow, index, axis=1)
        self.n_UAV -= 1

    def add_UAV_shadow(self):
        '''增加无人机，增加无人机的阴影'''
        new_shadow = cp.random.normal(0, self.shadow_std, size=(self.n_Veh, 1))
        self.Shadow = cp.concatenate((self.Shadow, new_shadow), axis=1)
        # 更新n_Veh
        self.n_UAV += 1

    def update_positions(self, veh_positions, uav_positions):
        '''更新车辆和无人机的位置'''
        self.veh_positions = cp.asarray(veh_positions)
        self.uav_positions = cp.asarray(uav_positions)
        self.n_Veh = len(veh_positions)
        self.n_UAV = len(uav_positions)
        
    def update_pathloss(self):
        if self.n_Veh == 0 or self.n_UAV==0:
            self.PathLoss = cp.zeros((self.n_Veh, self.n_UAV, self.n_RB))
            return
        self.PathLoss = self.pathloss_callback(self.veh_positions, self.uav_positions, self.frequency_ranges)

    def update_shadow(self, veh_delta_distance_list=None, uav_delta_distance_list=None):
        if veh_delta_distance_list is None and uav_delta_distance_list is None:
            # 1 如果过去一个时间片的车辆数量发生了变化（只会增加）或无人机数量发生了变化（只会减少）
            self.Shadow = cp.random.normal(0, self.shadow_std, size=(self.n_Veh, self.n_UAV))
        else:
            veh_delta_distance_list = cp.asarray(veh_delta_distance_list)
            uav_delta_distance_list = cp.asarray(uav_delta_distance_list)
            delta_distance = cp.add.outer(veh_delta_distance_list, uav_delta_distance_list)
            self.Shadow = self.shadow_callback(self.Shadow, delta_distance=delta_distance, std=self.shadow_std, d_correlation=self.decorrelation_distance)

    def update_fast_fading(self):
        self.FastFading = self.fast_fading_callback(self.n_Veh, self.n_UAV, self.n_RB, std=self.fastfading_std)

class U2IChannel: 
    # U2I仿真信道
    def __init__(self, n_BS, n_UAV, frequency_ranges, hei_UAV, BS_positions, **kwargs):
        '''V2I只存在于一个BS范围内的V2I channel'''
        self.h_bs = 25 # 基站高度25m
        self.hei_UAV = hei_UAV
        self.Decorrelation_distance = 50 
        self.shadow_std = 3
        self.n_BS = n_BS
        self.n_UAV = n_UAV
        self.n_RB = len(frequency_ranges)
        self.frequency_ranges = cp.asarray(frequency_ranges)
        self.BS_positions = cp.asarray(BS_positions)
        self.pathloss_callback = PathLossCallback(kwargs.get('pathloss_type', 'free_space'))
        self.shadow_callback = ShadowingCallback(kwargs.get('shadowing_type', '3GPP_LogNormal'))
        self.fast_fading_callback = FastFadingCallback(kwargs.get('fastfading_type', 'Rayleigh'))
        self.update_shadow()

    def remove_UAV_shadow(self, vid, vid_index):
        '''删除无人机，删除无人机的阴影'''
        index = vid_index[vid]
        self.Shadow = cp.delete(self.Shadow, index, axis=0)
        self.n_UAV -= 1

    def add_UAV_shadow(self):
        '''增加无人机，增加无人机的阴影'''
        new_shadow = cp.random.normal(0, self.shadow_std, size=(1,self.n_BS))
        self.Shadow = cp.concatenate((self.Shadow, new_shadow), axis=0)
        # 更新n_Veh
        self.n_UAV += 1

    def update_positions(self, UAV_positions):
        self.UAV_positions = cp.asarray(UAV_positions)
        self.n_UAV = len(UAV_positions)
        
    def update_pathloss(self):
        if self.n_UAV==0:
            self.PathLoss = cp.zeros((self.n_UAV, self.n_BS, self.n_RB))
            return
        self.PathLoss = self.pathloss_callback(self.UAV_positions, self.BS_positions, self.frequency_ranges)

    def update_shadow(self, delta_distance_list=None):
        if delta_distance_list is None:
            # 1 如果过去一个时间片的车辆数量发生了变化
            self.Shadow = cp.random.normal(0, self.shadow_std, size=(self.n_UAV, self.n_BS))
        else: 
            delta_distance_list = cp.asarray(delta_distance_list)
            delta_distance = cp.repeat(delta_distance_list[:,cp.newaxis], self.n_BS, axis=1)
            self.Shadow = self.shadow_callback(self.Shadow, delta_distance=delta_distance, std=self.shadow_std, d_correlation=self.Decorrelation_distance)

    def update_fast_fading(self):
        self.FastFading = self.fast_fading_callback(self.n_UAV, self.n_BS, self.n_RB)

class U2UChannel:
    def __init__(self, n_UAV, frequency_ranges, hei_UAV, **kwargs):
        # M. M. Azari, G. Geraci, A. Garcia-Rodriguez and S. Pollin, "UAV-to-UAV Communications in Cellular Networks," in IEEE Transactions on Wireless Communications, 2020, doi: 10.1109/TWC.2020.3000303.
        # A Survey of Channel Modeling for UAV Communications
        self.t = 0
        self.h_tx = hei_UAV 
        self.h_rx = hei_UAV 
        self.n_RB = len(frequency_ranges)
        self.frequency_ranges = cp.asarray(frequency_ranges)
        self.decorrelation_distance = 50
        self.shadow_std = 3 # shadow的标准值
        self.n_UAV = n_UAV
        self.pathloss_callback = PathLossCallback(kwargs.get('pathloss_type', 'free_space'))
        self.shadow_callback = ShadowingCallback(kwargs.get('shadowing_type', '3GPP_LogNormal'))
        self.fast_fading_callback = FastFadingCallback(kwargs.get('fastfading_type', 'Rayleigh'))
        self.update_shadow()

    def remove_UAV_shadow(self, vid, vid_index):
        '''删除车辆，删除车辆的阴影'''
        index = vid_index[vid]
        self.Shadow = cp.delete(self.Shadow, index, axis=0)
        self.Shadow = cp.delete(self.Shadow, index, axis=1)
        self.n_UAV -= 1

    def add_UAV_shadow(self):
        '''增加车辆，增加车辆的阴影'''
        new_shadow = cp.random.normal(0, self.shadow_std, size=(1, self.n_UAV))
        self.Shadow = cp.concatenate((self.Shadow, new_shadow), axis=0)
        new_shadow = cp.random.normal(0, self.shadow_std, size=(self.n_UAV + 1, 1))
        self.Shadow = cp.concatenate((self.Shadow, new_shadow), axis=1)
        self.n_UAV += 1

    def update_positions(self, uav_positions):
        '''更新无人机的位置'''
        self.positions = cp.asarray(uav_positions)
        self.n_UAV = len(uav_positions)

    def update_pathloss(self):
        if self.n_UAV==0:
            self.PathLoss = cp.zeros((self.n_UAV, self.n_UAV, self.n_RB))
            return
        self.PathLoss = self.pathloss_callback(self.positions, self.positions, self.frequency_ranges)
        for i in range(self.PathLoss.shape[0]):
            self.PathLoss[i, i, :] = 0
                
    def update_shadow(self, delta_distance_list = None):
        '''输入距离变化，计算阴影变化，基于3GPP的规范'''
        if delta_distance_list is None: 
            self.Shadow = cp.random.normal(0,self.shadow_std, size=(self.n_UAV, self.n_UAV))
        else:
            delta_distance = cp.add.outer(delta_distance_list, delta_distance_list)
            delta_distance_list = cp.asarray(delta_distance_list)
            self.Shadow = self.shadow_callback(self.Shadow, delta_distance=delta_distance, std=self.shadow_std, d_correlation=self.decorrelation_distance)

    def update_fast_fading(self):
        '''更新快速衰落'''
        self.FastFading = self.fast_fading_callback(self.n_UAV, self.n_UAV, self.n_RB)

class I2IChannel:
    def __init__(self, n_BS, frequency_ranges, BS_positions, **kwargs):
        self.t = 0
        self.h_rx = 25 
        self.h_tx = 25 
        self.fc = 2 # carrier frequency
        self.decorrelation_distance = 50
        self.shadow_std = 3 # shadow的标准值
        self.n_BS = n_BS # 车辆数量
        self.n_RB = len(frequency_ranges)
        self.positions = cp.asarray(BS_positions)
        self.frequency_ranges = cp.asarray(frequency_ranges)
        self.pathloss_callback = PathLossCallback(kwargs.get('pathloss_type', 'free_space'))
        self.shadow_callback = ShadowingCallback(kwargs.get('shadowing_type', '3GPP_LogNormal'))
        self.fast_fading_callback = FastFadingCallback(kwargs.get('fastfading_type', 'Rayleigh'))
        self.update_shadow()

    def update_pathloss(self):
        if self.n_BS == 0:
            self.PathLoss = cp.zeros((self.n_BS, self.n_BS, self.n_RB))
            return
        self.PathLoss = self.pathloss_callback(self.positions, self.positions, self.frequency_ranges, h_BS=25, h_UT=25)
        # 对角线置为0
        for i in range(self.PathLoss.shape[0]):
            self.PathLoss[i, i, :] = 0
                
    def update_shadow(self):
        '''更新阴影'''
        self.Shadow = self.shadow_callback(cp.zeros((self.n_BS, self.n_BS)), std=self.shadow_std)
