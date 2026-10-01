from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    sector: str | None = None
    stage: str | None = None
    state: str | None = None
    incorporation_date: date | None = None
    incorporation_year: int | None = Field(default=None, ge=1800)
    annual_turnover_inr: int | None = Field(default=None, ge=0)
    funding_raised_inr: int | None = Field(default=None, ge=0)
    funding_requirement_inr: int | None = Field(default=None, ge=0)
    employee_count: int | None = Field(default=None, ge=0)
    num_founders: int | None = Field(default=None, ge=0)
    entity_type: str | None = None
    women_led: bool | None = None
    dpiit_recognized: bool | None = None
    use_of_funds: str | None = None
    declaration: str | None = None


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: str = Field(min_length=1)
    page: int = Field(ge=1)
    exact_clause: str = Field(min_length=1)


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    field: str
    operator: Literal[
        "equals",
        "not_equals",
        "greater_than",
        "greater_than_or_equal",
        "less_than",
        "less_than_or_equal",
        "in",
        "not_in",
        "contains",
    ]
    value: Any = None
    citations: list[Citation] = Field(default_factory=list)


class RuleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    status: Literal["met", "not_met", "unknown"]
    reason: str
    action_if_unmet: str | None = None
    citations: list[Citation] = Field(default_factory=list)

    @property
    def explanation(self) -> str:
        return self.reason


class Scheme(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str | None = None
    authority: str | None = None
    source_url: str | None = None
    file_path: str | None = None
    last_verified_date: date | None = None
    sectors: list[str] = Field(default_factory=list)
    stages: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)


class SchemeEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheme_id: str
    scheme_name: str
    status: Literal["eligible", "ineligible", "unknown"]
    summary: str
    rule_results: list[RuleResult] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)