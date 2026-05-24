from __future__ import annotations

import csv
import gzip
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .io import group_events, write_json
from .models import EVENT_TYPES, FLOW_TYPES, P2PEvent


COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "case_id": ("case_id", "case:concept:name", "case", "case id", "caseid"),
    "event_id": ("event_id", "event:id", "event id", "id"),
    "timestamp": ("timestamp", "time:timestamp", "complete timestamp", "event_time", "time"),
    "activity": ("activity", "concept:name", "event", "event_name", "task", "activity_en"),
    "event_type": ("event_type", "p2p_event_type"),
    "po_id": ("po_id", "purchase_order", "purchase order", "po", "case:purchasing document"),
    "line_id": ("line_id", "item", "item_id", "case:item", "case:item type"),
    "vendor_id": ("vendor_id", "vendor", "supplier", "case:vendor", "case:supplier"),
    "invoice_id": ("invoice_id", "invoice", "invoice_id", "document", "document_id"),
    "amount": ("amount", "value", "case:amount", "invoice_amount", "po_amount", "net value"),
    "quantity": ("quantity", "qty", "case:quantity", "invoice_quantity", "po_quantity"),
    "actor": ("actor", "org:resource", "resource", "user", "agent"),
    "item_category": ("item_category", "item category", "case:item category", "item type", "case:item type"),
    "document_type": ("document_type", "document type", "case:document type"),
    "goods_receipt": ("goods_receipt", "goods receipt", "case:goods receipt"),
    "gr_based_inv_verif": ("gr_based_inv_verif", "gr-based inv. verif.", "case:gr-based inv. verif.", "gr based inv verif"),
}


ACTIVITY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("po_created", ("create purchase order", "purchase order created", "po created", "create po", "create order")),
    ("goods_receipt", ("goods receipt", "receive goods", "gr posted", "record goods", "goods received")),
    ("invoice_received", ("invoice receipt", "receive invoice", "invoice received", "record invoice", "scan invoice")),
    ("invoice_blocked", ("block invoice", "invoice blocked", "payment block", "block payment")),
    ("invoice_released", ("release invoice", "invoice released", "release blocked", "remove block")),
    ("approval_recorded", ("approve", "approval", "release purchase order", "manager approved")),
    ("payment_cleared", ("payment cleared", "clear invoice", "clear payment", "pay invoice", "payment sent")),
    ("reversal", ("reverse", "reversal", "cancel invoice", "cancel payment", "credit memo")),
)

STANDARD_COLUMN_MAP = {
    "case_id": "case_id",
    "event_id": "event_id",
    "timestamp": "timestamp",
    "activity": "activity",
    "event_type": "event_type",
    "po_id": "po_id",
    "line_id": "line_id",
    "vendor_id": "vendor_id",
    "invoice_id": "invoice_id",
    "amount": "amount",
    "quantity": "quantity",
    "actor": "actor",
    "item_category": "item_category",
    "document_type": "document_type",
    "goods_receipt": "goods_receipt",
    "gr_based_inv_verif": "gr_based_inv_verif",
}


@dataclass(frozen=True)
class ImportStats:
    rows_read: int
    events_written: int
    skipped_rows: int
    unmapped_activities: dict[str, int]
    row_errors: dict[str, int]
    column_map: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows_read": self.rows_read,
            "events_written": self.events_written,
            "skipped_rows": self.skipped_rows,
            "unmapped_activities": dict(sorted(self.unmapped_activities.items())),
            "row_errors": dict(sorted(self.row_errors.items())),
            "column_map": dict(sorted(self.column_map.items())),
        }


def import_csv_events(
    input_path: Path,
    output_path: Path,
    *,
    activity_map_path: Path | None = None,
    report_path: Path | None = None,
    strict: bool = False,
    activity_map: dict[str, str] | None = None,
) -> ImportStats:
    activity_map = activity_map or _load_activity_map(activity_map_path)
    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"{input_path} has no CSV header")
        column_map = _detect_columns(reader.fieldnames)
        _require_columns(input_path, column_map, ("case_id", "timestamp"))

        row_count = 0
        skipped_rows = 0
        unmapped: dict[str, int] = {}
        row_errors: dict[str, int] = {}
        events: list[P2PEvent] = []
        for row_count, row in enumerate(reader, start=1):
            event_type = _event_type(row, column_map, activity_map)
            if event_type is None:
                activity = _value(row, column_map, "activity") or _value(row, column_map, "event_type") or "<missing>"
                unmapped[activity] = unmapped.get(activity, 0) + 1
                skipped_rows += 1
                continue
            try:
                events.append(_event_from_row(row, row_count, column_map, event_type))
            except ValueError as exc:
                label = str(exc)
                row_errors[label] = row_errors.get(label, 0) + 1
                skipped_rows += 1

    if strict and (unmapped or row_errors):
        labels = []
        labels.extend(f"unmapped {name} ({count})" for name, count in sorted(unmapped.items()))
        labels.extend(f"row_error {name} ({count})" for name, count in sorted(row_errors.items()))
        raise ValueError("; ".join(labels))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for event in sorted(events, key=lambda item: (item.case_id, item.timestamp, item.event_id)):
            handle.write(json.dumps(_event_to_dict(event), sort_keys=True) + "\n")

    stats = ImportStats(
        rows_read=row_count,
        events_written=len(events),
        skipped_rows=skipped_rows,
        unmapped_activities=unmapped,
        row_errors=row_errors,
        column_map=column_map,
    )
    if report_path:
        write_json(report_path, stats.to_dict())
    return stats


def import_xes_events(
    input_path: Path,
    output_path: Path,
    *,
    activity_map_path: Path | None = None,
    report_path: Path | None = None,
    strict: bool = False,
    activity_map: dict[str, str] | None = None,
) -> ImportStats:
    activity_map = activity_map or _load_activity_map(activity_map_path)
    row_count = 0
    skipped_rows = 0
    unmapped: dict[str, int] = {}
    row_errors: dict[str, int] = {}
    events: list[P2PEvent] = []
    for row_count, row in enumerate(_xes_rows(input_path), start=1):
        event_type = _event_type(row, STANDARD_COLUMN_MAP, activity_map)
        if event_type is None:
            activity = row.get("activity") or row.get("event_type") or "<missing>"
            unmapped[activity] = unmapped.get(activity, 0) + 1
            skipped_rows += 1
            continue
        try:
            events.append(_event_from_row(row, row_count, STANDARD_COLUMN_MAP, event_type))
        except ValueError as exc:
            label = str(exc)
            row_errors[label] = row_errors.get(label, 0) + 1
            skipped_rows += 1

    if strict and (unmapped or row_errors):
        labels = []
        labels.extend(f"unmapped {name} ({count})" for name, count in sorted(unmapped.items()))
        labels.extend(f"row_error {name} ({count})" for name, count in sorted(row_errors.items()))
        raise ValueError("; ".join(labels))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for event in sorted(events, key=lambda item: (item.case_id, item.timestamp, item.event_id)):
            handle.write(json.dumps(_event_to_dict(event), sort_keys=True) + "\n")

    stats = ImportStats(
        rows_read=row_count,
        events_written=len(events),
        skipped_rows=skipped_rows,
        unmapped_activities=unmapped,
        row_errors=row_errors,
        column_map=STANDARD_COLUMN_MAP,
    )
    if report_path:
        write_json(report_path, stats.to_dict())
    return stats


def write_policy_template(
    events: list[P2PEvent],
    output_path: Path,
    *,
    flow_type: str = "three_way_gr_based",
    approval_limit: float = 1000.0,
    allow_missing_amount: bool = False,
) -> list[dict[str, Any]]:
    if flow_type != "auto" and flow_type not in FLOW_TYPES:
        raise ValueError(f"unknown flow_type: {flow_type}")
    policies: list[dict[str, Any]] = []
    for case_id, case_events in sorted(group_events(events).items()):
        first = case_events[0]
        warnings: list[str] = []
        po_amount = _first_amount(case_events)
        if po_amount is None:
            if not allow_missing_amount:
                raise ValueError(f"{case_id}: cannot create policy template without amount data")
            po_amount = 0.0
            warnings.append("po_amount defaulted to 0.0 because no amount data was present")
        po_quantity = _first_quantity(case_events)
        if po_quantity is None:
            po_quantity = 0.0
            warnings.append("po_quantity defaulted to 0.0 because no quantity data was present")
        if first.vendor_id == "UNKNOWN":
            warnings.append("vendor_id defaulted to UNKNOWN because no vendor column was present")
        case_flow_type = _case_flow_type(case_events, flow_type)
        if case_flow_type == "auto":
            raise ValueError(f"{case_id}: cannot infer flow_type; pass an explicit --flow-type")
        policies.append({
            "case_id": case_id,
            "flow_type": case_flow_type,
            "po_amount": po_amount,
            "po_quantity": po_quantity,
            "vendor_id": first.vendor_id,
            "approval_limit": approval_limit,
            "amount_tolerance": 0.01,
            "quantity_tolerance": 0.0,
            "template_warnings": warnings,
        })
    write_json(output_path, policies)
    return policies


def _detect_columns(fieldnames: list[str]) -> dict[str, str]:
    normalized = {_norm_column(name): name for name in fieldnames}
    mapping: dict[str, str] = {}
    for target, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            match = normalized.get(_norm_column(alias))
            if match:
                mapping[target] = match
                break
    return mapping


def _require_columns(path: Path, column_map: dict[str, str], required: tuple[str, ...]) -> None:
    missing = [name for name in required if name not in column_map]
    if missing:
        raise ValueError(f"{path} is missing required columns: {missing}")


def _event_type(row: dict[str, str], column_map: dict[str, str], activity_map: dict[str, str]) -> str | None:
    explicit = _value(row, column_map, "event_type")
    if explicit in EVENT_TYPES:
        return explicit
    activity = _value(row, column_map, "activity")
    if not activity:
        return None
    normalized = _norm_activity(activity)
    if normalized in activity_map:
        return activity_map[normalized]
    for event_type, patterns in ACTIVITY_PATTERNS:
        if any(pattern in normalized for pattern in patterns):
            return event_type
    return None


def _event_from_row(row: dict[str, str], source_row: int, column_map: dict[str, str], event_type: str) -> P2PEvent:
    case_id = _required_value(row, column_map, "case_id")
    activity = _value(row, column_map, "activity")
    event_id = _value(row, column_map, "event_id") or f"{case_id}-{source_row:06d}-{event_type}"
    attrs = {
        "source_row": source_row,
        "source_activity": activity,
    }
    for target in ("item_category", "document_type", "goods_receipt", "gr_based_inv_verif"):
        value = _value(row, column_map, target)
        if value is not None:
            attrs[f"source_{target}"] = value
    flow_hint = _infer_flow_type_from_row(row, column_map)
    if flow_hint is not None:
        attrs["flow_hint"] = flow_hint
    return P2PEvent(
        case_id=case_id,
        event_id=event_id,
        timestamp=_required_value(row, column_map, "timestamp"),
        event_type=event_type,
        po_id=_value(row, column_map, "po_id") or f"PO-{case_id}",
        line_id=_value(row, column_map, "line_id") or "10",
        vendor_id=_value(row, column_map, "vendor_id") or "UNKNOWN",
        invoice_id=_value(row, column_map, "invoice_id"),
        amount=_float_or_none(_value(row, column_map, "amount")),
        quantity=_float_or_none(_value(row, column_map, "quantity")),
        actor=_value(row, column_map, "actor") or "system",
        attrs={key: value for key, value in attrs.items() if value is not None},
    )


def _load_activity_map(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return normalize_activity_map(data, str(path))


def normalize_activity_map(data: dict[str, Any], source_label: str = "activity_map") -> dict[str, str]:
    if not isinstance(data, dict):
        raise ValueError(f"{source_label}: activity map must be a JSON object")
    mapping = {}
    for source, target in data.items():
        target = str(target)
        if target not in EVENT_TYPES:
            raise ValueError(f"{source_label}: unknown target event_type for {source}: {target}")
        mapping[_norm_activity(str(source))] = target
    return mapping


def _value(row: dict[str, str], column_map: dict[str, str], target: str) -> str | None:
    column = column_map.get(target)
    if not column:
        return None
    value = row.get(column)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _required_value(row: dict[str, str], column_map: dict[str, str], target: str) -> str:
    value = _value(row, column_map, target)
    if value is None:
        raise ValueError(f"row missing required value for {target}")
    return value


def _float_or_none(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = value.replace(",", "").strip()
    if not cleaned:
        return None
    return float(cleaned)


def _first_amount(events: list[P2PEvent]) -> float | None:
    for preferred in ("po_created", "invoice_received", "goods_receipt"):
        for event in events:
            if event.event_type == preferred and event.amount is not None:
                return event.amount
    return None


def _first_quantity(events: list[P2PEvent]) -> float | None:
    for preferred in ("po_created", "invoice_received", "goods_receipt"):
        for event in events:
            if event.event_type == preferred and event.quantity is not None:
                return event.quantity
    return None


def _case_flow_type(events: list[P2PEvent], requested: str) -> str:
    if requested != "auto":
        return requested
    for event in events:
        flow_hint = event.attrs.get("flow_hint")
        if isinstance(flow_hint, str) and flow_hint in FLOW_TYPES:
            return flow_hint
    return "auto"


def _infer_flow_type_from_row(row: dict[str, str], column_map: dict[str, str]) -> str | None:
    item_category = _norm_activity(_value(row, column_map, "item_category") or "")
    document_type = _norm_activity(_value(row, column_map, "document_type") or "")
    category_text = f"{item_category} {document_type}"
    if "consignment" in category_text:
        return "consignment"
    goods_receipt = _truthy_flag(_value(row, column_map, "goods_receipt"))
    gr_based = _truthy_flag(_value(row, column_map, "gr_based_inv_verif"))
    if goods_receipt is False:
        return "two_way"
    if goods_receipt is True:
        if gr_based is True:
            return "three_way_gr_based"
        return "three_way_invoice_before_gr"
    if "2 way" in category_text or "two way" in category_text:
        return "two_way"
    if "3 way" in category_text:
        if "gr based" in category_text:
            return "three_way_gr_based"
        return "three_way_invoice_before_gr"
    return None


def _truthy_flag(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = _norm_activity(value)
    if normalized in {"true", "yes", "y", "1", "x", "required", "gr"}:
        return True
    if normalized in {"false", "no", "n", "0", "none", "not required", "nan"}:
        return False
    return None


def _event_to_dict(event: P2PEvent) -> dict[str, Any]:
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


def _norm_column(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _norm_activity(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def _xes_rows(path: Path):
    for trace_attrs, event_attrs in _iter_xes(path):
        yield {
            "case_id": _pick(trace_attrs, "concept:name", "case:concept:name"),
            "event_id": _pick(event_attrs, "identity:id", "event_id", "event:id"),
            "timestamp": _pick(event_attrs, "time:timestamp", "timestamp"),
            "activity": _pick(event_attrs, "concept:name", "activity"),
            "event_type": _pick(event_attrs, "event_type", "p2p_event_type"),
            "po_id": _pick(event_attrs, "Purchasing Document", "po_id", "purchase_order")
            or _pick(trace_attrs, "Purchasing Document", "case:Purchasing Document", "po_id", "purchase_order"),
            "line_id": _pick(event_attrs, "Item", "Item ID", "line_id", "item")
            or _pick(trace_attrs, "Item", "Item ID", "case:Item", "line_id", "item"),
            "vendor_id": _pick(event_attrs, "Vendor", "Supplier", "vendor_id", "supplier")
            or _pick(trace_attrs, "Vendor", "Supplier", "case:Vendor", "vendor_id", "supplier"),
            "invoice_id": _pick(event_attrs, "Invoice", "Invoice ID", "Document", "Document ID", "invoice_id"),
            "amount": _pick(event_attrs, "amount", "Amount", "Net value", "Cumulative net worth (EUR)")
            or _pick(trace_attrs, "amount", "Amount", "Net value", "Cumulative net worth (EUR)", "case:Cumulative net worth (EUR)"),
            "quantity": _pick(event_attrs, "quantity", "Quantity", "qty") or _pick(trace_attrs, "quantity", "Quantity", "qty"),
            "actor": _pick(event_attrs, "org:resource", "resource", "user", "User"),
            "item_category": _pick(trace_attrs, "Item Category", "case:Item Category", "Item Type", "case:Item Type", "item_category"),
            "document_type": _pick(trace_attrs, "Document Type", "case:Document Type", "document_type"),
            "goods_receipt": _pick(trace_attrs, "Goods Receipt", "case:Goods Receipt", "goods_receipt"),
            "gr_based_inv_verif": _pick(trace_attrs, "GR-Based Inv. Verif.", "case:GR-Based Inv. Verif.", "gr_based_inv_verif"),
        }


def _iter_xes(path: Path):
    trace_attrs: dict[str, str] = {}
    event_attrs: dict[str, str] | None = None
    in_trace = False
    with _open_xes(path) as handle:
        for action, elem in ET.iterparse(handle, events=("start", "end")):
            tag = _local_name(elem.tag)
            if action == "start":
                if tag == "trace":
                    trace_attrs = {}
                    in_trace = True
                elif tag == "event":
                    event_attrs = {}
                continue
            if tag in {"string", "date", "int", "float", "boolean", "id"}:
                key = elem.attrib.get("key")
                value = elem.attrib.get("value")
                if key and value is not None:
                    if event_attrs is not None:
                        event_attrs[key] = value
                    elif in_trace:
                        trace_attrs[key] = value
            elif tag == "event":
                if event_attrs is not None:
                    yield dict(trace_attrs), dict(event_attrs)
                event_attrs = None
                elem.clear()
            elif tag == "trace":
                in_trace = False
                trace_attrs = {}
                elem.clear()


def _open_xes(path: Path):
    if path.suffix == ".gz" or "".join(path.suffixes[-2:]) == ".xes.gz":
        return gzip.open(path, "rb")
    return path.open("rb")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _pick(data: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None
