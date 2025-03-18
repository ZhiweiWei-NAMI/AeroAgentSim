import json

from torch.utils import tensorboard
import numpy as np

writer = {
    'Greedy': tensorboard.SummaryWriter("./logs/Greedy"),  # 必须要不同的writer
    'D3QN': tensorboard.SummaryWriter("./logs/D3QN"),
    'TransD3QN': tensorboard.SummaryWriter("./logs/TransD3QN")
}

# 读取第一个 JSON 文件
with open('Greedy.json', 'r', encoding='utf-8') as f:
    Greedy = json.load(f)

# 读取第二个 JSON 文件
with open('D3QN.json', 'r', encoding='utf-8') as f:
    D3QN = json.load(f)

# 读取第三个 JSON 文件
with open('TransD3QN.json', 'r', encoding='utf-8') as f:
    TransD3QN = json.load(f)



for i in range(80):
    writer['Greedy'].add_scalar("completion", Greedy[i]['completion_ratio'], i)  # 要想显示在一张图 表格名字要一样！！
    writer['D3QN'].add_scalar("completion", D3QN[i]['completion_ratio'], i)
    writer['TransD3QN'].add_scalar("completion", TransD3QN[i]['completion_ratio'], i)

    writer['Greedy'].add_scalar("time", Greedy[i]['avg_floating_time'], i)  # 要想显示在一张图 表格名字要一样！！
    writer['D3QN'].add_scalar("time", D3QN[i]['avg_floating_time'], i)
    writer['TransD3QN'].add_scalar("time", TransD3QN[i]['avg_floating_time'], i)

writer['Greedy'].close()
writer['D3QN'].close()
writer['TransD3QN'].close()
