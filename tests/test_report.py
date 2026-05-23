from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from p2p_replay_gate.fixture import write_fixture
from p2p_replay_gate.io import load_events, load_policy, load_scenarios
from p2p_replay_gate.report import build_report


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


if __name__ == "__main__":
    unittest.main()
