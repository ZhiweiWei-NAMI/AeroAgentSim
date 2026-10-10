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

MAX_PL = 500 # The maximum path loss in dB, does not have a physical meaning


def PathLossCallback(pathloss_type):
    """The callback function to get the path loss.

    Args:
        pathloss_type (str): The path loss type.

    Returns:
        function: The callback function to get the path loss.
    """
    if pathloss_type == 'UMa_LOS_tr38901':
        return UMa_LOS_pathloss
    elif pathloss_type == 'V2V_highway_tr37885':
        return V2V_highway_pathloss
    elif pathloss_type == 'V2V_urban_tr37885':
        return V2V_urban_pathloss
    elif pathloss_type == 'free_space':
        return FreeSpacePathLoss
    else:
        raise ValueError(f"Invalid path loss type: {pathloss_type}")
    
def FreeSpacePathLoss(tx_positions, rx_positions, frequency_ranges, **kwargs):
    """The free space path loss model.

    Args:
        tx_positions (cp.ndarray): The transmitter positions.
        rx_positions (cp.ndarray): The receiver positions.
        frequency_ranges (cp.ndarray): The frequency range in GHz, e.g., [3.5, 3.6].

    Returns:
        cp.ndarray: The path loss in dB. The shape is (num_tx, num_rx, num_freq).
    """
    frequency_ranges = frequency_ranges[None, None, :] * cp.ones((tx_positions.shape[0], rx_positions.shape[0], len(frequency_ranges)))
    d_2d = cp.sqrt(cp.sum((tx_positions[:, None, :] - rx_positions[None, :, :])**2, axis=-1)) # The 2D distance in meters, shape: (num_tx, num_rx)
    d_2d = d_2d[:, :, None] * cp.ones((1, 1, frequency_ranges.shape[-1]))
    path_loss = 20 * cp.log10(1e-9+d_2d) + 20 * cp.log10(1e-9+frequency_ranges) + 32.44 # The path loss in dB, shape: (num_tx, num_rx, num_freq)
    return path_loss

def V2V_highway_pathloss(tx_positions, rx_positions, frequency_ranges, **kwargs):
    h_tx = kwargs.get('h_tx', 1.5) # The height of the transmitter in meters
    h_rx = kwargs.get('h_rx', 1.5) # The height of the receiver in meters
    frequency_ranges = frequency_ranges[None, None, :] * cp.ones((tx_positions.shape[0], rx_positions.shape[0], len(frequency_ranges)))
    d_2d = cp.sqrt(cp.sum((tx_positions[:, None, :] - rx_positions[None, :, :])**2, axis=-1)) # The 2D distance in meters, shape: (num_tx, num_rx)
    d_2d = d_2d[:, :, None] * cp.ones((1, 1, frequency_ranges.shape[-1]))
    d_3d = cp.sqrt(d_2d**2 + (h_tx-h_rx)**2) # The 3D distance in meters, shape: (num_tx, num_rx, num_freq)
    path_loss = 32.4 + 20 * cp.log10(1e-9+d_3d) + 20 * cp.log10(1e-9+frequency_ranges) # The path loss in dB, shape: (num_tx, num_rx, num_freq)
    return path_loss

def V2V_urban_pathloss(tx_positions, rx_positions, frequency_ranges, **kwargs):
    h_tx = kwargs.get('h_tx', 1.5) # The height of the transmitter in meters
    h_rx = kwargs.get('h_rx', 1.5) # The height of the receiver in meters
    frequency_ranges = frequency_ranges[None, None, :] * cp.ones((tx_positions.shape[0], rx_positions.shape[0], len(frequency_ranges)))
    d_2d = cp.sqrt(cp.sum((tx_positions[:, None, :] - rx_positions[None, :, :])**2, axis=-1)) # The 2D distance in meters, shape: (num_tx, num_rx)
    d_2d = d_2d[:, :, None] * cp.ones((1, 1, frequency_ranges.shape[-1]))
    d_3d = cp.sqrt(d_2d**2 + (h_tx-h_rx)**2) # The 3D distance in meters, shape: (num_tx, num_rx, num_freq)
    path_loss = 38.77 + 16.7 * cp.log10(1e-9+d_3d) + 18.2 * cp.log10(1e-9+frequency_ranges) # The path loss in dB, shape: (num_tx, num_rx, num_freq)
    return path_loss

def UMa_LOS_pathloss(tx_positions_cp, rx_positions_cp, frequency_ranges, **kwargs):
    """The path loss model of UMa. 3GPP TR38.901 Table 7.4.1-1. Mostly used for V2I.

    Args:
        tx_positions_cp (cp.ndarray): The transmitter positions.
        rx_positions_cp (cp.ndarray): The receiver positions.
        frequency_range (cp.ndarray): The frequency range in GHz, e.g., [3.5, 3.6].
        h_BS (float): The height of the base station in meters.
        h_UT (float): The height of the user terminal in meters.
        h_E (float): The effective environment height. The default value is 1 meter for UMa.

    Returns:
        cp.ndarray: The path loss in dB. The shape is (num_tx, num_rx, num_freq).
    """
    h_BS = kwargs.get('h_BS', 25) # The height of the base station in meters
    h_UT = kwargs.get('h_UT', 1.5) # The height of the user terminal in meters
    h_E = kwargs.get('h_E', 1) # The effective environment height in meters
    # 1. Calculate the distance between the transmitter and the receiver
    # frequency_ranges repeated num_tx*num_rx times, shape: (num_tx, num_rx, num_freq)
    frequency_ranges = frequency_ranges[None, None, :] * cp.ones((tx_positions_cp.shape[0], rx_positions_cp.shape[0], len(frequency_ranges)))
    d_2d = cp.sqrt(cp.sum((tx_positions_cp[:, None, :] - rx_positions_cp[None, :, :])**2, axis=-1)) # The 2D distance in meters, shape: (num_tx, num_rx)
    # d_2d repeated len(frequency_ranges) times, shape: (num_tx, num_rx, num_freq)
    d_2d = d_2d[:, :, None] * cp.ones((1, 1, frequency_ranges.shape[-1]))
    d_3d = cp.sqrt(d_2d**2 + (h_BS-h_UT)**2) # The 3D distance in meters, shape: (num_tx, num_rx)
    # d_3d repeated len(frequency_ranges) times, shape: (num_tx, num_rx, num_freq)
    d_bp = 4 * (h_BS-h_E) * (h_UT-h_E) * frequency_ranges * 1e9 / 3e8 # Breakpoint distance in meters, shape: (num_freq,)
    # if 10m <= d_2d <= d_bp, then pathloss = PL1; if d_bp < d_2d <= 5000m, then pathloss = PL2; if d_2d < 10m, then pathloss = 0; if d_2d > 5000m, then pathloss = MAX_PL
    PL1 = 28 + 22 * cp.log10(1e-9+d_3d) + 20 * cp.log10(1e-9+frequency_ranges) # The path loss in dB, shape: (num_tx, num_rx, num_freq)
    PL2 = PL1 + 18 * cp.log10(1e-9+d_3d) - 9 * cp.log10(1e-9+d_bp**2 + (h_BS-h_UT)**2) # The path loss in dB, shape: (num_tx, num_rx, num_freq)
    path_loss = cp.where(d_2d < 10, 0, cp.where(d_2d <= d_bp, PL1, cp.where(d_2d <= 5000, PL2, MAX_PL))) # The path loss in dB, shape: (num_tx, num_rx, num_freq)
    return path_loss


if __name__ == '__main__':
    # 1. Test the UMa_LOS_pathloss
    # Random The transmitter positions, shape: (num_tx, 200)
    tx_positions_cp = cp.random.rand(200, 2) * 100 # The transmitter positions, shape: (num_tx, 2)
    rx_positions_cp = cp.random.rand(200, 2) * 100 # The receiver positions, shape: (num_rx, 2)
    frequency_ranges_cp = cp.linspace(2.4, 2.5, 100) # The frequency range in GHz
    import time
    start_time = time.time()
    path_loss = UMa_LOS_pathloss(tx_positions_cp, rx_positions_cp, frequency_ranges_cp)
    print(f"UMa_LOS_cp_pathloss time: {time.time()-start_time}")
    # print(f"UMa_LOS_cp_pathloss: {path_loss}")
    