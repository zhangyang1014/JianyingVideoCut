"""任务级云端模型调用与费用统计契约。"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


CostSource = Literal["provider", "catalog", "manual", "unavailable", "cache"]
CallStatus = Literal["success", "failed", "quality_gate_failed", "cache_hit"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ModelUsage(BaseModel):
    input_tokens: Optional[int] = Field(default=None, ge=0)
    output_tokens: Optional[int] = Field(default=None, ge=0)
    reasoning_tokens: Optional[int] = Field(default=None, ge=0)
    cached_input_tokens: Optional[int] = Field(default=None, ge=0)
    total_tokens: Optional[int] = Field(default=None, ge=0)


class PriceSnapshot(BaseModel):
    currency: Literal["USD"] = "USD"
    input_per_token: Optional[str] = None
    output_per_token: Optional[str] = None
    reasoning_per_token: Optional[str] = None
    request_price: Optional[str] = None
    character_price: Optional[str] = None
    source: str = ""


class ModelCallRecord(BaseModel):
    call_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    execution_run_id: Optional[str] = None
    execution_generation: Optional[int] = None
    feature: str
    role: Optional[str] = None
    stage: Optional[str] = None
    provider_profile_id: Optional[str] = None
    provider: str
    requested_model: str
    actual_model: str
    tier: Optional[str] = None
    provider_request_id: Optional[str] = None
    attempt: int = Field(default=1, ge=1)
    status: CallStatus
    error_type: Optional[str] = None
    started_at: str
    completed_at: str = Field(default_factory=utc_now_iso)
    elapsed_ms: int = Field(default=0, ge=0)
    from_cache: bool = False
    cache_key: Optional[str] = None
    source_call_id: Optional[str] = None
    usage: ModelUsage = Field(default_factory=ModelUsage)
    currency: Literal["USD"] = "USD"
    cost_usd: Optional[str] = None
    cost_source: CostSource = "unavailable"
    price_snapshot: Optional[PriceSnapshot] = None
    usage_raw: dict[str, Any] = Field(default_factory=dict)

    def normalized_cost(self) -> Optional[str]:
        if self.cost_usd is None:
            return None
        return format(Decimal(self.cost_usd), "f")


class ModelCallList(BaseModel):
    items: list[ModelCallRecord]
    next_cursor: Optional[str] = None


class FeatureUsageSummary(BaseModel):
    feature: str
    label: str
    call_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: str
    unknown_cost_count: int
    actual_cost_count: int
    estimated_cost_count: int


class TaskUsageSummary(BaseModel):
    task_id: str
    currency: Literal["USD"] = "USD"
    cost_usd: str
    call_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    unknown_cost_count: int
    actual_cost_count: int
    estimated_cost_count: int
    tracking_started_at: Optional[str] = None
    features: list[FeatureUsageSummary] = Field(default_factory=list)
