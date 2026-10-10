

使用的是fognetpp文件夹中simulations/testing/wireless.ned 和wireless.ini，在当前目录的fognetpp_config下

其中使用的是802.11的标准，频谱用的是2.4GHz，其余参数如下

Config:
[Config BaseConfig]
**.constraintAreaMinX = 0m
**.constraintAreaMinY = 0m
**.constraintAreaMinZ = 0m
**.constraintAreaMaxX = 600m
**.constraintAreaMaxY = 400m
**.constraintAreaMaxZ = 0m
**.user*.mobilityType = "LinearMobility"
**.user*.mobility.speed = 20mps
**.user*.mobility.angle = 0
**.user*.mobility.acceleration = 0
**.user*.mobility.updateInterval = 100ms
**.radio.transmitter.power = 1.5mW
noise_floor = -110.0  # dBm



采用FreeSpacePathLoss，运行1000秒

user位置：397,78
两个AP：123，175；467，175
user顺着x轴来回移动，先从左到右，到头了就往回走。

使用airfogsim进行模拟运行，代码为airfogsim_sinr_simulation.py；运行结果在fognetpp_vs_airfogsim_comparison.png。



