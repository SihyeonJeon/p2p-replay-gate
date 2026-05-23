from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from p2p_replay_gate.adapters import import_csv_events, import_xes_events, write_policy_template
from p2p_replay_gate.io import load_events, read_json


CSV_TEXT = """case:concept:name,concept:name,time:timestamp,case:purchasing document,case:vendor,invoice,amount,qty,org:resource
C100,Create Purchase Order,2026-01-01T09:00:00Z,PO-C100,V01,,1250,10,buyer
C100,Record Goods Receipt,2026-01-02T09:00:00Z,PO-C100,V01,,1250,10,warehouse
C100,Record Invoice Receipt,2026-01-03T09:00:00Z,PO-C100,V01,INV-C100,1250,10,ap
C100,Manager Approved,2026-01-04T09:00:00Z,PO-C100,V01,INV-C100,1250,10,manager
C100,Clear Payment,2026-01-05T09:00:00Z,PO-C100,V01,INV-C100,1250,10,batch
C100,Archive Case,2026-01-06T09:00:00Z,PO-C100,V01,INV-C100,1250,10,batch
"""

CSV_NO_INVOICE_IDS = """case_id,event_type,timestamp,po_id,vendor_id,amount,quantity
C200,po_created,2026-01-01T09:00:00Z,PO-C200,V02,150,3
C200,invoice_received,2026-01-02T09:00:00Z,PO-C200,V02,100,2
C200,invoice_received,2026-01-03T09:00:00Z,PO-C200,V02,50,1
"""

CSV_NO_AMOUNT = """case_id,event_type,timestamp,po_id,vendor_id
C300,po_created,2026-01-01T09:00:00Z,PO-C300,V03
C300,invoice_received,2026-01-02T09:00:00Z,PO-C300,V03
"""

XES_TEXT = """<?xml version="1.0" encoding="UTF-8" ?>
<log xmlns="http://www.xes-standard.org/">
  <trace>
    <string key="concept:name" value="C500"/>
    <string key="Purchasing Document" value="PO-C500"/>
    <string key="Vendor" value="V05"/>
    <float key="Cumulative net worth (EUR)" value="700"/>
    <float key="Quantity" value="7"/>
    <event>
      <string key="concept:name" value="Create Purchase Order"/>
      <date key="time:timestamp" value="2026-02-01T09:00:00Z"/>
    </event>
    <event>
      <string key="concept:name" value="Record Invoice Receipt"/>
      <date key="time:timestamp" value="2026-02-02T09:00:00Z"/>
      <string key="Invoice" value="INV-C500"/>
    </event>
    <event>
      <string key="concept:name" value="Clear Payment"/>
      <date key="time:timestamp" value="2026-02-03T09:00:00Z"/>
      <string key="Invoice" value="INV-C500"/>
    </event>
  </trace>
</log>
"""


class AdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.csv_path = self.root / "events.csv"
        self.jsonl_path = self.root / "events.jsonl"
        self.report_path = self.root / "adapter_report.json"
        self.csv_path.write_text(CSV_TEXT, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_import_csv_maps_common_process_columns(self) -> None:
        stats = import_csv_events(self.csv_path, self.jsonl_path, report_path=self.report_path)
        self.assertEqual(6, stats.rows_read)
        self.assertEqual(5, stats.events_written)
        self.assertEqual(1, stats.skipped_rows)
        events = load_events(self.jsonl_path)
        self.assertEqual(["po_created", "goods_receipt", "invoice_received", "approval_recorded", "payment_cleared"], [event.event_type for event in events])

    def test_import_csv_report_records_unmapped_activity(self) -> None:
        import_csv_events(self.csv_path, self.jsonl_path, report_path=self.report_path)
        report = read_json(self.report_path)
        self.assertEqual({"Archive Case": 1}, report["unmapped_activities"])

    def test_import_csv_strict_fails_on_unmapped_activity(self) -> None:
        with self.assertRaises(ValueError):
            import_csv_events(self.csv_path, self.jsonl_path, strict=True)

    def test_activity_map_handles_site_specific_labels(self) -> None:
        activity_map = self.root / "activity_map.json"
        activity_map.write_text('{"Archive Case": "reversal"}\n', encoding="utf-8")
        stats = import_csv_events(self.csv_path, self.jsonl_path, activity_map_path=activity_map, strict=True)
        self.assertEqual(6, stats.events_written)
        self.assertIn("reversal", {event.event_type for event in load_events(self.jsonl_path)})

    def test_policy_template_from_imported_events(self) -> None:
        import_csv_events(self.csv_path, self.jsonl_path)
        policy_path = self.root / "policy.json"
        policies = write_policy_template(load_events(self.jsonl_path), policy_path, flow_type="three_way_gr_based", approval_limit=900.0)
        self.assertEqual(1, len(policies))
        self.assertEqual("C100", policies[0]["case_id"])
        self.assertEqual(1250.0, policies[0]["po_amount"])
        self.assertEqual(10.0, policies[0]["po_quantity"])
        self.assertEqual(900.0, policies[0]["approval_limit"])

    def test_missing_invoice_ids_do_not_collapse_into_duplicate_invoice(self) -> None:
        csv_path = self.root / "no_invoice_ids.csv"
        csv_path.write_text(CSV_NO_INVOICE_IDS, encoding="utf-8")
        import_csv_events(csv_path, self.jsonl_path, strict=True)
        invoice_ids = [event.invoice_id for event in load_events(self.jsonl_path) if event.event_type == "invoice_received"]
        self.assertEqual([None, None], invoice_ids)

    def test_policy_template_blocks_missing_amount_by_default(self) -> None:
        csv_path = self.root / "no_amount.csv"
        csv_path.write_text(CSV_NO_AMOUNT, encoding="utf-8")
        import_csv_events(csv_path, self.jsonl_path, strict=True)
        with self.assertRaises(ValueError):
            write_policy_template(load_events(self.jsonl_path), self.root / "policy.json")

    def test_policy_template_can_warn_on_missing_amount(self) -> None:
        csv_path = self.root / "no_amount.csv"
        csv_path.write_text(CSV_NO_AMOUNT, encoding="utf-8")
        import_csv_events(csv_path, self.jsonl_path, strict=True)
        policies = write_policy_template(load_events(self.jsonl_path), self.root / "policy.json", allow_missing_amount=True)
        self.assertIn("po_amount defaulted", policies[0]["template_warnings"][0])

    def test_import_xes_maps_trace_and_event_attributes(self) -> None:
        xes_path = self.root / "events.xes"
        xes_path.write_text(XES_TEXT, encoding="utf-8")
        stats = import_xes_events(xes_path, self.jsonl_path, strict=True)
        events = load_events(self.jsonl_path)
        self.assertEqual(3, stats.events_written)
        self.assertEqual(["po_created", "invoice_received", "payment_cleared"], [event.event_type for event in events])
        self.assertEqual("PO-C500", events[0].po_id)
        self.assertEqual("V05", events[0].vendor_id)
        self.assertEqual(700.0, events[0].amount)


if __name__ == "__main__":
    unittest.main()
