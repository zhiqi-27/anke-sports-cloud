from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import Event, ProviderState, Source


def seed_demo(db):
    if db.scalar(select(Source.id).where(Source.demo.is_(True)).limit(1)):
        return
    teams = [
        ("lakers", "洛杉矶湖人", "LAL", "basketball", "#e3b941"),
        ("warriors", "金州勇士", "GSW", "basketball", "#4d91f2"),
        ("celtics", "波士顿凯尔特人", "BOS", "basketball", "#4bb98f"),
        ("knicks", "纽约尼克斯", "NYK", "basketball", "#ed8951"),
        ("arsenal", "阿森纳", "ARS", "football", "#e77274"),
        ("city", "曼彻斯特城", "MCI", "football", "#8cbddd"),
        ("liverpool", "利物浦", "LIV", "football", "#ef6664"),
        ("chelsea", "切尔西", "CHE", "football", "#5d89ee"),
        ("mclaren", "迈凯伦", "MCL", "racing", "#f6a553"),
        ("ferrari", "法拉利", "FER", "racing", "#e76263"),
    ]
    lookup = {}
    for key, name, short, sport, color in teams:
        source = Source(
            id=f"demo:{key}",
            name=name,
            short_name=short,
            sport=sport,
            kind="team",
            color=color,
            provider="DEMO",
            demo=True,
        )
        db.add(source)
        lookup[key] = {"id": source.id, "name": name, "short_name": short, "color": color}
    for key, name, short, sport, color in [
        ("nba", "NBA", "NBA", "basketball", "#f3b56a"),
        ("epl", "英格兰超级联赛", "英超", "football", "#ac93e9"),
        ("f1", "F1 世界锦标赛", "F1", "racing", "#ec7972"),
    ]:
        db.add(
            Source(
                id=f"demo:{key}",
                name=name,
                short_name=short,
                sport=sport,
                kind="competition",
                color=color,
                provider="DEMO",
                demo=True,
            )
        )
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    monday = today - timedelta(days=today.weekday())
    samples = [
        (0, 2, "nba", "lakers", "warriors", "大通中心"),
        (1, 1, "epl", "arsenal", "city", "酋长球场"),
        (2, 2, "nba", "celtics", "knicks", "麦迪逊广场花园"),
        (2, 11, "epl", "liverpool", "chelsea", "安菲尔德球场"),
        (3, 2, "nba", "warriors", "celtics", "TD 花园"),
        (4, 10, "f1", "mclaren", "ferrari", "蒙扎赛道"),
        (4, 19, "epl", "city", "chelsea", "伊蒂哈德球场"),
        (5, 1, "nba", "knicks", "lakers", "Crypto.com 球馆"),
        (5, 13, "f1", "mclaren", "ferrari", "蒙扎赛道"),
        (5, 16, "epl", "arsenal", "liverpool", "酋长球场"),
        (6, 13, "f1", "mclaren", "ferrari", "蒙扎赛道"),
        (6, 3, "nba", "lakers", "celtics", "TD 花园"),
    ]
    for week in [-1, 0, 1, 2]:
        for index, (day, hour, league, a, b, venue) in enumerate(samples):
            start = monday + timedelta(days=day + week * 7, hours=hour)
            sport = "racing" if league == "f1" else "football" if league == "epl" else "basketball"
            title = (
                f"{lookup[a]['name']} vs {lookup[b]['name']}"
                if league != "f1"
                else f"意大利大奖赛 · { {4: '自由练习', 5: '排位赛', 6: '正赛'}[day] }"
            )
            db.add(
                Event(
                    source_key=f"demo:week{week}:event{index}",
                    competition_id=f"demo:{league}",
                    sport=sport,
                    title=title,
                    starts_at=start.isoformat(),
                    local_date=start.date().isoformat(),
                    venue=venue,
                    participants=[lookup[a], lookup[b]] if league != "f1" else [],
                    provider="DEMO",
                    duration=150 if league == "nba" else 120 if league != "f1" else 90,
                    demo=True,
                )
            )
    for provider in ["balldontlie", "football-data", "jolpica", "youtube"]:
        if not db.get(ProviderState, provider):
            db.add(ProviderState(id=provider))
