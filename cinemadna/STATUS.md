# CinemaDNA Factory Core · 工程状态

**更新日期**：2026-07-24
**当前阶段**：Phase 6 —— **真实出片已打通**。
Kling 文生视频接进管线，一个 5 秒镜头可从"合约 → 真实视频 → 拼接"真链路跑通
（实测产出 720×1280 / 5.1s / H.264 真实 AI 视频）。
真实付费提交由**预算闸**强制前置保护（默认关闭、逐次确认、硬性上限、只允许 5 秒）。

Phase 3 基线（mock 全链路）仍在，见下文。

运行方式见 [README.md](README.md)。

---

## 已完成

### 通用地基层（`asset_brain/common/`）
- `quad.py` — 四元组强制校验（上下文级 / 资产级两档）
- `workorder.py` — 通用 Workorder + 状态机（非法迁移一律拒绝）
- `schemas.py` — schema_version 登记表、ISO 8601 UTC、Bundle 14 目录与路径约束
- `gate.py` — Gate Report 基类；`store.py` — 内存三大库 + 工单簿
- `bundle.py` — Active Production Bundle 落盘；`hashing.py` — asset_hash + 确定性打分
- `service_base.py` — 三大 Service 公共基类（不含任何业务判断）

### 三大资产模型（mock 生成 + 真实 Gate）
- `scene_dna/` — 需求解析 / 内部检索 / 工单 / mock 原子重组 / Gate / 回流
- `identity_dna/` — 多人脸融合 mock / 年龄线 / 家族脸谱 / **禁止单一真人克隆硬性 Gate** /
  Master Pack / 回流
- `prop_dna/` — 状态版本生成 / 跨镜头连续性 / Gate / 回流
- `facade.py` — `AssetBrainFacade` 统一入口、人工审核、**判负后重生成 `regenerate()`**

### L2 Pipeline Orchestrator（`orchestrator/`）
- `story.py` — 两轴状态模型：`stage`（14 个生产阶段）× `status`（RUNNING /
  WAITING_HUMAN / BLOCKED / CLOSED），扁平视图 `Story.state`；Repair 精准打回
- `pipeline.py` — 建故事+建 Bundle、ScriptBrain 槽位与并行信号、资产分发、
  人工闸门队列与自动续跑、`retry_assets()` 只重跑判负工单、`factory_status()` 看板

### L4 ScriptBrain（`scriptbrain/`，Phase 2）
- `brief.py` — 输入 brief（一句题材即可）；场景数 < 3 直接拒绝（凑不齐钩子/冲突/悬念）
- `library.py` — 4 个题材模板（都市逆袭/悬疑追凶/家庭伦理/都市写实兜底），
  含场景、人物、道具、节拍素材；场景带 `difficulty` 标记
- `agents.py` — 7 个 Agent 纯函数：Idea Miner → Showrunner → Plot Architect →
  Episode Writer → Dialogue Master → Script Critic + Market Hook Critic
- `script.py` — 组装 + **结构契约校验器** + `scene_export` / `character_list` 导出
- `service.py` — Supervisor：三道闸门（G1 题材 / G2 大纲 / G3 拍摄版）
- Orchestrator 对接：`run_scriptbrain()` / `approve_script_gate()` /
  `dispatch_assets()` 可省略剧本入参

### L4 DirectorDNA + Shot Contract（`director/`，Phase 3）
- `shot_contract.py` — 一镜头一合约。**Render 的唯一合法输入**：
  `require_render_ready()` 校验每个引用资产都过了 Gate 且带 asset_hash；
  签发时锁 `contract_hash`，渲染前重算比对，**改过就拒渲**
- `strategy.py` — 镜头表（镜头数按时长推导、节拍决定镜头语言、
  单人场景不排反应镜、无道具不排插入镜、每个道具至少一个镜头带到）
- `continuity.py` — 一致性账本 + 四类结构性冲突检查（身份漂移 / 场景漂移 /
  道具状态倒退 / 与剧本不符）
- `service.py` — 资产解析（查不到已回流的资产就拒绝签约）+ 全流程

### L4 PerformanceDNA（`performance/`，Phase 3）
- 表演节拍（台词 → LINE，同框其他人 → REACTION，无台词镜头 → ACTION）
- 微表情由「性格关键词 + 情绪强度 + 景别」决定，不是随机
- 角色表演圣经跨故事累积（引用带 story_id 前缀，因 shot_id 跨剧会重名）
- 处理完即**签发合约**

### L4 Render Brain（`render/`，Phase 3）
- `router.py` — 模型路由 + 成本优化。人脸特写强制云端且**永不为省钱降级**，
  云端占比超标时如实上报 `over_budget`
- `backend.py` — `RenderBackend` 协议 + `MockRenderBackend`。
  真实模型只需实现同一签名；mock 产物诚实标注 `is_real_media=false`
- `service.py` — 入口铁规校验 → 参考注入（强制注入资产 hash）→ 执行 →
  一致性检查（结构层硬判 + 打分层 mock）；下载按 task_id 隔离
- `assembly.py` — 粗剪时间线 + EDL + 字幕轨 + 30–60 秒目标核对。
  **不做任何视频处理**

### L5 网页控制台（`webui/`，Phase 5）
- `state.py` — 全厂共享一个 Orchestrator + 线程锁（FastAPI 并发下必须）+ 文件越界防护
- `services.py` — 「下一步是什么」推导、人工闸门三路由（剧本闸门 / 资产 Gate / QA critical）、
  资产与工单只读投影、成片视图
- `app.py` — FastAPI 路由 + 领域异常到 HTTP 的映射（红线单独给 `HARD_BLOCK`，前端据此禁用「通过」）
- `static/index.html` — 零构建单页前端（五个页面，无任何外链资源）
- `scripts/run_webui.py` — `python scripts/run_webui.py` → http://127.0.0.1:8700

### 工具
- `mocks/sample_shooting_script.json` — 手写假剧本（当作"外来剧本"样本，走同一道校验）
- `scripts/run_sample_pipeline_demo.py` — 样片通路演示（`--benchmark` 跑标杆样片）
- `scripts/run_phase2_demo.py` — 题材 → 剧本 → 资产回流的端到端演示
- `scripts/run_phase1_demo.py` — 工厂骨架与两条红线的端到端演示

---

## 七条验收标准对照

| # | 标准 | 状态 | 证据 |
|---|------|------|------|
| 1 | 工厂骨架可用（四元组 + Bundle + Orchestrator 状态机） | ✅ | `TestCriterion1FactorySkeleton`（4 用例，含状态机自洽性自检） |
| 2 | 三大资产模型 mock 闭环完整可跑 | ✅ | `TestCriterion2ThreeModelsClosedLoop` |
| 3 | IdentityDNA 硬性拦截单一真人克隆 | ✅ | `TestCriterion3IdentityHardBlock` + `test_identity_dna.py`（24 用例） |
| 4 | SceneDNA 拒绝纯 AI 退化 | ✅ | `TestCriterion4NaturalSceneFirst`（含整链路与重生成恢复） |
| 5 | 假剧本跑通 查库→建单→生成→Gate→回流 | ✅ | `TestCriterion5EndToEndFromScript` |
| 6 | 支持同时启动第二部剧本 | ✅ | `TestCriterion6ParallelProduction` |
| 7 | 核心路径有单元测试且通过 | ✅ | **214 passed**（Phase 1 部分 154 例全部保留且仍通过） |

---

## Phase 2 目标对照

| 目标 | 状态 | 证据 |
|------|------|------|
| 输入一个简单题材/方向 | ✅ | `ScriptBrainService().run("一句话")`，自动题材归类 |
| 产出 shooting_script.json + scene_export | ✅ | 另加 concept_brief / show_bible / episode_outline / character_list / 两份 Critic 报告 |
| 能被 AssetBrainFacade 消费 | ✅ | `test_scene_export_consumed_by_facade_directly` |
| 能被 Orchestrator 消费 | ✅ | `run_scriptbrain()` → 并行信号 → `dispatch_assets()` 免传参 |
| 对应测试 | ✅ | `tests/test_scriptbrain.py` 60 例 |

---

## Phase 3 目标对照（样片通路）

| 目标 | 状态 | 证据 |
|------|------|------|
| DirectorDNA + Shot Contract | ✅ | `tests/test_director.py` 34 例 |
| PerformanceDNA | ✅ | `tests/test_render.py` 表演组 |
| Render Brain 最小版（接口可对接真实模型） | ✅ | `RenderBackend` 协议 + `MockRenderBackend` |
| 完整演示通路 | ✅ | `scripts/run_sample_pipeline_demo.py` |
| 标杆样片指标 1 场景/1 主角/5–8 镜头/30–60 秒 | ✅ | `TestBenchmarkSample`：8 镜头 / 40.0 秒 |
| 所有测试继续通过 | ✅ | **295 passed** |

**实测标杆样片**：8 个镜头、40.0 秒、单场景单主角、连续性与一致性全过、
0 拒渲、Bundle 内 56 份 JSON 产物。`media_ready=false`。

---

## 明确尚未实现（保持 Phase 1+2 边界）

- 真实网络搜索与场景原子提取、真实多人脸融合算法、真实 3D 重建、真实图像/视频生成
- **ScriptBrain 不接 LLM**：内容来自题材模板库 + 确定性挑选。结构一定合法，
  内容谈不上创意；换真实编剧模型时只需替换 `agents.py` 各函数体
- 题材库只有 4 个模板；库外题材一律落到"都市写实"兜底
- **Render 没有产出任何真实画面**：`MockRenderBackend` 只产出结构清单，
  `media_type=mock/none`、`is_real_media=false`。模型登记表是占位，未接任何 API
- **拼接不做视频处理**：没有解码、转场、导出，只有时间线 + EDL + 字幕轨
- 一致性分数（identity/scene/motion）是 mock；只有"跨镜头同一人物必须同一
  Master Pack"这类结构判定是真的
- AudioDNA / Post Brain（配音、唇同步、BGM、字幕烧录、多比例导出）未实现
- QA Critic + Repair Planner 未实现：`request_repair()` 只有状态机，没有质检
- POST / PUBLISHED 阶段仍是空壳 `advance()`
- Cost / Budget Governor
- 持久化（全部在内存，进程退出即丢）
- 真正的并发执行：槽位是**语义约束**，不是线程/进程调度
- Web 前端

**除 IdentityDNA 硬性拦截（结构性检查）外，所有 Gate 分数都来自确定性伪随机 mock，
不具备任何真实质量评估意义。**

---

## 距离真正出一条 30–60 秒样片，还差什么

按"挡路程度"排序：

1. **真实的人脸资产**（最关键）：IdentityDNA 现在只产出结构，没有一张脸。
   要出片必须先有真实的多人脸融合产物（且仍须过硬性拦截）。
2. **真实的渲染后端**：实现一个 `RenderBackend`（调 API、轮询、下载到
   `09_downloads/<task_id>/`、返回同样的 rendered_shot 结构）。
   路由、参考注入、一致性检查、拒渲铁规都不用改。
3. **真实的场景资产**：SceneDNA 目前没有图像/3D 产物，参考注入注入的是空壳 hash。
4. **视频拼接**：接 ffmpeg，把 EDL 变成真实文件。
5. **镜头级 QA**：现在渲染完直接进 QA 阶段，但没有任何质检；
   没有 QA 就没有 Repair Planner 的输入。
6. **音频链路**：配音 / 唇同步 / BGM / 字幕烧录全部空白。

其余（持久化、Web、Cost Governor）不挡出片，但挡规模化。
