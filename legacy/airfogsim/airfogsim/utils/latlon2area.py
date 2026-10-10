import math

def haversine(lat1, lon1, lat2, lon2):
    # 将角度转化为弧度
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    # 纬度差
    dlat = lat2 - lat1 
    # 经度差
    dlon = lon2 - lon1 
    # Haversine公式
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a)) 
    r = 6371 # 地球平均半径，单位为公里
    return c * r * 1000  # 返回单位为米

# 定义经纬度
lat1, lon1 = 31.288334, 121.491877
lat2, lon2 = 31.331631, 121.527668

# 计算距离
distance = haversine(lat1, lon1, lat2, lon2)
print(f"距离为：{distance} 米")

# 计算面积（近似为矩形，忽略地球曲率）
area = distance**2
print(f"面积为：{area} 平方米")
