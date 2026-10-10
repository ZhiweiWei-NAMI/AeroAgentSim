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

def OutageProbCallback(outage_prob_type):
    """The callback function to get the outage probability.

    Args:
        outage_prob_type (str): The outage probability type.

    Returns:
        function: The callback function to get the outage probability.
    """
    if outage_prob_type == 'Rayleigh':
        return rayleigh_outage_prob
    else:
        raise ValueError(f"Invalid outage probability type: {outage_prob_type}")

def rayleigh_outage_prob(snr, snr_threshold):
    """The Rayleigh outage probability model.

    Args:
        snr (cp.ndarray): The signal-to-noise ratio.
        snr_threshold (float): The signal-to-noise ratio threshold.

    Returns:
        cp.ndarray: The outage probability.
    """
    # 如果snr有任意shape是0，则返回空数组
    if cp.prod(snr.shape) == 0:
        return cp.ones_like(snr)
    # 处理snr<=0的情况
    snr = cp.maximum(snr, 1e-9)
    return 1 - cp.exp(-snr_threshold / snr)
