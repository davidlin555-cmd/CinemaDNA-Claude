"""Web API 的请求模型 (Phase 5)"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateStoryRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    theme: str = Field(min_length=1, max_length=200, description="一句话题材/方向")
    priority: str = Field(default="normal", pattern="^(normal|high|critical)$")
    episode_count: int = Field(default=1, ge=1, le=20)
    scenes_per_episode: int = Field(default=3, ge=3, le=8)
    backend: str | None = Field(default=None, description="mock | placeholder")
    simulate: str | None = Field(
        default=None,
        description="模拟场景（用于在网页上复现审核/阻塞/红线路径），默认 none",
    )


class StepRequest(BaseModel):
    #: 留空 = 按当前 stage 自动决定下一步
    action: str | None = None


class ReviewRequest(BaseModel):
    approved: bool
    decided_by: str = Field(min_length=1, max_length=60)
    note: str = Field(default="", max_length=2000)
    #: 只裁决某一个资产 Gate；留空则裁决该故事全部待审 Gate
    gate_id: str | None = None


class GateDecisionRequest(BaseModel):
    approved: bool
    decided_by: str = Field(min_length=1, max_length=60)
    note: str = Field(default="", max_length=2000)
