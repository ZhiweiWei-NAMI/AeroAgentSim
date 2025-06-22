#!/usr/bin/env python3
"""
对比FogNet++和AirFogSim的SINR分布 - 简化版本
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# FogNet++的数据
bins = [51.5275, 138.5887, 225.6499, 312.7111, 399.7722, 486.8334, 573.8946, 660.9558, 748.0170, 835.0782, 922.1394, 1009.2005, 1096.2617, 1183.3229, 1270.3841, 1357.4453, 1444.5065, 1531.5677, 1618.6288, 1705.6900, 1792.7512, 1879.8124, 1966.8736]
fognetpp_bin_val = [0, 2, 0, 4649, 3482, 4538, 6565, 6094, 5373, 4993, 4417, 4119, 4433, 4048, 4447, 5008, 7100, 6739, 0, 0, 0, 0, 0]

# AirFogSim的CSV文件路径
airfogsim_csv = 'user_best_ap_sinr_data.csv'

print("FogNet++ vs AirFogSim SINR分布对比分析")
print("=" * 50)

# 读取AirFogSim数据
print("读取AirFogSim数据...")
df = pd.read_csv(airfogsim_csv)
airfogsim_sinr_linear = df['sinr_linear'].values

print(f"AirFogSim数据点数: {len(airfogsim_sinr_linear)}")
print(f"AirFogSim SINR范围: {airfogsim_sinr_linear.min():.2f} ~ {airfogsim_sinr_linear.max():.2f}")

# 计算FogNet++的概率分布
fognetpp_total = sum(fognetpp_bin_val)
fognetpp_prob = [val / fognetpp_total for val in fognetpp_bin_val]

print(f"FogNet++数据点数: {fognetpp_total}")
print(f"FogNet++有效bins数: {sum(1 for val in fognetpp_bin_val if val > 0)}")

# 使用相同的bins对AirFogSim数据进行分组
airfogsim_hist, _ = np.histogram(airfogsim_sinr_linear, bins=bins)
airfogsim_prob = airfogsim_hist / airfogsim_hist.sum()

print(f"AirFogSim有效bins数: {sum(1 for val in airfogsim_hist if val > 0)}")

# 计算bin中心点用于绘图
bin_centers = [(bins[i] + bins[i+1]) / 2 for i in range(len(bins)-1)]

# 确保所有数组长度一致
min_length = min(len(bin_centers), len(fognetpp_prob), len(airfogsim_prob))
bin_centers = bin_centers[:min_length]
fognetpp_prob = fognetpp_prob[:min_length]
airfogsim_prob = airfogsim_prob[:min_length]

print(f"使用的bins数量: {min_length}")

# 设置中文字体支持
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'SimHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

# 创建对比图
fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(14, 16))

# 计算bin宽度
bin_widths = [bins[i+1] - bins[i] for i in range(min_length)]

# 子图1: FogNet++分布
bars1 = ax1.bar(bin_centers, fognetpp_prob, width=bin_widths, alpha=0.7, color='red', edgecolor='black', label='FogNet++')
ax1.set_title('FogNet++ SINR Distribution (Probability)', fontsize=14, fontweight='bold')
ax1.set_xlabel('SINR (Linear Scale)', fontsize=12)
ax1.set_ylabel('Probability', fontsize=12)
ax1.grid(True, alpha=0.3)
ax1.legend()

# 添加统计信息
fognetpp_mean = sum(bin_centers[i] * fognetpp_prob[i] for i in range(len(bin_centers)))
fognetpp_stats = f'Mean: {fognetpp_mean:.2f}\nTotal Samples: {fognetpp_total}'
ax1.text(0.02, 0.98, fognetpp_stats, transform=ax1.transAxes, 
         verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8))

# 子图2: AirFogSim分布
bars2 = ax2.bar(bin_centers, airfogsim_prob, width=bin_widths, alpha=0.7, color='blue', edgecolor='black', label='AirFogSim')
ax2.set_title('AirFogSim SINR Distribution (Probability)', fontsize=14, fontweight='bold')
ax2.set_xlabel('SINR (Linear Scale)', fontsize=12)
ax2.set_ylabel('Probability', fontsize=12)
ax2.grid(True, alpha=0.3)
ax2.legend()

# 添加统计信息
airfogsim_mean = np.mean(airfogsim_sinr_linear)
airfogsim_stats = f'Mean: {airfogsim_mean:.2f}\nTotal Samples: {len(airfogsim_sinr_linear)}'
ax2.text(0.02, 0.98, airfogsim_stats, transform=ax2.transAxes, 
         verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8))

# 子图3: 对比图
width_factor = 0.4
width_values = [w * width_factor for w in bin_widths]
x_offset = [w * width_factor / 2 for w in bin_widths]

bars3a = ax3.bar([bin_centers[i] - x_offset[i] for i in range(len(bin_centers))], 
                 fognetpp_prob, width=width_values, alpha=0.7, color='red', label='FogNet++', edgecolor='black')
bars3b = ax3.bar([bin_centers[i] + x_offset[i] for i in range(len(bin_centers))], 
                 airfogsim_prob, width=width_values, alpha=0.7, color='blue', label='AirFogSim', edgecolor='black')

ax3.set_title('FogNet++ vs AirFogSim SINR Distribution Comparison', fontsize=14, fontweight='bold')
ax3.set_xlabel('SINR (Linear Scale)', fontsize=12)
ax3.set_ylabel('Probability', fontsize=12)
ax3.grid(True, alpha=0.3)
ax3.legend()

plt.tight_layout()
plt.savefig('fognetpp_vs_airfogsim_comparison.png', dpi=300, bbox_inches='tight')
plt.savefig('fognetpp_vs_airfogsim_comparison.pdf', bbox_inches='tight')

print("\n对比图已保存:")
print("  - fognetpp_vs_airfogsim_comparison.png")
print("  - fognetpp_vs_airfogsim_comparison.pdf")

# 显示图表（如果在交互环境中）
try:
    plt.show()
except:
    print("  注意: 无法显示图表，但已保存到文件")

# 统计分析
print("\n" + "=" * 50)
print("统计分析")
print("=" * 50)

# 计算各种统计指标
def calculate_distribution_stats(bin_centers, probabilities):
    """计算分布的统计指标"""
    mean = sum(bin_centers[i] * probabilities[i] for i in range(len(bin_centers)))
    
    # 计算方差和标准差
    variance = sum(probabilities[i] * (bin_centers[i] - mean)**2 for i in range(len(bin_centers)))
    std = np.sqrt(variance)
    
    # 找到众数（概率最大的bin）
    max_prob_idx = np.argmax(probabilities)
    mode = bin_centers[max_prob_idx]
    
    # 计算偏度（使用三阶中心矩）
    if std > 0:
        skewness = sum(probabilities[i] * ((bin_centers[i] - mean) / std)**3 for i in range(len(bin_centers)))
    else:
        skewness = 0
    
    return {
        'mean': mean,
        'std': std,
        'variance': variance,
        'mode': mode,
        'max_prob': probabilities[max_prob_idx],
        'skewness': skewness
    }

fognetpp_stats = calculate_distribution_stats(bin_centers, fognetpp_prob)
airfogsim_stats = calculate_distribution_stats(bin_centers, airfogsim_prob)

print("FogNet++分布统计:")
print(f"  - 均值: {fognetpp_stats['mean']:.2f}")
print(f"  - 标准差: {fognetpp_stats['std']:.2f}")
print(f"  - 众数: {fognetpp_stats['mode']:.2f} (概率: {fognetpp_stats['max_prob']:.4f})")
print(f"  - 偏度: {fognetpp_stats['skewness']:.3f}")

print("\nAirFogSim分布统计:")
print(f"  - 均值: {airfogsim_stats['mean']:.2f}")
print(f"  - 标准差: {airfogsim_stats['std']:.2f}")
print(f"  - 众数: {airfogsim_stats['mode']:.2f} (概率: {airfogsim_stats['max_prob']:.4f})")
print(f"  - 偏度: {airfogsim_stats['skewness']:.3f}")

# 计算分布差异指标
print("\n分布差异分析:")
print("-" * 30)

# 计算重叠系数
overlap = sum(min(fognetpp_prob[i], airfogsim_prob[i]) for i in range(len(fognetpp_prob)))
print(f"分布重叠系数: {overlap:.4f}")

print(f"均值差异: {abs(fognetpp_stats['mean'] - airfogsim_stats['mean']):.2f}")
print(f"标准差差异: {abs(fognetpp_stats['std'] - airfogsim_stats['std']):.2f}")

# 生成简报
print("\n" + "=" * 50)
print("分布对比简报")
print("=" * 50)

print("1. 数据概览:")
print(f"   - FogNet++: {fognetpp_total:,} 个样本")
print(f"   - AirFogSim: {len(airfogsim_sinr_linear):,} 个样本")

print("\n2. 分布特征对比:")
print(f"   - 均值: FogNet++ {fognetpp_stats['mean']:.0f} vs AirFogSim {airfogsim_stats['mean']:.0f}")
print(f"   - 众数: FogNet++ {fognetpp_stats['mode']:.0f} vs AirFogSim {airfogsim_stats['mode']:.0f}")
print(f"   - 标准差: FogNet++ {fognetpp_stats['std']:.0f} vs AirFogSim {airfogsim_stats['std']:.0f}")

print("\n3. 分布形状:")
if fognetpp_stats['skewness'] > 0:
    fognetpp_skew_desc = "右偏"
elif fognetpp_stats['skewness'] < 0:
    fognetpp_skew_desc = "左偏"
else:
    fognetpp_skew_desc = "对称"

if airfogsim_stats['skewness'] > 0:
    airfogsim_skew_desc = "右偏"
elif airfogsim_stats['skewness'] < 0:
    airfogsim_skew_desc = "左偏"
else:
    airfogsim_skew_desc = "对称"

print(f"   - FogNet++: {fognetpp_skew_desc} (偏度: {fognetpp_stats['skewness']:.2f})")
print(f"   - AirFogSim: {airfogsim_skew_desc} (偏度: {airfogsim_stats['skewness']:.2f})")

print("\n4. 相似性分析:")
print(f"   - 分布重叠系数: {overlap:.3f} ({overlap*100:.1f}%)")

if overlap > 0.7:
    similarity = "高度相似"
elif overlap > 0.5:
    similarity = "中等相似"
elif overlap > 0.3:
    similarity = "低度相似"
else:
    similarity = "差异显著"

print(f"   - 相似性评估: {similarity}")

print("\n5. 主要差异:")
mean_diff_pct = abs(fognetpp_stats['mean'] - airfogsim_stats['mean']) / fognetpp_stats['mean'] * 100
print(f"   - 均值差异: {mean_diff_pct:.1f}%")

if abs(fognetpp_stats['skewness'] - airfogsim_stats['skewness']) > 0.5:
    print("   - 分布形状存在明显差异")
else:
    print("   - 分布形状基本相似")

print("\n分析完成！")
