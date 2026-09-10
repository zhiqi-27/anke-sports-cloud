"""Offline metadata replay. No database, credentials, network or mutation imports."""

import argparse
from collections import Counter
from hashlib import sha256
import json
import inspect
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.matching import RULE_VERSION, evaluate, parse_time


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Participant(StrictRecord):
    name: str = Field(min_length=1, max_length=160)
    short_name: str = Field(min_length=1, max_length=40)


class Event(StrictRecord):
    id: str = Field(min_length=1, max_length=64)
    sport: Literal["basketball", "football", "racing"]
    title: str = Field(max_length=240)
    source_key: str = Field(max_length=220)
    starts_at: str | None
    timezone: str
    duration: int = Field(ge=1, le=1440)
    status: Literal["scheduled", "finished", "cancelled", "postponed"]
    participants: list[dict[str, str]] = Field(max_length=2)

    @model_validator(mode="after")
    def validate_event(self):
        if self.starts_at:
            parse_time(self.starts_at)
        ZoneInfo(self.timezone)
        for participant in self.participants:
            Participant.model_validate(participant)
        return self


class Video(StrictRecord):
    title: str = Field(max_length=300)
    description: str = Field(max_length=10000)
    published_at: str

    @model_validator(mode="after")
    def validate_time(self):
        parse_time(self.published_at)
        return self


class ExpectedLink(StrictRecord):
    event_id: str
    kind: Literal["preview", "recap"]


class Case(StrictRecord):
    id: str = Field(min_length=1, max_length=100)
    language: Literal["en", "zh", "mixed", "other"]
    event_ids: list[str] = Field(min_length=1, max_length=100)
    video: Video
    expected_links: list[ExpectedLink] = Field(max_length=100)


class Dataset(StrictRecord):
    schema_version: Literal[1]
    provenance: Literal["synthetic", "human_labeled"]
    label_note: str = Field(min_length=1, max_length=1000)
    events: list[Event] = Field(min_length=1, max_length=10000)
    cases: list[Case] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def validate_references(self):
        events = {event.id for event in self.events}
        if len(events) != len(self.events) or len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("DUPLICATE_ID")
        for case in self.cases:
            if len(set(case.event_ids)) != len(case.event_ids) or not set(case.event_ids) <= events:
                raise ValueError("INVALID_EVENT_REFERENCE")
            keys = {(item.event_id, item.kind) for item in case.expected_links}
            if len(keys) != len(case.expected_links) or any(k[0] not in case.event_ids for k in keys):
                raise ValueError("INVALID_LABEL_REFERENCE")
        return self


def metrics(rows):
    tp = sum(len(row["correct"]) for row in rows)
    fp = sum(len(row["incorrect"]) for row in rows)
    fn = sum(len(row["missed"]) for row in rows)
    return {
        "cases": len(rows), "true_positive": tp, "false_positive": fp, "false_negative": fn,
        "automatic_precision": tp / (tp + fp) if tp + fp else None,
        "automatic_recall": tp / (tp + fn) if tp + fn else None,
        "automatic_case_coverage": sum(bool(row["automatic"]) for row in rows) / len(rows) if rows else None,
        "decisions": dict(Counter(item["decision"] for row in rows for item in row["candidates"])),
    }


def replay(dataset, dataset_hash, *, evaluator=evaluate, rule_version=RULE_VERSION):
    events = {event.id: event for event in dataset.events}
    rows = []
    for case in dataset.cases:
        candidates = evaluator(case.video, [events[key] for key in case.event_ids])
        actual = {(item["event_id"], item["kind"]) for item in candidates if item["decision"] == "automatic"}
        expected = {(item.event_id, item.kind) for item in case.expected_links}
        rows.append({
            "case_id": case.id, "language": case.language,
            "sports": sorted({events[key].sport for key in case.event_ids}),
            "automatic": sorted(actual), "correct": sorted(actual & expected),
            "incorrect": sorted(actual - expected), "missed": sorted(expected - actual),
            "candidates": candidates,
        })
    # Report IDs and rule reasons; original titles/descriptions are not copied.
    def sport_rows(sport):
        filtered = []
        for row in rows:
            if sport not in row["sports"]:
                continue
            scoped = dict(row)
            for key in ("automatic", "correct", "incorrect", "missed"):
                scoped[key] = [link for link in row[key] if events[link[0]].sport == sport]
            scoped["candidates"] = [item for item in row["candidates"] if events[item["event_id"]].sport == sport]
            filtered.append(scoped)
        return filtered

    return {
        "schema_version": 1, "dataset_sha256": dataset_hash, "rule_version": rule_version,
        "rule_source_sha256": sha256(Path(inspect.getsourcefile(evaluator)).read_bytes()).hexdigest(),
        "provenance": dataset.provenance, "meets_product_acceptance": False,
        "acceptance_note": "Offline labeled-set results only; synthetic data cannot prove real-video quality.",
        "metrics": metrics(rows),
        "by_sport": {sport: metrics(sport_rows(sport))
                     for sport in sorted({e.sport for e in dataset.events})},
        "by_language": {lang: metrics([row for row in rows if row["language"] == lang])
                        for lang in sorted({row["language"] for row in rows})},
        "cases": rows,
    }


def compare(previous, current):
    if previous["dataset_sha256"] != current["dataset_sha256"]:
        raise ValueError("DATASET_CHANGED")
    old = {row["case_id"]: row for row in previous["cases"]}
    if set(old) != {row["case_id"] for row in current["cases"]}:
        raise ValueError("CASE_SET_CHANGED")
    changes = []
    for row in current["cases"]:
        before = old[row["case_id"]]
        prior = {(c["event_id"], c["kind"], c["decision"]) for c in before["candidates"]}
        after = {(c["event_id"], c["kind"], c["decision"]) for c in row["candidates"]}
        if prior != after:
            changes.append({"case_id": row["case_id"], "before": sorted(prior), "after": sorted(after)})
    return {"previous_rule_version": previous["rule_version"], "changed_cases": changes}


def read_bytes(path):
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("INPUT_TOO_LARGE")
    return path.read_bytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    try:
        raw = read_bytes(args.dataset)
        data = Dataset.model_validate_json(raw)
        report = replay(data, sha256(raw).hexdigest())
        if args.compare:
            report["comparison"] = compare(json.loads(read_bytes(args.compare)), report)
        with args.output.open("x", encoding="utf-8") as target:
            json.dump(report, target, ensure_ascii=False, indent=2)
            target.write("\n")
    except (ValueError, OSError, KeyError) as exc:
        # Input validation errors can include the original metadata. Do not dump
        # their contents, URLs or accidentally supplied credentials to a terminal.
        parser.exit(2, f"Replay failed ({type(exc).__name__}); check input and use a new output path.\n")
    print(json.dumps({"rule_version": report["rule_version"], "provenance": report["provenance"],
                      "metrics": report["metrics"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
