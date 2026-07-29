# CinemaDNA / DramaOS-X Factory Core
## 短剧 AI 数字制片厂 · Codex 实现规格说明书 v1.1

**文档用途**：给 Codex / 任何编码 Agent 直接理解并实现的完整需求与架构规格  
**版本**：v1.1（2026-07-22 优化版）  
**项目状态**：纯概念与架构设计阶段。无任何可运行代码、无真实数据、无端到端验证。  
**核心原则**：先完整理解本文件，再写任何代码。禁止假设已有实现。

---

## 一、系统终极目标（必须达成的商业级能力）

构建一个**工厂级、多故事并行、资产闭环、自然资产优先**的短剧 AI 数字制片厂。

**不是**「输入一个主题 → 等一部剧全部做完 → 再开始下一部」的串行工具。  
**而是**「剧本模块完成后立即把任务交给下游模块，同时立刻可以启动下一部剧的剧本生产」的**连续流水线工厂**。

### 最终愿景（用户原话核心）

> 一个能读懂剧本场景需求，  
> 从内部 SceneDNA 大数据中调用自然场景原子，  
> 缺失时自动从网上大数据抽象场景规律，  
> 组合出新的电影化空间，  
> 同时调用 IdentityDNA 生成自然新脸、年龄线和家族脸谱，  
> 并把所有成功场景和身份资产沉淀回统一资产大脑的短剧 AI 数字制片厂。

### 必须实现的关键特性

1. **多故事并行连续生产**  
   - Story A 的剧本模块一旦输出 shooting_script.json 并提交给下游，系统立即可以启动 Story B 的剧本生产。  
   - 不需要等待 Story A 的视频渲染完成。  
   - Pipeline Orchestrator 维护多故事状态机，支持 Human Review Hold 时其他故事继续推进。

2. **统一资产大脑下的三大专用大模型**（本版本核心升级）  
   - **SceneDNA 自然场景大模型**：从网上大数据搜索自然场景 → 提取原子 → 根据剧本需求重新布局组合。  
   - **IdentityDNA 自然多人脸合成大模型**：多人脸融合生成自然新脸（解决肖像权与版权问题），支持年龄线与家族脸谱。  
   - **PropDNA 道具生成大模型**：根据剧本需求生成自然道具，并保持跨镜头状态连续。  
   - 三大模型产生的所有成功资产必须回流数据库，供后期新剧本直接调用。

3. **严格资产隔离与回流**  
   - 每个故事必须使用 Active Production Bundle（独立目录 + 四元组：story_id + bundle_id + task_id + asset_hash）。  
   - 成片后有价值资产回流全局 Asset Brain，临时文件清理，Bundle 关闭。

4. **全功能 Web 操作界面**  
   - 所有人工审核 Gate、状态监控、资产浏览、Workorder 审批、模型路由配置、成本看板，全部在网页完成。  
   - 用户无需操作命令行。

5. **Production Gate 铁律**  
   - 任何未过 Gate 的角色、场景、道具、镜头，禁止进入视频渲染。

---

## 二、总体五层架构（工厂级）

```
L0  基础设施层          算力（本地 GPU + 云端 API）、存储、队列、密钥、模型路由
L1  统一资产大脑        SceneDNA + IdentityDNA + PropDNA + Character Registry + FamilyDNA + 回流系统
L2  Pipeline Orchestrator  多故事状态机、并行调度、Human Review Hold、自动续跑
L3  Active Production Bundle  单剧独立生产包（严格隔离）
L4  执行大脑集群        ScriptBrain / Director / Performance / Render / Post / QA Critic
```

### Active Production Bundle 标准目录（必须强制）

```
_ACTIVE_PRODUCTION_BUNDLE/
├── 01_script
├── 02_cast
├── 03_scene
├── 04_props
├── 05_performance
├── 06_shots
├── 07_payloads          # 带 hash 的完整载荷
├── 08_submissions
├── 09_downloads         # 按 task_id 隔离
├── 10_outputs
├── 11_review
├── 12_asset_backflow
├── 13_archive
└── 14_trash
```

**铁律**：没有 story_id + bundle_id + task_id + asset_hash，一律禁止渲染、禁止提交、禁止注册下载。

---

## 三、统一资产大脑（护城河）——三大专用大模型详细设计

这是系统最核心的差异化竞争力。统一资产大脑不再是笼统的一个模块，而是由**三个高度专业化的大模型**协同构成，所有成功资产必须回流数据库，供后续新剧本直接调用。

### 3.1 SceneDNA 自然场景大模型（第一大专用模型）

**核心任务**：  
从网上大数据搜索自然真实场景 → 提取场景原子 → 根据剧本需求重新布局组合成电影化空间（支持 3D 立体空间变化与拼接）。

**为什么必须独立成大模型**：  
当前短剧画面场景布局不自然，必须坚持「真实场景原子优先」，而不是纯 AI 生图的假场景。缺失时从公开大数据抽象规律再组合，成功后回流，系统越用场景越真实、越丰富。

**多智能体协作结构**：

| 智能体 | 核心职责 | 输出 |
|--------|----------|------|
| **Scene Requirement Parser** | 读懂剧本每场的场景需求（时间、空间、光影、情绪、机位、氛围） | scene_requirement.json |
| **Natural Scene Searcher** | 从授权公开大数据 / 公开素材库搜索匹配的自然场景素材 | candidate_scene_list.json |
| **Scene Atom Extractor** | 把场景拆解为可复用原子（布局结构、光影、材质、空间关系、情绪基调） | scene_atoms.json |
| **3D Layout Composer** | 根据剧本需求重新组合原子，生成电影化可变化空间（支持 3D 布局） | composed_scene_layout.json |
| **Scene Rights & Quality Gate** | 检查版权风险、自然度、可编辑性、与剧本匹配度 | scene_gate_report.json |
| **Scene Workorder Agent** | 内部库缺失时自动生成补产工单 | scene_workorder.json |
| **Scene Backflow Agent** | 成功场景原子沉淀回全局 SceneDNA 数据库 | backflow_record.json |

**工作流**：  
剧本需求 → 内部库检索 → 有则直接引用 → 无则 Searcher 搜取 + Atom Extractor 提取 → Composer 按剧本重新布局 → Gate 通过 → 回流数据库。

**关键原则**：  
- 优先真实自然场景原子，禁止默认走纯生成路径。  
- 支持 3D 立体空间重建与可编辑拼接。  
- 所有成功资产必须回流，形成越用越强的场景库。

---

### 3.2 IdentityDNA 自然多人脸合成大模型（第二大专用模型）

**核心任务**：  
用**多张自然人脸**合成不同的自然新脸，根据剧本需要生成角色。为了解决肖像权与版权问题，必须走「多人脸融合」路线，禁止单一真人高相似克隆。同时支持年龄线演化与家族脸谱。

**为什么必须独立成大模型**：  
角色不稳定、AI 脸、欧美脸、版权风险是当前短剧最大痛点。必须用多源融合生成自然中国人脸，并通过严格 Production Gate 才能进入渲染。成功身份资产回流 Character Registry，支持跨剧本复用。

**多智能体协作结构**：

| 智能体 | 核心职责 | 输出 |
|--------|----------|------|
| **Character Requirement Parser** | 从剧本提取人物需求（年龄、性别、气质、关系、隐藏秘密） | character_requirement.json |
| **Multi-Face Fusion Agent** | 从多张授权自然人脸中融合生成自然新身份（核心解决肖像权） | synthetic_face_candidates.json |
| **AgeWeaver Agent** | 同一角色生成不同年龄版本（18/35/58 岁自然过渡） | age_line_pack.json |
| **FamilyResemblance Agent** | 生成基因相近的父母、子女、兄弟姐妹、通婚子代脸谱 | family_pack.json |
| **Naturalness + Likeness Risk Gate** | 检查自然度、相似度风险（禁止高相似公众人物）、家族一致性 | identity_gate_report.json |
| **Character Master Pack Builder** | 打包定妆照、多角度、表情基线、服装建议 | character_master_pack.json |
| **Identity Workorder Agent** | 缺失时生成补产工单 | identity_workorder.json |
| **Identity Backflow Agent** | 成功身份资产沉淀回 Character Registry / Cast Universe | backflow_record.json |

**工作流**：  
剧本人物需求 → 内部 Character Registry 检索 → 有则复用 → 无则 Multi-Face Fusion 生成新脸 → AgeWeaver + FamilyResemblance 扩展 → Gate 严格审查 → Master Pack 打包 → 回流数据库。

**关键原则**：  
- **必须多人脸合成**，严禁单一真人克隆。  
- 自然度 + 相似度风险 Gate 必须通过才能进入渲染。  
- 支持跨剧本长期复用（Cast Universe）。  
- 所有成功资产必须回流。

---

### 3.3 PropDNA 道具生成大模型（第三大专用模型）

**核心任务**：  
根据剧本需求生成自然道具，并保持跨镜头、跨集的状态连续（例如：缴费单从完整到撕毁、手机屏幕内容变化）。成功道具资产回流数据库，供后期新剧本调用。

**为什么必须独立成大模型**：  
道具是场景真实感的重要组成部分，且必须与剧情状态连续。单独做成专用模型，才能保证道具质量与可复用性，避免每次临时生成导致不一致。

**多智能体协作结构**：

| 智能体 | 核心职责 | 输出 |
|--------|----------|------|
| **Prop Requirement Parser** | 从剧本提取道具需求（外观、状态、剧情作用、是否需要变化） | prop_requirement.json |
| **Prop Atom Retriever** | 优先从内部库检索已有道具原子 | prop_candidates.json |
| **Prop Synthesizer** | 缺失时根据需求生成自然道具（支持状态版本） | synthesized_prop.json |
| **Prop Continuity Agent** | 管理道具跨镜头/跨集状态变化（完整→破损→丢弃等） | prop_state_timeline.json |
| **Prop Rights & Quality Gate** | 检查版权、自然度、与剧本匹配度 | prop_gate_report.json |
| **Prop Workorder Agent** | 缺失时生成补产工单 | prop_workorder.json |
| **Prop Backflow Agent** | 成功道具资产沉淀回全局 PropDNA 数据库 | backflow_record.json |

**工作流**：  
剧本道具需求 → 内部库检索 → 有则引用并检查状态连续 → 无则 Synthesizer 生成 → Continuity 管理状态 → Gate 通过 → 回流数据库。

**关键原则**：  
- 道具必须支持状态连续（同一道具在不同场景可有不同状态版本）。  
- 优先复用已有资产，减少重复生成。  
- 所有成功资产必须回流。

---

### 3.4 三大模型与统一资产大脑的协作关系

```
剧本需求
    ↓
Pipeline Orchestrator 分发
    ↓
┌─────────────────────────────────────────────────────────┐
│                  统一资产大脑（护城河）                    │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │  SceneDNA   │  │ IdentityDNA │  │   PropDNA   │     │
│  │ 自然场景大模型│  │ 多人脸合成  │  │ 道具生成器  │     │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘     │
│         │                │                │            │
│         └────────────────┼────────────────┘            │
│                          ↓                             │
│              Workorder 系统 + Production Gate           │
│                          ↓                             │
│              成功资产回流数据库（可被新剧本直接调用）      │
└─────────────────────────────────────────────────────────┘
    ↓
Active Production Bundle（本剧隔离使用）
    ↓
下游 Director / Render / QA
```

**核心闭环**：  
任何新剧本优先查询三大数据库 → 缺失才触发对应大模型生成 → Gate 通过 → 回流 → 下一剧本直接可用。  
系统越用，资产越丰富、越自然、越一致。

---

## 四、其他核心子模型（简要）

### 4.1 Pipeline Orchestrator（总控调度）
- 真正实现「剧本提交后立刻启动新剧本」的并行工厂。
- 多智能体：Workflow Planner、State Manager、Human Gate Manager、Parallel Scheduler、Cost Governor。

### 4.2 ScriptBrain V4.1 旗舰编剧工厂
- 7 个专业智能体 + 3 个人工审核闸门。
- 输出标准化 shooting_script.json + scene_export_for_cinemadna.json，可直接被三大资产模型消费。

### 4.3 DirectorDNA + PerformanceDNA + Render Brain + QA Critic
- Shot Contract（一镜头一合约）。
- 表演微表情与 Blocking。
- 动态模型路由（本地一致性优先 + 云端高质量补强）。
- 精准根因修复（不是失败全重跑）。

---

## 五、标准单剧生产流水线（与并行工厂的关系）

1. 触发（主题 / 热点 / 商业目标）
2. 洞察（Trend + DramaDNA）
3. **ScriptBrain** 生成结构化剧本 → **立即可以启动下一部剧的 ScriptBrain**
4. **三大资产模型并行匹配/生成**（SceneDNA + IdentityDNA + PropDNA）
5. Production Gate 审查
6. Director → Performance → Shot Contract
7. Render（强制参考 + 路由）
8. 镜头级 QA → 精准打回
9. Post（剪辑 / 字幕 / 配音 / 导出）
10. 总审 QA Critic
11. 发布包生成
12. **资产回流三大数据库** + Bundle 清理关闭

**关键点**：步骤 3 完成后，Orchestrator 立刻允许新故事进入步骤 3。三大资产模型可异步并行工作。

---

## 六、Web 操作界面要求

所有功能必须通过网页完成：
- 多故事工厂看板（实时 stage / status / Hold 原因）
- 人工审核 Gate 界面（题材 / 大纲 / 拍摄版 / one-shot / 资产 Gate）
- 三大资产浏览器（SceneDNA / IdentityDNA / PropDNA）
- Workorder 审批与状态
- 模型路由与成本看板
- Bundle 详情与日志查看
- 最终成片预览与发布包下载

---

## 七、生产铁规（绝对不可违反）

1. **多故事并行，而非串行等待**。
2. 所有生产行为必须绑定四元组（story_id + bundle_id + task_id + asset_hash）。
3. **三大资产模型优先查库，没有则补产并强制回流**。
4. 未过 Production Gate（场景 / 身份 / 道具）不得渲染。
5. 成片后必须资产回流 + Bundle 清理。
6. IdentityDNA 必须走多人脸融合，禁止单一真人高相似克隆。
7. 禁止复用旧 drama_0001 的任何污染路径。
8. 禁止在未明确确认的情况下进行付费渲染。

---

## 八、当前真实状态（给 Codex 的诚实起点）

- 无任何可运行代码
- 无项目目录结构
- 无 SceneDNA / IdentityDNA / PropDNA 真实数据
- 无端到端验证
- 仅有完整架构设计

**Codex 必须从零开始**：  
优先顺序建议：
1. 工厂骨架（Active Bundle + Orchestrator 多故事状态机 + 四元组校验）
2. 统一资产大脑骨架 + 三大模型接口定义（先 mock 检索与 Workorder）
3. IdentityDNA 多人脸融合 + Production Gate（解决角色版权与自然度）
4. SceneDNA 检索/组合 + PropDNA 基础闭环
5. ScriptBrain V4.1 最小可运行版
6. Director + Render 路由
7. QA + 回流 + Web 界面

**首个验收标准**：  
完成 1 个 30–60 秒标杆样片（1 场景、1 主角、5–8 镜头），角色通过 IdentityDNA Production Gate（多人脸合成），场景与道具来自对应大模型并成功回流，生产过程全程 Bundle 隔离，且系统支持同时启动第二部剧本。

---

## 九、系统整体优势总结

1. **真正的工厂并行**：剧本提交后立刻可开新剧，产能可扩展。
2. **三大专用资产大模型**：SceneDNA（自然场景原子 + 重新布局）、IdentityDNA（多人脸合成解决版权）、PropDNA（道具状态连续）。
3. **强制回流闭环**：成功资产沉淀数据库，系统越用越强，形成真正护城河。
4. **Production Gate 全覆盖**：场景、身份、道具全部过闸才能渲染。
5. **一镜头一合约 + Continuity**：长剧一致性有账可查。
6. **精准根因修复**：失败只打回对应部门。
7. **全 Web 操作**：真正可给非技术团队使用的工业系统。
8. **严格隔离与清洁度**：Active Bundle 防止资产污染。

---

## 十、给 Codex 的执行指令

1. 完整阅读并理解本文件，尤其是**三大专用资产大模型**的设计。
2. 确认「多故事并行、剧本提交后立即启动新剧本」是最高优先级生产模式。
3. 确认 SceneDNA、IdentityDNA、PropDNA 是独立但协同的三大专用模型，所有成功资产必须回流。
4. IdentityDNA 必须实现「多人脸融合」，严禁单一真人克隆路径。
5. 从工厂骨架开始写代码，禁止先写完整视频生成。
6. 所有输出必须结构化 JSON，并绑定四元组。
7. 任何渲染前必须检查三大资产的 Production Gate。
8. 所有人工交互点必须预留 Web 接口。
9. 每完成一个模块，更新状态文档，并保证可被其他故事并行调用。

---

**文档版本**：CinemaDNA / DramaOS-X Codex 实现规格说明书 v1.1  
**生成日期**：2026-07-22  
**对应项目**：短剧 AI 数字制片厂（SceneDNA + IdentityDNA + PropDNA + Asset Brain）  
**状态**：架构锁定（三大资产模型已细分），等待工程落地

---

*本文件在 v1.0 基础上，根据用户最新要求，将统一资产大脑进一步细分为 SceneDNA 自然场景大模型、IdentityDNA 自然多人脸合成大模型、PropDNA 道具生成大模型三大专用系统，并明确各自多智能体结构与回流机制。*
