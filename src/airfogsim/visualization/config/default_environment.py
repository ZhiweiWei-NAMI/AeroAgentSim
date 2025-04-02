"""
默认环境配置，包含空域、频率和着陆点的默认设置
"""

DEFAULT_AIRSPACE = {
    "x_range": (0, 1000),
    "y_range": (0, 1000),
    "altitude_range": (0, 500),
    "max_capacity": 10,
    "attributes": {
        "name": "主飞行区域", 
        "weather": "clear"
    }
}

DEFAULT_FREQUENCY = {
    "frequency_range": (2400, 2500),
    "bandwidth": 20,
    "max_users": 8,
    "power_limit": 50.0,
    "attributes": {
        "purpose": "控制通信", 
        "protocol": "IEEE 802.11"
    }
}

DEFAULT_LANDING_SPOT = {
    "location": (10, 10, 0),
    "radius": 15.0,
    "max_capacity": 2,
    "has_charging": True,
    "has_data_transfer": True,
    "attributes": {
        "name": "基地", 
        "charging_power": 200.0
    }
}