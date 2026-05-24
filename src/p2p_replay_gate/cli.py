from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .adapters import import_csv_events, import_xes_events, normalize_activity_map, write_policy_template
from .fixture import write_fixture
from .io import group_events, load_events, load_policy, load_scenarios, read_json, write_json
from .oracle import replay_case
from .packs import load_activity_map, load_manifest
from .report import build_audit_report, build_report
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

    import_parser = subparsers.add_parser("import-csv", help="convert a CSV event log into P2P JSONL")
    import_parser.add_argument("--input", type=Path, required=True, help="source event log CSV")
    import_parser.add_argument("--output", type=Path, required=True, help="output JSONL path")
    import_parser.add_argument("--activity-map", type=Path, default=None, help="optional JSON map from source activity to P2P event_type")
    import_parser.add_argument("--pack", default=None, help="bundled mapping pack name, for example bpic2019")
    import_parser.add_argument("--report", type=Path, default=None, help="optional adapter report JSON path")
    import_parser.add_argument("--strict", action="store_true", help="fail when any source activity is unmapped")
    import_parser.set_defaults(func=_import_csv)

    xes_parser = subparsers.add_parser("import-xes", help="convert an XES event log into P2P JSONL")
    xes_parser.add_argument("--input", type=Path, required=True, help="source event log .xes or .xes.gz")
    xes_parser.add_argument("--output", type=Path, required=True, help="output JSONL path")
    xes_parser.add_argument("--activity-map", type=Path, default=None, help="optional JSON map from source activity to P2P event_type")
    xes_parser.add_argument("--pack", default=None, help="bundled mapping pack name, for example bpic2019")
    xes_parser.add_argument("--max-cases", type=int, default=None, help="optional first-N-case smoke limit for large XES logs")
    xes_parser.add_argument("--report", type=Path, default=None, help="optional adapter report JSON path")
    xes_parser.add_argument("--strict", action="store_true", help="fail when any source activity is unmapped")
    xes_parser.set_defaults(func=_import_xes)

    policy_parser = subparsers.add_parser("policy-template", help="write a policy skeleton for imported traces")
    policy_parser.add_argument("--events", type=Path, required=True, help="imported event log JSONL")
    policy_parser.add_argument("--output", type=Path, required=True, help="policy JSON output path")
    policy_parser.add_argument("--flow-type", default="three_way_gr_based", help="default flow type for every case")
    policy_parser.add_argument("--approval-limit", type=float, default=1000.0, help="approval threshold for generated policies")
    policy_parser.add_argument("--allow-missing-amount", action="store_true", help="emit po_amount=0.0 with template warnings when amount data is missing")
    policy_parser.set_defaults(func=_policy_template)

    audit_parser = subparsers.add_parser("audit", help="audit supplied traces against a policy file")
    audit_parser.add_argument("--events", type=Path, required=True, help="event log JSONL")
    audit_parser.add_argument("--policy", type=Path, required=True, help="case policy JSON")
    audit_parser.add_argument("--output", type=Path, default=Path("reports/audit.json"), help="audit report output path")
    audit_parser.set_defaults(func=_audit)

    pack_parser = subparsers.add_parser("pack-info", help="print bundled mapping pack metadata")
    pack_parser.add_argument("pack", help="mapping pack name, for example bpic2019")
    pack_parser.set_defaults(func=_pack_info)

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


def _import_csv(args: argparse.Namespace) -> int:
    stats = import_csv_events(
        args.input,
        args.output,
        activity_map_path=args.activity_map,
        activity_map=_activity_map(args),
        report_path=args.report,
        strict=args.strict,
    )
    print(
        f"imported: rows={stats.rows_read} "
        f"events={stats.events_written} "
        f"skipped={stats.skipped_rows} "
        f"output={args.output}"
    )
    if stats.unmapped_activities:
        print("unmapped activities:")
        for activity, count in sorted(stats.unmapped_activities.items()):
            print(f"- {activity}: {count}")
    if stats.row_errors:
        print("row errors:")
        for error, count in sorted(stats.row_errors.items()):
            print(f"- {error}: {count}")
    return 0


def _import_xes(args: argparse.Namespace) -> int:
    stats = import_xes_events(
        args.input,
        args.output,
        activity_map_path=args.activity_map,
        activity_map=_activity_map(args),
        max_cases=args.max_cases,
        report_path=args.report,
        strict=args.strict,
    )
    print(
        f"imported: rows={stats.rows_read} "
        f"events={stats.events_written} "
        f"skipped={stats.skipped_rows} "
        f"output={args.output}"
    )
    if stats.unmapped_activities:
        print("unmapped activities:")
        for activity, count in sorted(stats.unmapped_activities.items()):
            print(f"- {activity}: {count}")
    if stats.row_errors:
        print("row errors:")
        for error, count in sorted(stats.row_errors.items()):
            print(f"- {error}: {count}")
    return 0


def _policy_template(args: argparse.Namespace) -> int:
    events = load_events(args.events)
    policies = write_policy_template(
        events,
        args.output,
        flow_type=args.flow_type,
        approval_limit=args.approval_limit,
        allow_missing_amount=args.allow_missing_amount,
    )
    print(f"policy template written: {args.output} ({len(policies)} cases)")
    return 0


def _audit(args: argparse.Namespace) -> int:
    report = build_audit_report(load_events(args.events), load_policy(args.policy))
    write_json(args.output, report)
    summary = report["summary"]
    print(f"audit written: {args.output}")
    print(
        "summary: "
        f"traces={summary['trace_count']} "
        f"violations={summary['violation_case_count']} "
        f"critical={summary['critical_case_count']} "
        f"missing_policy={summary['missing_policy_count']}"
    )
    return int(summary["exit_code"])


def _pack_info(args: argparse.Namespace) -> int:
    print(json.dumps(load_manifest(args.pack), indent=2, sort_keys=True))
    return 0


def _activity_map(args: argparse.Namespace) -> dict[str, str] | None:
    mapping: dict[str, str] = {}
    if args.pack:
        mapping.update(load_activity_map(args.pack))
    if args.activity_map:
        data = read_json(args.activity_map)
        if not isinstance(data, dict):
            raise ValueError(f"{args.activity_map}: activity map must be a JSON object")
        mapping.update(normalize_activity_map(data, str(args.activity_map)))
    return mapping or None


def _load_inputs(args: argparse.Namespace):
    policy_path = args.policy or args.events.parent / "policy.json"
    events = load_events(args.events)
    policies = load_policy(policy_path)
    scenarios = load_scenarios(args.scenario_pack)
    return events, policies, scenarios


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
