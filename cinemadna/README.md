# CinemaDNA / DramaOS-X Factory Core — Phase 1–3

短剧 AI 数字制片厂：工厂骨架 + 编剧工厂 + 导演/表演/渲染链路。

**当前为 Phase 3：一句题材可跑通「剧本 → 资产 → Shot Contract → 渲染 → 粗剪时间线」。
但渲染是 mock —— 没有任何一帧真实画面，拼接也不做视频处理。**

## 快速开始：网页控制台（主操作方式）

```bash
pip install -r requirements-dev.txt
python scripts/run_webui.py
```

打开 **http://127.0.0.1:8700** ，在浏览器里完成全部人工操作：

| 页面 | 能做什么 |
|------|----------|
| 工厂看板 | 多故事实时 stage / status / Hold，一键「执行下一步」，新建故事 |
| 人工审核 | 待审闸门列表，通过 / 拒绝 + 填意见（写进状态机与事件流） |
| 资产浏览器 | SceneDNA / IdentityDNA / PropDNA 三库可复用资产 |
| 工单 | Workorder 列表、筛选、详情，Gate 直接审批 |
| 成片预览 | 播放器 + 时间线 + 字幕，下载 MP4 / VTT / EDL / 动态分镜 |

API 文档在 `/docs`（FastAPI 自动生成）。

## 命令行（可选，用于调试与回归）

```bash
python -m pytest                                        # 全部单元测试
python scripts/run_sample_pipeline_demo.py --benchmark  # 完整样片通路（标杆样片）
python scripts/run_phase2_demo.py                       # 题材 → 剧本 → 资产
python scripts/run_phase1_demo.py                       # 工厂骨架与两条红线
```

一句题材出剧本：

```bash
python scripts/run_phase2_demo.py --theme "婆媳矛盾与养老困局" --scenes 4
```

```python
from scriptbrain import ScriptBrainService
r = ScriptBrainService().run("县城女护士被高利贷追债")
r.shooting_script   # 完整拍摄版
r.scene_export      # 资产大脑直接消费的视图
```

运行环境：Python 3.10+，除 pytest 外无第三方依赖。

## 这套骨架保证了什么

| 铁规 | 落地位置 |
|------|----------|
| 任何生产行为绑定四元组 | `asset_brain/common/quad.py`，三大 Service 每个入口都校验 |
| 资产文件必须落在本剧 Bundle 内 | `asset_brain/common/bundle.py`，越界路径直接抛错 |
| 未过 Gate 不得回流 | 三个 `gate.py` + 各 Service 的 `backflow()` 二次校验 |
| **IdentityDNA 严禁单一真人克隆** | `identity_dna/gate.py` 硬性拦截，人工也不可推翻 |
| 自然场景原子优先，不许退化成纯 AI 生图 | `scene_dna/gate.py` 检查 `source_kind` |
| 剧本提交后立刻可开下一部 | `orchestrator/pipeline.py` 的 ScriptBrain 槽位模型 |
| 人工审核不阻塞其它故事 | 挂起只动 `status`，同时释放槽位 |
| 剧本必须能被下游接单 | `scriptbrain/script.py` 的结构契约校验器 |
| 没有合约的镜头禁止渲染 | `director/shot_contract.py` 的 `require_render_ready()` |
| 合约签发后不可篡改 | 签发即锁 `contract_hash`，渲染前重算比对 |
| 下载物按 task_id 隔离 | `09_downloads/<task_id>/`，每个镜头独立目录 |

## 目录

```
asset_brain/          L1 统一资产大脑
  common/             四元组 / Workorder 状态机 / Gate 基类 / Bundle / 内存三库
  scene_dna/          SceneDNA：自然场景原子检索与重组
  identity_dna/       IdentityDNA：多人脸融合 + 红线 Gate
  prop_dna/           PropDNA：道具状态版本与跨镜头连续性
  facade.py           AssetBrainFacade 统一入口
orchestrator/         L2 Pipeline Orchestrator 多故事状态机
scriptbrain/          L4 编剧工厂：brief → 7 Agent → 拍摄版 + scene_export
director/             L4 DirectorDNA：镜头表 + Shot Contract + 连续性账本
performance/          L4 PerformanceDNA：表演节拍 + 微表情 + 表演圣经
render/               L4 Render Brain：路由 + 参考注入 + 执行 + 一致性 + 粗剪
qa/                   镜头级质检 + Repair Planner（精准打回）
webui/                L5 网页主控：FastAPI + 零构建单页前端
mocks/                手写的假 shooting_script.json（外来剧本样本）
scripts/              端到端演示
tests/                单元测试 + Phase 1 验收对照测试 + 样片通路测试
```

## 全链路一览

```
题材 → ScriptBrain → shooting_script + scene_export
     → 三大资产（查库 → 建单 → Gate → 回流）
     → DirectorDNA（镜头表 + 合约 + 连续性）
     → PerformanceDNA（表演节拍 → 签发合约）
     → Render Brain（路由 → 参考注入 → 执行 → 一致性）
     → 粗剪（时间线 + EDL + 字幕轨）
```

## 两类 Gate 不要混淆

| | ScriptBrain 三道闸门 | 资产 Gate | IdentityDNA 硬性拦截 |
|---|---|---|---|
| 判什么 | 编辑质量（逻辑/连续性/留存） | 资产质量与版权 | 肖像权法律红线 |
| 人工可放行 | ✅ | ✅（软性问题） | ❌ **永远不可** |
| 代码位置 | `scriptbrain/service.py` | 三个 `gate.py` | `identity_dna/gate.py` |

详细状态与缺口见 [STATUS.md](STATUS.md)。

## 明确不做（Phase 1–3 边界）

- **没有一帧真实画面**：`MockRenderBackend` 只产出结构清单，
  `is_real_media=false`；模型登记表是占位，未接任何 API
- **拼接不做视频处理**：只有时间线 + EDL + 字幕轨，没有解码/转场/导出
- **ScriptBrain 不接 LLM**：内容来自题材模板库 + 确定性挑选，
  保证结构合法与可回归，不保证内容有创意
- 真实网络搜索、真实多人脸融合、真实 3D 重建未实现
- AudioDNA / Post / QA Critic / Repair Planner 未实现
- Web 前端、持久化存储未实现（全部在内存）

**除 IdentityDNA 硬性拦截、Shot Contract 渲染前置校验、连续性结构检查外，
所有分数均为确定性伪随机 mock，不具备真实质量评估意义。**
