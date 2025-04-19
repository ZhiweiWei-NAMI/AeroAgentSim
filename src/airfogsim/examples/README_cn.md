# AirFogSim 示例程序

本目录包含多个AirFogSim框架的示例程序，展示了不同功能的使用方法。以下是各示例的简要介绍：

## 基础功能示例

- **example_trigger_basic.py**: 展示如何使用AirFogSim的触发器系统创建和管理工作流，包括基于事件、状态和时间的触发器。
- **example_workflow_diagram.py**: 演示如何将工作流状态机转换为可视化图表（PlantUML和Mermaid格式）。
- **test_workflow_diagram.py**: 提供工作流图表生成的另一个示例，直接输出到控制台。

## 传感器和数据处理

- **example_weather_openweathermap.py**: 演示如何使用OpenWeatherMap API获取实时天气数据并转换为仿真系统格式。
- **example_weather_provider.py**: 在仿真环境中集成WeatherDataProvider，并订阅天气变化事件。
- **example_workflow_image_processing.py**: 展示环境图像感知处理工作流，包括感知和数据处理阶段。

## 无人机任务和合约

- **example_workflow_contract.py**: 演示如何创建和执行包含多个任务的合约工作流。
- **example_workflow_inspection.py**: 展示无人机巡检路径规划和自动充电工作流。
- **example_workflow_logistics.py**: 物流工作流示例，模拟快递站和无人机配送过程。
- **example_task_priority.py**: 演示任务优先级和抢占机制，展示不同优先级任务的调度和抢占过程。
- **example_task_duplicate_check.py**: 演示任务重复检查机制。
- **example_task_queue_sort.py**: 演示任务队列排序机制。
- **example_workflow_priority.py**: 演示工作流优先级机制。

## 交通仿真集成

- **example_simulation_traffic.py**: 演示SUMO交通仿真器与AirFogSim集成（需要SUMO安装和配置）。

## 基准测试示例

- **example_benchmark_multi_workflow.py**: JOSS论文多工作流基准测试示例，包含巡检、物流和充电工作流的综合场景。

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
python test_examples.py --run example_workflow_diagram example_trigger_basic  # 测试指定示例
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