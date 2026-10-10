# AeroAgentSim

AeroAgentSim 是面向科研实验的通用仿真平台：用 AeroGraph 类型、状态和关系描述场景，由一个共享离散事件内核协调时间、外部输入、状态更新与事件链。可替换领域插件负责运动、交通、网络和天气；LangGraph 插件支持模型驱动的智能体决策。控制台提供场景配置、运行控制及时间同步的关系图和三维视图。

默认交通事故示例使用已提交的注册表快照和 OSM 简化城市，不需要访问 AeroGraph 仓库或额外模型包。默认决策是明确标注的脚本回复；真实模型和原生仿真器需要单独配置。

发布版安装并运行：

```bash
python -m pip install -e ./aerokernel -e '.[server]'
(cd frontend && npm ci && npm run build)
aeroagentsim demo traffic-accident
```

浏览器依赖和系统环境见英文安装指南。

## 日常低空实验（2.1）

在 **Home → Choose your scenario** 选择快递配送、医疗配送、电力巡检、农田喷洒、空中出租车、安防巡逻或混合城市日。**Run now** 运行默认配置，**Customize** 调节无人机数量、任务分配算法等参数，**Open in Studio** 编辑真实场景。

简单视图优先显示城市、可读事件和实际 KPI；专家视图补充同步关系图与状态。七个日常模板共用电量储备、禁飞区、间隔和风速规则。在 Studio 修改谓词参数，再在 Inspect 点击事件旁的 **Why?**，查看“读取的状态 → 谓词 → 事件链 → 共享状态变化 → 命令回执”。

```bash
aeroagentsim run-template parcel-delivery --set allocator=auction --headless
aeroagentsim experiment parcel-delivery --vary allocator=nearest,auction,hungarian --seeds 3 --jobs 2
```

也可在场景库点击 **Compare changes · Experiment** 发起匹配随机种子的对比实验。[规则编辑](docs/guides/lowalt-rules.md) · [算法实验](docs/guides/experiments.md)

- [英文文档首页](docs/README.md)
- [安装](docs/getting-started/install.md)与[快速入门](docs/getting-started/quickstart.md)
- [交通事故实验](docs/examples/traffic-accident.md)
- [架构](docs/concepts/architecture.md)与[开发贡献](CONTRIBUTING.md)
