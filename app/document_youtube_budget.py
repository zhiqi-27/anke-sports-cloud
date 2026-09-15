"""Project-wide reservations before YouTube HTTP, committed independently of content."""

from datetime import datetime, timedelta, timezone

from app.document_accounts import document
from app.document_store import Conflict, StoreError, Write
from app.provider_adapters import provider_key
from app.security import digest, problem
from app.youtube_rules import COSTS, Reservation, wait_error, window


def clock():
    return datetime.now(timezone.utc)


class Budget:
    def __init__(self, store, cfg):
        self.store, self.cfg = store, cfg

    def partition(self):
        if not self.cfg.youtube_project_id:
            problem("YOUTUBE_PROJECT_REQUIRED", "请先配置独立 YouTube 项目 ID", 503)
        return "youtube-budget:" + digest(self.cfg.youtube_project_id)

    def configured(self):
        return bool(self.cfg.youtube_project_id and provider_key("YOUTUBE_API_KEY", self.cfg))

    def read(self):
        row = self.store.get("indexes", self.partition(), "daily")
        if row:
            value = row.get("payload", {})
            try:
                valid = (
                    row["kind"] == "youtube_budget"
                    and datetime.strptime(value["period"], "%Y-%m-%d").date().isoformat() == value["period"]
                    and type(value["daily_limit"]) is int
                    and value["daily_limit"] >= 1
                    and type(value["reserved_units"]) is int
                    and value["reserved_units"] >= 0
                    and type(value.get("search_reserved_calls", 0)) is int
                    and value.get("search_reserved_calls", 0) >= 0
                    and (
                        value["blocked_until"] is None
                        or (
                            datetime.fromisoformat(value["blocked_until"]).utcoffset() is not None
                            and value["reason"]
                            in {"YOUTUBE_BUDGET_EXHAUSTED", "YOUTUBE_QUOTA_EXHAUSTED", "YOUTUBE_RATE_LIMITED"}
                        )
                    )
                )
            except (KeyError, TypeError, ValueError):
                valid = False
            if not valid:
                raise StoreError("YOUTUBE_BUDGET_INVALID")
        return row

    def prepared(self, old, instant):
        period, _ = window(instant)
        value = (
            dict(old["payload"])
            if old
            else {
                "period": period,
                "daily_limit": self.cfg.youtube_daily_budget,
                "reserved_units": 0,
                "search_daily_limit": self.cfg.youtube_search_daily_budget,
                "search_reserved_calls": 0,
                "search_blocked_until": None,
                "blocked_until": None,
                "reason": "",
            }
        )
        if value["period"] > period:
            raise StoreError("YOUTUBE_CLOCK_MOVED_BACKWARD")
        if value["period"] < period:
            value.update(
                period=period,
                reserved_units=0,
                daily_limit=self.cfg.youtube_daily_budget,
                search_daily_limit=self.cfg.youtube_search_daily_budget,
                search_reserved_calls=0,
                search_blocked_until=None,
            )
        else:
            value["daily_limit"] = min(value["daily_limit"], self.cfg.youtube_daily_budget)
            value["search_daily_limit"] = min(
                value.get("search_daily_limit", self.cfg.youtube_search_daily_budget),
                self.cfg.youtube_search_daily_budget,
            )
            value["search_reserved_calls"] = value.get("search_reserved_calls", 0)
            value["search_blocked_until"] = value.get("search_blocked_until")
        value["updated_at"] = instant.isoformat()
        return value

    def commit(self, old, value):
        pk = self.partition()
        row = document(pk, "daily", "youtube_budget", **value)
        self.store.batch(
            "indexes",
            pk,
            [Write("replace" if old else "create", "daily", row, old["_etag"] if old else None)],
        )

    def reserve(self, endpoint):
        if endpoint not in COSTS:
            raise ValueError("YOUTUBE_ENDPOINT_NOT_BUDGETED")
        for _ in range(32):
            instant = clock()
            period, reset = window(instant)
            old = self.read()
            value = self.prepared(old, instant)
            error = None
            if value["blocked_until"] and datetime.fromisoformat(value["blocked_until"]) > instant:
                error = wait_error(value["reason"], datetime.fromisoformat(value["blocked_until"]), instant)
            elif endpoint == "search" and value["search_blocked_until"] and datetime.fromisoformat(
                value["search_blocked_until"]
            ) > instant:
                error = wait_error(
                    "YOUTUBE_SEARCH_BUDGET_EXHAUSTED",
                    datetime.fromisoformat(value["search_blocked_until"]),
                    instant,
                )
            elif endpoint == "search" and value["search_reserved_calls"] >= value["search_daily_limit"]:
                value["search_blocked_until"] = reset.isoformat()
                error = wait_error("YOUTUBE_SEARCH_BUDGET_EXHAUSTED", reset, instant)
            elif value["reserved_units"] + COSTS[endpoint] > value["daily_limit"]:
                value.update(blocked_until=reset.isoformat(), reason="YOUTUBE_BUDGET_EXHAUSTED")
                error = wait_error(value["reason"], reset, instant)
            else:
                value["reserved_units"] += COSTS[endpoint]
                if endpoint == "search":
                    value["search_reserved_calls"] += 1
                value.update(blocked_until=None, reason="")
            try:
                self.commit(old, value)
            except Conflict:
                continue
            if error:
                raise error
            return Reservation(self.cfg.youtube_project_id, period, reset, endpoint)
        raise StoreError("YOUTUBE_BUDGET_BUSY", retryable=True, retry_after=1)

    def upstream_wait(self, ticket, code, seconds=60):
        if ticket.project != self.cfg.youtube_project_id or code not in {
            "YOUTUBE_QUOTA_EXHAUSTED",
            "YOUTUBE_RATE_LIMITED",
        }:
            raise StoreError("YOUTUBE_RESERVATION_INVALID")
        for _ in range(32):
            instant = clock()
            period, reset = window(instant)
            if code == "YOUTUBE_QUOTA_EXHAUSTED" and ticket.period != period:
                return wait_error(code, instant + timedelta(seconds=60), instant)
            resume = (
                reset
                if code == "YOUTUBE_QUOTA_EXHAUSTED"
                else (instant + timedelta(seconds=min(max(1, seconds), 30 * 86400)))
            )
            old = self.read()
            if not old:
                raise StoreError("YOUTUBE_BUDGET_MISSING")
            value = self.prepared(old, instant)
            search_only = ticket.endpoint == "search" and code == "YOUTUBE_QUOTA_EXHAUSTED"
            if search_only:
                value["search_blocked_until"] = max(
                    value.get("search_blocked_until") or resume.isoformat(), resume.isoformat()
                )
            elif value["blocked_until"] and datetime.fromisoformat(value["blocked_until"]) > resume:
                resume = datetime.fromisoformat(value["blocked_until"])
            else:
                value.update(blocked_until=resume.isoformat(), reason=code)
            try:
                self.commit(old, value)
            except Conflict:
                continue
            return wait_error(
                "YOUTUBE_SEARCH_BUDGET_EXHAUSTED" if search_only else value["reason"],
                resume,
                instant,
            )
        raise StoreError("YOUTUBE_BUDGET_BUSY", retryable=True, retry_after=1)

    def status(self):
        result = {
            "configured": self.configured(),
            "state": "unconfigured",
            "daily_limit": self.cfg.youtube_daily_budget,
            "reserved_units": 0,
            "available_units": None,
            "reset_at": None,
            "resume_at": None,
            "search_daily_limit": self.cfg.youtube_search_daily_budget,
            "search_reserved_calls": 0,
            "search_available_calls": None,
            "search_resume_at": None,
        }
        if not result["configured"]:
            return result
        instant = clock()
        _, reset = window(instant)
        value = self.prepared(self.read(), instant)
        resume = (
            value["blocked_until"]
            if value["blocked_until"] and (datetime.fromisoformat(value["blocked_until"]) > instant)
            else None
        )
        if value["reserved_units"] >= value["daily_limit"] and not resume:
            resume = reset.isoformat()
        return {
            **result,
            "state": "waiting" if resume else "available",
            "daily_limit": value["daily_limit"],
            "reserved_units": value["reserved_units"],
            "available_units": max(0, value["daily_limit"] - value["reserved_units"]),
            "reset_at": reset.isoformat(),
            "resume_at": resume,
            "search_daily_limit": value["search_daily_limit"],
            "search_reserved_calls": value["search_reserved_calls"],
            "search_available_calls": max(
                0, value["search_daily_limit"] - value["search_reserved_calls"]
            ),
            "search_resume_at": (
                value["search_blocked_until"]
                if value["search_blocked_until"]
                and datetime.fromisoformat(value["search_blocked_until"]) > instant
                else reset.isoformat()
                if value["search_reserved_calls"] >= value["search_daily_limit"]
                else None
            ),
        }
