<!-- guide-language: en-US -->

# Air taxi service

A beginner template: small passenger aircraft fly between three vertiports,
share limited landing pads, and separate from each other when their paths
come close.

## What happens

- Passenger tasks run between `home`, `east-port` and `north-port`
  (eight departures spread over the 300 s horizon).
- Aircraft start close together, so the airspace engine detects separation
  conflicts and the `altitude_layers` conflict-resolution algorithm assigns
  different cruise altitudes; `hold_and_go` holds on the ground instead.
- When a landing pad is occupied the aircraft waits (`lowalt.mobility.pad_wait`).
- Two corridor geofence zones shape the east–west and north flows.

## Knobs

| Knob | Meaning | Default |
| --- | --- | --- |
| `fleet_size` | Number of aircraft (1–12) | 3 |
| `wind_speed` | Base wind speed in m/s (0–15) | 2.0 |
| `conflict_resolver` | `altitude_layers` or `hold_and_go` | altitude_layers |

## KPIs to watch

`tasks_completed`, `mean_task_latency_s`, `energy_used_kj`, `weather_holds`,
`separation_conflicts`.

## Run it (existing CLI)

```bash
aeroagentsim run scenarios/templates/air-taxi/scenario.yaml
aeroagentsim metrics runs/<run-directory>
aeroagentsim replay runs/<run-directory>
aeroagentsim serve   # browser console for the same scenario
```

The airspace engine is the only writer of separation decisions; motion
engines execute the authorized commands on the same shared clock.

## Huangpu city placement

Coordinates are derived from the committed lite-city building outlines, roof
heights and roads by `tools/templates/place.py`. Roof pads use their actual roof
height; inter-site flights depart/arrive vertically and cruise above the 105 m
roof ceiling. Names and business metadata are pinned with each run. See
[the shared placement guide](../../../docs/examples/city-day.md#huangpu-placements-la3).

## Operator story

Choose **interactive** Run mode, Apply controls to draft, Validate, Save version, Initialize, then Start. Select east-port and inject close-vertiport. Flights using it divert to north-port, with real route braking and replanning. Follow closure, dispatch and flight completion in Why?. Events require a live run; receipt acceptance is followed by recorded domain effects.

<!-- guide-language: zh-CN -->

# 空中出租车服务

三架小型载人飞行器在三个起降站之间飞行，共享有限的降落平台，并在航路接近时保持间隔。

## 场景过程

- 乘客任务往返于 `home`、`east-port` 和 `north-port`；300 秒内安排八次出发。
- 飞行器初始距离较近，空域引擎检测间隔冲突。`altitude_layers` 分配不同巡航高度，`hold_and_go` 则让飞行器在地面等待。
- 降落平台被占用时，飞行器等待并记录 `lowalt.mobility.pad_wait`。
- 两个走廊围栏区域约束东西向和北向流量。

## 控制项

| 控制项 | 含义 | 默认值 |
| --- | --- | --- |
| `fleet_size` | 飞行器数量（1–12） | 3 |
| `wind_speed` | 常态风速，米/秒（0–15） | 2.0 |
| `conflict_resolver` | `altitude_layers` 或 `hold_and_go` | altitude_layers |

## 衡量指标

`tasks_completed`、`mean_task_latency_s`、`energy_used_kj`、`weather_holds`、`separation_conflicts`。

## 运行与查看

先创建项目、配置、校验、保存版本并初始化；初始化后保持静止，按“启动”才开始仿真。命令行也可运行同一场景：

```bash
aeroagentsim run scenarios/templates/air-taxi/scenario.yaml
aeroagentsim metrics runs/<run-directory>
aeroagentsim replay runs/<run-directory>
```

空域引擎是间隔决策的唯一写入者；运动引擎在同一共享时钟上执行已授权的命令。
## 黄浦城区布置

坐标来自仓库中的 lite-city 建筑轮廓、屋顶高度和道路，由 `tools/templates/place.py` 生成。屋顶起降点使用实际屋顶高度；站点间飞行先垂直起降，再在 105 米屋顶高度上限之上巡航。名称和业务元数据会随运行固定保存。参见[共享布置指南](../../../docs/examples/city-day.md#huangpu-placements-la3)。

## 操作员故事

选择 **实时交互** 运行方式，应用到草稿、校验、保存版本、初始化，再明确启动。选择 east-port 并注入 close-vertiport。使用该起降场的航班改往 north-port，实际制动并重新规划航线；在“为什么？”中查看关闭、调度和航班完成。 事件须在实时运行中提交；接收回执之后，以日志中的领域影响确认执行。
