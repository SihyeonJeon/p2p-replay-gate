# p2p-replay-gate

[![CI](https://github.com/SihyeonJeon/p2p-replay-gate/actions/workflows/ci.yml/badge.svg)](https://github.com/SihyeonJeon/p2p-replay-gate/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Replay purchase-to-pay state before an invoice agent acts

Invoice agents can make unsafe moves when documents look clean but workflow
state is wrong: duplicate invoices, vendor mismatch, missing goods receipt,
missing approval, payment before release, or payment while blocked. This repo
turns those cases into replayable traces, policy checks, and a pre-execution
action gate.

Visual case page: <https://sihyeonjeon.github.io/projects/p2p-replay-gate/>

## What It Does

- replays purchase-to-pay traces from JSONL
- imports process-mining CSV or XES event logs
- maps BPIC2019-style activities into the replay format
- injects duplicate, receipt, vendor, approval, and payment defects
- checks 2-way, 3-way, consignment, approval, duplicate, and hold rules
- scores violation detection, duplicate recall, false holds, and trace coverage
- blocks proposed agent actions before unsafe payment or approval events execute

## Current Result

Synthetic fixture plus BPIC2019 smoke

| Check | Result |
| --- | ---: |
| clean traces | 12 |
| injected scenarios | 48 |
| tests | 58 |
| critical violations caught | 36 / 36 |
| duplicate catch recall | 1.000 |
| false holds on clean traces | 0 |
| BPIC2019 smoke | 1,000 cases |
| BPIC2019 trace coverage | 0.970 |
| action gate decisions | `allow` / `review` / `block` |

Example gate output:

```text
decision: block
reason: blocked by PAYMENT_BEFORE_APPROVAL
case: C004
```

The gate compares the current trace with the trace after the proposed action.
If the new event introduces a critical policy violation, the action is blocked
before the agent writes to an AP queue.

## Quick Start

```bash
git clone https://github.com/SihyeonJeon/p2p-replay-gate
cd p2p-replay-gate
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

Run the fixture:

```bash
p2p-replay-gate validate \
  --events data/scenarios/base_events.jsonl \
  --scenario-pack data/scenarios/injected_scenarios.json

p2p-replay-gate run \
  --events data/scenarios/base_events.jsonl \
  --scenario-pack data/scenarios/injected_scenarios.json \
  --output reports/local_scorecard.json
```

Pre-check an agent action:

```bash
p2p-replay-gate gate-action \
  --events data/scenarios/base_events.jsonl \
  --policy data/scenarios/policy.json \
  --action clear_payment \
  --case-id C004 \
  --output reports/agent_action_gate_sample.json
```

## Import Event Logs

CSV:

```bash
p2p-replay-gate import-csv \
  --input examples/events.csv \
  --output imported/events.jsonl \
  --report imported/adapter_report.json

p2p-replay-gate policy-template \
  --events imported/events.jsonl \
  --output imported/policy.json \
  --flow-type three_way_gr_based

p2p-replay-gate audit \
  --events imported/events.jsonl \
  --policy imported/policy.json \
  --output imported/audit.json
```

XES:

```bash
p2p-replay-gate import-xes --input examples/events.xes --output imported/events.jsonl --strict
```

BPIC2019 mapping pack:

```bash
p2p-replay-gate pack-info bpic2019
p2p-replay-gate import-xes --pack bpic2019 --input examples/bpic2019_tiny.xes --output imported/bpic2019.jsonl --report imported/bpic2019_adapter.json --strict
p2p-replay-gate policy-template --events imported/bpic2019.jsonl --output imported/bpic2019_policy.json --flow-type auto --approval-limit 1000000000
p2p-replay-gate audit --events imported/bpic2019.jsonl --policy imported/bpic2019_policy.json --output imported/bpic2019_audit.json
```

For the official full XES, start with `--max-cases 1000` and inspect
`unmapped_activities` before relying on the mapping.

## Evidence Files

- [reports/scorecard.json](reports/scorecard.json): synthetic fixture scorecard
- [reports/agent_action_gate_sample.json](reports/agent_action_gate_sample.json):
  blocked payment sample
- [reports/bpic2019_smoke_summary.json](reports/bpic2019_smoke_summary.json):
  1,000-case real-log smoke summary
- [docs/index.html](docs/index.html): local visual summary

## Test

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Boundary

Synthetic fixture evidence only. No real vendor, invoice, payment, or company
data is included. BPIC2019 is supported as a mapping pack, but the source log is
not redistributed here.
