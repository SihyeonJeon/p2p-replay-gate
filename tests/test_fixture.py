from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from p2p_replay_gate.fixture import write_fixture
from p2p_replay_gate.io import group_events, load_events, load_policy, load_scenarios
from p2p_replay_gate.scenario import validate_scenario_expectations


class FixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_fixture(self.root)
        self.events_path = self.root / "data" / "scenarios" / "base_events.jsonl"
        self.policy_path = self.root / "data" / "scenarios" / "policy.json"
        self.scenario_path = self.root / "data" / "scenarios" / "injected_scenarios.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_fixture_counts(self) -> None:
        self.assertEqual(12, len(load_policy(self.policy_path)))
        self.assertEqual(48, len(load_scenarios(self.scenario_path)))

    def test_scenario_ids_are_unique(self) -> None:
        scenarios = load_scenarios(self.scenario_path)
        self.assertEqual(len(scenarios), len({scenario.scenario_id for scenario in scenarios}))

    def test_event_ids_are_unique(self) -> None:
        events = load_events(self.events_path)
        self.assertEqual(len(events), len({event.event_id for event in events}))

    def test_every_scenario_has_policy(self) -> None:
        policies = load_policy(self.policy_path)
        for scenario in load_scenarios(self.scenario_path):
            self.assertIn(scenario.case_id, policies)

    def test_scenario_expectations_validate(self) -> None:
        events = group_events(load_events(self.events_path))
        policies = load_policy(self.policy_path)
        scenarios = load_scenarios(self.scenario_path)
        self.assertEqual([], validate_scenario_expectations(events, policies, scenarios))


if __name__ == "__main__":
    unittest.main()
