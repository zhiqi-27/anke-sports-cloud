"""Explicit local synthetic creator preview. No external API request is permitted."""

import json
import os
from pathlib import Path
import sys

CHANNEL = "UC" + "c" * 22
AUTO_VIDEO, REVIEW_VIDEO, RECAP_VIDEO = "auto0000001", "review00001", "recap000001"


def install_fixture():
    if os.getenv("WEBSITE_INSTANCE_ID") or os.getenv("ANKE_SPORTS_ENV") != "local":
        raise RuntimeError("LOCAL_CREATOR_FIXTURE_ONLY")
    if os.getenv("ANKE_SPORTS_YOUTUBE_PROJECT_ID") != "synthetic-document-creators-ui":
        raise RuntimeError("SYNTHETIC_PROJECT_REQUIRED")
    fixture = json.loads(Path(os.environ["ANKE_DOCUMENT_CREATOR_FIXTURE"]).read_text())
    if fixture.get("channel_id") != CHANNEL:
        raise RuntimeError("SYNTHETIC_CHANNEL_REQUIRED")
    import httpx

    client_type = httpx.Client

    def response(request):
        if (
            request.url.host != "www.googleapis.com"
            or request.headers.get("x-goog-api-key") != "synthetic-no-network"
        ):
            raise RuntimeError("EXTERNAL_REQUEST_FORBIDDEN_IN_FIXTURE")
        if request.url.path == "/youtube/v3/channels":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": CHANNEL,
                            "snippet": {"title": "演示分析频道（合成数据）"},
                            "contentDetails": {"relatedPlaylists": {"uploads": "UUsynthetic"}},
                        }
                    ]
                },
            )
        if request.url.path == "/youtube/v3/playlistItems":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "contentDetails": {
                                "videoId": video["id"],
                                "videoPublishedAt": video["snippet"]["publishedAt"],
                            }
                        }
                        for video in fixture["videos"]
                    ]
                },
            )
        if request.url.path == "/youtube/v3/videos":
            requested = set(request.url.params.get("id", "").split(","))
            return httpx.Response(
                200, json={"items": [video for video in fixture["videos"] if video["id"] in requested]}
            )
        raise RuntimeError("EXTERNAL_REQUEST_FORBIDDEN_IN_FIXTURE")

    httpx.Client = lambda **kwargs: client_type(transport=httpx.MockTransport(response), **kwargs)


def main():
    if "--worker" in sys.argv:
        install_fixture()
        from app.document_worker import main as worker

        return worker([])
    os.environ.update(
        {
            "ANKE_SPORTS_ENV": "local",
            "YOUTUBE_API_KEY": "synthetic-no-network",
            "ANKE_SPORTS_YOUTUBE_PROJECT_ID": "synthetic-document-creators-ui",
            "ANKE_SPORTS_YOUTUBE_DAILY_BUDGET": "200",
        }
    )
    os.environ.setdefault("ANKE_DOCUMENT_UI_PORT", "3008")
    from experiments import document_ui as preview
    from app.document_accounts import now

    fixture_path = Path(preview.storage.name) / "creator-fixture.json"
    titles = [
        (AUTO_VIDEO, f"合成演示：演示蓝队 演示红队 {preview.events[3]['local_date']} 赛前前瞻"),
        (REVIEW_VIDEO, "合成演示：演示蓝队 赛前前瞻"),
        (RECAP_VIDEO, f"合成演示：演示蓝队 演示红队 {preview.events[0]['local_date']} 赛后复盘"),
    ]
    fixture_path.write_text(
        json.dumps(
            {
                "channel_id": CHANNEL,
                "videos": [
                    {
                        "id": ident,
                        "snippet": {
                            "channelId": CHANNEL,
                            "title": title,
                            "description": "仅用于本地交互验收，不是真实视频或播放入口。",
                            "publishedAt": now(),
                        },
                        "status": {"privacyStatus": "public"},
                    }
                    for ident, title in titles
                ],
            },
            ensure_ascii=False,
        )
    )
    os.environ["ANKE_DOCUMENT_CREATOR_FIXTURE"] = str(fixture_path)
    install_fixture()
    preview.WORKER_COMMAND = [sys.executable, "-m", "experiments.document_creators_ui", "--worker"]

    @preview.app.get("/api/v1/local/creator-fixture", include_in_schema=False)
    def marker():
        return {
            "fixture": "document-creators-v1",
            "channel_id": CHANNEL,
            "automatic_video": AUTO_VIDEO,
            "review_video": REVIEW_VIDEO,
            "recap_video": RECAP_VIDEO,
        }

    preview.app.router.routes.insert(0, preview.app.router.routes.pop())
    preview.uvicorn.run(preview.app, host="127.0.0.1", port=preview.preview_port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
