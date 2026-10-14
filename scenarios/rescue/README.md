# 屋顶洪涝救援原型

四架独立 UAV harness 提议条件性角色，中心模型提出任务图，程序按能力、载荷、当前承诺、依赖和占用计算指派。观察和交付等待真实运动回执；报告与交付回执经原生 ns-3 两跳送达。

## 启动

在仓库根目录执行。先按 `containers/ns3/Dockerfile` 的官方 ns-3.48 recipe 构建 native provider；以下使用已构建的本地二进制，不需要 Docker。将路径换成实际构建目录：

```sh
export NS3_ROOT=/absolute/path/to/ns-3.48
export AAS_NS3_PROVIDER="$NS3_ROOT/build/scratch/ns3.48-aero-ns3-provider-optimized"
export LD_LIBRARY_PATH="$NS3_ROOT/build/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
AAS_PORT=19004 PYTHONPATH="$PWD/containers/ns3${PYTHONPATH:+:$PYTHONPATH}" \
  .venv/bin/python -m service.rpc
```

保持此终端运行。ns-3 与仿真服务必须处于同一网络环境；单个 RPC 服务同时只承载一个仿真世界。当前云工作区已有 `aeroagentsim-native/start-ns3.sh` 封装上述路径。

另一个终端启动现有 Web 服务；已有服务时复用它，不要重复启动：

```sh
.venv/bin/aeroagentsim serve --out runs/smoke --scenario-root "$PWD" \
  --frontend frontend/dist --host 127.0.0.1 --port 8002
```

在该服务的模型连接界面由用户完成 OpenAI-compatible 连接。场景使用现有 `assistant` profile，由服务的 ConnectionStore 注入；模型名、地址和密钥不写入场景。配置完成后创建并运行：

```sh
curl -sS http://127.0.0.1:8002/v1/runs \
  -H 'Content-Type: application/json' \
  -d '{"scenario_path":"scenarios/rescue/rooftop-flood.yaml","autostart":true}'
```

如服务器的 scenario root 是 `scenarios/`，请求路径改为 `rescue/rooftop-flood.yaml`。在 UI 打开返回的 run，查看洪水、淹路、R7、逐机角色和事件。运行记录保存在所选 run root 下。

## 配置与研究入口

- `observation` 可设为 `positive`、`negative` 或 `uncertain`；这是到达观察位后才揭示的作者给定语义真值，不是视觉识别。
- 每机能力、位置、载荷和旧任务在 `vehicles` 中声明。代码不按 U1/U2/U3/U4 分支选赢家。当前运行仅分配空闲机，已有任务和充电/避让控制保留原责任。
- 当前单事件域每阶段每个角色最多一个动作，最多三个动作。交付必须已收到阳性报告并等待中继就绪；中继保留到消息实际送达。每事件只发一次报告和一次交付回执，未实现自动重传。
- 默认每机和中心使用真实 API。可选 `participation_filter: /absolute/path/participation-filter.json` 仅替换局部参与建议；需安装独立 AeroRescue 包（或将其 `src` 加入 `PYTHONPATH`）及 NumPy，中心仍调用 API。
- `plan_computed` 记录完整 `problem`、过滤前 `candidate_universe` 和结果；局部请求保留当时快照。跨时刻样本不能直接当作同一状态的训练标签。

## 目前已运行的范围

2026-10-14：最终观察位 `[-24.81,-204.65,28]`、中继 `[30,-130,70]`、中心 `[100,20,2]` 的原生无线操作已实际运行。seed 42 下直传在 1 s 超时；第一跳 0.682017 ms 送达，收到后才提交第二跳，第二跳 5.437808 ms 送达。配置使用明确声明的合成 AABB 衰减盒，不是黄浦真实建筑校准，也不是可靠性统计。

场景已实际加载；完整 API→规划→运动→无线→交付闭环尚未运行，等待模型连接。指挥通道仍是可靠业务事件，现场报告/回执才走 ns-3；洪水是场景语义可视化，不模拟水动力学。模型调用会暂停仿真，决策 wall time 不能用仿真时间代替。未新增或执行测试套件。
