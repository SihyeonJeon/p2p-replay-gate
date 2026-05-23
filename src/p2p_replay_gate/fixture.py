from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_fixture(root: Path) -> None:
    scenario_dir = root / "data" / "scenarios"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    policies = _policies()
    base_events = _base_events(policies)
    scenarios = _scenarios(policies, base_events)
    (scenario_dir / "policy.json").write_text(json.dumps(policies, indent=2) + "\n", encoding="utf-8")
    with (scenario_dir / "base_events.jsonl").open("w", encoding="utf-8") as handle:
        for event in base_events:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
    (scenario_dir / "injected_scenarios.json").write_text(json.dumps(scenarios, indent=2) + "\n", encoding="utf-8")


def _policies() -> list[dict[str, Any]]:
    flows = [
        ("three_way_gr_based", 1250.0, 10.0, 1000.0),
        ("three_way_invoice_before_gr", 1800.0, 12.0, 1000.0),
        ("two_way", 640.0, 4.0, 1000.0),
        ("consignment", 2200.0, 20.0, 1000.0),
    ]
    rows = []
    for i in range(12):
        flow, amount, qty, approval = flows[i % len(flows)]
        rows.append({
            "case_id": f"C{i + 1:03d}",
            "flow_type": flow,
            "po_amount": amount + (i // 4) * 100,
            "po_quantity": qty,
            "vendor_id": f"V{i % 4 + 1:02d}",
            "approval_limit": approval,
            "amount_tolerance": 0.01,
            "quantity_tolerance": 0.0,
        })
    return rows


def _base_events(policies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx, policy in enumerate(policies, start=1):
        case_id = policy["case_id"]
        po_id = f"PO-{case_id}"
        line_id = "10"
        vendor = policy["vendor_id"]
        amount = policy["po_amount"]
        qty = policy["po_quantity"]
        invoice = f"INV-{case_id}"
        rows.append(_event(case_id, 1, "po_created", po_id, line_id, vendor, amount=amount, quantity=qty))
        flow = policy["flow_type"]
        if flow == "three_way_gr_based":
            rows.append(_event(case_id, 2, "goods_receipt", po_id, line_id, vendor, amount=amount, quantity=qty))
            rows.append(_event(case_id, 3, "invoice_received", po_id, line_id, vendor, invoice_id=invoice, amount=amount, quantity=qty))
            rows.append(_event(case_id, 4, "approval_recorded", po_id, line_id, vendor, invoice_id=invoice, amount=amount, quantity=qty, actor="manager"))
            rows.append(_event(case_id, 5, "payment_cleared", po_id, line_id, vendor, invoice_id=invoice, amount=amount, quantity=qty, actor="batch"))
        elif flow == "three_way_invoice_before_gr":
            rows.append(_event(case_id, 2, "invoice_received", po_id, line_id, vendor, invoice_id=invoice, amount=amount, quantity=qty))
            rows.append(_event(case_id, 3, "invoice_blocked", po_id, line_id, vendor, invoice_id=invoice, amount=amount, quantity=qty))
            rows.append(_event(case_id, 4, "goods_receipt", po_id, line_id, vendor, amount=amount, quantity=qty))
            rows.append(_event(case_id, 5, "invoice_released", po_id, line_id, vendor, invoice_id=invoice, amount=amount, quantity=qty, actor="batch"))
            rows.append(_event(case_id, 6, "approval_recorded", po_id, line_id, vendor, invoice_id=invoice, amount=amount, quantity=qty, actor="manager"))
            rows.append(_event(case_id, 7, "payment_cleared", po_id, line_id, vendor, invoice_id=invoice, amount=amount, quantity=qty, actor="batch"))
        elif flow == "two_way":
            rows.append(_event(case_id, 2, "invoice_received", po_id, line_id, vendor, invoice_id=invoice, amount=amount, quantity=qty))
            rows.append(_event(case_id, 3, "payment_cleared", po_id, line_id, vendor, invoice_id=invoice, amount=amount, quantity=qty, actor="batch"))
        elif flow == "consignment":
            rows.append(_event(case_id, 2, "goods_receipt", po_id, line_id, vendor, amount=amount, quantity=qty))
        else:
            raise AssertionError(flow)
    return rows


def _scenarios(policies: list[dict[str, Any]], base_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_case = {}
    for event in base_events:
        by_case.setdefault(event["case_id"], []).append(event)
    scenarios: list[dict[str, Any]] = []
    for index, policy in enumerate(policies):
        mutation_plan = [
            "duplicate_invoice",
            "vendor_mismatch",
            "consignment_invoice" if policy["flow_type"] == "consignment" else ("amount_overbill" if index % 2 == 0 else "quantity_mismatch"),
            _state_mutation(policy["flow_type"]),
        ]
        for mutation in mutation_plan:
            scenarios.append(_scenario(policy, by_case[policy["case_id"]], mutation))
    return scenarios


def _state_mutation(flow_type: str) -> str:
    if flow_type == "three_way_gr_based":
        return "payment_before_gr"
    if flow_type == "three_way_invoice_before_gr":
        return "payment_before_approval"
    if flow_type == "two_way":
        return "payment_while_blocked"
    if flow_type == "consignment":
        return "consignment_duplicate_invoice"
    raise AssertionError(flow_type)


def _scenario(policy: dict[str, Any], base: list[dict[str, Any]], mutation: str) -> dict[str, Any]:
    case_id = policy["case_id"]
    amount = policy["po_amount"]
    qty = policy["po_quantity"]
    vendor = policy["vendor_id"]
    po_id = f"PO-{case_id}"
    invoice = f"INV-{case_id}"
    line = "10"
    sid = f"S-{case_id}-{mutation}"
    flow = policy["flow_type"]
    event = None
    replace = False
    expected = _expected_codes(mutation)

    if mutation == "duplicate_invoice":
        if flow == "consignment":
            events = [
                _event(case_id, 3, "invoice_received", po_id, line, vendor, invoice_id=invoice, amount=amount, quantity=qty),
                _event(case_id, 4, "invoice_received", po_id, line, vendor, invoice_id=invoice, amount=amount, quantity=qty),
            ]
            expected = ["CONSIGNMENT_INVOICE", "DUPLICATE_INVOICE"]
            return _scenario_payload(sid, case_id, mutation, expected, events, replace)
        else:
            event = _event(case_id, 8, "invoice_received", po_id, line, vendor, invoice_id=invoice, amount=amount, quantity=qty)
    elif mutation == "vendor_mismatch":
        event = _event(case_id, 8, "invoice_received", po_id, line, "V99", invoice_id=f"BAD-{invoice}", amount=0, quantity=0)
    elif mutation == "amount_overbill":
        event = _event(case_id, 8, "invoice_received", po_id, line, vendor, invoice_id=f"OVER-{invoice}", amount=75.0, quantity=0)
    elif mutation == "quantity_mismatch":
        event = _event(case_id, 8, "invoice_received", po_id, line, vendor, invoice_id=f"QTY-{invoice}", amount=0, quantity=1)
    elif mutation == "payment_before_gr":
        event = _event(case_id, 0, "payment_cleared", po_id, line, vendor, invoice_id=f"EARLY-{invoice}", amount=amount, quantity=qty, actor="batch")
    elif mutation == "payment_before_approval":
        replace = True
        mutated = [
            _event(case_id, 1, "po_created", po_id, line, vendor, amount=amount, quantity=qty),
            _event(case_id, 2, "invoice_received", po_id, line, vendor, invoice_id=invoice, amount=amount, quantity=qty),
            _event(case_id, 3, "goods_receipt", po_id, line, vendor, amount=amount, quantity=qty),
            _event(case_id, 4, "payment_cleared", po_id, line, vendor, invoice_id=invoice, amount=amount, quantity=qty, actor="batch"),
        ]
        return _scenario_payload(sid, case_id, mutation, expected, mutated, replace)
    elif mutation == "payment_while_blocked":
        replace = True
        mutated = [
            _event(case_id, 1, "po_created", po_id, line, vendor, amount=amount, quantity=qty),
            _event(case_id, 2, "invoice_received", po_id, line, vendor, invoice_id=invoice, amount=amount, quantity=qty),
            _event(case_id, 3, "invoice_blocked", po_id, line, vendor, invoice_id=invoice, amount=amount, quantity=qty),
            _event(case_id, 4, "payment_cleared", po_id, line, vendor, invoice_id=invoice, amount=amount, quantity=qty, actor="batch"),
        ]
        return _scenario_payload(sid, case_id, mutation, expected, mutated, replace)
    elif mutation == "consignment_invoice":
        event = _event(case_id, 3, "invoice_received", po_id, line, vendor, invoice_id=invoice, amount=amount, quantity=qty)
    elif mutation == "consignment_duplicate_invoice":
        events = [
            _event(case_id, 3, "invoice_received", po_id, line, vendor, invoice_id=invoice, amount=amount, quantity=qty),
            _event(case_id, 4, "invoice_received", po_id, line, vendor, invoice_id=invoice, amount=amount, quantity=qty),
        ]
        return _scenario_payload(sid, case_id, mutation, expected, events, replace)
    elif mutation == "missing_required_gr":
        replace = True
        event = None
        mutated = [
            _event(case_id, 1, "po_created", po_id, line, vendor, amount=amount, quantity=qty),
            _event(case_id, 2, "invoice_received", po_id, line, vendor, invoice_id=invoice, amount=amount, quantity=qty),
            _event(case_id, 3, "approval_recorded", po_id, line, vendor, invoice_id=invoice, amount=amount, quantity=qty, actor="manager"),
        ]
        return _scenario_payload(sid, case_id, mutation, expected, mutated, replace)
    elif mutation == "unreleased_block":
        replace = True
        mutated = [
            _event(case_id, 1, "po_created", po_id, line, vendor, amount=amount, quantity=qty),
            _event(case_id, 2, "invoice_received", po_id, line, vendor, invoice_id=invoice, amount=amount, quantity=qty),
            _event(case_id, 3, "invoice_blocked", po_id, line, vendor, invoice_id=invoice, amount=amount, quantity=qty),
        ]
        if flow != "two_way":
            mutated.append(_event(case_id, 4, "goods_receipt", po_id, line, vendor, amount=amount, quantity=qty))
        return _scenario_payload(sid, case_id, mutation, expected, mutated, replace)
    elif mutation == "reversal_after_payment":
        event = _event(case_id, 9, "reversal", po_id, line, vendor, invoice_id=invoice, amount=amount, quantity=qty, actor="ap_user")
    elif mutation == "unneeded_hold":
        event = _event(case_id, 9, "invoice_blocked", po_id, line, vendor, invoice_id=f"LATEHOLD-{invoice}", amount=amount, quantity=qty, actor="ap_user")
    else:
        raise AssertionError(mutation)

    return _scenario_payload(sid, case_id, mutation, expected, [event], replace)


def _expected_codes(mutation: str) -> list[str]:
    expected = {
        "duplicate_invoice": ["DUPLICATE_INVOICE"],
        "vendor_mismatch": ["VENDOR_MISMATCH"],
        "amount_overbill": ["VALUE_MISMATCH"],
        "quantity_mismatch": ["QUANTITY_MISMATCH"],
        "payment_before_gr": ["PAYMENT_BEFORE_GR"],
        "payment_before_approval": ["PAYMENT_BEFORE_APPROVAL"],
        "payment_while_blocked": ["PAYMENT_WHILE_BLOCKED"],
        "consignment_invoice": ["CONSIGNMENT_INVOICE"],
        "consignment_duplicate_invoice": ["CONSIGNMENT_INVOICE", "DUPLICATE_INVOICE"],
        "missing_required_gr": ["MISSING_REQUIRED_GR"],
        "unreleased_block": ["UNRELEASED_BLOCK"],
        "reversal_after_payment": ["REVERSAL_AFTER_PAYMENT"],
        "unneeded_hold": ["UNNEEDED_HOLD"],
    }
    return list(expected[mutation])


def _scenario_payload(sid: str, case_id: str, mutation: str, expected: list[str], events: list[dict[str, Any]], replace: bool = False) -> dict[str, Any]:
    return {
        "scenario_id": sid,
        "case_id": case_id,
        "mutation": mutation,
        "expected_codes": expected,
        "description": mutation.replace("_", " "),
        "replace_base": replace,
        "events": events,
    }


def _event(
    case_id: str,
    step: int,
    event_type: str,
    po_id: str,
    line_id: str,
    vendor_id: str,
    invoice_id: str | None = None,
    amount: float | None = None,
    quantity: float | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "event_id": f"{case_id}-{step:02d}-{event_type}-{invoice_id or 'na'}",
        "timestamp": f"2026-01-{max(1, step + 1):02d}T09:00:00Z",
        "event_type": event_type,
        "po_id": po_id,
        "line_id": line_id,
        "vendor_id": vendor_id,
        "invoice_id": invoice_id,
        "amount": amount,
        "quantity": quantity,
        "actor": actor,
        "attrs": {},
    }


if __name__ == "__main__":
    write_fixture(Path.cwd())
