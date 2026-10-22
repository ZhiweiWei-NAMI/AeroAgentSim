# Guide: Visualization and the web console

The web console is a React/Vite single-page app in
[../../frontend/src](../../frontend/src), served by `aeroagentsim serve` (or
any static host proxying the API). Routes are defined in
[../../frontend/src/App.js](../../frontend/src/App.js).

## Routes

| Route | Page | What it does |
| --- | --- | --- |
| `/`, `/w/{workspace}` | [Workbench](../../frontend/src/workbench/Workbench.tsx) | Gallery, configuration, versions, live runs, replay, records and comparison — one 3D scene with hovering panels |
| `/agents/{id}` | [AgentConsole](../../frontend/src/pages/AgentConsole.tsx) | Agent decisions, records and receipt history for one run |
| any route with `?capture=1` | [CaptureConsole](../../frontend/src/observations/CaptureConsole.tsx) | Headless renderer used by the in-simulation camera |

Attach a console to a service origin with the `api` query parameter, for
example `/?run=<run-id>&mode=live&api=http://127.0.0.1:8002`. Earlier
addresses (`/studio`, `/runs/<id>`, `/inspect/<id>`, `/aerograph`) redirect
into the workbench with their context.

## Configuring a project

Click a thumbnail in the gallery to read the scenario guide, then **Create a
project**. The configuration panel opens next to the 3D draft preview:

- **Controls** — the template's knobs (written into the draft).
- **Space, Objects, Resources, Tasks, Algorithms, Components, Run** — the
  seven sections of the draft. Tasks includes a structured editor for
  predicates, triggers and event chains.
- **File** — the same draft as YAML (export/import).

**Validate** lists every issue and jumps to its section and field. Validation
checks configuration and declarations; it is not readiness and does not prove
that initialization, a worker, a camera, a native engine or a model provider
will succeed. **Save version** freezes the validated draft. **Initialize**
launches that immutable version with `autostart: false`; the world is held at
t = 0 only when the returned status is `initialized`. A faulted, interrupted or
other terminal response is not success, and the run stays available for
diagnosis. **Start** is separate and accepts only an `initialized` run. The
browser never evaluates predicates or simulates behaviour — the server does.

For manual creation, use [the blank-project tutorial](blank-project-tutorial.md)
([中文](blank-project-tutorial.zh-CN.md)): Region preview/apply, Add objects,
Add relation, Add order stream, rules, Weather & traffic, algorithms and the
same validation/version/run sequence. Templates prefill these inputs;
region geometry alone does not produce a complete simulation.

## Operating a run

- **Simulation control** (top bar): start, pause, resume, stop and simulation
  speed (0.5× to max). Refreshing the page never starts, stops or resubmits.
- **Playback** (dock): play, seek and playback speed browse the record and
  never affect the simulation. Seeking a past cut shows a "viewing history"
  banner with **Back to live**.
- **One selection**: selecting in the 3D scene, the object list, the graph or
  an event selects the same identity `{runId, epoch, id, generation}`
  everywhere. **Focus**/**Follow** fly the camera; the camera pad zooms,
  rotates and resets.
- **Operate**: the inspector's Operate tab selects a registered command or a
  declared injection point with its typed payload and shows the receipt. When a
  run reports `waiting_for_input`, a banner explains what it needs.
- **Why?**: an event's recorded-link view on the timeline. These are same-cut
  associations and recorded links, not causal attribution. **Photos**: camera
  captures (traffic accident) open at their source cut.
- **Records**: replay, compare 2–4 runs, **Modify & retry** to derive a new
  draft from a run.

The persistent camera browser acknowledges app readiness, then initializes the
issued run header's pinned city resources and shaders before its first frame.
Browser startup and asset initialization each have a 30-second bound. Asset
readiness is reused for the same camera resources; preparation creates no PNG
or journal event. Frame acquisition still uses the smaller of the renderer and
request timeouts and verifies the exact source cut, camera and assets. Failure
in any phase remains an explicit capture failure and never produces a photo.

## Workbench shortcuts

These are the bindings in `workbench/Workbench.tsx`. Letter keys below mean
unshifted lowercase key presses; use the language button for English/Chinese.
工作台字母快捷键不按 Shift；语言通过按钮切换。

| Key / 按键 | Action / 操作 | Context / 条件 |
| --- | --- | --- |
| `Ctrl+K` / `⌘K` | Command palette / 命令面板 | Also available while typing, outside dialogs / 对话框之外，输入时也可用 |
| `C` | Configure / 配置 | Project open / 已打开项目 |
| `A` | Assistant / 助手 | Toggle assistant / 切换助手 |
| `R` | Records / 运行记录 | Toggle records / 切换记录 |
| `G` | Relation graph / 关系图 | Run session loaded / 已加载运行 |
| `F` | Follow / 跟随 | Selected entity has a spatial binding; toggles orbit/follow / 已选实体有空间绑定，切换环绕／跟随 |
| `I` | Operate / 操作 | Following live, live mode, status running/paused/waiting_for_input / 实时模式跟随当前运行，状态为运行／暂停／等待输入 |
| `L` | Back to live / 返回实时 | Run session loaded; not a language toggle / 已加载运行，不切换语言 |
| `Space` | Play/pause recorded playback / 播放或暂停记录回放 | Run session loaded; leaves live following, does not pause the simulation / 已加载运行，离开实时跟随，不暂停仿真 |
| `Esc` | Close overlay or clear selection / 关闭浮层或取消选择 | First leaves an edited field; dialogs own their keys / 先离开编辑字段，对话框使用自己的键盘操作 |

Use top-bar Pause/Resume for the simulation. Other shortcuts are inactive while
entering text. Workbench does not bind
`D`, `W` or `?` globally. The older `console/shortcuts.tsx` Simple-view bindings
(`D` Director, `C` cinema, `W` Why?, `L` language, `?` help) describe that
component, not the main workbench. Use the workbench camera controls and
recorded-event Why? action instead.

暂停／恢复仿真使用顶部按钮。输入文字时其他快捷键不触发。主工作台未全局绑定
`D`、`W`、`?`；旧 Simple 组件
的导播／影院／解释／语言／帮助绑定不能当作工作台绑定。工作台使用相机控件和
记录事件的“为什么”入口。

## Feed contract

The pinned service boundary is
[../../frontend/src/contracts/viewer-feed.ts](../../frontend/src/contracts/viewer-feed.ts)
(`aeroagentsim.viewer-feed/v1`); transports implement `header`/`subscribe`
over the HTTP/SSE endpoints (`/v1/runs/{id}/header`, `/commits`, `/stream`).
The store owns the committed cut, exact facts, relation changes and
message/receipt history; display buffers never write facts back. Behaviour
extensions (`predicate-truth/v1`, `chain-instance/v1`) appear as optional
header/commit fields and are absent in older runs.

## Limitations

- 3D presentation is display art direction (sky, lighting, interpolation);
  it does not add physics steps or invent state.
- The demo's camera captures render committed poses with the selected city geometry
  in headless Chromium — simulation-camera output, not licensed city imagery.
- The optional high-detail city pack is a separate download, keeping clones small.
- Live model replies and fixture/scripted decisions are different evidence
  classes. Assistant text parts retain `source: model` or `source: platform`
  (legacy or ambiguous parts are `unknown`); a fixture or scripted run is not
  evidence of live-model quality.
- The browser console sends no bearer token, so a server with
  `AEROAGENTSIM_API_TOKEN` set is not a supported browser deployment even though
  its API accepts bearer tokens. Use the console with the token unset on a
  local/trusted boundary, or use an API client for a protected service.
- Feed requests use `AbortController`. Temporary network and server failures
  reconnect with bounded backoff from the last received journal index; they
  never resubmit commands or become empty successful data. Leaving the view
  cancels pending reads.

See also: [HTTP API reference](../reference/http-api.md),
[viewer feed reference](../reference/viewer-feed.md).

## Shared Huangpu city

Shipped templates declare `city: huangpu` in their manifest or scenario. Display
coordinates share the traffic demo's local ENU metres: east is scene X, north is
negative scene Z, and up is scene Y. The city reference selects display geometry;
it does not move entities, change flight paths or add obstacle physics.

Without an installed pack, the viewer and photo camera render the committed lite
city. `aeroagentsim assets fetch` adds the detailed city for every Huangpu template.
Reload the viewer after installation or removal. The viewer header records
`cityDetail` (`lite` or `detailed`) and `assetPacks`; these are header metadata,
not proof that geometry loaded or that every frontend displays an install hint.
Use `aeroagentsim assets list` for installed id/version/directory and
[asset verification and licences](../../ASSETS.md) for SHA-256, manifest sizes,
offline installation and NOTICE. An explicitly configured asset root has priority.

查看器头记录轻量／精细身份与资源包信息，但元数据不证明几何载入成功，也不保证
所有前端显示下载提示。用 `assets list` 查看真实安装版本与目录；详见资产说明。

The lite bundle covers a smaller central excerpt, while the detailed scene spans
approximately ±540 m. Template site placement must be checked against actual
footprints and rooftop heights; selecting a city does not certify a landing site.

For parcel-delivery's current placement and a proposed translation for template
authors, see [parcel city placement](parcel-city-placement.md). The template
coordinates remain unchanged.
