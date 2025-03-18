import numpy as np
import math

def generate_2d_normal_distribution(mu_x, mu_y, sigma_x, sigma_y, rho, x_range, y_range):
    """Get x,y from a 2d normal distribution.

    Args:
        mu_x (float): X-axis mean.
        mu_y (float): Y-axis mean.
        sigma_x (float): X-axis std.
        sigma_y (float): Y-axis std.
        rho (float): Related Coefficient.
        x_range (list): X-axis range.
        y_range (list): Y-axis range.

    Returns:
        int: x
        int: y

    Examples:
        generate_2d_normal_distribution(0.5,0.5,0.03,0.03,0,[0,1],[0,1])
    """
    # 均值向量和协方差矩阵
    mean = [mu_x, mu_y]
    cov = [[sigma_x ** 2, rho * sigma_x * sigma_y],
           [rho * sigma_x * sigma_y, sigma_y ** 2]]

    # 使用numpy生成符合二维正态分布的样本
    x, y = np.random.multivariate_normal(mean, cov)

    # 对生成的点进行截断
    x = np.clip(x, x_range[0], x_range[1])
    y = np.clip(y, y_range[0], y_range[1])

    return x, y

def check_approaching(old_position,new_position,target_position):
    # 计算向量差：new_position - old_position
    vector_old_to_new = [
        new_position[i] - old_position[i]
        for i in range(len(new_position))
    ]

    # 计算向量差：target_position - new_position
    vector_new_to_target = [
        target_position[i] - new_position[i]
        for i in range(len(new_position))
    ]

    # 计算点积
    dot_product = sum(
        vector_old_to_new[i] * vector_new_to_target[i]
        for i in range(len(vector_old_to_new))
    )

    # 如果点积大于零，则夹角为锐角，返回 True 表示正在接近
    return dot_product > 0


def calculate_perpendicular_distance(old_position, new_position, target_position):
    """
    计算目标点(target_position)到移动轨迹(old_position→new_position)的垂直距离（2D版本）

    Args:
        old_position (tuple/list): 旧位置坐标 (x1, y1)
        new_position (tuple/list): 新位置坐标 (x2, y2)
        target_position (tuple/list): 目标点坐标 (x3, y3)

    Returns:
        float: 垂距（始终为非负数）
    """
    # 解包坐标（假设为2D）
    Ax, Ay = old_position
    Bx, By = new_position
    Cx, Cy = target_position

    # 计算向量AB的分量
    ABx = Bx - Ax
    ABy = By - Ay

    # 计算向量AC的分量
    ACx = Cx - Ax
    ACy = Cy - Ay

    # 计算AB向量的长度
    AB_length = math.sqrt(ABx ** 2 + ABy ** 2)

    # 处理AB为零向量的情况
    if AB_length == 0:
        return 0.0

    # 手动计算二维叉乘绝对值：|AB × AC| = |ABx*ACy - ABy*ACx|
    cross_abs = abs(ABx * ACy - ABy * ACx)

    # 垂距 = 叉乘绝对值 / AB长度
    return cross_abs / AB_length


def calculate_distance(node_position, target_position):
    """
    计算两个坐标点之间的欧几里得距离

    Args:
        node_position (iterable): 第一个点的坐标 (如 [x, y] 或 [x, y, z])
        target_position (iterable): 第二个点的坐标 (维度需与第一个点一致)

    Returns:
        float: 两点之间的直线距离
    """
    squared_sum = 0.0
    for i in range(len(node_position)):
        squared_sum += (node_position[i] - target_position[i]) ** 2
    return math.sqrt(squared_sum)