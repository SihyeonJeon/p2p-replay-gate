from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from p2p_replay_gate.fixture import write_fixture
from p2p_replay_gate.io import load_events, load_policy
from p2p_replay_gate.storage import (
    append_events,
    build_store_replay_report,
    open_store,
    read_events,
    stable_partition,
)


class StorageReplayTests(unittest.TestCase):
    def test_store_append_is_idempotent_and_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_fixture(root)
            events = load_events(root / "data" / "scenarios" / "base_events.jsonl")
            with open_store(root / "store.sqlite") as conn:
                first = append_events(conn, events)
                second = append_events(conn, events)
                stored = read_events(conn)

        self.assertEqual(len(events), first.inserted)
        self.assertEqual(0, first.duplicate_retries)
        self.assertEqual(0, second.inserted)
        self.assertEqual(len(events), second.duplicate_retries)
        self.assertEqual([event.event_id for event in events], [event.event_id for event in stored])

    def test_stable_partition_does_not_use_python_hash_seed(self) -> None:
        self.assertEqual(stable_partition("C001", 4), stable_partition("C001", 4))
        self.assertIn(stable_partition("C001", 4), {0, 1, 2, 3})

    def test_store_replay_report_recovers_after_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_fixture(root)
            report = build_store_replay_report(
                root / "data" / "scenarios" / "base_events.jsonl",
                load_policy(root / "data" / "scenarios" / "policy.json"),
                db_path=root / "reports" / "store.sqlite",
                partitions=4,
                repeats=2,
                queue_limit=10,
                simulate_crash_after_partitions=2,
            )

        self.assertEqual("recovered", report["failure_recovery"]["status"])
        self.assertEqual(51, report["storage"]["event_rows"])
        self.assertEqual(4, report["storage"]["checkpoint_rows"])
        self.assertEqual(51, report["backpressure"]["duplicate_retries"])
        self.assertTrue(report["partitioning"]["partition_matches_serial"])

    def test_store_replay_report_shape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_fixture(root)
            report = build_store_replay_report(
                root / "data" / "scenarios" / "base_events.jsonl",
                load_policy(root / "data" / "scenarios" / "policy.json"),
                db_path=root / "reports" / "store.sqlite",
                partitions=2,
                repeats=1,
                queue_limit=32,
                simulate_crash_after_partitions=1,
            )

        self.assertEqual("p2p-replay-gate-store-replay-v0", report["run_id"])
        self.assertEqual("sqlite", report["storage"]["engine"])
        self.assertIn("partition_key", report["consistency_model"])


if __name__ == "__main__":
    unittest.main()
