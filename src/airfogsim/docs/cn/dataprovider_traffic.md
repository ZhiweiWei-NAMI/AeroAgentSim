# 交通数据提供者 (TrafficDataProvider) 使用指南

`TrafficDataProvider` 是 AirFogSim 中用于提供地面车辆位置数据的组件，它支持两种数据源：

1. **SUMO 交通仿真器**：通过 TraCI 接口实时获取车辆位置
2. **轨迹文件**：从 CSV 等文件格式读取预定义的车辆轨迹

本文档介绍如何配置和使用 TrafficDataProvider，以及如何将其与 AirFogSim 的事件驱动架构集成。

## 基本概念

TrafficDataProvider 作为一个数据提供者，遵循以下工作流程：

1. 从数据源（SUMO 或文件）加载交通数据
2. 在仿真运行期间触发 `TrafficUpdate` 事件
3. 通过事件回调机制通知订阅者（如无人机、着陆管理器等）

## 配置选项

### 通用配置

```python
traffic_config = {
    'source': 'sumo',  # 或 'file'
    'update_interval': 1.0,  # 更新间隔（秒）
    'coordinate_conversion': {
        # 坐标转换配置
        'type': 'none' | 'offset_scale' | 'latlon',
        # 其他参数取决于转换类型
    }
}
```

### SUMO 数据源配置

```python
sumo_config = {
    'host': 'localhost',  # SUMO TraCI 服务器主机
    'port': 8813,         # SUMO TraCI 服务器端口
    'vehicle_filter': {   # 可选：过滤特定类型的车辆
        'type': 'car'     # 只跟踪类型为"car"的车辆
    }
}
```

### 文件数据源配置

```python
file_config = {
    'path': 'data/vehicle_trajectories.csv',  # 轨迹文件路径
    'format': 'csv',                          # 文件格式
    'time_column': 'timestamp',               # 时间列名
    'id_column': 'vehicle_id',                # 车辆 ID 列名
    'pos_columns': ['x', 'y', 'z'],           # 位置列名
    'speed_column': 'speed',                  # 速度列名（可选）
    'angle_column': 'angle',                  # 方向角列名（可选）
    'type_column': 'type'                     # 车辆类型列名（可选）
}
```

## 坐标转换

TrafficDataProvider 支持三种坐标转换模式：

1. **none**：不进行转换，直接使用源坐标
2. **offset_scale**：应用偏移和缩放
   ```python
   'coordinate_conversion': {
       'type': 'offset_scale',
       'offset_x': -500.0,  # X 轴偏移
       'offset_y': -500.0,  # Y 轴偏移
       'offset_z': 0.0,     # Z 轴偏移（可选）
       'scale': 1.0         # 统一缩放因子（可选）
   }
   ```
3. **latlon**：将经纬度坐标转换为局部坐标
   ```python
   'coordinate_conversion': {
       'type': 'latlon',
       'ref_lat': 31.2304,  # 参考纬度
       'ref_lon': 121.4737  # 参考经度
   }
   ```

## 使用示例

### 基于 SUMO 的示例

```python
from airfogsim.core.environment import Environment
from airfogsim.dataprovider.traffic import TrafficDataProvider

# 创建仿真环境
env = Environment()

# 配置 TrafficDataProvider
traffic_config = {
    'source': 'sumo',
    'update_interval': 1.0,
    'coordinate_conversion': {
        'type': 'offset_scale',
        'offset_x': -500.0,
        'offset_y': -500.0
    },
    'sumo_config': {
        'host': 'localhost',
        'port': 8813
    }
}

# 创建并注册 TrafficDataProvider
traffic_provider = TrafficDataProvider(env, traffic_config)
env.add_data_provider('traffic', traffic_provider)

# 加载数据并启动事件触发
traffic_provider.load_data()
traffic_provider.start_event_triggering()

# 运行仿真
env.run(until=60)  # 运行 60 秒

# 清理资源
traffic_provider.close()
```

### 基于文件的示例

```python
from airfogsim.core.environment import Environment
from airfogsim.dataprovider.traffic import TrafficDataProvider

# 创建仿真环境
env = Environment()

# 配置 TrafficDataProvider
traffic_config = {
    'source': 'file',
    'coordinate_conversion': {
        'type': 'none'
    },
    'file_config': {
        'path': 'data/vehicle_trajectories.csv',
        'format': 'csv',
        'time_column': 'timestamp',
        'id_column': 'vehicle_id',
        'pos_columns': ['x', 'y', 'z']
    }
}

# 创建并注册 TrafficDataProvider
traffic_provider = TrafficDataProvider(env, traffic_config)
env.add_data_provider('traffic', traffic_provider)

# 加载数据并启动事件触发
traffic_provider.load_data()
traffic_provider.start_event_triggering()

# 运行仿真
env.run(until=60)  # 运行 60 秒
```

## 事件订阅

要接收车辆位置更新，代理或管理器需要订阅 `TrafficUpdate` 事件：

```python
# 在代理类中
self.env.event_registry.subscribe(
    'TrafficDataProvider',
    'TrafficUpdate',
    f"{self.id}_traffic_monitor",
    self._handle_traffic_update
)

def _handle_traffic_update(self, event_data):
    """处理交通更新事件。"""
    vehicle_id = event_data.get('id')
    vehicle_pos = event_data.get('position')
    vehicle_type = event_data.get('type')
    vehicle_speed = event_data.get('speed')
    
    # 处理车辆位置数据...
```

## 事件数据格式

`TrafficUpdate` 事件包含以下数据：

```python
{
    'sim_time': 123.4,            # 仿真时间（秒）
    'id': 'vehicle_1',            # 车辆 ID
    'type': 'car',                # 车辆类型
    'position': (100.0, 200.0, 0.0),  # 位置 (x, y, z)
    'speed': 13.9,                # 速度（米/秒）
    'angle': 90.0                 # 方向角（度，0=北，90=东）
}
```

## 与 SUMO 集成

### 前提条件

1. 安装 SUMO：
   ```bash
   sudo apt-get install sumo sumo-tools sumo-doc  # Ubuntu/Debian
   ```

2. 安装 Python TraCI 接口：
   ```bash
   pip install eclipse-sumo
   ```

### 启动 SUMO

在运行 AirFogSim 之前，需要启动 SUMO 并开启 TraCI 服务器：

```bash
sumo-gui -c your_config.sumocfg --remote-port 8813
```

或者使用 AirFogSim 提供的辅助函数自动启动 SUMO：

```python
from airfogsim.examples.run_traffic_simulation import start_sumo

sumo_process = start_sumo("sumo_configs/simple.sumocfg", port=8813, gui=True)

# 使用完毕后终止 SUMO
sumo_process.terminate()
```

## 与事件驱动架构的集成

TrafficDataProvider 完全集成到 AirFogSim 的事件驱动架构中：

1. **事件触发**：根据配置的更新间隔或文件中的时间戳触发 `TrafficUpdate` 事件
2. **事件订阅**：代理和管理器可以订阅这些事件
3. **回调处理**：提供标准回调方法 `on_traffic_update` 用于处理事件

这种设计使得交通数据可以无缝集成到现有的仿真工作流程中，影响无人机的行为、着陆点的可用性等。

## 最佳实践

1. **选择合适的数据源**：
   - 对于复杂的交通场景和实时交互，使用 SUMO
   - 对于预定义的轨迹和重放，使用文件数据源

2. **坐标转换**：确保车辆坐标与仿真坐标系统匹配

3. **资源管理**：使用完毕后调用 `close()` 方法释放资源

4. **性能考虑**：
   - 对于大规模仿真，考虑使用车辆过滤器减少数据量
   - 调整更新间隔以平衡精度和性能

## 完整示例

请参考以下示例文件：
- `src/airfogsim/examples/demo_traffic_provider.py`：基本使用示例
- `src/airfogsim/examples/run_traffic_simulation.py`：与 SUMO 集成的完整示例