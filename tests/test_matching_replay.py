"""Regression truth is synthetic and intentionally includes review-only positives."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from app.matching import evaluate, explicit_date
from scripts.replay_matching import Dataset, compare, replay

CORPUS = Path(__file__).parent / "fixtures/matching-synthetic-v1.json"


def corpus():
    return Dataset.model_validate_json(CORPUS.read_bytes())


def test_replay_accounts_for_wrong_and_withheld_links_without_claiming_acceptance():
    report = replay(corpus(), sha256(CORPUS.read_bytes()).hexdigest())
    assert report["metrics"]["false_positive"] == 0
    assert report["metrics"]["true_positive"] == 13
    assert report["metrics"]["false_negative"] == 1  # Phase appears only in description.
    assert report["metrics"]["automatic_recall"] == 13 / 14
    assert report["metrics"]["automatic_case_coverage"] == 13 / 32
    assert not report["meets_product_acceptance"]
    assert "title" not in json.dumps(report) and "description" not in report
    cases = {row["case_id"]: row for row in report["cases"]}
    for case in ["description_promo", "multiple_dates", "score_not_date", "ambiguous_slash"]:
        assert cases[case]["automatic"] == []
        assert cases[case]["candidates"][0]["decision"] == "needs_review"


@pytest.mark.parametrize("title,result", [
    ("2026-09-10", (True, False, False)),
    ("2026/09/10", (True, False, False)),
    ("2026.09.10", (True, False, False)),
    ("2026年9月10日", (True, False, False)),
    ("september 10th, 2026", (True, False, False)),
    ("10 sept. 2026", (True, False, False)),
    ("9月10日 and 2026-09-10", (True, False, False)),
    ("9/10", (False, False, True)),
    ("9-10", (False, False, False)),
    ("9:10", (False, False, False)),
    ("2026-09-10 2026-09-11", (False, False, True)),
    ("2025-09-10", (False, True, False)),
    ("2026-02-29", (False, True, False)),
    ("32/13", (False, True, False)),
])
def test_calendar_dates_do_not_confuse_scores_locales_or_conflicting_mentions(title, result):
    assert explicit_date(title, corpus().events[0]) == result


def test_per_sport_metrics_do_not_count_other_sports_in_a_multi_sport_case():
    data = corpus().model_dump()
    case = data["cases"][0]
    case["event_ids"].append("football")
    case["expected_links"].append({"event_id": "football", "kind": "preview"})
    data["cases"] = [case]
    report = replay(Dataset.model_validate(data), "fixture")
    assert report["by_sport"]["basketball"]["true_positive"] == 1
    assert report["by_sport"]["basketball"]["false_negative"] == 0
    assert report["by_sport"]["football"]["true_positive"] == 0
    assert report["by_sport"]["football"]["false_negative"] == 1


def test_comparison_reports_decision_changes_and_refuses_changed_corpus():
    current = replay(corpus(), "same")
    previous = deepcopy(current)
    previous["rule_version"] = "prior"
    previous["cases"][0]["candidates"][0]["decision"] = "needs_review"
    diff = compare(previous, current)
    assert [row["case_id"] for row in diff["changed_cases"]] == ["iso_preview"]
    previous["dataset_sha256"] = "other"
    with pytest.raises(ValueError, match="DATASET_CHANGED"):
        compare(previous, current)


def test_replay_cli_has_no_app_database_configuration_and_never_overwrites(tmp_path, monkeypatch):
    monkeypatch.setenv("ANKE_SPORTS_DATABASE_URL", "unsupported://must-not-connect")
    monkeypatch.setenv("ANKE_SPORTS_FIREBASE_CREDENTIALS_JSON", "must-not-read")
    output = tmp_path / "report.json"
    cmd = [sys.executable, "-m", "scripts.replay_matching", str(CORPUS), "--output", str(output)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    before = output.read_bytes()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    assert result.returncode == 2 and output.read_bytes() == before
    assert "must-not-read" not in result.stderr + result.stdout


def test_replay_rejects_unknown_secret_fields_and_bad_references(tmp_path):
    data = corpus().model_dump()
    data["cases"][0]["video"]["feed_token"] = "synthetic-secret-should-not-print"
    source = tmp_path / "bad.json"
    source.write_text(json.dumps(data))
    result = subprocess.run(
        [sys.executable, "-m", "scripts.replay_matching", str(source), "--output", str(tmp_path / "unused")],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 2
    assert "synthetic-secret" not in result.stderr + result.stdout
    assert not (tmp_path / "unused").exists()
    data = corpus().model_dump()
    data["cases"][0]["expected_links"][0]["event_id"] = "missing"
    with pytest.raises(ValidationError):
        Dataset.model_validate(data)


def test_candidate_order_does_not_resolve_doubleheaders():
    data = corpus()
    case = next(c for c in data.cases if c.id == "repeated_opponent")
    events = [e for e in data.events if e.id in case.event_ids]
    before = {row["event_id"]: row for row in evaluate(case.video, events)}
    after = {row["event_id"]: row for row in evaluate(case.video, list(reversed(events)))}
    assert before == after
    assert all(row["decision"] == "needs_review" for row in before.values())
