# P01 Qwen 预测模型训练实施交接

日期：2026-10-05。执行目标：完成全家族验收、封存旧 GRU 后，集中实现一条可以真实训练、可检查数据来源的 Qwen 路径。本文沿用已有设计并以最新收窄为准，不再展开主干选型。

## 结论与第一条可训练路径

采用 **typed 局部图编码器 → entity-time tokens → Qwen3.5-2B-Base 加小 LoRA → 独立 predicate-instance 查询头 → 每轨迹 11 类概率 → K 等权平均**。

第一轮接状态图和语义文字；相机、LiDAR 保留同一实体和时间对齐接口，主链路通过后再接历史传感器特征。冻结 Qwen 只做加载和梯度诊断，不另起一个长期冻结训练项目。Qwen 负责预测，不调用 generate，也不把数值转成长段文本。

当前 14,628 参数 GRU 的五步 loss 0.428944→0.418348 仅作为已有链路能反传的记录；不是 Qwen 训练结果，也不是实际预测有效性的证据。12 家族、72 可执行谓词、91 字段及 remaining-deadline 类别词表修复状态须以全家族最终验收文件为准；本次没有服务器访问、权重下载或训练实测。

## 1 执行顺序和每步交付

1. **封存验收版本。** remaining-deadline 类别词表修复后重跑受影响测试和全家族总验收；保存 schema、枚举词表、AST executor、标签器、loss、配置和数据 manifest 的版本/hash。按已授权流程停止旧训练，保留最后 checkpoint/log，不再追加旧 GRU 实验。旧作业停止状态由服务器执行方核实。
2. **先写数据边界。** 输入构造器只收历史观测、预测器输出、已知地图/规则和 query 定义；真实未来在独立标签加载器中。离线输出字段可用性/预测来源清单及 Q 的实际分布；运行时只做实现所需的基础形状检查，来源隔离通过针对性代码测试验证。任何缺少预测来源的字段都必须明确，不能用真未来补齐后称 real prediction。
3. **实现最小图到 Qwen 的模型。** 优先复用现有 typed encoder，并补足 relation-aware message passing；每实体每时刻一个 slot；按已有输出维度投影到2048；Qwen 文本主干；外部独立查询读出。用短 synthetic fixture 检查形状与梯度，再接真实样本形状。
4. **核验预训练加载和 LoRA。** 固定模型 revision、库版本和内核；保存加载键检查、模块白名单、可训练参数清单。确认没有意外随机初始化文本层，没有调用词表输出头。
5. **做真实形状单卡测量。** B=1、K=1、N≈100、历史21点、未来10点，以实际语义长度和实际 Q 跑 forward/backward/optimizer step。记录分阶段峰值显存、耗时和内核路径。短 fixture 通过不代表生产形状通过。
6. **接入 K=8 的同一 mixture 目标。** 小形状先验证全 K 并行与省显存重算方案的概率、loss、梯度一致性；然后真实形状测量。K 不能改成八个独立标签样本。
7. **通过隔离测试。** 真未来污染测试、K 独立性、查询增删与置换不变、绑定敏感性、padding 不变、保存重载和恢复训练测试全部通过。
8. **才开始最小数据训练闭环。** 一个小批次可重复拟合，然后短训练/验证 smoke run；记录每家族有效监督数、实际 Q、loss 各分量、梯度、显存、吞吐。只在 real-predicted 输入有合法来源时报告预测质量。无需等待大规模基准矩阵或视觉分支才能进入这一步。

每一步产生明确的 PASS/FAIL/未运行状态。来源清单是离线准备产物，之后仅在数据生成逻辑或schema实质变化时更新。第2步若发现真实预测输入尚缺失，继续模型/loader/梯度/显存准备；将 predictive-quality run 标为阻塞，不能把 oracle run 改名交付。

## 2 输入和输出契约

### 2.1 样本身份和数量

- B：互相独立的预测窗口数。
- K：同一窗口、同一预测分布的未来状态样本数。正式目标 K=8，K=1 仅是实现和资源诊断起点。
- N：该窗口实体槽位数；由 t0 可得实体/候选实体集合确定，不能从真未来实体表补身份。预测出生实体需要预测器显式产生、带独立来源。
- P=72：当前谓词定义数。**Q：实际已绑定谓词实例数**，与 P 不同。二元/集合/区域绑定可能令 Q 大于 P；不能假定所有实体两两组合都必须展开。
- H=21：历史 -10,-9.5,…,0 秒；F=10：未来 0.5,1,…,5 秒。所有时间以 t0 为零点。
- 离线manifest按需要保存 scene/source_sequence/window标识、t0、split、schema/registry/label版本、predictor版本与seed；公共版本信息放数据集级，避免每条记录或每个字段重复携带。

### 2.2 模型输入

以结构体表示，不强迫91字段全部变成同质 dense float 数组：

- history.continuous：[B,H,N,D_cont]，标准化量；单位/归一化统计仅从 TRAIN 拟合。
- history.categorical / boolean：各自整数/布尔 typed block；每字段独立词表及 unknown/not-applicable 状态。
- history.entity_valid：[B,H,N]；history.field_valid 与对应字段形状一致；observed/predicted/unknown是必要的输入语义。来源/可用时间审计放离线manifest和代码测试，不做热路径逐字段追踪。
- forecast：对应 typed block 的 [B,K,F,N,…]；必须是 `predictor(history_as_of_t0, known_context)` 的输出或可审计派生值。
- edges：按窗口、时间和轨迹的稀疏 edge_index、relation_type、relation attributes、validity。历史边只由历史可得数据构造；未来边只由该 k 的预测状态及已知静态关系构造。
- static_context：地图、区域、规则版本、已知计划和实体静态属性；离线记录必要的版本/as_of信息。事先可知的未来计划可用，但不能混入事后更新。
- queries：长度 Q 的 predicate_id、family_id、AST、参数及单位、绑定实体/集合/区域索引、query_valid。绑定索引是结构输入，不依赖实体名字文本猜测。
- query_text：固定模板产生的谓词语义；数值参数同时有 typed 编码。不要把真实结果写入 query 描述。
- sensor_refs：预留 camera_id、timestamp、calibration_revision、entity association、visibility、image/grid feature reference；LiDAR 同样保留坐标系、时间和可见性。第一轮可为空，空值不伪造成已观察到零特征。

未知、未预测和不适用分别表达；必要validity是模型数据语义，不等于增加运行时来源检查框架。已有类别词表新增值必须走版本更新与兼容检查，禁止训练途中静默重排 category id。

### 2.3 标签单独加载

- labels.y0：[B,Q]，t0 的已知谓词真值及有效 mask。
- labels.flip_bin：[B,Q]，0…9 分别对应0.5…5秒的首次翻转，10为有效支持完整、可确认 T>5秒。
- labels.cdf_target / cdf_valid：[B,Q,10]。
- labels.event_target / terminal_valid：[B,Q]。
- 标签的 first-flip 定义、等号边界、事件持续时间要求、采样网格和缺失处理全部沿用最终验收的现有 contract；不改成 first-true/onset。
- 第11类仅用于完整可识别支持下确认 T>5秒，不表示永不发生，也不包含 censor/unknown。y0未知、早期关键字段缺失、窗口不足等按现有可识别/删失逻辑处理，不能全塞第11类。
- 实际已观察到翻转与完整观察到无翻转可有合法 terminal target；部分观察且尚未翻转通常不能当作已知无事件。CDF 可只在可识别时点计分，具体 mask 复用验收实现。

模型 forward 接口不接受 labels、oracle_future、真未来 AST trace。训练脚本在 forward 返回后才读取标签计算 loss。用输入构造函数边界和针对性测试保证隔离，不在每个训练step加入逐字段provenance拦截。

### 2.4 输出

- logits：[B,Q,K,11]。
- per_trajectory_prob = softmax(logits.float(), dim=-1)。
- mixture_prob = per_trajectory_prob.mean(dim=2)，形状 [B,Q,11]。
- event_prob = 1 - mixture_prob[...,10]。
- cdf = cumsum(mixture_prob[...,:10], dim=-1)。

每个 query、每条轨迹各自做11维 softmax，再对 K 等权平均。不得先平均 logits；不得对 Q 做归一化；不得用某条平均状态轨迹替代 K 个预测样本。

## 3 教师和真实预测输入必须分开

定义三个清楚的入口，避免接口里一个布尔参数静默改变数据来源：

- `real_predicted`：主训练/验证入口。历史和 t0 可得数据产生 forecast；监督来自后来发生的真未来。预测器训练集必须服从 scene/sequence 分组切分；若预测器用同一批训练场景拟合，需要说明是否采用 out-of-fold 生成，以评估训练/部署输入分布差异。
- `oracle_future`：真未来状态替代 forecast 的独立诊断入口。只检查表征容量或给出上界；单独目录、manifest、指标表，绝不混入真实预测榜单。
- `executor_teacher`：在给定的预测轨迹上运行 AST，得到该轨迹的确定性结果或辅助 trace。它是规则执行教师，不是真实世界未来标签。

第一条 trainable path 不另加教师损失或 residual 分支。复用现有 executor 已提供的预测轨迹执行特征：真假/unknown、实际比较量、单位、合法的margin及时间/截止约束值；在独立query head中保留这些精确规则特征，不因为更换Qwen而删掉。没有自然数值margin的逻辑谓词不编造margin。Rule-MC可作为同一执行结果的诊断读出。所有这些特征只由同一k的forecast和已知context产生，真未来trace不进入模型。

为91字段建立 availability matrix：历史能否观测、预测器是否输出、能否从该 k 的其他预测字段推导、是否已知静态、是否只在 oracle 中可得。不能假设几何轨迹预测器自然覆盖任务状态、类别、deadline、工序、占用和逻辑关系。

## 4 模型具体实现

### 4.1 图编码器和序列

优先复用现有 typed encoder 的字段、类别、单位、缺失处理和输出，不重写已验收语义。若其尚无关系消息模块，再加2层width256的类型相关消息和聚合：2层让实体接收两跳局部关系，256是控制初始显存/参数的工程默认值，未经过最优性验证。若已有合理的图编码维度，沿用该维度并改projector输入维度即可，不为凑256而重构。真实空间/任务/拓扑边必须参与消息；只拼实体属性不算图编码。采用LayerNorm、初期dropout=0以便查错。

将特征经归一化和D_graph→2048 projector，再加可训练的相对时间、实体角色、来源/缺失提示。实体标识用于一致绑定；排序采用可复现的 scene-local 槽位，不假定 Qwen 天然置换不变。

Qwen 序列顺序：与 query 集合无关的固定 schema/context 文字前缀 → 历史 time-major entity tokens → 该 k 的未来 time-major entity tokens。图编码器第一版只做同一时间的空间/关系消息，不让某个 k 的未来反写公共历史表示，便于隔离并为以后共享历史 prefill 保留条件。

语义前缀的实际 token 数必须 tokenize 后统计；256 只能作为第一轮预算起点，不是91字段完整描述必然装得下的断言。超长时生成经过版本化的简洁 schema 说明或提高预算后重测，禁止静默截掉规则/单位。predicate 的全部实例描述不进公共 Qwen 前缀，避免 Q 改变主干输入。scene graph的节点/边及图attention也不能按当前query集合或其依赖子图并集重建；否则增删query仍会改变共享主干。依赖子图选择、绑定特征和query-specific attention bias只在独立head中应用。

实体维 padding 不能被模型误当成真实体；按有效长度做 bucket，第一版不把不同场景打包进一条连续 DeltaNet 序列。采用1D序列 position_ids，并让相对物理时间另有显式编码；不要擅自把实体 xyz 当作原生视觉 RoPE 三维坐标。

### 4.2 Qwen 文本主干

官方2B配置为24层、hidden2048、18个 linear-attention 层和6个 full-attention 层。官方文本模型支持 inputs_embeds、返回 last_hidden_state；其混合结构保持因果，不改为伪双向 encoder。[S1–S3]

第一轮最稳妥的权重核验方式：以官方整模型类在 CPU/低峰值加载路径加载并检查 report，取 `model.language_model`；训练进程 GPU 上只保留文本主干、冻结 embedding 和自定义模块，不保留 visual、MTP 或词表输出计算。也可以用官方文本类的 checkpoint 前缀映射，但必须逐键证明文本权重完整加载，不能只因 `from_pretrained` 未抛错就通过。保存原始加载 report、抽查权重键/校验和、总参数数。

训练时 use_cache=False、output_hidden_states=False、output_attentions=False，直接读取最后层返回。保留 embedding 以表示语义文字，但它冻结；不运行 LM head。不能套默认语言建模 Trainer loss。

即使只有最后8层加 LoRA，输入图编码器/projector 的梯度仍要穿过前面冻结层。不能把整个 Qwen 包在 no_grad 中，也不能把图 token detach。用非 reentrant activation checkpointing，并检查 graph/projector/LoRA/head 均有有限梯度；LoRA 的零初始化使部分因子第一步梯度为零是可能的，应检查首步可训练分支和随后数步参数变化，而不是错误要求每个因子第一步非零。[S5]

### 4.3 独立查询头

每个已绑定 predicate instance 形成一个 width256 query，包含：AST结构/操作符、family、typed参数、绑定实体/区域特征、谓词文字语义。最小文字入口可用冻结 Qwen embedding 的 masked pooling 加可训练投影；这保留预训练词嵌入信息，但不夸称它等同完整语义理解。

一层 width256 cross-attention 让每个 query 读取该 k 的完整 Qwen hidden states；K/V先2048→256，query之间无 self-attention、无 BatchNorm、无共享竞争式 softmax。再拼接绑定实体的图特征skip connection、typed参数，以及现有executor对该k的预测轨迹给出的精确规则特征，经过共享MLP输出11 logits。skip connection保留数值边界，不代替Qwen输出。

历史 hidden states 在因果主干中不含未来上下文，但查询头可读整个历史和预测未来，不能把所有历史 token 都描述成双向融合状态。绑定实体索引必须作用于 query构造/skip路径/依赖标识，不能仅在自然语言中写一个实体名。

**Q 的预算：** 先按家族导出实例数量 q_f 及绑定 arity，Q=sum(q_f)。共享主干长度不含 Q；head 有 Q 个读出向量，计算量约随 Q×L 增长。按实际 Q 分布做 query chunk（例如先从32试测），共享同一份主干 states；不重跑 Qwen，不设置虚构的1000 query tokens。chunking不可丢弃问题或改变 loss 的全局归一化。

规则组合仍由AST完整表达，head必须保留操作符、参数、单位、实体/集合绑定和必要的子表达式结果；不能把复合规则压成一个predicate ID后丢失组合语义。查询head的无交互仅意味着“问题彼此不影响”，不限制单个规则内部组合。

如果forecast状态已经完整且确定，AST本身可精确给出该轨迹的规则结果；神经头的价值应来自预测误差、缺失信息和有限K下的概率修正，而不是把确定规则重新猜一遍。因此最小训练报告同时保留同一forecast上的Rule-MC诊断值，不扩成另一条主架构。

## 5 LoRA 白名单与参数量

第一版只对文本层16…23（0-based）加 rank8、alpha16、dropout0、bias=none LoRA；冻结其余Qwen参数、FFN、norm、embedding。下列名称以最终提取出的 `Qwen3_5TextModel` 为根；实际前缀由 named_modules 验证。[S3–S4]

- full-attention层19、23：`layers.{i}.self_attn.q_proj/k_proj/v_proj/o_proj`。
- linear-attention层16、17、18、20、21、22：`layers.{i}.linear_attn.in_proj_qkv/in_proj_z/out_proj`。
- 第一版不改 DeltaNet 的 in_proj_a、in_proj_b、A_log、dt_bias、conv1d，也不对视觉层或MLP套 all-linear。

按官方2B配置逐矩阵计算 r×(in+out)：

| 模块 | in→out | 每层rank8参数 |
|---|---:|---:|
| full q_proj，含输出gate | 2048→4096 | 49,152 |
| full k_proj | 2048→512 | 20,480 |
| full v_proj | 2048→512 | 20,480 |
| full o_proj | 2048→2048 | 32,768 |
| DeltaNet in_proj_qkv | 2048→6144 | 65,536 |
| DeltaNet in_proj_z | 2048→2048 | 32,768 |
| DeltaNet out_proj | 2048→2048 | 32,768 |

共26个目标 Linear；2×122,880 + 6×131,072 = **1,032,192 LoRA参数**，另加图编码器、projector、query/head。此数是按已核查代码的算术，不是运行计数。PEFT必须收到枚举出的完整目标名，并断言匹配数量与维度；不要依赖新模型的默认target映射。官方代码/PEFT在服务器锁定版本如有变化，以运行枚举为准并解释差异。

优化器起步：LoRA lr=1e-4，图/projector/query/head lr=1e-3，AdamW，grad clip=1.0；这些是工程起点，不是最佳超参数结论。前几步检查各组梯度尺度，若发散先查单位、missingness、损失reduction和加载错误，不立即扩rank或上更大模型。

## 6 hybridCDF 和 terminal BCE

**复用全家族已验收的 loss 函数、权重、mask与reduction，不在换主干时重新定义 hybridCDF。** 将旧实现的函数定位、hash和所有系数写入配置。下面只展开CDF及终端概率接口，若现有hybridCDF还含其他约定分项，应原样保留并单独日志，不静默替换成单一RPS。

对平均概率 p_bar，F_j=sum(p_bar[0:j+1])，j=0…9；相应CDF误差只在cdf_valid处计分。若原实现的CDF项是平方误差，则为mean((F_j-cdf_target_j)^2)。terminal BCE使用 event_prob=1-p_bar[10] 和event_target，只在terminal_valid处计分。FP32计算softmax、累加、log与loss，并采用现有数值稳定方式。

标签属于窗口与query，即[B,Q]，不把同一真值当作K份独立监督。训练统计和有效样本数按独立窗口/有效query记录；K8不会令样本数变为8倍。

### K8 省显存而不改变目标

先实现小形状完整[B,K]并行作为正确性参考。真实形状若K8激活超显存，可以按轨迹重算，不能平均 `loss(p_k,y)` 冒充 `loss(mean(p_k),y)`。

推荐精确一阶梯度的两遍方案：

1. 固定参数、sample seed、dropout状态；no_grad逐k计算p_k，保留小型概率数组，得到p_bar。
2. 令p_bar成为独立leaf，对完整原loss求g=∂L/∂p_bar；g含所有mask和reduction。
3. 逐k重新进行有梯度forward，以 `<p_k, stopgrad(g)>/K` 为反传代理标量累积梯度。
4. 所有k结束后只做一次optimizer step。日志记录第2步真正的loss，不能记录代理标量充当loss。

按链式法则，这在两遍forward一致时等价于完整mixture的一阶梯度。保持dropout=0、相同autocast/backend；若以后引入随机性，必须保存并重放RNG。先在小fixture验证原/重算概率、各模块梯度、更新后参数一致。不要跨optimizer step缓存旧p_k。共享历史图特征若产生可训练计算图，也必须按正确重算/累积策略处理，不能提前detach。

DDP初期让一个完整窗口及其K条样本留在同一rank，3张卡处理3个窗口；暂不跨卡拆同一K mixture。no_sync只用于已验证的梯度累积，最终同步和全局loss归一化必须一致。DDP复制每卡主干，不会把24GB×3变成一块72GB显存。[S7]

## 7 真实 token 与激活预算

### 7.1 状态主链路

对于S=1：L = L_schema/context + Σ_t N_valid(t) + L_extra。N=100、H=21、F=10时，entity-time部分为3,100 tokens/轨迹。若前缀恰好256且无额外关系token，L=3,356；这是示例算术，运行以真实tokenizer和有效实体计数为准。

关系已由图消息编码；第一版不额外复制所有边为LLM tokens。Q个查询在外部head，不计入L。K8是8条不同的约3.3k序列，并非只付一条序列成本。

仅一个[BK,L,2048] BF16张量，B=1、L=3356时约13.1MiB；K8约104.9MiB。它远不是训练峰值：24层的MLP、中间投影、DeltaNet、全注意力、反传和优化器都需另外计量。若误用eager全注意力，一个8-head、L=3356的BF16注意力矩阵已约171.9MiB/序列/层；用SDPA/适配内核避免物化这类矩阵，仍需验证实际backend。LoRA参数少不代表激活少。

完整官方权重文件索引含视觉和MTP，总tensor字节约4.548GB；这既不是文本主干单独权重大小，也不是训练显存。按实际驻留tensor的dtype/numel报告文本权重和训练模块大小。[S8]

### 7.2 相机和 LiDAR 接口

10相机、1Hz、-10…0秒含端点，是110张图。若每张**独立图像确为512×512**，按patch16、spatial merge2名义计算，每图为(512/16/2)^2=256视觉tokens，总计28,160，尚未加图状态、文字、分隔符。连同3,100个状态tokens已超过31k。这是按结构的算术估计，不是processor实测；动态resize、裁切、视频temporal patch和时间标记会改变实际数，必须记录image_grid_thw/video_grid_thw核算。[S1,S9]

第一轮不把这110张图直接塞Qwen。后续沿同一图路径，将冻结视觉编码器的历史特征按相机标定、可见性、实体和时间关联后送入图编码器；先测压缩后的槽位信息是否够用，再决定是否需要额外视觉上下文。不能在没有实体关联时随意平均多相机特征。LiDAR采用同样的实体/时间接口；没有原生LiDAR预训练输入的证据，不能说Qwen本身已支持点云。

传感器全是t≤0的真实观测。“后续接图像/LiDAR”指开发阶段，不允许将真实未来帧作为部署时输入。生成/预测的未来特征如采用，必须带predictor来源并与oracle隔离。

### 7.3 单卡和三卡测量表

在实际3090服务器记录GPU型号/显存、驱动、CUDA、PyTorch、Transformers/PEFT commit、causal_conv1d/fla或kernel版本、BF16支持、实际启用的attention/DeltaNet实现。官方文档提醒缺少快内核可能退回更慢且更吃内存的路径，不能只看到程序可跑就忽略警告。[S2]

按顺序测试：

- 短fixture，B1/K1/Q少量，验证加载与梯度。
- 真实N/H/F，B1/K1，Q按实际窗口取值；测中位和高分位实体数/边数/Q。
- 同一真实窗口K8，优先正确的重算模式；对比可容纳的小形状K8并行。
- 单卡通过后3-rank DDP，每卡B1、完整K8；再考虑独立窗口梯度累积。先不启用自动device_map、FSDP或量化来叠加排错因素。

每个测量阶段清零peak stats、CUDA synchronize，记录load后常驻、forward峰值、backward峰值、optimizer第一次分配后的峰值，以及max_memory_allocated、max_memory_reserved、设备可用显存和外部占用。至少包括预热后的多步测量，不能只记首个forward。PyTorch峰值计数只统计其allocator，需结合设备总显存观察。[S6]

工程放行条件：实际目标形状完成forward/backward/step、梯度有限、无随机OOM、实测设备空闲留有约15%的可操作余量。这个15%是部署余量建议，不是某个模型的固有要求。若不满足，先减独立窗口microbatch、启用已验证checkpointing和K重算；仍不满足则报告具体峰值和阶段，再评估量化/切分，不自动缩短10秒历史或丢实体改变问题。

## 8 历史 prefill 可共享到什么程度

训练第一版：不做Qwen cache复用，use_cache=False；只在参数和梯度语义清楚时共享历史图计算。推理可评估一次公共prefix+history prefill，然后每条未来轨迹独立续算。

可共享的前提是：所有k的prefix+history token逐值相同，且不依赖query集合或预测未来；LoRA权重相同；eval模式；相同位置、mask与dtype。

Qwen3.5缓存包含full-attention KV以及DeltaNet recurrent/conv状态。每条k必须有独立可变状态，不能shallow copy同一cache后依次覆盖。外部查询头还要读取公共历史last hidden states，不能只保存cache。序列续算还要正确延续position_ids与mask；多token continuation路径必须单独验证。[S3]

验收：同一窗口先各k完整重跑作为参考；再prefix复用，逐query逐bin比较概率；交换k执行顺序，修改某一k，追加无关k，验证其他结果不变；同时测显存/时延，确认拷贝cache和保存history states后仍有净收益。通过后才启用。不存在“只有最后8层LoRA，因此前16层历史能永久缓存”的捷径：输入projector/图编码器训练时会更新，缓存会过期。

## 9 必须通过的自动测试

- **输入时间边界代码测试**：用可控fixture与loader测试验证真实观测只取t≤t0，已知未来计划按其版本可见性处理，预测future来自forecast入口；不在训练热路径加逐字段来源审计。
- **真未来污染**：固定历史、forecast、queries，任意改写labels/oracle_future，model logits必须不变；输入bundle序列化hash也不变。
- **标签边界**：保留全家族的first-flip、t0 unknown、完整无翻转、部分删失、恰好5秒、等号、deadline类别和unknown fixture。
- **K隔离**：改变一条预测样本只改变该k的logits；k重排只重排K轴；重复完全相同的样本得到相同的mixture；无跨K attention或BatchNorm。
- **查询隔离**：固定一条query，追加/删去/调换其他queries，当前输出不变；query chunk大小改变不改变结果。绑定实体改变应在有区别的fixture上改变结果。
- **结构正确性**：实体重标号并同步重排图与绑定，图编码器等变；Qwen排序敏感性单独测，不把图等变错误当作全模型严格置换不变。
- **padding正确性**：增加被mask的entity/time/query不改变有效输出或loss归一化。
- **梯度**：图/projector/query/head和LoRA均可训练；冻结参数不被optimizer更新；forward不产生词表logits。
- **K目标**：完整mixture与重算算法在小fixture上梯度一致；不能仅比较forward loss。
- **恢复**：保存/重载同一config、词表、adapter和自定义模块后输出一致，optimizer/scheduler/RNG可恢复。

## 10 训练准备应交付的文件

具体放在现有P01项目的适当模块中，不必为了这份交接重建仓库：

- 数据：schema/registry/label contract snapshots，field_availability.json，split_manifest，forecast_manifest，实际N/E/Q/token统计。
- 模型：typed_graph_encoder、qwen_backbone_loader、predicate_query_head、P01组合模块。
- 训练：复用现有loss的接口适配，K-mixture聚合与可选两遍重算，checkpoint恢复。
- 核验：load_report.json，lora_targets.json，trainable_parameters.json，isolation_tests，memory_profile.jsonl。
- 配置：模型revision、环境lock、数据hash、LoRA白名单、学习率、loss函数hash/系数、K/H/F、随机种子。
- 最小运行报告：运行输入模式，all-family验收状态，实际预测字段覆盖，shape/gradient/memory/restore各gate状态；任何未跑项目明确写未运行。

不要以“stub可导入”“随机小模型shape通过”或“oracle loss下降”替代Qwen预训练权重、真实形状、真实预测来源的验收。当前最重要的结果是一条诚实可复现的训练链路，而不是更多候选架构。

## 来源

下列官方资料于2026-10-05查阅。设计、预算算术和测试门槛是本交接的工程判断，不是来源声称已验证P01。

- S1 [Qwen 2B Base配置](https://huggingface.co/Qwen/Qwen3.5-2B-Base/blob/main/config.json)；[模型卡](https://huggingface.co/Qwen/Qwen3.5-2B-Base)。卡片顶部明确为pre-trained only；模板overview的post-training措辞不能覆盖这一说明。
- S2 [Transformers Qwen3.5文档](https://huggingface.co/docs/transformers/model_doc/qwen3_5)。
- S3 [Transformers官方实现](https://github.com/huggingface/transformers/blob/main/src/transformers/models/qwen3_5/modeling_qwen3_5.py)。核对TextModel、GatedDeltaNet、Attention和cache续算路径。
- S4 [PEFT自定义模型及目标模块核验](https://huggingface.co/docs/peft/main/en/developer_guides/custom_models)；[LoRA配置](https://huggingface.co/docs/peft/main/package_reference/lora)。
- S5 [PyTorch activation checkpointing](https://docs.pytorch.org/docs/2.9/checkpoint.html)。
- S6 [PyTorch max_memory_allocated](https://docs.pytorch.org/docs/2.9/generated/torch.cuda.memory.max_memory_allocated.html)。
- S7 [PyTorch DDP设计](https://docs.pytorch.org/docs/2.9/notes/ddp.html)。
- S8 [官方权重索引](https://huggingface.co/Qwen/Qwen3.5-2B-Base/blob/main/model.safetensors.index.json)。
- S9 [官方图像预处理配置](https://huggingface.co/Qwen/Qwen3.5-2B-Base/blob/main/preprocessor_config.json)。

查阅时HF仓库最新revision为 `b1485b2fa6dfa1287294f269f5fb618e03d52d7c`（[官方commit](https://huggingface.co/Qwen/Qwen3.5-2B-Base/commit/b1485b2fa6dfa1287294f269f5fb618e03d52d7c)）。应以这一明确revision作为准备起点，而非浮动main。Transformers/PEFT本次浏览的是官方main文档/代码，尚未在服务器锁定可工作的commit；必须在准备阶段补齐并实测，不能将网页可见代码当作环境已安装且可用的证明。
