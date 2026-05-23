from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from p2p_replay_gate.cli import main
from p2p_replay_gate.io import read_json, write_json


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


if __name__ == "__main__":
    unittest.main()
