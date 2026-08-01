# 三大资产模型 · 第一阶段可执行接口定义与 Workorder 数据结构
## CinemaDNA / DramaOS-X Factory Core · Phase 1 Executable Spec

**文档版本**：v1.0
**生成日期**：2026-07-23
**用途**：给 Codex / 编码 Agent 直接落地的第一阶段可执行接口与数据结构
**对应主规格**：CinemaDNA_DramaOS-X_Codex实现规格说明书_v1.1.md
**当前状态**：架构锁定，无任何可运行代码。本文件定义最小可实现接口。

---

## 0. 通用约定（所有模块必须遵守）

### 0.1 四元组（强制）
任何资产操作、Workorder、Gate、Backflow 必须携带：

```json
{
  "story_id": "drama_0005",
  "bundle_id": "bundle_20260723_001",
  "task_id": "task_scenedna_001",
  "asset_hash": "sha256:..."
}
```

没有完整四元组的请求一律拒绝。

### 0.2 通用状态机
```text
PENDING → RUNNING → GATE_REVIEW → APPROVED / REJECTED → BACKFLOWED / FAILED
```

### 0.3 文件与命名规范
- 所有接口输入输出使用 JSON
- schema_version 字段必须存在
- 时间使用 ISO 8601 UTC
- 资产文件路径必须落在对应 Active Production Bundle 内

### 0.4 Phase 1 目标
只实现**接口骨架 + Workorder 流转 + Gate 判断 + Backflow 记录**。
不实现真实搜索、真实多人脸融合、真实 3D 重建。
用 mock 数据跑通「查库 → 缺失 → 生成 Workorder → Gate → 回流」闭环即可。

---

## 1. 通用 Workorder 数据结构（三大模型共用基础）

```json
{
  "schema_version": "cinemadna.workorder.v1",
  "workorder_id": "wo_scenedna_20260723_001",
  "workorder_type": "SCENE | IDENTITY | PROP",
  "story_id": "drama_0005",
  "bundle_id": "bundle_20260723_001",
  "task_id": "task_xxx",
  "asset_hash": null,
  "status": "PENDING",
  "priority": "normal | high | critical",
  "created_at": "2026-07-23T04:00:00Z",
  "updated_at": "2026-07-23T04:00:00Z",
  "requested_by": "scriptbrain | director | human",
  "requirement": {},
  "internal_search_result": {
    "found": false,
    "matched_asset_ids": [],
    "similarity_scores": []
  },
  "generation_plan": {},
  "gate_report": null,
  "result_asset_ids": [],
  "backflow_record_id": null,
  "error": null
}
```

**状态流转规则**：
- 创建时 status = PENDING
- 开始检索/生成时 → RUNNING
- 生成完成后必须先跑 Gate → GATE_REVIEW
- Gate 通过 → APPROVED，然后触发 Backflow → BACKFLOWED
- Gate 失败 → REJECTED，可重新生成或人工介入
- 任何异常 → FAILED

---

## 2. SceneDNA 自然场景大模型 · 第一阶段接口

### 2.1 核心接口定义

```python
# 伪代码接口，Codex 按此实现

class SceneDNAService:
    def parse_requirement(self, shooting_script_scene: dict, context: Quad) -> dict:
        """从剧本场景提取标准化需求"""
        ...

    def search_internal(self, requirement: dict, context: Quad) -> dict:
        """查询内部 SceneDNA 数据库，返回是否命中"""
        ...

    def create_workorder(self, requirement: dict, search_result: dict, context: Quad) -> dict:
        """内部缺失时创建 Workorder"""
        ...

    def mock_generate(self, workorder: dict) -> dict:
        """Phase 1 用 mock 生成场景原子与布局（真实搜索与3D后续实现）"""
        ...

    def run_gate(self, generated_asset: dict, requirement: dict, context: Quad) -> dict:
        """场景质量与版权 Gate"""
        ...

    def backflow(self, approved_asset: dict, workorder: dict, context: Quad) -> dict:
        """成功资产回流全局 SceneDNA 库"""
        ...
```

### 2.2 SceneDNA Workorder 扩展字段

在通用 Workorder 基础上，`requirement` 与 `generation_plan` 必须包含：

```json
{
  "requirement": {
    "scene_id": "EP001_SC001",
    "location_description": "县城老旧出租屋",
    "time_of_day": "深夜",
    "mood": "压抑、疲惫",
    "camera_intent": "中近景，压迫感",
    "required_atoms": ["室内布局", "光线", "家具陈设", "窗户"],
    "spatial_needs": "可支持人物走动与特写",
    "must_be_natural": true
  },
  "generation_plan": {
    "search_sources": ["internal_db", "authorized_public"],
    "atom_extraction_needed": true,
    "layout_recompose": true,
    "3d_support": false,          // Phase 1 可先 false
    "mock_mode": true
  }
}
```

### 2.3 SceneDNA Gate Report 结构

```json
{
  "schema_version": "cinemadna.scene_gate.v1",
  "gate_id": "gate_scene_001",
  "workorder_id": "wo_scenedna_...",
  "passed": false,
  "scores": {
    "naturalness": 0.0,
    "script_match": 0.0,
    "layout_usability": 0.0,
    "rights_risk": 0.0
  },
  "issues": [],
  "human_review_required": true,
  "decided_at": null,
  "decided_by": null
}
```

### 2.4 SceneDNA Backflow Record

```json
{
  "schema_version": "cinemadna.scene_backflow.v1",
  "backflow_id": "bf_scene_001",
  "asset_id": "scene_atom_xxx",
  "workorder_id": "wo_...",
  "story_id": "...",
  "bundle_id": "...",
  "asset_type": "SCENE_ATOM | COMPOSED_LAYOUT",
  "tags": ["出租屋", "深夜", "压抑"],
  "source": "generated | searched",
  "quality_score": 0.85,
  "reusable": true,
  "created_at": "..."
}
```

---

## 3. IdentityDNA 自然多人脸合成大模型 · 第一阶段接口

### 3.1 核心接口定义

```python
class IdentityDNAService:
    def parse_requirement(self, character_from_script: dict, context: Quad) -> dict:
        """从剧本人物描述提取标准化身份需求"""
        ...

    def search_registry(self, requirement: dict, context: Quad) -> dict:
        """查询 Character Registry / Cast Universe"""
        ...

    def create_workorder(self, requirement: dict, search_result: dict, context: Quad) -> dict:
        """缺失时创建身份 Workorder"""
        ...

    def mock_multi_face_fusion(self, workorder: dict) -> dict:
        """Phase 1 mock 多人脸融合（真实融合后续接入）"""
        ...

    def generate_age_line(self, base_identity: dict, ages: list) -> dict:
        """生成年龄线版本"""
        ...

    def generate_family_pack(self, base_identity: dict, relations: list) -> dict:
        """生成家族脸谱"""
        ...

    def run_gate(self, identity_pack: dict, requirement: dict, context: Quad) -> dict:
        """自然度 + 相似度风险 + 家族一致性 Gate（最关键）"""
        ...

    def build_master_pack(self, approved_identity: dict) -> dict:
        """打包 Character Master Pack"""
        ...

    def backflow(self, master_pack: dict, workorder: dict, context: Quad) -> dict:
        """回流 Character Registry"""
        ...
```

### 3.2 IdentityDNA Workorder 扩展字段

```json
{
  "requirement": {
    "character_id": "char_linwan",
    "name": "林晚",
    "age_range": "25-28",
    "gender": "female",
    "ethnicity_preference": "东亚/中国",
    "personality_keywords": ["压抑", "坚韧", "疲惫"],
    "role_type": "主角",
    "must_multi_face_fusion": true,
    "forbid_single_real_clone": true,
    "need_age_line": true,
    "need_family": false
  },
  "generation_plan": {
    "fusion_source_count": 3,
    "mock_mode": true,
    "age_targets": [25, 35, 50],
    "family_relations": []
  }
}
```

### 3.3 IdentityDNA Gate Report（最严格）

```json
{
  "schema_version": "cinemadna.identity_gate.v1",
  "gate_id": "gate_identity_001",
  "workorder_id": "wo_identity_...",
  "passed": false,
  "scores": {
    "naturalness": 0.0,
    "likeness_risk": 0.0,          // 越低越好，高相似公众人物必须拒
    "ethnicity_match": 0.0,
    "family_consistency": 0.0,
    "overall": 0.0
  },
  "hard_blocks": [
    "single_real_person_detected",
    "high_public_figure_similarity"
  ],
  "issues": [],
  "human_review_required": true,
  "decided_at": null,
  "decided_by": null
}
```

**硬性规则（Phase 1 就必须实现）**：
- `must_multi_face_fusion = true`
- 检测到单一真人高相似 → 直接 REJECTED
- likeness_risk 超过阈值 → REJECTED

### 3.4 IdentityDNA Backflow / Master Pack 结构

```json
{
  "schema_version": "cinemadna.character_master_pack.v1",
  "character_id": "char_linwan",
  "master_pack_id": "cmp_001",
  "base_face_asset_id": "...",
  "age_line": {
    "25": "asset_id_25",
    "35": "asset_id_35"
  },
  "family_pack": {},
  "expression_baseline": [],
  "reusable": true,
  "gate_passed": true,
  "backflow_at": "..."
}
```

---

## 4. PropDNA 道具生成大模型 · 第一阶段接口

### 4.1 核心接口定义

```python
class PropDNAService:
    def parse_requirement(self, prop_from_script: dict, context: Quad) -> dict:
        """从剧本提取道具需求与状态变化"""
        ...

    def search_internal(self, requirement: dict, context: Quad) -> dict:
        """查询内部 PropDNA 库"""
        ...

    def create_workorder(self, requirement: dict, search_result: dict, context: Quad) -> dict:
        ...

    def mock_synthesize(self, workorder: dict) -> dict:
        """Phase 1 mock 生成道具"""
        ...

    def manage_continuity(self, prop_asset: dict, state_timeline: list) -> dict:
        """管理跨镜头状态版本"""
        ...

    def run_gate(self, prop_asset: dict, requirement: dict, context: Quad) -> dict:
        ...

    def backflow(self, approved_prop: dict, workorder: dict, context: Quad) -> dict:
        ...
```

### 4.2 PropDNA Workorder 扩展字段

```json
{
  "requirement": {
    "prop_id": "prop_hospital_bill",
    "name": "医院缴费单",
    "description": "旧的纸质医院缴费单",
    "states_needed": ["完整", "被攥皱", "放在桌上"],
    "story_function": "建立女主经济压力",
    "must_be_natural": true,
    "continuity_critical": true
  },
  "generation_plan": {
    "mock_mode": true,
    "generate_state_variants": true
  }
}
```

### 4.3 PropDNA Gate Report

```json
{
  "schema_version": "cinemadna.prop_gate.v1",
  "gate_id": "gate_prop_001",
  "workorder_id": "wo_prop_...",
  "passed": false,
  "scores": {
    "naturalness": 0.0,
    "script_match": 0.0,
    "state_consistency": 0.0,
    "rights_risk": 0.0
  },
  "issues": [],
  "human_review_required": false
}
```

### 4.4 PropDNA Backflow 结构

```json
{
  "schema_version": "cinemadna.prop_backflow.v1",
  "backflow_id": "bf_prop_001",
  "prop_id": "prop_hospital_bill",
  "asset_ids_by_state": {
    "完整": "asset_xxx",
    "被攥皱": "asset_yyy"
  },
  "reusable": true,
  "continuity_supported": true
}
```

---

## 5. 统一调用入口（推荐给 Orchestrator 使用）

```python
class AssetBrainFacade:
    def request_scene(self, scene_req: dict, context: Quad) -> dict:
        """统一入口：SceneDNA 全流程"""
        ...

    def request_identity(self, char_req: dict, context: Quad) -> dict:
        """统一入口：IdentityDNA 全流程"""
        ...

    def request_prop(self, prop_req: dict, context: Quad) -> dict:
        """统一入口：PropDNA 全流程"""
        ...

    def get_workorder_status(self, workorder_id: str) -> dict:
        ...

    def approve_gate(self, gate_id: str, human_decision: dict) -> dict:
        """人工审核 Gate 接口（Web 调用）"""
        ...
```

---

## 6. Phase 1 最小可运行验收标准

必须全部满足才算 Phase 1 完成：

1. 能创建带完整四元组的 Workorder（三种类型都能创建）
2. 内部检索接口存在（可返回 mock 命中/未命中）
3. 未命中时能自动创建 Workorder 并进入 RUNNING
4. mock 生成后能产出 Gate Report
5. IdentityDNA Gate 能识别并拒绝「单一真人克隆」模拟情况
6. Gate 通过后能写入 Backflow Record
7. 所有记录可被 Active Production Bundle 目录正确存放
8. 提供简单的状态查询接口（供后续 Web 看板使用）
9. 整个流程可用一份假 shooting_script.json 跑通一次完整闭环

**明确不做（Phase 1）**：
- 真实网络搜索
- 真实多人脸融合算法
- 真实 3D 场景重建
- 真实图像/视频生成
- 完整 Web 前端（可先用 API + 简单测试脚本）

---

## 7. 建议的目录与文件结构（Phase 1）

```text
cinemadna/
├── asset_brain/
│   ├── __init__.py
│   ├── common/
│   │   ├── quad.py
│   │   ├── workorder.py
│   │   └── schemas.py
│   ├── scene_dna/
│   │   ├── service.py
│   │   ├── workorder.py
│   │   └── gate.py
│   ├── identity_dna/
│   │   ├── service.py
│   │   ├── fusion_mock.py
│   │   └── gate.py
│   ├── prop_dna/
│   │   ├── service.py
│   │   └── gate.py
│   └── facade.py
├── tests/
│   └── test_asset_brain_phase1.py
└── mocks/
    └── sample_shooting_script.json
```

---

## 8. 给 Codex 的执行指令

1. 严格按照本文件定义的 JSON 结构实现。
2. 先实现通用 Workorder + 四元组校验。
3. 再分别实现三个 Service 的 mock 版本。
4. IdentityDNA 的 Gate 必须包含「禁止单一真人克隆」硬逻辑。
5. 所有成功路径必须写入 Backflow Record。
6. 写完整的单元测试，用假剧本跑通一次「查库 → Workorder → Gate → Backflow」。
7. 不要实现真实生成能力，保持 Phase 1 边界清晰。
8. 完成后更新状态文档，标明「三大资产模型 Phase 1 接口已就绪」。

---

**文档结束**

*本文件是 v1.1 主规格的可执行补充，专门服务第一阶段落地。三大模型的真实能力（搜索、多人脸融合、3D）将在后续阶段替换 mock。*
