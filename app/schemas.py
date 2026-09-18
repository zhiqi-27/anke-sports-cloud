from app.broadcast_schemas import BroadcastPublicView
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
    broadcast_platforms: dict[str, str] = Field(default_factory=dict, max_length=20)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value

class Follow(StrictModel):
    type: Literal["team"]
    source_key: str = Field(min_length=1, max_length=160)


class ManualEventSource(StrictModel):
    """A personal calendar source independent from team follows."""

    event_id: str = Field(min_length=1, max_length=64)


class LinkOverride(StrictModel):
    event_key: str
    url: str = Field(max_length=2000)
    state: Literal["block", "pin"]


class Config(StrictModel):
    schema_version: Literal[1] = 1
    follows: list[Follow] = Field(default_factory=list, max_length=500)
    preferences: Preferences = Field(default_factory=Preferences)
    manual_events: list[ManualEventSource] = Field(default_factory=list, max_length=2000)
    link_overrides: list[LinkOverride] = Field(default_factory=list, max_length=2000)

    @field_validator("manual_events")
    @classmethod
    def unique_manual_events(cls, value):
        event_ids = [item.event_id for item in value]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("Manual event sources must be unique")
        return value


class SaveFollows(StrictModel):
    expected_revision: int
    follows: list[Follow] = Field(max_length=500)
    confirmation: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class FollowChangeSource(Follow):
    name: str
    demo: bool | None


class FollowImpactEvent(BaseModel):
    id: str
    title: str
    starts_at: str | None
    local_date: str | None
    time_precision: str
    past: bool
    demo: bool | None


class FollowImpactGroup(BaseModel):
    total: int
    future: int
    past: int
    items: list[FollowImpactEvent]


class FollowPreviewView(BaseModel):
    revision: int
    confirmation: str
    added_sources: list[FollowChangeSource]
    removed_sources: list[FollowChangeSource]
    added: FollowImpactGroup
    removed: FollowImpactGroup
    retained: FollowImpactGroup
    historical_retained: int
    result_count: int
    undated_count: int
    window_start: str
    window_end: str
    feed_paused: bool
    publication_pending: bool


class SavePreferences(StrictModel):
    expected_revision: int
    preferences: Preferences


class CalendarEventChange(StrictModel):
    expected_revision: int = Field(ge=0)


class AddLink(StrictModel):
    url: str = Field(min_length=8, max_length=2000)
    title: str = Field(default="", max_length=300)


class ImportInput(StrictModel):
    config: Config
    mode: Literal["merge", "replace"] = "merge"
    dry_run: bool = True
    expected_revision: int
    confirmation: str | None = None


class FeedAction(StrictModel):
    confirmed: bool


class AccountDeletionView(BaseModel):
    deleted: bool
    identity_cleanup: Literal["queued", "not_applicable"]
    external_cache: str


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
    logo_url: str | None = None


class EventResult(StrictModel):
    """A completed two-sided result in participant/title order."""

    away_score: int = Field(ge=0)
    home_score: int = Field(ge=0)
    winner: Literal["away", "home", "draw"]


class SourceView(ParticipantView):
    logo_url: str | None = None
    sport: str
    kind: str
    demo: bool


class SourceList(BaseModel):
    items: list[SourceView]


class CalendarSourceView(BaseModel):
    type: Literal["follow", "manual"]
    key: str
    name: str


class CalendarMembershipView(BaseModel):
    sources: list[CalendarSourceView]
    can_remove: bool


class LinkView(BaseModel):
    broadcast: BroadcastPublicView | None = None
    id: str
    url: str
    title: str
    kind: str
    platform: str
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
    result: EventResult | None = None
    provider: str
    source_url: str
    updated_at: str
    demo: bool
    included: bool
    calendar: CalendarMembershipView | None = None
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


class FeedView(BaseModel):
    revision: int
    updated_at: str
    paused: bool
    status: str
    event_count: int


class CalendarUserView(BaseModel):
    is_maintainer: bool = False
    id: str
    display_name: str
    revision: int
    config: Config
    feed: FeedView


class ProviderView(BaseModel):
    id: str
    last_success: str | None
    error: str
    enabled: bool
    consecutive_failures: int = 0
    next_attempt_at: str | None = None
    activity: Literal["idle", "queued", "running", "waiting"] = "idle"


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


class PublicFeedView(BaseModel):
    source_id: str
    name: str
    demo: bool
    status: Literal["unavailable", "pending", "updating", "error", "published"]
    url: str | None
    revision: int
    updated_at: str | None
    event_count: int
    local_only: bool
