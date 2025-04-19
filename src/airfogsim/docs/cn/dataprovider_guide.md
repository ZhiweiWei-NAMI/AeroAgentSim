## AirFogSim 数据提供者框架文档

本文档解释了 AirFogSim 中的数据提供者框架，重点介绍如何将外部数据源集成到仿真环境中，以创建更加真实和动态的场景。

### 1. 概述

AirFogSim 中的数据提供者框架使外部数据源（如天气、交通和事故）能够集成到仿真环境中。这允许创建更加真实和动态的场景，代理可以对不断变化的环境条件做出反应。

*   **`DataProvider`:** 外部数据提供者的抽象基类。它定义了在仿真环境中加载数据和触发事件的通用接口。

*   **`DataIntegration`:** 数据集成模块的抽象基类。它充当 DataProviders 和仿真之间的桥梁，处理注册、配置和事件订阅逻辑。

*   **具体实现:** AirFogSim 包含几个 DataProviders 的具体实现，如 `WeatherDataProvider`、`TrafficDataProvider` 和 `AccidentDataProvider`。

### 2. 核心概念

#### 2.1 `DataProvider` 抽象基类

`DataProvider` 类为所有数据提供者定义了通用接口：

```python
class DataProvider(ABC):
    def __init__(self, env, config=None):
        self.env = env
        self.config = config if config is not None else {}
        
    @abstractmethod
    def load_data(self):
        """加载此提供者所需的外部数据。"""
        pass
        
    @abstractmethod
    def start_event_triggering(self):
        """启动基于加载数据触发事件的进程。"""
        pass
```

* **初始化:** 接收一个仿真环境和一个可选的配置字典。
* **`load_data()`:** 抽象方法，从文件、数据库或 API 加载外部数据。
* **`start_event_triggering()`:** 抽象方法，启动基于加载数据触发事件的进程。

#### 2.2 `DataIntegration` 抽象基类

`DataIntegration` 类充当 DataProviders 和仿真之间的桥梁：

```python
class DataIntegration(ABC):
    def __init__(self, env, config=None):
        self.env = env
        self.config = config or {}
        self.default_config = {}
        self.config = {**self.default_config, **self.config}
        self._initialize_provider()
        self._register_event_listeners()
        
    @abstractmethod
    def _initialize_provider(self):
        """初始化此集成使用的数据提供者。"""
        pass
        
    @abstractmethod
    def _register_event_listeners(self):
        """为数据提供者注册事件监听器。"""
        pass
```

* **初始化:** 接收一个仿真环境和一个可选的配置字典。
* **`_initialize_provider()`:** 抽象方法，初始化此集成使用的数据提供者。
* **`_register_event_listeners()`:** 抽象方法，为数据提供者注册事件监听器。

### 3. 具体实现

#### 3.1 天气数据提供者

`WeatherDataProvider` 提供天气数据并在仿真中触发与天气相关的事件：

```python
class WeatherDataProvider(DataProvider):
    EVENT_WEATHER_CHANGED = 'WeatherChanged'
    
    def __init__(self, env, config=None):
        super().__init__(env, config)
        self.api_key = self.config.get('api_key')
        self.location = self.config.get('location')
        self.update_interval = self.config.get('update_interval', 3600)
        self.api_refresh_interval = self.config.get('api_refresh_interval', 3600)
        # ...
        
    def load_data(self):
        """从 OpenWeatherMap API 加载天气数据。"""
        # ...
        
    def start_event_triggering(self):
        """启动触发天气事件的进程。"""
        # ...
```

* **配置:**
  * `api_key`: OpenWeatherMap API 密钥。
  * `location`: 包含 `lat` 和 `lon` 键的字典。
  * `update_interval`: 提供者在仿真时间中检查下一个事件的频率（默认：3600 秒）。
  * `api_refresh_interval`: 在真实秒数中从 API 重新获取数据的频率（默认：3600 秒）。

* **事件:**
  * `WeatherChanged`: 当天气条件变化时触发。

#### 3.2 交通数据提供者

`TrafficDataProvider` 提供地面车辆交通数据并在仿真中触发与交通相关的事件：

```python
class TrafficDataProvider(DataProvider):
    def __init__(self, env, config=None):
        super().__init__(env, config)
        self.source = self.config.get('source')
        self.update_interval = float(env.visual_interval)
        # ...
        
    def load_data(self):
        """从配置的源加载交通数据。"""
        # ...
        
    def start_event_triggering(self):
        """启动触发交通事件的进程。"""
        # ...
        
    def get_all_vehicle_states(self):
        """获取所有车辆的完整状态。"""
        # ...
```

* **配置:**
  * `source`: 数据源类型，可以是 'sumo' 或 'file'。
  * `update_interval`: 更新间隔（秒）（默认：环境的 visual_interval）。
  * `sumo_config`: SUMO 数据源的配置（当 source='sumo' 时需要）。
  * `file_config`: 文件数据源的配置（当 source='file' 时需要）。
  * `center_coordinates`: 坐标转换的中心坐标（可选，但对 SUMO 推荐）。

* **事件:**
  * `TrafficUpdate`: 当车辆位置更新时触发。

#### 3.3 天气集成

`WeatherIntegration` 类将天气数据集成到仿真中：

```python
class WeatherIntegration(DataIntegration):
    def __init__(self, env, config=None):
        self.default_config = {
            'location': {'lat': 31.2304, 'lon': 121.4737},  # 上海
            'api_refresh_interval': 1800,  # 30 分钟
            'simulation_start_time': None,
            'time_scale': 1.0,
            'use_mock_data': True,
        }
        # ...
        super().__init__(env, self.default_config)
        
    def _initialize_provider(self):
        """初始化天气数据提供者。"""
        # ...
        
    def _register_event_listeners(self):
        """为天气数据提供者注册事件监听器。"""
        # ...
```

* **配置:**
  * `location`: 包含 `lat` 和 `lon` 键的字典。
  * `api_refresh_interval`: 在真实秒数中从 API 重新获取数据的频率。
  * `simulation_start_time`: 仿真开始时间（真实时间）。
  * `time_scale`: 时间比例（仿真时间 / 真实时间）。
  * `use_mock_data`: 是否使用模拟数据而不是真实 API 数据。

### 4. 使用示例

#### 4.1 使用天气数据提供者

```python
from airfogsim.core.environment import Environment
from airfogsim.dataprovider.weather import WeatherDataProvider

# 创建仿真环境
env = Environment()

# 配置 WeatherDataProvider
weather_config = {
    'api_key': 'your_openweathermap_api_key',
    'location': {'lat': 40.7128, 'lon': -74.0060},  # 纽约市
    'api_refresh_interval': 1800  # 每 30 分钟刷新一次（真实时间）
}

# 创建并注册 WeatherDataProvider
weather_provider = WeatherDataProvider(env, config=weather_config)

# 订阅天气事件
def handle_weather_event(subscriber_id, event_data):
    sim_time = event_data.get('sim_timestamp', 'N/A')
    severity = event_data.get('severity', 'N/A')
    condition = event_data.get('condition', 'N/A')
    temp = event_data.get('temperature', 'N/A')
    print(f"天气在 {sim_time} 变化: {condition}, {temp}°C, 严重程度: {severity}")

env.event_registry.subscribe(
    source_id=WeatherDataProvider.__name__,
    listener_id="WeatherListener",
    event_name=WeatherDataProvider.EVENT_WEATHER_CHANGED,
    callback=handle_weather_event
)

# 启动事件触发
weather_provider.start_event_triggering()

# 运行仿真
env.run(until=3600)  # 运行 1 小时
```

#### 4.2 使用 SUMO 的交通数据提供者

```python
from airfogsim.core.environment import Environment
from airfogsim.dataprovider.traffic import TrafficDataProvider
from airfogsim.examples.run_traffic_simulation import start_sumo

# 启动 SUMO
sumo_process = start_sumo("sumo_configs/simple.sumocfg", port=8813, gui=True)

# 创建仿真环境
env = Environment()

# 配置 TrafficDataProvider
traffic_config = {
    'source': 'sumo',
    'update_interval': 1.0,
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
sumo_process.terminate()
```

#### 4.3 使用文件源的交通数据提供者

```python
from airfogsim.core.environment import Environment
from airfogsim.dataprovider.traffic import TrafficDataProvider

# 创建仿真环境
env = Environment()

# 配置 TrafficDataProvider
traffic_config = {
    'source': 'file',
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

#### 4.4 使用天气集成

```python
from airfogsim.core.environment import Environment
from airfogsim.dataprovider.weather_integration import WeatherIntegration

# 创建仿真环境
env = Environment()

# 配置并创建 WeatherIntegration
weather_config = {
    'location': {'lat': 31.2304, 'lon': 121.4737},  # 上海
    'use_mock_data': True  # 使用模拟数据而不是真实 API 数据
}
weather_integration = WeatherIntegration(env, weather_config)

# 运行仿真
env.run(until=3600)  # 运行 1 小时
```

### 5. 创建自定义数据提供者

要创建自定义数据提供者，您需要：

1. **继承 `DataProvider`:**
   ```python
   from airfogsim.core.dataprovider import DataProvider
   
   class CustomDataProvider(DataProvider):
       EVENT_CUSTOM_UPDATE = 'CustomUpdate'
       
       def __init__(self, env, config=None):
           super().__init__(env, config)
           # 初始化您的数据提供者
           
       def load_data(self):
           # 从外部源加载数据
           
       def start_event_triggering(self):
           # 启动触发事件的进程
   ```

2. **实现所需的方法:**
   * `load_data()`: 从外部源加载数据。
   * `start_event_triggering()`: 启动触发事件的进程。

3. **将事件名称定义为类常量:**
   * `EVENT_CUSTOM_UPDATE = 'CustomUpdate'`

4. **使用环境的事件注册表触发事件:**
   ```python
   self.env.event_registry.trigger_event(
       source_id=self.__class__.__name__,
       event_name=self.EVENT_CUSTOM_UPDATE,
       event_data={
           'sim_timestamp': self.env.now,
           'custom_data': custom_data
       }
   )
   ```

### 6. 最佳实践

1. **使用环境的事件注册表进行事件触发和订阅:**
   * 使用 `env.event_registry.trigger_event()` 触发事件。
   * 使用 `env.event_registry.subscribe()` 订阅事件。

2. **将数据提供者注册到环境:**
   * 使用 `env.add_data_provider(key, provider)` 注册数据提供者。
   * 使用 `env.get_data_provider(key)` 访问已注册的数据提供者。

3. **安全处理 API 密钥和敏感数据:**
   * 将 API 密钥存储在环境变量中。
   * 在日志和调试输出中使用掩码密钥。

4. **实现适当的清理:**
   * 实现 `close()` 方法来清理资源。
   * 在仿真结束时调用 `close()`。

5. **使用模拟数据进行测试和开发:**
   * 实现模拟数据提供者用于测试和开发。
   * 使用 `use_mock_data` 配置选项在真实数据和模拟数据之间切换。

### 7. 结论

AirFogSim 中的数据提供者框架使外部数据源能够集成到仿真环境中，从而创建更加真实和动态的场景。通过使用提供的数据提供者或创建自定义数据提供者，您可以使用真实世界的数据增强您的仿真，并创建更加复杂和真实的场景。
