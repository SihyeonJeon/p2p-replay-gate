from __future__ import annotations

import unittest

from p2p_replay_gate.fixture import _base_events, _policies, _scenario
from p2p_replay_gate.models import CasePolicy, P2PEvent
from p2p_replay_gate.oracle import replay_case


def _case(index: int):
    policies = _policies()
    events = _base_events(policies)
    by_case = {}
    for event in events:
        by_case.setdefault(event["case_id"], []).append(event)
    return policies[index], by_case[policies[index]["case_id"]]


def _result(index: int, mutation: str):
    policy_row, base_rows = _case(index)
    scenario = _scenario(policy_row, base_rows, mutation)
    rows = scenario["events"] if scenario["replace_base"] else [*base_rows, *scenario["events"]]
    return replay_case(
        [P2PEvent.from_dict(row) for row in rows],
        CasePolicy.from_dict(policy_row),
    )


def _codes(result) -> set[str]:
    return {violation.code for violation in result.violations}


class OracleTests(unittest.TestCase):
    def test_clean_three_way_has_no_violation(self) -> None:
        policy_row, rows = _case(0)
        result = replay_case([P2PEvent.from_dict(row) for row in rows], CasePolicy.from_dict(policy_row))
        self.assertEqual([], list(result.violations))

    def test_duplicate_invoice(self) -> None:
        self.assertIn("DUPLICATE_INVOICE", _codes(_result(0, "duplicate_invoice")))

    def test_vendor_mismatch(self) -> None:
        self.assertIn("VENDOR_MISMATCH", _codes(_result(0, "vendor_mismatch")))

    def test_amount_overbill(self) -> None:
        self.assertIn("VALUE_MISMATCH", _codes(_result(0, "amount_overbill")))

    def test_quantity_mismatch(self) -> None:
        self.assertIn("QUANTITY_MISMATCH", _codes(_result(1, "quantity_mismatch")))

    def test_payment_before_goods_receipt(self) -> None:
        self.assertIn("PAYMENT_BEFORE_GR", _codes(_result(0, "payment_before_gr")))

    def test_payment_before_approval(self) -> None:
        self.assertIn("PAYMENT_BEFORE_APPROVAL", _codes(_result(1, "payment_before_approval")))

    def test_payment_while_blocked(self) -> None:
        self.assertIn("PAYMENT_WHILE_BLOCKED", _codes(_result(2, "payment_while_blocked")))

    def test_consignment_invoice(self) -> None:
        self.assertIn("CONSIGNMENT_INVOICE", _codes(_result(3, "consignment_invoice")))

    def test_consignment_duplicate_invoice(self) -> None:
        codes = _codes(_result(3, "consignment_duplicate_invoice"))
        self.assertIn("CONSIGNMENT_INVOICE", codes)
        self.assertIn("DUPLICATE_INVOICE", codes)

    def test_missing_required_goods_receipt(self) -> None:
        self.assertIn("MISSING_REQUIRED_GR", _codes(_result(0, "missing_required_gr")))

    def test_unreleased_block(self) -> None:
        self.assertIn("UNRELEASED_BLOCK", _codes(_result(0, "unreleased_block")))

    def test_reversal_after_payment(self) -> None:
        self.assertIn("REVERSAL_AFTER_PAYMENT", _codes(_result(0, "reversal_after_payment")))

    def test_unneeded_hold(self) -> None:
        self.assertIn("UNNEEDED_HOLD", _codes(_result(0, "unneeded_hold")))

    def test_consignment_audit_trace_is_complete_without_invoice(self) -> None:
        policy_row, rows = _case(3)
        result = replay_case([P2PEvent.from_dict(row) for row in rows], CasePolicy.from_dict(policy_row))
        self.assertTrue(result.audit_trace_complete)


if __name__ == "__main__":
    unittest.main()
