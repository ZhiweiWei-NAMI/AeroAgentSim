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
