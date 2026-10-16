# 屋顶洪涝救援原型

四架独立 UAV：边缘（Jev）用轻量 `participation_filter` 做加入/不加入提议；中心用更大模型调用 `propose_plan` 提出有界任务图；程序按能力、载荷、当前承诺、依赖和占用计算指派。观察和交付等待真实运动回执；报告与交付回执经原生 ns-3 两跳送达。

训练与过滤实现见独立仓库 [AeroRescue](https://github.com/ZhiweiWei-NAMI/AeroRescue)（安装其包或把 `src` 加入 `PYTHONPATH`，并安装 NumPy）。本仓库只消费已导出的 `participation-filter.json`，不嵌入密钥或权重构建流程。

## 场景文件

| 文件 | 用途 |
| --- | --- |
| `rooftop-flood.yaml` | 默认：边缘与中心均走 live `assistant` profile（无 filter） |
| `rooftop-flood-jev.yaml` | 边缘 `participation_filter` + 中心 live `assistant`（OpenCode 等）；需本地绝对路径指向 filter |
| `rooftop-flood-stub.yaml` | 脚本化 stub 应答 + radio，便于无密钥本地/CI 冒烟 |
| `rooftop-flood-stub-noradio.yaml` | 同上但无 radio，不依赖 ns-3 RPC |

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

无密钥冒烟（stub，不连模型）：

```sh
.venv/bin/aeroagentsim run scenarios/rescue/rooftop-flood-stub-noradio.yaml --out runs/rescue-stub
# 有 ns-3 RPC 时可用 rooftop-flood-stub.yaml
```

## Provider profile（仅环境变量名，无密钥）

场景只写 `provider: {profile: <name>}`。URL、模型名与凭据选择器由算子侧注入，不进场景文件。

| Profile | 环境变量（名称） |
| --- | --- |
| `default` | `AAS_LLM_BASE_URL`（可选）、`AAS_LLM_MODEL`、`AAS_LLM_API_KEY` |
| `live-llm` | `AEROAGENTSIM_LLM_BASE_URL`、`AEROAGENTSIM_LLM_MODEL`、`AEROAGENTSIM_LLM_API_KEY_ENV`（再指向实际密钥环境变量名） |
| 其它命名（如 `assistant`） | `AEROAGENTSIM_PROVIDER_CONFIG`（JSON 文件路径）和/或 `AEROAGENTSIM_PROVIDER_PROFILES`（JSON 字符串）。条目字段：`base_url`、`model`，以及 `api_key_env`（环境变量名）或 `api_key_file`（单行密钥文件路径，二选一） |

OpenCode Go / DeepSeek thinking 路径：平台在 `host == opencode.ai` 时会去掉 `tools[].function.strict`，且请求体不发送 `tool_choice=required|function`（thinking 模式会 HTTP 400）。

## `participation_filter` 如何指向

在场景 `engines.tasks.config.rescue` 下设绝对路径（相对路径按进程 cwd，不推荐）：

```yaml
provider: {profile: assistant}
participation_filter: /absolute/path/to/participation-filter.json
```

- 仅替换**边缘** `propose_roles`；中心仍走 profile 对应的 live API（`propose_plan`）。
- 需可 `import aerorescue.adapter.ParticipationFilter`（见 [AeroRescue](https://github.com/ZhiweiWei-NAMI/AeroRescue)）。
- `rooftop-flood-jev.yaml` 里的路径是作者本机示例，运行前改成你的 filter 绝对路径。
- 未设置时，边缘与中心都走模型 API。

CLI 示例（名称 only）：

```sh
export AEROAGENTSIM_PROVIDER_CONFIG=/absolute/path/to/provider-profiles.json
# provider-profiles.json 内 assistant.api_key_env 写密钥环境变量名，例如 DEEPSEEK_API_KEY
.venv/bin/aeroagentsim run scenarios/rescue/rooftop-flood-jev.yaml --out runs/rescue-jev
```

## 配置与研究入口

- `observation` 可设为 `positive`、`negative` 或 `uncertain`；这是到达观察位后才揭示的作者给定语义真值，不是视觉识别。
- 每机能力、位置、载荷和旧任务在 `vehicles` 中声明。代码不按 U1/U2/U3/U4 分支选赢家。当前运行仅分配空闲机，已有任务和充电/避让控制保留原责任。
- 当前单事件域每阶段每个角色最多一个动作，最多三个动作。中心相位约定：疑似证据只提 inspect+relay（不 deliver）；已收到阳性报告后提 relay+deliver（deliver 在 relay 之后），不再 inspect。交付必须已收到阳性报告并等待中继就绪；中继保留到消息实际送达。每事件只发一次报告和一次交付回执，未实现自动重传。
- `plan_computed` 记录完整 `problem`、过滤前 `candidate_universe` 和结果；局部请求保留当时快照。跨时刻样本不能直接当作同一状态的训练标签。

## 目前已运行的范围

2026-10-14：最终观察位 `[-24.81,-204.65,28]`、中继 `[30,-130,70]`、中心 `[100,20,2]` 的原生无线操作已实际运行。seed 42 下直传在 1 s 超时；第一跳 0.682017 ms 送达，收到后才提交第二跳，第二跳 5.437808 ms 送达。配置使用明确声明的合成 AABB 衰减盒，不是黄浦真实建筑校准，也不是可靠性统计。

同日 **live5**（`rooftop-flood-jev`：边缘 `trained_filter` + 中心 OpenCode `propose_plan`）本地已跑通闭环：ns-3 送达 report 与 delivery_ack，出现 `delivery_completed` / `roles_released`，无 `model_failed`。指挥通道仍是可靠业务事件，现场报告/回执才走 ns-3；洪水是场景语义可视化，不模拟水动力学。模型调用会暂停仿真，决策 wall time 不能用仿真时间代替。未新增或执行完整回归测试套件。

## Research episodes / 研究批量场景

The `flood-rescue` template uses explicit stub model responses, so model mode
needs no API key. Its knobs are `participation=model|filter|all|distance`,
`observation=positive|negative|uncertain`, `radio=on|off`, absolute `deadline_s`
and `fleet_variation=authored|seeded`. `radio_port` selects the native RPC port
(default 19004; host 127.0.0.1). Platform seeds include zero; the ns-3
adapter uses `1 + seed % (2**32 - 1)` to map them deterministically into its
positive uint32 range. Matched policy seeds keep identical native radio seeds.

```sh
aeroagentsim experiment flood-rescue --seeds 5 --vary participation=all
aeroagentsim run-template flood-rescue --set participation=distance --set radio=on --headless
aeroagentsim metrics runs/rescue-stub
```

`seeded` (default) materializes after the experiment runner sets each run seed.
It deterministically jitters each horizontal position by at most ±150 m inside
the shipped city's ±540 m bounds, keeps starts in the authored safe flight
layer (130–180 m), and varies capabilities, payload and SOC. Each vehicle keeps
at least one capability and the uncommitted fleet covers all roles. This is
variation of authored episodes, not a distribution fitted to flood measurements.
`authored` preserves the YAML fleet.

模板使用明确的 stub 应答，无需模型密钥。默认 `seeded` 在运行种子确定后才生成
机队变化；同一种子得到同一场景，不同种子改变位置、能力、载荷和 SOC。
默认 `radio=off` 无需 ns-3，但会如实记录通信失败并在截止时过期，成功率为 0。
`radio=on` 需要上文所述真实 native RPC 服务；并发无线实验需要彼此隔离的服务。
无线服务端口由 `radio_port` 显式指定。平台种子允许 0，ns-3 种子范围为
1 到 2³²−1；原生 ns-3 适配器固定使用 `1 + seed % (2**32 - 1)`，
各策略的同种子仍使用同一无线随机种子。
`filter` 需要安装 AeroRescue，并在模板 `scenario.yaml` 旁放置真实导出的
`participation-filter.json`；不提供演示权重或成功替代路径。

每个阶段（notice、report_received）都有新的局部请求和 bids，`plan_computed`
中的 `now_s` 与该阶段请求一致。`commitment.busy_until_s` 从实时剩余航段和停留
估算，`busy_estimate=route`；无法估算时标记 `unknown` 并使用 JSON 安全的有限
无穷哨兵值，保持不可提前参与。filter/all/distance 不创建本地模型客户端；
过滤器推理耗时写入运行目录 `rescue-filter-timing.jsonl`，不进入确定性日志。

C1 committed fields are `rescue.markers` (inspect/relay/command) and
`rescue.links` (per-hop sending/delivered/failed plus relay holding coverage).
Delivered links remain visible after release. Story/Events lines are bilingual.
One `outcome` records success and elapsed confirmation/delivery times; missing
measurements stay null. Positive success requires a received delivery ack before
the deadline; negative/uncertain success requires the centre to resolve the
received report without delivery. Deadline expiry releases rescue roles.

C2 指标为 `rescue_success`、`rescue_time_to_confirmation_s`、
`rescue_time_to_delivery_s`、`rescue_plans`、`rescue_bids`、`rescue_candidates`、
`rescue_radio_packets`、`rescue_radio_delivered`、`rescue_radio_bytes`、
`rescue_bus_messages`、`rescue_bus_bytes`、`rescue_model_calls`。
候选数统计所有 bid 提议的角色数；无线量来自 ns-3 send/delivery 消息，每跳算一包；
总线字节数为 `rescue.*` 消息 `data_json` 的 UTF-8 字节长度。未收到 outcome 时
成功率保持未测量；无无线消息时发包量为实际的 0。指标由现有日志投影计算，
`aeroagentsim metrics <run>` 同样输出这些指标。
