# p2p-replay-gate

Replay and policy-oracle evaluation for purchase-to-pay agent workflows

Invoice agents fail when documents disagree: duplicate invoices, partial receipts, vendor mismatch, missing approval, payment before goods receipt. This repo tests those state transitions before an agent touches an AP queue.

## What it does

- replays P2P traces from JSONL
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
| oracle tests | 33 |
| critical policy violations caught | 36/36 |
| duplicate catch recall | 1.000 |
| false holds on clean traces | 0 |

Visual summary: `docs/index.html`

## Run

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
p2p-replay-gate validate --events data/scenarios/base_events.jsonl --scenario-pack data/scenarios/injected_scenarios.json
p2p-replay-gate run --events data/scenarios/base_events.jsonl --scenario-pack data/scenarios/injected_scenarios.json --output reports/local_scorecard.json
p2p-replay-gate inspect --report reports/local_scorecard.json --top 10
```

Test:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## Boundary

Synthetic fixture evidence only. No real vendor, invoice, payment, or company data is included. The BPIC2019 source is a future adapter target, not redistributed here.
