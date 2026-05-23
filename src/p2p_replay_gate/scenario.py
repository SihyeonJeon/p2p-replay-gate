from __future__ import annotations

from .models import CasePolicy, P2PEvent, Scenario
from .oracle import replay_case


def materialize_scenario(base_events: dict[str, list[P2PEvent]], scenario: Scenario) -> list[P2PEvent]:
    events = list(base_events.get(scenario.case_id, []))
    if not events:
        raise ValueError(f"scenario {scenario.scenario_id} references unknown case_id: {scenario.case_id}")
    if scenario.replace_base:
        return sorted(list(scenario.events), key=lambda event: (event.timestamp, event.event_id))
    return sorted([*events, *scenario.events], key=lambda event: (event.timestamp, event.event_id))


def validate_scenario_expectations(
    base_events: dict[str, list[P2PEvent]],
    policies: dict[str, CasePolicy],
    scenarios: list[Scenario],
) -> list[str]:
    errors: list[str] = []
    for scenario in scenarios:
        events = materialize_scenario(base_events, scenario)
        result = replay_case(events, policies[scenario.case_id])
        actual = {violation.code for violation in result.violations}
        expected = set(scenario.expected_codes)
        missing = sorted(expected - actual)
        if missing:
            errors.append(f"{scenario.scenario_id}: missing expected violations {missing}")
    return errors
