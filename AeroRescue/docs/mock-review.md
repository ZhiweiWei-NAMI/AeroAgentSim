# Mock 后端独立审阅

更新：2026-10-17 15:47 UTC。状态：seed42 a3与seed43 a1的五端mock业务完成、无线因果、返航收尾和六进程正常退出均已独立核对；最新C08 a3的pending接受遇closure失败及返航已核对。下表另标明由主代理/SDK记录的补充运行范围；旧失败保留。本文件不是原生验收通过声明。

## 本阶段边界

本阶段允许明确标识的 mock 后端替代当前不可用的 Docker 原生后端，用于验证相同业务接口、交互代码及真实 Cordis/DSH 工具链。mock 的执行结果只能证明对应的软件交互；不能证明 PX4 飞行、Gazebo 图像/接触、ns-3 无线传播或原生容器隔离。

默认 native 启动应保持不变，mock 必须显式选择。按项记录实现、实际运行及限制，未知项保持待验证。原有 [acceptance.csv](acceptance.csv) 的 50 项不因 mock 运行自动升级为 PASS；最终逐项审批仍待完成。

## 已核对的起点

- 13:57 UTC 的工作树尚无 mock 实现改动。`deploy/compose.yaml` 仍配置真实 simulator、radio、coordinator、四架 UAV 与 centre 服务。
- `coordinator.py` 当前构造现有 `Simulation`、`PX4GazeboEngine`、`NS3Engine`；唯一 coordinator 推进内核时间，并向私有 endpoint 分发消息。
- 已有本地 `read-evidence` 和 DSH 恢复记录仅覆盖单项本地工具与恢复，不是本阶段五个 DSH 实例的自主救援证据。
- 既有源码受控检查不是 mock 完整运行，也不是 native 验收。旧失败及原生未运行状态继续保留。
- 已只读检查 `tools/run_mock.py` 首版：新建唯一运行目录，显式 `--backend mock`；private-scene 仅传 coordinator，去掉 `mock_backend` 后的配置传五个正式 Node/DSH launcher，各用独立数据目录；runner 没有代替 DSH 向工具队列写请求。此时尚未执行。
- 该本地 runner 不提供 OS 级目录隔离。同宿主身份的 Bash/文件访问不能据此宣称跨机安全隔离；本阶段只能核对正常接口绑定、数据目录分离以及 DSH 上下文是否泄漏其他机未收信息。

## Mock / native 逐项差异与核对状态

| 项目 | 应复用的软件路径 | Mock 与 native 的实际差异 | 当前证据及待办 |
| --- | --- | --- | --- |
| 后端选择 | 现有 native coordinator 与 Compose 默认入口 | Mock 需要显式入口/参数；不能静默回退 | 已核对 `Coordinator(...backend='native')` 和 CLI 默认 native；仅显式 mock 替换两个 adapter client，Compose 未改 |
| 单一时间推进 | Coordinator 排队、推进、收集结果 | Mock 推进自有模拟时钟；不等同 Gazebo/ns-3 同步 | 已核对仍使用 kernel `run_until` 和原 PX4/ns-3 adapters；bootstrap 记录推进 2 sim s，单机运行至设定边界 |
| FlightClient / bridge | 同一请求接口、operation ID、accepted/pending/terminal 生命周期 | Mock 专用 `file://` IPC 代替不可用 Unix socket，状态来源为 mock | 单机跨进程请求已运行；operation/kernel history 和返回结果标识 mock，原 Unix 路径保留 |
| 飞行与取消 | 真实工具提交、队列、取消与结果查询 | Mock 使用明确的运动/停止模型；不能证明真实 5 秒 hold 指标 | file IPC smoke-02及C05组件诊断已见实际运动取消，C05为3m/s、1.98sim秒终态，另有queued边界/旧revision/低层重放诊断；主代理/SDK记录C04 a2五端实际撤回、取消确认并换u4 inspect，整场后因投送报价迟到未交付 |
| 私有信息 | 每机 endpoint、独立数据目录和已收 inbox | 同宿主共享 OS 身份，无目录访问安全隔离；只能验证正常接口的信息边界 | 已核对private/public配置、独立目录与实际context来源；file IPC诊断有跨identity拒绝，centre求解引用对应本地inbox收件。上述不证明同宿主Bash无法读取其他目录 |
| Cordis/DSH | 正式 SDK agent loop、skill discovery、工具派发与完成通知 | 本地 rule provider 可用，但不能据此证明外部模型调用 | 单机真实 DSH 自主完成 accept/arm/takeoff/hold；五端 a1 各有实际 tool/call，已因 relay 缺陷中断；a3真实五端已产生completed_with_delivery，三架非保护机返航落地并disarm，六进程exit0 |
| 中心求解 | 已收 bid/report → 现有 bounded solver → 显式 publish | 输入只能来自 centre 已收消息；mock 全局状态不可直接灌入 solver | 五端 a1 已核对：三个 bid 均于 2.8 sim s 在 centre inbox commit，10 sim s 求解，selected u2 引用本轮 bid；其有效期 16.4 sim s。u1 protected 未出现在候选 bid 中；a2 的 delivery 求解已核对本轮收件：u4/u3 bid 分别235.4/236.0 sim s commit，242.6 sim s计划选择u4，其有效期249.0 sim s |
| 分片与 ACK | 现有 RadioTransport、真实字节、重组、校验、ACK 和超时 | Mock 产生显式模拟无线事件；不证明 ns-3 传播损伤 | C03 两个直接接口诊断已逐包核对：1ns lifetime 触发 mock drop，真实 RadioTransport 查询/补片/补 ACK，5731 bytes 收件一致；主代理/SDK另记录五DSH缺片与文件finalACK丢失run分别313.8/312.8 sim s交付并返航。失路后二次attempt、no_valid_path、接受前丢分配尚无完成证据 |
| Relay | 现有 probe、route、hold 与重测业务流程 | Mock 链路可达性须有声明模型，不能硬编码业务成功 | 定点 relay 真 DSH 运行已见 6 轮 readiness、12 次 submitted，未再发生旧 TTL 错误；a3及seed43已见receipt/confirmation真实超时后，经本地收件学到候选并同ID中继恢复；不能外推所有移动拓扑 |
| RGB-D / 估计姿态 | 现有 capture 与档案接口、帧引用、时间关联 | Mock 对公开 AABB、地面及私有正对相机的照片 billboard 渲染 PNG/optical-Z 深度；姿态来自 mock 运动模型 | 源码及 a1 实际 PNG/metadata 已核对，来源为 mock_scene_renderer/mock_kinematic_model；真实 Gazebo/PX4 待验证 |
| 检测、定位、历史 | 现有 detector/geometry/history 工具与本机持久档案 | 执行真实 ONNX 不代表 mock 图片等同自然场景，也不能替代原生/外部真实图像指标 | a1已见CPU实际检测、深度反投影及双视点；C10-05同38张开发renderer图达31/31定位有效，非留出集。可选640tile默认关闭；主代理/SDK记录C06 a3双机worker重启恢复12帧档案/收件/游标，冻结记录派生查询重启前后各20/20命中、5/5缺失正确为空；本审阅未重复核该批原记录 |
| 投放与接收 | 现有 release、receipt、双 ACK confirmation、centre 完成门槛 | Mock 落点/接触由声明的物理简化模型产生；不能冒充 Gazebo contact | 直接接口 inside/outside 已见不同落点导致有/无 receipt；C07 两个损伤诊断已核对无线因果。五端 a2 已实际自主投放并 carrier 验证；a2的centre回执及报告被固定直连路由阻断；a3修复后双ACK confirmation和carrier报告均在centre收件，293.2 sim s业务完成 |
| 能耗 | 既有显式研究模型与源时间积分 | Mock 速度/时间输入不是实测 PX4；250 W + 12 J/m 仍为模型参数 | 已独立核对idle hover实际扣能；主代理/SDK记录C04 a2在82.6 sim s实收withdrawal、96.6s取消确认后换u4 inspect。后续投送报价窗口超时，348.4s unfulfilled_no_resource；不将部分换机成功写成完整交付 |
| 重启与不确定提交 | 现有持久 operation、control_inflight、已处理消息 | Mock 可覆盖软件失 ACK/重启；不能证明原生连接恢复 | H04两种RadioTransport构造器重建已独立核对；主代理/SDK另记录耐久run在283.6 sim s释放后有序terminate/wait重启u4，同一operation恢复到delivery_verified、release仅1次、coordinator不重启，1811.806墙钟秒存活且六进程exit0。不是突发崩溃/容器恢复，跨代源码未事前逐代冻结 |
| 构建与原生验收 | 原有镜像构建、Compose、原生案例入口 | Host 仍不能运行 Docker/GPU 后端；mock 不消除这一缺口 | Native 构建、传感器、飞行、无线及接触仍未通过 |

## 确定问题与修复记录

1. 已修并复核：`mock_backend.py` 首版 `advance_flight` 未应用 `goto_location.params.yaw_deg`，而 estimate/render 姿态取自 reset 的 yaw。现按 MAVSDK compass heading 转 ENU yaw，并以默认 45 deg/s 推进，完成判据包含航向到达。`mock-flight-smoke-02` 的 180 度命令完成后，实际 telemetry FRD→ENU quaternion 为 `[0.70710678, -0.70710678, 0, 0]`。
2. 已修并复核：首版仅在活动 command 循环内扣 `energy_used_j`，airborne idle 不扣 hover 能量。现每个 advance 对全部飞机按实际位移和 airborne/armed 状态积分。smoke-02 中 12.6→14.6 sim s 位置不变、没有活动动作，kernel battery fraction 从 `0.9871666667` 降至 `0.9850833333`，240000 J 容量下减少 500 J，与声明 250 W 相符。
3. 已修，定点DSH及后续五端完整复跑均已核对：五端 a1 的 u3 成功复测后，`runtime._relay_decision` 更新 monitor 并返回 None，`propose` 将其转成等待 5 秒；再宣布 readiness 时最早 probe 已过 5 秒，工具按正确的新鲜度门槛拒绝。实际记录为 202.0/203.2 sim s 两个 ACK，203.4 更新轮次，208.4 才开始宣布。已核对修复为更新 monitor 后立即返回下一条 readiness 提议，仍只在 detached view 计算且保留 5 秒阈值。`mock-relay-u3-42-rule-a1` 的实际 SDK 日志已有 6 轮、12 条 readiness submitted，无 unavailable/failed；submitted 本身不证明接收方收到。

4. 已定位、修复并核对a3/seed43实际恢复：五端 a2 的 receiver→centre receipt（`coordinator.py:357–358`）和后续 confirmation（`:411–412`）没有 relay route，固定直连；同场景建筑遮挡下持续 mock delivery_timeout。283.6 sim s receipt 排队，carrier 在284.8 sim s实际收到、285.2 sim s ACK；centre方向288.8/293.8/298.8 sim s等连续丢包，centre receipts仍为0。另外 `runtime.py:595` relay peers只包含centre+inspect机，carrier u4没有readiness，实际delivery_report也走 `[u4,centre]` 被阻断。需要补真实无线中继路径，不能通过降低完成门槛、直接复制收件或调弱损伤来变成成功。已即时告知01/03/root。现修复为relay根据本机已收任务角色轮换centre+service-peer两端实际probe；receiver仅消费自身真实radio收件、校验source/service_peer/两proof及5s时序，然后对同event未终态receipt/confirmation以原ID更新route并真实probe。carrier已有收到fresh readiness后对active message同ID reroute机制。已只读核对，不因这次源码修复把a2改为成功。

以上均为本阶段运行发现的实现缺陷。前两项在mock backend，后两项在复用业务流程；不能用native未运行掩盖，也不能未经原生运行外推原生表现。

## 运行证据与审批

- `runs/mock/mock-single-u1-42-rule-a1`：已读 manifest、summary、DSH 事件。u1 自主 accept-task、arm、takeoff、hold，共 4 次 DSH call、4 次 pending 和 4 次 completion；三个 flight 经 accepted/executing/succeeded kernel history 完成。21.15 wall s，coordinator/u1 exit 0；没有 centre outcome，不代表完整救援成功。
- `runs/mock/mock-four-42-rule-a1`：14:02:40 UTC 启动，因上述 relay TTL 缺陷在 361.4 sim s 中断，manifest 为 `INTERRUPTED/NOT_PASSED`，没有 centre outcome。旧 runner 未完成退出汇总，不能由停止增长的日志宣称所有进程正常退出。五端均有独立 DSH tool/call；public `scenario.yaml` 不含 `mock_backend`，各 context 的 `execution_backend` 为 mock。
- `runs/mock/mock-four-42-rule-a2`：14:10:48 UTC 启动，使用已修源码的独立运行；最终 STOPPED，stop_reason 为 missing_carrier_and_receiver_relay_routes，wall407.54s，六进程exit0。manifest明确incomplete/NOT_PASSED，无centre outcome；exit0仅证明清理正常。已经完成真实DSH的检测、复核、中心二次求解、carrier飞行/投放/观察，但上述回执和报告路由阻断完整闭环。
- `runs/mock/mock-four-42-rule-a3`：14:21:24 UTC启动，采用pair relay与receiver本地收路由修复；最终已逐包核对293.2 sim s的completed_with_delivery和三架非保护机返航；STOPPED/completed/closure_complete=true，wall388.51s，六进程exit0。
- `runs/mock/mock-relay-u3-42-rule-a1`：已独立核对原始 SDK 事件与汇总：34 次 tool/call、34 次 pending、33 条唯一完成通知（21 succeeded、12 submitted），6 轮向 centre/u2 宣布 readiness。达到 65 sim s 边界停止，wall 66.21 s，两进程 exit 0；有一项 pending 未由边界前 completion 闭合，不写成全部完成。
- `runs/mock-flight-smoke-02`：直接真实 file IPC 调用，不经过 DSH 的窄诊断。已核对 kernel、mock-world 与 estimated-telemetry 日志中的状态运动、航向、空闲耗电和取消时刻。该目录不能替代五端自主运行证据。
- 五端 a1 在上述 mock 修复前已启动，不会因磁盘源码更新自动得到新行为；其结果必须保留首轮缺陷与版本时序，后续复跑单独记录。
- 五端 a2 已核对 delivery 阶段：centre 的 `last_plan.received_bid_ids` 为u4 `b077ec64b07c45a0868a331033ed6c71` 与u3 `fd1c9f690aa8456186dd000f8c069967`；对应本地inbox接收时间235.4/236.0 sim s早于242.6 sim s求解，选择u4且其bid有效期249.0 sim s。u2的crop文件225.0 sim s收件、确认报告232.4 sim s收件先于此轮征集；没有用未收到的u2迟到delivery bid填充原求解输入。投放281.8 sim s release，282.5 sim s落点 `[60,60,15.2]`，285.4 sim s投后帧，286.8 sim s carrier本地delivery_verified；这些均不等于centre完成。
- 五端 a1 首轮求解引用：u2 bid `c79cccc4587f4867b1a33a7fdc764b25`；u3 bid `1e53305603d34da1ac50750a71fa39d8`；u4 bid `3cb184840e57445b9ac23f8bfe7679ee`。已逐一核对 centre 本地 inbox 文件的来源、任务、接收时间及 packet 引用与 `centre-state.json.last_plan` 一致。`native_packet_ids` 是复用协议的字段名；对应事件来自 mock，不能因字段名推断原生执行。
- 五端 a1 感知记录：u2 的 43.4 sim s 帧在 202.4 sim s 被实际 ONNX 分析，person score 0.7162；215.2 sim s 新视点帧与其完成几何复核，记录视角差 51.15 度、定位差 0.035 m。已查看框图，目标是灰色几何背景中的照片 billboard；这些结果只证明该模拟输入上的实际工具链，不证明真实救援场景检测质量。生成 crop 为 1137 bytes，收件/最终 ACK 尚不能据此推断。

### 五端 a3 的独立因果核对（mock）

- 五个独立真实DSH都生成实际tool/call和完成通知；centre原始SDK记录中明确调用`rescue_control(action=centre-finish)`，引用实际carrier报告`aea753e8181143a7a02c09fe5aa19adc`。不是runner直接填写outcome。
- carrier在273.8 sim s release；274.5 sim s落在 `[60,60,15.2]` receiver内；274.5→275.5 sim s连续接触后生成物理简化模型receipt。
- carrier receipt `receipt-fd7050a0da495769ace6cbab4d892148` 于276.8 sim s commit，277.2 sim s ACK；投后帧时间277.4 sim s。carrier验证报告在286.2 sim s到centre。
- receiver自身在276.6 sim s收到readiness后，对原centre receipt `receipt-8d86909b9f265a9c96c64045c68c1de2` 同ID改路由 `[drop-receiver-1,u3,centre]`；279.0 sim s commit，279.8 sim s ACK，才排队confirmation。
- confirmation原直连先实际超时，receiver在290.6 sim s收到下一条readiness后对同ID `confirmation-42ca987d4ed851cfb8621ef75ec09af1` 改路由；293.0 sim s到centre，centre于293.2 sim s完成。该通知自身ACK在293.8 sim s到receiver；没有递归等待通知自己的ACK。
- centre收到的原receipt、confirmation、carrier报告均绑定同event、`flood-rooftop-1-u4` task、revision2、同package/receipt来源。完成时间严格晚于centre收到确认和carrier报告，且两原receipt ACK先于confirmation。
- 收尾已核对：u3/u2/u4分别294.4/295.6/295.8 sim s实际收到mission_closed；最后disarm kernel终态分别315.4/387.2/323.0 sim s succeeded，三架均ON_GROUND、armed=false、return_after_closure succeeded。u1受保护任务保持，未被强制返航。六进程exit0，wall388.51s；runner的业务完成分类与实际证据一致。
- 实际DSH tool/call数：u1=6、u2=49、u3=254、u4=19、centre=43。不是每项调用都成功：u1拒绝受保护任务改派、u2一份未被中心采用的迟到delivery bid超时、u3早期移动peer probe失败，均有tool.failed记录，未掩盖。u3的summary按user/message消费统计216条终态通知，raw inbox/spliced去重为253条入队终态（186 succeeded、66 submitted、1 failed）；不得把入队和已消费混算成所有调用闭合。

### 第二seed独立重复：seed43 a1（mock）

`runs/mock/mock-four-43-rule-a1` 于14:29:18 UTC启动，最终STOPPED/completed/closure_complete=true；wall395.01s、六进程exit0。已核对原始mock-world、radio ledger、centre本地收件与各机最终状态：

- 280.6 sim s release，281.3 sim s落入receiver，281.3→282.3 sim s连续接触。
- carrier receipt283.6 sim s commit/284.0 sim s ACK；centre receipt285.2 sim s commit/286.0 sim s ACK。
- carrier验证报告292.6 sim s到centre；receiver在297.4 sim s根据自身新收readiness对confirmation原ID改中继，299.8 sim s到centre；centre在300.0 sim s完成，通知自身ACK300.6 sim s。
- u3/u4/u2返航最后disarm kernel succeeded为323.2/329.8/393.6 sim s；三机ON_GROUND、armed=false，u1保护任务保持。
- 实际DSH tool/call：u1/u2/u3/u4/centre=6/50/258/19/44。第二seed证明一次独立重复完成，不构成成功率、泛化能力或native可靠性估计。

### 已独立核对的直接接口诊断

以下运行通过实际 FlightClient/file IPC、原 coordinator/kernel/adapters 与 RadioTransport；没有真实 DSH 自主业务决策，也没有 native 后端。已查看原始 ledger/mock-world，不仅依据 result.json。

- `runs/mock-radio-missing_fragment_nack_then_retry-01`：5731-byte PNG 共 6 片，index 1 首次发送 lifetime=1ns，2.8 sim s 被 adapter 收为 mock drop；7.4 sim s 发 status_query，8.6 sim s 缺片到达并完成重组，9.0 sim s final ACK。只有缺失片补发，收件 bytes 与源文件一致。
- `runs/mock-radio-final_ack_lost-01`：2.8 sim s 完整收件，首 final ACK 在 3.2 sim s 被收为 mock drop；7.4 sim s 查询，8.2 sim s 补 ACK 后发送方成功。六个数据片各仅发一次；收件成功与发送方知晓成功的时刻分开。
- `runs/mock-payload-inside-01`：40.4 sim s 从 `[60,60,17.65]` release，41.1 sim s 落在 `[60,60,15.2]`，41.1→42.1 sim s 满足连续接触；形成一份 carrier receipt，并排队双 ACK confirmation。`runs/mock-payload-outside-01`：44.0 sim s 从 `[72,72,17.65]` release，44.72 sim s 落到 `[72,72,15.1]`，receiver 外，无 receipt/confirmation。结果由运动、落点和接触状态演进决定，不能将简化模型解释为真实投放精度。
- `runs/mock-C07-receiver_receipt_lost-01`：同样实际 mock 接触后，centre 在 43.8 sim s 收到 receipt、44.2 sim s ACK；carrier 的单片 `chunk_index=0` 在两轮 attempt 各发送 3 次，共 6 次 mock drop，不是 6 个不同分片。35 sim s 观察窗口内 carrier 没有 receipt、没有双 ACK confirmation；不能推断窗口之后的业务终态。
- `runs/mock-C07-confirmation_final_ack_lost-01`：centre/carrier 在 43.8 sim s 分别 commit 原 receipt；centre ACK 44.2 sim s 收到，carrier 首 ACK 同时被 mock drop。48.4 sim s 发状态查询，49.2 sim s 恢复 carrier ACK，50.4 sim s confirmation 才在 centre commit，符合双 ACK 因果。该诊断未启动 centre DSH，不能据此证明 centre 业务 completed。

- `runs/mock-receiver-relay-recovery-03`：保留原35dB遮挡，直接FlightClient窄诊断。receiver在81.4 sim s实际收到readiness（两probe ACK79.0/80.0s，有效至84.0s），原receipt同ID换中继route，83.8s centre commit、84.6s ACK；第二readiness88.8s收件后原confirmation同ID换路由，91.2s centre commit。carrier原receipt ACK71.4s。已核对receiver自己的inbox、route日志和packet ledger；诊断停止时confirmation自身尚awaiting_ack，只证明centre收件。此前-01/-02驱动失败保留，不能抹去；无centre DSH业务终态。

- `runs/mock-C05-cancel-01`：已读驱动及实际report。running分支经FlightClient/file IPC，在3m/s水平速度下12.4 sim s接受cancel、14.38 sim s达到canceled，间隔1.98s；queued分支明确在同一bridge锁边界直接调用handler，未dispatch且无kernel command。替换任务通过实际radio送达后旧revision被拒，唯一coordinator受控重放旧底层command ID被mock拒绝，后续5sim秒位置保持。不是DSH自主取消，也不证明PX4/MAVSDK异步取消。

### C08闭事件乱序持久化窄审（mock执行服务）

本次具体缺陷是另一事件的centre closure已收到，但只按当前task判断导致语义丢失，迟到notice随后可复活闭事件。最终runtime在本机实际收件时按event保存mission_closures；仅显式apply且当前同event非protected时才关闭当前任务/释放relay。accept在资源校验和bridge授权前拒绝已闭事件，pending接受在闭事件事实到达后明确失败。不同当前事件不被关闭，未知event的合法centre closure保留为事实；非centre发来的closure/cancel显式rejected并保存结果，不静默记成成功。

已核`case_mock_closure_order.py`与`runs/mock-closure-order-02`原radio ledger、本机持久状态和实际flight终态：B closure2.6s收到但尚未apply，3.8s迟B notice显式接受被拒；执行服务对象同data重建后5.4s的新revision仍被拒。C notice6.8s正常接受，u4在8.2s发来的假C closure被明确拒绝，闭表不写C。C实际飞行中D notice17.0s成为pending，D closure18.4s到达后pending接受失败并移除；C goto仍在39.0s达到kernel succeeded。最终当前event为C、mission_closed为空，闭表仅B/D。

此驱动选择实际无线发送顺序和排队控制，没有改内存写成功；覆盖执行服务对象重新构造，不是进程/容器崩溃重启，也不是五DSH自主案例。原C08失败attempt保留，不能用此窄诊断替代完整多事件运行。

后续`runs/mock/mock-C08-no-eligible-closure-fixed-42-a3`已完成五个真实DSH运行，且已独立核对原本机收件、radio ledger、tool结果、正式DSH session、最终状态与进程生命周期：

- 公开scenario与原`mock-C08-no-eligible-42-a1`逐项相同；主事件323.6s completed_with_delivery，第二事件337.6s unfulfilled_no_resource，第二solver实际received_bid_ids为空。该结果不能解释成已证实所有机均不合格，也不能混用clear-centre a2低能bid排除证据。
- 这是notice先收但accept pending，之后closure使其失败：u2 notice418.4s/closure419.0s；u3 328.4s/339.0s；u4 343.4s/344.0s。三个实际accept结果均为failed，原因incident closed before task acceptance；正式DSH user/message seq1713/3697/451分别实际消费对应tool.failed，区别于仅inbox入队。
- decisions中没有第二event任务曾被接受，pending_task最终均空。三机原任务返航最后disarm分别432.2/346.6/353.4s succeeded，最终全部ON_GROUND、armed=false。process-lifecycle终态和summary六进程均exit0，closure_complete=true，wall433.59s。
- a3证明此pending遇closure后不复活且真实返航的五端路径；closure先于notice、对象重建后保留、另一当前事件不误关等边界仍由order-02单独覆盖。分析中旧purpose文字已更正为pending-notice/closure，未更改原测量。

### C02不确定观测组件的独立核对（mock，driver选择请求）

`tools/case_mock_uncertain.py`与`runs/mock-C02-uncertain-01`两分支已只读核对；查看四张实际PNG、原mock-world/flight operations、工具结果和radio ledger，没有重跑推理。driver直接写既有本机工具队列并推进runtime，不启动Cordis/DSH；该目录不能作为五端自主策略通过证据。

- `occluded_then_visible`：35.6s部分遮挡首帧已有实际ONNX person score0.873且定位available。初始uncertain来自driver刻意用同一frame作双输入，工具拒绝缺乏独立新图；不是模型先判断遮挡不确定。新增`initial_uncertainty_source`明确该边界。随后driver请求reobserve-target，生产工具根据实际首帧定位与公开地图生成[61.300,45.074,40]视点/路线；原记录48.0s goto、53.48s达到、53.8s hold、54.82s完成、55.0s新采图。新图实际检测score0.831，工具验证视角差25.50°、两次水平位置差0.503m，结果confirmed；这些不是相对真值误差或自主救援完成。
- `still_uncertain`：36.0/54.4s两张不同新帧均0检出；无实际detection时reobserve-target返回unavailable，未捏造位置。driver选择固定第二公开视角[62,51,40]；verify仍uncertain，compare明确“未观测不代表离开”。真实report在65.6s到centre、66.0s最终ACK，之后driver直接初始化/调用RescueCentre，在66.2s选择unresolved_evidence。completed调用因缺少必要交付证据被拒，只证明一般完成门槛，不能称centre自主判定或自主二次侦察。诊断centre无线节点显式移到[45,90,0]，不能据此证明原场景遮挡链路。
- 两分支均无payload_release。10个无效输入检查包括原RGB复用推理后的9种明确标记controlled_sensor_fault_input副本，以及不存在frame查询；不是新相机观测或真实故障。NaN/越界深度、背景占优/双簇、shape/配准/时间/pose/缺depth均返回unavailable且无位置。缺块由未接收chunk的CaptureDownload触发；未来帧pending与旧frame缓存过期经实际mock RPC返回，不称native相机断流。
- 本运行显式启用640tile、score.5；不改变默认单图，也不证明真实RGB-D/PX4或五DSH无定位情况下的自主重观察策略。源码与证据限定表述可checkpoint，原50项native验收不升级。

### C02五DSH bbox视线修复的来源边界

`reobserve_target`新增视线支撑只读本机归档frame中的实际detection bbox、near-depth samples、内外参、姿态/位置和当前本机估计；`PublicMapPlanner`只读公开障碍盒。bbox四角以实测选点转换的optical-Z作明确平面假设，原depth samples仍按原convention回投；无私有person坐标、尺寸或评估标签。候选用公开盒检查所有支撑射线，返回metadata明确不保证整个人体可见，最终仍需新帧检测/复核。此处只复核来源边界，几何与固定输入已由其他审阅者核对，不重复扩大评估。

已读`runs/mock/mock-C02-occluded-bbox-los-42-a3/mock-summary.json`：157.8s completed_with_delivery、closure_complete=true、六进程exit0、wall252.04s；其public scenario不含mock_backend。它是独立五DSH运行目录，不能与前述driver组件诊断混算，也不覆盖native或独立留出泛化。此前漏检/未确认attempt仍保留。

### Relay交接责任窄审（mock，两端DSH组件）

- 已读`run_mock_handover.py`、两端正式DSH记录及实际radio ledger。runner配置u3 task revision7与u4独立task revision11、同event；仅accepted分支明确预授权u4接任。没有centre Agent决策，不覆盖中心自主选择替换机。
- `mock-handover-accepted-seed42-a1`：真实帧文件29.0s到u4、29.4s最终ACK；offer38.6s创建、39.8s到达；u4实际到公开relay点hold并对centre/u2完成61.2/62.2s probe ACK后发业务接受，u3在64.2s收到并解除旧责任，再实际返航。原机在等待窗口保持held和原点。该a1启动于accepted_at_ns新增前，不能用其证明最终字段检查。
- `mock-handover-not-authorized-seed42-a1`：真实帧28.8s到达、29.2s最终ACK，offer39.4s到u4；39.6s因当前task未授权拒绝。158.0s交接截止到期，继续到163.0s旧机仍held，无release/return；没有用offer传输ACK或超时替代业务接受。
- 本次源码审查找到并已复读两处修复：held未release时，独立search/reobserve/delivery及goto/land/disarm/takeoff工具入口拒绝离位，保留责任内hold-relay恢复；新hold实际全部peer probe成功建立责任时清旧release授权、旧handover_released与旧返航结果，防同event/同task再次持责继承旧许可。
- 最终实现从本机实际收件校验offer/source、旧/新task版本、已ACK证据、peers与位置；业务接受要求source-bound probe的ACK≤accepted_at≤received且≤5s，接收在offer截止内。proposal只读detached state；责任变化仅发生于实际工具结果。最后源码可checkpoint。`mock-handover-accepted-seed42-a2`已补最终源实跑：快照含accepted_at与上述两处守护；原transport两个u4 probe ACK为61.2/62.2s，accepted_at=63.0s，原radio ledger记录64.2s到u3，真实DSH同64.2s才release；发送方自身最终ACK64.8s不被递归要求。84s最终旧机已ground，新机仍在[10,30,30] held，身份/offer/证据一致；18项固定检查全真，wall85.20s。两端mock组件结果不能外推centre自主选择替换机或native可靠性。

### H04无线组件重建的独立核对（mock）

`runs/mock-H04-radio-rebuild-02` 两分支复用实际FlightClient/file IPC、kernel/adapters与mock包事件，仅在coordinator collect边界重建同root/run的RadioTransport。已读驱动、原始fault/drop/packet ledger和完整重建前后状态文件：

- 缺片分支在2.6 sim s已发生index1实际mock timeout、仅收到0/2/3/4/5共5片且尚未commit时重建；其后只补index1，8.4s commit、8.8s ACK。
- 丢ACK分支在3.0 sim s实际mock final_ACK timeout后重建；2.6s已commit但发送方无ACK，后续查询恢复8.0s ACK；六片各发一次。
- 两分支重建前后完整持久状态相等，原message ID、created_ns、80s deadline、attempt和每片发送计数均保留。kernel/world对象未换，原始mock RPC中flight/radio reset各仅启动时1次；不是重置世界重跑。
- 5731-byte源/收到文件逐字节相同，各receiver_file_commit仅1次，领取后5sim秒无重复本地收件。只证明组件构造器恢复；不证明协调进程、容器或native ns-3崩溃恢复。此前-01浅引用诊断断言失败保留。

### C10切片开发对比（非原生、非留出集）

- `mock-C10-geometry-02/03/04/05`原结果全部保留。03为38帧、31eligible，单图24TP/3FP/7FN，定位有效24/31（77.4%）。04固定640tile、至少25%相邻重叠、全图坐标IoU.5 NMS，31TP/27FP/0FN；额外框均在eligible单人画面内，precision降到53.4%，没有掩盖该退步。
- 05仅增加通用内部tile边界≤1px的候选过滤，保留full-frame和原图外边；score.5/NMS.5未变，不读取GT。已只读核对03/05固定fixtures相同、38张RGB引用与03实际字节一致；GT只在评估匹配/算误差时使用。统一规则排除75个截断候选，最终31TP/0FP/0FN，定位有效31/31，水平误差median0.382m/P950.427m；7views/帧，推理median2.518s/P952.724s。
- 这是观察开发失败后在同一组photo-billboard输入上的迭代，不能称独立held-out指标或实际救援精度。边界过滤可能影响其他尺寸/边缘目标，重叠与全图检测不保证所有目标保留。
- `vision.py`默认tile_size=None仍只有full-frame、原ID/坐标/阈值路径，不启用新增过滤/NMS。root另实际检查empty-scene与new-run-repeat两帧，默认全部detections与03逐项相同，记录于`runs/development/tile-default-compatibility.json`；该记录只覆盖两帧。`perception.detector_tile_size`已可选贯通PerceptionTools，当前场景/Compose未开启，耐久run明确不用tile。

日志中的 `native_submit`、`native_drop`、`native_packet_ids`、`gazebo.simulation` 为复用协议/能力名称。以上来源均为显式 mock；实际 ns-3/Gazebo/PX4 仍未运行。

- 本阶段源码/受控检查与完整运行分别记录。
- 原生结果：不变，仍以原有原生证据及阻塞记录为准。
- 逐项用户审批：待最终报告，不预先标记批准或通过。
