"""Shared event-driven YouTube discovery with independent AI event scoring."""

from datetime import datetime, timedelta, timezone
from math import ceil, log, sqrt
from types import SimpleNamespace

from app.ai_matching import evaluate_with_ai
from app.calendar_rules import included
from app.document_accounts import Outbox, document, now, projection_job
from app.document_store import Conflict, StoreError, Write, clean, partition_items
from app.security import digest
from app.youtube_content_rules import comment_sample, discovered_video_batch, search_video_ids


DISCOVERY_PK = "provider:video-discovery"
QUERY_VERSION = "event-query-v1"
WINDOWS = {
    "before_24h": (-24, -48, 0),
    "before_3h": (-3, -24, 0),
    "after_3h": (3, 0, 6),
    "after_18h": (18, 0, 24),
}


def _instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not parsed.tzinfo:
        raise StoreError("EVENT_TIMEZONE_REQUIRED")
    return parsed.astimezone(timezone.utc)


def event_query(event, window):
    participants = [row.get("name", "").strip() for row in event.participants]
    participants += [row.get("short_name", "").strip() for row in event.participants]
    terms = list(dict.fromkeys(term for term in participants if term))
    if event.sport == "racing":
        terms.insert(0, event.title.split("·", 1)[0].strip())
        terms.append(event.source_key.rsplit(":", 1)[-1])
    terms.append(event.title)
    terms.append("preview analysis" if window.startswith("before") else "highlights analysis")
    return " ".join(terms)[:500]


def window_times(event, window):
    start = _instant(event.starts_at)
    due_offset, lower_offset, upper_offset = WINDOWS[window]
    end = start + timedelta(minutes=event.duration)
    anchor = start if window.startswith("before") else end
    return (
        anchor + timedelta(hours=due_offset),
        anchor + timedelta(hours=lower_offset),
        anchor + timedelta(hours=upper_offset),
    )


def _confidence(reasons):
    for reason in reasons:
        if reason.startswith("AI_CONFIDENCE_"):
            return int(reason.rsplit("_", 1)[-1]) / 100
    return 0.0


def _hot_channels(videos, instant):
    if len(videos) < 5:
        return set()
    scored = []
    for video in videos:
        age = max(0, (instant - _instant(video["published_at"])).total_seconds() / 3600)
        scored.append((log(video["view_count"] + 1) / sqrt(age + 2), video))
    threshold = sorted(score for score, _ in scored)[ceil(0.9 * len(scored)) - 1]
    return {
        video["channel_id"]
        for score, video in scored
        if video["view_count"] >= 10_000 and score >= threshold
    }


class Discovery:
    def __init__(self, runtime):
        self.rt, self.store = runtime, runtime.store
        self.outbox = Outbox(self.store)

    def reputation(self, channel_id):
        return self.store.get("state", DISCOVERY_PK, "reputation:" + channel_id)

    def set_official(self, channel_id, value, actor):
        ident = "reputation:" + channel_id
        old = self.store.get("state", DISCOVERY_PK, ident)
        payload = dict(old["payload"]) if old else {
            "channel_id": channel_id,
            "channel_name": "",
            "discovered_results": 0,
            "assessed_pairs": 0,
            "automatic": 0,
            "candidate": 0,
            "rejected": 0,
        }
        payload.update(
            official=value.official,
            official_evidence_url=value.evidence_url if value.official else "",
            official_reviewed_by=actor,
            official_reviewed_at=now(),
            official_valid_until=value.valid_until if value.official else None,
        )
        row = document(DISCOVERY_PK, ident, "channel_reputation", **payload)
        self.store.batch(
            "state",
            DISCOVERY_PK,
            [Write("replace" if old else "create", ident, row, old["_etag"] if old else None)],
        )
        return row["payload"]

    def schedule(self, *, instant=None):
        if not self.rt.youtube_budget.configured() or not self.rt.cfg.matching_ai_enabled:
            return 0
        instant = instant or datetime.now(timezone.utc)
        snapshot = self.rt.catalog.capture()
        events = {event.id: event for event in snapshot.events()}
        wanted = set()
        for route in partition_items(self.store, "indexes", "owners", "owner_route"):
            account = self.store.get("state", route["payload"]["owner_pk"], "account")
            if not account or account["payload"]["deleted"]:
                continue
            active = self.rt.accounts.active(account["payload"]["user_id"])["payload"]
            windows = active["config"]["preferences"].get(
                "content_search_windows", ["before_24h", "after_3h"]
            )
            for event in events.values():
                if (
                    event.demo
                    or not event.starts_at
                    or event.time_precision != "exact"
                    or event.status in {"cancelled", "postponed"}
                    or not included(event, active["config"])
                ):
                    continue
                for window in windows:
                    due, _, _ = window_times(event, window)
                    # Catch up after a transient timer/deployment outage without turning
                    # a newly followed old event into an unbounded historical crawl.
                    if due <= instant < due + timedelta(hours=6):
                        wanted.add((event.id, event.updated_at, window))
        count = 0
        for event_id, revision, window in sorted(wanted):
            ident = "run:" + digest(QUERY_VERSION + ":" + event_id + ":" + revision + ":" + window)[:32]
            if self.store.get("state", DISCOVERY_PK, ident):
                continue
            run = document(
                DISCOVERY_PK,
                ident,
                "event_search_run",
                event_id=event_id,
                event_updated_at=revision,
                window=window,
                query_version=QUERY_VERSION,
                state="pending",
                result_count=0,
                assessment_ids=[],
                error="",
                created_at=now(),
                finished_at=None,
            )
            job = projection_job(DISCOVERY_PK, 0)
            job["payload"].update(operation="event_search", run_id=ident)
            try:
                self.store.batch(
                    "state",
                    DISCOVERY_PK,
                    [Write("create", ident, run), Write("create", job["id"], job)],
                )
                count += 1
            except Conflict:
                pass
        snapshot.assert_current()
        return count

    def process(self, claim):
        run = self.store.get("state", DISCOVERY_PK, claim["payload"]["run_id"])
        if not run or run["kind"] != "event_search_run":
            raise StoreError("DISCOVERY_RUN_NOT_FOUND")
        snapshot = self.rt.catalog.capture()
        event = snapshot.event(run["payload"]["event_id"])
        if (
            not event
            or event.updated_at != run["payload"]["event_updated_at"]
            or event.status in {"cancelled", "postponed"}
            or not event.starts_at
        ):
            self._finish_stale(claim, run)
            return
        _, lower, upper = window_times(event, run["payload"]["window"])
        params = {
            "part": "snippet",
            "type": "video",
            "order": "date",
            "safeSearch": "moderate",
            "maxResults": 25,
            "q": event_query(event, run["payload"]["window"]),
            "publishedAfter": lower.isoformat().replace("+00:00", "Z"),
            "publishedBefore": min(upper, datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z"),
        }
        ids = search_video_ids(self.rt.youtube_request("search", params))
        videos = discovered_video_batch(
            ids,
            self.rt.youtube_request(
                "videos", {"id": ",".join(ids), "part": "snippet,status,statistics"}
            ) if ids else {"items": []},
            now(),
        )
        for video in videos:
            if not video["available"]:
                continue
            try:
                video["comments"] = comment_sample(
                    self.rt.youtube_request(
                        "commentThreads",
                        {
                            "videoId": video["id"],
                            "part": "snippet",
                            "maxResults": 12,
                            "order": "relevance",
                            "textFormat": "plainText",
                        },
                    )
                )
            except Exception as error:
                # Disabled comments are valid; quota/network failures must retry the run.
                if getattr(error, "detail", {}).get("code") == "commentsDisabled":
                    video["comments"] = []
                else:
                    raise
        hot_channels = _hot_channels(videos, datetime.now(timezone.utc))
        writes, assessment_ids = [], []
        reputations = {}
        for video in videos:
            output = evaluate_with_ai(
                SimpleNamespace(**video), [event], self.rt.cfg, creator_name=video["channel_name"]
            )
            result = output[0] if output else {
                "event_id": event.id,
                "kind": "video",
                "content_labels": [],
                "decision": "reject",
                "reason_codes": ["DETERMINISTIC_CONFLICT"],
                "rule_version": QUERY_VERSION,
            }
            reputation = self.reputation(video["channel_id"])
            official = bool(
                reputation
                and reputation["payload"].get("official")
                and (
                    not reputation["payload"].get("official_valid_until")
                    or _instant(reputation["payload"]["official_valid_until"])
                    >= datetime.now(timezone.utc)
                )
            )
            system_labels = (["✅官方频道"] if official else []) + (
                ["🔥热门"] if video["channel_id"] in hot_channels else []
            )
            ai_labels = list(result.get("content_labels", []))
            display_labels = (system_labels + ai_labels)[:3]
            assessment_id = "assessment:" + digest(event.id + ":" + video["id"])[:32]
            old = self.store.get("state", DISCOVERY_PK, assessment_id)
            assessment = document(
                DISCOVERY_PK,
                assessment_id,
                "event_video_assessment",
                event_id=event.id,
                event_updated_at=event.updated_at,
                video=video,
                decision=result["decision"],
                confidence=_confidence(result["reason_codes"]),
                reason_codes=result["reason_codes"],
                rule_version=result["rule_version"],
                system_labels=system_labels,
                ai_content_labels=ai_labels,
                display_labels=display_labels,
                search_window=run["payload"]["window"],
                updated_at=now(),
            )
            writes.append(
                Write("replace" if old else "create", assessment_id, assessment, old["_etag"] if old else None)
            )
            assessment_ids.append(assessment_id)
            rep_id = "reputation:" + video["channel_id"]
            if rep_id not in reputations:
                source = reputation["payload"] if reputation else {}
                reputations[rep_id] = (
                    reputation,
                    {
                        "channel_id": video["channel_id"],
                        "channel_name": video["channel_name"],
                        "official": source.get("official", False),
                        "official_evidence_url": source.get("official_evidence_url", ""),
                        "official_reviewed_by": source.get("official_reviewed_by", ""),
                        "official_reviewed_at": source.get("official_reviewed_at"),
                        "official_valid_until": source.get("official_valid_until"),
                        "discovered_results": source.get("discovered_results", 0),
                        "assessed_pairs": source.get("assessed_pairs", 0),
                        "automatic": source.get("automatic", 0),
                        "candidate": source.get("candidate", 0),
                        "rejected": source.get("rejected", 0),
                    },
                )
            prior, payload = reputations[rep_id]
            payload["discovered_results"] += 1
            if not old:
                payload["assessed_pairs"] += 1
                payload[{"automatic": "automatic", "needs_review": "candidate"}.get(result["decision"], "rejected")] += 1
        for ident, (old, payload) in reputations.items():
            row = document(DISCOVERY_PK, ident, "channel_reputation", **payload)
            writes.append(Write("replace" if old else "create", ident, row, old["_etag"] if old else None))
        updated = clean(run)
        updated["payload"].update(
            state="assessed",
            result_count=len(videos),
            assessment_ids=assessment_ids,
            error="",
            finished_at=now(),
        )
        fanout = projection_job(DISCOVERY_PK, 0)
        fanout["payload"].update(
            operation="discovery_fanout", run_id=run["id"], after=""
        )
        snapshot.assert_current()
        self.store.batch(
            "state",
            DISCOVERY_PK,
            [
                Write("replace", run["id"], updated, run["_etag"]),
                self.outbox.completion(claim),
                Write("create", fanout["id"], fanout),
                *writes,
            ],
        )

    def _finish_stale(self, claim, run):
        updated = clean(run)
        updated["payload"].update(state="stale", error="EVENT_CHANGED", finished_at=now())
        self.store.batch(
            "state",
            DISCOVERY_PK,
            [Write("replace", run["id"], updated, run["_etag"]), self.outbox.completion(claim)],
        )

    def fanout(self, claim):
        job = self.outbox.current(claim)
        run = self.store.get("state", DISCOVERY_PK, job["payload"]["run_id"])
        if not run or run["payload"]["state"] != "assessed":
            raise StoreError("DISCOVERY_RUN_NOT_READY", retryable=True)
        snapshot = self.rt.catalog.capture()
        event = snapshot.event(run["payload"]["event_id"])
        assessments = [
            self.store.get("state", DISCOVERY_PK, ident)
            for ident in run["payload"]["assessment_ids"]
        ]
        routes = self.store.page(
            "indexes", "owners", "owner_route", after=job["payload"].get("after", ""), limit=50
        )
        for route in routes:
            account = self.store.get("state", route["payload"]["owner_pk"], "account")
            if not account or account["payload"]["deleted"]:
                continue
            active = self.rt.accounts.active(account["payload"]["user_id"])
            config = active["payload"]["config"]
            if (
                not event
                or event.updated_at != run["payload"]["event_updated_at"]
                or run["payload"]["window"] not in config["preferences"].get(
                    "content_search_windows", ["before_24h", "after_3h"]
                )
                or not included(event, config)
            ):
                continue
            self._publish_owner(active, event, [row for row in assessments if row])
        current = self.outbox.current(claim)
        if len(routes) == 50:
            updated = clean(current)
            updated.update(state="pending", due_at=now())
            updated["payload"].update(after=routes[-1]["id"], lease=None, attempts=0, error="")
            completion = Write("replace", current["id"], updated, current["_etag"])
        else:
            completion = self.outbox.completion(current)
        snapshot.assert_current()
        self.store.batch("state", DISCOVERY_PK, [completion])

    def _publish_owner(self, account, event, assessments):
        pk, user = account["pk"], account["payload"]
        overrides = {
            row["url"]: row["state"]
            for row in user["config"]["link_overrides"]
            if row["event_key"] == event.source_key
        }
        writes = []
        for raw in assessments:
            value = raw["payload"]
            if value["decision"] == "reject":
                continue
            video = value["video"]
            url = "https://www.youtube.com/watch?v=" + video["id"]
            state = overrides.get(url)
            match_id = digest(user["user_id"] + ":" + video["id"] + ":" + event.id)[:32]
            row_id = "match:" + match_id
            old_match = self.store.get("state", pk, row_id)
            if old_match and old_match["payload"].get("decision") in {"confirmed", "ignored"}:
                continue
            match_value = {
                "id": match_id,
                "owner_id": user["user_id"],
                "source": "discovery",
                "video_id": video["id"],
                "channel_id": video["channel_id"],
                "creator": video["channel_name"],
                "video_title": video["title"],
                "published_at": video["published_at"],
                "event_id": event.id,
                "event_updated_at": event.updated_at,
                "source_updated_at": video["updated_at"],
                "kind": "video",
                "content_labels": value["display_labels"],
                "system_labels": value["system_labels"],
                "ai_content_labels": value["ai_content_labels"],
                "decision": "ignored" if state == "block" else value["decision"],
                "confidence": value["confidence"],
                "reason_codes": value["reason_codes"],
                "rule_version": value["rule_version"],
                "updated_at": value["updated_at"],
            }
            match = document(pk, row_id, "video_match")
            match["payload"] = match_value
            writes.append(
                Write(
                    "replace" if old_match else "create",
                    row_id,
                    match,
                    old_match["_etag"] if old_match else None,
                )
            )
            if value["decision"] != "automatic" or state == "block":
                continue
            link_id = digest(user["user_id"] + ":" + event.id + ":" + url)[:32]
            link_row_id = "link:" + link_id
            old_link = self.store.get("state", pk, link_row_id)
            if old_link and (state == "pin" or old_link["payload"].get("origin") != "discovery"):
                continue
            link = document(pk, link_row_id, "link")
            link["payload"] = dict(
                id=link_id,
                owner_id=user["user_id"],
                event_id=event.id,
                url=url,
                url_hash=digest(url),
                title=video["title"],
                kind="video",
                content_labels=value["display_labels"],
                system_labels=value["system_labels"],
                ai_content_labels=value["ai_content_labels"],
                platform="YouTube",
                channel_id=video["channel_id"],
                creator=video["channel_name"],
                origin="discovery",
                access="unknown",
                regions=[],
                available=video["available"],
                created_at=(old_link["payload"]["created_at"] if old_link else now()),
            )
            writes.append(
                Write(
                    "replace" if old_link else "create",
                    link_row_id,
                    link,
                    old_link["_etag"] if old_link else None,
                )
            )
        if not writes:
            return
        projection = projection_job(pk, user["revision"])
        projection["id"] = "job:" + digest("discovery:" + account["pk"] + ":" + event.id + ":" + str(max(row["payload"]["updated_at"] for row in assessments)))[:32]
        if not self.store.get("state", pk, projection["id"]):
            writes.append(Write("create", projection["id"], projection))
        self.store.batch("state", pk, writes)
