<!-- guide-language: en-US -->

# Urgent medical delivery

A beginner template: six drones carry routine parcels, and when an
urgent medical request appears they pause the parcel and fly the medicine
first. One shared simulated world, one clock, 300 simulated seconds.

## What happens

- Routine parcel orders arrive in small bursts at two vertiports; allocation runs every 10 s over all pending orders, so `nearest`, `auction` and `hungarian` choose differently.
- Urgent medical tasks (priority 100) are released at 8 s, 90 s and 150 s.
- With `medical_preemption` on, an eligible parcel flight is suspended, the
  drone flies the medical leg, then the parcel resumes.
- The environment engine publishes `lowalt.wind`; each aircraft class has a
  wind limit and holds position when the wind exceeds it.

## Knobs

| Knob | Meaning | Default |
| --- | --- | --- |
| `fleet_size` | Number of aircraft (1–12) | 6 |
| `order_rate` | Routine parcel orders per second | 0.06 |
| `wind_speed` | Base wind speed in m/s (0–15) | 2.0 |
| `allocator` | Task allocation: `nearest`, `auction`, `hungarian`, `llm` | nearest |

Knobs are applied to the scenario before validation; errors are reported in
plain language.

## KPIs to watch

`tasks_completed`, `mean_task_latency_s`, `energy_used_kj`, `weather_holds`,
`separation_conflicts`, plus medical-specific `medical_mean_task_latency_s`
and `medical_p95_task_latency_s`.

## Run it (existing CLI)

```bash
aeroagentsim run scenarios/templates/medical-delivery/scenario.yaml
aeroagentsim metrics runs/<run-directory>
aeroagentsim replay runs/<run-directory>
aeroagentsim serve   # browser console for the same scenario
```

All engines write through one shared runtime with a single writer per field,
so a completed run replays deterministically.

## Huangpu city placement

Coordinates are derived from the committed lite-city building outlines, roof
heights and roads by `tools/templates/place.py`. Roof pads use their actual roof
height; inter-site flights depart/arrive vertically and cruise above the 105 m
roof ceiling. Names and business metadata are pinned with each run. See
[the shared placement guide](../../../docs/examples/city-day.md#huangpu-placements-la3).

Routine arrivals stop at 180 s. Urgent supplies arrive at 8, 90 and 150 s;
late routine deliveries have time to resume after preemption. Edit battery,
separation and wind policy in [the shared rules](../../../docs/guides/lowalt-rules.md).

At seed 42 the six-aircraft default, using `hold_and_go`, completes 15 of 18
created orders by 300 s. Keeping four aircraft and changing only the resolver
improves 11/18 to 14/18; the extra two aircraft complete one further order.
Preemption and congestion still leave a backlog. These are unfinished orders,
not task failures, and adding aircraft is not guaranteed to improve every seed.

## Operator story

Choose **interactive** Run mode, Apply controls to draft, Validate, Save version, Initialize, then Start. Select urgent-2 and inject prioritize-medical with priority 200. The unused medical request releases immediately and can preempt routine work; follow dispatch and completion in Why?. Events require a live run; receipt acceptance is followed by recorded domain effects.

<!-- guide-language: zh-CN -->

# 紧急医疗配送

六架无人机配送普通包裹。紧急医疗请求出现时，符合条件的无人机先暂停普通包裹任务、优先运送药品，再恢复包裹配送。整个场景共享同一世界与时钟，运行 300 仿真秒。

## 场景过程

- 普通订单在两个起降站小批量到达。每 10 秒对所有待分配订单进行分配，`nearest`、`auction` 和 `hungarian` 的选择可能不同。
- 优先级为 100 的医疗任务在 8、90 和 150 秒释放。
- 开启 `medical_preemption` 后，可中断的包裹航段会暂停，医疗航段完成后再恢复。
- 环境引擎发布 `lowalt.wind`；各类飞行器按自己的风速上限决定是否等待。

## 控制项

| 控制项 | 含义 | 默认值 |
| --- | --- | --- |
| `fleet_size` | 飞行器数量（1–12） | 6 |
| `order_rate` | 普通包裹订单到达速率，单/秒 | 0.06 |
| `wind_speed` | 常态风速，米/秒（0–15） | 2.0 |
| `allocator` | 分配算法：`nearest`、`auction`、`hungarian`、`llm` | nearest |

控制项先写入场景草稿，再进行真实校验。

## 衡量指标

`tasks_completed`、`mean_task_latency_s`、`energy_used_kj`、`weather_holds`、`separation_conflicts`，以及 `medical_mean_task_latency_s` 和 `medical_p95_task_latency_s`。

## 运行与查看

创建项目、配置、校验、保存版本、初始化，再按“启动”。命令行入口：

```bash
aeroagentsim run scenarios/templates/medical-delivery/scenario.yaml
aeroagentsim metrics runs/<run-directory>
aeroagentsim replay runs/<run-directory>
```

各引擎共享运行时，每个字段只有一个写入者，已完成的运行可确定性回放。
## 黄浦城区布置

坐标来自仓库中的 lite-city 建筑轮廓、屋顶高度和道路，由 `tools/templates/place.py` 生成。屋顶起降点使用实际屋顶高度；站点间飞行先垂直起降，再在 105 米屋顶高度上限之上巡航。名称和业务元数据会随运行固定保存。参见[共享布置指南](../../../docs/examples/city-day.md#huangpu-placements-la3)。

普通订单在 180 秒停止到达，晚到的普通配送可在医疗抢占后恢复。电池、间隔和风速策略可在[共享规则](../../../docs/guides/lowalt-rules.md)中编辑。

种子 42、默认六架飞行器和 `hold_and_go` 下，300 秒内完成 18 个已创建订单中的 15 个。四架飞行器只改变冲突处理器，完成数可由 11/18 提升至 14/18；增加两架再多完成一个。抢占和拥堵仍会留下积压：未完成订单并不等于失败，增加飞行器也不保证每个种子都改善。

## 操作员故事

选择 **实时交互** 运行方式，应用到草稿、校验、保存版本、初始化，再明确启动。选择 urgent-2，注入 prioritize-medical 并将优先级设为 200。尚未释放的医疗任务立即进入队列，可抢占普通任务；在“为什么？”中查看调度和完成。 事件须在实时运行中提交；接收回执之后，以日志中的领域影响确认执行。
