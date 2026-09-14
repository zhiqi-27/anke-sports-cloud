"""Date, selection and pagination rules shared by persistence adapters."""

import base64
import json
from datetime import date, datetime, timedelta, timezone

from app.calendar_rules import inclusion_filter
from app.security import digest, problem


def schedule_range(from_, to, dataset, q, limit):
    if dataset not in {"real", "demo"} or len(q) > 200 or not 1 <= limit <= 500:
        problem("INVALID_QUERY", "查询条件无效")
    try:
        lower, upper = (datetime.fromisoformat(value.replace("Z", "+00:00")) for value in (from_, to))
        if not lower.tzinfo or not upper.tzinfo or not timedelta(0) < upper - lower <= timedelta(days=180):
            raise ValueError()
        lower_utc, upper_utc = lower.astimezone(timezone.utc), upper.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        problem("INVALID_RANGE", "请查询带时区、最长 180 天的有效时间范围")
    # ISO offset strings are not ordered by absolute time. Use a conservative
    # indexed date envelope, then compare actual instants below. UTC offsets
    # are less than 24 hours; this includes both extreme offsets at the edges.
    earliest = date.fromordinal(max(date.min.toordinal(), lower_utc.date().toordinal() - 1)).isoformat()
    last_day = upper_utc.date().toordinal() + 2
    latest = date.fromordinal(last_day).isoformat() if last_day <= date.max.toordinal() else None
    return lower, upper, earliest, latest


def schedule_page(
    events,
    from_,
    to,
    dataset="real",
    followed=False,
    q="",
    user=None,
    limit=500,
    cursor=None,
    source_id="",
):
    lower, upper, _, _ = schedule_range(from_, to, dataset, q, limit)
    if len(source_id) > 200:
        problem("INVALID_QUERY", "查询条件无效")
    if followed and not user:
        problem("AUTH_REQUIRED", "登录后查看个人赛程", 401)
    result = []
    accepts = inclusion_filter(user.config) if followed else None
    needle = q.casefold()
    for event in events:
        if getattr(event, "demo", dataset == "demo") != (dataset == "demo"):
            continue
        if accepts is not None and not accepts(event):
            continue
        if (
            source_id
            and event.competition_id != source_id
            and not any(participant["id"] == source_id for participant in event.participants)
        ):
            continue
        if needle and needle not in event.title.casefold():
            continue
        start = datetime.fromisoformat(event.starts_at.replace("Z", "+00:00")) if event.starts_at else None
        if start is not None and not lower <= start < upper:
            continue
        if start is None and (
            not event.local_date
            or not lower.date().isoformat() <= event.local_date < upper.date().isoformat()
        ):
            continue
        order = start.astimezone(timezone.utc).isoformat() if start else event.local_date
        result.append((order, event))
    result.sort(key=lambda item: (item[0], item[1].id))
    binding = digest(
        json.dumps(
            [
                from_,
                to,
                dataset,
                followed,
                q,
                source_id,
                user.id if user else None,
                user.revision if user else None,
                [(e.id, e.updated_at) for _, e in result],
            ]
        )
    )
    offset = 0
    if cursor:
        try:
            if len(cursor) > 200:
                raise ValueError()
            value = json.loads(base64.urlsafe_b64decode(cursor))
            offset = value["offset"]
            if value["binding"] != binding or type(offset) is not int or not 0 <= offset <= len(result):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            problem("CURSOR_EXPIRED", "赛程或查询已变化，请重新查询第一页", 409)
    next_cursor = (
        base64.urlsafe_b64encode(json.dumps({"offset": offset + limit, "binding": binding}).encode()).decode()
        if offset + limit < len(result)
        else None
    )
    page = [event for _, event in result[offset : offset + limit]]
    return page, next_cursor
