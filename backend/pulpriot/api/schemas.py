"""Pydantic models for requests and the typed parts of responses."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Decision = Literal["approve", "reject", "modify"]


class DecisionIn(BaseModel):
    decision: Decision
    actor_role: str = Field(..., description="Role the person is acting as (prototype identity, no login)")
    asof: str
    reason: str | None = None
    qty: float | None = Field(None, ge=0)

    @field_validator("reason")
    @classmethod
    def strip(cls, v: str | None) -> str | None:
        return v.strip() if isinstance(v, str) else v


class AskIn(BaseModel):
    question: str = Field(..., min_length=2, max_length=300)
    asof: str


class TransferIn(BaseModel):
    asof: str
    flavor: str
    donor: str
    recipient: str
    qty: float = Field(..., gt=0)


class CampaignIn(BaseModel):
    start_date: str
    end_date: str | None = None
    flavor: str | None = None
    kind: Literal["promotion", "influencer", "launch", "price", "other"] = "promotion"
    note: str | None = Field(None, max_length=200)
    expected_uplift: float | None = Field(None, ge=0)
    entered_by: str | None = None
    entered_asof: str | None = None


class LaunchIn(BaseModel):
    asof: str
    flavor: str = "DP"
    scale: float = Field(1.0, gt=0, le=5)
    similarity: dict[str, dict[str, float]] | None = None
    hypothetical: dict[str, Any] | None = None


class Meta(BaseModel):
    model_version: str
    config_hash: str
    source_hash: str
    workbook: str
    labels: dict[str, str]
    asof: dict[str, str]
    price: float
    margin: float


class ActionOut(BaseModel):
    id: str
    type: str
    flavor: str | None = None
    depot: str | None = None
    qty: float | None = None
    unit: str | None = None
    title: str
    rationale: str | None = None
    exposure_inr: float | None = None
    cost_inr: float | None = None
    net_benefit_inr: float | None = None
    deadline: str | None = None
    approver_role: str
    status: str
    status_at_asof: str
    created_asof: str
    decided_asof: str | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    approved_qty: float | None = None
    active: bool = True
    model_config = {"extra": "allow"}
