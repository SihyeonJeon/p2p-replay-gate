from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from p2p_replay_gate.cli import main
from p2p_replay_gate.io import load_events, read_json, write_json


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.scenario_dir = self.root / "data" / "scenarios"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _main(self, args: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def _write_fixture(self) -> None:
        code, _, _ = self._main(["fixture", "--root", str(self.root)])
        self.assertEqual(0, code)

    def test_fixture_command_writes_files(self) -> None:
        self._write_fixture()
        self.assertTrue((self.scenario_dir / "base_events.jsonl").exists())
        self.assertTrue((self.scenario_dir / "policy.json").exists())
        self.assertTrue((self.scenario_dir / "injected_scenarios.json").exists())

    def test_validate_command_ok(self) -> None:
        self._write_fixture()
        code, _, _ = self._main([
            "validate",
            "--events",
            str(self.scenario_dir / "base_events.jsonl"),
            "--scenario-pack",
            str(self.scenario_dir / "injected_scenarios.json"),
        ])
        self.assertEqual(0, code)

    def test_run_command_writes_report(self) -> None:
        self._write_fixture()
        output = self.root / "reports" / "scorecard.json"
        code, _, _ = self._main([
            "run",
            "--events",
            str(self.scenario_dir / "base_events.jsonl"),
            "--scenario-pack",
            str(self.scenario_dir / "injected_scenarios.json"),
            "--output",
            str(output),
        ])
        self.assertEqual(0, code)
        self.assertEqual(48, read_json(output)["summary"]["scenario_count"])

    def test_inspect_command_prints_failure_queue(self) -> None:
        self._write_fixture()
        output = self.root / "reports" / "scorecard.json"
        code, _, _ = self._main([
            "run",
            "--events",
            str(self.scenario_dir / "base_events.jsonl"),
            "--scenario-pack",
            str(self.scenario_dir / "injected_scenarios.json"),
            "--output",
            str(output),
        ])
        self.assertEqual(0, code)
        code, stdout, _ = self._main(["inspect", "--report", str(output), "--top", "2"])
        self.assertEqual(0, code)
        self.assertIn("failure queue", stdout)

    def test_validate_fails_on_bad_expected_code(self) -> None:
        self._write_fixture()
        scenario_path = self.scenario_dir / "injected_scenarios.json"
        rows = read_json(scenario_path)
        rows[0]["expected_codes"] = ["PAYMENT_WHILE_BLOCKED"]
        write_json(scenario_path, rows)
        code, _, _ = self._main([
            "validate",
            "--events",
            str(self.scenario_dir / "base_events.jsonl"),
            "--scenario-pack",
            str(scenario_path),
        ])
        self.assertEqual(1, code)

    def test_import_csv_and_policy_template_commands(self) -> None:
        csv_path = self.root / "events.csv"
        jsonl_path = self.root / "events.jsonl"
        policy_path = self.root / "policy.json"
        csv_path.write_text(
            "case_id,event_type,timestamp,po_id,vendor_id,invoice_id,amount,quantity\n"
            "C900,po_created,2026-01-01T09:00:00Z,PO-C900,V90,,500,2\n"
            "C900,invoice_received,2026-01-02T09:00:00Z,PO-C900,V90,INV-C900,500,2\n",
            encoding="utf-8",
        )
        code, _, _ = self._main(["import-csv", "--input", str(csv_path), "--output", str(jsonl_path), "--strict"])
        self.assertEqual(0, code)
        code, _, _ = self._main(["policy-template", "--events", str(jsonl_path), "--output", str(policy_path), "--flow-type", "two_way"])
        self.assertEqual(0, code)
        self.assertEqual("two_way", read_json(policy_path)[0]["flow_type"])

        audit_path = self.root / "audit.json"
        code, _, _ = self._main(["audit", "--events", str(jsonl_path), "--policy", str(policy_path), "--output", str(audit_path)])
        self.assertEqual(0, code)
        self.assertEqual(1, read_json(audit_path)["summary"]["trace_count"])

    def test_import_xes_command(self) -> None:
        xes_path = self.root / "events.xes"
        jsonl_path = self.root / "events.jsonl"
        xes_path.write_text(
            '<?xml version="1.0" encoding="UTF-8" ?><log xmlns="http://www.xes-standard.org/">'
            '<trace><string key="concept:name" value="C901"/>'
            '<string key="Purchasing Document" value="PO-C901"/><string key="Vendor" value="V91"/>'
            '<float key="Cumulative net worth (EUR)" value="300"/><float key="Quantity" value="1"/>'
            '<event><string key="concept:name" value="Create Purchase Order"/>'
            '<date key="time:timestamp" value="2026-01-01T09:00:00Z"/></event></trace></log>',
            encoding="utf-8",
        )
        code, _, _ = self._main(["import-xes", "--input", str(xes_path), "--output", str(jsonl_path), "--strict"])
        self.assertEqual(0, code)
        self.assertEqual("po_created", load_events(jsonl_path)[0].event_type)

    def test_gate_action_command_blocks_unsafe_payment(self) -> None:
        self._write_fixture()
        output = self.root / "reports" / "agent_gate.json"
        code, stdout, _ = self._main([
            "gate-action",
            "--events",
            str(self.scenario_dir / "base_events.jsonl"),
            "--policy",
            str(self.scenario_dir / "policy.json"),
            "--action",
            "clear_payment",
            "--case-id",
            "C004",
            "--output",
            str(output),
        ])
        self.assertEqual(3, code)
        self.assertIn("decision: block", stdout)
        report = read_json(output)
        self.assertEqual("block", report["decision"])
        self.assertIn("PAYMENT_BEFORE_APPROVAL", report["blocking_codes"])

    def test_ops_report_command_writes_operational_checks(self) -> None:
        self._write_fixture()
        output = self.root / "reports" / "ops.json"
        code, stdout, _ = self._main([
            "ops-report",
            "--events",
            str(self.scenario_dir / "base_events.jsonl"),
            "--policy",
            str(self.scenario_dir / "policy.json"),
            "--output",
            str(output),
            "--iterations",
            "2",
        ])
        self.assertEqual(0, code)
        self.assertIn("ops report written", stdout)
        report = read_json(output)
        self.assertEqual("p2p-replay-gate-ops-readiness-v0", report["run_id"])
        self.assertEqual(12, report["input"]["replayable_cases"])
        self.assertIn("replay_digest", report["replay"])


if __name__ == "__main__":
    unittest.main()
