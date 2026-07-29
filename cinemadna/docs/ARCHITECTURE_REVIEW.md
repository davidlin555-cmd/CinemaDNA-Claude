# CinemaDNA / DramaOS-X 架构复盘与优化 v2

**日期**：2026-07-23
**基线**：Phase 1–5 已落地，429 tests passed，Kling 真实出片已验证（5.1s / 720p）
**方法**：拿"建议最终形态"逐条对照**真实代码**，只给可落地项，不做抽象评价。

---

## 0. 复盘基线：现在真实有什么（不是规划，是能跑的）

| 模块 | 真实状态 | 关键实现 | 最大缺口 |
|------|----------|----------|----------|
| ScriptBrain | 🟡 mock（模板库，无 LLM） | 3 道闸门 G1/G2/G3 + 结构契约校验器 + 双 Critic | 内容质量靠模板；LLM key 已验证可用但未接 |
| SceneDNA | 🟡 mock 生成 + 真实 Gate | 拒绝 `pure_ai_generated`、标签复用、回流 | 无真实场景图像 |
| IdentityDNA | 🟢 mock 生成 + **真实红线 Gate** | 单一真人克隆硬拦截（6 种绕过全堵）、人工不可翻 | 无真实人脸图像 |
| PropDNA | 🟡 mock 生成 + 真实 Gate | 状态版本、跨镜头连续性 | 无真实道具图像 |
| DirectorDNA | 🟢 真实逻辑 | Shot Contract（不可篡改、hash 锁）、连续性账本 + 4 类检查 | 镜头语言规则较粗 |
| PerformanceDNA | 🟢 真实逻辑 | 表演节拍、微表情（性格×情绪×景别）、表演圣经 | 无口型/音轨对齐 |
| Render Brain | 🟢 骨架完整 + Kling 已验证 | `RenderBackend` 协议、路由、重试、一致性、成本优化、占位 MP4 | KlingBackend 未接进管线 |
| Audio & Post | 🔴 **未实现** | 仅粗剪（时间线/EDL/VTT/动态分镜） | TTS/唇同步/BGM/字幕烧录全缺 |
| 镜头级 QA | 🟢 真实逻辑 | Vision/Performance/Continuity + Repair Planner | 不看画面（结构+mock 分） |
| **成片级 Final Review** | 🟢 **结构层已落地**（v2.1 收敛） | QA Council 5 维裁决、复用现有全部报告、口型铁律、PENDING 判断层、`FINAL_REVIEW` 状态 | 判断层（多模态/音频）待接入 |
| Orchestrator | 🟢 真实状态机 | 两轴（stage×status）、并行槽位、人工闸门、精准打回、**FINAL_REVIEW 阶段已加** | — |

🟢 真实可跑　🟡 结构真实/内容 mock　🔴 未实现

**一句话结论**：你的"建议最终形态"里，1/3/4/8 已基本成形，2 的 Gate 逻辑是全系统最硬的部分；**真正的两个空洞是「Audio & Post」和「成片级 Final Review」**，加上「真实出片链路」这个瓶颈。下面所有优化围绕这三点展开。

---

## 1. 优化后的完整模块 + 智能体分工表

**改动要点**：① 把 QA 拆成"镜头级 QA"和"成片级 Final Review"两个独立模块；② 状态机插入 `FINAL_REVIEW` 阶段（POST 之后、PUBLISHED 之前）；③ Audio & Post 独立成模块。

| # | 模块 | 智能体（[已实现]/[mock]/[待建]） | 审核门 | 门类型 |
|---|------|-----------------------------------|--------|--------|
| 1 | **ScriptBrain** | Idea Miner[mock]、Showrunner[mock]、Plot Architect[mock]、Episode Writer[mock]、Dialogue Master[mock]、Script Critic[✓]、Market Hook Critic[✓] | G1 题材 / G2 大纲 / G3 拍摄版 | 软门 |
| 2 | **Asset Brain** | | | |
| 2a | SceneDNA | 需求解析[✓]、检索[✓]、原子提取[mock]、重布局[mock]、回流[✓] | 自然场景门 | **硬门** |
| 2b | IdentityDNA | 需求解析[✓]、多源融合[mock]、年龄线[✓]、家族脸谱[✓]、回流[✓] | 版权红线门 | **硬门** |
| 2c | PropDNA | 需求解析[✓]、状态机[✓]、生成[mock]、回流[✓] | 连续性门 | 软门 |
| 3 | **DirectorDNA** | 分镜规划[✓]、镜头语言[✓]、连续性检查[✓]、合约生成[✓] | 合约完整性门 | 软门 |
| 4 | **PerformanceDNA** | 情绪曲线[✓]、动作走位[✓]、微表情[✓]、口型对齐[待建] | 表演可执行门 | 软门 |
| 5 | **Render Brain** | 模型路由[✓]、参考注入[✓]、执行器[✓ mock/占位]、KlingBackend[待建]、成本控制[✓] | 单镜头技术门 | 软门 |
| 6 | **Audio & Post** | TTS[待建]、BGM 匹配[待建]、唇同步[待建]、字幕[待建]、粗剪[✓] | 音画同步门 | 软门 |
| 7a | **镜头级 QA** | Vision[✓]、Performance[✓]、Continuity[✓]、Repair Planner[✓] | 镜头 QA 门 | 软门 |
| 7b | **成片级 Final Review** ⭐新独立模块 | 视觉一致性审[待建]、叙事连贯审[待建]、技术质量审[部分]、安全合规审[待建]、商业完成度审[待建] | **总审核门** | **硬门 + 人工门** |
| 8 | **Pipeline Orchestrator** | 状态机[✓]、并行调度[✓]、人工闸门[✓]、重试/打回[✓部分] | 管控层 | — |

**状态机改动**（`orchestrator/story.py`）：
```
现在:  RENDERING → QA → POST → PUBLISHED → ARCHIVED
改为:  RENDERING → SHOT_QA → POST → FINAL_REVIEW → PUBLISHED → ARCHIVED
```
`FINAL_REVIEW` 通过才允许进 `PUBLISHED`（= 发布包）。打回目标集合增加 `POST`。

---

## 2. 每层审核门的通过/失败标准（可执行，多数已有真实阈值）

### 硬门（不过就阻断，禁止降级绕过）

| 门 | 通过标准（全部满足） | 失败动作 | 真实位置 |
|----|---------------------|----------|----------|
| **IdentityDNA 版权门** | 去重后融合源 ≥3；单一真人源权重 <0.60；成品-真人相似度 <0.60；公众人物相似度 <0.50；综合肖像风险 ≤0.35；来源均授权/合成/公有；`must_multi_face_fusion`+`forbid_single_real_clone` 未被关 | 直接 REJECTED，**人工不可翻**（`HardBlockOverrideError`），只能换合规融合源重生成 | `identity_dna/gate.py` |
| **SceneDNA 自然场景门** | 自然度 ≥0.70；剧本匹配 ≥0.65；布局可用 ≥0.60；版权风险 ≤0.30；`source_kind ≠ pure_ai_generated`（当 `must_be_natural`）；素材来源在白名单 | REJECTED → 故事 BLOCKED，换自然原子路线重生成 | `scene_dna/gate.py` |
| **Final Review 总审核门** | 见 §3 五维全部达标 | 阻断发布，按维度打回对应模块或转人工 | 待建 |

### 软门（可自动重试/降级，记录原因）

| 门 | 通过标准 | 失败动作 |
|----|---------|----------|
| ScriptBrain G3 | 每集齐 HOOK/CONFLICT/CLIFFHANGER；无未声明人物/道具引用；无"当前生成能力做不了"的场景；留存分 ≥0.60 | 软失败 → 自动重试改写（§4）；含 hard 场景 → 人工门 |
| PropDNA 连续性门 | 状态覆盖 ≥0.80；`continuity_critical` 道具状态零缺失；无真实品牌标识 | 重生成缺失状态版本 |
| DirectorDNA 合约门 | 每镜头绑定的 Scene/Identity/Prop 均 `gate_passed=True` 且带 `asset_hash`；连续性账本 4 类检查全过；单镜头 1–12 秒 | 打回 DIRECTING 重排；连续性冲突打回对应模块 |
| PerformanceDNA 表演门 | 每句台词有 LINE 节拍；特写有微表情；强情绪(≥0.85)有生理反应；同框人物都有节拍 | 打回 PERFORMANCE 补节拍 |
| Render 单镜头技术门 | 产物存在且是 video/*；时长与合约差 ≤0.05s；参考 hash 未被掉包；身份一致性 ≥0.75；场景一致性 ≥0.70 | 可重试错误→自动重试；一致性低→打回 |

### 人工门

| 门 | 触发条件 | 已实现 |
|----|---------|--------|
| 剧本关键节点 | G3 含 hard 场景 / 留存分不达标 | ✓ `script_gate3` 挂起 |
| 资产复核 | 场景版权风险 0.15–0.30（自动通过线与失败线之间） | ✓ `asset_gate_review` |
| QA critical | 产物层身份漂移等机器判不了的问题 | ✓ `shot_qa_critical` |
| **最终成片确认** | Final Review 五维通过后，人签字才发布 | 待建 |

---

## 3. Final Review 详细指标清单（本次核心新增设计）

**定位**：整集/整部渲染+后期完成后的一次总审，是 PUBLISHED 前的最后硬门。
**复用已有数据**：大量指标可直接读现成产物，不必重算——连续性账本、一致性报告、镜头 QA 报告、rough_cut、字幕轨。

| 维度 | 指标 | 数据来源 | 通过阈值 | 硬/软 |
|------|------|----------|----------|-------|
| **① 视觉一致性** | 主角全片同一 Master Pack | 连续性账本 `character_packs` | 唯一 | **硬** |
| | 跨镜头身份漂移分 | Render 一致性报告 `identity_consistency` | 全片均值 ≥0.80 且无单镜 <0.75 | 硬 |
| | 同场景资产不跳变 | 连续性账本 `scene_assets` | 每场唯一 | 硬 |
| | 服装/道具状态无倒退 | 连续性账本 `prop_states` | 时间轴单调 | 硬 |
| **② 叙事连贯** | 每集齐钩子/冲突/悬念 | shooting_script beats | 齐全 | 硬 |
| | 场景顺序与剧本一致 | shot_contract.order vs 剧本 | 一致 | 软 |
| | 台词全部有对应镜头与音轨 | 字幕轨 vs 镜头 vs 音轨 | 无孤儿台词 | 软 |
| | 悬念钩子强度 | Market Critic 复评 | ≥0.60 | 软 |
| **③ 技术质量** | 无黑屏/缺帧（首尾帧非纯黑、平均亮度>阈值） | ffprobe + 抽帧分析 | 达标 | 硬 |
| | 时长落在目标区间 | rough_cut `total_duration_sec` | 30–60s（样片） | 软 |
| | 分辨率/帧率一致 | ffprobe 每片段 | 全片统一 | 硬 |
| | 音画同步（音轨时长=视频时长） | Audio&Post 输出 | 偏差 ≤0.1s | 硬 |
| | 无渲染失败/缺失镜头 | rough_cut `missing_shots` | 空 | 硬 |
| **④ 安全合规** | 无单一真人高相似（复查） | IdentityDNA Gate 汇总 | 零 hard_block | **硬** |
| | 无未授权素材来源 | 各资产 `sources_used` | 全白名单 | 硬 |
| | 无真实品牌标识 | PropDNA `real_brand_marks` | 空 | 硬 |
| | 敏感内容扫描（暴力/色情/政治） | 多模态安全模型（接入后） | 通过 | 硬 |
| **⑤ 商业完成度** | 前 3 秒有钩子镜头 | 第一个镜头 beat_type=HOOK | 是 | 软 |
| | 结尾有悬念 | 末镜头 beat_type=CLIFFHANGER | 是 | 软 |
| | 有可发布素材（非占位） | rendered_shot `is_generated_footage` | 全 True | **硬** |
| | 多比例导出就绪（9:16/16:9/1:1） | Audio&Post 导出清单 | 齐 | 软 |

**判定规则**：任一**硬**指标不过 → Final Review REJECTED，按该指标的 `target` 打回对应模块；全部硬指标过、软指标有失分 → 转**人工门**（人看一眼签字）；全过 → 自动过，仍建议保留人工终确认。

**落地形态**：新增 `final_review/` 模块，产出 `final_review_report.json`（结构对齐现有 `qa/` 的 report），复用 `qa.agents` 的 `QAIssue`（带 `severity`+`target`），直接喂 Repair Planner。

---

## 4. 审核失败重试策略（统一，而非各自为政）

**现状问题**：重试逻辑散在三处——Render 的 `RetryableRenderError` vs `FatalRenderError`、Repair Planner 的精准打回、`retry_assets`。缺一个统一策略层。

**建议**：抽一个 `RetryPolicy`，按"失败是否确定性"分类：

| 失败类型 | 可自动重试 | 次数 | 重试时改什么 | 升级人工条件 |
|----------|-----------|------|-------------|-------------|
| 渲染限流/超时（RetryableRenderError） | ✅ | 2 | 原样重发 | 2 次仍失败 |
| 渲染一致性低（身份/场景分不够） | ✅ | 2 | 重发+强化参考权重 | 2 次仍低 |
| 渲染内容拒绝/参数非法（FatalRenderError） | ❌ | 0 | — | 立即人工 |
| ScriptBrain G3 软失败（留存/结构） | ✅ | 1 | 换钩子/调节拍模板（未来：换 LLM prompt） | 1 次仍不达标 |
| PerformanceDNA 节拍缺失 | ✅ | 1 | 补节拍 | 1 次仍缺 |
| DirectorDNA 连续性冲突 | ✅ | 1 | 按账本冲突项重排该镜头 | 冲突涉及资产层→打回 ASSET |
| PropDNA 状态缺失 | ✅ | 2 | 只补缺失状态版本 | 2 次仍缺 |
| **IdentityDNA 红线** | ❌ | 0 | — | **不重试、不升级，只能换合规源重新生成** |
| **SceneDNA 纯 AI 退化** | ✅（改路线） | 1 | 强制 `natural_atom_recompose` | 1 次仍违规→人工 |
| Final Review 硬指标失败 | ❌（先打回） | 0 | 按维度 target 精准打回对应模块 | 打回后重跑该段 |

**三条铁律**：
1. **红线永不自动重试**——IdentityDNA 硬拦截换汤不换药会被同一 Gate 再拦（已有测试 `test_retry_without_fixing_the_plan_stays_blocked`）。
2. **重试必须改参数**——不改参数的重试是浪费；每次重试记录 `retry_count` 与"改了什么"。
3. **精准打回，不重跑全片**——复用 Repair Planner 的"取最靠前 target 阶段"逻辑，只重跑受影响镜头（已实现）。

`RetryPolicy` 落点：Orchestrator 层新增，读 QA/Gate 报告的 `severity`+`target`，自动执行"软门重试→超限升级人工"，全程写事件流。

---

## 5. RLHF 优化审核模型：现阶段的诚实建议

**结论：现在不做 RLHF，先做"规则挖掘"（rule-mining），这才是你 docx 里"经验回流"的正确第一步。**

**为什么现在不做 RLHF**：
1. **红线不能学**——IdentityDNA 版权拦截是法律红线，必须是**可审计的确定性规则**，不能交给一个概率模型"大概率拦住"。RLHF 天然与红线冲突。
2. **没有量与标签**——RLHF 需要成千上万条带人工偏好标注的样本。你现在连稳定出片都还在打通，没有这个数据基础。
3. **规则型 Gate 现在够用且可解释**——每次拒绝都能说清"为什么"（`issues`+`hard_blocks`），这在早期比"学出来的黑箱"值钱得多。

**该做什么（分三阶段，成本递增）**：

| 阶段 | 做什么 | 现在能不能做 | 产出 |
|------|--------|-------------|------|
| **A. 决策日志（立刻做）** | 每次 Gate/QA/人工裁决都落结构化日志：输入特征、判定、`issues`、人工是否推翻、推翻理由 | ✅ 事件流已有雏形，补字段即可 | 未来一切优化的数据地基 |
| **B. 规则挖掘（有几百条后）** | 离线分析日志：哪些失败原因最高频？人工最常推翻哪类软门判定？据此调阈值、加新规则 | ✅ 纯离线分析脚本 | "阈值调优建议 + 新规则候选"报告，人工确认后进 Gate |
| **C. 学习型审核模型（远期）** | 仅对**软门**（叙事连贯、商业钩子、表演到位度这类主观项）训一个打分模型，用人工标注当监督信号——注意是监督学习/偏好排序，不是完整 RLHF | ⛔ 需要 B 阶段积累的标注量 | 软门越用越准；硬门永远保持规则型 |

**关键原则**：**硬门永远规则型（可审计），只有软门才考虑学习型**。这条要写死进架构。

---

## 6. 当前最该优先加强的 3 个子模块（按真实关键路径排序）

真实关键路径 = "从 mock 到能交付一条**真实画面**的样片"缺什么。

**① Render Brain 真实后端 —— 最高优先（瓶颈已探明）**
- Kling 已验证真能出片（5.1s/720p）。差的是把它写成 `KlingBackend(BaseAsyncRenderBackend)`——我 Phase 4 预留的 `submit/poll/download` 三个抽象正好对应 Kling 的 `text2video→轮询→下载 CDN`。
- **必须配套**：硬性预算闸（单会话最多 N 次生成 + 每次提交前打印"约耗 X units 是否继续"）。上次我探测误提交 4 次烧了 ~73 units，这个教训要做进代码。

**② 真实资产图像 —— 与①并列（否则①只能 text2video）**
- image2video 需要真实参考图，但 IdentityDNA/SceneDNA 现在只产结构、没有一张图。这是"角色一致性"的根：没有真实人脸参考图，Kling 生成的人物每个镜头都会漂移。
- 建议先接 **IdentityDNA 的真实多人脸融合出图**（红线 Gate 已经就位，接上真实融合器即可），再接 SceneDNA。LLM/图像 key 已验证可用。

**③ 成片级 Final Review —— 紧随其后**
- 它是发布前唯一的总闸，现在完全空缺（只有镜头级 QA）。§3 的指标清单里超过一半能直接读现成产物，落地成本不高，但价值极高——没有它，"能发布"这个状态没有任何依据。

> PerformanceDNA/DirectorDNA 相对已经够用（结构真实），**不建议现在投入**；ScriptBrain 接真实 LLM 重要但不在出片关键路径上（模板已能产合法剧本），排在这三个之后。

---

## 7. 结构上的合并 / 拆分建议

| 建议 | 类型 | 理由 | 落地 |
|------|------|------|------|
| **QA 拆成"镜头级 QA" + "成片级 Final Review"** | 拆 | 二者审的对象、时机、阻断力完全不同；混在一起会导致"镜头都过了但整片没人总审" | Final Review 独立成 `final_review/` 模块 + 新增 `FINAL_REVIEW` 状态 |
| **连续性逻辑合并成共享服务** | 合 | 现在**三处**在做连续性/身份漂移检查：`director/continuity.py`、`render/service.py` 的 `enforce_consistency`、`qa/agents.py` 的 `continuity_qa`。三处规则会漂移、难维护 | 抽 `continuity/` 共享服务，三处都调它，只维护一套漂移判定 |
| **重试逻辑统一成 RetryPolicy** | 合 | Render 重试、Repair 打回、retry_assets 三套并存 | §4 的 `RetryPolicy` 收口到 Orchestrator |
| **Audio & Post 保持单模块** | 不拆 | 现在一行没写，过早拆成 TTS/BGM/字幕多模块是过度设计 | 先一个 `audio_post/` 模块跑通，成熟再拆 |
| **Asset Brain 三大模型保持独立** | 不合 | 三者审核重点根本不同（版权/自然/连续），合并会削弱各自的硬门 | 维持现状 |

---

## 8. 建议的下一版落地顺序

1. **先做安全闸**：给 Render 加预算闸 + 提交前确认（避免再烧 units）——半天。
2. **KlingBackend + text2video 打通**：先不管参考图，证明"合约→真实视频→回填四元组→拼接"整条真链路——1–2 天。
3. **IdentityDNA 真实出图**：解锁 image2video，角色一致性才有根——最花时间，但最关键。
4. **Final Review 模块 + FINAL_REVIEW 状态**：把 §3 里能读现成数据的指标先做上——1–2 天。
5. **决策日志补全**（RLHF 的 A 阶段）：顺手做，为将来铺路。
6. Audio & Post、真实 LLM 接入——排在能出一条真实无声样片之后。

---

*本文档基于真实代码复盘，所有阈值/文件/常量均来自现有实现，可直接作为下一版开发的对照清单。*

---

# 附录 v2.1：QA Council 收敛（对齐 ChatGPT「AI Director QA Council」）

**问题**：只过了技术 Gate（ffprobe + 文件存在），没过导演/表演/剧情/商业 QA。
**收敛原则**：ChatGPT 的 14 个独立 Agent + 11 个 Gate 文件 → **重新归组成 3 层**，
不新建 14 个模块、不落 14 个 gate 文件。

## 9.1 三层结构（14 Agent 归组，不是 14 模块）

```
Layer 1  镜头级 Shot QA（增强现有 qa/）        —— 每个镜头出来就审，结构性
Layer 2  成片级 Final Review（5 大维度）        —— 整集/整部审，含判断层
Layer 3  Multimodal QA Council（汇总投票层）    —— 不是新 Agent，是 Final Review 的裁决器
```

| ChatGPT 的 14 Agent | 收敛到 | 层 |
|---------------------|--------|----|
| ① Script QA、⑥ Dialogue Director | 维度A 叙事与剧本 | Final Review |
| ② Shot Director、⑩ Editing Rhythm | 维度B 导演与节奏 | Final Review |
| ③ Performance Director、④ Character Consistency | 维度C 表演与角色一致性 | Shot QA + Final Review |
| ⑤ Prop QA（连续性） | 维度C（连续性）| Shot QA |
| ⑤ Prop QA（清晰度/乱码）、⑨ Visual Quality、⑦ Voice Casting、⑧ Lip-sync、⑪ Subtitle、⑫ Audio Mix | 维度D 视听技术 | Final Review |
| ⑬ Commercial Platform QA | 维度E 商业完成度 | Final Review |
| ⑭ Multimodal QA Council | = Layer 3 裁决器本身 | — |

## 9.2 两个 tier（对齐"硬门确定性、软门可重试"）

每个维度的指标分两种，这是全收敛的关键：

- **确定性 tier（结构性）**：读现成报告即可判定，**硬门**，**现在就能做**，不需要任何新模型。
- **判断 tier（多模态/LLM）**：真正的"像不像商业短剧"，**软门可重试**，**需要新能力**
  （Audio 模块 + 多模态视觉/LLM 裁判）。模型没接上时标 `PENDING`，
  **绝不因为"没检查"就当 PASS**（否则又回到"只过技术 Gate"的老问题）。

## 9.3 Final Review 五维指标清单（可执行）

标注：【现成】=读现有报告即可；【需Audio】=等 Audio&Post；【需多模态】=等视觉/LLM 裁判。

### 维度 A · 叙事与剧本
| 指标 | 类型 | 数据来源 | 通过标准 | 状态 |
|------|------|----------|----------|------|
| 每集齐 钩子/冲突/悬念 | 硬确定 | shooting_script beats + script_critic | 齐全 | 【现成】 |
| 无逻辑/连续性硬伤 | 硬确定 | script_critic `logic_issues`/`continuity_issues` | 空 | 【现成】 |
| 无"当前生成做不了"的场景 | 硬确定 | script_critic `production_difficulty` | level≠hard 或已人工放行 | 【现成】 |
| 对白像真人、不像旁白 | 软判断 | LLM 台词裁判 | 评分≥阈值 | 【需多模态】 |
| 每句对白对应画面 | 软判断 | 对白 vs 分镜 LLM 对齐 | 无脱节 | 【需多模态】 |

### 维度 B · 导演与节奏
| 指标 | 类型 | 数据来源 | 通过标准 | 状态 |
|------|------|----------|----------|------|
| 首镜是钩子、末镜是悬念 | 硬确定 | shot_contract `order`+`beat_type` | 是 | 【现成】 |
| 景别有搭配（非全静态堆叠） | 硬确定 | shot_contract `shot_type` 分布 | 特写/中/近景比例达标 | 【现成】 |
| 无缺失/失败镜头 | 硬确定 | rough_cut `missing_shots` | 空 | 【现成】 |
| 单镜时长合理、总长达标 | 硬确定 | rough_cut `total_duration_sec`+各段 | 30–60s，单镜1–12s | 【现成】 |
| 剪辑节奏不拖沓、情绪递进 | 软判断 | 多模态剪辑裁判 | 评分≥阈值 | 【需多模态】 |

### 维度 C · 表演与角色一致性
| 指标 | 类型 | 数据来源 | 通过标准 | 状态 |
|------|------|----------|----------|------|
| 主角全片同一 Master Pack | 硬确定 | 连续性账本 `character_packs` | 唯一 | 【现成】 |
| 渲染产物无身份漂移 | 硬确定 | Render 一致性报告 `structural_issues` + continuity_qa `RENDERED_IDENTITY_DRIFT` | 无 | 【现成】 |
| 身份一致性分 | 硬确定 | rendered_shot `metrics.identity_consistency` | 全片≥0.80，无单镜<0.75 | 【现成】 |
| 每镜有表演节拍、特写有微表情 | 硬确定 | performance_qa | 无 `NO_ACTING_BEATS`/`CLOSEUP_WITHOUT_EXPRESSION` | 【现成】 |
| 道具状态跨镜头连续 | 硬确定 | 连续性账本 `prop_states` + continuity_qa `PROP_STATE_SPLIT` | 单调不倒退 | 【现成】 |
| 表演真实、眼神有情绪、非摆拍 | 软判断 | 多模态表演裁判 | 评分≥阈值 | 【需多模态】 |

### 维度 D · 视听技术（含口型铁律）
| 指标 | 类型 | 数据来源 | 通过标准 | 状态 |
|------|------|----------|----------|------|
| **口型铁律**（见 9.4） | 硬确定 | shot_contract `mouth_policy` + 有无音轨 | 全部满足 | 【现成(规则)/需Audio(实测)】 |
| 分辨率/帧率全片统一 | 硬确定 | ffprobe 每段 | 一致 | 【现成】 |
| 无黑屏/缺帧 | 硬确定 | ffprobe + 抽帧亮度 | 达标 | 【现成】 |
| 音画同步 | 硬确定 | 音轨时长 vs 视频时长 | 偏差≤0.1s | 【需Audio】 |
| 女声/男声匹配、非全男声 | 硬确定 | 音轨声纹标签 vs 角色性别 | 匹配 | 【需Audio】 |
| 字幕大小/位置/同步/无乱码 | 硬确定 | 字幕烧录参数（固定铁律） | 达标 | 【需Audio】 |
| 对白/BGM/环境音音量比 | 软判断 | 音频电平分析 | 对白不被压过 | 【需Audio】 |
| 画面清晰、无 AI 假感/手部错误/文字乱码 | 软判断 | 多模态画质裁判 | 评分≥阈值 | 【需多模态】 |
| 道具清晰可信（手机/合同/付款记录无乱码） | 软判断 | 多模态 OCR/画质裁判 | 达标 | 【需多模态】 |

### 维度 E · 商业完成度
| 指标 | 类型 | 数据来源 | 通过标准 | 状态 |
|------|------|----------|----------|------|
| 前 5 秒有钩子镜头 | 硬确定 | 首镜 beat_type=HOOK + 时长 | 是 | 【现成】 |
| 结尾有悬念/反转 | 硬确定 | 末镜 beat_type=CLIFFHANGER | 是 | 【现成】 |
| 留存/钩子强度 | 硬确定 | market_hook_critic `retention_score`/`hook_strength` | ≥0.60 | 【现成】 |
| **有可发布素材（非占位）** | 硬确定 | rendered_shot `is_generated_footage` | 全 True | 【现成】 |
| 安全合规（无真人克隆/无未授权源/无真实品牌） | 硬确定 | 各 Gate `hard_blocks`/`sources_used`/`real_brand_marks` | 零违规 | 【现成】 |
| 整体商业感、平台适配 | 软判断 | 多模态商业裁判 | 评分≥阈值 | 【需多模态】 |

## 9.4 口型策略铁律（写死为确定性硬门）

给 Shot Contract 增加 `mouth_policy` 字段（DirectorDNA 生成时定，Final Review 复查）：

```
每个镜头必须声明 mouth_policy ∈ {
  SPEAKING_LIPSYNC,   # 说话且做口型 → 必须有对应角色音轨
  SILENT_CLOSED,      # 不说话 → 嘴必须闭合，禁止无声嘴动
  REACTION,           # 反应镜（无台词）
  OFFSCREEN_VO,       # 画外音/旁白
  PHONE_SCREEN,       # 手机/画面插入
  BACK_SHOT,          # 背影
}
```

**硬门判定**（确定性，现在就能做）：
1. `SPEAKING_LIPSYNC` 的镜头**必须**有对应角色的音轨（有 Audio 后实测；无 Audio 前至少校验合约声明了音轨需求）。
2. 有台词的镜头，若无法 lip-sync → 合约**必须**改判为 `REACTION/OFFSCREEN_VO/PHONE_SCREEN/BACK_SHOT` 之一，否则 REJECT。
3. `SILENT_CLOSED` 镜头**禁止**出现"嘴动无声"（有 Audio + 多模态后实测；现在先靠合约声明约束）。
4. 违反 → 打回 DirectorDNA 改镜头设计（软门可重试）；改判后仍无解 → 人工门。

## 9.5 QA Council 裁决机制（Layer 3）

```
输入：Shot QA 报告 + Final Review 五维指标
规则：
  ① 任一"硬确定"指标失败 → 直接否决，不投票，按该指标 target 精准打回
  ② "软判断"指标 → 多裁判加权打分（复用现有 Repair Planner 的 severity/target 结构）
  ③ 判断层模型未接入的维度 → 标 PENDING，commercial_ready 不得置 true
输出：final_review_report.json（结构对齐现有 qa/ 的 report，复用 QAIssue 的 severity+target）
铁律：commercial_ready=true 需五维硬门全过 + 判断层无致命项 + 人工终确认
```

## 9.6 复用现有报告一览（直接回答"哪些能复用"）

| 现有产物（真实字段） | 喂给 Final Review 的哪些指标 |
|----------------------|------------------------------|
| 连续性账本 `character_packs`/`scene_assets`/`prop_states` | 维度C 全部结构性一致性 |
| continuity_qa `RENDERED_IDENTITY_DRIFT`/`PROP_STATE_SPLIT`/`LEDGER_*` | 维度C 漂移/道具穿帮 |
| Render 一致性报告 `structural_issues` + `metrics.identity/scene_consistency` | 维度C 身份/场景一致性分 |
| performance_qa 全部 code | 维度C 表演节拍/微表情 |
| vision_qa `DURATION_MISMATCH`/`MISSING`/`FPS`/`NOT_REAL_FOOTAGE`/`PLACEHOLDER` | 维度B 缺失镜头、维度D 规格、维度E 非占位 |
| rough_cut `missing_shots`/`total_duration_sec`/`is_generated_footage`/`timeline` | 维度B 节奏结构、维度E 可发布 |
| script_critic `logic_issues`/`continuity_issues`/`production_difficulty` | 维度A 剧本硬伤 |
| market_hook_critic `retention_score`/`hook_strength`/`cliffhanger_strength` | 维度A/E 钩子留存 |
| shooting_script beats + shot_contract `order`/`beat_type`/`shot_type` | 维度A/B 结构、景别搭配 |
| 各 Gate `hard_blocks`/`sources_used`/`real_brand_marks` | 维度E 安全合规复查 |

**结论**：五维里所有【现成】指标——约占硬门的全部——**不需要任何新模型，现在就能做**，
纯粹是把已有报告在成片级再聚合投票一次。真正要等的是【需Audio】和【需多模态】的判断层。

## 9.7 落地顺序（对齐 §8，插入收敛后的 QA Council）

| 步骤 | 内容 | 依赖 | 与 §8 关系 |
|------|------|------|-----------|
| 已完成 | 预算闸、KlingBackend text2video | — | §8-1、§8-2 ✓ |
| **4a（先做，纯现成）** | Final Review 结构 tier + QA Council 裁决器：只用现有报告，全硬门确定性；`FINAL_REVIEW` 状态入状态机 | 无新模型 | §8-4 前半，**现在就能做** |
| **4b** | 口型铁律：合约加 `mouth_policy`，DirectorDNA 生成 + Final Review 复查（确定性规则层） | 无 | §8-4 |
| 3 | IdentityDNA 真实出图 → 解锁 image2video、角色一致性有根 | 图像模型 | §8-3 |
| 6a | Audio & Post → 解锁维度D 的【需Audio】指标（口型实测、声纹、字幕、音画同步） | TTS/唇同步 | §8-6 |
| 6b | 判断 tier：多模态视觉/LLM 裁判 → 维度A/B/C/D/E 的软判断项 | 视觉/LLM 裁判 | §8-6 之后 |
| 5 | 决策日志（QA Council 每次投票落日志）→ 为规则挖掘/未来软门学习铺路 | — | §8-5 |

**关键**：4a 是"把已过的技术检查在成片级重新聚合成确定性硬门"，**零新模型、现在可做**，
先把 Final Review 的骨架和 QA Council 裁决器立起来；判断层随 Audio 和多模态模型逐维点亮，
每点亮一维就从 PENDING 转为真实评分——**永远不让未检查的维度自动通过**。
