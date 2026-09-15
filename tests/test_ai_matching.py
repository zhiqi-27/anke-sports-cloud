import json
from types import SimpleNamespace

import httpx
from pydantic import SecretStr
import pytest

from app.ai_matching import AIMatchingUnavailable, clear_ai_match_cache, evaluate_with_ai
from app.config import Settings
from app.job_rules import error_code
from app.matching import evaluate


def event(ident, start, *, opponent="Oklahoma City Thunder", short="OKC"):
    return SimpleNamespace(
        id=ident,
        sport="basketball",
        competition_id="nba",
        title=f"{opponent} @ San Antonio Spurs",
        source_key="balldontlie:" + ident,
        starts_at=start,
        timezone="America/Chicago",
        duration=150,
        status="finished",
        participants=[
            {"name": opponent, "short_name": short},
            {"name": "San Antonio Spurs", "short_name": "SAS"},
        ],
    )


def video(title="Thunder stun San Antonio after a wild finish"):
    return SimpleNamespace(
        channel_id="UC000000000000000000001",
        title=title,
        description="Full post-game reaction and analysis.",
        comments=["That fourth-quarter comeback decided this Thunder-Spurs game."],
        published_at="2026-10-08T04:00:00+00:00",
    )


def config(**values):
    return Settings(
        matching_ai_enabled=True,
        matching_ai_api_key=SecretStr("test-key"),
        matching_ai_base_url="https://model.invalid/v1",
        **values,
    )


def response(value):
    return httpx.Response(
        200,
        json={
            "candidates": [{"content": {"parts": [{"text": json.dumps(value)}]}}]
        },
    )


def assessment(*scores, labels=None):
    scores = scores or (("game-1", 0.93),)
    return {
        "content_labels": labels if labels is not None else ["🎙️赛后讨论", "📊战术分析"],
        "assessments": [
            {
                "event_id": event_id,
                "confidence": confidence,
                "evidence_codes": ["OPPONENT_PAIR", "SCORE_RESULT", "TITLE_SEMANTICS"],
            }
            for event_id, confidence in scores
        ],
    }


def test_cost_first_ai_promotes_a_semantic_match_without_explicit_phase_or_date():
    clear_ai_match_cache()
    game = event("game-1", "2026-10-08T00:00:00+00:00")
    assert evaluate(video(), [game])[0]["decision"] == "needs_review"
    requests = []

    def handle(request):
        requests.append(request)
        return response(assessment())

    client = httpx.Client(transport=httpx.MockTransport(handle))
    result = evaluate_with_ai(video(), [game], config(), creator_name="Official Team", client=client)
    assert result[0]["decision"] == "automatic"
    assert result[0]["kind"] == "video"
    assert result[0]["content_labels"] == ["🎙️赛后讨论", "📊战术分析"]
    assert result[0]["rule_version"] == "matching-ai-v4"
    assert {"AI_CONFIDENCE_093", "AI_AUTO_THRESHOLD_MET"}.issubset(result[0]["reason_codes"])
    payload = json.loads(requests[0].content)
    assert requests[0].url.path.endswith("/models/gemini-3.1-flash-lite:generateContent")
    assert requests[0].headers["x-goog-api-key"] == "test-key"
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert payload["generationConfig"]["responseJsonSchema"]["type"] == "object"
    assert payload["generationConfig"]["maxOutputTokens"] == 1200
    prompt = payload["contents"][0]["parts"][0]["text"]
    assert "user" not in prompt
    assert "Official Team" in prompt
    assert "fourth-quarter comeback" in prompt


def test_each_video_event_pair_is_scored_independently_without_a_runner_up_margin():
    clear_ai_match_cache()
    games = [
        event("game-1", "2026-10-08T00:00:00+00:00"),
        event("game-2", "2026-10-09T00:00:00+00:00"),
    ]
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: response(assessment(("game-1", 0.93), ("game-2", 0.92)))
        )
    )
    result = {
        row["event_id"]: row for row in evaluate_with_ai(video(), games, config(), client=client)
    }
    assert result["game-1"]["decision"] == "automatic"
    assert result["game-2"]["decision"] == "automatic"
    assert all(
        not any(code.startswith("AI_MARGIN_") for code in row["reason_codes"])
        for row in result.values()
    )


def test_high_confidence_score_auto_attaches_without_a_separate_model_decision():
    clear_ai_match_cache()
    game = event("game-1", "2026-10-08T00:00:00+00:00")
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: response(assessment(("game-1", 0.97)))
        )
    )
    result = evaluate_with_ai(video(), [game], config(), client=client)[0]
    assert result["decision"] == "automatic"
    assert "AI_SCORE_AUTOMATIC" in result["reason_codes"]


def test_low_confidence_rejects_weak_candidate_and_model_failure_aborts_the_write():
    clear_ai_match_cache()
    game = event("game-1", "2026-10-08T00:00:00+00:00")
    low = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: response(assessment(("game-1", 0.42)))
        )
    )
    assert evaluate_with_ai(video(), [game], config(), client=low)[0]["decision"] == "reject"
    clear_ai_match_cache()
    failed = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    with pytest.raises(AIMatchingUnavailable, match="AI_REQUEST_FAILED") as captured:
        evaluate_with_ai(video(), [game], config(), client=failed)
    assert captured.value.retryable
    assert error_code(captured.value) == "AI_REQUEST_FAILED"


def test_ai_can_reject_a_viable_deterministic_candidate():
    clear_ai_match_cache()
    game = event("game-1", "2026-10-08T00:00:00+00:00")
    exact_video = video("Oklahoma City Thunder @ San Antonio Spurs recap")
    assert evaluate(exact_video, [game])[0]["decision"] == "automatic"
    rejected = assessment(("game-1", 0.2))
    client = httpx.Client(transport=httpx.MockTransport(lambda request: response(rejected)))
    result = evaluate_with_ai(exact_video, [game], config(), client=client)[0]
    assert result["decision"] == "reject"


def test_identical_public_input_reuses_the_warm_instance_cache():
    clear_ai_match_cache()
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        return response(assessment())

    client = httpx.Client(transport=httpx.MockTransport(handle))
    game = event("game-1", "2026-10-08T00:00:00+00:00")
    evaluate_with_ai(video(), [game], config(), client=client)
    evaluate_with_ai(video(), [game], config(), client=client)
    assert calls == 1


def test_disabled_ai_does_not_call_network():
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(AssertionError("called")))
    )
    game = event("game-1", "2026-10-08T00:00:00+00:00")
    assert evaluate_with_ai(video(), [game], Settings(), client=client) == evaluate(video(), [game])
