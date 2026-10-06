# Observer 多模态图世界模型实施方案

Qwen 条件动力学与共享谓词风险头

技术方案与验收契约  2026年10月6日

## 结论与本轮决定

保留两阶段主架构：Stage 1 将带 Observer 证据来源的历史图编码给 Qwen3.5-2B-Base，预测联合状态分布并抽样 K=8 条、每条 10 步的未来图轨迹；Stage 2 从零训练一个小型、按 query 条件化的 Graph Transformer，供所有谓词共享，逐轨迹输出 11 个首次翻转桶，再对概率取均值。精确 RULEMC 与学习头并行保留，用相同轨迹区分动力学误差、规则近似和风险校正。

当前先完成有界原型，验证数据契约、因果边界、联合采样和冻结损失。首版不启用完整图像或点云生成，不将跨阶段永久共享权重列为默认。Qwen 的价值、多模态的增量和 5 秒事件概率的可靠性，都要由同信息边界的实验确认。

## 固定契约

历史为 t−10s 至 t 的 21 点，间隔 0.5s 且包含双端点；未来为 10 步、5s。第 1…10 桶表示对应 tick 首次翻转，第 11 桶表示窗口内未翻转。先逐轨迹 softmax，再 mean-K，最后计算已知 CDF 平方误差 j=1…9 ＋ 0.5×已知终点 BCE j=10。Stage 1 版本冻结后训练 Stage 2，默认跨阶段 untied。

## 旧 820 诊断的证据范围

旧实验使用 H8／3.5s 历史、F10／5s 未来、seed=0；1640 个 TRAIN 样本遍历一次，820 次更新，820 个 VALID 样本。其 future-truth＋Gaussian 数值图诊断说明 K 分支路径可训练；未重跑 TEST 或外部基线，也未验证本方案的多模态世界模型。[1]

| 原报告指标 | L 通信时延 K1 → K8 | P TTL 未及时交付 K1 → K8 |
| F1 | 0.7677 → 0.9870 | 0.4858 → 0.5470 |
| RPS | 0.02633 → 0.01605 | 0.04704 → 0.03550 |
| 时间误差 s | 0.9925 → 1.0042 | 1.1301 → 1.2006 |

K8 的 F1／RPS 改善，时间误差略升。L 对应 latency_ms；P 的字段名为 packet_loss_ratio，但实际语义是 TTL 窗口内未及时交付比例，不能当作纯 PHY 丢包。两者来源为 ns-3 packet stats，事件仍按原规则定义。

旧 820 实验继续封存。新的 H21 数据契约待采集／接通，不能从旧 H8 凭空补出。已有 typed-graph LoRA/head 原型是工程起点；限定原型正在实现，本稿不报告未经完成的新训练结果。

:::page landscape
# 两阶段系统结构

:::figure overview

图 1  推理实线、监督虚线与可选生成分支。完整图是带状态、关系、mask 和来源语义的接口；开放世界实体出生尚需另行建模。独立 SVG 可放大查看。

:::page
# Observer 证据与图数据契约

## 观测归属与实体关联

每次图像、点云扫描或日志窗口归属于一个 Observer，保留独立 observation_id 与 (observer_id, timestamp, modality) 键。同一帧 scene tokens 只保存一份；实体通过 ROI、mask、point indices、射线或空间 token 引用取得局部证据。复制整帧向量给多个实体不会产生不同的局部观测，也不能按实体数重复累计证据。

Observer 保存传感器类型、标定版本、内外参、量程与安装关系；每次 Observation 保存采集时间、到达／可用时间、位姿及不确定性、有效性、原始数据引用。移动相机的位姿属于必要条件。未归属实体的背景仍保留在 scene tokens，避免丢失遮挡物、区域边界和潜在新进入物体。

Entity 保存稳定 ID、类型、字段化状态、单位、坐标系、有效 mask、来源、置信度／协方差及距上次观测的时间。区分直接测量、融合估计、预测值与仅用于 label 的仿真真值。日志以结构化数值或带出处的文本输入；无法归属某实体的日志进入全局 context。

## 边的语义不能混用

potential_coverage 表示基于合法位姿和当前 belief 的几何覆盖候选；observed_by 表示已经取得并关联的证据。可覆盖、无遮挡、实际检出和身份关联正确是不同条件。没有本帧证据的遮挡实体可沿用历史 belief，但不能凭隐藏真值补 bbox 或点集。身份不确定时保留候选权重或 unknown。

physical_relation 记录距离、接触、从属等物理关系；temporal_link 连接同一实体或 Observer 的历史状态，并携带实际时间差。多相机融合保留各证据的年龄、质量和关联分数，不能把重复帧或共享跟踪错误当成独立证据，直接乘置信度。

## 时间与可用性约束

以预测 cutoff 前实际可得信息判定输入合法性，同时记录 event_time、available_time 和编码窗口。双向视频 encoder、离线跟踪、平滑、插值可能把未来帧写进“历史”特征；只检查输出 timestamp 不足以排除泄漏。事后生成的事故日志、未知未来控制、未来真实可见性和关联边不得回填输入。

训练、验证和测试按 episode／场景／时间段分开；相邻重叠窗口不能随机分散。归一化和缺失处理仅拟合训练部分。初始谓词值、规则操作数或必要关联未知时必须标记；未知、未检出、缺记录和窗口内未翻转不能共用一个标签。

:::page
# 从多模态历史图到 Qwen token

:::figure panelA

## 编码路径

图片优先复用 Qwen3.5-2B-Base 的原生视觉 encoder，首轮冻结；点云使用独立 point／voxel／range encoder，之后投影为局部与场景 token。官方 Qwen 配置有图像编码器，没有原始 XYZ／LiDAR 编码器。Point-BERT 等工作说明专门点云编码可行，但其对象尺度预训练不能直接保证大场景 LiDAR 适用。[2][5]

数值按字段标准化，经带 mask、time-gap 和 type 的 MLP 编码。typed 图融合先在同一时刻传递观测证据与实体关系，再形成 entity／scene／subgraph tokens。图宽 256、Qwen 宽 2048、每相机每 tick 8 个 scene tokens 都是预算起点，尚未证明充分。

## 子图压缩与序列接口

重叠子图使用同一全局实体索引，并定义重复节点的融合与归一化；跨分区关系、长距离任务关系和 query 端点必须保留。实体重编号时同时重映射边和 query；比较重排与分区变动，检验输出是否依赖任意顺序。

projector 产生 [B,L,2048] 的连续 token，显式加入时间、类型、实体／Observer 绑定与几何信息，再传入 inputs_embeds。Qwen 的 2B 文本骨干为 24 层，含 18 层 DeltaNet 和 6 层全注意力；因果序列顺序不会自动变成图对称性。先做切片内图融合，再用末尾 entity query 或外部 cross-attention 读出，避免前排实体看不到后排实体。[2][3]

图邻接不能直接假定为全部 Qwen 层的通用 attention mask。新建图 encoder、projector、分布头需要训练；只调整末层通常不足以让陌生连续输入对齐。LoRA 覆盖须按固定 revision 的真实 named_modules 检查，避免 all-linear 误改计划冻结的视觉塔。[3][4]

:::page
# Stage 1 联合未来图分布

:::figure panelB

## 将联合性写进分布与采样器

首个可计算候选是场景级混合模式加低秩高斯残差。每个模式输出状态均值 μ、正尺度 σ 和跨实体／字段低秩因子 F，使协方差为 diag(σ²)+FFᵀ；M=4、rank=4 仅是起步超参。离散存在／类别使用同一场景潜条件下的分布头，确定性几何边按样本状态重建。该结构为相关性提供通道，不保证已经覆盖真实交互或尾部模式。[6]

每条轨迹沿用自己的上一图和隐状态，再抽样下一步。若整段固定一个场景模式，训练似然也必须按整段模式求和；不能训练逐步独立混合，却在推理时强行锁定模式。未知未来行为需预测／采样后边缘化；已知未来控制才作为条件。纯观测数据首先支持观测条件预测，不能据此声称反事实干预已正确。

## 首版实现选择与限制

首版可将历史交给 Qwen 编码一次，再由共享 graph transition decoder 逐步推进。每一步重新调用 Qwen 是另一个实现与成本假设，需单独记录和比较。状态分布应以有效字段 NLL 为主训练，缺失字段使用相应观测子维度的似然；零填充不能替代未知值。

图头至少覆盖已有实体的数值、存在／离散状态及必要关系。若初版固定 N 个已知实体、不处理出生，验收中须保留这一限制。未来 Observation 边若参与推演，需由模型预测或由合法可观测性模型构造，不能沿用未来真值。一个均值轨迹只给出点预测事件，不能当成概率分布。

:::page
# Stage 2 共享查询头与冻结损失

:::figure panelC

## 查询条件化与精确操作数旁路

Stage 2 输入一条未来图轨迹、G0／初始真值和 query。query 指定规则、实体／角色、阈值、区域、持续时间与有效性。模型先处理同 tick 关系，再沿实体时间边汇聚，由 query 对绑定节点／边读出 [B,K,Q,11]；所有谓词共享网络。

令 rⱼ 为原规则的布尔真值，y 是最早满足 rⱼ≠r₀ 的未来步；10 步均不翻转为 11。持续时间／滑动窗口规则还需 cutoff 时刻的计时器或必要历史；q0 本身不足，初始规则记忆必须进入接口。

数值旁路按 query 保留各 tick 的原始操作数、阈值差、mask 与真实时间。小型 Graph Transformer 可从宽度 128／256 起步；它无需成为第二个 Qwen。旁路能消除一类压缩瓶颈，但不保证网络学会规则，仍需精确 RULEMC 对照。

## 损失的精确定义

p̄=meanₖ softmax(logitsₖ)，Fⱼ=Σᵦ≤ⱼp̄ᵦ，cⱼ=1[y≤j]。主目标固定为 L=Mean_known[(Fⱼ−cⱼ)²，j=1…9]＋0.5×Mean_known[BCE(F₁₀,c₁₀)]。两项各按自身已知项分母归一化，mean-K 只做一次；mask、空集与 clamp 复用冻结实现并回归比对。[1]

j=1…9 对应 0.5…4.5s；j=10 为 5s 终点。报告 RPS 另取 j=1…10 的全部已知 CDF，不能与训练 timing 项混名。删失后的未知项不能作负例；已发生 first-flip 时事件已知。0.5s 网格也可能漏掉两 tick 之间翻转后恢复，若需连续时间事件必须另定标签协议。

:::page
# 学习头语义与跨阶段复用

## 同一组轨迹保留两种清晰语义

精确 RULEMC 对每条假设图执行原规则，将所得单桶标签统计成频率。规则操作数缺失时，报告不可判定率与可判定查询范围；不得记为第 11 桶，也不得悄悄剔除样本后重新归一化。已知确定规则在充分状态上的逐轨迹真值应为 one-hot。可选的规则代理 Graph Transformer 用每条真实／预测／扰动图自己的规则标签训练，检验网络能否忠实近似规则。

主方案是混合风险头：在 K 条 softmax 概率平均后，使用实际未来的原规则标签计算冻结 hybrid。该目标允许学习头校正 Stage 1 的偏差；它不要求每条假设未来都等于实际未来，也不能把每个分支的 softmax 解释为该假设图的精确规则真值。真实事件定义保持不变。CE／NLL 可解释混合目标或用于评价，本方案不以其替换已选 hybrid。

来自目标分布的等权样本可取均值；相关性改变估计方差，重要性采样才需要相应权重。K=8 的硬 RULEMC 概率粒度为 1/8，11 桶至多 8 桶非零。独立二项边际的最坏标准误差约 0.177，仅表示采样误差量级，不包含模型偏差。soft head 使概率连续，不会自动补回未抽到的尾部情形。

## 能预测状态的表征是否足够判断谓词

若历史 H 的压缩 Z 满足 P(S_future|H)=P(S_future|Z)，且完整保留 G0 与 query，那么确定规则 T=R(G0,S_future,q) 的条件分布也保留。确定规则映射不会放大两联合分布的总变差距离。这一结论要求完整条件分布充分；小 state-MSE 或 latent-MSE 并不提供该保证。

例如距离阈值为 2m，1.999m 与 2.001m 仅差 2mm，却给出相反真值。动力学需要历史速度、控制和遮挡记忆；未来碰撞判定可能只需位置、尺寸和时间。两阶段最小充分因素通常不同，罕见越界、相关误差与持续时间尤其不能由平均误差代替。

## 默认复用结构 不永久绑定权重

主线采用 scratch Stage 2。复用 schema、实体索引、单位和图模块结构有工程依据；复制 Stage 1 权重后独立训练是一个后续消融。冻结共享底层加阶段 adapter 也是候选。训练 Stage 2 若改动同一份可训练 encoder，会改变 Stage 1 及其轨迹分布，使缓存失效；全量 weight tying 须作为新联合版本重评估。

节点重编号等变性通常可复用，但 query 也需重映射。相对距离适合平移／旋转不变性；绝对高度、地理区域、朝向和截止时间需要保留绝对量。图结构相似不能推出所有不变性或压缩瓶颈都应相同。

:::page
# 可选未来观测生成与文献边界

## 数值状态与可解码观测分层

完整未来图像或点云需要足够的 scene／appearance／entity latent，加未来 Observer 位姿、标定、模态和专用 decoder。单个 entity token 缺少背景、其他实体与遮挡信息；Qwen 的视觉 encoder 和词表 LM head 也不自带通用图像／点云解码能力。未知传感器未来位姿必须预测或采样，并单独归因其误差。

任务 latent、entity latent、camera-specific observation latent 与可解码 latent 可以使用不同空间和 adapter。若训练观测 latent 预测，需冻结或版本化 target encoder，记录 target 的时间支持，并防止表示塌缩。不同模态的 latent-MSE 不能直接比较。

生成分支依次验收：真实观测编码再解码的上限；同一 decoder 下真实未来 latent 与预测 latent 的差距；多视角身份、重投影、深度与数值状态／谓词的一致性。画面清楚、FVD 下降或点云 Chamfer 变好，都不能单独证明事件预测可靠。

## 文献提供组件依据 不提供本系统的现成验证

DreamerV3 用随机 latent 与递归状态按动作预测，并设置观测、奖励等读出，支持“信念动力学加多读出”的分工；它没有现成 Observer 与实体的证据图。[7] V-JEPA 2-AC 在冻结视觉表征上加入动作和状态预测未来特征，论文另训 decoder 做可视化；这不能推出任意语义 embedding 可逆，也不能由其单相机机器人结果外推十相机融合。[8]

SlotFormer 展示对象 slots 的联合时序动力学，强调身份与交互；slots 不自动等于业务实体 ID。[9] Drive-WM 用布局、动作和多视角条件生成视频；条件布局不等于模型已自主预测布局。[10] Copilot4D 使用 LiDAR tokenizer、离散扩散和点云读出，并依赖未来 ego pose；其评估范围不能直接证明本方案的 5s 多模态精度。[11]

C-SWM 和 GWM 已包含对象图动力学或多模态图与语言模型组合，“图＋多模态＋LLM”本身不能作为首创结论。[12][13] 近期 World Observer 联合 actor／observer 视频流维护视野外动态，但其生成视角不是新增真实传感器证据，也不是显式实体数值图。[14]

值得检验的研究问题是：在部分可观测、时间不同步和关联有误时，显式记录证据来源、局部关联与未知状态，是否比同预算数值图／简单融合改善 5s 联合状态和事件校准。新颖性仍需进一步系统检索，不能由模块命名代替。

:::page
# 分阶段实施与版本边界

## 阶段 A  数据和无训练闭环

先冻结 Observation／Entity／edge schema、规则参数、单位与坐标系、cutoff 逻辑、21 点历史／10 步未来／11 桶定义。用 tiny fixtures 接通编码占位、10 步采样、query 绑定、RULEMC 与 mean-K 后 hybrid。覆盖当前已真、遮挡未见、记录不足、tick 间短暂翻转及实体重编号。

产物是数据检查器、张量契约、可重复随机种子、fixture 标签、损失回归与小样本资源记录。它验证接口是否自洽；没有训练的随机模块不能以该结果宣称预测有效。若现有数据只有 H8，先报缺口并制作新 H21 数据入口，不悄然填造历史。

## 阶段 B  世界模型训练准备与训练设计

固定训练／验证／测试 episode 清单与输入可用边界。冻结模态特征 encoder，训练图融合、projector、状态分布头和受限 Qwen LoRA。先做 teacher-forced 一步似然与有限值检查，再评估完全 open-loop 的 0.5、1、2、3、5s；逐步喂真实未来图的分数不能冒充自主推演。

数值字段采用 schema 对应的归一化、正值与角度处理；存在／类别按合法分布监督，不添加未证实适用的守恒约束。保存图 encoder、projector、所有预测头及 adapter；只保存 LoRA 会丢失关键模块。此训练设计不将本次原型授权扩展为正式长训练。

## 阶段 C  冻结生成器 训练混合风险头

冻结 Stage 1、其 encoder、预处理与 schema revision，记录 checkpoint、代码版本、采样策略与随机种子。以 held-out／折外预测图构建 Stage 2 输入，更接近部署误差；避免只见 Stage 1 训练内的过好轨迹。先跑 scratch 小型 query Graph Transformer，在最终混合概率上计算冻结 hybrid。

相同样本并行运行精确 RULEMC；可选规则代理使用每张假设图自己的规则标签，单独评估。不得让 Stage 2 更新 Stage 1 的共享参数对象。以后联合训练需要新版本、重生成轨迹缓存，并按最终联动系统重测。

## 阶段 D  逐项解释收益

先比较同 K、同数据分割下的 RULEMC 与主风险头。再依次做用户已选的 hybrid／RPS-only／BCE-only、常速及小型时空图基线、必要的单模态增益和一个权重复用对照。K 与压缩预算、OOD、稀有事件和多种子可靠性属于后续研究；不把全部消融作为首个接口原型的门槛。

硬阈值、first-flip 和关联操作不能直接按普通梯度穿透。若以后使用 smooth surrogate 或一致性目标，记录近似与 stop-gradient 边界，最终仍回到原规则评价。两个头彼此同意不能充当真实性证据。

:::page
# 损失复核与有界原型验收

:::figure panelD

## 三项必要验收

数据语义：21 点含双端点、10 个未来 tick、11 桶、G0／query／entity 绑定正确；fixture 中 unknown 与右删失不误记为第 11 桶；字段、边和日志通过 available_time 与编码支持审计。RULEMC 对已知例给出预期 first-flip。

计算契约：K=8 各自反馈；logits／概率／CDF 的 shape、归一化和单调性正确；hybrid 在 mean-K 之后计算，λ=0.5、两项分母、mask、空集与 clamp 同冻结实现回归一致。操作数旁路没有错误实体绑定，前向／短反向有限值，梯度确实到达计划训练的 projector 与头。

资源与边界：先 microbatch=1 小样短 profile，记录模型／代码 revision、dtype、kernel 路径、token 与显存峰值；遇到 OOM、NaN、方差塌缩或异常内核回退先修复。旧 820 实验及其配置保持封存，不自动升级模型、开启大训练或生成完整观测。

## 通过的含义

通过上述检查，只表示新接口具备进入训练验证的条件。受限固定实体、占位感知编码或合成 fixtures 必须在结果中逐项声明；尚未连接真实模态数据时，不能称多模态链路已验收。若某项缺少冻结源实现，只能报告未比对，不能用新写近似函数标记“回归通过”。

首次原型的结束条件是可复现的小样闭环、已知限制和失败定位齐备。正式模型验收还需要下一页的预测、校准与模态增量证据；当前不编造固定训练时长或提升幅度。

:::page
# 资源预算 评估与待决问题

## 当前预算与资源顺序

以 100 个实体、10 台相机、21 个历史点估算：实体 21×100=2100 tokens；相机每 tick 压缩为 8 tokens，得到 21×10×8=1680；合计 3780，再加 context／query，尚未计入点云 tokens。相机若每次保留 64 tokens，仅相机 tokens 即达 13440。长上下文容量不等于显存预算。

Stage 2 每样本约 8×10×100=8000 个未来实体状态，尚未计 edges／query。沿 K 与 query 分块，不把所有轨迹和实体对共同铺进 Qwen。先冻结模态 encoder 并缓存特征，在单卡 microbatch=1 下量测；再判断 checkpointing、累积梯度与多卡方案。3×24GB 不自动成为一张 72GB 卡，DDP 会复制模型。[15]

冻结 Qwen 但训练输入 projector 时，仍需经骨干对输入反传；整段 no_grad 会切断 projector 梯度。4-bit QLoRA 是资源不足时的备选，需核验 DeltaNet 精度与 kernel 路径，不能默认无损。预算必须记录实际加载的视觉塔、激活与工作区，不能仅凭约 2B 权重体积承诺可训。

## 模型效果需要分开报告

动力学报告字段 MAE／RMSE、ADE／FDE、状态 NLL 或适用的分布分数、存在／关系误差及覆盖率；必须含 5s open-loop。事件报告全已知 CDF 的 RPS、终点 Brier、NLL、校准和各 horizon 误差；在完整 K-sample 混合后评估校准，单轨迹温度校准不足以保证最终校准。

按谓词、实体类型、遮挡／缺失率、边界距离和稀有事件分组，置信区间按 episode 汇总。用 oracle graph→head、sampled graph→exact rule、sampled graph→head 拆分规则计算、动力学与风险补偿。比较真实图训练和预测图训练的分布迁移，所有基线使用相同合法输入与切分。

多模态价值通过数值图与数值＋模态对照确认，再用关联打乱、删除／重复相机、延迟和标定扰动诊断。新增图像在数值已充分时可能没有增量；无可重复收益时，应停止增加复杂度，而非默认换更大骨干或 decoder。

## 需要由数据或 profile 回答的问题

真实相机／点云与在线关联是否可得，标定和时间误差有多大？H21 数据是否能按 cutoff 生成？未来控制与 Observer pose 哪些事先已知？首版是否只覆盖固定实体？谓词是否完全数值化，初始真值如何取得？这些答案决定输入和有效 mask。

尚待实测的还有：8 个 scene tokens 是否保留关键证据，低秩联合头能否覆盖行为模式，Qwen 相对小图模型的增益，以及各硬件／kernel 路径的峰值与吞吐。验收阈值应按任务容忍度确定；本稿不以通用百分比替代这些事实。

:::page
# 主要依据与交付范围

下列为关键定义、官方接口及组件先例。文献支持的范围已在正文区分；组合后系统的效果仍需 P01 验证。链接保留在 PDF 与可编辑稿中。

[1] 冻结配置与旧 820 记录。AeroAgentSim，固定 commit a4f49e8b1aa43cfa22552fea7cd092eac569fdac，first_epoch_820_result.json。
https://github.com/ZhiweiWei-NAMI/AeroAgentSim/blob/a4f49e8b1aa43cfa22552fea7cd092eac569fdac/aero-bench/docs/coordination-checkpoint-20261005/p01/first_epoch_820_result.json

[2] Qwen3.5-2B-Base 官方模型卡与配置。
https://huggingface.co/Qwen/Qwen3.5-2B-Base
https://huggingface.co/Qwen/Qwen3.5-2B-Base/blob/main/config.json

[3] Transformers Qwen3.5 官方接口与实现。
https://huggingface.co/docs/transformers/main/model_doc/qwen3_5
https://github.com/huggingface/transformers/tree/main/src/transformers/models/qwen3_5

[4] PEFT LoRA 官方文档。 https://huggingface.co/docs/peft/main/package_reference/lora

[5] Point-BERT，CVPR 2022。 https://arxiv.org/abs/2111.14819

[6] PyTorch LowRankMultivariateNormal 分布接口。
https://docs.pytorch.org/docs/2.14/distributions.html#lowrankmultivariatenormal

[7] DreamerV3，Nature 2025。 https://www.nature.com/articles/s41586-025-08744-2

[8] V-JEPA 2，2025，§2、§3 与附录 B.3。 https://arxiv.org/html/2506.09985v1

[9] SlotFormer，ICLR 2023。 https://arxiv.org/pdf/2210.05861

[10] Drive-WM，CVPR 2024。 https://arxiv.org/html/2311.17918v1

[11] Copilot4D，ICLR 2024。 https://arxiv.org/html/2311.01017v2

[12] C-SWM，ICLR 2020。 https://arxiv.org/abs/1911.12247

[13] Graph World Model，2025。 https://arxiv.org/html/2507.10539v1

[14] World Observer，2026 年预印本。 https://arxiv.org/html/2610.02162v1

[15] Transformers 多 GPU 训练官方说明。
https://huggingface.co/docs/transformers/main/en/perf_train_gpu_many

## 本次交付

本文件给出完整方案、结构图、接口、概率语义、冻结损失、分阶段实施和验收范围。可编辑 DOCX 与 Markdown 保留正文；结构图另有 SVG 与 PNG。后续原型结果应独立列出实际运行环境、测试通过项与未接通部分，不能以方案文字替代运行证据。
