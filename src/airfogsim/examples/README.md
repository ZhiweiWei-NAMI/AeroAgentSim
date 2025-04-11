# AirFogSim 示例程序

本目录包含多个AirFogSim框架的示例程序，展示了不同功能的使用方法。以下是各示例的简要介绍：

## 基础功能示例

- **trigger_example.py**: 展示如何使用AirFogSim的触发器系统创建和管理工作流，包括基于事件、状态和时间的触发器。
- **workflow_diagram_demo.py**: 演示如何将工作流状态机转换为可视化图表（PlantUML和Mermaid格式）。
- **test_workflow_diagram.py**: 提供工作流图表生成的另一个示例，直接输出到控制台。

## 传感器和数据处理

- **demo_openweathermap_adapter.py**: 演示如何使用OpenWeatherMap API获取实时天气数据并转换为仿真系统格式。
- **demo_weather_provider_api.py**: 在仿真环境中集成WeatherDataProvider，并订阅天气变化事件。
- **run_image_processing.py**: 展示环境图像感知处理工作流，包括感知和数据处理阶段。

## 无人机任务和合约

- **run_multi_task_contract.py**: 演示如何创建和执行包含多个任务的合约工作流。
- **run_drone_inspection.py**: 展示无人机巡检路径规划和自动充电工作流。
- **run_logistics_simulation.py**: 物流工作流示例，模拟快递站和无人机配送过程。

## 交通仿真集成

- **run_traffic_simulation.py**: 演示SUMO交通仿真器与AirFogSim集成（需要SUMO安装和配置）。

## 运行要求

部分示例需要额外设置：

1. 天气相关示例需要设置OpenWeatherMap API密钥：
   ```bash
   export OPENWEATHERMAP_API_KEY='your_api_key'
   ```

2. 交通仿真示例需要安装SUMO并配置相关文件。

## 一键测试

您可以使用`test_examples.py`脚本快速测试多个示例：

```bash
python test_examples.py          # 测试所有示例
python test_examples.py --list   # 列出所有可用测试
python test_examples.py --run workflow_diagram_demo trigger_example  # 测试指定示例
```

## 单元测试

如果您想在自己的代码库中编写AirFogSim组件的测试，建议使用Python的`unittest`或`pytest`框架。例如：

```python
import unittest
from airfogsim.core.environment import Environment
from airfogsim.agent.drone import DroneAgent

class TestDroneAgent(unittest.TestCase):
    def setUp(self):
        self.env = Environment()
        self.drone = DroneAgent(self.env, "test_drone", {"position": (0, 0, 0)})
        
    def test_drone_movement(self):
        # 测试代码...
        self.assertEqual(self.drone.get_state("position"), (10, 10, 10))
        
if __name__ == "__main__":
    unittest.main()