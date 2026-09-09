from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class Preferences(StrictModel):
    timezone: str = "Asia/Shanghai"
    locale: Literal["zh-CN", "en"] = "zh-CN"
    watch_region: str | None = None
    spoiler_free: bool = True
    transparent: bool = True

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value


class Follow(StrictModel):
    type: Literal["team", "competition", "event", "series"]
    source_key: str = Field(min_length=1, max_length=160)


class CreatorFollow(StrictModel):
    channel_id: str = Field(min_length=1, max_length=80)
    scope_keys: list[str] = Field(default_factory=list, max_length=500)
    preview: bool = True
    recap: bool = True
    enabled: bool = True


class EventOverride(StrictModel):
    event_key: str = Field(min_length=1, max_length=220)
    state: Literal["include", "exclude"]


class LinkOverride(StrictModel):
    event_key: str
    url: str = Field(max_length=2000)
    state: Literal["block", "pin"]


class Config(StrictModel):
    schema_version: Literal[1] = 1
    follows: list[Follow] = Field(default_factory=list, max_length=500)
    creators: list[CreatorFollow] = Field(default_factory=list, max_length=200)
    preferences: Preferences = Field(default_factory=Preferences)
    event_overrides: list[EventOverride] = Field(default_factory=list, max_length=2000)
    link_overrides: list[LinkOverride] = Field(default_factory=list, max_length=2000)


class SaveFollows(StrictModel):
    expected_revision: int
    follows: list[Follow] = Field(max_length=500)


class SavePreferences(StrictModel):
    expected_revision: int
    preferences: Preferences


class AddLink(StrictModel):
    url: str = Field(min_length=8, max_length=2000)
    title: str = Field(default="", max_length=300)
    kind: Literal["live", "preview", "recap", "watch_along"]


class AddCreator(StrictModel):
    url: str = Field(min_length=3, max_length=2000)
    scope_keys: list[str] = Field(default_factory=list, max_length=500)
    preview: bool = True
    recap: bool = True
    expected_revision: int


class OverrideInput(StrictModel):
    expected_revision: int
    state: Literal["include", "exclude", "reset"]


class ImportInput(StrictModel):
    config: Config
    mode: Literal["merge", "replace"] = "merge"
    dry_run: bool = True
    expected_revision: int
    confirmation: str | None = None


class FeedAction(StrictModel):
    confirmed: bool


class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool = False
    request_id: str


class ParticipantView(BaseModel):
    id: str
    name: str
    short_name: str
    color: str


class SourceView(ParticipantView):
    sport: str
    kind: str
    demo: bool


class SourceList(BaseModel):
    items: list[SourceView]


class LinkView(BaseModel):
    id: str
    url: str
    title: str
    kind: str
    platform: str
    creator: str
    origin: str
    access: str
    regions: list[str]
    pinned: bool
    created_at: str


class EventView(BaseModel):
    id: str
    source_key: str
    competition_id: str
    sport: str
    title: str
    starts_at: str | None
    local_date: str | None
    time_precision: str
    timezone: str
    duration: int
    venue: str
    status: str
    participants: list[ParticipantView]
    provider: str
    source_url: str
    updated_at: str
    demo: bool
    included: bool
    links: list[LinkView]
    description: str
    description_in_feed: bool


class CoverageView(BaseModel):
    dataset: str
    complete: bool
    note: str


class EventList(BaseModel):
    items: list[EventView]
    coverage: CoverageView
    next_cursor: str | None


class CreatorView(CreatorFollow):
    name: str
    last_error: str
    sync_status: str
    last_synced_at: str | None
    websub_status: str


class ResolveCreator(StrictModel):
    url: str = Field(min_length=3, max_length=2000)


class CreatorIdentity(BaseModel):
    channel_id: str
    name: str
    url: str


class UpdateCreator(StrictModel):
    expected_revision: int
    scope_keys: list[str] = Field(max_length=500)
    preview: bool
    recap: bool
    enabled: bool


class CreatorRemovalImpact(BaseModel):
    automatic_removed: int
    manual_retained: int
    revision: int


class ReviewView(BaseModel):
    id: str
    video_id: str
    title: str
    url: str
    creator: str
    published_at: str
    event_id: str
    event_title: str
    starts_at: str | None
    kind: str
    reason_codes: list[str]
    rule_version: str
    updated_at: str


class ReviewList(BaseModel):
    items: list[ReviewView]


class ReviewDecision(StrictModel):
    decision: Literal["confirm", "ignore"]
    kind: Literal["preview", "recap"]
    expected_updated_at: str


class FeedView(BaseModel):
    revision: int
    updated_at: str
    paused: bool
    status: str
    event_count: int


class CalendarUserView(BaseModel):
    id: str
    display_name: str
    revision: int
    config: Config
    creators: list[CreatorView]
    feed: FeedView


class ProviderView(BaseModel):
    id: str
    last_success: str | None
    error: str
    enabled: bool


class ServiceStatusView(BaseModel):
    local_preview: bool
    firebase_configured: bool
    providers: list[ProviderView]
    integrations: dict[str, str]


class ImportPreviewView(BaseModel):
    added: int
    removed: int
    unresolved: list[str]
    revision: int
    confirmation: str
    applied: bool


class LinkAddedView(BaseModel):
    id: str
    event: EventView


class ConsentRequestView(BaseModel):
    client_id: str
    client_name: str
    redirect_uri: str
    scopes: list[str]
    resource: str
    expires_at: int


class ConsentDecision(StrictModel):
    approved: bool
    scopes: list[Literal["calendar:read", "calendar:write", "feed:read"]] = Field(max_length=3)


class ConsentRedirectView(BaseModel):
    redirect_url: str


class ConnectionView(BaseModel):
    id: str
    client_name: str
    scopes: list[str]
    resource: str
    created_at: str
    expires_at: int


class ConnectionList(BaseModel):
    items: list[ConnectionView]
