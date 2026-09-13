"""Storage-independent broadcast validation and public evidence labels."""

from app.broadcast_schemas import BroadcastDraft
from app.platforms import candidate_url
from app.security import digest, problem

CONTENT_LABELS = {
    "official_match": "比赛直播",
    "reservation": "直播预约",
    "programme": "查看官方播出信息",
    "watch_along": "同步解说，无比赛画面",
    "replay": "官方完整回放",
}
ACCESS_LABELS = {
    "unknown": "观看条件未验证",
    "free": "免费",
    "login": "需要登录",
    "subscription": "需要订阅",
    "pay_per_view": "单次付费",
}


def normalized(data):
    value = BroadcastDraft.model_validate(data).model_dump()
    try:
        value["url"], _ = candidate_url(value["url"])
        value["evidence_url"], _ = candidate_url(value["evidence_url"])
    except ValueError:
        problem("INVALID_URL", "请使用不含凭据的具体内容或官方证据页面")
    value["title"] = value["title"].strip()
    value["evidence_note"] = value["evidence_note"].strip()
    return value


def public_metadata(record):
    value = record.published
    mode, regions = value["region_mode"], value["regions"]
    region_label = (
        "地区限制未验证"
        if mode == "unknown"
        else "来源声明全球可用"
        if mode == "global"
        else ("仅限 " if mode == "include" else "不含 ") + "、".join(regions)
    )
    return {
        "content_type": value["content_type"],
        "content_label": CONTENT_LABELS[value["content_type"]],
        "access_label": ACCESS_LABELS[value["access"]],
        "region_label": region_label,
        "evidence_url": value["evidence_url"],
        "reviewed_at": value["reviewed_at"],
        "valid_until": value["valid_until"],
        "network_status": record.network_status,
        "network_checked_at": record.network_checked_at,
        "device_tests": [
            {k: v for k, v in row.items() if k not in {"evidence_ref", "url_hash"}}
            for row in record.device_tests
            if row["url_hash"] == digest(value["url"])
        ],
    }
