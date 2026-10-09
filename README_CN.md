# AeroAgentSim

AeroAgentSim 是面向科研实验的通用仿真平台：用 AeroGraph 类型、状态和关系描述场景，由一个共享离散事件内核协调时间、外部输入、状态更新与事件链。可替换领域插件负责运动、交通、网络和天气；LangGraph 插件支持模型驱动的智能体决策。控制台提供场景配置、运行控制及时间同步的关系图和三维视图。

默认交通事故示例使用已提交的注册表快照和 OSM 简化城市，不需要访问 AeroGraph 仓库或额外模型包。默认决策是明确标注的脚本回复；真实模型和原生仿真器需要单独配置。

发布版安装并运行：

```bash
python -m pip install -e ./aerokernel -e '.[server]'
(cd frontend && npm ci && npm run build)
aeroagentsim demo traffic-accident
```

当前开发工作区若使用相邻内核目录，将 `./aerokernel` 改为 `../aerokernel`。浏览器依赖和系统环境见英文安装指南。

- [英文文档首页](docs/README.md)
- [安装](docs/getting-started/install.md)与[快速入门](docs/getting-started/quickstart.md)
- [交通事故实验](docs/examples/traffic-accident.md)
- [架构](docs/concepts/architecture.md)与[开发贡献](CONTRIBUTING.md)
