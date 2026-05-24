# bpic2019 mapping pack

Maps BPIC2019 purchase-order events into the replay gate event types.

The BPIC2019 dataset is not included. Download the source log from the official challenge page, then run:

```bash
p2p-replay-gate import-xes --pack bpic2019 --input BPI_Challenge_2019.xes --output imported/bpic2019.jsonl --report imported/bpic2019_adapter.json --max-cases 1000
p2p-replay-gate policy-template --events imported/bpic2019.jsonl --output imported/bpic2019_policy.json --flow-type auto --approval-limit 1000000000
p2p-replay-gate audit --events imported/bpic2019.jsonl --policy imported/bpic2019_policy.json --output imported/bpic2019_audit.json
```

For the full BPIC2019 log, start with a `--max-cases` smoke run, review `unmapped_activities`, then rerun with `--strict` after the audit scope is fixed.
