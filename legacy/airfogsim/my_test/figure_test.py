import matplotlib.pyplot as plt

# 10个点的坐标
x = [2, 4, 1, 5, 3, 8, 7, 6, 9, 10]  # x 坐标
y = [3, 1, 4, 6, 2, 7, 9, 5, 10, 8]  # y 坐标

# 创建一个绘图
plt.figure(figsize=(8, 6))

# 绘制轨迹
plt.plot(x, y, marker='o', linestyle='-', color='b', label='Trajectory')

# 绘制点
plt.scatter(x, y, color='r')  # 使用红色标记每个点

# 标题与标签
plt.title("Point Trajectory", fontsize=14)
plt.xlabel("X", fontsize=12)
plt.ylabel("Y", fontsize=12)

# 显示图例
plt.legend()

# 显示图形
plt.grid(True)
plt.show()
