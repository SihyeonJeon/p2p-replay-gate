from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from p2p_replay_gate.fixture import write_fixture
from p2p_replay_gate.io import load_events, load_policy, load_scenarios
from p2p_replay_gate.models import CasePolicy, P2PEvent
from p2p_replay_gate.report import build_audit_report, build_report


def _report():
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    write_fixture(root)
    report = build_report(
        load_events(root / "data" / "scenarios" / "base_events.jsonl"),
        load_policy(root / "data" / "scenarios" / "policy.json"),
        load_scenarios(root / "data" / "scenarios" / "injected_scenarios.json"),
    )
    tmp.cleanup()
    return report


class ReportTests(unittest.TestCase):
    def test_summary_counts(self) -> None:
        summary = _report()["summary"]
        self.assertEqual(12, summary["clean_trace_count"])
        self.assertEqual(48, summary["scenario_count"])

    def test_critical_policy_violations_are_caught(self) -> None:
        summary = _report()["summary"]
        self.assertEqual(36, summary["critical_policy_violations_expected"])
        self.assertEqual(36, summary["critical_policy_violations_caught"])

    def test_duplicate_recall_is_complete(self) -> None:
        self.assertEqual(1.0, _report()["summary"]["duplicate_catch_recall"])

    def test_clean_violations_are_empty(self) -> None:
        self.assertEqual([], _report()["clean_violations"])

    def test_failure_queue_prioritizes_critical_rows(self) -> None:
        queue = _report()["failure_queue"]
        self.assertGreater(len(queue), 0)
        self.assertEqual("critical", queue[0]["severity"])

    def test_metric_glossary_is_present(self) -> None:
        glossary = _report()["metric_glossary"]
        self.assertIn("duplicate_catch_recall", glossary)
        self.assertIn("false_holds_on_clean_traces", glossary)

    def test_scenario_results_all_pass(self) -> None:
        self.assertTrue(all(row["passed"] for row in _report()["scenario_results"]))

    def test_exit_code_is_zero(self) -> None:
        self.assertEqual(0, _report()["summary"]["exit_code"])

    def test_audit_report_over_clean_fixture(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        write_fixture(root)
        report = build_audit_report(
            load_events(root / "data" / "scenarios" / "base_events.jsonl"),
            load_policy(root / "data" / "scenarios" / "policy.json"),
        )
        tmp.cleanup()
        self.assertEqual(12, report["summary"]["trace_count"])
        self.assertEqual(0, report["summary"]["critical_case_count"])
        self.assertEqual(0, report["summary"]["exit_code"])

    def test_audit_critical_exit_code_outranks_missing_policy(self) -> None:
        events = [
            P2PEvent("C001", "e1", "2026-01-01T09:00:00Z", "po_created", "PO-C001", "10", "V01", amount=1000.0, quantity=1.0),
            P2PEvent("C001", "e2", "2026-01-02T09:00:00Z", "payment_cleared", "PO-C001", "10", "V01", amount=1000.0, quantity=1.0),
            P2PEvent("C002", "e3", "2026-01-01T09:00:00Z", "po_created", "PO-C002", "10", "V02", amount=100.0, quantity=1.0),
        ]
        policies = {
            "C001": CasePolicy("C001", "three_way_gr_based", 1000.0, 1.0, "V01", approval_limit=500.0),
        }
        report = build_audit_report(events, policies)
        self.assertEqual(1, report["summary"]["missing_policy_count"])
        self.assertEqual(1, report["summary"]["critical_case_count"])
        self.assertEqual(3, report["summary"]["exit_code"])

    def test_audit_coverage_counts_missing_policy_cases(self) -> None:
        events = [
            P2PEvent("C001", "e1", "2026-01-01T09:00:00Z", "po_created", "PO-C001", "10", "V01", amount=100.0, quantity=1.0),
            P2PEvent("C001", "e2", "2026-01-02T09:00:00Z", "invoice_received", "PO-C001", "10", "V01", invoice_id="INV-C001", amount=100.0, quantity=1.0),
            P2PEvent("C002", "e3", "2026-01-01T09:00:00Z", "po_created", "PO-C002", "10", "V02", amount=100.0, quantity=1.0),
        ]
        policies = {
            "C001": CasePolicy("C001", "two_way", 100.0, 1.0, "V01"),
        }
        report = build_audit_report(events, policies)
        self.assertEqual(0.5, report["summary"]["audit_trace_coverage"])


if __name__ == "__main__":
    unittest.main()
