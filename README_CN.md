# AeroAgentSim

AeroAgentSim 是通用、本体驱动的多引擎仿真平台，以独立的 **aerokernel**
包提供执行语义。实体类型、状态、关系、谓词和事件由注册表描述；DES 工作流、
固定步长模型、PX4/Gazebo、SUMO、ns-3 等锁步外部模拟器可共同运行，也可选择
实时节奏。航空器、道路车辆、无线网络与业务记录属于场景；内核没有无人机专用假设。

AeroAgentSim 是最终平台，AeroBench 是能力迁移来源。旧 SimPy 代码仍保留，
通过 `legacy` extra 使用并已标记弃用。当前尚不能宣布 AeroBench 全部退役，
以[能力清单](docs/platform/CAPABILITIES.md)中的逐项证据为准。

```text
AeroGraph 类型 / 字段 / 关系 / 谓词定义（只读）
                    ↓ 编译与固定 registry snapshot
场景 YAML → Simulation / RunSession → 独立 aerokernel
                    ├─ kinematic / workflow / records / predicates
                    ├─ PX4+Gazebo / SUMO / ns-3 适配器
                    ├─ logistics / inspection 领域包
                    └─ decision：授权观测 → 有类型命令
                                  ↓ 原子提交 / action receipts
                           journal / run storage
                                  ↓ 无引擎 replay / committed feed
                       CLI / HTTP+SSE / Three.js viewer / Studio
```

字段写入者、生命周期控制者和时钟映射均由场景明确绑定。查看器刷新不改变物理状态
或仿真时间；缺失观测保持缺失，模拟器与模型错误保留真实失败。

## 快速开始

使用 Python 3.10+（本轮测试为 3.11），并在同级准备兼容的 `../aerokernel`：

```bash
python3.11 -m venv --symlinks .venv
source .venv/bin/activate
pip install -e ../aerokernel -e '.[server]'
aeroagentsim run scenarios/p1-slice.yaml --out runs
aeroagentsim replay runs/<run输出的目录名>
aeroagentsim serve --help
```

依赖与匹配的 AeroGraph 源码已就绪时，22 秒 kinematic 场景在实测主机上可在两分钟内
运行完成，本轮实测 8.02 秒。下载和安装时间另计。场景目前固定
`/mnt/data2/weizhiwei/AeroGraph` 与谓词源码摘要；其他目录布局需要修改自己的场景副本，
指向真实路径。[INSTALL.md](INSTALL.md)说明 snapshot 方式与不依赖本地 AeroGraph
源码的 logistics 示例，不用演示注册表替代真实来源。

```python
from aeroagentsim import Simulation
from aeroagentsim.scenario import load_scenario

sim = Simulation(load_scenario("scenarios/p1-slice.yaml"))
try:
    sim.start()
    view = sim.run_until(1_000_000_000)
    print(view.instant.ns)
finally:
    sim.close()
```

## 外部模拟器与 LLM

PX4/Gazebo、SUMO、ns-3 分别在容器中运行。示例使用本地主机实测镜像
`aeroagentsim/px4-gazebo:dev-p2b`、`aeroagentsim/sumo:dev-p3a-5`、
`aeroagentsim/ns3:dev-p4b`；这不意味着镜像已发布到公共仓库。
构建与运行条件见 [PX4](containers/px4-gazebo/README.md)、
[SUMO](containers/sumo/README.md)、[ns-3](containers/ns3/README.md)。

```bash
python -m aeroagentsim.adapters.runner scenarios/adapters/px4-flight.yaml \
  --containers --flight --journal /tmp/flight-new.jsonl
python -m aeroagentsim.adapters.runner scenarios/adapters/sumo-grid.yaml \
  --containers --journal /tmp/traffic-new.jsonl
python -m aeroagentsim.adapters.runner scenarios/adapters/coupled.yaml \
  --containers --journal /tmp/coupled-new.jsonl
```

journal 路径必须是新文件。分发包已注册三种适配器入口；普通 `Simulation` 可发现
工厂，adapter runner 另负责容器生命周期和飞行动作序列。
[适配器说明](docs/platform/adapters.md)明确原生结果、时间、lag 与 freshness。
原生适配器尚不支持取消，耦合示例尚未建模 UAV 与道路车辆接触。

LLM 通过 stdlib HTTP provider 与有类型、受授权范围限制的工具进行决策。
修改 `scenarios/agents/llm-dispatch.yaml` 的副本以配置真实 endpoint/model 后使用
`aeroagentsim run`。原示例连接本地 8788 端口。凭据由环境变量提供；内核不导入
模型 SDK。离线决策期间仿真时间不推进，replay 不调用模型。
[Agent 文档](docs/platform/agents.md)包含真实会话和超时记录。

## 查看器与 Studio

```bash
cd frontend
npm ci
npm run build
cd ..
aeroagentsim serve --out runs --scenario-root . --frontend frontend/dist
```

打开 `http://127.0.0.1:8002/runs`，查看 live/replay、任意实体、时间戳事实、关系、
命令和回执。Three.js 对显示做插值，检查器保留原始记录。决策页面为
`/agents/<run-id>?mode=replay`；`npm run dev` 提供的 `/viewer-demo` 明确是人工编写的示例。
城市资产需要可核验的 source/mesh pack。

P7b 正在集成描述符驱动的 Studio/authoring。保留的旧 Workflow Studio 仍使用旧版
`/api` 后端。可选服务见 [Studio](docs/platform/studio.md)，启动 `serve` 时设置
`AEROAGENTSIM_STUDIO_ROOT` 与 `AEROAGENTSIM_AEROGRAPH_ROOT`；P7b 验证仍在进行。
本轮不宣称完整 AeroBench City Studio、OSM 导入与地形/道路/建筑编辑
已经完成；也不宣称已达到 60 Hz 城市场景验收。

## 实测性能

| Engine / workload | Simulation | Wall time | RTF | Evidence / limits |
| --- | ---: | ---: | ---: | --- |
| Kinematic + workflow P1 (5 movers, 10 orders) | 22 s | 8.02 s | 2.74 | Fresh P8 clean install; compile/run/index/close, flush WAL; replay complete. |
| PX4/Gazebo arm → takeoff → goto → land | 27.2 s | 6.42 s | 4.24 | Component-job real container run; startup 13.77 s excluded; replay passed. |
| SUMO, 50 vehicles | 60 s | 24.47 s | 2.45 | Component-job real container run; startup 2.22 s excluded; replay passed. |
| PX4 + SUMO + ns-3, 29 packet deliveries | 30 s | 19.22 s | 1.56 | Component-job real container run; startup 16.99 s excluded; replay passed. |
| ns-3 standalone, 5 nodes / 2,400 datagrams | 61 s | 4.18 s | 14.58 | Native radio run: 430 deliveries, 1,970 application timeouts. |

RTF = 仿真秒数 / 墙钟秒数。原生适配器测量包括 reset 后协调与 journal 成本，
不含启动；本轮 CLI 测量包含编译、bootstrap、index 和 close。不同负载与精度不能
作为直接性能排名，也不能推断大规模舰队性能。数据来自
[adapter measurements](tests/adapters/measurements.json)、
[ns-3 measurements](containers/ns3/verification/metrics.json) 和
[P8 验证记录](docs/platform/CAPABILITIES.md#p8-verification)。
另有 [P1 1,000 实体测量](tests/platform/measurements.json)。

## AeroGraph 与当前边界

编译器读取七类目录中的类型、字段、关系与语义信息；调研源码包含 970 种实体类型。
它固定选定切片的继承、schema、单位/坐标系、review disposition 和 provenance，
不运行 AeroGraph 构建器，也不把概念 ownership 自动变成运行时写入权限。
[source compiler](docs/platform/aerograph-compiler.md)说明 admission 策略。
`scenarios/p1-spectrum.yaml` 展示无位置字段的频谱与约束实体。
当前 threshold 是受限比较 AST，任意谓词解释器尚待实现。

P1、原生适配器、P5 领域包、P6 决策已有真实交付证据；P5-F 与 P7b 仍在并行整合。
运动/能耗/巡检几何是明确建模的参数，尚不能称为经过现实标定的物理与传感器模型。
ns-3 当前采用共享信道的单无线接口 ad-hoc 模型；部分 MAVSDK 缓存遥测缺少源时间，
会明确记录。跨模拟器接触、长时间 bounded history、原生取消与完整城市编辑均有限制。
浏览器软件渲染检查不证明硬件 GPU 帧率。

默认依赖仅 `aerokernel` 与 `PyYAML`；可选项为 `server`、`legacy`、`dev`、`docs`。
旧 `Environment` 的迁移与可运行前后对照见
[MIGRATION-v1.md](docs/platform/MIGRATION-v1.md)。
[安装指南](INSTALL.md)、[文档索引](docs/README.md)、
[整合计划](docs/platform/PLAN.md)与能力清单说明完整状态。

## 引用

保留 AirFogSim 论文作为本项目科研来源；该论文描述历史包，不能视为新平台全部能力的
验证。[JOSS 论文](https://joss.theoj.org/papers/10.21105/joss.08267)。

```bibtex
@article{Wei2025,
  doi = {10.21105/joss.08267},
  url = {https://doi.org/10.21105/joss.08267},
  year = {2025},
  publisher = {The Open Journal},
  volume = {10},
  number = {111},
  pages = {8267},
  author = {Wei, Zhiwei and Li, Bing and Zhang, Rongqing},
  title = {AirFogSim: A Python Package for Benchmarking Collaborative Intelligence in Low-Altitude Vehicular Fog Computing},
  journal = {Journal of Open Source Software}
}
```
