from types import SimpleNamespace

from app.link_rules import selected_links
from app.platforms import selected_product


def event(competition_id="balldontlie:nba"):
    return SimpleNamespace(
        id="event-1",
        source_key="provider:event-1",
        competition_id=competition_id,
        status="scheduled",
    )


def config(*, region=None, preferred=None, competition_id="balldontlie:nba"):
    preferences = {"watch_region": region, "broadcast_platforms": {}}
    if preferred:
        key = f"{region}:{competition_id}" if region else competition_id
        preferences["broadcast_platforms"][key] = preferred
    return {"preferences": preferences, "link_overrides": []}


def test_default_product_uses_first_rights_product_and_preference_selects_provider():
    default = selected_product(event(), config())
    assert default["url"] == "https://www.nba.com/watch/featured"
    assert default["metadata"]["content_label"] == "官方直播产品"

    preferred = selected_product(event(), config(region="US", preferred="peacock"))
    assert preferred["url"] == "https://www.peacocktv.com/sports/nba"
    assert preferred["metadata"]["platform_id"] == "peacock"


def test_region_limits_default_product_to_the_regional_rights_matrix():
    selected = selected_product(event(), config(region="CN"))
    assert selected["url"] == "https://sports.qq.com/kbsweb/index.htm#nba"
    assert selected["regions"] == ["CN"]


def test_cn_products_use_the_requested_competition_entry_urls():
    nba_migu = selected_product(event(), config(region="CN", preferred="migu"))
    assert nba_migu["url"] == "https://www.miguvideo.com/p/home/3cd6ba04967742879aaa40bee02a99a6"

    f1_tencent = selected_product(
        event("jolpica:f1"),
        config(region="CN", preferred="tencent-sports", competition_id="jolpica:f1"),
    )
    assert f1_tencent["url"] == "https://sports.qq.com/kbsweb/#100360"


def test_premier_league_migu_entry_remains_unchanged():
    selected = selected_product(
        event("football-data:PL"),
        config(region="CN", preferred="migu", competition_id="football-data:PL"),
    )
    assert selected["url"] == (
        "https://www.miguvideo.com/mgs/website/prd/sportsHomePage.html?"
        "pageId=0c40bbc85fa345bbba20f8e5fd11a922"
    )


def test_manual_link_hides_public_product_for_that_user():
    public = SimpleNamespace(
        id="official",
        owner_id="public",
        available=True,
        kind="live",
        origin="official",
        url="https://www.nba.com/watch/featured",
        title="NBA · Watch",
        platform="nba",
        access="unknown",
        regions=[],
        created_at="2026-09-18T00:00:00+00:00",
    )
    manual = SimpleNamespace(
        id="manual",
        owner_id="user-1",
        available=True,
        kind="watch_along",
        origin="manual",
        url="https://www.nba.com/game/user-link",
        title="我的入口",
        platform="nba",
        access="unknown",
        regions=[],
        created_at="2026-09-18T00:00:00+00:00",
    )
    selected = selected_links(
        event(),
        config(),
        [public, manual],
        lambda link: {
            "platform_id": "nba",
            "platform_name": "NBA",
        },
        "user-1",
    )
    assert [link["id"] for link in selected] == ["manual"]
    assert selected[0]["kind"] == "live"

    other_user = selected_links(
        event(),
        config(),
        [public, manual],
        lambda link: {"platform_id": "nba", "platform_name": "NBA"},
        "user-2",
    )
    assert [link["id"] for link in other_user] == ["official"]
