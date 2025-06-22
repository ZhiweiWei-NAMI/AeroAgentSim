#!/usr/bin/env python3
"""
使用AirFogSim平台的UAV对AP的SINR记录仿真脚本

使用AirFogSim的FrequencyManager和SignalDataProvider进行SINR计算：
- 使用FrequencyManager进行频率资源管理和SINR计算
- 使用SignalDataProvider进行信号传播建模
- 使用signal_integration的calculate_link_quality函数
- 创建AP信号源并计算用户移动过程中的SINR变化
- 严格按照OMNeT++配置参数

配置参数：
- AP位置：(123, 175) 和 (467, 175)
- User初始位置：(397, 78)
- 移动轨迹：向x轴右边移动，然后返回
- 约束区域: 600m × 400m × 0m
- 移动速度: 20 m/s
- 发射功率: 1.5mW
- 更新间隔: 100ms
- 频段: 2.4GHz IEEE 802.11, Channel 0
"""

import sys
import os
# 添加src目录到Python路径
current_dir = os.path.dirname(os.path.abspath(__file__))
src_path = os.path.join(current_dir, '..', 'src')
sys.path.insert(0, src_path)

import csv
import math
import simpy
import matplotlib.pyplot as plt
import numpy as np
from typing import List, Tuple, Dict
from airfogsim.core.environment import Environment
from airfogsim.agent.drone import DroneAgent
from airfogsim.agent.terminal import TerminalAgent
from airfogsim.component.mobility import MoveToComponent
from airfogsim.component.communication import CommunicationComponent
from airfogsim.manager.frequency import FrequencyManager
from airfogsim.dataprovider.signal import SignalDataProvider, SignalSource
from airfogsim.dataprovider.signal_integration import calculate_link_quality, register_link_quality_calculator
from airfogsim.manager.airspace import AirspaceManager
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

print('开始使用AirFogSim平台的UAV对AP的SINR记录仿真')

# OMNeT++配置参数
speed = 20.0  # 20 m/s
update_interval = 0.1  # 100ms
tx_power_mw = 1.5  # mW
tx_power_dbm = 10 * math.log10(tx_power_mw)  # 转换为dBm
frequency_mhz = 2400.0  # 2.4GHz
noise_floor = -110.0  # dBm

# AP位置（根据你的描述）
ap1_position = (123.0, 175.0, 0.0)
ap2_position = (467.0, 175.0, 0.0)

# User初始位置
user_initial_position = (397.0, 78.0, 0.0)

print(f'AP1位置: {ap1_position}')
print(f'AP2位置: {ap2_position}')
print(f'User初始位置: {user_initial_position}')

def calculate_distance(pos1, pos2):
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(pos1, pos2)))

def setup_airfogsim_environment():
    """设置AirFogSim环境，包括FrequencyManager和SignalDataProvider"""
    # 创建仿真环境
    env = Environment()

    # 创建空域管理器
    airspace_manager = AirspaceManager(env)
    env.airspace_manager = airspace_manager

    # 创建信号数据提供者
    signal_provider = SignalDataProvider(
        env=env,
        config={
            'propagation_model': 'free_space',
            'default_noise_floor': noise_floor,
            'weather_enabled': False
        }
    )
    env.signal_data_provider = signal_provider

    # 创建频率管理器，启用信号提供者集成
    frequency_manager = FrequencyManager(
        env=env,
        total_bandwidth=20.0,  # 20MHz带宽
        block_bandwidth=5.0,   # 2MHz每个资源块
        start_frequency=frequency_mhz,  # 2.4GHz
        power_limit=tx_power_mw,
        config={
            'use_signal_provider_for_sinr': True
        }
    )
    env.frequency_manager = frequency_manager

    # 注册链路质量计算函数
    register_link_quality_calculator(env)

    return env, signal_provider, frequency_manager

def get_sinr_measurement_with_airfogsim(user_pos, ap_pos, ap_id, env, signal_provider):
    """使用AirFogSim的FrequencyManager计算SINR"""
    # 构建链路上下文 - user是发射源，AP是接收端
    link_context = {
        'source_id': 'user',
        'target_id': ap_id,
        'source_position': user_pos,
        'target_position': ap_pos,
        'transmit_power_dbm': tx_power_dbm,
        'center_frequency': frequency_mhz,
        'bandwidth': 2.0,  # 5MHz带宽
        'resource_id': f'resource_user_to_{ap_id}'
    }

    # 使用链路质量计算函数
    quality_info = calculate_link_quality(link_context, env)
    sinr = quality_info.get('sinr', 0)
    received_power = quality_info.get('received_power_dbm', 0)
    return sinr, received_power

# 设置AirFogSim环境
print('设置AirFogSim环境...')
env, signal_provider, frequency_manager = setup_airfogsim_environment()

# 注意：在这个场景中，user是发射源，AP是接收端
# 我们不需要预先创建AP信号源，而是在计算时动态创建user信号源
print('AirFogSim环境设置完成，准备进行SINR计算')

# 定义移动轨迹：持续1000秒来回移动
# 约束区域: 0-600m × 0-400m × 0m
# 移动模式：从起始点(397,78) -> 600 -> 0 -> 600 -> 0 ... 持续往返
simulation_duration = 1000.0  # 1000秒持续仿真
trajectory_points = []

# 移动参数
start_x = 397.0
y_fixed = 78.0
min_x = 0.0
max_x = 600.0

# 计算各段距离和时间
distance_start_to_max = max_x - start_x  # 397 -> 600: 203米
distance_max_to_min = max_x - min_x      # 600 -> 0: 600米
distance_min_to_max = max_x - min_x      # 0 -> 600: 600米

time_start_to_max = distance_start_to_max / speed  # 10.15秒
time_max_to_min = distance_max_to_min / speed      # 30秒
time_min_to_max = distance_min_to_max / speed      # 30秒

# 一个完整周期的时间：起始点->600->0->600
cycle_time = time_start_to_max + time_max_to_min + time_min_to_max  # 70.15秒

print(f'移动参数:')
print(f'  起始位置: x={start_x}m, y={y_fixed}m')
print(f'  移动范围: x从{min_x}m到{max_x}m，y固定在{y_fixed}m')
print(f'  移动速度: {speed}m/s')
print(f'  起始点到600距离: {distance_start_to_max}m，时间: {time_start_to_max:.2f}s')
print(f'  600到0距离: {distance_max_to_min}m，时间: {time_max_to_min:.2f}s')
print(f'  0到600距离: {distance_min_to_max}m，时间: {time_min_to_max:.2f}s')
print(f'  完整周期时间: {cycle_time:.2f}s')
print(f'  仿真总时长: {simulation_duration}s')

# 生成1000秒的轨迹点
current_time = 0.0
while current_time <= simulation_duration:
    # 计算当前在哪个周期内的位置
    time_in_cycle = current_time % cycle_time

    if time_in_cycle <= time_start_to_max:
        # 阶段1：从起始点(397)向右移动到600
        progress = time_in_cycle / time_start_to_max
        current_x = start_x + progress * distance_start_to_max
    elif time_in_cycle <= time_start_to_max + time_max_to_min:
        # 阶段2：从600向左移动到0
        progress = (time_in_cycle - time_start_to_max) / time_max_to_min
        current_x = max_x - progress * distance_max_to_min
    else:
        # 阶段3：从0向右移动到600
        progress = (time_in_cycle - time_start_to_max - time_max_to_min) / time_min_to_max
        current_x = min_x + progress * distance_min_to_max

    trajectory_points.append((current_x, y_fixed, 0.0))
    current_time += update_interval

print(f'生成移动轨迹，共 {len(trajectory_points)} 个点')
print(f'轨迹时长: {len(trajectory_points) * update_interval:.1f}s')
print(f'轨迹模式: {start_x} -> {max_x} -> {min_x} -> {max_x} -> {min_x} ... (持续往返)')

# 记录数据 - 只记录到最大SINR的AP的数据
data_records = []

print('开始使用AirFogSim FrequencyManager计算SINR...')

for i, user_pos in enumerate(trajectory_points):
    timestamp = i * update_interval  # 按100ms间隔

    # 计算到AP1的SINR - 使用AirFogSim
    sinr1, rx_power1 = get_sinr_measurement_with_airfogsim(user_pos, ap1_position, 'ap1', env, signal_provider)
    distance1 = calculate_distance(user_pos, ap1_position)

    # 计算到AP2的SINR - 使用AirFogSim
    sinr2, rx_power2 = get_sinr_measurement_with_airfogsim(user_pos, ap2_position, 'ap2', env, signal_provider)
    distance2 = calculate_distance(user_pos, ap2_position)

    # 选择SINR更大的AP
    if sinr1 >= sinr2:
        # AP1的SINR更大
        best_ap_id = 'ap1'
        best_ap_position = ap1_position
        best_sinr = sinr1
        best_rx_power = rx_power1
        best_distance = distance1
    else:
        # AP2的SINR更大
        best_ap_id = 'ap2'
        best_ap_position = ap2_position
        best_sinr = sinr2
        best_rx_power = rx_power2
        best_distance = distance2

    # 计算SINR的绝对数值（从dB转换）
    best_sinr_linear = 10 ** (best_sinr / 10)

    if best_sinr_linear > 10000:
        print('e')


    # 记录到最大SINR的AP的数据
    record = {
        'timestamp': timestamp,
        'user_x': user_pos[0],
        'user_y': user_pos[1],
        'user_z': user_pos[2],
        'best_ap_id': best_ap_id,
        'ap_x': best_ap_position[0],
        'ap_y': best_ap_position[1],
        'ap_z': best_ap_position[2],
        'distance': best_distance,
        'sinr_db': best_sinr,
        'sinr_linear': best_sinr_linear,
        'received_power_dbm': best_rx_power
    }
    data_records.append(record)

    # 每10个点打印一次进度
    if i % 10 == 0:
        print(f'时间 {timestamp:.2f}s: User({user_pos[0]:.0f},{user_pos[1]:.0f})')
        print(f'  到AP1距离 {distance1:.1f}m, SINR {sinr1:.2f}dB')
        print(f'  到AP2距离 {distance2:.1f}m, SINR {sinr2:.2f}dB')
        print(f'  最佳AP: {best_ap_id}, SINR {best_sinr:.2f}dB')

# 导出CSV文件
fieldnames = ['timestamp', 'user_x', 'user_y', 'user_z', 'best_ap_id', 'ap_x', 'ap_y', 'ap_z', 'distance', 'sinr_db', 'sinr_linear', 'received_power_dbm']

# 导出到最大SINR的AP的数据
with open('response_to_JOSS/user_best_ap_sinr_data.csv', 'w', newline='', encoding='utf-8') as csvfile:
    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(data_records)

print(f'SINR数据已导出:')
print(f'  - user_best_ap_sinr_data.csv: {len(data_records)} 条记录')

# 统计信息
sinr_values = [record['sinr_db'] for record in data_records]
distances = [record['distance'] for record in data_records]

# 统计每个AP被选择的次数
ap1_count = sum(1 for record in data_records if record['best_ap_id'] == 'ap1')
ap2_count = sum(1 for record in data_records if record['best_ap_id'] == 'ap2')

print('最佳AP选择统计:')
print(f'  - 总记录数: {len(data_records)}')
print(f'  - AP1被选择: {ap1_count} 次 ({ap1_count/len(data_records)*100:.1f}%)')
print(f'  - AP2被选择: {ap2_count} 次 ({ap2_count/len(data_records)*100:.1f}%)')
print(f'  - SINR范围: {min(sinr_values):.2f} ~ {max(sinr_values):.2f} dB')
print(f'  - 平均SINR: {sum(sinr_values)/len(sinr_values):.2f} dB')
print(f'  - 距离范围: {min(distances):.1f} ~ {max(distances):.1f} m')
print(f'  - 平均距离: {sum(distances)/len(distances):.1f} m')

# 绘制SINR分布直方图
print('\n绘制SINR分布直方图...')

# 提取SINR绝对数值
sinr_linear_values = [record['sinr_linear'] for record in data_records]

# 设置中文字体支持
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'SimHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

# 创建直方图
plt.figure(figsize=(12, 8))

# 绘制直方图
n, bins, patches = plt.hist(sinr_linear_values, bins=50, alpha=0.7, color='skyblue', edgecolor='black')

# 设置图表标题和标签
plt.title('SINR Distribution (Linear Scale)', fontsize=16, fontweight='bold')
plt.xlabel('SINR (Linear Scale)', fontsize=14)
plt.ylabel('Frequency', fontsize=14)

# 添加网格
plt.grid(True, alpha=0.3)

# 添加统计信息文本
stats_text = f'Total Samples: {len(sinr_linear_values)}\n'
stats_text += f'Mean: {np.mean(sinr_linear_values):.2f}\n'
stats_text += f'Std: {np.std(sinr_linear_values):.2f}\n'
stats_text += f'Min: {np.min(sinr_linear_values):.2f}\n'
stats_text += f'Max: {np.max(sinr_linear_values):.2f}'

plt.text(0.02, 0.98, stats_text, transform=plt.gca().transAxes,
         verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8))

# 保存图表
plt.tight_layout()
plt.savefig('response_to_JOSS/sinr_distribution_histogram.png', dpi=300, bbox_inches='tight')
plt.savefig('response_to_JOSS/sinr_distribution_histogram.pdf', bbox_inches='tight')

print(f'SINR分布直方图已保存:')
print(f'  - sinr_distribution_histogram.png')
print(f'  - sinr_distribution_histogram.pdf')

# 显示图表（如果在交互环境中）
try:
    plt.show()
except:
    print('  注意: 无法显示图表，但已保存到文件')

# 输出SINR分布的详细统计信息
print(f'\nSINR分布统计信息 (绝对数值):')
print(f'  - 样本数量: {len(sinr_linear_values)}')
print(f'  - 平均值: {np.mean(sinr_linear_values):.4f}')
print(f'  - 标准差: {np.std(sinr_linear_values):.4f}')
print(f'  - 最小值: {np.min(sinr_linear_values):.4f}')
print(f'  - 最大值: {np.max(sinr_linear_values):.4f}')
print(f'  - 中位数: {np.median(sinr_linear_values):.4f}')

# 输出分位数信息
percentiles = [25, 50, 75, 90, 95, 99]
print(f'\nSINR分位数信息 (绝对数值):')
for p in percentiles:
    value = np.percentile(sinr_linear_values, p)
    print(f'  - {p}th percentile: {value:.4f}')

print('AirFogSim配置验证:')
print(f'  ✓ 发射功率: {tx_power_mw}mW = {tx_power_dbm:.2f}dBm')
print(f'  ✓ 移动速度: {speed} m/s')
print(f'  ✓ 更新间隔: {update_interval*1000} ms')
print(f'  ✓ 约束区域: 0-600m × 0-400m × 0m')
print(f'  ✓ 频段: 2.4GHz IEEE 802.11, Channel 0')
print(f'  ✓ User初始位置: {user_initial_position}')
print(f'  ✓ 仿真时长: {simulation_duration}s')
print(f'  ✓ 移动轨迹: x轴方向持续往返 {start_x} -> {max_x} -> {min_x} -> {max_x} ...')
print(f'  ✓ 完整周期时间: {cycle_time:.2f}s')
print(f'  ✓ 使用AirFogSim FrequencyManager进行SINR计算')
print(f'  ✓ 使用SignalDataProvider进行信号传播建模')
print(f'使用AirFogSim的UAV对AP的SINR记录仿真完成 - 总计{len(trajectory_points)}个数据点')
