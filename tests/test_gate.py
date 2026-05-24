from __future__ import annotations

import unittest

from p2p_replay_gate.gate import AgentAction, evaluate_agent_action
from p2p_replay_gate.models import CasePolicy, P2PEvent


def _event(case_id: str, event_id: str, event_type: str, amount: float = 500.0, invoice_id: str | None = None) -> P2PEvent:
    return P2PEvent(
        case_id=case_id,
        event_id=event_id,
        timestamp=f"2026-01-01T09:00:0{event_id[-1]}Z",
        event_type=event_type,
        po_id=f"PO-{case_id}",
        line_id="10",
        vendor_id="V01",
        invoice_id=invoice_id,
        amount=amount,
        quantity=1.0,
    )


class AgentActionGateTests(unittest.TestCase):
    def test_allows_clean_two_way_payment(self) -> None:
        events = [
            _event("C100", "e1", "po_created"),
            _event("C100", "e2", "invoice_received", invoice_id="INV-C100"),
        ]
        policies = {"C100": CasePolicy("C100", "two_way", 500.0, 1.0, "V01", approval_limit=1000.0)}
        decision = evaluate_agent_action(events, policies, AgentAction("clear_payment", "C100", invoice_id="INV-C100"))
        self.assertEqual("allow", decision["decision"])
        self.assertEqual(0, decision["exit_code"])
        self.assertEqual([], decision["blocking_codes"])

    def test_blocks_payment_before_receipt_and_approval(self) -> None:
        events = [
            _event("C101", "e1", "po_created", amount=1500.0),
            _event("C101", "e2", "invoice_received", amount=1500.0, invoice_id="INV-C101"),
        ]
        policies = {"C101": CasePolicy("C101", "three_way_gr_based", 1500.0, 1.0, "V01", approval_limit=1000.0)}
        decision = evaluate_agent_action(events, policies, AgentAction("clear_payment", "C101", invoice_id="INV-C101"))
        self.assertEqual("block", decision["decision"])
        self.assertEqual(3, decision["exit_code"])
        self.assertIn("PAYMENT_BEFORE_GR", decision["blocking_codes"])
        self.assertIn("PAYMENT_BEFORE_APPROVAL", decision["blocking_codes"])
        self.assertIn("PAYMENT_BEFORE_GR", decision["new_codes"])

    def test_goods_receipt_can_resolve_missing_required_gr(self) -> None:
        events = [
            _event("C102", "e1", "po_created", amount=500.0),
            _event("C102", "e2", "invoice_received", amount=500.0, invoice_id="INV-C102"),
        ]
        policies = {"C102": CasePolicy("C102", "three_way_gr_based", 500.0, 1.0, "V01", approval_limit=1000.0)}
        decision = evaluate_agent_action(events, policies, AgentAction("record_goods_receipt", "C102"))
        self.assertEqual("allow", decision["decision"])
        self.assertIn("MISSING_REQUIRED_GR", decision["resolved_codes"])

    def test_unknown_case_requires_policy(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing policy"):
            evaluate_agent_action([], {}, AgentAction("clear_payment", "C404"))

    def test_unknown_action_type_is_rejected(self) -> None:
        events = [_event("C103", "e1", "po_created")]
        policies = {"C103": CasePolicy("C103", "two_way", 500.0, 1.0, "V01")}
        with self.assertRaisesRegex(ValueError, "unknown action_type"):
            evaluate_agent_action(events, policies, AgentAction("send_wire", "C103"))


if __name__ == "__main__":
    unittest.main()
