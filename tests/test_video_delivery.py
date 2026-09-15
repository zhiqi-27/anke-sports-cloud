from types import SimpleNamespace

from app.calendar_rules import delivery_links, describe


def video(index):
    return {
        "id": f"video-{index}",
        "url": f"https://www.youtube.com/watch?v=video{index:06d}",
        "title": f"Hidden source title {index}",
        "kind": "video",
        "content_labels": [f"🎙️采访 {index}", "📊战术分析"],
        "platform": "YouTube",
        "creator": f"Channel {index}",
        "origin": "automatic",
        "pinned": False,
    }


def test_video_delivery_has_no_per_event_limit_and_uses_labels_plus_urls():
    links = [video(index) for index in range(5)]
    assert delivery_links(links) == links
    event = SimpleNamespace(
        demo=False,
        duration=150,
        provider="provider",
        source_url="https://example.com/schedule",
    )
    description = describe(
        event,
        links,
        {"preferences": {"spoiler_free": True}},
    )
    assert description.count("https://www.youtube.com/watch?") == 5
    assert "🎙️采访 4 · 📊战术分析" in description
    assert "Hidden source title" not in description
