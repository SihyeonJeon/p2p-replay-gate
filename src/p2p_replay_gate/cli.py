from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .fixture import write_fixture
from .io import group_events, load_events, load_policy, load_scenarios, read_json, write_json
from .oracle import replay_case
from .report import build_report
from .scenario import validate_scenario_expectations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="p2p-replay-gate",
        description="Replay purchase-to-pay traces against a policy oracle.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fixture_parser = subparsers.add_parser("fixture", help="write the bundled synthetic fixture")
    fixture_parser.add_argument("--root", type=Path, default=Path("."), help="project root to write fixture files into")
    fixture_parser.set_defaults(func=_fixture)

    validate_parser = subparsers.add_parser("validate", help="validate traces, policy, and seeded scenarios")
    _add_input_args(validate_parser)
    validate_parser.set_defaults(func=_validate)

    run_parser = subparsers.add_parser("run", help="write a JSON scorecard")
    _add_input_args(run_parser)
    run_parser.add_argument("--output", type=Path, default=Path("reports/scorecard.json"), help="scorecard output path")
    run_parser.set_defaults(func=_run)

    inspect_parser = subparsers.add_parser("inspect", help="print a compact scorecard summary")
    inspect_parser.add_argument("--report", type=Path, required=True, help="scorecard JSON path")
    inspect_parser.add_argument("--top", type=int, default=8, help="number of failure queue rows to print")
    inspect_parser.set_defaults(func=_inspect)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:  # pragma: no cover - CLI guardrail
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _add_input_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--events", type=Path, required=True, help="base event log JSONL")
    parser.add_argument("--policy", type=Path, default=None, help="case policy JSON; defaults to policy.json next to events")
    parser.add_argument("--scenario-pack", type=Path, required=True, help="seeded scenario JSON")


def _fixture(args: argparse.Namespace) -> int:
    write_fixture(args.root)
    print(f"fixture written: {args.root / 'data' / 'scenarios'}")
    return 0


def _validate(args: argparse.Namespace) -> int:
    events, policies, scenarios = _load_inputs(args)
    grouped = group_events(events)
    errors: list[str] = []

    for case_id in sorted(grouped):
        if case_id not in policies:
            errors.append(f"{case_id}: missing policy")
            continue
        result = replay_case(grouped[case_id], policies[case_id])
        if result.violations:
            codes = ", ".join(violation.code for violation in result.violations)
            errors.append(f"{case_id}: clean trace emitted violations: {codes}")

    for scenario in scenarios:
        if scenario.case_id not in grouped:
            errors.append(f"{scenario.scenario_id}: unknown case_id {scenario.case_id}")
        if scenario.case_id not in policies:
            errors.append(f"{scenario.scenario_id}: missing policy for case_id {scenario.case_id}")

    if not errors:
        errors.extend(validate_scenario_expectations(grouped, policies, scenarios))

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print(f"validated: {len(grouped)} clean traces, {len(scenarios)} injected scenarios")
    return 0


def _run(args: argparse.Namespace) -> int:
    events, policies, scenarios = _load_inputs(args)
    report = build_report(events, policies, scenarios)
    write_json(args.output, report)
    summary = report["summary"]
    print(f"scorecard written: {args.output}")
    print(
        "summary: "
        f"clean={summary['clean_trace_count']} "
        f"scenarios={summary['scenario_count']} "
        f"critical={summary['critical_policy_violations_caught']}/{summary['critical_policy_violations_expected']} "
        f"duplicate_recall={summary['duplicate_catch_recall']:.3f} "
        f"false_holds={summary['false_holds_on_clean_traces']}"
    )
    return int(summary["exit_code"])


def _inspect(args: argparse.Namespace) -> int:
    report = read_json(args.report)
    summary: dict[str, Any] = report["summary"]
    print(f"run: {report.get('run_id', 'unknown')}")
    print(f"scope: {report.get('scope_note', '')}")
    print(f"clean traces: {summary['clean_trace_count']}")
    print(f"injected scenarios: {summary['scenario_count']}")
    print(f"critical caught: {summary['critical_policy_violations_caught']}/{summary['critical_policy_violations_expected']}")
    print(f"duplicate recall: {summary['duplicate_catch_recall']:.3f}")
    print(f"false holds: {summary['false_holds_on_clean_traces']}")
    print(f"audit trace coverage: {summary['audit_trace_coverage']:.3f}")
    queue = report.get("failure_queue", [])[: max(0, args.top)]
    if queue:
        print("\nfailure queue")
        for row in queue:
            print(f"- {row['severity']:8} {row['case_id']} {row['mutation']}: {row['code']}")
    return 0


def _load_inputs(args: argparse.Namespace):
    policy_path = args.policy or args.events.parent / "policy.json"
    events = load_events(args.events)
    policies = load_policy(policy_path)
    scenarios = load_scenarios(args.scenario_pack)
    return events, policies, scenarios


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
