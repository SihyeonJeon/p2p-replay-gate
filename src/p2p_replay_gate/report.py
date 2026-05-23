from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .io import group_events
from .models import CasePolicy, CaseResult, P2PEvent, Scenario
from .oracle import replay_case, summarize_violations
from .scenario import materialize_scenario


def build_report(
    events: list[P2PEvent],
    policies: dict[str, CasePolicy],
    scenarios: list[Scenario],
) -> dict[str, Any]:
    grouped = group_events(events)
    clean_results = [
        replay_case(case_events, policies[case_id])
        for case_id, case_events in sorted(grouped.items())
    ]
    scenario_results = [
        _scenario_row(scenario, materialize_scenario(grouped, scenario), policies[scenario.case_id])
        for scenario in scenarios
    ]
    critical_expected = [
        row for row in scenario_results
        if any(code in {"DUPLICATE_INVOICE", "PAYMENT_BEFORE_GR", "PAYMENT_BEFORE_APPROVAL", "PAYMENT_WHILE_BLOCKED", "VENDOR_MISMATCH"} for code in row["expected_codes"])
    ]
    critical_caught = [
        row for row in critical_expected
        if set(row["expected_codes"]).issubset(set(row["actual_codes"]))
    ]
    duplicate_expected = [row for row in scenario_results if "DUPLICATE_INVOICE" in row["expected_codes"]]
    duplicate_caught = [row for row in duplicate_expected if "DUPLICATE_INVOICE" in row["actual_codes"]]
    false_holds = sum(result.clean_hold_count for result in clean_results)
    audit_complete = sum(1 for result in [*clean_results, *[row["case_result"] for row in scenario_results]] if result.audit_trace_complete)
    total_runs = len(clean_results) + len(scenario_results)
    all_scenario_case_results = [row["case_result"] for row in scenario_results]
    return {
        "run_id": "p2p-replay-gate-v0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evidence_grade": "synthetic_fixture",
        "claim_allowed": False,
        "scope_note": "synthetic purchase-to-pay replay fixture; no real payment or vendor data",
        "summary": {
            "clean_trace_count": len(clean_results),
            "scenario_count": len(scenario_results),
            "critical_policy_violations_caught": len(critical_caught),
            "critical_policy_violations_expected": len(critical_expected),
            "duplicate_catch_recall": _rate(len(duplicate_caught), len(duplicate_expected)),
            "false_holds_on_clean_traces": false_holds,
            "audit_trace_coverage": _rate(audit_complete, total_runs),
            "deterministic_replay": True,
            "exit_code": _exit_code(clean_results, scenario_results, false_holds),
        },
        "clean_violations": _results_to_rows(clean_results),
        "scenario_violations_by_code": summarize_violations(all_scenario_case_results),
        "scenario_results": [_public_scenario_row(row) for row in scenario_results],
        "failure_queue": _failure_queue(scenario_results),
        "metric_glossary": {
            "critical_policy_violations_caught": "seeded critical defects whose expected violation codes were emitted by the oracle",
            "duplicate_catch_recall": "duplicate invoice scenarios caught by duplicate invoice rule",
            "false_holds_on_clean_traces": "clean traces where the oracle found an unnecessary invoice hold",
            "audit_trace_coverage": "runs containing the events needed to audit the policy decision",
        },
    }


def build_audit_report(
    events: list[P2PEvent],
    policies: dict[str, CasePolicy],
) -> dict[str, Any]:
    grouped = group_events(events)
    results = [
        replay_case(case_events, policies[case_id])
        for case_id, case_events in sorted(grouped.items())
        if case_id in policies
    ]
    missing_policy = sorted(case_id for case_id in grouped if case_id not in policies)
    audit_complete = sum(1 for result in results if result.audit_trace_complete)
    total_cases = len(grouped)
    violation_cases = [result for result in results if result.violations]
    critical_cases = [result for result in results if result.has_critical]
    return {
        "run_id": "p2p-replay-gate-audit-v0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evidence_grade": "external_or_imported_event_log",
        "claim_allowed": False,
        "scope_note": "audit report over supplied purchase-to-pay traces and user-provided policy",
        "summary": {
            "trace_count": len(results),
            "missing_policy_count": len(missing_policy),
            "violation_case_count": len(violation_cases),
            "critical_case_count": len(critical_cases),
            "audit_trace_coverage": _rate(audit_complete, total_cases),
            "exit_code": _audit_exit_code(missing_policy, critical_cases, violation_cases),
        },
        "missing_policy": missing_policy,
        "violations_by_code": summarize_violations(results),
        "case_results": _results_to_rows(results),
        "failure_queue": _case_failure_queue(results),
        "metric_glossary": {
            "violation_case_count": "cases with at least one emitted policy violation",
            "critical_case_count": "cases with duplicate, vendor, early payment, or blocked-payment critical findings",
            "audit_trace_coverage": "cases containing the events needed to audit the policy decision",
        },
    }


def _scenario_row(scenario: Scenario, events: list[P2PEvent], policy: CasePolicy) -> dict[str, Any]:
    result = replay_case(events, policy)
    actual_codes = tuple(sorted({violation.code for violation in result.violations}))
    expected = tuple(sorted(scenario.expected_codes))
    return {
        "scenario_id": scenario.scenario_id,
        "case_id": scenario.case_id,
        "mutation": scenario.mutation,
        "description": scenario.description,
        "expected_codes": expected,
        "actual_codes": actual_codes,
        "passed": set(expected).issubset(set(actual_codes)),
        "case_result": result,
    }


def _public_scenario_row(row: dict[str, Any]) -> dict[str, Any]:
    result: CaseResult = row["case_result"]
    return {
        "scenario_id": row["scenario_id"],
        "case_id": row["case_id"],
        "mutation": row["mutation"],
        "description": row["description"],
        "expected_codes": list(row["expected_codes"]),
        "actual_codes": list(row["actual_codes"]),
        "passed": row["passed"],
        "audit_trace_complete": result.audit_trace_complete,
        "violations": [_violation_row(violation) for violation in result.violations],
    }


def _results_to_rows(results: list[CaseResult]) -> list[dict[str, Any]]:
    return [
        {
            "case_id": result.case_id,
            "event_count": result.event_count,
            "audit_trace_complete": result.audit_trace_complete,
            "violations": [_violation_row(violation) for violation in result.violations],
        }
        for result in results
        if result.violations
    ]


def _failure_queue(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    queue = []
    for row in rows:
        result: CaseResult = row["case_result"]
        for violation in result.violations:
            if violation.severity in {"critical", "high"}:
                queue.append({
                    "scenario_id": row["scenario_id"],
                    "case_id": row["case_id"],
                    "mutation": row["mutation"],
                    **_violation_row(violation),
                })
    return sorted(queue, key=lambda item: (item["severity"] != "critical", item["scenario_id"], item["code"]))


def _case_failure_queue(results: list[CaseResult]) -> list[dict[str, Any]]:
    queue = []
    for result in results:
        for violation in result.violations:
            if violation.severity in {"critical", "high"}:
                queue.append({
                    "case_id": result.case_id,
                    **_violation_row(violation),
                })
    return sorted(queue, key=lambda item: (item["severity"] != "critical", item["case_id"], item["code"]))


def _violation_row(violation) -> dict[str, Any]:
    return {
        "code": violation.code,
        "severity": violation.severity,
        "event_id": violation.event_id,
        "message": violation.message,
        "evidence": violation.evidence,
    }


def _exit_code(clean_results: list[CaseResult], scenario_results: list[dict[str, Any]], false_holds: int) -> int:
    if any(result.has_critical for result in clean_results):
        return 3
    if false_holds:
        return 2
    if any(not row["passed"] for row in scenario_results):
        return 1
    return 0


def _audit_exit_code(missing_policy: list[str], critical_cases: list[CaseResult], violation_cases: list[CaseResult]) -> int:
    if critical_cases:
        return 3
    if missing_policy:
        return 4
    if violation_cases:
        return 1
    return 0


def _rate(numerator: int, denominator: int) -> float:
    return numerator / max(denominator, 1)
