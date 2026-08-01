# 短剧 AI 数字制片厂
## 全子模型多智能体详细分工 + 子模型完美协作自动成片流程
### CinemaDNA / DramaOS-X Factory Core · 详细落地规格

**文档版本**：v1.0
**生成日期**：2026-07-23
**用途**：完整描述每一个子模型需要哪些多智能体、每个智能体具体负责什么，以及所有子模型如何完美配合，最终自动产出可落地成片视频。
**配套文件**：
- CinemaDNA_DramaOS-X_Codex实现规格说明书_v1.1.md
- 三大资产模型_第一阶段可执行接口与Workorder数据结构_v1.0.md

---

## 一、系统总览：从剧本到成片的完整协作链

```
主题 / 热点 / 商业目标
        ↓
┌──────────────────────────────────────────────────────────────┐
│ 1. Pipeline Orchestrator（总控调度大脑）                      │
│    多故事并行状态机 + Human Hold + 成本控制                     │
└────────────────────────────┬─────────────────────────────────┘
                             ↓
┌──────────────────────────────────────────────────────────────┐
│ 2. ScriptBrain V4.1 旗舰编剧工厂                              │
│    输出：shooting_script.json + scene_export + character_list │
└────────────────────────────┬─────────────────────────────────┘
                             ↓
┌──────────────────────────────────────────────────────────────┐
│ 3. 统一资产大脑（三大专用大模型并行）                           │
│    SceneDNA（自然场景） + IdentityDNA（多人脸合成） + PropDNA  │
│    全部走 Workorder → Gate → Backflow 闭环                    │
└────────────────────────────┬─────────────────────────────────┘
                             ↓
┌──────────────────────────────────────────────────────────────┐
│ 4. DirectorDNA + ShotDNA + Continuity Director               │
│    输出：一镜头一合约（Shot Contract）                         │
└────────────────────────────┬─────────────────────────────────┘
                             ↓
┌──────────────────────────────────────────────────────────────┐
│ 5. PerformanceDNA（表演导演）                                 │
│    输出：Acting Beat + 微表情 + Blocking                      │
└────────────────────────────┬─────────────────────────────────┘
                             ↓
┌──────────────────────────────────────────────────────────────┐
│ 6. Render Brain + Model Router                               │
│    本地一致性优先 + 云端高质量补强，强制参考资产                 │
└────────────────────────────┬─────────────────────────────────┘
                             ↓
┌──────────────────────────────────────────────────────────────┐
│ 7. AudioDNA + Post Brain                                     │
│    配音、唇同步、BGM、字幕、多比例导出                          │
└────────────────────────────┬─────────────────────────────────┘
                             ↓
┌──────────────────────────────────────────────────────────────┐
│ 8. QA Critic + Repair Planner                                │
│    多模态质检 + 根因精准打回（不是整部重跑）                    │
└────────────────────────────┬─────────────────────────────────┘
                             ↓
                    成片通过 → 发布包 + 资产回流
                             ↓
              系统自我进化（DramaDNA + PromptGenome）
```

**核心并行原则**：
ScriptBrain 一旦输出 shooting_script.json 并提交，Pipeline Orchestrator 立刻允许启动下一部剧的 ScriptBrain。下游资产匹配、导演、渲染可异步推进。

---

## 二、每个子模型的多智能体详细分工

### 2.1 Pipeline Orchestrator（总控调度大脑）

**目标**：实现真正的多故事并行工厂，剧本提交后立即可以开新剧。

| 智能体名称 | 核心职责 | 输入 | 输出 | 关键决策点 |
|------------|----------|------|------|------------|
| **Workflow Planner Agent** | 全局任务规划与优先级排序 | 所有 story 状态、人工 Hold 队列、成本预算 | next_task_plan.json | 决定哪个故事先推进、是否触发新故事 |
| **State Manager Agent** | 维护所有故事的 stage 与 status | 各模块上报状态 | factory_status.json | 实时更新看板数据 |
| **Human Gate Manager** | 处理人工审核 Hold，不阻塞其他故事 | human_review_notes | hold_release_signal | 人工通过后自动续跑 |
| **Parallel Scheduler** | 真正实现「剧本提交后立刻启动新剧本」 | shooting_script 完成信号 | new_story_trigger | 核心并行逻辑 |
| **Cost / Budget Governor** | 控制云端调用比例与总成本 | 当前渲染成本、月预算 | routing_constraint | 超过预算时强制本地优先 |
| **Bundle Lifecycle Agent** | 创建、隔离、关闭 Active Production Bundle | story_id | bundle_id + 目录结构 | 确保无资产污染 |

**协作要点**：
Orchestrator 是唯一能创建新故事和关闭 Bundle 的模块。其他所有大脑只向它汇报状态，不直接互相调用。

---

### 2.2 ScriptBrain V4.1 旗舰编剧工厂

**目标**：产出可直接被三大资产模型和 Director 消费的标准化拍摄版 JSON。

| 智能体名称 | 核心职责 | 输入 | 输出 | 关键能力 |
|------------|----------|------|------|----------|
| **ScriptBrain Supervisor** | 总调度，决定谁写、谁审、何时返工、何时锁定 | 全流程状态 | script_pipeline_status.json<br>script_rework_tasks.json<br>script_final_approval_gate.json | 流程控制，不直接写大段剧本 |
| **Idea Miner** | 题材爆点挖掘 | 主题 / 热点 / 商业目标 | concept_brief.json<br>hook_map.json<br>market_angle_report.json | 前3秒钩子、反转、情绪压迫判断 |
| **Showrunner Brain** | 世界观、人物关系、主线冲突总控 | concept_brief | show_bible.json<br>character_relationship_map.json<br>world_rules.json<br>season_arc.json | 直接喂给 IdentityDNA / WorldDNA |
| **Plot Architect** | 分集结构与节奏 | show_bible | episode_outline.json<br>episode_beat_sheet.json<br>turning_point_map.json<br>cliffhanger_plan.json | 每集必须有开头钩子 + 中段冲突 + 结尾悬念 |
| **Episode Writer** | 写分集初稿 | episode_outline | episode_001_draft.json<br>episode_001_scene_list.json<br>episode_001_dialogue_draft.json | 结构化场景列表 |
| **Dialogue Master** | 台词润色与去AI味 | dialogue_draft | dialogue_polished.json<br>line_by_line_revision_report.json | 短、狠、有情绪、有身份差 |
| **Script Critic** | 逻辑、连续性、拍摄可行性审查 | 全剧本 | script_review_report.json<br>logic_issues.json<br>continuity_issues.json<br>production_difficulty_report.json | 检查场景是否太难生成、动作是否当前模型做不了 |
| **Market Hook Critic** | 爆款留存审查 | 全剧本 | retention_score_report.json<br>hook_strength_report.json<br>platform_adaptation_report.json | 前3秒、30秒冲突、每集结尾钩子打分 |
| **Human Review Gate × 3** | 人工关键闸门 | 题材 / 大纲 / 拍摄版 | human_review_g1/g2/g3_notes.json | 只有 Gate3 通过才进入资产与导演阶段 |

**协作要点**：
最终输出必须是 `episode_xxx_shooting_script.json` + `scene_export_for_cinemadna.json`，可被 SceneDNA / IdentityDNA / PropDNA / Director 直接解析。

---

### 2.3 SceneDNA 自然场景大模型（统一资产大脑 · 第一块）

**目标**：真实自然场景原子优先，缺失时从大数据抽象规律再按剧本重新布局，成功必须回流。

| 智能体名称 | 核心职责 | 输入 | 输出 | 详细能力 |
|------------|----------|------|------|----------|
| **Scene Requirement Parser** | 从 shooting_script 提取每场场景需求 | scene 节点 | scene_requirement.json | 时间、空间、光影、情绪、机位、氛围、空间可走位需求 |
| **Natural Scene Searcher** | 从授权公开大数据 / 内部库搜索自然场景 | requirement | candidate_scene_list.json | 优先真实影视/公开素材，记录来源与版权状态 |
| **Scene Atom Extractor** | 把场景拆解为可复用原子 | candidate scenes | scene_atoms.json | 布局结构、光影、材质、空间关系、情绪基调、可编辑点 |
| **3D Layout Composer** | 根据剧本需求重新组合原子，生成电影化空间 | atoms + requirement | composed_scene_layout.json | 支持 3D 立体空间变化与拼接，输出可被 Render 使用的布局描述 |
| **Scene Rights & Quality Gate** | 版权风险 + 自然度 + 布局可用性审查 | composed layout | scene_gate_report.json | 未过 Gate 禁止进入 Render |
| **Scene Workorder Agent** | 内部库缺失时自动创建补产工单 | requirement + search_result | scene_workorder.json | 与通用 Workorder 结构兼容 |
| **Scene Backflow Agent** | 成功场景原子沉淀回全局库 | approved asset | backflow_record.json | 打标签、质量分、可复用标记 |

**协作要点**：
Director 和 Render 只能使用经过 Gate 的场景资产。成功资产回流后，下一部剧可直接命中。

---

### 2.4 IdentityDNA 自然多人脸合成大模型（统一资产大脑 · 第二块）

**目标**：多人脸融合生成自然新脸（解决肖像权），支持年龄线与家族脸谱，严格 Production Gate。

| 智能体名称 | 核心职责 | 输入 | 输出 | 详细能力 |
|------------|----------|------|------|----------|
| **Character Requirement Parser** | 从剧本提取人物需求 | character 节点 + show_bible | character_requirement.json | 年龄、性别、气质、关系、隐藏秘密、角色类型 |
| **Multi-Face Fusion Agent** | **核心**：多张授权自然人脸融合生成新身份 | requirement + 多张源脸 | synthetic_face_candidates.json | 必须多人脸，禁止单一真人高相似克隆 |
| **AgeWeaver Agent** | 同一角色生成不同年龄版本 | base identity | age_line_pack.json | 18/35/58 岁自然过渡，保持身份一致性 |
| **FamilyResemblance Agent** | 生成基因相近的父母、子女、兄弟姐妹 | base identity + relations | family_pack.json | 血缘逻辑一致 |
| **Naturalness + Likeness Risk Gate** | 最严格审查 | identity pack | identity_gate_report.json | 自然度、相似度风险、家族一致性；高相似公众人物直接拒 |
| **Character Master Pack Builder** | 打包定妆照、多角度、表情基线 | approved identity | character_master_pack.json | 供 Render 和 Performance 使用 |
| **Identity Workorder Agent** | 缺失时创建工单 | requirement | identity_workorder.json | |
| **Identity Backflow Agent** | 回流 Character Registry / Cast Universe | master pack | backflow_record.json | 支持跨剧本长期复用 |

**协作要点**：
任何角色未过 Identity Gate，整个故事禁止进入 Render。Master Pack 是后续所有镜头的身份锁定依据。

---

### 2.5 PropDNA 道具生成大模型（统一资产大脑 · 第三块）

**目标**：按剧本生成自然道具，并保持跨镜头状态连续，成功回流。

| 智能体名称 | 核心职责 | 输入 | 输出 | 详细能力 |
|------------|----------|------|------|----------|
| **Prop Requirement Parser** | 从剧本提取道具需求与状态变化 | props 列表 + actions | prop_requirement.json | 外观、状态版本、剧情作用、是否关键连续 |
| **Prop Atom Retriever** | 优先从内部库检索 | requirement | prop_candidates.json | |
| **Prop Synthesizer** | 缺失时生成自然道具 | requirement | synthesized_prop.json | 支持多状态版本生成 |
| **Prop Continuity Agent** | 管理跨镜头/跨集状态时间线 | prop + state changes | prop_state_timeline.json | 例如：缴费单「完整→攥皱→放桌上」 |
| **Prop Rights & Quality Gate** | 版权 + 自然度 + 状态一致性 | prop asset | prop_gate_report.json | |
| **Prop Workorder Agent** | 缺失创建工单 | requirement | prop_workorder.json | |
| **Prop Backflow Agent** | 回流全局 PropDNA 库 | approved prop | backflow_record.json | 按状态存储可复用版本 |

**协作要点**：
Performance 和 Render 使用道具时必须引用正确的状态版本。Continuity Agent 与 Continuity Director 协同防止道具穿帮。

---

### 2.6 DirectorDNA / ShotDNA + Continuity Director

**目标**：把剧本 + 资产变成「一镜头一合约」，保证长剧一致性。

| 智能体名称 | 核心职责 | 输入 | 输出 | 详细能力 |
|------------|----------|------|------|----------|
| **Shot Strategy Agent** | 整体镜头策略与情绪曲线 | shooting_script + 资产 | shot_strategy.json<br>emotion_curve.json | 建立镜、反应镜、钩子镜分配 |
| **Shot Contract Generator** | 生成一镜头一合约 | strategy + 资产 hash | shot_contract.json（每个镜头） | 绑定场景、人物、道具、情绪、机位、时长、难度 |
| **Camera Intent Agent** | 机位与运镜意图 | contract | camera_params.json | 推拉摇移、景别、压迫感等 |
| **Continuity Ledger Agent** | 长剧一致性账本 | 所有 contract + 历史 | continuity_ledger.json | 跟踪秘密暴露、伤痕、道具状态、人物关系变化 |
| **Continuity Director** | 全局连续性总控 | ledger + 新 contract | continuity_check_report.json | 发现冲突立即打回对应模块 |

**协作要点**：
Shot Contract 是 Render 的唯一合法输入。没有合同的镜头禁止渲染。

---

### 2.7 PerformanceDNA（表演导演系统）

**目标**：让角色有灵魂，而不是僵硬表情。

| 智能体名称 | 核心职责 | 输入 | 输出 | 详细能力 |
|------------|----------|------|------|----------|
| **Acting Beat Planner** | 把台词与动作拆成表演节拍 | dialogue + actions | acting_beats.json | 每个节拍对应微表情与肢体 |
| **Micro-expression Designer** | 设计符合身份与情绪的微表情 | beat + character master pack | micro_expression_plan.json | 眼神、嘴角、眉心等细节 |
| **Blocking & Space Matching Agent** | 人物在场景中的走位与空间关系 | scene layout + characters | blocking_plan.json | 与 3D 布局匹配 |
| **Performance Bible Manager** | 维护角色专属表演圣经 | character + 历史成功表演 | performance_bible.json | 习惯动作、标志性微表情 |
| **Performance Driver Library Agent** | 调用真人动作驱动库（高级） | beat | driver_reference.json | 用真人动作注入灵魂（可选） |

**协作要点**：
Performance 输出直接进入 Shot Contract 的 performance 字段，Render 必须遵守。

---

### 2.8 Render Brain + Model Router

**目标**：按镜头类型动态路由最优模型，强制使用已过 Gate 的资产，保证一致性。

| 智能体名称 | 核心职责 | 输入 | 输出 | 详细能力 |
|------------|----------|------|------|----------|
| **Model Router Agent** | 根据镜头类型选择模型 | shot_contract | model_choice.json | 特写→Kling；连续对话→Seedance；复杂动作→混合 |
| **Reference Control Agent** | 强制注入场景、人物、道具参考 | 资产 hash + contract | controlled_payload.json | 防止角色漂移、场景穿帮 |
| **Consistency Enforcer** | 跨镜头身份与场景一致性检查 | 已生成镜头 + 新 contract | consistency_score.json | 低于阈值打回 |
| **Local/Cloud Cost Optimizer** | 本地优先，云端仅补高难度 | cost governor 约束 | routing_decision.json | 控制云端占比 |
| **Render Executor** | 实际调用模型生成 | payload | rendered_shot + task_id | 下载文件必须按 task_id 隔离 |

**协作要点**：
Render 只能接收带完整资产 hash 且 Gate 通过的 Shot Contract。生成后立即进入镜头级 QA。

---

### 2.9 AudioDNA + Post Brain

**目标**：配音、唇同步、BGM、字幕、多比例导出，形成可发布成片。

| 智能体名称 | 核心职责 | 输入 | 输出 | 详细能力 |
|------------|----------|------|------|----------|
| **Voice / TTS Agent** | 生成符合年龄与情绪的配音 | dialogue + character | voice_tracks.json | 情感匹配、声纹一致性 |
| **LipSync Agent** | 唇形同步 | voice + rendered_shot | lipsync_video | 高精度口型 |
| **BGM / SFX Agent** | 背景音乐与音效 | emotion_curve + scene | audio_plan.json | 卡点、情绪强化 |
| **Editor & Exporter Agent** | 粗剪、字幕、转场、多比例导出 | 所有镜头 + audio | final_package（9:16/16:9/1:1） | 平台适配 |
| **Subtitle Agent** | 自动字幕与样式 | dialogue | subtitle_tracks | |

**协作要点**：
Post 完成后进入总审 QA Critic。只有总审通过才能生成发布包。

---

### 2.10 QA Critic + Repair Planner

**目标**：全链路质检 + 根因精准打回，避免整部重跑。

| 智能体名称 | 核心职责 | 输入 | 输出 | 详细能力 |
|------------|----------|------|------|----------|
| **Vision QA Agent** | 多模态画面质检 | rendered_shot + contract | vision_qa_report.json | 人脸崩坏、动作畸变、光影不一致 |
| **Performance QA Agent** | 表演与情绪一致性 | acting_beats + video | performance_qa_report.json | 表情是否到位、是否符合身份 |
| **Continuity QA Agent** | 连续性检查 | ledger + 新镜头 | continuity_qa_report.json | 道具、伤痕、服装穿帮 |
| **Audio QA Agent** | 声音与口型检查 | voice + video | audio_qa_report.json | |
| **Global QA Aggregator** | 汇总全链路报告 | 所有 QA | global_qa_report.json | 决定通过 / 打回 / 人工 |
| **Repair Planner（o1风格）** | 根因分析与精准打回 | global_qa_report | repair_plan.json | 「女主表情呆滞 → 打回 PerformanceDNA 重写 Acting Beat」，而不是重跑全剧 |

**协作要点**：
Repair Planner 是成本控制的关键。它只打回最小必要模块，由 Orchestrator 重新调度。

---

### 2.11 自我进化层（DramaDNA + PromptGenome）

| 智能体名称 | 核心职责 | 输入 | 输出 |
|------------|----------|------|------|
| **DramaDNA Pattern Extractor** | 从成功/失败成片与竞品视频逆向提纯爆款公式 | 视频链接 + 成片 | pattern_cards.json |
| **PromptGenome Evolution Agent** | 自动总结最不易跑脸、最自然的提示词 | 成功 payload | evolved_prompts.json |
| **GrowthTwin** | 模拟观众完播率预测 | 成片特征 | retention_prediction.json |
| **System Self-Fix Agent** | 代码级审计与补丁建议 | 运行日志 + 错误 | patch_suggestions.json |

---

## 三、子模型完美协作自动成片流程（可落地时序）

### 阶段 A：剧本生产（可立即启动下一部）
1. Orchestrator 收到新故事触发
2. ScriptBrain 全流程运行（Idea → Showrunner → Plot → Writer → Dialogue → Critic → Market → Human Gate）
3. 输出 shooting_script.json + scene_export + character_list
4. **Orchestrator 立刻允许启动下一个故事的 ScriptBrain**（并行核心）

### 阶段 B：资产并行匹配与生成
5. SceneDNA、IdentityDNA、PropDNA **并行**接收需求
6. 各自执行：内部检索 → 缺失则 Workorder → mock/真实生成 → Gate
7. 全部 Gate 通过后，资产写入本剧 Bundle，并准备回流

### 阶段 C：导演与表演
8. DirectorDNA 读取剧本 + 已过 Gate 资产，生成 Shot Strategy + 每个镜头的 Shot Contract
9. Continuity Director 检查账本，发现冲突立即打回
10. PerformanceDNA 为每个合同填充 Acting Beat、微表情、Blocking

### 阶段 D：渲染
11. Render Brain 按 Shot Contract 路由模型，强制注入参考资产
12. 生成镜头，按 task_id 隔离下载

### 阶段 E：质检与精准修复
13. 镜头级 QA（Vision + Performance + Continuity）
14. 失败 → Repair Planner 根因分析 → 只打回对应模块（Performance / Scene / Identity / 某个镜头）
15. Orchestrator 重新调度被打回的模块，其他故事继续

### 阶段 F：后期与总审
16. AudioDNA + Post Brain 完成配音、唇同步、BGM、字幕、导出
17. QA Critic 总审
18. 通过 → 生成发布包（多比例）
19. 资产回流三大数据库 + Bundle 清理关闭

### 阶段 G：进化
20. DramaDNA 与 PromptGenome 从本次成功/失败中学习，更新模式库与提示词基因组

---

## 四、关键协作约束（保证可落地）

1. **所有下游只能使用过 Gate 的资产**。未过 Gate 的角色、场景、道具禁止进入 Shot Contract。
2. **Shot Contract 是渲染的唯一合法输入**。没有合同的镜头禁止生成。
3. **Repair 只打回最小模块**。禁止「一失败就全剧重跑」。
4. **剧本提交后立即允许新故事启动**。这是工厂产能的核心。
5. **四元组贯穿全流程**。任何文件、任务、下载都必须绑定。
6. **成功资产强制回流**。不回流的系统会越用越弱，回流的系统才是护城河。
7. **Human Gate 不阻塞其他故事**。人工审核时，其他故事继续推进。

---

## 五、给 Codex 的最终落地指引

1. 先实现 Pipeline Orchestrator 的状态机与四元组校验。
2. 再实现三大资产模型的 Phase 1 接口（已有详细数据结构）。
3. 实现 ScriptBrain 最小可运行版（先做第1集 shooting_script）。
4. 实现 Director → Performance → Shot Contract 链路。
5. 实现 Render 的 mock 路由与一致性检查。
6. 实现 QA + Repair Planner 的打回机制。
7. 最后补 Audio/Post 与 Web 看板。

只有当以上链路能用一份假剧本跑通「从 ScriptBrain 到最终发布包 + 资产回流」，且同时能启动第二部剧本时，才算达到「可自动生成成片」的第一阶段目标。

---

**文档结束**

*本文件完整描述了每个子模型的多智能体分工，以及它们如何在 Pipeline Orchestrator 的统一调度下，完美配合自动产出可落地的短剧成片视频。*
