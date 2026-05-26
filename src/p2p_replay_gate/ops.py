from __future__ import annotations

import hashlib
import json
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .io import group_events
from .models import CasePolicy, P2PEvent
from .oracle import replay_case, summarize_violations


def build_ops_report(
    events_path: Path,
    policies: dict[str, CasePolicy],
    *,
    iterations: int = 1,
) -> dict[str, Any]:
    parsed = parse_event_log(events_path)
    valid_events = [row["event"] for row in parsed["valid_rows"]]
    deduped_events = dedupe_events(valid_events)
    grouped = group_events(deduped_events)
    replayable_cases = {
        case_id: events
        for case_id, events in grouped.items()
        if case_id in policies
    }

    iterations = max(1, iterations)
    started = time.perf_counter()
    results = []
    for _ in range(iterations):
        results = [
            replay_case(events, policies[case_id])
            for case_id, events in sorted(replayable_cases.items())
        ]
    elapsed = time.perf_counter() - started
    per_iteration_elapsed = elapsed / iterations

    event_count = len(deduped_events)
    case_count = len(replayable_cases)
    schema_counts = schema_version_counts(valid_events)
    duplicate_ids = duplicate_event_ids(valid_events)
    ordering = ordering_findings(parsed["valid_rows"])
    same_timestamp = same_timestamp_collisions(valid_events)
    workers = min(4, max(1, case_count))
    parallel_results = parallel_replay_cases(replayable_cases, policies, workers=workers)
    parallel_matches_serial = violation_signature(results) == violation_signature(parallel_results)

    return {
        "run_id": "p2p-replay-gate-ops-readiness-v0",
        "scope_note": "operational-readiness checks over supplied event log; not a production SLA",
        "input": {
            "events_path": str(events_path),
            "raw_rows": parsed["raw_rows"],
            "valid_rows": len(valid_events),
            "deduped_events": event_count,
            "policy_cases": len(policies),
            "replayable_cases": case_count,
        },
        "idempotency": {
            "key": "event_id",
            "duplicate_event_id_count": sum(duplicate_ids.values()),
            "duplicate_event_ids": dict(sorted(duplicate_ids.items())),
            "dedupe_strategy": "first event_id wins for this report; validate/load paths remain strict",
        },
        "ordering": {
            "key": ["case_id", "timestamp", "event_id"],
            "input_order_inversions": len(ordering["inversions"]),
            "inversion_samples": ordering["inversions"][:10],
            "same_timestamp_collision_count": sum(same_timestamp.values()),
            "same_timestamp_collision_keys": dict(sorted(same_timestamp.items())),
        },
        "schema": {
            "event_schema_version_attr": "attrs.schema_version",
            "schema_version_counts": schema_counts,
            "missing_schema_version_count": schema_counts.get("missing", 0),
            "legacy_default": "p2p.replay_event.v1",
        },
        "consistency": {
            "case_partition_key": "case_id",
            "parallel_workers": workers,
            "parallel_matches_serial": parallel_matches_serial,
            "boundary": "case-local replay only; cross-case transactions require external storage semantics",
        },
        "replay": {
            "iterations": iterations,
            "elapsed_ms_per_iteration": round(per_iteration_elapsed * 1000, 3),
            "events_per_second": round(event_count / per_iteration_elapsed, 1) if per_iteration_elapsed else 0.0,
            "cases_per_second": round(case_count / per_iteration_elapsed, 1) if per_iteration_elapsed else 0.0,
            "replay_digest": replay_digest(deduped_events, policies),
            "resume_cursor": resume_cursor(deduped_events),
            "violations_by_code": summarize_violations(results),
        },
        "readiness": readiness_status(
            parse_errors=parsed["parse_errors"],
            duplicate_event_id_count=sum(duplicate_ids.values()),
            input_order_inversions=len(ordering["inversions"]),
            missing_schema_version_count=schema_counts.get("missing", 0),
            parallel_matches_serial=parallel_matches_serial,
        ),
        "parse_errors": parsed["parse_errors"],
    }


def parse_event_log(path: Path) -> dict[str, Any]:
    valid_rows: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []
    raw_rows = 0
    with path.open("r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            raw_rows += 1
            try:
                data = json.loads(line)
                event = P2PEvent.from_dict(data)
            except Exception as exc:  # noqa: BLE001 - report bad input rows without aborting
                parse_errors.append({"line": lineno, "error": str(exc)})
                continue
            valid_rows.append({"line": lineno, "event": event})
    return {"raw_rows": raw_rows, "valid_rows": valid_rows, "parse_errors": parse_errors}


def dedupe_events(events: list[P2PEvent]) -> list[P2PEvent]:
    seen: set[str] = set()
    output: list[P2PEvent] = []
    for event in events:
        if event.event_id in seen:
            continue
        seen.add(event.event_id)
        output.append(event)
    return sorted(output, key=lambda event: (event.case_id, event.timestamp, event.event_id))


def duplicate_event_ids(events: list[P2PEvent]) -> Counter[str]:
    counts = Counter(event.event_id for event in events)
    return Counter({event_id: count - 1 for event_id, count in counts.items() if count > 1})


def ordering_findings(valid_rows: list[dict[str, Any]]) -> dict[str, Any]:
    latest_by_case: dict[str, tuple[str, str, int]] = {}
    inversions: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
    for row in valid_rows:
        event: P2PEvent = row["event"]
        if event.event_id in seen_event_ids:
            continue
        seen_event_ids.add(event.event_id)
        current = (event.timestamp, event.event_id, row["line"])
        previous = latest_by_case.get(event.case_id)
        if previous and (event.timestamp, event.event_id) < (previous[0], previous[1]):
            inversions.append(
                {
                    "case_id": event.case_id,
                    "line": row["line"],
                    "event_id": event.event_id,
                    "timestamp": event.timestamp,
                    "previous_line": previous[2],
                    "previous_event_id": previous[1],
                    "previous_timestamp": previous[0],
                }
            )
        if not previous or (event.timestamp, event.event_id) > (previous[0], previous[1]):
            latest_by_case[event.case_id] = current
    return {"inversions": inversions}


def same_timestamp_collisions(events: list[P2PEvent]) -> Counter[str]:
    counts = Counter(f"{event.case_id}|{event.timestamp}" for event in events)
    return Counter({key: count for key, count in counts.items() if count > 1})


def schema_version_counts(events: list[P2PEvent]) -> dict[str, int]:
    counts = Counter(str(event.attrs.get("schema_version") or "missing") for event in events)
    return dict(sorted(counts.items()))


def replay_digest(events: list[P2PEvent], policies: dict[str, CasePolicy]) -> str:
    payload = {
        "events": [event_row(event) for event in sorted(events, key=lambda item: (item.case_id, item.timestamp, item.event_id))],
        "policies": [
            policy_row(policy)
            for policy in sorted(policies.values(), key=lambda item: item.case_id)
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parallel_replay_cases(
    replayable_cases: dict[str, list[P2PEvent]],
    policies: dict[str, CasePolicy],
    *,
    workers: int,
) -> list[Any]:
    rows = sorted(replayable_cases.items())
    if not rows:
        return []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda row: replay_case(row[1], policies[row[0]]), rows))


def violation_signature(results: list[Any]) -> list[tuple[str, tuple[str, ...]]]:
    return [
        (result.case_id, tuple(violation.code for violation in result.violations))
        for result in sorted(results, key=lambda item: item.case_id)
    ]


def resume_cursor(events: list[P2PEvent]) -> dict[str, str] | None:
    if not events:
        return None
    event = max(events, key=lambda item: (item.case_id, item.timestamp, item.event_id))
    return {"case_id": event.case_id, "timestamp": event.timestamp, "event_id": event.event_id}


def readiness_status(
    *,
    parse_errors: list[dict[str, Any]],
    duplicate_event_id_count: int,
    input_order_inversions: int,
    missing_schema_version_count: int,
    parallel_matches_serial: bool,
) -> dict[str, Any]:
    findings: list[str] = []
    if parse_errors:
        findings.append("parse_errors")
    if duplicate_event_id_count:
        findings.append("duplicate_event_ids")
    if input_order_inversions:
        findings.append("input_order_inversions")
    if missing_schema_version_count:
        findings.append("missing_schema_version")
    if not parallel_matches_serial:
        findings.append("parallel_consistency_mismatch")
    status = "pass" if not findings else "review"
    if parse_errors:
        status = "fail"
    return {
        "status": status,
        "findings": findings,
        "production_note": (
            "This report checks replay hygiene. Production operation still needs "
            "persistent storage, queue semantics, auth, monitoring, rollback, and ERP-specific mapping review."
        ),
    }


def event_row(event: P2PEvent) -> dict[str, Any]:
    return {
        "case_id": event.case_id,
        "event_id": event.event_id,
        "timestamp": event.timestamp,
        "event_type": event.event_type,
        "po_id": event.po_id,
        "line_id": event.line_id,
        "vendor_id": event.vendor_id,
        "invoice_id": event.invoice_id,
        "amount": event.amount,
        "quantity": event.quantity,
        "actor": event.actor,
        "attrs": event.attrs,
    }


def policy_row(policy: CasePolicy) -> dict[str, Any]:
    return {
        "case_id": policy.case_id,
        "flow_type": policy.flow_type,
        "po_amount": policy.po_amount,
        "po_quantity": policy.po_quantity,
        "vendor_id": policy.vendor_id,
        "approval_limit": policy.approval_limit,
        "amount_tolerance": policy.amount_tolerance,
        "quantity_tolerance": policy.quantity_tolerance,
    }
