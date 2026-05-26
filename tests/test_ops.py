from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from p2p_replay_gate.fixture import write_fixture
from p2p_replay_gate.io import load_policy
from p2p_replay_gate.ops import build_ops_report


class OpsReportTests(unittest.TestCase):
    def test_ops_report_on_fixture_has_replay_digest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_fixture(root)
            report = build_ops_report(
                root / "data" / "scenarios" / "base_events.jsonl",
                load_policy(root / "data" / "scenarios" / "policy.json"),
                iterations=2,
            )
        self.assertEqual("pass", report["readiness"]["status"])
        self.assertEqual(12, report["input"]["replayable_cases"])
        self.assertGreater(report["replay"]["events_per_second"], 0)
        self.assertEqual(64, len(report["replay"]["replay_digest"]))
        self.assertEqual(0, report["idempotency"]["duplicate_event_id_count"])
        self.assertEqual({"p2p.replay_event.v1": 51}, report["schema"]["schema_version_counts"])
        self.assertTrue(report["consistency"]["parallel_matches_serial"])
        self.assertEqual("case_id", report["consistency"]["case_partition_key"])

    def test_ops_report_flags_duplicate_and_ordering_inversion(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events = root / "events.jsonl"
            policy = root / "policy.json"
            policy.write_text(
                '[{"case_id":"C1","flow_type":"two_way","po_amount":100.0,'
                '"po_quantity":1.0,"vendor_id":"V1"}]',
                encoding="utf-8",
            )
            events.write_text(
                '{"case_id":"C1","event_id":"e2","timestamp":"2026-01-02T00:00:00Z",'
                '"event_type":"invoice_received","po_id":"PO1","line_id":"10","vendor_id":"V1",'
                '"invoice_id":"INV1","amount":100,"quantity":1}\n'
                '{"case_id":"C1","event_id":"e1","timestamp":"2026-01-01T00:00:00Z",'
                '"event_type":"po_created","po_id":"PO1","line_id":"10","vendor_id":"V1",'
                '"amount":100,"quantity":1,"attrs":{"schema_version":"p2p.replay_event.v1"}}\n'
                '{"case_id":"C1","event_id":"e1","timestamp":"2026-01-01T00:00:00Z",'
                '"event_type":"po_created","po_id":"PO1","line_id":"10","vendor_id":"V1",'
                '"amount":100,"quantity":1,"attrs":{"schema_version":"p2p.replay_event.v1"}}\n',
                encoding="utf-8",
            )
            report = build_ops_report(events, load_policy(policy))
        self.assertEqual("review", report["readiness"]["status"])
        self.assertEqual(1, report["idempotency"]["duplicate_event_id_count"])
        self.assertEqual(2, report["input"]["deduped_events"])
        self.assertEqual(1, report["ordering"]["input_order_inversions"])


if __name__ == "__main__":
    unittest.main()
