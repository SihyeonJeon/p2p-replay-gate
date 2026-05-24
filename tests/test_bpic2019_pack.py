from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from p2p_replay_gate.adapters import import_xes_events, write_policy_template
from p2p_replay_gate.cli import main
from p2p_replay_gate.io import load_events, read_json
from p2p_replay_gate.packs import load_activity_map, load_manifest


FLOW_VARIANT_XES = """<?xml version="1.0" encoding="UTF-8" ?>
<log xmlns="http://www.xes-standard.org/">
  <trace>
    <string key="concept:name" value="BPIC19-TWO-WAY"/>
    <string key="Purchasing Document" value="450000002"/>
    <string key="Item" value="00020"/>
    <string key="Vendor" value="VENDOR-002"/>
    <string key="Item Category" value="2-way match"/>
    <boolean key="Goods Receipt" value="false"/>
    <boolean key="GR-Based Inv. Verif." value="false"/>
    <float key="Cumulative net worth (EUR)" value="400"/>
    <float key="Quantity" value="4"/>
    <event><string key="concept:name" value="Create Purchase Order Item"/><date key="time:timestamp" value="2018-01-01T08:00:00.000+00:00"/></event>
    <event><string key="concept:name" value="Record Invoice Receipt"/><date key="time:timestamp" value="2018-01-02T08:00:00.000+00:00"/><string key="Invoice" value="510000002"/></event>
    <event><string key="concept:name" value="Clear Invoice"/><date key="time:timestamp" value="2018-01-03T08:00:00.000+00:00"/><string key="Invoice" value="510000002"/></event>
  </trace>
  <trace>
    <string key="concept:name" value="BPIC19-CONSIGNMENT"/>
    <string key="Purchasing Document" value="450000003"/>
    <string key="Item" value="00030"/>
    <string key="Vendor" value="VENDOR-003"/>
    <string key="Item Category" value="Consignment"/>
    <boolean key="Goods Receipt" value="true"/>
    <boolean key="GR-Based Inv. Verif." value="true"/>
    <float key="Cumulative net worth (EUR)" value="250"/>
    <float key="Quantity" value="5"/>
    <event><string key="concept:name" value="Create Purchase Order Item"/><date key="time:timestamp" value="2018-01-01T08:00:00.000+00:00"/></event>
    <event><string key="concept:name" value="Record Goods Receipt"/><date key="time:timestamp" value="2018-01-02T08:00:00.000+00:00"/></event>
  </trace>
</log>
"""


class Bpic2019PackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.jsonl_path = self.root / "events.jsonl"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _main(self, args: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_pack_imports_tiny_xes_and_auto_policy(self) -> None:
        source = Path(__file__).parents[1] / "examples" / "bpic2019_tiny.xes"
        mapping = load_activity_map("bpic2019")
        stats = import_xes_events(source, self.jsonl_path, activity_map=mapping, strict=True)

        self.assertEqual(4, stats.events_written)
        events = load_events(self.jsonl_path)
        self.assertEqual(
            ["po_created", "goods_receipt", "invoice_received", "payment_cleared"],
            [event.event_type for event in events],
        )
        self.assertEqual("three_way_gr_based", events[0].attrs["flow_hint"])

        policy_path = self.root / "policy.json"
        policies = write_policy_template(events, policy_path, flow_type="auto")
        self.assertEqual("three_way_gr_based", policies[0]["flow_type"])
        self.assertEqual(1250.0, policies[0]["po_amount"])

    def test_pack_auto_policy_selects_flow_variants(self) -> None:
        source = self.root / "variants.xes"
        source.write_text(FLOW_VARIANT_XES, encoding="utf-8")
        import_xes_events(source, self.jsonl_path, activity_map=load_activity_map("bpic2019"), strict=True)

        policies = write_policy_template(load_events(self.jsonl_path), self.root / "policy.json", flow_type="auto")
        by_case = {policy["case_id"]: policy["flow_type"] for policy in policies}
        self.assertEqual("two_way", by_case["BPIC19-TWO-WAY"])
        self.assertEqual("consignment", by_case["BPIC19-CONSIGNMENT"])

    def test_cli_imports_pack_and_prints_manifest(self) -> None:
        source = Path(__file__).parents[1] / "examples" / "bpic2019_tiny.xes"
        report_path = self.root / "adapter_report.json"
        policy_path = self.root / "policy.json"
        audit_path = self.root / "audit.json"

        code, _, _ = self._main([
            "import-xes",
            "--pack",
            "bpic2019",
            "--input",
            str(source),
            "--output",
            str(self.jsonl_path),
            "--report",
            str(report_path),
            "--strict",
        ])
        self.assertEqual(0, code)
        self.assertEqual(4, read_json(report_path)["events_written"])

        code, _, _ = self._main([
            "policy-template",
            "--events",
            str(self.jsonl_path),
            "--output",
            str(policy_path),
            "--flow-type",
            "auto",
            "--approval-limit",
            "1000000000",
        ])
        self.assertEqual(0, code)
        self.assertEqual("three_way_gr_based", read_json(policy_path)[0]["flow_type"])

        code, _, _ = self._main(["audit", "--events", str(self.jsonl_path), "--policy", str(policy_path), "--output", str(audit_path)])
        self.assertEqual(0, code)

        code, stdout, _ = self._main(["pack-info", "bpic2019"])
        self.assertEqual(0, code)
        self.assertIn("BPIC2019 purchase-to-pay mapping pack", stdout)
        self.assertEqual("bpic2019", load_manifest("bpic2019")["name"])


if __name__ == "__main__":
    unittest.main()
