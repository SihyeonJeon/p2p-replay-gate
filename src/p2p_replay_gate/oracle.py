from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from .models import CasePolicy, CaseResult, P2PEvent, SEVERITY_BY_CODE, Violation


@dataclass
class ReplayState:
    po_created: bool = False
    received_amount: float = 0.0
    received_quantity: float = 0.0
    invoice_amount: float = 0.0
    invoice_quantity: float = 0.0
    invoice_ids: set[str] | None = None
    blocked_invoice_ids: set[str] | None = None
    released_invoice_ids: set[str] | None = None
    approval_recorded: bool = False
    payment_cleared: bool = False
    reversal_after_payment: bool = False
    audit_events: list[str] | None = None

    def __post_init__(self) -> None:
        self.invoice_ids = set()
        self.blocked_invoice_ids = set()
        self.released_invoice_ids = set()
        self.audit_events = []


def replay_case(events: list[P2PEvent], policy: CasePolicy) -> CaseResult:
    state = ReplayState()
    violations: list[Violation] = []
    clean_hold_count = 0
    for event in sorted(events, key=lambda row: (row.timestamp, row.event_id)):
        state.audit_events.append(event.event_id)
        if event.vendor_id != policy.vendor_id:
            violations.append(_violation("VENDOR_MISMATCH", event, f"event vendor {event.vendor_id} differs from PO vendor {policy.vendor_id}", {"expected": policy.vendor_id, "actual": event.vendor_id}))

        if event.event_type == "po_created":
            state.po_created = True
        elif event.event_type == "goods_receipt":
            state.received_amount += event.amount or 0.0
            state.received_quantity += event.quantity or 0.0
        elif event.event_type == "invoice_received":
            if event.invoice_id in state.invoice_ids:
                violations.append(_violation("DUPLICATE_INVOICE", event, f"invoice {event.invoice_id} was seen more than once", {"invoice_id": event.invoice_id}))
            if policy.flow_type == "consignment":
                violations.append(_violation("CONSIGNMENT_INVOICE", event, "consignment flow should not receive PO-level invoice", {"invoice_id": event.invoice_id}))
            state.invoice_ids.add(event.invoice_id or event.event_id)
            state.invoice_amount += event.amount or 0.0
            state.invoice_quantity += event.quantity or 0.0
        elif event.event_type == "invoice_blocked":
            invoice_id = event.invoice_id or event.event_id
            state.blocked_invoice_ids.add(invoice_id)
            if _can_release_invoice(state, policy):
                clean_hold_count += 1
                violations.append(_violation("UNNEEDED_HOLD", event, f"invoice {invoice_id} was held even though required evidence was already present", {"invoice_id": invoice_id}))
        elif event.event_type == "invoice_released":
            state.released_invoice_ids.add(event.invoice_id or event.event_id)
        elif event.event_type == "approval_recorded":
            state.approval_recorded = True
        elif event.event_type == "payment_cleared":
            _check_payment(event, state, policy, violations)
            state.payment_cleared = True
        elif event.event_type == "reversal":
            if state.payment_cleared:
                state.reversal_after_payment = True
                violations.append(_violation("REVERSAL_AFTER_PAYMENT", event, "reversal happened after payment was cleared", {"event_id": event.event_id}))

    _check_end_state(events, state, policy, violations)
    audit_complete = _audit_trace_complete(events, state, policy)
    return CaseResult(
        case_id=policy.case_id,
        violations=tuple(_dedupe_violations(violations)),
        clean_hold_count=clean_hold_count,
        audit_trace_complete=audit_complete,
        event_count=len(events),
    )


def _check_payment(event: P2PEvent, state: ReplayState, policy: CasePolicy, violations: list[Violation]) -> None:
    if policy.requires_gr and state.received_amount <= 0:
        violations.append(_violation("PAYMENT_BEFORE_GR", event, "payment cleared before required goods receipt", {"received_amount": state.received_amount}))
    if policy.po_amount >= policy.approval_limit and not state.approval_recorded:
        violations.append(_violation("PAYMENT_BEFORE_APPROVAL", event, "payment cleared before required approval", {"approval_limit": policy.approval_limit, "po_amount": policy.po_amount}))
    unreleased = sorted((state.blocked_invoice_ids or set()) - (state.released_invoice_ids or set()))
    if unreleased:
        violations.append(_violation("PAYMENT_WHILE_BLOCKED", event, "payment cleared while invoice hold was still active", {"blocked_invoice_ids": unreleased}))


def _check_end_state(events: list[P2PEvent], state: ReplayState, policy: CasePolicy, violations: list[Violation]) -> None:
    if policy.requires_gr and state.received_amount <= 0:
        event = events[-1]
        violations.append(_violation("MISSING_REQUIRED_GR", event, "required goods receipt was not present", {"flow_type": policy.flow_type}))
    if policy.requires_invoice and state.invoice_ids:
        amount_delta = abs(state.invoice_amount - policy.po_amount)
        if amount_delta > policy.amount_tolerance:
            violations.append(_violation("VALUE_MISMATCH", events[-1], "invoice amount does not match PO amount within tolerance", {"po_amount": policy.po_amount, "invoice_amount": state.invoice_amount, "delta": amount_delta}))
        quantity_delta = abs(state.invoice_quantity - policy.po_quantity)
        if quantity_delta > policy.quantity_tolerance:
            violations.append(_violation("QUANTITY_MISMATCH", events[-1], "invoice quantity does not match PO quantity within tolerance", {"po_quantity": policy.po_quantity, "invoice_quantity": state.invoice_quantity, "delta": quantity_delta}))
    if state.blocked_invoice_ids:
        unreleased = sorted((state.blocked_invoice_ids or set()) - (state.released_invoice_ids or set()))
        if unreleased and not state.payment_cleared:
            violations.append(_violation("UNRELEASED_BLOCK", events[-1], "invoice hold remained unresolved by replay end", {"blocked_invoice_ids": unreleased}))


def _can_release_invoice(state: ReplayState, policy: CasePolicy) -> bool:
    if policy.requires_gr and state.received_amount <= 0:
        return False
    if abs(state.invoice_amount - policy.po_amount) > policy.amount_tolerance:
        return False
    if abs(state.invoice_quantity - policy.po_quantity) > policy.quantity_tolerance:
        return False
    return True


def _audit_trace_complete(events: list[P2PEvent], state: ReplayState, policy: CasePolicy) -> bool:
    event_types = {event.event_type for event in events}
    if "po_created" not in event_types:
        return False
    if policy.requires_gr and "goods_receipt" not in event_types:
        return False
    if policy.requires_invoice and "invoice_received" not in event_types:
        return False
    if policy.requires_invoice and policy.po_amount >= policy.approval_limit and "approval_recorded" not in event_types:
        return False
    return bool(state.audit_events)


def _violation(code: str, event: P2PEvent, message: str, evidence: dict[str, Any]) -> Violation:
    return Violation(
        code=code,
        severity=SEVERITY_BY_CODE[code],
        case_id=event.case_id,
        event_id=event.event_id,
        message=message,
        evidence=evidence,
    )


def _dedupe_violations(violations: list[Violation]) -> list[Violation]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[Violation] = []
    for violation in violations:
        key = (violation.code, violation.case_id, violation.event_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(violation)
    return deduped


def summarize_violations(results: list[CaseResult]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for result in results:
        counts.update(violation.code for violation in result.violations)
    return dict(sorted(counts.items()))
