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

def FastFadingCallback(fastfading_type):
    """The callback function to get the fast fading.

    Args:
        fastfading_type (str): The fast fading type.

    Returns:
        function: The callback function to get the fast fading.
    """
    if fastfading_type == 'Rayleigh':
        return Rayleigh_fastfading
    else:
        raise ValueError(f"Invalid fast fading type: {fastfading_type}")
        
        
def Rayleigh_fastfading(n_tx, n_rx, n_rb, **kwargs):
    """The Rayleigh fast fading model.

    Args:
        n_tx (int): The number of transmitters.
        n_rx (int): The number of receivers.
        n_rb (int): The number of resource blocks.
        std (float): The standard deviation of the fast fading in dB. The default value is 1.

    Returns:
        cp.ndarray: The fast fading in dB. The shape is (n_tx, n_rx, n_rb).
    """
    std = kwargs.get('std', 1)
    # 生成两个独立的高斯分布随机变量
    gaussian1 = cp.random.normal(0, std, size=(n_tx, n_rx))
    gaussian2 = cp.random.normal(0, std, size=(n_tx, n_rx))
    # 计算瑞利分布的信道增益
    r = cp.sqrt(gaussian1 ** 2 + gaussian2 ** 2)
    # 计算信道增益的平均值；如果是empty，则返回None
    if r.size == 0:
        h = cp.empty((n_tx, n_rx, n_rb))
        return h
    omega = cp.mean(r ** 2)
    # 计算瑞利分布的概率密度函数
    p_r = (2 * r / omega) * cp.exp(-r ** 2 / omega)
    # 计算信道增益
    h = 10 * cp.log10(1e-9+p_r)
    # repeat n_rb times
    h = h[:, :, None] * cp.ones((1, 1, n_rb))
    return h
