from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io import group_events, load_events
from .models import CasePolicy, P2PEvent
from .ops import replay_digest, resume_cursor, violation_signature
from .oracle import replay_case, summarize_violations


STORE_SCHEMA_VERSION = "p2p_replay_store.v1"


@dataclass(frozen=True)
class AppendStats:
    attempted: int = 0
    inserted: int = 0
    duplicate_retries: int = 0

    def add(self, other: "AppendStats") -> "AppendStats":
        return AppendStats(
            attempted=self.attempted + other.attempted,
            inserted=self.inserted + other.inserted,
            duplicate_retries=self.duplicate_retries + other.duplicate_retries,
        )


def build_store_replay_report(
    events_path: Path,
    policies: dict[str, CasePolicy],
    *,
    db_path: Path,
    partitions: int = 4,
    repeats: int = 2,
    queue_limit: int = 32,
    simulate_crash_after_partitions: int | None = None,
    reset_db: bool = True,
) -> dict[str, Any]:
    events = load_events(events_path)
    partitions = max(1, partitions)
    repeats = max(1, repeats)
    queue_limit = max(1, queue_limit)
    if simulate_crash_after_partitions is None:
        simulate_crash_after_partitions = max(1, partitions // 2)
    simulate_crash_after_partitions = max(0, min(partitions, simulate_crash_after_partitions))

    if reset_db:
        remove_store_files(db_path)
    with open_store(db_path) as conn:
        ingest = ingest_with_backpressure(conn, events, repeats=repeats, queue_limit=queue_limit)
        persisted_events = read_events(conn)
        replayable = replayable_cases(persisted_events, policies)
        partitioned = partition_replayable_cases(replayable, partitions)

        started = time.perf_counter()
        partial_partitions = list(range(simulate_crash_after_partitions))
        partial = replay_partitions(partitioned, policies, partition_ids=partial_partitions)
        for row in partial:
            write_checkpoint(conn, "store-replay", row)
        checkpoint_count_after_interrupt = len(read_checkpoints(conn, "store-replay"))

        remaining_partitions = [
            partition_id
            for partition_id in range(partitions)
            if partition_id not in {row["partition_id"] for row in partial}
        ]
        recovered = replay_partitions(partitioned, policies, partition_ids=remaining_partitions)
        for row in recovered:
            write_checkpoint(conn, "store-replay", row)
        elapsed = time.perf_counter() - started

        partition_results = sorted(partial + recovered, key=lambda row: row["partition_id"])
        serial_results = [
            replay_case(events, policies[case_id])
            for case_id, events in sorted(replayable.items())
        ]
        partition_case_results = [
            result
            for row in partition_results
            for result in row["case_results"]
        ]
        partition_matches_serial = violation_signature(serial_results) == violation_signature(partition_case_results)

        checkpoints = read_checkpoints(conn, "store-replay")
        db_bytes = store_bytes(db_path)
        return {
            "run_id": "p2p-replay-gate-store-replay-v0",
            "scope_note": "local SQLite replay-store pressure and recovery check over supplied event log; not a production SLA",
            "input": {
                "events_path": str(events_path),
                "unique_events": len(events),
                "append_repeats": repeats,
                "attempted_appends": ingest["append_stats"].attempted,
                "replayable_cases": len(replayable),
            },
            "consistency_model": {
                "ingest_semantics": "at-least-once append with event_id primary-key idempotency",
                "case_order": ["case_id", "timestamp", "event_id"],
                "partition_key": "stable sha256(case_id) modulo partition_count",
                "checkpoint_scope": "partition cursor after successful replay",
                "cross_case_boundary": "no cross-case distributed transaction model in this artifact",
            },
            "storage": {
                "engine": "sqlite",
                "schema_version": STORE_SCHEMA_VERSION,
                "db_path": str(db_path),
                "journal_mode": journal_mode(conn),
                "event_rows": count_rows(conn, "events"),
                "checkpoint_rows": len(checkpoints),
                "db_bytes": db_bytes,
                "indexes": [
                    "events(case_id, timestamp, event_id)",
                    "events(timestamp, event_id)",
                    "checkpoints(runner_id, partition_id)",
                ],
            },
            "backpressure": {
                "queue_limit": queue_limit,
                "peak_queue_depth": ingest["peak_queue_depth"],
                "flush_count": ingest["flush_count"],
                "duplicate_retries": ingest["append_stats"].duplicate_retries,
                "inserted_events": ingest["append_stats"].inserted,
            },
            "partitioning": {
                "partition_count": partitions,
                "case_counts": {
                    str(partition_id): len(partitioned[partition_id])
                    for partition_id in range(partitions)
                },
                "event_counts": {
                    str(row["partition_id"]): row["event_count"]
                    for row in partition_results
                },
                "partition_matches_serial": partition_matches_serial,
                "elapsed_ms": round(elapsed * 1000, 3),
            },
            "failure_recovery": {
                "simulated_interrupt_after_partitions": simulate_crash_after_partitions,
                "checkpoint_count_after_interrupt": checkpoint_count_after_interrupt,
                "recovered_partition_count": len(recovered),
                "final_checkpoint_count": len(checkpoints),
                "status": "recovered" if len(checkpoints) == partitions and partition_matches_serial else "review",
                "checkpoints": checkpoints,
            },
            "replay": {
                "violations_by_code": summarize_violations(serial_results),
                "replay_digest": replay_digest(persisted_events, policies),
                "resume_cursor": resume_cursor(persisted_events),
            },
            "production_boundary": (
                "This store proves local idempotent persistence, checkpointed partition replay, "
                "and recovery behavior. Real operation still needs queue service semantics, "
                "multi-writer contention tests, observability, access control, and ERP mapping review."
            ),
        }


def open_store(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    init_store(conn)
    return conn


def init_store(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            ingested_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_case_order
            ON events(case_id, timestamp, event_id);
        CREATE INDEX IF NOT EXISTS idx_events_time_order
            ON events(timestamp, event_id);

        CREATE TABLE IF NOT EXISTS checkpoints (
            runner_id TEXT NOT NULL,
            partition_id INTEGER NOT NULL,
            case_id TEXT,
            timestamp TEXT,
            event_id TEXT,
            replay_digest TEXT NOT NULL,
            case_count INTEGER NOT NULL,
            event_count INTEGER NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (runner_id, partition_id)
        );
        """
    )
    conn.commit()


def ingest_with_backpressure(
    conn: sqlite3.Connection,
    events: list[P2PEvent],
    *,
    repeats: int,
    queue_limit: int,
) -> dict[str, Any]:
    pending: list[P2PEvent] = []
    peak_queue_depth = 0
    flush_count = 0
    total = AppendStats()

    for _ in range(repeats):
        for event in events:
            pending.append(event)
            peak_queue_depth = max(peak_queue_depth, len(pending))
            if len(pending) >= queue_limit:
                total = total.add(append_events(conn, pending))
                pending = []
                flush_count += 1
    if pending:
        total = total.add(append_events(conn, pending))
        flush_count += 1

    return {
        "append_stats": total,
        "peak_queue_depth": peak_queue_depth,
        "flush_count": flush_count,
    }


def append_events(conn: sqlite3.Connection, events: list[P2PEvent]) -> AppendStats:
    inserted = 0
    now = time.time()
    with conn:
        for event in events:
            payload = event_payload(event)
            result = conn.execute(
                """
                INSERT OR IGNORE INTO events (
                    event_id, case_id, timestamp, event_type, schema_version, payload_json, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.case_id,
                    event.timestamp,
                    event.event_type,
                    str(event.attrs.get("schema_version") or "missing"),
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    now,
                ),
            )
            inserted += result.rowcount
    attempted = len(events)
    return AppendStats(attempted=attempted, inserted=inserted, duplicate_retries=attempted - inserted)


def read_events(conn: sqlite3.Connection) -> list[P2PEvent]:
    rows = conn.execute(
        """
        SELECT payload_json
        FROM events
        ORDER BY case_id, timestamp, event_id
        """
    ).fetchall()
    return [P2PEvent.from_dict(json.loads(row["payload_json"])) for row in rows]


def replayable_cases(
    events: list[P2PEvent],
    policies: dict[str, CasePolicy],
) -> dict[str, list[P2PEvent]]:
    return {
        case_id: rows
        for case_id, rows in group_events(events).items()
        if case_id in policies
    }


def partition_replayable_cases(
    replayable: dict[str, list[P2PEvent]],
    partitions: int,
) -> dict[int, list[tuple[str, list[P2PEvent]]]]:
    output: dict[int, list[tuple[str, list[P2PEvent]]]] = {
        partition_id: []
        for partition_id in range(partitions)
    }
    for case_id, events in sorted(replayable.items()):
        output[stable_partition(case_id, partitions)].append((case_id, events))
    return output


def replay_partitions(
    partitioned: dict[int, list[tuple[str, list[P2PEvent]]]],
    policies: dict[str, CasePolicy],
    *,
    partition_ids: list[int],
) -> list[dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=max(1, len(partition_ids))) as pool:
        return list(pool.map(lambda partition_id: replay_partition(partition_id, partitioned[partition_id], policies), partition_ids))


def replay_partition(
    partition_id: int,
    rows: list[tuple[str, list[P2PEvent]]],
    policies: dict[str, CasePolicy],
) -> dict[str, Any]:
    case_results = [
        replay_case(events, policies[case_id])
        for case_id, events in rows
    ]
    events = [
        event
        for _, case_events in rows
        for event in case_events
    ]
    policy_subset = {
        case_id: policies[case_id]
        for case_id, _ in rows
    }
    return {
        "partition_id": partition_id,
        "case_count": len(rows),
        "event_count": len(events),
        "cursor": resume_cursor(events),
        "digest": replay_digest(events, policy_subset),
        "case_results": case_results,
        "violations_by_code": summarize_violations(case_results),
    }


def write_checkpoint(conn: sqlite3.Connection, runner_id: str, row: dict[str, Any]) -> None:
    cursor = row["cursor"] or {}
    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO checkpoints (
                runner_id, partition_id, case_id, timestamp, event_id,
                replay_digest, case_count, event_count, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                runner_id,
                row["partition_id"],
                cursor.get("case_id"),
                cursor.get("timestamp"),
                cursor.get("event_id"),
                row["digest"],
                row["case_count"],
                row["event_count"],
                time.time(),
            ),
        )


def read_checkpoints(conn: sqlite3.Connection, runner_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT partition_id, case_id, timestamp, event_id, replay_digest, case_count, event_count
        FROM checkpoints
        WHERE runner_id = ?
        ORDER BY partition_id
        """,
        (runner_id,),
    ).fetchall()
    return [
        {
            "partition_id": int(row["partition_id"]),
            "cursor": {
                "case_id": row["case_id"],
                "timestamp": row["timestamp"],
                "event_id": row["event_id"],
            } if row["case_id"] is not None else None,
            "replay_digest": row["replay_digest"],
            "case_count": int(row["case_count"]),
            "event_count": int(row["event_count"]),
        }
        for row in rows
    ]


def stable_partition(case_id: str, partitions: int) -> int:
    digest = hashlib.sha256(case_id.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % partitions


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])


def journal_mode(conn: sqlite3.Connection) -> str:
    return str(conn.execute("PRAGMA journal_mode").fetchone()[0])


def event_payload(event: P2PEvent) -> dict[str, Any]:
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


def remove_store_files(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        target = Path(str(path) + suffix)
        if target.exists():
            os.remove(target)


def store_bytes(path: Path) -> int:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        target = Path(str(path) + suffix)
        if target.exists():
            total += target.stat().st_size
    return total
