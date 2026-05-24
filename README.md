# p2p-replay-gate

Replay and policy-oracle evaluation for purchase-to-pay agent workflows

Invoice agents fail when documents disagree: duplicate invoices, partial receipts, vendor mismatch, missing approval, payment before goods receipt. This repo tests those state transitions before an agent touches an AP queue.

## What it does

- replays P2P traces from JSONL
- streams process-mining style CSV or XES event logs into the replay format
- writes a policy skeleton for imported traces
- injects seeded procurement defects
- checks 2-way, 3-way, consignment, approval, duplicate, and payment-hold rules
- scores policy violations, duplicate catch recall, false holds, determinism, and audit trace coverage
- writes a reviewable failure queue

## Current result

Fixture v0

| Check | Result |
| --- | ---: |
| clean traces | 12 |
| injected scenarios | 48 |
| tests | 50 |
| critical policy violations caught | 36/36 |
| duplicate catch recall | 1.000 |
| false holds on clean traces | 0 |

Visual summary: <https://sihyeonjeon.github.io/p2p-replay-gate/>

## Run

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
p2p-replay-gate validate --events data/scenarios/base_events.jsonl --scenario-pack data/scenarios/injected_scenarios.json
p2p-replay-gate run --events data/scenarios/base_events.jsonl --scenario-pack data/scenarios/injected_scenarios.json --output reports/local_scorecard.json
p2p-replay-gate inspect --report reports/local_scorecard.json --top 10
```

Import a CSV event log:

```bash
p2p-replay-gate import-csv --input examples/events.csv --output imported/events.jsonl --report imported/adapter_report.json
p2p-replay-gate policy-template --events imported/events.jsonl --output imported/policy.json --flow-type three_way_gr_based
p2p-replay-gate audit --events imported/events.jsonl --policy imported/policy.json --output imported/audit.json
```

`policy-template` fails when amount data is missing, unless `--allow-missing-amount` is passed.

XES works the same way:

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

Test:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## Boundary

Synthetic fixture evidence only. No real vendor, invoice, payment, or company data is included. BPIC2019 is supported as a mapping pack, but the source log is not redistributed here.
