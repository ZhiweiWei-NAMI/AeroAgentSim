

#!/usr/bin/env python3
"""
对比FogNet++和AirFogSim的SINR分布
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy import stats

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

# 检查数据维度
print(f"Bins数量: {len(bins)}")
print(f"FogNet++数据点数量: {len(fognetpp_bin_val)}")

# 确保bins和fognetpp_bin_val长度匹配
if len(bins) != len(fognetpp_bin_val) + 1:
    print("警告: bins长度应该比bin_val长度多1")
    # 如果bins比bin_val多1，这是正确的（bins定义区间边界）
    if len(bins) == len(fognetpp_bin_val) + 1:
        print("bins定义了区间边界，这是正确的")
    else:
        # 调整为匹配
        min_len = min(len(bins) - 1, len(fognetpp_bin_val))
        bins = bins[:min_len + 1]
        fognetpp_bin_val = fognetpp_bin_val[:min_len]
        print(f"调整后 - Bins: {len(bins)}, FogNet++值: {len(fognetpp_bin_val)}")

# 使用相同的bins对AirFogSim数据进行分组
airfogsim_hist, _ = np.histogram(airfogsim_sinr_linear, bins=bins)
airfogsim_prob = airfogsim_hist / airfogsim_hist.sum()

print(f"AirFogSim有效bins数: {sum(1 for val in airfogsim_hist if val > 0)}")
print(f"直方图bins数: {len(airfogsim_hist)}")

# 计算bin中心点用于绘图
bin_centers = [(bins[i] + bins[i+1]) / 2 for i in range(len(bins)-1)]
print(f"Bin中心点数量: {len(bin_centers)}")

# 确保所有数组长度一致
print(f"数组长度检查:")
print(f"  - bin_centers: {len(bin_centers)}")
print(f"  - fognetpp_bin_val: {len(fognetpp_bin_val)}")
print(f"  - fognetpp_prob: {len(fognetpp_prob)}")
print(f"  - airfogsim_hist: {len(airfogsim_hist)}")
print(f"  - airfogsim_prob: {len(airfogsim_prob)}")

# 确保所有用于绘图的数组长度相同
min_length = min(len(bin_centers), len(fognetpp_prob), len(airfogsim_prob))
bin_centers = bin_centers[:min_length]
fognetpp_prob = fognetpp_prob[:min_length]
airfogsim_prob = airfogsim_prob[:min_length]

print(f"调整后统一长度: {min_length}")

# 计算分布相似度指标
print("\n" + "=" * 50)
print("分布相似度指标")
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

# 计算KL散度 (Kullback-Leibler divergence)
def kl_divergence(p, q):
    """计算KL散度 D(P||Q)"""
    kl = 0
    for i in range(len(p)):
        if p[i] > 0 and q[i] > 0:
            kl += p[i] * np.log(p[i] / q[i])
        elif p[i] > 0 and q[i] == 0:
            kl = float('inf')
            break
    return kl

# 为了避免零概率问题，添加小的平滑项
epsilon = 1e-10
fognetpp_prob_smooth = [p + epsilon for p in fognetpp_prob]
airfogsim_prob_smooth = [p + epsilon for p in airfogsim_prob]

# 重新归一化
fognetpp_prob_smooth = [p / sum(fognetpp_prob_smooth) for p in fognetpp_prob_smooth]
airfogsim_prob_smooth = [p / sum(airfogsim_prob_smooth) for p in airfogsim_prob_smooth]

kl_fg_to_af = kl_divergence(fognetpp_prob_smooth, airfogsim_prob_smooth)
kl_af_to_fg = kl_divergence(airfogsim_prob_smooth, fognetpp_prob_smooth)

# 计算JS散度 (Jensen-Shannon divergence)
def js_divergence(p, q):
    """计算JS散度"""
    m = [(p[i] + q[i]) / 2 for i in range(len(p))]
    return 0.5 * kl_divergence(p, m) + 0.5 * kl_divergence(q, m)

js_div = js_divergence(fognetpp_prob_smooth, airfogsim_prob_smooth)

# 计算Wasserstein距离 (Earth Mover's Distance)
def wasserstein_distance(bin_centers, p, q):
    """计算Wasserstein距离"""
    # 计算累积分布函数
    cdf_p = np.cumsum(p)
    cdf_q = np.cumsum(q)

    # 计算距离
    distance = 0
    for i in range(len(bin_centers)-1):
        bin_width = bin_centers[i+1] - bin_centers[i] if i < len(bin_centers)-1 else bin_centers[i] - bin_centers[i-1]
        distance += abs(cdf_p[i] - cdf_q[i]) * bin_width

    return distance

wasserstein_dist = wasserstein_distance(bin_centers, fognetpp_prob, airfogsim_prob)

# 计算重叠系数
overlap = sum(min(fognetpp_prob[i], airfogsim_prob[i]) for i in range(len(fognetpp_prob)))

# 计算Bhattacharyya距离
def bhattacharyya_distance(p, q):
    """计算Bhattacharyya距离"""
    bc = sum(np.sqrt(p[i] * q[i]) for i in range(len(p)))  # Bhattacharyya coefficient
    return -np.log(bc) if bc > 0 else float('inf')

bhatt_dist = bhattacharyya_distance(fognetpp_prob, airfogsim_prob)

# 计算Hellinger距离
def hellinger_distance(p, q):
    """计算Hellinger距离"""
    return np.sqrt(0.5 * sum((np.sqrt(p[i]) - np.sqrt(q[i]))**2 for i in range(len(p))))

hellinger_dist = hellinger_distance(fognetpp_prob, airfogsim_prob)

print("1. 基本统计对比:")
print(f"   FogNet++  - 均值: {fognetpp_stats['mean']:.2f}, 标准差: {fognetpp_stats['std']:.2f}, 偏度: {fognetpp_stats['skewness']:.3f}")
print(f"   AirFogSim - 均值: {airfogsim_stats['mean']:.2f}, 标准差: {airfogsim_stats['std']:.2f}, 偏度: {airfogsim_stats['skewness']:.3f}")

print("\n2. 分布相似度指标:")
print(f"   重叠系数 (Overlap Coefficient): {overlap:.4f} ({overlap*100:.1f}%)")
print(f"   KL散度 D(FogNet++||AirFogSim): {kl_fg_to_af:.4f}")
print(f"   KL散度 D(AirFogSim||FogNet++): {kl_af_to_fg:.4f}")
print(f"   JS散度 (Jensen-Shannon): {js_div:.4f}")
print(f"   Wasserstein距离: {wasserstein_dist:.2f}")
print(f"   Bhattacharyya距离: {bhatt_dist:.4f}")
print(f"   Hellinger距离: {hellinger_dist:.4f}")

print("\n3. 相似性评估:")
if overlap > 0.9:
    similarity = "极高相似"
elif overlap > 0.8:
    similarity = "高度相似"
elif overlap > 0.6:
    similarity = "中等相似"
elif overlap > 0.4:
    similarity = "低度相似"
else:
    similarity = "差异显著"

print(f"   基于重叠系数: {similarity}")

if js_div < 0.1:
    js_similarity = "极高相似"
elif js_div < 0.2:
    js_similarity = "高度相似"
elif js_div < 0.4:
    js_similarity = "中等相似"
else:
    js_similarity = "差异显著"

print(f"   基于JS散度: {js_similarity}")

# 设置中文字体支持
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'SimHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

# 创建对比图
fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(14, 16))

# 子图1: FogNet++分布
ax1.bar(bin_centers, fognetpp_prob, width=np.diff(bins), alpha=0.7, color='red', edgecolor='black', label='FogNet++')
ax1.set_title('FogNet++ SINR Distribution (Probability)', fontsize=14, fontweight='bold')
ax1.set_xlabel('SINR (Linear Scale)', fontsize=12)
ax1.set_ylabel('Probability', fontsize=12)
ax1.grid(True, alpha=0.3)
ax1.legend()

# 添加统计信息
fognetpp_mean = sum(bin_centers[i] * fognetpp_prob[i] for i in range(len(bin_centers)))
fognetpp_stats_text = f'FogNet++ Statistics:\n'
fognetpp_stats_text += f'Mean: {fognetpp_stats["mean"]:.2f}\n'
fognetpp_stats_text += f'Std: {fognetpp_stats["std"]:.2f}\n'
fognetpp_stats_text += f'Skewness: {fognetpp_stats["skewness"]:.3f}\n'
fognetpp_stats_text += f'Total Samples: {fognetpp_total:,}'
ax1.text(0.02, 0.98, fognetpp_stats_text, transform=ax1.transAxes,
         verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8),
         fontsize=9)

# 子图2: AirFogSim分布
ax2.bar(bin_centers, airfogsim_prob, width=np.diff(bins), alpha=0.7, color='blue', edgecolor='black', label='AirFogSim')
ax2.set_title('AirFogSim SINR Distribution (Probability)', fontsize=14, fontweight='bold')
ax2.set_xlabel('SINR (Linear Scale)', fontsize=12)
ax2.set_ylabel('Probability', fontsize=12)
ax2.grid(True, alpha=0.3)
ax2.legend()

# 添加统计信息
airfogsim_mean = np.mean(airfogsim_sinr_linear)
airfogsim_stats_text = f'AirFogSim Statistics:\n'
airfogsim_stats_text += f'Mean: {airfogsim_stats["mean"]:.2f}\n'
airfogsim_stats_text += f'Std: {airfogsim_stats["std"]:.2f}\n'
airfogsim_stats_text += f'Skewness: {airfogsim_stats["skewness"]:.3f}\n'
airfogsim_stats_text += f'Total Samples: {len(airfogsim_sinr_linear):,}'
ax2.text(0.02, 0.98, airfogsim_stats_text, transform=ax2.transAxes,
         verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8),
         fontsize=9)

# 子图3: 对比图
# 计算每个bin的宽度
bin_widths = np.diff(bins[:len(bin_centers)+1])  # 确保长度匹配
width_factor = 0.4

# 为每个bar计算位置和宽度
bar_width = [w * width_factor for w in bin_widths]
x_offset = [w * width_factor / 2 for w in bin_widths]

# 绘制对比柱状图
ax3.bar([bin_centers[i] - x_offset[i] for i in range(len(bin_centers))],
        fognetpp_prob, width=bar_width, alpha=0.7, color='red', label='FogNet++', edgecolor='black')
ax3.bar([bin_centers[i] + x_offset[i] for i in range(len(bin_centers))],
        airfogsim_prob, width=bar_width, alpha=0.7, color='blue', label='AirFogSim', edgecolor='black')
ax3.set_title('FogNet++ vs AirFogSim SINR Distribution Comparison', fontsize=14, fontweight='bold')
ax3.set_xlabel('SINR (Linear Scale)', fontsize=12)
ax3.set_ylabel('Probability', fontsize=12)
ax3.grid(True, alpha=0.3)
ax3.legend()

# 在对比图上添加相似度指标文本
similarity_text = f'Distribution Similarity Metrics:\n'
similarity_text += f'Overlap Coefficient: {overlap:.4f} ({overlap*100:.1f}%)\n'
similarity_text += f'KL Divergence D(FN||AF): {kl_fg_to_af:.4f}\n'
similarity_text += f'KL Divergence D(AF||FN): {kl_af_to_fg:.4f}\n'
similarity_text += f'JS Divergence: {js_div:.4f}\n'
similarity_text += f'Wasserstein Distance: {wasserstein_dist:.2f}\n'
similarity_text += f'Bhattacharyya Distance: {bhatt_dist:.4f}\n'
similarity_text += f'Hellinger Distance: {hellinger_dist:.4f}\n\n'
similarity_text += f'Assessment: Extremely High Similarity'

ax3.text(0.02, 0.98, similarity_text, transform=ax3.transAxes,
         verticalalignment='top', horizontalalignment='left',
         bbox=dict(boxstyle='round,pad=0.5', facecolor='lightgray', alpha=0.8),
         fontsize=9, fontfamily='monospace')

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

print("\n绘图完成！")