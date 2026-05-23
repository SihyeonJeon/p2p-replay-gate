from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


FLOW_TYPES = {"three_way_gr_based", "three_way_invoice_before_gr", "two_way", "consignment"}
EVENT_TYPES = {
    "po_created",
    "goods_receipt",
    "invoice_received",
    "invoice_blocked",
    "invoice_released",
    "approval_recorded",
    "payment_cleared",
    "reversal",
}
SEVERITY_BY_CODE = {
    "DUPLICATE_INVOICE": "critical",
    "PAYMENT_BEFORE_GR": "critical",
    "PAYMENT_BEFORE_APPROVAL": "critical",
    "PAYMENT_WHILE_BLOCKED": "critical",
    "VENDOR_MISMATCH": "critical",
    "CONSIGNMENT_INVOICE": "high",
    "VALUE_MISMATCH": "high",
    "QUANTITY_MISMATCH": "high",
    "MISSING_REQUIRED_GR": "high",
    "UNRELEASED_BLOCK": "medium",
    "REVERSAL_AFTER_PAYMENT": "medium",
    "UNNEEDED_HOLD": "low",
}


@dataclass(frozen=True)
class P2PEvent:
    case_id: str
    event_id: str
    timestamp: str
    event_type: str
    po_id: str
    line_id: str
    vendor_id: str
    invoice_id: str | None = None
    amount: float | None = None
    quantity: float | None = None
    actor: str = "system"
    attrs: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "P2PEvent":
        event_type = str(data["event_type"])
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event_type for {data.get('event_id')}: {event_type}")
        return cls(
            case_id=str(data["case_id"]),
            event_id=str(data["event_id"]),
            timestamp=str(data["timestamp"]),
            event_type=event_type,
            po_id=str(data["po_id"]),
            line_id=str(data["line_id"]),
            vendor_id=str(data["vendor_id"]),
            invoice_id=_optional_str(data.get("invoice_id")),
            amount=_optional_float(data.get("amount")),
            quantity=_optional_float(data.get("quantity")),
            actor=str(data.get("actor", "system")),
            attrs=dict(data.get("attrs", {})),
        )


@dataclass(frozen=True)
class CasePolicy:
    case_id: str
    flow_type: str
    po_amount: float
    po_quantity: float
    vendor_id: str
    approval_limit: float = 1000.0
    amount_tolerance: float = 0.01
    quantity_tolerance: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CasePolicy":
        flow_type = str(data["flow_type"])
        if flow_type not in FLOW_TYPES:
            raise ValueError(f"unknown flow_type for {data.get('case_id')}: {flow_type}")
        return cls(
            case_id=str(data["case_id"]),
            flow_type=flow_type,
            po_amount=float(data["po_amount"]),
            po_quantity=float(data["po_quantity"]),
            vendor_id=str(data["vendor_id"]),
            approval_limit=float(data.get("approval_limit", 1000.0)),
            amount_tolerance=float(data.get("amount_tolerance", 0.01)),
            quantity_tolerance=float(data.get("quantity_tolerance", 0.0)),
        )

    @property
    def requires_gr(self) -> bool:
        return self.flow_type in {"three_way_gr_based", "three_way_invoice_before_gr"}

    @property
    def requires_invoice(self) -> bool:
        return self.flow_type != "consignment"


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    case_id: str
    mutation: str
    expected_codes: tuple[str, ...]
    description: str
    events: tuple[P2PEvent, ...]
    replace_base: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Scenario":
        return cls(
            scenario_id=str(data["scenario_id"]),
            case_id=str(data["case_id"]),
            mutation=str(data["mutation"]),
            expected_codes=tuple(str(code) for code in data.get("expected_codes", [])),
            description=str(data.get("description", "")),
            events=tuple(P2PEvent.from_dict(row) for row in data["events"]),
            replace_base=bool(data.get("replace_base", False)),
        )


@dataclass(frozen=True)
class Violation:
    code: str
    severity: str
    case_id: str
    event_id: str
    message: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    violations: tuple[Violation, ...]
    clean_hold_count: int
    audit_trace_complete: bool
    event_count: int

    @property
    def has_critical(self) -> bool:
        return any(v.severity == "critical" for v in self.violations)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
