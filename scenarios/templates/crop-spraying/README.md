<!-- guide-language: en-US -->

# Crop spraying

A beginner template: a drone treats a rectangular field in alternating rows,
swaps batteries at the field edge when its charge runs low, and pauses
spraying while wind is too strong.

## What happens

- One spraying task crosses the field via the `coverage` work route
  (boustrophedon rows).
- The battery capacity is sized for a partial treatment; when the state of
  charge falls below the low threshold the drone returns to `home-pad` and
  the energy engine swaps in a fresh pack.
- A wind gust is authored between 90 s and 125 s at 8 m/s, above the
  spraying wind limit of 5 m/s, so spraying pauses until it passes.

## Knobs

| Knob | Meaning | Default |
| --- | --- | --- |
| `fleet_size` | Number of aircraft (1–12) | 1 |
| `wind_speed` | Base wind speed in m/s (0–15) | 2.0 |
| `row_spacing` | Distance between spraying rows in m (10–80) | 30 |

## KPIs to watch

`tasks_completed`, `mean_task_latency_s`, `energy_used_kj`, `weather_holds`,
`separation_conflicts`, plus `spraying_distance_m` and `battery_swaps`.

## Run it (existing CLI)

```bash
aeroagentsim run scenarios/templates/crop-spraying/scenario.yaml
aeroagentsim metrics runs/<run-directory>
aeroagentsim replay runs/<run-directory>
aeroagentsim serve   # browser console for the same scenario
```

Motion, energy, weather, airspace and task state each have exactly one
writer engine on the shared discrete-event clock, so results replay.

## Huangpu city placement

Coordinates are derived from the committed lite-city building outlines, roof
heights and roads by `tools/templates/place.py`. Roof pads use their actual roof
height; inter-site flights depart/arrive vertically and cruise above the 105 m
roof ceiling. Names and business metadata are pinned with each run. See
[the shared placement guide](../../../docs/examples/city-day.md#huangpu-placements-la3).

The green field is an explicitly authored demonstration polygon beyond the
northern dense core, not source OSM cropland. Spraying rows are 5 m above its
ground plane; the standalone swap pad is at the field edge.

## Operator story

Choose **interactive** Run mode, Apply controls to draft, Validate, Save version, Initialize, then Start. Select field-1 and inject pause-spraying for 20 seconds. The motion owner stops the aircraft; spraying observations pause, then resume after the hold. Follow both records in Why?. Events require a live run; receipt acceptance is followed by recorded domain effects.

<!-- guide-language: zh-CN -->

# 农田喷洒作业

无人机沿矩形地块逐行往返喷洒，电量不足时到田边换电，风速过大时暂停喷洒。

## 场景过程

- 一个喷洒任务使用 `coverage` 航路，以往返行覆盖地块。
- 电池容量只支持部分作业；电量低于阈值时，无人机返回 `home-pad`，由能量引擎换入新电池。
- 90–125 秒安排 8 米/秒阵风，超过 5 米/秒喷洒风速上限，喷洒暂停至阵风结束。

## 控制项

| 控制项 | 含义 | 默认值 |
| --- | --- | --- |
| `fleet_size` | 飞行器数量（1–12） | 1 |
| `wind_speed` | 常态风速，米/秒（0–15） | 2.0 |
| `row_spacing` | 喷洒行间距，米（10–80） | 30 |

## 衡量指标

`tasks_completed`、`mean_task_latency_s`、`energy_used_kj`、`weather_holds`、`separation_conflicts`，以及 `spraying_distance_m` 和 `battery_swaps`。

## 运行与查看

创建项目、配置、校验、保存版本、初始化，再按“启动”。命令行入口：

```bash
aeroagentsim run scenarios/templates/crop-spraying/scenario.yaml
aeroagentsim metrics runs/<run-directory>
aeroagentsim replay runs/<run-directory>
```

运动、能量、天气、空域和任务状态各自只有一个写入引擎，共享离散事件时钟，结果可回放。
## 黄浦城区布置

坐标来自仓库中的 lite-city 建筑轮廓、屋顶高度和道路，由 `tools/templates/place.py` 生成。屋顶起降点使用实际屋顶高度；站点间飞行先垂直起降，再在 105 米屋顶高度上限之上巡航。名称和业务元数据会随运行固定保存。参见[共享布置指南](../../../docs/examples/city-day.md#huangpu-placements-la3)。

绿色地块是在密集城区以北明确编写的演示多边形，并非来自 OSM 的真实农田。喷洒航行高度为地面上方 5 米，独立换电平台位于田边。

## 操作员故事

选择 **实时交互** 运行方式，应用到草稿、校验、保存版本、初始化，再明确启动。选择 field-1，注入 pause-spraying，停喷时长设为 20 秒。运动组件使飞行器停止，喷洒观测在停喷时段暂停，随后恢复；在“为什么？”中追踪这两条记录。 事件须在实时运行中提交；接收回执之后，以日志中的领域影响确认执行。
