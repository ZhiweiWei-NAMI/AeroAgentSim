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

def ShadowingCallback(shadowing_type):
    """The callback function to get the shadowing.

    Args:
        shadowing_type (str): The shadowing type.

    Returns:
        function: The callback function to get the shadowing.
    """
    if shadowing_type == '3GPP_LogNormal':
        return LogNormal_Shadowing
    else:
        raise ValueError(f"Invalid shadowing type: {shadowing_type}")

def LogNormal_Shadowing(preShadowing, **kwargs):
    """The UMa LOS shadowing model.

    Args:
        preShadowing (cp.ndarray): The pre-shadowing in dB. The shape is (num_tx, num_rx).
        delta_distance (cp.ndarray): The distance difference in meters. The shape is (num_tx, num_rx).
        n_tx (int): The number of transmitters.
        n_rx (int): The number of receivers.
        std (float): The standard deviation of the shadowing in dB. The default value is 4.
        d_correlation (float): The decorrelation distance in meters. The default value is 50.

    Returns:
        cp.ndarray: The shadowing in dB. The shape is (num_tx, num_rx).
    """
    std = kwargs.get('std', 4)
    d_correlation = kwargs.get('d_correlation', 50)
    delta_distance = kwargs.get('delta_distance', None)
    if delta_distance is None:
        delta_distance = cp.zeros(preShadowing.shape)
    if preShadowing.shape != delta_distance.shape:
        preShadowing = cp.random.normal(0, std, delta_distance.shape)
    new_shadowing = cp.random.normal(0, std, preShadowing.shape)
        
    shadowing = 10 * cp.log10(1e-9+cp.exp(-1*(delta_distance/d_correlation))* (10 ** (preShadowing / 10)) + cp.sqrt(1-cp.exp(-2*(delta_distance/d_correlation)))*(10**(new_shadowing/10)))
    return shadowing
    