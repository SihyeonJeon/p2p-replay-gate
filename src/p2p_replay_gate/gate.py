from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .io import group_events
from .models import CasePolicy, CaseResult, P2PEvent, Violation
from .oracle import replay_case


ACTION_EVENT_TYPES = {
    "clear_payment": "payment_cleared",
    "record_approval": "approval_recorded",
    "release_invoice": "invoice_released",
    "block_invoice": "invoice_blocked",
    "receive_invoice": "invoice_received",
    "record_goods_receipt": "goods_receipt",
}

BLOCK_SEVERITIES = {"critical", "high"}


@dataclass(frozen=True)
class AgentAction:
    action_type: str
    case_id: str
    timestamp: str | None = None
    actor: str = "agent"
    po_id: str | None = None
    line_id: str | None = None
    vendor_id: str | None = None
    invoice_id: str | None = None
    amount: float | None = None
    quantity: float | None = None
    attrs: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentAction":
        action_type = str(data["action_type"])
        if action_type not in ACTION_EVENT_TYPES:
            raise ValueError(f"unknown action_type: {action_type}")
        attrs = data.get("attrs") or {}
        if not isinstance(attrs, dict):
            raise ValueError("attrs must be a JSON object")
        return cls(
            action_type=action_type,
            case_id=str(data["case_id"]),
            timestamp=_optional_str(data.get("timestamp")),
            actor=str(data.get("actor", "agent")),
            po_id=_optional_str(data.get("po_id")),
            line_id=_optional_str(data.get("line_id")),
            vendor_id=_optional_str(data.get("vendor_id")),
            invoice_id=_optional_str(data.get("invoice_id")),
            amount=_optional_float(data.get("amount")),
            quantity=_optional_float(data.get("quantity")),
            attrs=dict(attrs),
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "case_id": self.case_id,
            "timestamp": self.timestamp,
            "actor": self.actor,
            "po_id": self.po_id,
            "line_id": self.line_id,
            "vendor_id": self.vendor_id,
            "invoice_id": self.invoice_id,
            "amount": self.amount,
            "quantity": self.quantity,
            "attrs": self.attrs or {},
        }


def evaluate_agent_action(
    events: list[P2PEvent],
    policies: dict[str, CasePolicy],
    action: AgentAction,
) -> dict[str, Any]:
    if action.action_type not in ACTION_EVENT_TYPES:
        raise ValueError(f"unknown action_type: {action.action_type}")
    if action.case_id not in policies:
        raise ValueError(f"{action.case_id}: missing policy")
    grouped = group_events(events)
    if action.case_id not in grouped:
        raise ValueError(f"{action.case_id}: missing event trace")

    policy = policies[action.case_id]
    case_events = grouped[action.case_id]
    baseline = replay_case(case_events, policy)
    proposed_event = action_to_event(action, case_events, policy)
    after = replay_case([*case_events, proposed_event], policy)

    blockers = [violation for violation in after.violations if violation.severity in BLOCK_SEVERITIES]
    if blockers:
        decision = "block"
        reason = "blocked by " + ", ".join(sorted({violation.code for violation in blockers}))
    elif after.violations or not after.audit_trace_complete:
        decision = "review"
        if after.violations:
            reason = "manual review for " + ", ".join(sorted({violation.code for violation in after.violations}))
        else:
            reason = "manual review because audit trace is incomplete"
    else:
        decision = "allow"
        reason = "action accepted by replay policy"

    baseline_codes = {violation.code for violation in baseline.violations}
    after_codes = {violation.code for violation in after.violations}

    return {
        "run_id": "p2p-agent-action-gate-v0",
        "decision": decision,
        "reason": reason,
        "case_id": action.case_id,
        "action": action.to_public_dict(),
        "proposed_event": _event_row(proposed_event),
        "policy": _policy_row(policy),
        "baseline": _case_row(baseline),
        "after_action": _case_row(after),
        "new_codes": sorted(after_codes - baseline_codes),
        "resolved_codes": sorted(baseline_codes - after_codes),
        "blocking_codes": sorted({violation.code for violation in blockers}),
        "scope_note": "pre-execution action gate over supplied purchase-to-pay trace and policy",
        "exit_code": {"allow": 0, "review": 1, "block": 3}[decision],
    }


def action_to_event(action: AgentAction, case_events: list[P2PEvent], policy: CasePolicy) -> P2PEvent:
    anchor = case_events[-1]
    invoice_id = action.invoice_id or _latest_invoice_id(case_events) or f"INV-{action.case_id}"
    event_type = ACTION_EVENT_TYPES[action.action_type]
    event_id = f"{action.case_id}-agent-{action.action_type}-{invoice_id or 'na'}"
    return P2PEvent(
        case_id=action.case_id,
        event_id=event_id,
        timestamp=action.timestamp or _next_timestamp(case_events),
        event_type=event_type,
        po_id=action.po_id or anchor.po_id,
        line_id=action.line_id or anchor.line_id,
        vendor_id=action.vendor_id or policy.vendor_id,
        invoice_id=None if event_type in {"po_created", "goods_receipt"} else invoice_id,
        amount=action.amount if action.amount is not None else policy.po_amount,
        quantity=action.quantity if action.quantity is not None else policy.po_quantity,
        actor=action.actor,
        attrs=action.attrs or {},
    )


def _case_row(result: CaseResult) -> dict[str, Any]:
    return {
        "event_count": result.event_count,
        "audit_trace_complete": result.audit_trace_complete,
        "has_critical": result.has_critical,
        "violations": [_violation_row(violation) for violation in result.violations],
    }


def _policy_row(policy: CasePolicy) -> dict[str, Any]:
    return {
        "case_id": policy.case_id,
        "flow_type": policy.flow_type,
        "po_amount": policy.po_amount,
        "po_quantity": policy.po_quantity,
        "vendor_id": policy.vendor_id,
        "approval_limit": policy.approval_limit,
        "amount_tolerance": policy.amount_tolerance,
        "quantity_tolerance": policy.quantity_tolerance,
    }


def _event_row(event: P2PEvent) -> dict[str, Any]:
    return {
        "case_id": event.case_id,
        "event_id": event.event_id,
        "timestamp": event.timestamp,
        "event_type": event.event_type,
        "po_id": event.po_id,
        "line_id": event.line_id,
        "vendor_id": event.vendor_id,
        "invoice_id": event.invoice_id,
        "amount": event.amount,
        "quantity": event.quantity,
        "actor": event.actor,
        "attrs": event.attrs,
    }


def _violation_row(violation: Violation) -> dict[str, Any]:
    return {
        "code": violation.code,
        "severity": violation.severity,
        "event_id": violation.event_id,
        "message": violation.message,
        "evidence": violation.evidence,
    }


def _latest_invoice_id(events: list[P2PEvent]) -> str | None:
    for event in reversed(events):
        if event.invoice_id:
            return event.invoice_id
    return None


def _next_timestamp(events: list[P2PEvent]) -> str:
    latest = max(events, key=lambda event: (event.timestamp, event.event_id)).timestamp
    try:
        parsed = datetime.fromisoformat(latest.replace("Z", "+00:00"))
    except ValueError:
        return latest
    return (parsed + timedelta(seconds=1)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
