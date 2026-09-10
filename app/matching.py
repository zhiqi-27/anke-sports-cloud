"""Deterministic metadata rules. No audio, transcripts, model calls or probability claims."""

import re
import unicodedata
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

RULE_VERSION = "matching-v2"
ALIASES = {
    "LAL": ["lakers", "湖人", "洛杉矶湖人"],
    "GSW": ["warriors", "勇士", "金州勇士"],
    "BOS": ["celtics", "凯尔特人"],
    "NYK": ["knicks", "尼克斯"],
    "ARS": ["arsenal", "阿森纳"],
    "MCI": ["manchester city", "曼城"],
    "LIV": ["liverpool", "利物浦"],
    "CHE": ["chelsea", "切尔西"],
}
RACE_ALIASES = {
    "italian grand prix": ["monza", "意大利站", "意大利大奖赛"],
    "spanish grand prix": ["西班牙站", "西班牙大奖赛"],
    "azerbaijan grand prix": ["阿塞拜疆站", "阿塞拜疆大奖赛", "baku"],
}
SESSIONS = {
    "FirstPractice": ["fp1", "practice 1", "自由练习 1", "一练"],
    "SecondPractice": ["fp2", "practice 2", "自由练习 2", "二练"],
    "ThirdPractice": ["fp3", "practice 3", "自由练习 3", "三练"],
    "SprintQualifying": ["sprint qualifying", "sprint shootout", "冲刺排位赛"],
    "Qualifying": ["qualifying", "排位赛"],
    "Sprint": ["sprint", "冲刺赛"],
    "race": ["race", "正赛", "main race"],
}
PREVIEW = ["preview", "前瞻", "赛前分析", "赛前预测"]
RECAP = ["recap", "postgame", "post game", "赛后", "复盘", "赛后分析"]
EXCLUDE = ["trade", "draft", "交易", "选秀", "十年前", "赛季盘点", "season review", "all time", "回顾经典"]


def normalized(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def contains(text, term):
    term = normalized(term)
    if not term:
        return False
    if re.fullmatch(r"[a-z0-9 ]+", term):
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text))
    return term in text


def parse_time(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED")
    return result


def explicit_date(text, event):
    """Dates in the video title, not scores or a description's link archive.

    Numeric short slash dates can use either D/M or M/D. Full ISO, Chinese
    month/day and named English months avoid silently choosing a locale.
    """
    day = parse_time(event.starts_at).astimezone(ZoneInfo(event.timezone)).date() if event.starts_at else None
    if not day:
        return False, False, False
    found = []
    invalid = False

    def record(year, month, number):
        nonlocal invalid
        try:
            found.append({date(int(year or day.year), int(month), int(number))})
        except ValueError:
            invalid = True
        return " "

    # Consume full dates first; don't parse the suffix again as a short date.
    text = re.sub(
        r"(?<!\d)(20\d{2})([-/.])(\d{1,2})\2(\d{1,2})(?!\d)",
        lambda m: record(m[1], m[3], m[4]), text,
    )
    text = re.sub(
        r"(?<!\d)(?:(20\d{2})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})(?:\s*日)?(?!\d)",
        lambda m: record(m[1], m[2], m[3]), text,
    )
    months = {name: i for i, name in enumerate(
        ["january", "february", "march", "april", "may", "june", "july", "august",
         "september", "october", "november", "december"], 1
    )}
    months.update({name[:3]: number for name, number in list(months.items())})
    months["sept"] = 9
    names = "|".join(sorted(months, key=len, reverse=True))
    text = re.sub(
        rf"\b({names})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(20\d{{2}}))?\b",
        lambda m: record(m[3], months[m[1]], m[2]), text,
    )
    text = re.sub(
        rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({names})\.?(?:,?\s+(20\d{{2}}))?\b",
        lambda m: record(m[3], months[m[2]], m[1]), text,
    )
    for m, d in re.findall(r"(?<![\d/])(\d{1,2})\s*/\s*(\d{1,2})(?![\d/])", text):
        options = set()
        for month, number in [(m, d), (d, m)]:
            try:
                options.add(date(day.year, int(month), int(number)))
            except ValueError:
                pass
        found.append(options)
        invalid |= not options
    years = [int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", text)]
    conflict = invalid or any(y != day.year for y in years) or (bool(found) and all(day not in x for x in found))
    ambiguous = bool(found) and any(x != {day} for x in found) and not conflict
    return bool(found) and not conflict and not ambiguous, conflict, ambiguous


def phase(text):
    before, after = any(contains(text, x) for x in PREVIEW), any(contains(text, x) for x in RECAP)
    return "preview" if before and not after else "recap" if after and not before else "unknown"


def team_hits(text, event):
    return sum(
        any(contains(text, a) for a in [p["name"], p["short_name"], *ALIASES.get(p["short_name"], [])])
        for p in event.participants
    )


def detected_sessions(text):
    detected = {k for k, terms in SESSIONS.items() if any(contains(text, t) for t in terms)}
    if "SprintQualifying" in detected:
        detected -= {"Qualifying", "Sprint"}
    return detected


def evaluate(video, events):
    title = normalized(video.title)
    text = normalized(video.title + "\n" + video.description)
    title_kind = phase(title)
    # Descriptions may suggest review candidates, but cannot provide the title's
    # missing subject, phase or date for automatic writes. Boilerplate commonly
    # lists unrelated games, old years and both preview/recap playlists.
    kind = title_kind if title_kind != "unknown" else phase(text)
    excluded = any(contains(text, x) for x in EXCLUDE)
    published = parse_time(video.published_at)
    candidates = []
    for event in events:
        if not event.starts_at or event.status == "cancelled":
            continue
        start = parse_time(event.starts_at)
        delta = start - published
        if not -timedelta(days=3) <= delta <= timedelta(days=7):
            continue
        if kind == "preview" and delta < timedelta(0):
            continue
        if kind == "recap" and delta > timedelta(0):
            continue
        reasons = []
        strong = False
        if event.sport == "racing":
            race = normalized(event.title.split("·")[0])
            if not any(contains(text, a) for a in [race, *RACE_ALIASES.get(race, [])]):
                continue
            reasons.append("RACE_FOUND")
            session = event.source_key.rsplit(":", 1)[-1]
            title_subject = any(contains(title, a) for a in [race, *RACE_ALIASES.get(race, [])])
            strong = title_subject and detected_sessions(title) == {session}
            reasons.append("SESSION_FOUND" if strong else "SESSION_AMBIGUOUS")
        else:
            hits = team_hits(text, event)
            if not hits:
                continue
            title_subject = team_hits(title, event) == 2 and len(event.participants) == 2
            strong = title_subject
            reasons.append("BOTH_TEAMS_FOUND" if hits == 2 else "ONE_TEAM_ONLY")
        if not title_subject:
            reasons.append("TITLE_SUBJECT_UNCLEAR")
        has_date, conflict, ambiguous = explicit_date(title, event)
        reasons.append(
            "EXPLICIT_DATE" if has_date else "DATE_CONFLICT" if conflict
            else "DATE_AMBIGUOUS" if ambiguous else "NO_EXPLICIT_DATE"
        )
        if title_kind == "unknown" and kind != "unknown":
            reasons.append("TITLE_PHASE_UNCLEAR")
        if kind == "unknown":
            reasons.append("PHASE_UNKNOWN")
        if excluded:
            reasons.append("EXCLUDED_TOPIC")
        ended = event.status == "finished" or published >= start + timedelta(minutes=event.duration)
        if kind == "recap" and not ended:
            reasons.append("MATCH_NOT_FINISHED")
        candidates.append(
            {
                "event_id": event.id,
                "kind": kind,
                "reason_codes": reasons,
                "rule_version": RULE_VERSION,
                "strong": strong,
                "date": has_date,
                "conflict": conflict,
                "ambiguous": ambiguous,
                "ended": ended,
            }
        )
    strong_candidates = [x for x in candidates if x["strong"] and not x["conflict"]]
    for item in candidates:
        unique = len(strong_candidates) == 1
        if len(strong_candidates) > 1 or (not strong_candidates and len(candidates) > 1):
            item["reason_codes"].append("MULTIPLE_CANDIDATES")
        unambiguous = unique and (item["date"] or kind == "recap" or "SESSION_FOUND" in item["reason_codes"])
        automatic = (
            item["strong"]
            and unambiguous
            and not item["conflict"]
            and not item["ambiguous"]
            and not excluded
            and title_kind != "unknown"
            and (kind != "recap" or item["ended"])
        )
        item["decision"] = (
            "reject" if excluded or item["conflict"] else "automatic" if automatic else "needs_review"
        )
        for field in ["strong", "date", "conflict", "ambiguous", "ended"]:
            item.pop(field)
    return candidates
