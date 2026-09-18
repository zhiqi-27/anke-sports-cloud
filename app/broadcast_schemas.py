from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Access = Literal["unknown", "free", "login", "subscription", "pay_per_view"]
ContentType = Literal["official_match", "reservation", "programme", "watch_along", "replay"]
RegionMode = Literal["unknown", "global", "include", "exclude"]


class BroadcastDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=64)
    url: str = Field(min_length=1, max_length=2000)
    title: str = Field(min_length=1, max_length=300)
    content_type: ContentType
    access: Access = "unknown"
    region_mode: RegionMode = "unknown"
    regions: list[str] = Field(default_factory=list, max_length=250)
    evidence_url: str = Field(min_length=1, max_length=2000)
    evidence_note: str = Field(min_length=10, max_length=1500)

    @field_validator("regions")
    @classmethod
    def clean_regions(cls, values):
        import re

        values = sorted(set(x.upper().strip() for x in values))
        if any(not re.fullmatch(r"[A-Z]{2}", x) for x in values):
            raise ValueError("Use two-letter region codes")
        return values

    @model_validator(mode="after")
    def region_consistency(self):
        if (self.region_mode in {"include", "exclude"}) != bool(self.regions):
            raise ValueError("Region list must match the explicit region rule")
        if not self.title.strip() or len(self.evidence_note.strip()) < 10:
            raise ValueError("Describe the source and exact event evidence")
        return self


class BroadcastEdit(BroadcastDraft):
    expected_revision: int = Field(ge=0)


class BroadcastDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    source_and_event_confirmed: bool
    valid_until: datetime

    @field_validator("valid_until")
    @classmethod
    def utc_date(cls, value):
        if value.tzinfo is None:
            raise ValueError("Timezone required")
        return value.astimezone(timezone.utc)


class BroadcastAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=3, max_length=500)


class DeviceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    platform_app: str = Field(min_length=1, max_length=100)
    os_version: str = Field(min_length=1, max_length=100)
    calendar_client: str = Field(min_length=1, max_length=100)
    region: str = Field(pattern=r"^[A-Z]{2}$")
    checked_at: datetime
    app_installed: bool
    exact_content: Literal["passed", "failed", "not_tested"]
    app_content: Literal["passed", "failed", "not_tested"]
    playback: Literal["passed", "failed", "not_tested"]
    conditions: str = Field(min_length=3, max_length=300)
    evidence_ref: str = Field(min_length=3, max_length=200)

    @model_validator(mode="after")
    def evidence_consistency(self):
        if not self.checked_at.tzinfo or self.checked_at > datetime.now(timezone.utc):
            raise ValueError("Observation must be a real past timestamp with timezone")
        if self.app_content == "passed" and (not self.app_installed or self.exact_content != "passed"):
            raise ValueError("App content pass requires installed app and correct content")
        if self.playback == "passed" and self.exact_content != "passed":
            raise ValueError("Playback pass requires exact content")
        return self


class BroadcastPublicView(BaseModel):
    platform_id: str
    platform_name: str
    mobile_opening: Literal["verified_https_app_link", "web_handoff"]
    content_type: ContentType
    content_label: str
    access_label: str
    region_label: str
    evidence_url: str
    reviewed_at: str
    valid_until: str | None
    network_status: str
    network_checked_at: str | None
    device_tests: list[dict]


class BroadcastView(BaseModel):
    id: str
    revision: int
    status: str
    draft: BroadcastDraft
    published: dict | None
    published_revision: int | None
    draft_changed: bool
    event_title: str
    event_demo: bool
    network_status: str
    network_checked_at: str | None
    next_check_at: str
    device_tests: list[dict]
    audit: list[dict]


class BroadcastList(BaseModel):
    items: list[BroadcastView]
    has_more: bool
