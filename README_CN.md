<a href="https://joss.theoj.org/papers/3bf61975c569326131f0bf169bfe4db9"><img src="https://joss.theoj.org/papers/3bf61975c569326131f0bf169bfe4db9/status.svg"></a>
[![DOI](https://zenodo.org/badge/735258267.svg)](https://doi.org/10.5281/zenodo.15779000)
# AirFogSim：低空车载雾计算协同智能基准测试平台

<div align="center">
<img src="src/airfogsim/docs/img/logo.png" alt="AirFogSim Logo" width="300">
</div>

AirFogSim 是一个基于 SimPy 构建的离散事件仿真框架，专为无人机（UAV）集成的雾计算环境中的协同智能基准测试而设计。它提供了一个综合平台，用于模拟异构空中和地面节点之间的复杂交互，并重点关注真实的通信、计算、能源和移动性建模。

[English Version](README.md)

## 📋 项目概述

AirFogSim 为以下场景提供了一个全面的仿真环境：

  - 在复杂环境中模拟自主代理（如无人机）
  - 研究资源分配和任务卸载策略
  - 评估低空车载雾计算中的协同智能
  - 对不同的工作流和协议进行基准测试
  - 可视化仿真过程并分析结果

该框架采用模块化设计，支持高度定制化的仿真场景，并为研究人员和开发人员提供了直观的可视化界面。

如果您在研究中使用了 AirFogSim，请引用我们的论文：

```bibtex
@misc{wei2024airfogsimlightweightmodularsimulator,
      title={AirFogSim: A Light-Weight and Modular Simulator for UAV-Integrated Vehicular Fog Computing},
      author={Zhiwei Wei and Chenran Huang and Bing Li and Yiting Zhao and Xiang Cheng and Liuqing Yang and Rongqing Zhang},
      year={2024},
      eprint={2409.02518},
      archivePrefix={arXiv},
      primaryClass={cs.NI},
      url={https://arxiv.org/abs/2409.02518},
}
```

## ✨ 核心特性

  - **高性能事件驱动仿真核心：** 优化的事件驱动仿真引擎，关键操作的计算复杂度低于 $O(n \log n)$，能够高效仿真大规模场景。

  - **基于工作流的任务组合框架：** 灵活且模块化的工作流驱动任务模型，明确捕捉任务依赖、资源约束以及异构节点间的协作交互。

  - **符合标准的现实建模：** 基于既定标准的综合模型，包括符合 3GPP 的通信信道模型、经过经验验证的能耗模型以及基于物理的移动模式。

  - **以代理为中心的自主性：** 代理（如无人机）作为具有内部状态的主要行动者，能够根据其状态、分配的工作流和环境感知进行自主决策。

  - **基于组件的能力：** 通过组件封装特定功能（移动、计算、传感）并管理任务执行环境，实现关注点分离。

  - **基于触发器的反应性：** 灵活的机制，用于对各种条件（事件、状态变化、时间）做出反应，驱动工作流状态机转换并实现自动化响应。

  - **托管资源：** 仿真资源（着陆点、CPU、空域、频谱）由专门的管理器类进行管理，处理注册、分配、竞争和动态属性变化。

  - **实时可视化：** 集成的前端界面，支持实时监控和数据分析。

  - **大语言模型（LLM）集成：** 支持通过大语言模型进行任务规划和决策。

## 🏗️ 系统架构

AirFogSim 基于事件驱动的代理基模型（ABM）架构构建，可高效仿真异构代理之间的复杂交互。该平台扩展了 SimPy 离散事件仿真库，为无人机集成的雾计算场景提供了专门的组件。

### 核心组件

- **🤖 代理 (Agents)**: 具有决策能力的自主实体（无人机、地面站）
- **🔧 组件 (Components)**: 代理可使用的模块化能力（移动、计算、传感）
- **📋 任务 (Tasks)**: 代理通过组件执行的具体操作
- **🔄 工作流 (Workflows)**: 协调多个任务的高级目标
- **⚡ 触发器 (Triggers)**: 驱动工作流转换的事件驱动条件
- **📊 资源 (Resources)**: 共享的仿真资源（空域、频谱、着陆点）
- **🎯 管理器 (Managers)**: 资源和系统服务的集中管理

详细架构文档请参阅 [系统架构指南](src/airfogsim/docs/cn/architecture.md)。

### 可视化系统

AirFogSim 包含集成的可视化系统，用于实时监控：

- **📊 仪表盘**: 仿真状态和代理监控
- **🗺️ 无人机跟踪**: 实时位置和轨迹可视化
- **⚙️ 工作流监控**: 配置和执行跟踪
- **📈 数据分析**: 资源使用和性能指标

<div align="center">
  <img src="src/airfogsim/docs/img/状态监控.png" alt="状态监控界面" width="600">
  <p><em>实时无人机监控和状态跟踪</em></p>
</div>

**架构**: React 前端 + FastAPI 后端 + WebSocket 通信

可视化设置请参阅 [安装指南](INSTALL.md#visualization-setup)。

## 🚀 安装指南

### 快速开始

```bash
pip install airfogsim
```

### 基本安装

#### 选项 1：从 PyPI 安装 (推荐)

```bash
pip install airfogsim
```

#### 选项 2：从源码安装

```bash
git clone https://github.com/ZhiweiWei-NAMI/AirFogSim.git
cd AirFogSim
pip install -e .[dev]
```

📋 **详细设置**: 完整的安装指南（包括系统要求、开发环境设置和故障排除）请参阅 [INSTALL.md](INSTALL.md)。

## 📝 使用示例

### 基本仿真示例

```python
from airfogsim.core.environment import Environment
from airfogsim.agent import DroneAgent
from airfogsim.component import MoveToComponent, ChargingComponent
from airfogsim.workflow.inspection import create_inspection_workflow
from airfogsim.helper import check_all_classes, find_compatible_components

# 创建环境
env = Environment()

# 检查系统中的所有类
check_all_classes(env)

# 创建无人机代理
drone = env.create_agent(
    DroneAgent,
    "drone1",
    initial_position=(10, 10, 0),
    initial_battery=100
)

# 查找合适的组件
find_compatible_components(env, drone, ['speed'])

# 添加组件
move_component = MoveToComponent(env, drone)
charging_component = ChargingComponent(env, drone)
drone.add_component(move_component)
drone.add_component(charging_component)

# 创建巡检工作流
waypoints = [
    (10, 10, 100),    # 起飞
    (400, 400, 150),  # 中途点
    (800, 800, 150),  # 目的地
    (800, 800, 0),    # 降落
    (800, 800, 100),  # 返航起飞
    (10, 10, 0)       # 返回起点
]
workflow = create_inspection_workflow(env, drone, waypoints)

# 启动工作流
workflow.start()

# 运行仿真
env.run(until=1000)
```

### 使用类检查器工具

```bash
# 显示所有类
python -m airfogsim.helper.class_finder --all

# 查找支持特定状态的 Agent 类
python -m airfogsim.helper.class_finder --find-agent position,battery_level

# 查找产生特定指标的 Component 类
python -m airfogsim.helper.class_finder --find-component speed,processing_power
```

### 启动可视化界面

```bash
python main_for_visualization.py --backend-port 8002 --frontend-port 3000
```

## 🧪 示例与测试

### 示例

AirFogSim 提供了一系列丰富的示例程序，用于演示各种功能和用例。这些示例位于 `src/airfogsim/examples` 目录中：

  - **基础触发器系统**: `example_trigger_basic.py` - 展示如何使用不同类型的触发器来创建和管理工作流。
  - **工作流图生成**: `example_workflow_diagram.py` - 演示如何将工作流状态机转换为可视化图表。
  - **图像处理工作流**: `example_workflow_image_processing.py` - 展示一个完整的环境图像传感和处理工作流。
  - **多任务合约**: `example_workflow_contract.py` - 演示合约工作流如何管理多个任务。
  - **无人机巡检**: `example_workflow_inspection.py` - 展示无人机巡检路径规划和自动充电。
  - **天气数据集成**: `example_weather_provider.py` - 演示如何将实时天气数据集成到仿真中。
  - **多工作流基准测试**: `example_benchmark_multi_workflow.py` - JOSS 论文中的基准测试示例，包含巡检、物流和充电工作流。

### 运行示例

```bash
# 列出所有可用示例
airfogsim examples

# 运行特定示例
airfogsim examples workflow_diagram trigger_basic

# 直接运行单个示例
cd src/airfogsim/examples
python example_trigger_basic.py
```

### 自动化测试

AirFogSim 包含一个全面的测试套件，以确保可靠性并捕获回归错误：

```bash
# 安装测试依赖
pip install -e .[dev]

# 运行所有测试
pytest tests/ -v

# 运行测试并生成覆盖率报告
pytest tests/ --cov=airfogsim --cov-report=html

# 仅运行快速测试
pytest tests/ -m "not slow"
```

测试套件包括：

  - 核心功能的**单元测试**
  - 组件交互的**集成测试**
  - 验证所有示例均可正确运行的**示例测试**
  - 通过 GitHub Actions 实现的**持续集成**

## 📁 项目结构

```
airfogsim-project/
├── LICENSE                   # 项目许可证
├── INSTALL.md                # 详细安装指南
├── CONTRIBUTING.md           # 贡献指南
├── main_for_visualization.py # 可视化系统启动脚本 (用于本地开发)
├── pyproject.toml            # Python 项目配置文件 (含依赖)
├── README.md                 # 本文档 (项目概览)
├── README_CN.md              # 中文版项目概览
├── requirements.txt          # Python 锁定依赖项 (由 pip-compile 生成)
├── docs/                     # 用户文档 (基于 Sphinx)
│   ├── README.md             # 文档导航中心
│   ├── api/                  # 自动生成的 API 参考
│   └── guides/               # 用户指南和教程
├── src/                      # 后端源代码
│   └── airfogsim/            # 核心仿真框架
│       ├── agent/            # Agent 实现
│       ├── component/        # Component 实现
│       ├── core/             # 核心类与接口
│       ├── docs/             # 技术文档 (面向开发者)
│       │   ├── en/           # 英文技术指南
│       │   ├── cn/           # 中文技术指南
│       │   └── img/          # 文档图片
│       ├── event/            # 事件处理
│       ├── examples/         # 示例代码和教程
│       ├── helper/           # 开发辅助工具
│       ├── manager/          # 各类管理器
│       ├── resource/         # Resource 实现
│       ├── task/             # Task 实现
│       ├── visualization/    # 可视化相关 (FastAPI 应用)
│       └── workflow/         # Workflow 实现
└── ... (其他配置文件、测试文件等)
```

## 📚 文档

### 📖 用户文档
- **[快速入门](docs/getting_started.html)** - 安装和首次仿真
- **[用户指南](docs/user_guide.html)** - 综合使用指南
- **[API 参考](docs/api/index.html)** - 完整的 API 文档
- **[示例](docs/examples.html)** - 即用示例

### 🔧 开发者文档
- **[系统架构](src/airfogsim/docs/cn/architecture.md)** - 详细系统设计
- **[开发指南](src/airfogsim/docs/cn/)** - 技术文档
- **[辅助工具](src/airfogsim/helper/README.md)** - 开发工具

### 🌍 English Documentation
- **[System Architecture](src/airfogsim/docs/en/architecture.md)** - Detailed system design
- **[Development Guides](src/airfogsim/docs/en/)** - Technical documentation

**📋 文档中心**: 完整导航请参阅 [docs/README.md](docs/README.md)

## 🤝 贡献

我们欢迎各种形式的贡献！请参阅我们的[贡献指南](CONTRIBUTING.md)以获取详细信息：

- 如何报告错误和请求功能
- 开发环境设置和编码标准
- 测试指南和最佳实践
- Pull Request 流程
- 社区准则

### 贡献者快速入门

```bash
# Fork 并克隆仓库
git clone https://github.com/YOUR_USERNAME/AirFogSim.git
cd AirFogSim

# 设置开发环境
pip install -e .[dev]

# 在创建新类之前检查现有类
python -m airfogsim.helper.class_finder --all

# 运行测试
pytest tests/ -v
```

有关详细的贡献指南，请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 📄 许可证

本项目采用 Apache 2.0 许可证 - 详情请参阅 [LICENSE](LICENSE) 文件。

---

**AirFogSim** - 强大的低空车载雾计算研究仿真工具