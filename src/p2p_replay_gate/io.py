from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import CasePolicy, P2PEvent, Scenario


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def load_events(path: Path) -> list[P2PEvent]:
    events: list[P2PEvent] = []
    with path.open("r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(P2PEvent.from_dict(json.loads(line)))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSONL: {exc}") from exc
    _ensure_unique(path, [event.event_id for event in events], "event_id")
    return sorted(events, key=lambda event: (event.case_id, event.timestamp, event.event_id))


def load_policy(path: Path) -> dict[str, CasePolicy]:
    data = read_json(path)
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a list of policies")
    policies = [CasePolicy.from_dict(row) for row in data]
    _ensure_unique(path, [policy.case_id for policy in policies], "case_id")
    return {policy.case_id: policy for policy in policies}


def load_scenarios(path: Path) -> list[Scenario]:
    data = read_json(path)
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a list of scenarios")
    scenarios = [Scenario.from_dict(row) for row in data]
    _ensure_unique(path, [scenario.scenario_id for scenario in scenarios], "scenario_id")
    return scenarios


def group_events(events: list[P2PEvent]) -> dict[str, list[P2PEvent]]:
    grouped: dict[str, list[P2PEvent]] = {}
    for event in events:
        grouped.setdefault(event.case_id, []).append(event)
    return grouped


def _ensure_unique(path: Path, values: list[str], label: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise ValueError(f"{path} has duplicate {label}: {sorted(duplicates)}")

