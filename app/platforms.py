"""Candidate URL grammar and a bounded, credential-free HEAD probe.

A matching URL or HTTP response never establishes an official broadcast or playback.
"""

import http.client
import ipaddress
import re
import socket
import ssl
from urllib.parse import parse_qs, urljoin, urlsplit

from app.security import canonical_url

# Explicit domains retained from the existing manual-link contract. These rules
# describe candidate pages, not a verified platform/region/device support matrix.
RULES = [
    {
        "id": "apple-tv",
        "name": "Apple TV",
        "hosts": ["tv.apple.com"],
        "paths": ["^/.+"],
        "evidence": "https://tv.apple.com/us/info/watch-f1",
        "app_paths": [r"^/.+"],
    },
    {
        "id": "peacock",
        "name": "Peacock",
        "hosts": ["www.peacocktv.com"],
        "paths": ["^/.+"],
        "evidence": "https://www.peacocktv.com/sports/premier-league",
        "app_paths": [r"^/(deeplink|watch)/"],
    },
    {
        "id": "tencent-sports",
        "name": "腾讯体育",
        "hosts": ["sports.qq.com", "v.qq.com"],
        "paths": ["^/.+"],
        "evidence": "https://corp.formula1.com/formula-1-renews-partnership-with-tencent-to-broadcast-f1-in-mainland-china/",
    },
    {
        "id": "migu",
        "name": "咪咕视频",
        "hosts": ["www.miguvideo.com", "m.miguvideo.com"],
        "paths": ["^/.+"],
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
        "app_paths": [r"^/(wap/resource/migu|mgs/common/migugotoapp)/"],
    },
    {
        "id": "premier-league",
        "name": "Premier League",
        "hosts": ["www.premierleague.com"],
        "paths": ["^/.+"],
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
    },
    {
        "id": "nba",
        "name": "NBA",
        "hosts": ["nba.com", "www.nba.com", "watch.nba.com", "tv.nba.com", "support.watch.nba.com"],
        "paths": [r"^/.+"],
        "evidence": "https://www.nba.com/watch/featured",
    },
    {
        "id": "prime-video",
        "name": "Prime Video",
        "hosts": ["www.primevideo.com"],
        "paths": [r"^/(detail|watch|dp|gp/video/detail|region/[^/]+/(detail|watch|dp))/.+"],
        "evidence": "https://www.nba.com/news/nba-media-agreements-2024",
        "app_paths": [r"^/(detail|watch|dp|gp/video/detail|region/[^/]+/(detail|watch|dp))/"],
    },
    {
        "id": "nbc-sports",
        "name": "NBC Sports",
        "hosts": ["www.nbcsports.com"],
        "paths": [r"^/(watch|soccer)/.+"],
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
    },
    {
        "id": "f1",
        "name": "Formula 1",
        "hosts": ["f1tv.formula1.com", "www.formula1.com", "corp.formula1.com"],
        "paths": [r"^/.+"],
        "evidence": "https://f1tv.formula1.com/",
    },
    {
        "id": "uefa",
        "name": "UEFA",
        "hosts": ["www.uefa.com"],
        "paths": [r"^/.+"],
        "evidence": "https://www.uefa.com/",
    },
    {
        "id": "fifa",
        "name": "FIFA",
        "hosts": ["www.fifa.com"],
        "paths": [r"^/.+"],
        "evidence": "https://www.fifa.com/",
    },
    {
        "id": "espn",
        "name": "ESPN",
        "hosts": ["www.espn.com"],
        "paths": [r"^/.+"],
        "evidence": "https://www.espn.com/watch/",
        "app_paths": [r"^/[^/]+/(game|watch)/.+", r"^/watch/.+"],
    },
    {
        "id": "fod",
        "name": "FOD",
        "hosts": ["fod.fujitv.co.jp", "news.fod.fujitv.co.jp", "www.fujitv.co.jp", "otn.fujitv.co.jp"],
        "paths": [r"^/.+"],
        "evidence": "https://corp.formula1.com/fuji-tv-to-exclusively-broadcast-formula-1-in-japan-in-new-long-term-deal/",
    },
    {
        "id": "u-next",
        "name": "U-NEXT",
        "hosts": ["video.unext.jp"],
        "paths": [r"^/(livedetail|title|series)/.+"],
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
        "app_paths": [r"^/(livedetail|title|series)/"],
    },
    {
        "id": "dazn",
        "name": "DAZN",
        "hosts": ["www.dazn.com"],
        "paths": [r"^/[^/]+/(competition|sport|show|schedule)/.+", r"^/[^/]+/schedule/?$"],
        "evidence": "https://www.formula1.com/en/information/f1-broadcast-information.45y3LNsT1D6VoK0ZmX8ciJ",
        "app_paths": [r"^/[^/]+/(competition|sport|show|schedule)/", r"^/[^/]+/schedule/?$"],
    },
    {
        "id": "viaplay",
        "name": "Viaplay",
        "hosts": ["viaplay.com", "www.viaplay.com"],
        "paths": [r"^/sport/.+"],
        "evidence": "https://www.formula1.com/en/information/f1-broadcast-information.45y3LNsT1D6VoK0ZmX8ciJ",
        "app_paths": [r"^/sport/"],
    },
    {
        "id": "canal-plus",
        "name": "CANAL+",
        "hosts": ["www.canalplus.com"],
        "paths": [r"^/[^/]+/(sport|sports)/.+"],
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
    },
    {
        "id": "sky-sports",
        "name": "Sky Sports",
        "hosts": ["www.skysports.com"],
        "paths": [r"^/.+"],
        "evidence": "https://www.formula1.com/en/information/f1-broadcast-information.45y3LNsT1D6VoK0ZmX8ciJ",
    },
    {
        "id": "sky-de",
        "name": "Sky Deutschland",
        "hosts": ["sport.sky.de", "www.sky.de"],
        "paths": [r"^/.+"],
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
    },
    {
        "id": "sky-it",
        "name": "Sky Italia",
        "hosts": ["sport.sky.it", "www.sky.it"],
        "paths": [r"^/.+"],
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
    },
    {
        "id": "rtbf",
        "name": "RTBF Auvio",
        "hosts": ["www.rtbf.be", "auvio.rtbf.be"],
        "paths": [r"^/.+"],
        "evidence": "https://www.formula1.com/en/information/f1-broadcast-information.45y3LNsT1D6VoK0ZmX8ciJ",
    },
    {
        "id": "play-sports",
        "name": "Play Sports",
        "hosts": ["www.playsports.be"],
        "paths": [r"^/.+"],
        "evidence": "https://www.formula1.com/en/information/f1-broadcast-information.45y3LNsT1D6VoK0ZmX8ciJ",
    },
    {
        "id": "telenet",
        "name": "Telenet",
        "hosts": ["www.telenet.be"],
        "paths": [r"^/.+"],
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
    },
    {
        "id": "eleven-sports-pl",
        "name": "Eleven Sports Poland",
        "hosts": ["elevensports.pl", "www.elevensports.pl"],
        "paths": [r"^/.+"],
        "evidence": "https://www.formula1.com/en/information/f1-broadcast-information.45y3LNsT1D6VoK0ZmX8ciJ",
    },
    {
        "id": "bilibili",
        "name": "bilibili",
        "hosts": ["www.bilibili.com", "bilibili.com"],
        "paths": [r"^/video/[^/]+/?$", r"^/bangumi/play/[^/]+/?$"],
        "evidence": "https://www.bilibili.com/",
    },
]

# Rights-holder evidence is deliberately separate from candidate URL grammar.
# It helps maintainers choose a platform, but never makes an arbitrary URL publishable.
RIGHTS = [
    {
        "competition_id": "jolpica:f1",
        "platform_id": "apple-tv",
        "regions": ["US"],
        "valid_through": "2030-12-31",
        "evidence": "https://www.apple.com/newsroom/2025/10/apple-is-the-exclusive-new-broadcast-partner-for-formula-1-in-the-us/",
    },
    {
        "competition_id": "jolpica:f1",
        "platform_id": "tencent-sports",
        "regions": ["CN"],
        "valid_through": "2027-12-31",
        "evidence": "https://corp.formula1.com/formula-1-renews-partnership-with-tencent-to-broadcast-f1-in-mainland-china/",
    },
    {
        "competition_id": "jolpica:f1",
        "platform_id": "fod",
        "regions": ["JP"],
        "valid_through": "2030-12-31",
        "evidence": "https://corp.formula1.com/fuji-tv-to-exclusively-broadcast-formula-1-in-japan-in-new-long-term-deal/",
    },
    {
        "competition_id": "jolpica:f1",
        "platform_id": "sky-sports",
        "regions": ["GB", "IE"],
        "valid_through": None,
        "evidence": "https://www.formula1.com/en/information/f1-broadcast-information.45y3LNsT1D6VoK0ZmX8ciJ",
    },
    {
        "competition_id": "jolpica:f1",
        "platform_id": "canal-plus",
        "regions": ["FR"],
        "valid_through": None,
        "evidence": "https://www.formula1.com/en/information/f1-broadcast-information.45y3LNsT1D6VoK0ZmX8ciJ",
    },
    {
        "competition_id": "jolpica:f1",
        "platform_id": "sky-de",
        "regions": ["DE"],
        "valid_through": None,
        "evidence": "https://www.formula1.com/en/information/f1-broadcast-information.45y3LNsT1D6VoK0ZmX8ciJ",
    },
    {
        "competition_id": "jolpica:f1",
        "platform_id": "sky-de",
        "regions": ["AT", "CH"],
        "valid_through": None,
        "evidence": "https://www.formula1.com/en/information/f1-broadcast-information.45y3LNsT1D6VoK0ZmX8ciJ",
    },
    {
        "competition_id": "jolpica:f1",
        "platform_id": "sky-it",
        "regions": ["IT"],
        "valid_through": None,
        "evidence": "https://www.formula1.com/en/information/f1-broadcast-information.45y3LNsT1D6VoK0ZmX8ciJ",
    },
    {
        "competition_id": "jolpica:f1",
        "platform_id": "sky-it",
        "regions": ["CH"],
        "valid_through": None,
        "evidence": "https://www.formula1.com/en/information/f1-broadcast-information.45y3LNsT1D6VoK0ZmX8ciJ",
    },
    {
        "competition_id": "jolpica:f1",
        "platform_id": "canal-plus",
        "regions": ["CH"],
        "valid_through": None,
        "evidence": "https://www.formula1.com/en/information/f1-broadcast-information.45y3LNsT1D6VoK0ZmX8ciJ",
    },
    {
        "competition_id": "jolpica:f1",
        "platform_id": "dazn",
        "regions": ["ES", "PT"],
        "valid_through": None,
        "evidence": "https://www.formula1.com/en/information/f1-broadcast-information.45y3LNsT1D6VoK0ZmX8ciJ",
    },
    {
        "competition_id": "jolpica:f1",
        "platform_id": "viaplay",
        "regions": ["NL", "DK", "FI", "NO", "SE"],
        "valid_through": None,
        "evidence": "https://www.formula1.com/en/information/f1-broadcast-information.45y3LNsT1D6VoK0ZmX8ciJ",
    },
    {
        "competition_id": "jolpica:f1",
        "platform_id": "rtbf",
        "regions": ["BE"],
        "valid_through": None,
        "evidence": "https://www.formula1.com/en/information/f1-broadcast-information.45y3LNsT1D6VoK0ZmX8ciJ",
    },
    {
        "competition_id": "jolpica:f1",
        "platform_id": "play-sports",
        "regions": ["BE"],
        "valid_through": None,
        "evidence": "https://www.formula1.com/en/information/f1-broadcast-information.45y3LNsT1D6VoK0ZmX8ciJ",
    },
    {
        "competition_id": "jolpica:f1",
        "platform_id": "eleven-sports-pl",
        "regions": ["PL"],
        "valid_through": None,
        "evidence": "https://www.formula1.com/en/information/f1-broadcast-information.45y3LNsT1D6VoK0ZmX8ciJ",
    },
    {
        "competition_id": "balldontlie:nba",
        "platform_id": "nba",
        "regions": [
            "US",
            "JP",
            "GB",
            "IE",
            "FR",
            "DE",
            "AT",
            "CH",
            "IT",
            "ES",
            "PT",
            "NL",
            "BE",
            "DK",
            "FI",
            "NO",
            "SE",
            "PL",
        ],
        "valid_through": None,
        "evidence": "https://pr.nba.com/nba-tap-to-watch-initiative/",
    },
    {
        "competition_id": "balldontlie:nba",
        "platform_id": "espn",
        "regions": ["US"],
        "valid_through": "2036-06-30",
        "evidence": "https://www.nba.com/news/nba-media-agreements-2024",
    },
    {
        "competition_id": "balldontlie:nba",
        "platform_id": "peacock",
        "regions": ["US"],
        "valid_through": "2036-06-30",
        "evidence": "https://www.nba.com/news/nba-media-agreements-2024",
    },
    {
        "competition_id": "balldontlie:nba",
        "platform_id": "prime-video",
        "regions": ["US"],
        "valid_through": "2036-06-30",
        "evidence": "https://www.nba.com/news/nba-media-agreements-2024",
    },
    {
        "competition_id": "balldontlie:nba",
        "platform_id": "tencent-sports",
        "regions": ["CN"],
        "valid_through": None,
        "evidence": "https://support.watch.nba.com/hc/en-us/articles/115000586373-Accessing-NBA-League-Pass-in-China",
    },
    {
        "competition_id": "balldontlie:nba",
        "platform_id": "migu",
        "regions": ["CN"],
        "valid_through": None,
        "evidence": "https://support.watch.nba.com/hc/en-us/articles/115000586373-Accessing-NBA-League-Pass-in-China",
    },
    {
        "competition_id": "football-data:PL",
        "platform_id": "nbc-sports",
        "regions": ["US"],
        "valid_through": "2028-06-30",
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
    },
    {
        "competition_id": "football-data:PL",
        "platform_id": "peacock",
        "regions": ["US"],
        "valid_through": "2028-06-30",
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
    },
    {
        "competition_id": "football-data:PL",
        "platform_id": "migu",
        "regions": ["CN"],
        "valid_through": "2028-06-30",
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
    },
    {
        "competition_id": "football-data:PL",
        "platform_id": "u-next",
        "regions": ["JP"],
        "valid_through": "2031-06-30",
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
    },
    {
        "competition_id": "football-data:PL",
        "platform_id": "sky-sports",
        "regions": ["GB", "IE"],
        "valid_through": "2029-06-30",
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
    },
    {
        "competition_id": "football-data:PL",
        "platform_id": "canal-plus",
        "regions": ["FR", "PL"],
        "valid_through": "2028-06-30",
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
    },
    {
        "competition_id": "football-data:PL",
        "platform_id": "canal-plus",
        "regions": ["CH"],
        "valid_through": "2028-06-30",
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
    },
    {
        "competition_id": "football-data:PL",
        "platform_id": "sky-de",
        "regions": ["DE", "AT"],
        "valid_through": "2028-06-30",
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
    },
    {
        "competition_id": "football-data:PL",
        "platform_id": "sky-de",
        "regions": ["CH"],
        "valid_through": "2028-06-30",
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
    },
    {
        "competition_id": "football-data:PL",
        "platform_id": "sky-it",
        "regions": ["IT"],
        "valid_through": "2028-06-30",
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
    },
    {
        "competition_id": "football-data:PL",
        "platform_id": "sky-it",
        "regions": ["CH"],
        "valid_through": "2028-06-30",
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
    },
    {
        "competition_id": "football-data:PL",
        "platform_id": "dazn",
        "regions": ["ES", "PT"],
        "valid_through": "2028-06-30",
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
    },
    {
        "competition_id": "football-data:PL",
        "platform_id": "viaplay",
        "regions": ["NL", "DK", "FI", "NO", "SE"],
        "valid_through": "2028-06-30",
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
    },
    {
        "competition_id": "football-data:PL",
        "platform_id": "telenet",
        "regions": ["BE"],
        "valid_through": "2028-06-30",
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
    },
]

# These are the provider products that the calendar can open when a user has
# not attached a personal link for the event.  They intentionally point at a
# provider's sport/league product page rather than a guessed media stream or
# a short-lived per-game URL.  Rights still decide whether a product can be
# selected for a competition and region.
PRODUCTS = {
    ("jolpica:f1", "apple-tv"): {
        "url": "https://tv.apple.com/us/info/watch-f1",
        "title": "Apple TV · Formula 1",
    },
    ("jolpica:f1", "tencent-sports"): {
        "url": "https://sports.qq.com/kbsweb/#100360",
        "title": "腾讯体育 · Formula 1",
    },
    ("jolpica:f1", "fod"): {
        "url": "https://fod.fujitv.co.jp/title/91di/",
        "title": "FOD · Formula 1",
    },
    ("balldontlie:nba", "nba"): {
        "url": "https://www.nba.com/watch/featured",
        "title": "NBA · Watch",
    },
    ("balldontlie:nba", "espn"): {
        "url": "https://www.espn.com/watch/",
        "title": "ESPN · Watch",
    },
    ("balldontlie:nba", "peacock"): {
        "url": "https://www.peacocktv.com/sports/nba",
        "title": "Peacock · NBA",
    },
    ("balldontlie:nba", "prime-video"): {
        "url": "https://www.primevideo.com/-/en_US/sports",
        "title": "Prime Video · Sports",
    },
    ("balldontlie:nba", "tencent-sports"): {
        "url": "https://sports.qq.com/kbsweb/index.htm#nba",
        "title": "腾讯体育 · NBA",
    },
    ("balldontlie:nba", "migu"): {
        "url": "https://www.miguvideo.com/p/home/3cd6ba04967742879aaa40bee02a99a6",
        "title": "咪咕视频 · NBA",
    },
    ("football-data:PL", "nbc-sports"): {
        "url": "https://www.nbcsports.com/watch/soccer",
        "title": "NBC Sports · Premier League",
    },
    ("football-data:PL", "peacock"): {
        "url": "https://www.peacocktv.com/sports/premier-league",
        "title": "Peacock · Premier League",
    },
    ("football-data:PL", "migu"): {
        "url": "https://www.miguvideo.com/mgs/website/prd/sportsHomePage.html?pageId=0c40bbc85fa345bbba20f8e5fd11a922",
        "title": "咪咕视频 · Premier League",
    },
}

RIGHTS_REVIEWED_AT = "2026-09-18T00:00:00+00:00"


def registry():
    return [
        {
            **rule,
            "verification": "candidate_only",
            "mobile_opening": "verified_https_app_link" if rule.get("app_paths") else "web_handoff",
            "rights": [
                {
                    **right,
                    "product_url": PRODUCTS.get((right["competition_id"], right["platform_id"]), {}).get("url"),
                    "product_title": PRODUCTS.get((right["competition_id"], right["platform_id"]), {}).get(
                        "title"
                    ),
                }
                for right in RIGHTS
                if right["platform_id"] == rule["id"]
            ],
        }
        for rule in RULES
    ]


def selected_product(event, config):
    """Select the configured official product for one event.

    This is deliberately a product-directory lookup, not per-event discovery.
    The user's regional preference narrows the rights matrix; an explicit
    platform preference wins within that matrix, otherwise the first
    configured product wins.  A personal link is applied later by
    ``selected_links`` and can hide this result for that user.
    """

    competition_id = event.competition_id
    preferences = (config or {}).get("preferences", {})
    region = preferences.get("watch_region")
    platform_preferences = preferences.get("broadcast_platforms", {})
    preferred_platform = platform_preferences.get(
        f"{region}:{competition_id}" if region else competition_id
    )
    candidates = [
        right
        for right in RIGHTS
        if right["competition_id"] == competition_id
        and (not region or region in right["regions"])
        and (competition_id, right["platform_id"]) in PRODUCTS
    ]
    if preferred_platform:
        preferred = [right for right in candidates if right["platform_id"] == preferred_platform]
        if preferred:
            candidates = preferred
    if not candidates:
        return None

    right = candidates[0]
    product = PRODUCTS[(competition_id, right["platform_id"])]
    opening = mobile_opening(product["url"])
    valid_until = (
        f"{right['valid_through']}T23:59:59+00:00" if right.get("valid_through") else None
    )
    return {
        "id": f"product:{event.id}:{right['platform_id']}",
        "url": product["url"],
        "title": product["title"],
        "kind": "live",
        "platform": opening["platform_id"],
        "origin": "official",
        "access": "unknown",
        "regions": right["regions"] if region else [],
        "created_at": RIGHTS_REVIEWED_AT,
        "metadata": {
            **opening,
            "content_type": "official_match",
            "content_label": "官方直播产品",
            "access_label": "观看条件未验证",
            "region_label": f"{region} 地区版权方" if region else "按候选版权方自动选择",
            "evidence_url": right["evidence"],
            "reviewed_at": RIGHTS_REVIEWED_AT,
            "valid_until": valid_until,
            "network_status": "configured",
            "network_checked_at": None,
            "device_tests": [],
        },
    }


def platform_rule(value: str):
    parsed = urlsplit(value)
    return next((rule for rule in RULES if parsed.hostname in rule["hosts"]), None)


def mobile_opening(value: str):
    rule = platform_rule(value)
    if not rule:
        return {
            "platform_id": "unknown",
            "platform_name": "官方平台",
            "mobile_opening": "web_handoff",
        }
    supported = any(re.search(pattern, urlsplit(value).path) for pattern in rule.get("app_paths", []))
    return {
        "platform_id": rule["id"],
        "platform_name": rule["name"],
        "mobile_opening": "verified_https_app_link" if supported else "web_handoff",
    }


def rights_cover(value: str, competition_id: str, regions: list[str]) -> bool:
    rule = platform_rule(value)
    if not rule:
        return False
    claimed = set(regions)
    covered = {
        region
        for right in RIGHTS
        if right["competition_id"] == competition_id and right["platform_id"] == rule["id"]
        for region in right["regions"]
    }
    return bool(claimed) and claimed <= covered


def candidate_url(value: str):
    # Security checks happen before canonicalization can remove unsafe material.
    from urllib.parse import unquote

    parsed = urlsplit(value.strip())
    decoded_path = unquote(parsed.path).lower()
    if any(x in decoded_path for x in ["\\", "..", ".m3u8", ".mpd", ".mp4", ".m4s", ".ts/"]):
        raise ValueError("UNSAFE_CONTENT_URL")
    if any(
        x.lower().replace("-", "_")
        in {
            "token",
            "access_token",
            "auth",
            "authorization",
            "signature",
            "sig",
            "key",
            "jwt",
            "session",
            "sessionid",
            "code",
            "password",
            "expires",
            "policy",
            "credential",
            "x_amz_signature",
            "x_amz_credential",
        }
        for x in parse_qs(parsed.query, keep_blank_values=True)
    ):
        raise ValueError("CREDENTIAL_URL")
    url, platform = canonical_url(value)
    parsed = urlsplit(url)
    rule = next((x for x in RULES if parsed.hostname in x["hosts"]), None)
    if not rule or not any(re.search(pattern, parsed.path) for pattern in rule["paths"]):
        raise ValueError("UNSUPPORTED_CONTENT_PATH")
    if (
        parsed.path.rstrip("/").lower()
        in {"/login", "/signin", "/sign-in", "/subscribe", "/account", "/auth", "/watch", "/games"}
    ):
        raise ValueError("CONTENT_PAGE_REQUIRED")
    return url, platform


def _public_ips(host):
    addresses = sorted({entry[4][0] for entry in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    if not addresses or any(not ipaddress.ip_address(value).is_global for value in addresses):
        raise ValueError("UNSAFE_ADDRESS")
    return addresses


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, timeout=5, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Connect to the already validated IP; TLS still verifies the original host.
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            if not ipaddress.ip_address(raw.getpeername()[0]).is_global:
                raise ValueError("UNSAFE_ADDRESS")
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def head_probe(value: str) -> str:
    """No redirects, cookies, proxies, bearer headers, body storage or exception URLs."""
    connection = None
    try:
        url, _ = candidate_url(value)
        parsed = urlsplit(url)
        addresses = _public_ips(parsed.hostname)
        connection = _PinnedHTTPS(parsed.hostname, addresses[0])
        target = parsed.path + ("?" + parsed.query if parsed.query else "")
        connection.request(
            "HEAD",
            target,
            headers={
                "User-Agent": "AnkeSports-LinkCheck/0.1",
                "Accept": "text/html,application/xhtml+xml",
                "Connection": "close",
            },
        )
        response = connection.getresponse()  # http.client bounds headers; HEAD reads no body.
        if response.status in {301, 302, 303, 307, 308}:
            location = response.getheader("Location", "")
            if not location or len(location) > 2000:
                return "unsafe"
            candidate_url(urljoin(url, location))
            # Even an allowed redirect may lead to a different event. A reviewer must inspect it.
            return "redirect_review"
        if response.status in {404, 410}:
            return "not_found"
        if response.status in {401, 403, 451}:
            return "restricted"
        if response.status in {405, 501}:
            return "head_unsupported"
        if 200 <= response.status < 300:
            content_type = response.getheader("Content-Type", "").lower()
            if any(x in content_type for x in ["mpegurl", "dash+xml", "video/", "octet-stream"]):
                return "unsafe"
            return "reachable"
        return "retry"
    except (ValueError, http.client.InvalidURL):
        return "unsafe"
    except Exception as exc:
        from fastapi import HTTPException

        return "unsafe" if isinstance(exc, HTTPException) else "retry"
    finally:
        if connection:
            connection.close()
