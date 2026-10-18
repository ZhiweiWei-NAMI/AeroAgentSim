# AeroRescue 阶段验收包

更新时间：北京时间2026-10-18 09:12（UTC 2026-10-18 01:12）。平台PR17与应用PR18均未合并，全部用户审批仍待；本次新增运行按各自冻结源记录，不追认到旧版本。标记箱体视觉源码ecb1ae0已审阅推送，与本次冻结运行实现对应；该节点CI待完成。旧必要性四run使用独立冻结源，不追认新视觉门槛。

## 范围与结论

本轮在本机用明确标识的mock飞行、照片billboard RGBD、无线传播与重力包裹模型，运行真实Cordis/DSH、Python执行服务、ONNX/BGE、SQLite、求解器和通信协议。Docker未build/run，原PX4/Gazebo/ns-3及live模型验收要求继续保留，在50项表的剩余项中逐一列明。

C01 seed42/43/44已完成旧定义的模拟接收区投送与回执闭环及非protected三机返航；C03缺片/文件ACK丢失与C07回执ACK丢失也已完成五DSH闭环。21项技能全部有实际调用记录，来源包括完整主案例和单独组件。C04撤回换机成功，后续投送通知迟于报价窗口导致整场未交付。单次30分钟存活及u4有序重启已完成，新增制品归档状态见交付索引，用户审批尚待。

## 投送结果的两代定义

旧运行的原始枚举completed_with_delivery只表示“模拟接收区投送与回执闭环”。旧verify-delivery新帧检查的是接收位置在视野及深度可用，未检测包裹、未验证包裹留存，更未观察人员领取。下面保留历史枚举与数值，不把这些运行追认为新视觉验收。

新运行mock-C01-package-visual-42-a1增加公开库存标记DICT_4X4_50/id7的图像识别与配准深度定位。282.0秒实际释放，285.0秒收到回执，285.6秒新RGBD帧，286.6秒识别标记并定位箱顶[60,60,15.3]米；301.4秒中心收到与投送机相同的观测并结案。实际五DSH共306次调用，396.66秒墙钟，三机返航完成、六进程退出0。证据为runs/mock/mock-C01-package-visual-42-a1/package-visual-integration-observation.json。

新视觉范围仅为“带指定标记的箱体位于接收区”，不等于人员领取或长期留存；0.75米高度容差尚未标定。中心检查收到报告的因果与证据引用，没有下载原图独立重做识别。原生场景未贴标时应保持unknown；这不是无标包裹通用检测。

## 在线重规划对照的实际结果

比较的是“阶段性分配、资源撤回后不再重规划”与在线重规划。静态基线保留正常巡查→投送阶段、相同求解器、无线重试、取消和安全返航；只在真实收到撤回并完成取消后停止产生新报价轮。传统规划器也能在线重规划，本比较不能证明Agent或LLM必需。

C01静态校准在301.4仿真秒达到旧代理投送指标，396.72秒墙钟、306次调用。扰动配对两个a2使用同源、同seed42和归一化后相同公开/私有输入：静态96.6秒failed_execution，126.63秒墙钟、86次调用、92,572提交hop字节；在线106.2秒改派u4、293.4秒完成巡查，随后投送报价未赶上原10秒窗口，346.4秒unfulfilled_no_resource，499.58秒墙钟、370次调用、389,590提交hop字节。两支无释放/回执，均安全返航。

在线恢复了巡查，但本样本没有最终投送收益，多284次调用、297,018提交hop字节及372.95秒墙钟。这些含额外工作/等待/返航，不是标准化吞吐率；一次配对没有统计显著性或普适可靠性结论。两臂都未调用真实模型API，且冻源早于新标记视觉门槛。静态a1暴露取消后关闭返航gate问题，265.54秒停止且未安全收尾；失败保留，修复后a2才纳入配对。证据为runs/development/resource-replan-comparison-20261018/中的report.md、comparison-metrics.json和input-pair-audit.json。

## 短接触与持续接触对照

runs/mock-P04-short-contact-02为真实执行工具链组件：包裹44.9秒落台，45.4秒接触累计500ms时施加明确模拟速度冲量，46.42秒落在接收区外，回执及确认均0。持续接触对照在45.7秒达到1秒门槛，47.0秒投送机收到回执、48.6秒中心收到确认。两支各实际释放1次并返航成功；它补齐P04短接触软件范围，不是五DSH自主实验，也不把接触本身当视觉包裹确认。

## 系统怎样实际工作

1. 每架机与中心各运行一个Cordis/DSH实例，分别持有本机目录和session。Cordis是唯一决策owner；Python以execution-only接受task/revision绑定的工具请求，返回pending与最终完成事件，不运行第二套自主循环。
2. 中心通过无线公开事件和需求；各机根据本机任务、能力、能量估计及可行路线报价。u1原保护任务保持；中心仅用实际收到的报价选任务，不直接读取各机私有档案。
3. u2真实执行goto/capture搜索，RT-DETR检测、数值深度定位、换视角复核。档案分别保留观察、分析与接收时间；证据裁剪成实际JPEG并按分片无线发送。
4. u3先实测双端链路再持续发送ready。文件接收重组、hash检查、应用ACK和有限重试均由现有RadioTransport执行；中继失效或消息超时不会直接写成功。
5. u4接受明确需求和证据后到投放位，执行release。包裹运动、持续接触形成独立receiver receipt；carrier和centre双ACK满足后才形成确认，中心再结案。结束后u2/u3/u4返航、着地disarm，u1原任务不被覆盖。

## 完整案例与运行开销

| run_id | 场景 | 中心终态 | 墙钟 | DSH调用u1/u2/u3/u4/centre | 收尾 |
| --- | --- | --- | --- | --- | --- |
| mock-four-42-rule-a3 | C01 seed42 | 293.2sim s completed_with_delivery | 388.51s | 6/49/254/19/43 | 最后u2 kernel387.2s disarm；三机返航 |
| mock-four-43-rule-a1 | C01 seed43 | 300.0sim s completed_with_delivery | 395.01s | 6/50/258/19/44 | closure=true |
| mock-four-44-rule-a1 | C01 seed44 | 304.4sim s completed_with_delivery | 399.73s | 6/50/258/19/44 | closure=true |
| mock-C03-missing-fragment-42-a1 | 缺文件分片 | 313.8sim s completed_with_delivery | 408.32s | 6/51/262/19/45 | closure=true |
| mock-C03-final-ack-loss-42-a1 | 丢文件finalACK | 312.8sim s completed_with_delivery | 407.75s | 6/51/263/19/45 | closure=true |
| mock-C07-receipt-ack-loss-42-a1 | 丢carrier回执finalACK | 296.8sim s completed_with_delivery | 391.71s | 6/49/252/19/43 | closure=true |

上表各run位于`runs/mock/<run_id>/`，保留`mock-summary.json`、`run-manifest.json`、逐机DSH/工具记录及coordinator事件；均六进程exit0。seed42 a3共371次调用，子进程user/system CPU60.36/11.20秒，ru_maxrss296288KiB为子进程单项记账，不是全进程树峰值。seed43为64.63/12.43秒、275464KiB；seed44为66.73/14.16秒、269032KiB。rule没有外部模型API调用，不能据此判断live模型成本。

任务成功不表示每次工具都成功：保护任务拒绝、早期probe失败和未选bid超时原样保留。a3 u2旧bid直到356.2sim s按原deadline失败后才返航，存在收尾等待。a2规划预计inspect约117s完成，30组goto/capture实际约200s；加减速、停稳和采集开销尚未校准，时间/能耗估计与规划最优性不列为已验证。

## 失败、修复与仍存在的时限边界

C01 seed42 a1因ready租约过期中止，真实调用4/21/209/3/6，u2文件120s期限失败；exit130后摘要只读重建。a2运行407.54秒，u4已本地delivery_verified，但carrier/receiver缺回centre中继路由，centre无终态。调用5/72/263/14/58；六进程exit0只代表有序清理，两run业务均NOT_PASSED。

修复按实际收到的无线ready重路由同ID消息，没有降低35dB遮挡。定点`mock-receiver-relay-recovery-03`中carrier原ACK71.4s、centre receipt ACK84.6s、confirmation91.2s实际收到；随后a3完成主案例。更早-01公告不足与-02驱动收件检查失败保留。

C04低能a1撤回消息没有送达，普通交付成功但换机目标失败。a2 centre82.6sim s收到withdrawal，96.6s确认旧u2取消，再报价选u4完成inspect；后续投送通知338.0s创建，经中继349.2s才到达u4，349.4s立即accept，但中心10秒报价窗口348.2s已关闭，348.4s结案unfulfilled_no_resource。这里是11.2秒通知延迟超过报价窗口，缺bid按unknown处理，不是已证明资源不可行。固定窗口未放宽；完整交付仍未完成。证据：`runs/mock/mock-C04-low-energy-42-a2/`的centre-state、transport-state和u4 decisions。

## 故障与业务分支的实际证据

C03缺片五DSH运行中，1137B JPEG经u2→u3→centre；message `9905f17252884a588ebaaf1ae7e13567`的chunk1在228.6s首发末跳丢失，只补发该片一次，236.4s ACK。文件ACK丢失run的message `215133379b6e42d8b34912090ab82059`在228.8s丢首ACK，两片均只发送一次，query1后234.2s补ACK。C07回执message `receipt-4a3294b2d68c5f79979f3e1a9314b2d8`在279.6s丢首ACK，285s补ACK且不重传数据。各run的mock-fault-injections、transport-state、packet-ledger保留完整因果。

直接FileClient组件还覆盖落偏无receipt、receipt同一chunk_index0跨两attempt共六次发送全丢后不确认，以及finalACK丢失补回后才确认。对应`mock-payload-outside-01`、`mock-C07-receiver_receipt_lost-01`、`mock-C07-confirmation_final_ack_lost-01`；后一run双方43.8s commit，carrier首ACK44.2s丢失，49.2s补ACK，centre50.4s才收到confirmation。

C02 no_person五DSH实跑30帧/30推理全0检测，241.8s unresolved_evidence；592条RPC无release，u2/u3返航、u4已在地面，最终281.2s。`negative-branch-analysis.json`确认阴性目标通过，通用runner terminal_failure仅指没有交付。

C02遮挡两组件位于`runs/mock-C02-uncertain-01/`：部分遮挡首帧35.6s已检测（score0.873）且可定位，同frame双输入因缺独立性返回uncertain；原工具规划实际换位，55s新帧25.50°视角差、0.503m位置差confirmed。完全遮挡36/54.4s两帧均0检测、verify uncertain，65.6s无线报告后完成guard拒绝，driver66.2s选unresolved，release0。完全遮挡无location时reobserve不可用，第二视角与结案由driver指定，该旧组件不证明自主补拍，后续独立五DSH结果如下。控制输入副本另覆盖NaN/超量程/多簇/配准/超40ms/旧pose/缺图深度，均带原因unavailable；实际缺块下载和缓存过期也被拒绝。

后续新增自主C02运行单列：`mock-C02-still-uncertain-42-a1`由五DSH在40m搜索6帧、再自主换30m搜索6帧，均0检测；104.8sim s真实uncertain报告抵中心并结案unresolved_evidence，release0，三机收尾完成，墙钟135.82秒。`mock-C02-occluded-42-a1`首视角45s检出score0.629，但73.6s独立视角漏检，82.2s准确unresolved、closure=true；未把它写成遮挡后确认成功。两run均保存uncertain-branch-analysis；partial a3后续真实完成：`mock-C02-occluded-bbox-los-42-a3`80.2s双视角confirmed（33.665°/0.1623m），157.8s中心completed_with_delivery、closure=true，墙钟252.04秒，六进程exit0。该修复在公共遮挡检查中加入已测bbox边缘支持点，解决只检中心点不足；阈值和renderer未改。

C05 `mock-C05-cancel-01`在3m/s水平速度下取消→canceled/HOLD为1.98sim s；queued请求未dispatch，取消中拒新动作，新rev2后拒旧rev1和旧command_id重放，新目标随后5s保持零速。

C08 queued真实新帧线索228s送达，234.6s发端收到queued；首事件326.2s交付后，第二无独立需求329.6s unresolved。expired分支227.2s收件、233.8s收到queued，按公开30s线索窗口249.6s准确expired，首事件331.2s交付、返航完成。两个run的queue-branch-analysis与queue-deadline-analysis记录来源和状态变化。

C08 noeligible a2 `mock-C08-no-eligible-clear-centre-42-a2`实际收到U2有效bid，soc0.18144低于固定0.2门槛，第二事件293s结案无资源；但u3/u4未收bootstrap，主事件250.6s已无资源，整个run incomplete/closure=false。a2另将公开centre节点移到[45,90,0]；缺失bid始终是unknown，没有用别机本地状态补入求解。此run提供低能bid排除证据，不列完整闭环通过，原a1输入新attempt完成，结果如下。

C08最终修复复跑`mock-C08-no-eligible-closure-fixed-42-a3`保持原a1公开输入，主事件323.6sim s交付，第二337.6s因未收到bid结案。三机先收到第二notice但接受仍pending，匹配closure到达后accept显式failed，不重开已关闭事件；u2/u3/u4分别432.2/346.6/353.4s返航着地并解除武装，六进程exit0，墙钟433.59秒。此run证明待接受消息遇关闭后的收尾修复；第二求解器输入为空，不能拼接a2低能bid成“已知全机无eligible”。证据：该run的closure-recovery-analysis及input-comparison。

C08调查还发现迟closure/notice边界（下列短组件覆盖closure先于notice，与上述a3的pending遇closure分别记录），后续`mock-closure-order-02/report.json`用真实无线控制顺序验证：非centre关闭被拒，closed事件迟到notice和pending接受被拒，重启保留closed记录，旧事件closure不打断无关open-C飞行。该短驱动指定消息顺序，单列为执行服务组件结果。

## 历史、技能接替与视觉组件

C06 `mock-history-c06-seed42-a3`实采12个不同RGB、12depth/足迹，冻结记录派生查询在双机worker重启前后各20/20 top5命中、5/5缺失正确空结果。旧空检测帧观察51.6s、初分析85.6s、重分析124.4s，原ID/hash/观察时间不变。另一机收无线文件前读取被拒，5747B文件送达和回传保留原observer/observed_at，radio_sender单列。重启恢复档案、收件、任务游标，coordinator106.4→108.4s持续推进。证据在history-query-summary、historical-reanalysis和evidence-integrity-audit。

21项技能已有实际调用：C01五DSH自主17项、C06组件补3项、双DSH handover补1项。`mock-handover-accepted-seed42-a2`证据文件29.6s已ACK，38.8s offer，40.6s新机accept，61.2/62.2s实际hold与双端probe完成，63.0s形成接任确认，旧机64.2s收到才release返航；84s新机仍held。18项流程及6项task/时限/位置检查通过。无授权/未接任保留责任分支也保留。该组件没有启动中心选接替策略。

C10视觉正式对照使用同38张固定开发photo-billboard图（31eligible、7阴性）：03 baseline有效24/31、FP3、precision0.889，水平median0.447m/P950.475m；05统一tile+内部裁边过滤有效31/31、TP31/FP0/FN0，median0.382m/P950.427m。score/IoU均0.5，每帧7views，推理median2.518s/P952.724s、总95.805s。04曾出现27FP/precision0.534，失败保留；02几何出FOV修订的历史也保留，正式对照不混入该fixture变化。此固定开发集不是heldout；tile未接默认生产，三seed旧run不追认使用此改进。另6组深度反投影及旧epoch拒绝有组件记录。

## 恢复、回放与交付状态

H04 `mock-H04-radio-rebuild-02`在持久5/6分片时重建RadioTransport，只补片1、8.8s ACK；已commit丢finalACK时重建，8s补ACK不重传。各5731B一致、commit1次、ACK后5s无重复、kernel/world未reset。此为构造器重建；真实u4释放后进程重启与30分钟存活结果如下。

耐久`mock-endurance-restart-u4-42-a1`实际运行1811.806墙钟秒/1810sim s，61次活性采样，最后一次1804.88墙钟秒，六进程exit0。中心302.6sim s交付后完成返航并等待；400墙钟秒后只有u3产生1408次wait-until，其余端无工具调用，因此结论是30分钟存活而非30分钟复杂救援。user/system CPU294.57/50.83秒，childRSS319604KiB不代表并发进程树总峰值。

u4于283.6sim s释放后、回执前有序terminate/wait再启动；原operation `e7f9d3e159884a6a9f3e5df905c687fb`从released恢复到delivery_verified，payload_release总计1次，coordinator保持。该操作不是突发崩溃；重启发生时源码有增量，未事前逐代捕获源码diff，不能称整轮单一冻结版本。证据：`runs/mock/mock-endurance-restart-u4-42-a1/endurance-restart-observation.json`。

耐久暴露的结束后wait空转随后修复。`runs/development/closed-relay-no-spin-02/report.json`使用复制的closed u3状态、真实DSH/worker及明确local file-IPC时钟fixture，12.13秒越过旧timer后旧pending succeeded，新工具调用0、proposal为空、exit0；期间仍有140次gateway state/inbox调用。它验证局部停止派发，未重做30分钟，原1408次wait成本不回改。

C09 `mock-replay-four-42-rule-a2`实际aerokernel.replay耗时37.505s；cut16012、终点/seal406.2s一致，48动作状态逐一匹配，65024facts/1713messages/3523receipts，新模型/物理/相机调用0，源未变。源a2失败结论保持。

新增C03/C07组件补验位于`runs/development/c03-c07-fault-components/`。失路后二次attempt实际经普通FileClient探测替代u2/u3/centre路由，同ID attempt2在8.4s ACK，5731B/commit1；无路径在13.6s公开截止失败，attempt0/无数据片/commit0；接受前丢分配耗尽2attempt、每次一片首发+2补发，task_accept明确拒绝未收消息。joint未释放组件在accepted后观察35sim s持续attached/released=false，无释放时刻/contact/回执。四项均为driver组件；后续五DSH结果分别列在下文。

C03接受前丢分配五DSH`mock-C03-lost-assignment-42-a1`在180sim s受限观察结束：plan `c3d130c5d26b4b769f0961cc1d593ff8`耗尽两attempt，每片初发+2补发，接收端无commit，u2保持原offer任务，未接受该巡查、无实际飞行搜索调用。协调服务180s观察horizon产生expired，不是原900s自然任务截止；整run incomplete保留。证据为case-branch-observation。

C04 `mock-C04-payload-unavailable-42-a1`五DSH实际完成无物资阴性：初始库存明确为0；u4报价235sim s观测、236.6s中心实收、有效至250s，中心保持10s窗口，于244s因有效库存0拒绝投送分配。释放0、返回完成、六进程exit0，墙钟279.45秒。terminal_failure是无物资的正确业务结案；该变体不是报价后动态卸货，动态撤回由独立低能运行证明。证据：resource-variant-analysis。

C04身份置换`mock-C04-role-permutation-42-a1`五DSH按实际能力与报价选机：10.2sim s选u3巡查，243.2s按实收新版报价选载荷1kg的u1投送，u4执行中继，u2保留受保护原任务。303.2s交付完成；u1/u3/u4分别333.0/396.8/325.8s实际解除武装，六进程exit0，墙钟398.28秒。结果证明角色不硬绑原u1/u2/u3/u4编号；原始resource-variant-analysis保留映射与每轮实收bid。

C03失路后二次attempt的五DSH运行`mock-C03-path-second-attempt-42-a1`完整交付：同一文件`15e4475ac6834069a79c2455af4defa6`在225.6→245.6sim s实际经历20秒路由中断（6个注入包），恢复后attempt2两片各发1次、248.8s ACK，324.2s交付并返航。工具调用6/53/201/19/47；原始case-branch-observation记录同ID、outage和receiver commit。

C03指定无路径五DSH`mock-C03-no-valid-path-42-a1`自然到原消息deadline：文件218.6sim s创建，无有效路径时保持attempt0/无数据片/无commit；338.6s即原120s期限失败，中心349.2s结案证据不足并完成返航。CLI1为预期阴性业务终态，工具调用5/62/220/5/48。没有用可靠旁路或共享源文件伪造送达；证据为case-branch-observation。

C07 joint未释放五DSH a2保留明确未完成：`mock-C07-joint-not-released-42-a2`最后939.8sim s执行器仍attached/released=false，无释放/contact/回执；业务deadline实际被世界horizon一同改成940s，940s提交最终状态查询后协调服务停止，没有后续状态回复。故该run只证明未假报释放，不证明900s自然失败终态或返航；记录为INCOMPLETE_BOUNDARY_CLOCK_CONFIGURATION，后续a4在分离业务截止与世界运行上限后完成复核，见下。

C07后续a3采用5倍推进实验，900s业务截止与1040s世界上限已独立；但工具/无线相对时序改变，u2证据和报告直连attempt0超时，中心901s过期，u4未到release分支。`mock-C07-joint-not-released-42-a3/case-branch-observation.json`记录NOT_REACHED_RELEASE_BRANCH，不作为1倍性能或关节终态验证。

真实模型当前阻碍：运行入口使用AAS_LLM_BASE_URL、AAS_LLM_MODEL、AAS_LLM_API_KEY；本执行环境三项均未绑定，尚未接入用户已有OpenCode/DeepSeek配置。核查仅查看变量名存在性，没有读取key值，不表示用户从未配置密钥。原真实API闭环要求继续保留。

C07最终1倍推进a4 `mock-C07-joint-not-released-42-a4`准确覆盖关节未释放负例：900sim s新执行器观测仍attached/released=false，工具failed/release_not_observed，物理释放0、无contact回执；中心业务expired。u4在902.6s真实收到关闭消息后，以独立903.4→1023.4s固定返航窗口收尾，原任务900s截止没有修改；u2/u3也各有独立关闭来源窗口。原始动作终态u3=923.0s、u4=930.4s、u2=933.6s着地并解除武装；较晚self快照不作动作完成时间。六进程exit0，墙钟935.13秒。结论为“不脱离不报交付，且安全收尾通过”，不是救援交付成功。原始case-branch-observation保留deadline、执行器、关闭来源和返回状态。

## 可复制运行入口

先按AeroRescue/README.md安装Python应用及Node Harness依赖，并按docs/mock-visual-asset.md准备照片资产。以下在AeroRescue目录运行；python指已安装应用的解释器，MODEL_CACHE和SPRITE分别设为本机模型缓存目录与已准备的照片素材文件。每次使用新的run-id，保留独立输出目录。

C01标准四机案例（无variant参数，默认完整场景）：

```bash
python tools/run_mock.py --scenario scenarios/rooftop-flood-mock.yaml \
  --run-id my-C01-seed42 --case C01 --seed 42 \
  --model-cache "$MODEL_CACHE" --person-sprite "$SPRITE" \
  --stop-on-outcome
```

C07关节未释放阴性分支：

```bash
python tools/run_mock.py --scenario scenarios/rooftop-flood-mock.yaml \
  --run-id my-C07-joint-seed42 --case C07 \
  --variant joint_did_not_release --seed 42 \
  --model-cache "$MODEL_CACHE" --person-sprite "$SPRITE" \
  --until-ns 1040000000000 --wall-timeout 1100 --stop-on-outcome
```

C07业务截止仍为900秒；1040秒仅是世界运行观察窗口，为900秒的新状态回复及独立返航预算留出时间。该阴性案例应记录未脱离、不报交付及真实收尾，业务状态为expired/terminal_failure，进程退出码不单独代表分支验收结论。以上为已实跑入口的可移植路径写法，不会复用报告中的旧run目录。

## 逐项验收与审批

[机器可读50项台账](mock-acceptance.csv)沿用原执行包编号。MOCK_PASS只评价本轮列明的软件范围；PARTIAL表示该范围尚有缺口，NOT_RUN/NOT_COVERED明确保留原任务。每项用户审批为PENDING_USER，只有用户明确决定后更新。原native台账没有改写PASS。

| 编号 | 原要求 | 本轮mock可核内容 | 当前结果 | Native仍待 | 用户审批 |
| --- | --- | --- | --- | --- | --- |
| REQ-D01 | 正确仓库与main基线 | 核对应用/平台分支、main基线及PR目标 | PARTIAL：应用视觉源码ecb1ae0已push至PR18；旧阶段文档592280a已push；平台196d1b1/PR17，均按main基线拆分，PR未合并。最终新增证据随本台账一并提交，PR待用户审批。 | 保留平台与应用PR最终审批；不自动合并 | 待审批 |
| REQ-D02 | 目标云机与资源 | 记录本机运行环境与mock进程资源；不要求mock代替Docker | PARTIAL：本机实际完成五DSH及mock coordinator运行并记录资源；Docker/GPU原生环境仍不可用。 | Docker/Compose、OGRE2/EGL及实际graphics能力 | 待审批 |
| REQ-D03 | 软件与资源锁 | 复用已安装实际依赖及本地模型，记录本轮实际使用路径 | PARTIAL：本轮实际使用现成Cordis/DSH依赖与真实ONNX/BGE；没有镜像构建记录，不增设形式hash门槛。 | 平台镜像构建与原生资产实际加载 | 待审批 |
| REQ-D04 | 可重建镜像 | 独立新run目录冷启动五个DSH及mock coordinator，不下载模型 | PARTIAL：a3新run/private目录启动五端并完成；没有容器构建/冷卷证据。 | 干净镜像构建、新空容器卷和离线镜像冷启动 | 待审批 |
| REQ-D05 | 共享世界与八服务编排 | 四机和中心连接同一mock coordinator，只有其推进时间 | MOCK_PASS：a3四机+centre实际独立DSH进程连接同一coordinator；完整执行并有序退出，六进程exit0。 | 八服务容器编排与真实共享Gazebo/ns-3世界 | 待审批 |
| REQ-D06 | 每机数据隔离 | 五端独立目录/身份，检查上下文仅本机与已收消息；单独尝试越权身份 | PARTIAL：FlightClient跨机身份请求被拒；C06另一机未收文件前read-evidence unavailable，实际无线接收后只读本机副本；往返保留原observer/observed_at，radio_sender单列。共享host/user未验证OS文件/socket隔离。 | 容器/网络隔离和Unix socket服务端原生身份绑定 | 待审批 |
| REQ-D07 | 退出与重启 | 唯一run拒绝覆盖；停止后保留证据；另run启动与原run恢复分别记录 | PARTIAL：a1/a2失败证据保留，a3/43/44独立目录；三次六进程有序exit0。 耐久run在283.6sim s有序terminate/wait重启u4，同一operation从released无receipt恢复到delivery_verified，release仅1次，coordinator不重启；不是崩溃/容器恢复，跨代源码未事前逐代冻结。 | 真实容器清理与原生后端重启恢复 | 待审批 |
| REQ-F01 | 救援实际使用PX4 | 复用FlightClient与任务接口调用显式mock飞行后端 | PARTIAL：a3真实FlightClient/kernel命令日志连接明确mock后端并完成任务。 | 实际kernel→PX4 goto_location与飞控执行 | 待审批 |
| REQ-F02 | 动作完成证据 | 记录accepted/pending/terminal及模拟估计随时间变化，不把提交当完成 | MOCK_PASS：三seed五DSH完整动作链保留accepted/executing/terminal；a3 u2/u3/u4返航着地并disarm，最后u2 kernel387.2sim s成功，u1原保护任务保持。C05另实测cancel终态1.98s，未将提交当完成。 | PX4真实估计和飞控到位/起降完成判据 | 待审批 |
| REQ-F03 | 真实取消与hold | mock运动中取消、停止及迟到回调不复活；独立故障分支 | MOCK_PASS：组件实际初速3.0m/s，cancel接受12.4→terminal14.38sim s，共1.98s；queued未dispatch即取消；取消中拒新动作；radio新rev2后拒旧rev1及旧低层command_id重放，后5sim s停新目标。 | 实测3±0.25m/s条件下5sim s内hold | 待审批 |
| REQ-F04 | 可行搜索和运输路径 | 实际公共地图规划、覆盖路线和投放选点算法，mock轨迹引用 | PARTIAL：a3实际search-area及deliver-supply经公共地图路线完成，投放点来自select-drop-site；最优性/覆盖完整性未校准。 | 原生飞行可达性、真实观察覆盖及落点安全 | 待审批 |
| REQ-F05 | 唯一时间推进与双时钟 | coordinator独占mock时钟、pending期间继续推进、epoch隔离 | PARTIAL：a3 coordinator独占mock时钟，异步DSH期间持续推进；C10新run/epoch对旧epoch pose join实际返回unavailable；C06停worker时coordinator106.4→108.4sim s继续推进。原生源时钟映射另待。 | PX4/Gazebo/ns-3源时钟映射与真实reset | 待审批 |
| REQ-F06 | 本机状态和代价来源 | 本机模拟状态→真实能耗/逐航段代价算法；过期状态保留unknown | PARTIAL：FileClient受控运行观察空中无活动指令时电量下降，另验证yaw180°改变相机；尚未覆盖bid成本/过期状态全链。a2运行观察：首plan预计inspect约117sim s结束，30组goto/capture实际约200sim s；声明duration=30s未校准加减速、停稳及采集全开销。规划最优性/时间与能耗准确性尚未验证。 | 实际遥测来源与原生航段时间/能耗测量 | 待审批 |
| REQ-O01 | 原生RGB与数值深度 | 生成明确标识的模拟RGB/depth/K并随mock视点更新 | PARTIAL：受控mock capture/read实际产生可读PNG；并非Gazebo图像且不是四机传感器验收。 | 四机Gazebo OGRE2/headless真实图像和数值深度 | 待审批 |
| REQ-O02 | 估计位姿与真值分离 | 严格分开mock估计输出与仅coordinator可见的模拟真值 | PARTIAL：后端明确mock_kinematic_model、mock epoch与独立renderer输入；源标签可核。未执行MAVLink/NED原点验证。 | 实际MAVLink time_boot_ms、原点和高度基准 | 待审批 |
| REQ-O03 | 坐标与深度配准 | 对声明模拟标定执行真实几何反投影与深度有效性逻辑 | PARTIAL：a2实际locate-target两次available；真实geometry处理模拟标定/深度，独立位置水平差0.0662m；误差指标仍未校准。C10显式optical_z/ray_range的6组反投影误差≤2.18e-15m。 | 原生传感器坐标标定及真实配准误差 | 待审批 |
| REQ-O04 | 采样时间对齐 | 检查模拟帧/估计源时间、过期和epoch拒绝 | PARTIAL：C10固定组件复测新run/epoch，旧epoch pose join实际返回unavailable；尚未测原生时钟40ms对齐。 | 原生RGB/depth/PX4源消息≤40ms对齐或有界插值 | 待审批 |
| REQ-O05 | 检测模型实际加载 | 实际加载已有ONNX/BGE并记录推理调用，禁止预填检测结果 | MOCK_PASS：mock-render-detector-02实际ONNX RT-DETR阈值0.5：40/30/25m模拟帧各检出1person，score=.687/.838/.894；不替代既有真实图等价或P/R指标。 | 既有真实图等价证据独立保留；本轮不新增等价结论 | 待审批 |
| REQ-O06 | 人员检测与位置指标 | 公开mock检测/定位实际输出及失败，不以模拟样本代替目标指标 | MOCK_PASS：固定03原38图/31eligible统一tile边界去重后05实测TP31/FP0/FN0，P/R100%，31valid(100%)，median0.382m/P950.427m；7views/帧，推理median2.518s/P952.724s。正式同图对照03→05：24/31→31/31、FP3→0；tile未接默认生产。仅该开发fixture达标，原独立留出/真实场景指标未验证。 | 固定留出原生场景P/R和≥30样本定位覆盖/误差 | 待审批 |
| REQ-O07 | 跟踪与换视角复核 | 不同模拟视点实际生成新frame，执行跟踪/几何复核 | MOCK_PASS：主案例两视角64.52°/0.0662m一致；旧C02组件25.50°/0.503m确认，完全遮挡第二视角由driver指定。后续独立五DSH持续遮挡自主6+6帧、两高度仍0检测，104.8s准确unresolved并收尾；partial a1独立视图漏检失败保留，a3在80.2s以33.665°/0.1623m confirmed，157.8s交付并收尾。 | 真实连续帧与相机运动/遮挡表现 | 待审批 |
| REQ-O08 | 缺图和无效深度 | 显式缺帧/无效深度/超范围分支返回unavailable，历史帧不冒新帧 | MOCK_PASS：C02控制输入副本：NaN/超量程/背景占优/双簇/shape/registration/40ms失配/stale pose/缺depth/缺frame均带原因unavailable；实际缺块下载拒绝、缓存过期不可用。无效副本与实际采帧分别记录。 | 原生相机故障和真实深度混淆 | 待审批 |
| REQ-M01 | 持久本机观察档案 | 真实本机SQLite/文件档案、来源和三类时间字段及重启读取 | MOCK_PASS：C06实采12张不同RGB及12depth/足迹；双机worker重启后档案、frame ID/hash、原观察时间、收件和workflow cursor恢复，coordinator持续推进。 | 原生采样档案全链覆盖 | 待审批 |
| REQ-M02 | 时空与中文语义检索 | 实际BGE/过滤/查询；只有执行完整20+5题才报告阈值 | MOCK_PASS：C06冻结记录派生的中文/时间/来源/空间查询，重启前后各20/20正例top5命中、5/5缺失正确返回空；没有伪造ID或过滤违规。该集合不是独立人工语义基准。 | 原生观察语料上的固定查询集指标 | 待审批 |
| REQ-M03 | 真实证据裁剪压缩 | 真实图像裁剪/压缩、可解码文件及字节预算/hash | MOCK_PASS：a2真实package-evidence生成1137B JPEG，33×48，可解码，SHA256与manifest一致，预算65536B；报告方实际重新读取解码/hash核对。 | 原生图像来源的端到端传输证据 | 待审批 |
| REQ-M04 | 新旧证据比较 | 实际旧帧读取/重新推理与新旧比较，原ID/观察时间不变 | MOCK_PASS：C06实际compare-observations/read-evidence/retrieve-sightings；原空检测帧51.6sim s观察、85.6s初分析、124.4s重分析，保持原ID/hash/观察时间；未观察到不推断离开。 | 原生连续观察和历史无检测帧覆盖 | 待审批 |
| REQ-C01 | 全部救援业务过无线链路 | 所有业务走现有RadioTransport与mock链路事件，不直接投递业务结果 | MOCK_PASS：三seed全部notice/bid/plan/report/file/ACK/receipt经RadioTransport实际完成；handover双DSH组件证据先无线ACK，旧机实收业务接任才释放，未直接写业务成功。 | 所有消息实际经ns-3传播 | 待审批 |
| REQ-C02 | 真实文件字节和分片 | 实际字节分片、hash及mock packet/attempt/commit关联 | MOCK_PASS：FileClient实际发送5731B PNG、6分片；两故障run均centre收到5731B且byte_equality=true，原始packet日志可关联。该5731B记录来自FileClient组件。 五DSH C03缺片闭环实际完成：1137B图经u3，chunk1首发丢失后仅该片补发1次，236.4sim s ACK、313.8s中心交付、返航完成。 | 原生packet逐跳与末跳delivery证据 | 待审批 |
| REQ-C03 | 接收重组和应用ACK | 真实重组校验/落库/ACK代码；丢ACK分支需另run证据 | MOCK_PASS：FileClient故障run中最终ACK被实际mock drop，8.2sim s恢复ACK且状态succeeded；缺片run于9.0sim s收到ACK。H04构造器重建保留同ID/去重，commit仅1次且ACK后无重复。C07首finalACK丢失后也恢复并排入confirmation。 五DSH C03缺片闭环实际完成：1137B图经u3，chunk1首发丢失后仅该片补发1次，236.4sim s ACK、313.8s中心交付、返航完成。 | ns-3上的应用ACK时序与丢包恢复 | 待审批 |
| REQ-C04 | 中继有效及保持 | 声明mock链路模型、真实probe和中继候选/保持流程 | PARTIAL：a1租约/a2回传路径失败保留；修复后a3中心实际完成交付且u3收到closure后返航succeeded。双DSH合法交接旧机等待新机真实hold及双端probe，64.2s实收接任才返航；移位全分支待。 | ns-3实际路径可达性与移动后的无线效果 | 待审批 |
| REQ-C05 | 分片恢复与幂等 | 显式丢片/丢ACK故障下实际重试计数、deadline和持久去重 | MOCK_PASS：缺片只补缺片，丢文件/回执ACK只query补确认，失路20s后同ID attempt2成功；对应五DSH均有终态和收尾。接受前丢plan两attempt各片初发+2补发后失败；H04重建去重commit仅1次。原120s期限不延长，所有重试均有逐包计数。 | 原生无线损伤条件下恢复与幂等 | 待审批 |
| REQ-C06 | 无链路时不伪造成功 | 显式断链时保持pending/失败，不制造收件与ACK | MOCK_PASS：指定五DSH no_valid_path：文件218.6s创建、原120s期限338.6s到期失败，attempt0/无数据片/无commit；中心349.2s准确unresolved并返航。接受前丢分配五DSH两attempt每片最多2补发，无commit/u2未accept/未巡查；该run180s受限观察，整场未完成且不代表900s自然截止。 | 原生不可达链路及无可靠旁路核对 | 待审批 |
| REQ-P01 | 包裹物理实体 | 声明mock包裹/接收面几何、质量和挂载状态 | PARTIAL：受控mock包裹按声明几何落在接收面内/外；接触来源明确mock_contact_model。尚无原生实体。 | Gazebo物理实体、真实挂载及Contact传感器 | 待审批 |
| REQ-P02 | 释放真实生效 | 真实release请求触发mock分离/重力轨迹而非直接写成功 | MOCK_PASS：a3、seed43/44均由真实DSH调用release，经mock分离和重力轨迹形成接触，再完成交付；受控outside分支实际落偏且无receipt。 | 原生DetachableJoint动作与自由运动 | 待审批 |
| REQ-P03 | 交付后置条件 | 模拟接收区接触回执与双ACK；新标记箱体图像ID+深度定位，区分旧site-only检查 | MOCK_PASS：旧a3/43/44仅接触回执+双ACK及接收位置入镜，不证明看见包裹。新mock-C01-package-visual-42-a1实际282s释放→285s回执→285.6s新RGBD→286.6s解码公开标记7并定位[60,60,15.3]→301.4s中心收到相同observation并结案；306次DSH调用、396.66s墙钟、三机返航及六进程0。仅确认标记箱体位于接收区。 | 人员领取/长期留存未观测；0.75m高度容差未标定；中心仅核报告因果并不重跑识别；原生接触/相机/无线及落区速度评价 | 待审批 |
| REQ-P04 | 投放异常不报完成 | 未释放/区域外/短接触/回执丢失分别运行，不报交付完成 | MOCK_PASS：落偏无receipt、receipt丢失不确认、ACK恢复才确认均有组件或五DSH实证。joint五DSH a4在900s新观测仍attached，failed/release_not_observed，物理释放0/无contact；业务expired，固定独立返航窗口内三机着地解除武装。a2世界/业务截止耦合未完成、a3五倍未达分支均保留。新short-contact-02真实工具链组件：500ms接触后受控速度冲量离台，回执/确认0；持续1s对照产生回执及确认。两支各释放1次、返航成功；非五DSH自主。 | 对应真实物理异常分支与重启不重复释放；组件短接触不代表原生接触源 | 待审批 |
| REQ-T01 | 本地参与与中心有限证据 | 中心仅用真实收到的mock radio bid与有效逐航段cost求解 | MOCK_PASS：a3中心实际保存已收bid与selected_bids message_id/commitment_version/有效期，收到1receipt和36reports后完成；成本估计偏差见F06。 C04初始载荷缺失五DSH：实收u4库存0的有效bid后244s准确无资源，释放0、closure=true。不是after-offer动态卸货，terminal_failure为正确阴性结案。 C04身份置换五DSH：10.2s按bid选u3inspect，243.2s选载荷1kg的u1deliver；u4relay、u2保护原任务，303.2s交付并全收尾。 | 原生无线收件和遥测成本输入 | 待审批 |
| REQ-T02 | 单一任务和动作owner | 五个真实Cordis/DSH唯一派发owner；Python只执行接受的task/revision | MOCK_PASS：a3五端Cordis/DSH唯一派发，Python执行task/revision；C05新rev2接受后拒迟到rev1，取消中拒新动作，旧command_id重放被拒。 后续短radio顺序组件实拒非centre关闭、closed事件迟notice与pending接受，重启仍记closed，旧closure不打断无关open-C飞行。 C08原a1输入a3主323.6s交付，第二337.6s无bid结案；notice先到但accept仍pending，closure使接受failed，三机未接第二任务、432.2/346.6/353.4s disarm且closure=true。此run未证明已知全机不可用。 C04身份置换五DSH：10.2s按bid选u3inspect，243.2s选载荷1kg的u1deliver；u4relay、u2保护原任务，303.2s交付并全收尾。 | 真实动作后端单owner及legacy engine停用证据 | 待审批 |
| REQ-T03 | 承诺与任务恢复 | 实际承诺版本变化、无线撤回、旧bid失效和任务恢复 | PARTIAL：a3保留保护任务并正确返航；C04a1撤回通知丢失失败保留。C04a2实收withdrawal82.6sim s、取消确认96.6sim s并换u4 inspect；后投送bid迟到，348.4sim s终态unfulfilled_no_resource，case尚未完整交付。新增同源配对：静态撤回后不重规划，96.6s failed_execution；在线恢复u4巡查但346.4s unfulfilled_no_resource，两支无投送且安全返航；不证明LLM必要性。 | 原生资源变化下抢占/取消/恢复 | 待审批 |
| REQ-T04 | 事件发现与多事件排队 | 由模拟新观察触发第二线索，检查事件排队和不双重分配 | PARTIAL：C08 queued由实际新帧/RT-DETR几何线索触发：centre228sim s收线索，234.6s发端实收queued通知；首事件326.2s交付后，第二事件329.6s因无独立需求结案unresolved_evidence。 C08 expired实收227.2sim s、queued通知233.8s、公示deadline249.6s准确expired移出队列；首事件331.2s交付，非protected三机返航，closure=true。 noeligible a2实际收到U2 bid soc.18144低于.2被排除，第二事件293s无资源；u3/u4未收bootstrap，主事件未交付、全run incomplete，保留部分证据。 C08原a1输入a3主323.6s交付，第二337.6s无bid结案；notice先到但accept仍pending，closure使接受failed，三机未接第二任务、432.2/346.6/353.4s disarm且closure=true。此run未证明已知全机不可用。 | 原生第二事件发现与完整多事件执行 | 待审批 |
| REQ-T05 | 业务终态准确 | 逐run记录人员证据/需求来源/交付/业务终态，失败不改成功 | MOCK_PASS：C01三seed交付并收尾，旧a1/a2失败保留；C02无人、持续不确定五DSH均准确unresolved且无释放，遮挡a1漏检未交付，a3独立视角确认后157.8s交付；旧组件driver结案不计自主。C08排队后无独立需求unresolved、249.6s公开deadline expired均有实证。C08关闭修复a3主323.6s交付、第二337.6s无bid结案并完成返航；低能bid排除a2仍是独立不完整run。C04撤回换机成立但报价迟到，348.4s未交付。未跑的指定variant由E02列出。 C04初始载荷缺失五DSH：实收u4库存0的有效bid后244s准确无资源，释放0、closure=true。不是after-offer动态卸货，terminal_failure为正确阴性结案。 C07未释放900s实观测失败，业务expired但安全返航完成，无假交付。 | 原生完整救援与所有阴性/无资源/过期分支 | 待审批 |
| REQ-H01 | 规则Agent跑真工具 | 五个真实DSH rule实例自主选择并调用共享工具，记录请求/完成序列 | MOCK_PASS：a3五个真实DSH自主派发完成交付及收尾；调用u1/u2/u3/u4/centre=6/49/254/19/43（合371），非CLI预排工具。seed43也实际completed_with_delivery（300sim s）且closure=true，墙钟395.01s。seed44也completed_with_delivery（304.4sim s）、closure=true；三seed均按各自当时源码运行。 | 同工具连接真实PX4/ns-3/物理载荷的完整rule闭环 | 待审批 |
| REQ-H02 | 模型Agent实际闭环 | 本轮计划仅rule；不把本地rule adapter说成外部模型调用 | NOT_COVERED：本轮live未跑；当前运行环境未绑定launch实际使用的AAS_LLM_BASE_URL/AAS_LLM_MODEL/AAS_LLM_API_KEY变量，未接入用户OpenCode/DeepSeek配置。仅检查变量名存在性，未读取key值，不代表用户从未配置。原真实模型验收保留。 | 真实模型完成C01 seed42及C04改计划分支 | 待审批 |
| REQ-H03 | 异步作业不阻塞推进 | 真实跨进程pending→DSH idle→completion followup，mock时间继续推进 | MOCK_PASS：五端完整运行实际pending→DSH idle→completion followup继续推进；a3记录371次调用及各agent completion序列，保留取消、bid超时和失败；部分结束时挂起请求不冒称全部成功。 | 原生后端连接与推进在异步期间保持活性 | 待审批 |
| REQ-H04 | 重启后对账 | 真实DSH/worker恢复，对账持久请求与任务版本，不重复执行 | PARTIAL：真实RadioTransport构造器重建两分支：缺片5/6时重建后仅补片1、8.8sim s ACK；commit后丢finalACK重建、8.0sim s补ACK且不重传数据。5731B一致、commit1次、ACK后5s无重复、原预算/epoch保持；不是进程重启。 C06实际双机worker重启恢复收件/档案/任务游标，coordinator不重启且持续推进。 耐久run在283.6sim s有序terminate/wait重启u4，同一operation从released无receipt恢复到delivery_verified，release仅1次，coordinator不重启；不是崩溃/容器恢复，跨代源码未事前逐代冻结。 | 原生连接/执行机构/clock epoch重启对账 | 待审批 |
| REQ-H05 | 可执行Skill清单 | 21个manifest实际被DSH加载，逐技能记录本轮真实调用及未调用项 | MOCK_PASS：21/21业务技能已有实际调用证据：a3五DSH自主17种，C06组件补3种历史技能，两DSH handover组件补接替。交接证据先ACK29.6s，实际hold/双端probe61.2/62.2s，旧机64.2s收到业务接任才release返航；未宣称中心自主选择接替。 | 每项所需原生工具调用及真实样例补齐 | 待审批 |
| REQ-E01 | 原始证据可追溯 | 唯一mock run_manifest标明backend，原始日志与工具/帧/包/终态ID关联 | PARTIAL：逐run manifest、DSH请求/完成、帧/定位/hash、逐包ACK及业务终态可关联，失败原样保留；稳定45run主卷已归档，最后增量卷归档由交付索引定位。 | 原生源证据完整追溯且真值不回灌 | 待审批 |
| REQ-E02 | 固定案例覆盖 | 列出实际运行的mock case/seed/variant，不把未跑项列覆盖 | PARTIAL：C01三seed；C02无人/持续不确定/遮挡复核；C03直阻中继、缺片/丢ACK、失路后二次attempt、无路径均有五DSH结果，接受前丢分配为180s受限观察。C04保护任务/低能撤回/载荷缺失/身份置换均已跑，低能整场未交付。C05/C06/C09/C10有组件实证；C07异常含joint a4准确未释放并返航；C08排队/过期/关闭收尾已跑，低能a2与a3不得合并。真实model/native条件保留。 | 完整C00–C10/variants及要求的rule/model seed集合 | 待审批 |
| REQ-E03 | 资源与持续运行 | 记录本轮真实墙钟时长/峰值/崩溃；不足30分钟不写达标 | MOCK_PASS：单run1811.806墙钟秒/1810sim s、61次活性采样、全六进程exit0；302.6sim s交付后返航等待，400墙钟秒后仅u3有1408次wait-until。CPU user294.57/system50.83s，childRSS319604KiB非并发总峰值。该通过限30min存活，不是30min持续复杂决策；live成本未测。 后续closed状态真实DSH+worker短跑12.13s，旧pending完成、新工具调用0；不回改耐久1408次成本。 | 四机原生30墙钟分钟持续运行及资源峰值 | 待审批 |
| REQ-E04 | 离线回放 | 从本轮保留mock记录只读回放，与新run冷启动区别 | PARTIAL：实际aerokernel.replay用时37.505s：cut16012，终点/seal406.2sim s一致，48动作状态吻合；65024facts/1713messages/3523receipts；新模型/物理/相机调用0、源未改。源a2仍NOT_PASSED；完整冷启动另待。 | 原生保留run离线回放与完整容器冷启动 | 待审批 |
| REQ-E05 | 交叉复核与PR | 逐50项证据与独立审阅、最终用户逐项审批 | PARTIAL：原50项ID/标题保持，逐项本轮范围/证据/原生待办与审批栏已完成；独立mock-review保留。用户审批全部PENDING_USER。 | 未完成原生项继续保留原状态并待用户决定 | 待审批 |

## 仍需审批和补验的事项

本轮指定mock运行已终结，逐项结果和失败均保留。以下限制继续明确列账：

- 原生PX4/Gazebo/ns-3、容器构建和隔离、真实传感器/物理条件尚未验证；真实模型环境未接入，原要求未取消。新标记视觉仅确认箱体位于接收区，人员领取和长期留存未观测。
- C04低能撤回与换机成立，但后续通知超过10秒报价窗口，整场未交付。C08低能bid排除a2整run不完整；a3完整关闭收尾的第二事件没有收到bid，不能合成已知全机无eligible的一次运行。
- C03接受前丢分配已证明未commit、未接受且不启动巡查；其180秒受限观察不代表原900秒自然任务截止。
- 视觉改进在固定开发照片集上通过，独立留出集和原生指标未测。飞行时间/能耗代价未校准，不声称最优性。
- 30分钟耐久为实际存活与任务后等待，非持续复杂决策；有序重启不等同突发崩溃恢复，跨重启源码未逐代事前冻结。

用户可逐项选择接受本轮范围、要求修正或保留待办；所有审批栏当前均未填写。
